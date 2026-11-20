"""StateGraph wiring: fetch_pr -> (agents in parallel) -> validate -> judge -> retry or post."""

from collections import defaultdict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from reviewpilot.agents import make_agent_node
from reviewpilot.config import MAX_LOOPS_CAP, Settings
from reviewpilot.diff import build_file_diffs
from reviewpilot.github import GitHubClient
from reviewpilot.judge import make_judge_node, make_router
from reviewpilot.llm import ModelFactory, real_model_factory
from reviewpilot.models import Finding
from reviewpilot.state import ReviewState, initial_state
from reviewpilot.validate import validate

# Each round is 3 supersteps (agents, validate, judge); plus fetch_pr and post_review.
RECURSION_LIMIT = 3 * MAX_LOOPS_CAP + 10


def compute_status(errors: list[dict], reviewers: list[str]) -> str:
    """Never "success" if anything failed. Judge failure, or every agent failing on the first
    pass, means nothing trustworthy was produced."""
    if any(e["node"] == "judge" for e in errors):
        return "failed"
    failed_first_pass = {e["node"] for e in errors if e["round"] == 1}
    if set(reviewers) <= failed_first_pass:
        return "failed"
    return "partial" if errors else "success"


def format_summary(state: dict, accepted: list[Finding], status: str) -> str:
    rounds = max(state["round"] - 1, 0)
    out = [
        f"## ReviewPilot summary for {state['repo']}#{state['pr_number']}",
        f"Status: {status} | rounds: {rounds} | files reviewed: {len(state['files'])} | "
        f"skipped: {len(state['skipped'])} | accepted findings: {len(accepted)}",
        "",
    ]
    if status == "failed":
        out.append("The review failed, so no findings are reported as confirmed.")
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
    if state["skipped"]:
        out.append("\nSkipped files:")
        out.extend(f"- {s.path}: {s.reason}" for s in state["skipped"])
    if state["errors"]:
        out.append("\nErrors:")
        out.extend(f"- {e['node']} (round {e['round']}): {e['error']}" for e in state["errors"])
    return "\n".join(out)


def build_graph(
    settings: Settings,
    model_factory: ModelFactory | None = None,
    github=None,
    dry_run: bool = True,
):
    """`github` is anything with get_pull_request and list_files (a GitHubClient by default)."""
    model_factory = model_factory or real_model_factory(settings)
    reviewers = settings.reviewers()

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

    def fan_out(state: ReviewState):
        if not state["files"]:
            return "post_review"
        return [Send(name, state) for name in reviewers]

    def post_review(state: ReviewState) -> dict:
        status = compute_status(state["errors"], reviewers)
        accepted = [] if status == "failed" else state["accepted"]
        summary = format_summary(state, accepted, status)
        if dry_run:
            print(summary)
        else:
            print("Posting to GitHub arrives in Stage 5. Printing instead:\n" + summary)
        return {"summary": summary, "status": status}

    graph = StateGraph(ReviewState)
    graph.add_node("fetch_pr", fetch_pr)
    for name in reviewers:
        graph.add_node(name, make_agent_node(name, model_factory(name)))
        graph.add_edge(name, "validate")
    graph.add_node("validate", validate)
    graph.add_node("judge", make_judge_node(model_factory("judge"), reviewers))
    graph.add_node("post_review", post_review)

    graph.add_edge(START, "fetch_pr")
    graph.add_conditional_edges("fetch_pr", fan_out, [*reviewers, "post_review"])
    graph.add_edge("validate", "judge")
    graph.add_conditional_edges("judge", make_router(settings), [*reviewers, "post_review"])
    graph.add_edge("post_review", END)
    return graph.compile()


def run_review(graph, repo: str, pr_number: int) -> dict:
    return graph.invoke(initial_state(repo, pr_number), {"recursion_limit": RECURSION_LIMIT})
