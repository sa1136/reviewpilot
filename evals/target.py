"""Eval target: one full review through the judge, auto approved, never posted.

Never posts: the GitHub client is wrapped so it can only read, and the graph runs in dry run
mode (post_review prints instead of writing; its output is swallowed here).
"""

import contextlib
import io
import threading
import time
import uuid

from langgraph.types import Command

from reviewpilot.config import Settings
from reviewpilot.github import GitHubClient
from reviewpilot.graph import build_graph, config_for
from reviewpilot.llm import ModelFactory, run_totals
from reviewpilot.models import Finding
from reviewpilot.state import initial_state

USAGE_KEYS = (
    "node",
    "round",
    "model",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "latency_ms",
    "cost_usd",
)


class ReadOnlyGitHub:
    """Only the two read calls a review needs, cached per PR so repetitions fetch once.
    There is no post_review, so a bug elsewhere cannot write to GitHub during an eval."""

    def __init__(self, client):
        self._client = client
        self._cache: dict[tuple, object] = {}
        self._lock = threading.Lock()

    def _cached(self, name: str, repo: str, number: int):
        key = (name, repo, number)
        with self._lock:
            if key not in self._cache:
                self._cache[key] = getattr(self._client, name)(repo, number)
            return self._cache[key]

    def get_pull_request(self, repo: str, number: int):
        return self._cached("get_pull_request", repo, number)

    def list_files(self, repo: str, number: int):
        return self._cached("list_files", repo, number)


def finding_dict(f: Finding) -> dict:
    return {
        "file": f.file,
        "line": f.line,
        "agent": f.agent,
        "category": f.category,
        "severity": f.severity,
        "title": f.title,
    }


def _stream(graph, graph_input, config, trajectory: list[str]) -> None:
    for chunk in graph.stream(graph_input, config, stream_mode="updates"):
        trajectory.extend(node for node in chunk if not node.startswith("__"))


def make_target(settings: Settings, model_factory: ModelFactory | None = None, github=None):
    """Returns target(inputs) -> outputs for langsmith.evaluate. One graph per call, with an in
    memory checkpointer, so runs never share state."""
    github = ReadOnlyGitHub(github or GitHubClient(settings.github_token, settings.github_api_url))
    max_rounds = 1 + settings.effective_max_retries

    def target(inputs: dict) -> dict:
        repo, number = inputs["repo"], int(inputs["pr"])
        sha = github.get_pull_request(repo, number).head_sha
        tid = f"{repo}#{number}@{sha}~eval-{uuid.uuid4().hex[:8]}"
        config = config_for(tid)
        graph = build_graph(settings, model_factory, github=github, dry_run=True)

        trajectory: list[str] = []
        start = time.perf_counter()
        _stream(graph, initial_state(repo, number, tid), config, trajectory)
        latency_s = time.perf_counter() - start  # fetch to approval: what a reviewer waits
        if graph.get_state(config).next == ("approval",):
            resume = Command(resume={"decisions": [], "post": True})  # defaults = auto approve
            with contextlib.redirect_stdout(io.StringIO()):
                _stream(graph, resume, config, trajectory)

        state = graph.get_state(config).values
        totals = run_totals(state.get("usage", []))
        return {
            "route": state.get("route", []),
            "route_reasons": state.get("route_reasons", {}),
            "enabled": settings.reviewers(),
            "inline": [finding_dict(f) for f in state.get("inline", [])],
            "overflow": [finding_dict(f) for f in state.get("overflow", [])],
            "nits": [finding_dict(f) for f in state.get("nits", [])],
            "accepted": [finding_dict(f) for f in state.get("accepted", [])],
            "possible_issues": [
                {**finding_dict(p["finding"]), "reason": p["reason"]}
                for p in state.get("possible_issues", [])
            ],
            "tests_line": state.get("tests_line"),
            "rounds": trajectory.count("judge"),
            "max_rounds": max_rounds,
            "trajectory": trajectory,
            "status": state.get("status"),
            "errors": [e["error"][:300] for e in state.get("errors", [])],
            "dropped": len(state.get("dropped", [])),
            "calls": len(state.get("usage", [])),
            "input_tokens": totals["input_tokens"],
            "output_tokens": totals["output_tokens"],
            "cache_read_tokens": totals["cache_read_tokens"],
            "cost_usd": round(totals["cost_usd"], 6),
            "latency_s": round(latency_s, 2),
            "models": sorted({u["model"] for u in state.get("usage", []) if "model" in u}),
            "usage": [
                {k: u.get(k) for k in USAGE_KEYS} for u in state.get("usage", []) if "model" in u
            ],
        }

    return target
