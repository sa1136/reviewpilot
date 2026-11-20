"""StateGraph wiring:
fetch_pr -> router -> (agents in parallel) -> validate -> judge -> retry or post_review."""

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from reviewpilot.agents import make_agent_node
from reviewpilot.config import MAX_LOOPS_CAP, Settings
from reviewpilot.diff import build_file_diffs
from reviewpilot.github import GitHubClient
from reviewpilot.judge import make_judge_node, make_router
from reviewpilot.llm import ModelFactory, real_model_factory
from reviewpilot.output import format_summary, inline_preview, plan_comments
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


def build_graph(
    settings: Settings,
    model_factory: ModelFactory | None = None,
    github=None,
    dry_run: bool = True,
):
    """`github` is anything with get_pull_request and list_files, plus post_review when
    dry_run is False (a GitHubClient by default). dry_run=True never writes to GitHub."""
    model_factory = model_factory or real_model_factory(settings)
    github = github or GitHubClient(settings.github_token, settings.github_api_url)
    enabled = settings.reviewers()

    def fetch_pr(state: ReviewState) -> dict:
        pr = github.get_pull_request(state["repo"], state["pr_number"])
        files = github.list_files(state["repo"], state["pr_number"])
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
        plan = plan_comments(accepted, state["files"], settings)
        summary = format_summary(state, plan, status)
        posted = None
        if dry_run:
            print(inline_preview(plan) + "\n\n" + summary)
        else:
            result = github.post_review(
                state["repo"], state["pr_number"], state["head_sha"], plan.inline, summary
            )
            posted = {
                "skipped_review": result.skipped_review,
                "review_url": result.review_url,
                "summary_url": result.summary_url,
                "summary_created": result.summary_created,
                "posted_inline": len(result.posted_inline),
                "moved_to_summary": len(result.moved_to_summary),
            }
            if result.skipped_review:
                print(f"Review for {state['head_sha'][:7]} already posted; skipped it.")
            else:
                print(f"Review: {result.review_url} ({len(result.posted_inline)} inline)")
            if result.moved_to_summary:
                print(
                    f"{len(result.moved_to_summary)} comments rejected by GitHub, moved to summary"
                )
            verb = "created" if result.summary_created else "updated"
            print(f"Summary {verb}: {result.summary_url}")
        return {
            "summary": summary,
            "status": status,
            "inline": plan.inline,
            "nits": plan.nits,
            "overflow": plan.overflow,
            "untested": plan.untested,
            "posted": posted,
        }

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
