"""What gets shown: which accepted findings become inline comments, and the summary text."""

import time
from collections import Counter, defaultdict
from dataclasses import dataclass

from reviewpilot.config import PRICING_CHECKED, Settings
from reviewpilot.llm import run_totals
from reviewpilot.models import SEVERITY_ORDER, Finding


@dataclass
class CommentPlan:
    inline: list[Finding]  # severity >= min_inline_severity, on an added line, within budget
    nits: list[Finding]  # below min_inline_severity: listed in the summary only
    overflow: list[Finding]  # inline worthy but over the max_comments budget: summary only


def severity_rank(f: Finding) -> int:
    return SEVERITY_ORDER.index(f.severity)


def plan_comments(accepted: list[Finding], files: list, settings: Settings) -> CommentPlan:
    added = {f.path: f.added_lines for f in files}
    cutoff = SEVERITY_ORDER.index(settings.min_inline_severity)
    ordered = sorted(accepted, key=lambda f: (severity_rank(f), f.file, f.line))
    worthy = [
        f for f in ordered if severity_rank(f) <= cutoff and f.line in added.get(f.file, set())
    ]
    nits = [f for f in ordered if f not in worthy]
    budget = max(settings.max_comments, 0)
    return CommentPlan(inline=worthy[:budget], nits=nits, overflow=worthy[budget:])


def _finding_lines(f: Finding) -> list[str]:
    lines = [
        f"- line {f.line} [{f.severity}] {f.title} ({f.agent}, round {f.round})",
        f"  {f.body}",
    ]
    if f.suggestion:
        lines.append(f"  Suggestion: {f.suggestion}")
    return lines


def format_summary(state: dict, plan: CommentPlan, status: str) -> str:
    out = [f"## ReviewPilot summary for {state['repo']}#{state['pr_number']}", ""]

    out.append(f"Route: {', '.join(state['route']) or '(no agents)'}")
    out.extend(f"- {agent}: {reason}" for agent, reason in state["route_reasons"].items())
    out.append("")

    shown = plan.inline + plan.nits + plan.overflow
    if status == "failed":
        out.append("The review failed, so no findings are reported as confirmed.")
    elif not state["route"]:
        out.append("No reviewer applies to these changes, so nothing was reviewed.")
    elif not shown:
        out.append(
            "No issues confirmed, but the review was incomplete (see errors)."
            if status == "partial"
            else "No issues found."
        )

    if plan.inline:
        out.append(f"Inline comments ({len(plan.inline)}):")
        by_file: dict[str, list[Finding]] = defaultdict(list)
        for f in plan.inline:
            by_file[f.file].append(f)
        for path, items in by_file.items():
            out.append(f"### {path}")
            for f in sorted(items, key=lambda f: f.line):
                out.extend(_finding_lines(f))
    if plan.overflow:
        out.append(f"\nMore findings (over the {len(plan.inline)} inline comment budget):")
        for f in plan.overflow:
            out.append(f"- {f.file}:{f.line} [{f.severity}] {f.title}")
    if plan.nits:
        out.append("\nNits:")
        out.extend(f"- {f.file}:{f.line} {f.title}" for f in plan.nits)

    dropped = state.get("dropped", [])
    if dropped:
        kinds = Counter(d["reason"].split(":")[0] for d in dropped)
        out.append(
            f"\nDropped before the judge ({len(dropped)}): "
            + ", ".join(f"{kind} {n}" for kind, n in sorted(kinds.items()))
        )
        for d in dropped:
            if d["reason"].startswith("out_of_lane"):
                f = d["finding"]
                out.append(f"- out of lane: {f.file}:{f.line} {f.title} ({d['reason']})")

    if state["skipped"]:
        out.append("\nSkipped files:")
        out.extend(f"- {s.path}: {s.reason}" for s in state["skipped"])
    if state["errors"]:
        out.append("\nErrors:")
        out.extend(f"- {e['node']} (round {e['round']}): {e['error']}" for e in state["errors"])

    judged = sum(u.get("judged", 0) for u in state["usage"])
    confirmed = sum(u.get("accepted", 0) for u in state["usage"])
    totals = run_totals(state["usage"])
    cost = f"${totals['cost_usd']:.4f}" + ("" if totals["cost_complete"] else " (partial)")
    rounds = max(state["round"] - 1, 0)
    elapsed = time.time() - state["started_at"]
    out += [
        "",
        "---",
        f"Status: {status} | rounds: {rounds} | files reviewed: {len(state['files'])} | "
        f"skipped: {len(state['skipped'])}",
        f"Dropped: {len(dropped)} | judged: {judged} | confirmed: {confirmed} | "
        f"rejected: {judged - confirmed} | inline: {len(plan.inline)} | nits: {len(plan.nits)}",
        f"Tokens: {totals['input_tokens']:,} in / {totals['output_tokens']:,} out | "
        f"cache: {totals['cache_read_tokens']:,} read / {totals['cache_write_tokens']:,} written",
        f"Est. cost: {cost} (prices checked {PRICING_CHECKED}) | time: {elapsed:.1f}s",
    ]
    return "\n".join(out)
