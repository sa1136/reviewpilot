"""Judge node and retry routing."""

import time

from langchain_core.messages import HumanMessage
from langgraph.types import Send

from reviewpilot.agents import cached_system, call_meta, untrusted_pr_block
from reviewpilot.config import MAX_LOOPS_CAP, Settings
from reviewpilot.events import emit
from reviewpilot.llm import call_model
from reviewpilot.models import SEVERITY_ORDER, Finding, apply_floor
from reviewpilot.prompts import load_prompt

# Rejected findings from these agents are kept as possible issues for a human to decide.
KEEP_REJECTED_FROM = ("correctness", "security")


def format_findings(candidates: list[Finding]) -> str:
    return "\n".join(
        f"- id={f.id} agent={f.agent} {f.file}:{f.line} [{f.severity}] {f.title}\n"
        f"  evidence: {f.evidence}\n  {f.body}"
        for f in candidates
    )


def build_judge_messages(state: dict, candidates: list[Finding]) -> list:
    text = "\n\n".join(
        [
            untrusted_pr_block(state),
            f"Agents that ran this round: {', '.join(state['route'])}",
            "<findings>\n" + format_findings(candidates) + "\n</findings>",
            "Decide every finding id above.",
        ]
    )
    return [cached_system(load_prompt("judge")), HumanMessage(text)]


def _one_level_lower(severity: str) -> str:
    i = SEVERITY_ORDER.index(severity)
    return SEVERITY_ORDER[min(i + 1, len(SEVERITY_ORDER) - 1)]


def allowed_retries(verdict, candidates: list[Finding], accepted: list[Finding], route: list[str]):
    """Agents the judge may send back: only agents in this PR's route, and only if more than half
    of the agent's findings this round were rejected, or the judge flagged a clear miss."""
    allowed = []
    for agent in dict.fromkeys(verdict.retry_agents):
        if agent not in route:
            continue
        total = sum(1 for f in candidates if f.agent == agent)
        rejected = total - sum(1 for f in accepted if f.agent == agent)
        mostly_wrong = total > 0 and rejected / total > 0.5
        clear_miss = any(fb.agent == agent and fb.clear_miss for fb in verdict.feedback)
        if mostly_wrong or clear_miss:
            allowed.append(agent)
    return allowed


def make_judge_node(model, model_name: str):
    def judge(state: dict) -> dict:
        rnd = state["round"]
        candidates = state["candidates"]
        # reviewed_at freezes the footer's review time before the run waits for a human
        done = {
            "round": rnd + 1,
            "retry_agents": [],
            "judge_feedback": {},
            "reviewed_at": time.time(),
        }
        if not candidates:
            emit("judge_round", round=rnd, judged=0, confirmed=0, rejected=0, retrying=[])
            return done

        try:
            result = call_model(
                model,
                build_judge_messages(state, candidates),
                "judge",
                rnd,
                model_name,
                call_meta(state),
            )
        except Exception as exc:  # judge failed: nothing from this round is accepted
            error = {"node": "judge", "round": rnd, "error": repr(exc)}
            emit("judge_round", round=rnd, judged=len(candidates), confirmed=0, rejected=0,
                 retrying=[], error=error["error"])  # fmt: skip
            return {**done, "errors": [error]}
        if result.error:
            error = {"node": "judge", "round": rnd, "error": result.error}
            emit("judge_round", round=rnd, judged=len(candidates), confirmed=0, rejected=0,
                 retrying=[], error=result.error)  # fmt: skip
            return {**done, "errors": [error], "usage": [result.usage]}
        verdict = result.parsed

        # Findings the judge did not decide on count as rejected (the judge favors dropping).
        pending = {f.id: f for f in candidates}
        accepted, rejected = [], []
        for d in verdict.decisions:
            finding = pending.pop(d.finding_id, None)
            if finding is None:
                continue
            if d.decision == "reject":
                rejected.append((finding, d.reason or "rejected without a reason"))
                continue
            severity = finding.severity
            if d.final_severity:
                severity = d.final_severity
            elif d.decision == "downgrade":
                severity = _one_level_lower(finding.severity)
            severity = apply_floor(severity, finding.category)  # judge can raise, never lower
            accepted.append(finding.model_copy(update={"severity": severity}))
        rejected += [(f, "the judge gave no decision") for f in pending.values()]
        possible = [
            {"finding": f, "reason": reason}
            for f, reason in rejected
            if f.agent in KEEP_REJECTED_FROM
        ]

        retry = allowed_retries(verdict, candidates, accepted, state["route"])
        usage = {**result.usage, "judged": len(candidates), "accepted": len(accepted)}
        emit(
            "judge_round",
            round=rnd,
            judged=len(candidates),
            confirmed=len(accepted),
            rejected=len(candidates) - len(accepted),
            retrying=retry,
            latency_ms=result.usage["latency_ms"],
        )
        return {
            "accepted": accepted,
            "judged": candidates,
            "possible_issues": possible,
            "round": rnd + 1,
            "reviewed_at": time.time(),
            "retry_agents": retry,
            "judge_feedback": {a: verdict.feedback_for(a) for a in retry},
            "usage": [usage],
        }

    return judge


def make_router(settings: Settings):
    """After the judge: fan out again to the agents it sent back, or go to human approval.

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
        return "approval"

    return route_after_judge
