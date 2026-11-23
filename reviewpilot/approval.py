"""Human approval: the graph pauses here with interrupt() until a person decides.

Pending items get short stable ids, computed from saved state in a fixed order:
  i1..  inline comments            default: approve
  m1..  over the comment budget    default: approve (summary list)
  p1..  possible issues            default: not posted (approve = summary, promote = inline)
  n1..  nits                       default: approve (summary list)
  t1    new functions without tests line   default: approve

Resume with Command(resume={"decisions": [{"id", "action", "body"}], "post": bool}).
Actions: approve | reject | edit (body replaces the text) | promote (possible issue -> inline).
With post=False the decisions are saved and the graph pauses again, showing what would be posted.
"""

from langgraph.types import Command, interrupt

from reviewpilot.config import Settings
from reviewpilot.events import emit
from reviewpilot.models import Finding
from reviewpilot.output import (
    CommentPlan,
    by_severity,
    compute_status,
    format_summary,
    plan_comments,
    untested_line,
)

ACTIONS = ("approve", "reject", "edit", "promote")
DEFAULTS = {"inline": "approve", "more": "approve", "possible": "reject", "nit": "approve",
            "tests": "approve"}  # fmt: skip
PREFIX = {"inline": "i", "more": "m", "possible": "p", "nit": "n"}


def _item(kind: str, n: int, f: Finding, reason: str | None = None) -> dict:
    return {
        "id": f"{PREFIX[kind]}{n}",
        "kind": kind,
        "default": DEFAULTS[kind],
        "finding": f,
        "reason": reason,
    }


def pending_items(plan: CommentPlan, possible: list[dict]) -> list[dict]:
    items = [_item("inline", n, f) for n, f in enumerate(plan.inline, 1)]
    items += [_item("more", n, f) for n, f in enumerate(plan.overflow, 1)]
    items += [_item("possible", n, p["finding"], p["reason"]) for n, p in enumerate(possible, 1)]
    items += [_item("nit", n, f) for n, f in enumerate(plan.nits, 1)]
    if plan.untested:
        items.append(
            {
                "id": "t1",
                "kind": "tests",
                "default": "approve",
                "text": untested_line(plan.untested),
                "findings": plan.untested,
            }
        )
    return items


def check_decision(item: dict, decision: dict) -> str | None:
    """Why a decision is invalid for this item, or None."""
    action = decision.get("action")
    if action not in ACTIONS:
        return f"{item['id']}: unknown action {action!r}"
    if action == "promote" and item["kind"] != "possible":
        return f"{item['id']}: only possible issues (p ids) can be promoted"
    if action == "edit" and not (decision.get("body") or "").strip():
        return f"{item['id']}: edit needs new text"
    return None


def decision_errors(items: list[dict], decisions: list[dict]) -> list[str]:
    """Problems with a list of decisions against the pending items (empty list = all valid).
    `items` may be pending items or the public items from the interrupt payload."""
    by_id = {i["id"]: i for i in items}
    errors = []
    for d in decisions:
        item = by_id.get(d.get("id"))
        if item is None:
            errors.append(f"{d.get('id')}: no such item")
        elif problem := check_decision(item, d):
            errors.append(problem)
    return errors


