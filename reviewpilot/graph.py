"""StateGraph wiring:
fetch_pr -> router -> (agents in parallel) -> validate -> judge -> retry or post_review."""

import time
from collections import defaultdict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from reviewpilot.agents import make_agent_node
from reviewpilot.config import MAX_LOOPS_CAP, PRICING_CHECKED, Settings
from reviewpilot.diff import build_file_diffs
from reviewpilot.github import GitHubClient
from reviewpilot.judge import make_judge_node, make_router
from reviewpilot.llm import ModelFactory, real_model_factory, run_totals
from reviewpilot.models import Finding
from reviewpilot.router import route
from reviewpilot.state import ReviewState, initial_state
from reviewpilot.validate import validate

# Each round is 3 supersteps (agents, validate, judge); plus fetch_pr, router and post_review.
RECURSION_LIMIT = 3 * MAX_LOOPS_CAP + 10


def compute_status(errors: list[dict], agents: list[str]) -> str:
    """Never "success" if anything failed. Judge failure, or every routed agent failing on the
    first pass, means nothing trustworthy was produced."""
    if any(e["node"] == "judge" for e in errors):
        return "failed"
    failed_first_pass = {e["node"] for e in errors if e["round"] == 1}
    if agents and set(agents) <= failed_first_pass:
        return "failed"
    return "partial" if errors else "success"


def format_summary(state: dict, accepted: list[Finding], status: str) -> str:
    out = [f"## ReviewPilot summary for {state['repo']}#{state['pr_number']}", ""]

    out.append(f"Route: {', '.join(state['route']) or '(no agents)'}")
    out.extend(f"- {agent}: {reason}" for agent, reason in state["route_reasons"].items())
    out.append("")

    if status == "failed":
        out.append("The review failed, so no findings are reported as confirmed.")
    elif not state["route"]:
        out.append("No reviewer applies to these changes, so nothing was reviewed.")
    elif not accepted:
        out.append(
            "No issues confirmed, but the review was incomplete (see errors)."
            if status == "partial"
            else "No issues found."
        )
    by_file: dict[str, list[Finding]] = defaultdict(list)
    for f in accepted:
        by_file[f.file].append(f)
    for path, items in by_file.items():
        out.append(f"### {path}")
        for f in sorted(items, key=lambda f: f.line):
            out.append(f"- line {f.line} [{f.severity}] {f.title} ({f.agent}, round {f.round})")
            out.append(f"  {f.body}")
            if f.suggestion:
                out.append(f"  Suggestion: {f.suggestion}")

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
        f"Judged: {judged} | confirmed: {confirmed} | rejected: {judged - confirmed} | "
        f"shown: {len(accepted)}",
        f"Tokens: {totals['input_tokens']:,} in / {totals['output_tokens']:,} out | "
        f"cache: {totals['cache_read_tokens']:,} read / {totals['cache_write_tokens']:,} written",
        f"Est. cost: {cost} (prices checked {PRICING_CHECKED}) | time: {elapsed:.1f}s",
    ]
    return "\n".join(out)


def build_graph(
    settings: Settings,
    model_factory: ModelFactory | None = None,
    github=None,
    dry_run: bool = True,
):
    """`github` is anything with get_pull_request and list_files (a GitHubClient by default)."""
    model_factory = model_factory or real_model_factory(settings)
    enabled = settings.reviewers()

    def fetch_pr(state: ReviewState) -> dict:
        if github is not None:
            pr = github.get_pull_request(state["repo"], state["pr_number"])
            files = github.list_files(state["repo"], state["pr_number"])
        else:
            with GitHubClient(settings.github_token, settings.github_api_url) as gh:
                pr = gh.get_pull_request(state["repo"], state["pr_number"])
                files = gh.list_files(state["repo"], state["pr_number"])
        diffs, skipped = build_file_diffs(
            files, settings.skip_globs, settings.max_files, settings.max_patch_chars
        )
        return {
            "head_sha": pr.head_sha,
            "pr_title": pr.title,
            "pr_body": pr.body,
            "files": diffs,
            "skipped": skipped,
            "round": 1,
        }

    def router(state: ReviewState) -> dict:
        agents, reasons = route([f.path for f in state["files"]], enabled)
        return {"route": agents, "route_reasons": reasons}

    def fan_out(state: ReviewState):
        if not state["files"] or not state["route"]:
            return "post_review"
        return [Send(name, state) for name in state["route"]]

    def post_review(state: ReviewState) -> dict:
        status = compute_status(state["errors"], state["route"])
        accepted = [] if status == "failed" else state["accepted"]
        summary = format_summary(state, accepted, status)
        if dry_run:
            print(summary)
        else:
            print("Posting to GitHub arrives in Stage 5. Printing instead:\n" + summary)
        return {"summary": summary, "status": status}

    graph = StateGraph(ReviewState)
    graph.add_node("fetch_pr", fetch_pr)
    graph.add_node("router", router)
    for name in enabled:
        model = model_factory(name)
        graph.add_node(name, make_agent_node(name, model, settings.model_for(name)))
        graph.add_edge(name, "validate")
    graph.add_node("validate", validate)
    judge_node = make_judge_node(model_factory("judge"), settings.model_for("judge"))
    graph.add_node("judge", judge_node)
    graph.add_node("post_review", post_review)

    graph.add_edge(START, "fetch_pr")
    graph.add_edge("fetch_pr", "router")
    graph.add_conditional_edges("router", fan_out, [*enabled, "post_review"])
    graph.add_edge("validate", "judge")
    graph.add_conditional_edges("judge", make_router(settings), [*enabled, "post_review"])
    graph.add_edge("post_review", END)
    return graph.compile()


def run_review(graph, repo: str, pr_number: int) -> dict:
    return graph.invoke(initial_state(repo, pr_number), {"recursion_limit": RECURSION_LIMIT})
