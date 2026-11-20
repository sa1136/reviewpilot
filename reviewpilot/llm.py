"""Model factory and the single place model calls happen.

A model is anything with `invoke(messages)`. The real one (ChatAnthropic with structured output
and include_raw) returns {"raw": AIMessage, "parsed": obj | None, "parsing_error": ...}. FakeModel
returns either that same dict or the parsed object directly, so tests need no API.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from reviewpilot.config import AVAILABLE_AGENTS, Settings, estimate_cost
from reviewpilot.models import (
    LANE_CATEGORIES,
    AgentOutput,
    Finding,
    FindingDecision,
    JudgeVerdict,
)

ModelFactory = Callable[[str], Any]


def schema_for(role: str) -> type:
    return JudgeVerdict if role == "judge" else AgentOutput


def get_model(role: str, settings: Settings):
    """Real Claude model for a role (an agent name or "judge").

    No temperature: current Claude models reject non-default sampling values. Structured output
    uses the API's json_schema output format (forced tool choice is rejected by newer models).
    """
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(
        model=settings.model_for(role),
        api_key=settings.anthropic_api_key,
        max_tokens=settings.max_output_tokens,
        default_request_timeout=settings.request_timeout_s,
        max_retries=settings.api_max_retries,  # 429 / 5xx / network; not the judge loop
    )
    return llm.with_structured_output(schema_for(role), method="json_schema", include_raw=True)


def real_model_factory(settings: Settings) -> ModelFactory:
    def factory(role: str):
        return get_model(role, settings)

    return factory


@dataclass
class CallResult:
    parsed: Any  # AgentOutput / JudgeVerdict, or None when the output could not be used
    usage: dict
    error: str | None = None


def usage_entry(node: str, rnd: int, model: str, raw: Any, latency_ms: int) -> dict:
    meta = getattr(raw, "usage_metadata", None) or {}
    details = meta.get("input_token_details") or {}
    input_tokens = meta.get("input_tokens", 0) or 0  # full prompt, cached tokens included
    output_tokens = meta.get("output_tokens", 0) or 0
    cache_read = details.get("cache_read") or 0
    cache_write = (details.get("cache_creation") or 0) + (
        details.get("ephemeral_5m_input_tokens") or 0
    )
    return {
        "node": node,
        "round": rnd,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "latency_ms": latency_ms,
        "cost_usd": estimate_cost(model, input_tokens, output_tokens, cache_read, cache_write),
    }


def call_model(model: Any, messages: list, node: str, rnd: int, model_name: str) -> CallResult:
    """Invoke a model once and record usage. Exceptions propagate; unusable output is reported
    as an error with usage still recorded (the tokens were spent)."""
    start = time.perf_counter()
    result = model.invoke(messages)
    latency_ms = round((time.perf_counter() - start) * 1000)

    if isinstance(result, dict) and "raw" in result:
        usage = usage_entry(node, rnd, model_name, result["raw"], latency_ms)
        if result.get("parsed") is None:
            reason = result.get("parsing_error") or "no structured output"
            stop = getattr(result["raw"], "response_metadata", {}).get("stop_reason")
            note = f" (stop_reason={stop})" if stop else ""
            return CallResult(None, usage, f"unusable model output{note}: {reason}")
        return CallResult(result["parsed"], usage)
    return CallResult(result, usage_entry(node, rnd, model_name, None, latency_ms))


def run_totals(usage: list[dict]) -> dict:
    tokens_in = sum(u.get("input_tokens", 0) for u in usage)
    tokens_out = sum(u.get("output_tokens", 0) for u in usage)
    costs = [u.get("cost_usd") for u in usage if "model" in u]
    known = [c for c in costs if c is not None]
    return {
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cache_read_tokens": sum(u.get("cache_read_tokens", 0) for u in usage),
        "cache_write_tokens": sum(u.get("cache_write_tokens", 0) for u in usage),
        "cost_usd": sum(known),
        "cost_complete": len(known) == len(costs),  # False if a model is missing from PRICING
    }


class FakeModel:
    """Test double. Give it a list of scripted responses (one per call, in order) or a function
    of the messages. A response that is an Exception is raised instead of returned."""

    def __init__(self, responses: list | Callable[[list], Any]):
        self._responses = responses if callable(responses) else list(responses)
        self.calls: list[list] = []

    def invoke(self, messages: list) -> Any:
        self.calls.append(messages)
        if callable(self._responses):
            response = self._responses(messages)
        elif self._responses:
            response = self._responses.pop(0)
        else:
            raise RuntimeError("FakeModel ran out of scripted responses")
        if isinstance(response, Exception):
            raise response
        return response


def fake_factory(models: dict[str, FakeModel]) -> ModelFactory:
    def factory(role: str):
        return models[role]

    return factory


# --- Demo fakes for `--fake`: run the whole graph on a real PR without an API key. ---

_FILE_HEADER = re.compile(r"^=== (\S+)")
_ADDED_LINE = re.compile(r"^\s*(\d+) \+ (.*)$")
_FINDING_ID = re.compile(r"id=([0-9a-f]{12})")


def _added_lines_in_prompt(messages: list) -> list[tuple[str, int, str]]:
    """(file, line, code) for each added line in the numbered diff inside the prompt."""
    out, current = [], None
    for text_line in messages[-1].content.splitlines():
        if m := _FILE_HEADER.match(text_line):
            current = m.group(1)
        elif current and (m := _ADDED_LINE.match(text_line)) and m.group(2).strip():
            out.append((current, int(m.group(1)), m.group(2)))
    return out


def _demo_agent(role: str, index: int) -> Callable[[list], AgentOutput]:
    def respond(messages: list) -> AgentOutput:
        added = _added_lines_in_prompt(messages)
        if not added:
            return AgentOutput(notes="no added lines")
        file, line, code = added[min(index, len(added) - 1)]
        finding = Finding(
            file=file,
            line=line,
            severity="minor",
            category=LANE_CATEGORIES[role][0],
            title=f"Demo {role} finding",
            body="Placeholder finding from the fake model.",
            evidence=code.strip(),
            confidence=0.9,
        )
        return AgentOutput(findings=[finding], notes="fake")

    return respond


def _demo_judge(messages: list) -> JudgeVerdict:
    ids = _FINDING_ID.findall(messages[-1].content)
    return JudgeVerdict(
        decisions=[FindingDecision(finding_id=i, decision="confirm", reason="demo") for i in ids]
    )


def demo_fake_factory() -> ModelFactory:
    models = {name: FakeModel(_demo_agent(name, i)) for i, name in enumerate(AVAILABLE_AGENTS)}
    models["judge"] = FakeModel(_demo_judge)
    return fake_factory(models)
