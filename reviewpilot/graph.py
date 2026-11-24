"""StateGraph wiring:
fetch_pr -> router -> (agents in parallel) -> validate -> judge -> retry, or
approval (interrupt, human decides) -> post_review."""

import re
import uuid

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send

from reviewpilot.agents import make_agent_node
from reviewpilot.approval import make_approval_node
from reviewpilot.checkpoint import serializer
from reviewpilot.config import MAX_LOOPS_CAP, Settings
from reviewpilot.diff import build_file_diffs
from reviewpilot.events import emit
from reviewpilot.github import GitHubClient
from reviewpilot.judge import make_judge_node, make_router
from reviewpilot.llm import ModelFactory, real_model_factory
from reviewpilot.output import CommentPlan, compute_status, format_summary, inline_preview
from reviewpilot.router import route
from reviewpilot.state import ReviewState, initial_state
from reviewpilot.validate import validate

__all__ = ["RECURSION_LIMIT", "build_graph", "compute_status", "config_for", "run_review"]

# Each round is 3 supersteps (agents, validate, judge); plus fetch_pr, router, approval (which
# can repeat while decisions are saved without posting) and post_review.
RECURSION_LIMIT = 3 * MAX_LOOPS_CAP + 25


class LazyModel:
    """Creates the model on first use, so resuming at approval never needs model credentials."""

    def __init__(self, factory: ModelFactory, role: str):
        self._factory, self._role, self._model = factory, role, None

    def invoke(self, messages, config=None):
        if self._model is None:
            self._model = self._factory(self._role)
        return self._model.invoke(messages, config=config)


def trace_metadata(thread_id: str) -> dict:
    """LangSmith metadata from a thread id (owner/repo#pr@sha, maybe with ~fresh-...). Every
    node and model call in the run inherits it; model calls add route and round."""
    match = re.match(r"^([^#]+)#(\d+)@([^~]+)", thread_id)
    if not match:
        return {"thread_id": thread_id}
    repo, pr, sha = match.groups()
    return {"repo": repo, "pr": int(pr), "sha": sha, "thread_id": thread_id}


def config_for(thread_id: str) -> dict:
    meta = trace_metadata(thread_id)
    name = f"review {meta['repo']}#{meta['pr']}" if "pr" in meta else "review"
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": RECURSION_LIMIT,
        "metadata": meta,
        "run_name": name,
    }


def build_graph(
    settings: Settings,
    model_factory: ModelFactory | None = None,
    github=None,
    dry_run: bool = True,
    checkpointer=None,
):
    """`github` is anything with get_pull_request and list_files, plus post_review when
    dry_run is False (a GitHubClient by default). dry_run=True never writes to GitHub: the
    post step prints instead. `checkpointer` defaults to an in memory saver."""
    model_factory = model_factory or real_model_factory(settings)
    github = github or GitHubClient(settings.github_token, settings.github_api_url)
    enabled = settings.reviewers()

    def fetch_pr(state: ReviewState) -> dict:
        pr = github.get_pull_request(state["repo"], state["pr_number"])
        files = github.list_files(state["repo"], state["pr_number"])
        diffs, skipped = build_file_diffs(
            files, settings.skip_globs, settings.max_files, settings.max_patch_chars
        )
        emit("review_started", repo=state["repo"], pr=state["pr_number"], sha=pr.head_sha,
             title=pr.title, files=len(diffs), skipped=len(skipped))  # fmt: skip
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
        emit("route_decided", agents=agents, reasons=reasons)
        return {"route": agents, "route_reasons": reasons}

    def fan_out(state: ReviewState):
        if not state["files"] or not state["route"]:
            return "approval"
        return [Send(name, state) for name in state["route"]]

    def post_review(state: ReviewState) -> dict:
        """Posts exactly what the human approved (set by the approval node)."""
        plan = CommentPlan(
            inline=state["inline"],
            nits=state["nits"],
            overflow=state["overflow"],
            tests_line=state["tests_line"],
        )
        status = state["status"]
        summary = format_summary(
            state, plan, status, possible=state["approved_possible"], stats=state["review_stats"]
        )
        if dry_run:
            print(inline_preview(plan) + "\n\n" + summary)
            emit("posted", dry_run=True, review_url=None, summary_url=None,
                 inline_comments=len(plan.inline), skipped=False)  # fmt: skip
            return {"summary": summary, "posted": {"dry_run": True}}

        emit("posting", inline_comments=len(plan.inline))
        result = github.post_review(
            state["repo"],
            state["pr_number"],
            state["head_sha"],
            plan.inline,
            summary,
            force=state.get("fresh", False),
        )
        posted = {
            "skipped": result.skipped,
            "review_url": result.review_url,
            "summary_url": result.summary_url,
            "summary_created": result.summary_created,
            "posted_inline": len(result.posted_inline),
            "moved_to_summary": len(result.moved_to_summary),
        }
        emit(
            "posted",
            dry_run=False,
            review_url=result.review_url,
            summary_url=result.summary_url,
            inline_comments=len(result.posted_inline),
            moved_to_summary=len(result.moved_to_summary),
            skipped=result.skipped,
        )
        if result.skipped:
            print(f"Already posted for {state['head_sha'][:7]} on GitHub; wrote nothing.")
        else:
            if result.review_url:
                print(f"Review: {result.review_url} ({len(result.posted_inline)} inline)")
            if result.moved_to_summary:
                print(f"{len(result.moved_to_summary)} comments rejected by GitHub, in summary")
            verb = "created" if result.summary_created else "updated"
            print(f"Summary {verb}: {result.summary_url}")
        return {"summary": summary, "posted": posted}

    graph = StateGraph(ReviewState)
    graph.add_node("fetch_pr", fetch_pr)
    graph.add_node("router", router)
    for name in enabled:
        model = LazyModel(model_factory, name)
        graph.add_node(name, make_agent_node(name, model, settings.model_for(name)))
        graph.add_edge(name, "validate")
    graph.add_node("validate", validate)
    judge_model = LazyModel(model_factory, "judge")
    graph.add_node("judge", make_judge_node(judge_model, settings.model_for("judge")))
    graph.add_node(
        "approval", make_approval_node(settings), destinations=("approval", "post_review")
    )
    graph.add_node("post_review", post_review)

    graph.add_edge(START, "fetch_pr")
    graph.add_edge("fetch_pr", "router")
    graph.add_conditional_edges("router", fan_out, [*enabled, "approval"])
    graph.add_edge("validate", "judge")
    graph.add_conditional_edges("judge", make_router(settings), [*enabled, "approval"])
    graph.add_edge("post_review", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver(serde=serializer()))


def run_review(
    graph,
    repo: str,
    pr_number: int,
    decisions: list[dict] | None = None,
    post: bool = True,
    thread_id: str | None = None,
) -> dict:
    """Run start to finish in one go: review, then resume the approval step with `decisions`
    (none = defaults) and `post`. Used by tests and --auto-approve."""
    config = config_for(thread_id or f"{repo}#{pr_number}@run-{uuid.uuid4().hex[:8]}")
    graph.invoke(initial_state(repo, pr_number, config["configurable"]["thread_id"]), config)
    if graph.get_state(config).next == ("approval",):
        graph.invoke(Command(resume={"decisions": decisions or [], "post": post}), config)
    return graph.get_state(config).values
