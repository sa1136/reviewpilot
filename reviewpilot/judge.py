"""Judge node and retry routing. The judge prompt is a placeholder until Stage 4."""

import time

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import Send

from reviewpilot.config import MAX_LOOPS_CAP, Settings
from reviewpilot.models import SEVERITY_ORDER, Finding

JUDGE_PROMPT = (
    "You are the judge. For each finding, decide confirm, reject or downgrade. "
    "Prefer rejecting when unsure. (Placeholder, Stage 4.)"
)


def build_judge_messages(candidates: list[Finding]) -> list:
    lines = [
        f"- id={f.id} agent={f.agent} {f.file}:{f.line} [{f.severity}] {f.title}\n"
        f"  evidence: {f.evidence}\n  {f.body}"
        for f in candidates
    ]
    return [SystemMessage(JUDGE_PROMPT), HumanMessage("Findings:\n" + "\n".join(lines))]


def _one_level_lower(severity: str) -> str:
    i = SEVERITY_ORDER.index(severity)
    return SEVERITY_ORDER[min(i + 1, len(SEVERITY_ORDER) - 1)]


def make_judge_node(model, reviewers: list[str]):
    def judge(state: dict) -> dict:
        rnd = state["round"]
        candidates = state["candidates"]
        done = {"round": rnd + 1, "retry_agents": [], "judge_feedback": {}}
        if not candidates:
            return done

        start = time.perf_counter()
        try:
            verdict = model.invoke(build_judge_messages(candidates))
        except Exception as exc:  # judge failed: nothing from this round is accepted
            return {**done, "errors": [{"node": "judge", "round": rnd, "error": repr(exc)}]}

        # Findings the judge did not decide on are dropped (the judge favors dropping).
        pending = {f.id: f for f in candidates}
        accepted = []
        for d in verdict.decisions:
            finding = pending.pop(d.finding_id, None)
            if finding is None or d.decision == "reject":
                continue
            if d.decision == "downgrade":
                severity = d.new_severity or _one_level_lower(finding.severity)
                finding = finding.model_copy(update={"severity": severity})
            accepted.append(finding)

        retry = [a for a in dict.fromkeys(verdict.retry_agents) if a in reviewers]
        usage = {"node": "judge", "round": rnd, "latency_s": round(time.perf_counter() - start, 3)}
        return {
            "accepted": accepted,
            "round": rnd + 1,
            "retry_agents": retry,
            "judge_feedback": {a: verdict.feedback.get(a, "") for a in retry},
            "usage": [usage],
        }

    return judge


def make_router(settings: Settings):
    """After the judge: fan out again to the agents it sent back, or finish.

    The cap lives here in code. `round` has already been bumped to the round a retry would be.
    """

    def route_after_judge(state: dict):
        next_round = state["round"]
        retry_number = next_round - 1
        if (
            state["retry_agents"]
            and retry_number <= settings.effective_max_retries
            and next_round <= MAX_LOOPS_CAP
        ):
            return [Send(agent, state) for agent in state["retry_agents"]]
        return "post_review"

    return route_after_judge
