"""Model factory. Every LLM call goes through a model built here, so tests can swap in a fake.

A model is anything with `invoke(messages) -> pydantic object` (AgentOutput or JudgeVerdict).
"""

import re
from collections.abc import Callable
from typing import Any, Literal

from reviewpilot.config import Settings
from reviewpilot.models import AgentOutput, Finding, FindingDecision, JudgeVerdict

Role = Literal["quality", "correctness", "judge"]
ModelFactory = Callable[[str], Any]

SCHEMAS = {"quality": AgentOutput, "correctness": AgentOutput, "judge": JudgeVerdict}


def get_model(role: str, settings: Settings):
    """Real Claude model with structured output for the given role."""
    from langchain_anthropic import ChatAnthropic

    name = settings.judge_model if role == "judge" else settings.agent_model
    llm = ChatAnthropic(model=name, api_key=settings.anthropic_api_key, max_tokens=4096)
    return llm.with_structured_output(SCHEMAS[role])


def real_model_factory(settings: Settings) -> ModelFactory:
    def factory(role: str):
        return get_model(role, settings)

    return factory


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


def _demo_agent(role: str, pick_last: bool) -> Callable[[list], AgentOutput]:
    def respond(messages: list) -> AgentOutput:
        added = _added_lines_in_prompt(messages)
        if not added:
            return AgentOutput(notes="no added lines")
        file, line, code = added[-1] if pick_last else added[0]
        finding = Finding(
            file=file,
            line=line,
            severity="minor",
            category="demo",
            title=f"Demo {role} finding",
            body="Placeholder finding from the fake model; real reviews arrive in Stage 3.",
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
    return fake_factory(
        {
            "quality": FakeModel(_demo_agent("quality", pick_last=False)),
            "correctness": FakeModel(_demo_agent("correctness", pick_last=True)),
            "judge": FakeModel(_demo_judge),
        }
    )
