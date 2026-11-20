"""What gets shown: which accepted findings become inline comments, and the summary text."""

import time
from collections import Counter
from dataclasses import dataclass, field

from reviewpilot.config import PRICING_CHECKED, Settings
from reviewpilot.github import SUMMARY_MARKER, sha_marker
from reviewpilot.llm import run_totals
from reviewpilot.models import SEVERITY_ORDER, Finding
from reviewpilot.validate import DEF_NAME


@dataclass
class CommentPlan:
    inline: list[Finding]  # severity >= min_inline_severity, on an added line, within budget
    nits: list[Finding]  # below min_inline_severity: listed in the summary only
    overflow: list[Finding]  # inline worthy but over the max_comments budget: summary only
    untested: list[Finding] = field(default_factory=list)  # missing_test: one summary line
    tests_line: str | None = None  # after approval: the approved (or edited) tests line

    @property
    def shown(self) -> list[Finding]:
        return self.inline + self.overflow + self.nits + self.untested


def compute_status(errors: list[dict], agents: list[str]) -> str:
    """Never "success" if anything failed. Judge failure, or every routed agent failing on the
    first pass, means nothing trustworthy was produced."""
    if any(e["node"] == "judge" for e in errors):
        return "failed"
    failed_first_pass = {e["node"] for e in errors if e["round"] == 1}
    if agents and set(agents) <= failed_first_pass:
        return "failed"
    return "partial" if errors else "success"


def severity_rank(f: Finding) -> int:
    return SEVERITY_ORDER.index(f.severity)


def by_severity(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (severity_rank(f), f.file, f.line))


def plan_comments(accepted: list[Finding], files: list, settings: Settings) -> CommentPlan:
    added = {f.path: f.added_lines for f in files}
    cutoff = SEVERITY_ORDER.index(settings.min_inline_severity)
    untested = [f for f in accepted if f.category == "missing_test"]
    rest = by_severity([f for f in accepted if f.category != "missing_test"])
    worthy = [f for f in rest if severity_rank(f) <= cutoff and f.line in added.get(f.file, set())]
    nits = [f for f in rest if f not in worthy]
    budget = max(settings.max_comments, 0)
    return CommentPlan(
        inline=worthy[:budget],
        nits=nits,
        overflow=worthy[budget:],
        untested=sorted(untested, key=lambda f: (f.file, f.line)),
    )


def function_name(f: Finding) -> str:
    """Name of the untested function, from its def line evidence (falls back to file:line)."""
    match = DEF_NAME.search(f.evidence)
    return match.group(1) if match else f"{f.file}:{f.line}"


def untested_line(untested: list[Finding]) -> str:
    return "New functions without tests: " + ", ".join(function_name(f) for f in untested)


def finding_line(f: Finding) -> str:
    return f"- `{f.severity}` `{f.file}:{f.line}` {f.title} ({f.agent})"


def inline_preview(plan: CommentPlan) -> str:
    """Dry run view of what would be posted as inline comments."""
    if not plan.inline:
        return "Inline comments: none"
    out = [f"Inline comments ({len(plan.inline)}):"]
    for f in sorted(plan.inline, key=lambda f: (f.file, f.line)):
        out.append(f"- {f.file}:{f.line} [{f.severity}] {f.title} ({f.agent}, round {f.round})")
        out.append(f"  {f.body}")
        if f.suggestion:
            out.append(f"  Suggestion: {f.suggestion}")
    return "\n".join(out)


