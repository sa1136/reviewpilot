"""Sub agent nodes: correctness, security, quality."""

from langchain_core.messages import HumanMessage, SystemMessage

from reviewpilot.llm import call_model
from reviewpilot.models import FileDiff
from reviewpilot.prompts import agent_system_prompt


def cached_system(text: str) -> SystemMessage:
    """System prompt marked for prompt caching. It is identical across PRs and rounds, so repeat
    calls within the cache TTL read it at a fraction of the input price."""
    return SystemMessage(
        content=[{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
    )


def render_files(files: list[FileDiff]) -> str:
    return "\n\n".join(f"=== {f.path}\n{f.numbered_patch}" for f in files)


def untrusted_pr_block(state: dict) -> str:
    return "\n".join(
        [
            "<untrusted_pr>",
            f"Title: {state['pr_title']}",
            f"Body:\n{state['pr_body'] or '(empty)'}",
            "Numbered diff (new side line numbers; `+` = added, `-` = deleted, blank = context):",
            render_files(state["files"]),
            "</untrusted_pr>",
        ]
    )


def build_agent_messages(name: str, state: dict) -> list:
    parts = [untrusted_pr_block(state)]
    feedback = state.get("judge_feedback", {}).get(name)
    if feedback:
        parts.append(f"<judge_feedback>\n{feedback}\n</judge_feedback>")
    parts.append(f"Review this pull request as the {name} reviewer.")
    return [cached_system(agent_system_prompt(name)), HumanMessage("\n\n".join(parts))]


def make_agent_node(name: str, model, model_name: str):
    """Node that returns ONLY findings, usage and errors (all append reducers)."""

    def agent_node(state: dict) -> dict:
        rnd = state["round"]
        try:
            result = call_model(model, build_agent_messages(name, state), name, rnd, model_name)
        except Exception as exc:  # fail soft: record it, the other agents' work survives
            error = {"node": name, "round": rnd, "error": f"{type(exc).__name__}: {exc}"}
            return {"errors": [error]}
        if result.error:
            error = {"node": name, "round": rnd, "error": result.error}
            return {"errors": [error], "usage": [result.usage]}
        findings = [
            f.model_copy(update={"agent": name, "round": rnd}) for f in result.parsed.findings
        ]
        return {"findings": findings, "usage": [result.usage]}

    agent_node.__name__ = name
    return agent_node