def apply_decisions(
    items: list[dict], decisions: dict[str, dict], settings: Settings
) -> tuple[CommentPlan, list[dict], str | None, list[dict]]:
    """What gets posted: (plan, possible issues for the summary, tests line, decision log)."""
    inline, more, nits, approved_possible, log = [], [], [], [], []
    tests_line = None
    for item in items:
        chosen = decisions.get(item["id"])
        action = chosen["action"] if chosen else item["default"]
        body = (chosen or {}).get("body")
        entry = {"id": item["id"], "kind": item["kind"], "action": action,
                 "source": "human" if chosen else "default"}  # fmt: skip
        if action == "reject":
            log.append(entry)
            continue
        if item["kind"] == "tests":
            tests_line = body if action == "edit" else item["text"]
            entry["original"] = item["text"]
            log.append(entry | ({"body": body} if action == "edit" else {}))
            continue
        f: Finding = item["finding"]
        if body and action in ("edit", "promote"):
            entry |= {"original": f.body, "body": body}
            f = f.model_copy(update={"body": body})
        log.append(entry)
        if item["kind"] in ("inline", "more") or action == "promote":
            (inline if item["kind"] != "more" else more).append(f)
        elif item["kind"] == "possible":
            approved_possible.append({"finding": f, "reason": item["reason"]})
        elif item["kind"] == "nit":
            nits.append(f)

    inline = by_severity(inline)
    budget = max(settings.max_comments, 0)
    plan = CommentPlan(
        inline=inline[:budget],
        overflow=by_severity(inline[budget:] + more),
        nits=nits,
        untested=[],
        tests_line=tests_line,
    )
    return plan, approved_possible, tests_line, log


def review_stats(log: list[dict]) -> dict:
    counts = {a: sum(1 for e in log if e["action"] == a) for a in ACTIONS}
    counts["total"] = len(log)
    counts["by_human"] = sum(1 for e in log if e["source"] == "human")
    return counts


def make_approval_node(settings: Settings):
    def approval(state: dict):
        status = compute_status(state["errors"], state["route"])
        failed = status == "failed"
        plan = plan_comments([] if failed else state["accepted"], state["files"], settings)
        possible = [] if failed else state["possible_issues"]
        items = pending_items(plan, possible)
        saved = state.get("human_decisions") or {}

        would, would_possible, _, log = apply_decisions(items, saved, settings)
        preview = format_summary(state, would, status, possible=would_possible,
                                 stats=review_stats(log))  # fmt: skip
        payload = {
            "thread_id": state.get("thread_id", ""),
            "status": status,
            "items": [public_item(i, saved) for i in items],
            "would_post_inline": [
                {"file": f.file, "line": f.line, "severity": f.severity, "title": f.title}
                for f in would.inline
            ],
            "summary_preview": preview,
        }
        # This node runs again when resumed, so this also fires once per resume; the API
        # drops that stale copy. A failed review never waits for approval in the API.
        if status != "failed":
            emit("awaiting_approval", status=status, items=payload["items"],
                 would_post_inline=payload["would_post_inline"])  # fmt: skip
        answer = interrupt(payload)

        answer = answer or {}
        by_id = {i["id"]: i for i in items}
        merged = dict(saved)
        for d in answer.get("decisions", []):
            item = by_id.get(d.get("id"))
            if item is None or check_decision(item, d):
                continue  # the CLI validates first; ignore anything invalid here
            merged[d["id"]] = {"action": d["action"], "body": d.get("body")}

        if not answer.get("post"):
            return Command(update={"human_decisions": merged}, goto="approval")

        final, final_possible, tests_line, log = apply_decisions(items, merged, settings)
        return Command(
            update={
                "human_decisions": merged,
                "decisions_log": log,
                "review_stats": review_stats(log),
                "status": status,
                "inline": final.inline,
                "overflow": final.overflow,
                "nits": final.nits,
                "untested": [],
                "tests_line": tests_line,
                "approved_possible": final_possible,
            },
            goto="post_review",
        )

    return approval


def public_item(item: dict, saved: dict[str, dict]) -> dict:
    """Plain data for the interrupt payload and the CLI."""
    chosen = saved.get(item["id"])
    out = {
        "id": item["id"],
        "kind": item["kind"],
        "default": item["default"],
        "action": chosen["action"] if chosen else item["default"],
        "body_edit": (chosen or {}).get("body"),
    }
    if item["kind"] == "tests":
        return out | {"text": item["text"]}
    f: Finding = item["finding"]
    return out | {
        "file": f.file,
        "line": f.line,
        "severity": f.severity,
        "agent": f.agent,
        "category": f.category,
        "title": f.title,
        "body": f.body,
        "code": f.evidence,
        "suggestion": f.suggestion,
        "reason": item.get("reason"),
    }