def format_summary(
    state: dict,
    plan: CommentPlan,
    status: str,
    possible: list[dict] | None = None,
    stats: dict | None = None,
) -> str:
    """Markdown for the sticky summary comment. `possible` are the possible issues to show
    (default: all in state); `stats` are the human approval counts, when known."""
    errors = state["errors"]
    shown = plan.shown
    if possible is None:
        possible = state.get("possible_issues", [])
    if status == "failed":
        possible = []
    sha = state.get("head_sha") or ""
    out = [
        SUMMARY_MARKER,
        sha_marker(sha),
        f"## ReviewPilot review of `{sha[:7] or 'unknown'}`",
        "",
        f"**Status:** {status} · {len(shown)} findings · {len(plan.inline)} inline comments",
    ]
    if stats and stats.get("total"):
        out.append(
            f"**Human review:** {stats['approve']} approved, {stats['edit']} edited, "
            f"{stats['promote']} promoted, {stats['reject']} rejected"
        )
    if status == "failed":
        out.append("\nThe review failed, so no findings are reported as confirmed.")
    elif not state["route"]:
        out.append("\nNo reviewer applies to these changes, so nothing was reviewed.")
    elif not shown and not possible:
        out.append(
            "\nNo issues confirmed, but the review was incomplete (see errors)."
            if status == "partial"
            else "\nNo issues found."
        )
    if errors:
        out.append("\n**Errors:**")
        out.extend(f"- {e['node']} (round {e['round']}): {e['error']}" for e in errors)

    out.append(f"\n**Route:** {', '.join(state['route']) or 'no agents'}")
    out.extend(f"- {agent}: {reason}" for agent, reason in state["route_reasons"].items())

    counts = Counter(f.severity for f in shown)
    out.append(
        "\n**By severity:** " + " · ".join(f"{s} {counts.get(s, 0)}" for s in SEVERITY_ORDER)
    )

    must_fix = [f for f in by_severity(shown) if f.severity in ("critical", "major")]
    if must_fix:
        out.append("\n### Must fix")
        out.extend(finding_line(f) for f in must_fix)

    if possible:
        out.append("\n### Possible issues (judge was not sure)")
        out.extend(f"{finding_line(p['finding'])}. Judge: {p['reason']}" for p in possible)

    if plan.overflow:
        out.append(f"\n### Not posted inline (over the {len(plan.inline)} comment budget)")
        out.extend(finding_line(f) for f in by_severity(plan.overflow))

    if plan.nits:
        out.append("\n### Nits")
        out.extend(f"- `{f.file}:{f.line}` {f.title}" for f in plan.nits)

    tests = plan.tests_line or (untested_line(plan.untested) if plan.untested else None)
    if tests:
        out.append("\n### Tests")
        out.append(tests)

    if state["skipped"]:
        out.append("\n### Skipped files")
        out.extend(f"- `{s.path}`: {s.reason}" for s in state["skipped"])

    out += ["", "---", footer(state)]
    return "\n".join(out)


def footer(state: dict) -> str:
    dropped = state.get("dropped", [])
    kinds = Counter(d["reason"].split(":")[0] for d in dropped)
    dropped_text = ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())) or "none"
    lane = [d["finding"] for d in dropped if d["reason"].startswith("out_of_lane")]
    lane_text = "; ".join(f"`{f.file}:{f.line}` {f.title} ({f.agent})" for f in lane)
    judged = sum(u.get("judged", 0) for u in state["usage"])
    confirmed = sum(u.get("accepted", 0) for u in state["usage"])
    totals = run_totals(state["usage"])
    cost = f"${totals['cost_usd']:.4f}" + ("" if totals["cost_complete"] else " (partial)")
    elapsed = (state.get("reviewed_at") or time.time()) - state["started_at"]
    lines = [
        f"Rounds: {max(state['round'] - 1, 0)} · judged {judged}, confirmed {confirmed}, "
        f"rejected {judged - confirmed} · dropped before the judge: {dropped_text}",
        f"Tokens: {totals['input_tokens']:,} in / {totals['output_tokens']:,} out "
        f"(cache {totals['cache_read_tokens']:,} read / {totals['cache_write_tokens']:,} written)"
        f" · est. cost {cost} (prices checked {PRICING_CHECKED}) · time {elapsed:.1f}s",
    ]
    if lane_text:
        lines.append(f"Dropped as out of lane: {lane_text}")
    return "<sub>" + "<br>\n".join(lines) + "</sub>"
