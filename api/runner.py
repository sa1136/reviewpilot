"""Runs reviews in the background for the API, records their events, and builds snapshots.

The engine is the same graph the CLI uses. It runs in a worker thread (it is synchronous) and
is streamed with stream_mode="custom", so every event comes from LangGraph's stream writer
inside a node. Events go to the store's event log, which the SSE endpoint replays and tails.
"""

import asyncio
import base64
import json
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx
from langgraph.types import Command

from reviewpilot.approval import decision_errors
from reviewpilot.checkpoint import fresh_thread_id, list_threads, thread_id
from reviewpilot.config import AVAILABLE_AGENTS, Settings
from reviewpilot.graph import build_graph, config_for
from reviewpilot.llm import run_totals
from reviewpilot.state import initial_state

from .limits import HourlyRate, PublicLimits
from .store import Store

TERMINAL = ("posted", "failed")


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def encode_id(tid: str) -> str:
    return base64.urlsafe_b64encode(tid.encode()).decode().rstrip("=")


def decode_id(review_id: str) -> str:
    try:
        return base64.urlsafe_b64decode(review_id + "=" * (-len(review_id) % 4)).decode()
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiError(404, "unknown review") from exc


def severity_counts(findings) -> dict[str, int]:
    counts = Counter(f.severity if hasattr(f, "severity") else f.get("severity") for f in findings)
    return {s: counts.get(s, 0) for s in ("critical", "major", "minor", "nit")}


class ReviewManager:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        saver,
        github,
        model_factory,
        limits: PublicLimits | None = None,
    ):
        self.settings, self.store, self.github = settings, store, github
        self.limits = limits
        self.new_reviews = HourlyRate(limits.new_per_client_per_hour) if limits else None
        self.callers: dict[str, object] = {}  # thread id -> Caller of the running task
        self.decision_calls: Counter[str] = Counter()  # review id -> accepted decision calls
        self.graph = build_graph(
            settings, model_factory, github=github, dry_run=False, checkpointer=saver
        )
        self.saver = saver
        self.active: dict[str, asyncio.Task] = {}  # thread id -> running task
        self._lock = asyncio.Lock()
        self._secrets = [s for s in (settings.anthropic_api_key, settings.github_token,
                                     settings.api_token) if s]  # fmt: skip

    # --- helpers -----------------------------------------------------------------------------

    def scrub(self, text: str) -> str:
        """Never let a key or token reach a response, an event or a log line."""
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    def _state(self, tid: str):
        return self.graph.get_state(config_for(tid))

    def _ensure_row(
        self, rid: str, tid: str, repo: str, pr: int, sha: str, status: str, owner: str = ""
    ) -> None:
        if self.store.get_review(rid) is None:
            title = (self._state(tid).values or {}).get("pr_title", "")
            self.store.create_review(rid, tid, repo, pr, sha, status, title, owner)

    # --- visitors and limits (public demo only) -------------------------------------------------

    def require(self, rid: str, who) -> dict:
        """The review row, if this caller may see it. Someone else's review looks exactly like
        one that does not exist."""
        row = self.store.get_review(rid)
        if row is None or (self.limits and row["owner"] != who.owner):
            raise ApiError(404, "unknown review")
        return row

    def _delete(self, row: dict) -> None:
        self.saver.delete_thread(row["thread_id"])
        self.store.delete_review(row["review_id"])
        self.decision_calls.pop(row["review_id"], None)

    def _expire(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(hours=self.limits.ttl_hours)
        for row in self.store.list_reviews():
            if row["thread_id"] not in self.active and (
                datetime.fromisoformat(row["created_at"]) < cutoff
            ):
                self._delete(row)

    async def expire(self) -> None:
        if self.limits:
            async with self._lock:
                self._expire()

    def _make_room(self, owner: str) -> None:
        """Delete the oldest idle reviews so one more fits under both storage caps."""
        for scope, cap in ((owner, self.limits.stored_per_visitor),
                           (None, self.limits.stored_total)):  # fmt: skip
            rows = self.store.list_reviews(scope)
            idle = sorted(
                (r for r in rows if r["thread_id"] not in self.active),
                key=lambda r: r["updated_at"],
            )
            for row in idle[: max(len(rows) - cap + 1, 0)]:
                self._delete(row)

    def _check_running(self, who) -> None:
        if not self.limits:
            if len(self.active) >= self.settings.max_concurrent_reviews:
                raise ApiError(
                    429,
                    f"{len(self.active)} reviews are already running (limit "
                    f"{self.settings.max_concurrent_reviews}); try again when one finishes",
                )
            return
        # A task whose review already reached approval is only finishing up: not "running".
        running = [
            c for tid, c in self.callers.items()
            if tid in self.active
            and (self.store.get_review(encode_id(tid)) or {}).get("status")
            in ("running", "posting")
        ]  # fmt: skip
        if sum(c.owner == who.owner for c in running) >= self.limits.running_per_visitor:
            raise ApiError(429, "your demo review is still running; wait for it to finish")
        if sum(c.client == who.client for c in running) >= self.limits.running_per_client:
            raise ApiError(429, "too many reviews are running from your network; try again soon")
        if len(running) >= self.limits.running_total:
            raise ApiError(429, "the demo is busy; try again in a few seconds")

    def scrub_all(self, data):
        """scrub() applied to every string in a JSON compatible value."""
        return json.loads(self.scrub(json.dumps(data, default=str))) if self._secrets else data

    def _event(self, rid: str, event_type: str, data: dict, **review_fields) -> None:
        self.store.add_event(rid, event_type, self.scrub_all(data), **review_fields)

    def _fail(self, rid: str, message: str, **review_fields) -> None:
        message = self.scrub(message)
        self.store.add_event(rid, "error", {"message": message}, status="failed", error=message,
                             **review_fields)  # fmt: skip

    # --- starting and resuming ------------------------------------------------------------------

    async def start(self, repo: str, pr: int, fresh: bool, who) -> tuple[int, dict]:
        try:
            head = await asyncio.to_thread(self.github.get_pull_request, repo, pr)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ApiError(404, f"pull request {repo}#{pr} not found") from exc
            raise ApiError(502, f"GitHub error {exc.response.status_code}") from exc

        base = thread_id(repo, pr, head.head_sha)
        if self.limits:
            base = f"{base}~v-{who.owner}"  # one saved review per visitor and commit
        async with self._lock:
            if self.limits:
                self._expire()
                # Only this visitor's reviews, from the store (not by scanning checkpoints).
                existing = [] if fresh else self.store.thread_ids(who.owner, base)
            else:
                existing = [] if fresh else list_threads(self.saver, base)
            tid = existing[0] if existing else (fresh_thread_id(base) if fresh else base)
            rid = encode_id(tid)
            ref = {"review_id": rid, "thread_id": tid, "repo": repo, "pr": pr,
                   "sha": head.head_sha}  # fmt: skip

            if tid in self.active:
                return 200, {**ref, "status": self.store.get_review(rid)["status"]}

            graph_input = initial_state(repo, pr, tid, fresh=fresh)
            # A run that died before its first checkpoint has nothing saved: start it again.
            if existing and self._state(tid).values:
                snapshot = self._state(tid)
                if not snapshot.next:
                    self._ensure_row(rid, tid, repo, pr, head.head_sha, "posted", who.owner)
                    self.store.update_review(rid, status="posted")
                    return 200, {**ref, "status": "posted"}
                if snapshot.next == ("approval",):
                    status = "failed" if snapshot.interrupts[0].value["status"] == "failed" \
                        else "awaiting_approval"  # fmt: skip
                    self._ensure_row(rid, tid, repo, pr, head.head_sha, status, who.owner)
                    return 200, {**ref, "status": self.store.get_review(rid)["status"]}
                graph_input = None  # interrupted mid run: resume from the last checkpoint

            self._check_running(who)
            if self.limits and self.store.get_review(rid) is None:
                if self.new_reviews.full(who.client):
                    raise ApiError(429, "hourly demo limit reached for your network; try later")
                self.new_reviews.hit(who.client)
                self._make_room(who.owner)
            self._ensure_row(rid, tid, repo, pr, head.head_sha, "running", who.owner)
            self.store.update_review(rid, status="running", error=None)
            self.callers[tid] = who
            self._launch(rid, tid, graph_input, resuming=False)
            return 202, {**ref, "status": "running"}

    async def decide(self, rid: str, decisions: list[dict], post: bool) -> dict:
        row = self.store.get_review(rid)
        if row is None:
            raise ApiError(404, "unknown review")
        tid = row["thread_id"]
        async with self._lock:
            # A run that just reached approval may still be finishing its task; that is fine.
            if tid in self.active and row["status"] != "awaiting_approval":
                raise ApiError(409, f"review is {row['status']}; wait until it is done")
            snapshot = self._state(tid)
            if row["status"] != "awaiting_approval" or snapshot.next != ("approval",):
                raise ApiError(409, f"review is {row['status']}, not awaiting approval")
            payload = snapshot.interrupts[0].value
            errors = decision_errors(payload["items"], decisions)
            if errors:
                raise ApiError(400, "; ".join(errors))
            if post and not self.settings.github_token and not self.settings.demo:
                raise ApiError(400, "posting needs GITHUB_TOKEN on the server")
            if post and snapshot.values.get("fake"):
                raise ApiError(400, "this review used a fake model and cannot be posted")
            if self.limits:
                if self.decision_calls[rid] >= self.limits.decision_calls_per_review:
                    raise ApiError(429, "too many decision requests for this demo review")
                self.decision_calls[rid] += 1
            status = "posting" if post else "running"
            self.store.update_review(rid, status=status)
            command = Command(resume={"decisions": decisions, "post": post})
            self._launch(rid, tid, command, resuming=True)
            return {"review_id": rid, "status": status}

    def _launch(self, rid: str, tid: str, graph_input, resuming: bool) -> None:
        self.active[tid] = asyncio.create_task(self._run(rid, tid, graph_input, resuming))

    async def _run(self, rid: str, tid: str, graph_input, resuming: bool) -> None:
        try:
            await asyncio.to_thread(self._run_sync, rid, tid, graph_input, resuming)
        except Exception as exc:  # any crash: mark failed, emit error, never post
            self._fail(rid, f"{type(exc).__name__}: {exc}")
        finally:
            if self.active.get(tid) is asyncio.current_task():
                del self.active[tid]
                self.callers.pop(tid, None)

    def _run_sync(self, rid: str, tid: str, graph_input, resuming: bool) -> None:
        # On resume the approval node runs again and repeats its awaiting_approval event before
        # it reads the decisions; that copy is stale, so it is dropped.
        drop_stale_awaiting = resuming
        final: tuple[str, dict] | None = None  # written with the final status, see below
        for event in self.graph.stream(graph_input, config_for(tid), stream_mode="custom"):
            event = dict(event)
            event_type = event.pop("type")
            if event_type == "awaiting_approval" and drop_stale_awaiting:
                drop_stale_awaiting = False
                continue
            if event_type in ("awaiting_approval", "posted"):
                final = (event_type, event)
                continue
            if event_type == "review_started" and event.get("title"):
                self.store.update_review(rid, title=event["title"])
            if event_type == "posting":
                self.store.update_review(rid, status="posting")
            self._event(rid, event_type, event)

        snapshot = self._state(tid)
        values = snapshot.values
        cost = run_totals(values.get("usage", []))["cost_usd"]
        if snapshot.next == ("approval",):
            payload = snapshot.interrupts[0].value
            if payload["status"] == "failed":
                errors = "; ".join(e["error"] for e in values.get("errors", []))
                self._fail(rid, errors or "review failed", cost_usd=cost)
                return
            counts = severity_counts(i for i in payload["items"] if i.get("severity"))
            event = final[1] if final and final[0] == "awaiting_approval" else {}
            self._event(rid, "awaiting_approval", event, status="awaiting_approval",
                        counts=counts, cost_usd=cost)  # fmt: skip
        elif not snapshot.next:
            shown = values.get("inline", []) + values.get("overflow", []) + values.get("nits", [])
            event = final[1] if final and final[0] == "posted" else {}
            self._event(rid, "posted", event, status="posted", posted=values.get("posted") or {},
                        counts=severity_counts(shown), cost_usd=cost)  # fmt: skip

    # --- reading --------------------------------------------------------------------------------

    def snapshot(self, rid: str) -> dict:
        return self.scrub_all(self._snapshot(rid))

    def _snapshot(self, rid: str) -> dict:
        row = self.store.get_review(rid)
        if row is None:
            raise ApiError(404, "unknown review")
        state = self._state(row["thread_id"])
        values = state.values or {}
        events = self.store.events_after(rid)
        waiting = state.next == ("approval",) and row["status"] == "awaiting_approval"
        payload = state.interrupts[0].value if state.interrupts else {}
        route = values.get("route", [])
        posted = values.get("posted") if row["status"] == "posted" else None
        return {
            "review_id": rid,
            "thread_id": row["thread_id"],
            "repo": row["repo"],
            "pr": row["pr"],
            "title": row["title"] or values.get("pr_title", ""),
            "sha": row["sha"],
            "status": row["status"],
            "error": row["error"],
            "route": route,
            "route_reasons": values.get("route_reasons", {}),
            "agents": agent_statuses(route, values.get("route_reasons", {}), events),
            "pending": payload.get("items", []) if waiting else [],
            "summary_preview": payload.get("summary_preview") if waiting else values.get("summary"),
            "counts": row["counts"],
            "usage": run_totals(values.get("usage", [])),
            "review_stats": values.get("review_stats") or {},
            "posted": posted_view(posted) if posted and not posted.get("dry_run") else None,
            "last_event_id": events[-1]["id"] if events else 0,
            **timeline(events),
        }


def timeline(events: list[dict]) -> dict:
    """Rounds, file counts and review time from the event log (what the UI shows on reload)."""
    rounds: dict[int, dict] = {}
    started = reviewed = None
    files = skipped = 0
    for e in events:
        data, kind = e["data"], e["type"]
        if kind == "review_started":
            started = started or data.get("at")
            files, skipped = data.get("files", 0), data.get("skipped", 0)
        elif kind == "validate_done":
            r = rounds.setdefault(data["round"], {"round": data["round"]})
            r |= {"kept": data.get("kept"), "dropped": data.get("dropped") or {}}
        elif kind == "judge_round":
            r = rounds.setdefault(data["round"], {"round": data["round"]})
            r |= {k: data.get(k) for k in ("judged", "confirmed", "rejected")}
            r["retrying"] = data.get("retrying") or []
            reviewed = data.get("at")
        elif kind == "awaiting_approval" and reviewed is None:
            reviewed = data.get("at")
    seconds = None
    if started and reviewed:
        seconds = (
            datetime.fromisoformat(reviewed) - datetime.fromisoformat(started)
        ).total_seconds()
    return {"rounds": [rounds[k] for k in sorted(rounds)], "files": files,
            "skipped_files": skipped, "started_at": started, "review_seconds": seconds}  # fmt: skip


def agent_statuses(route: list[str], reasons: dict[str, str], events: list[dict]) -> list[dict]:
    """Per agent status from the event log (the most recent event for that agent wins)."""
    out = []
    for agent in AVAILABLE_AGENTS:
        mine = [e for e in events if e["data"].get("agent") == agent]
        status = {"agent": agent, "reason": reasons.get(agent, ""), "findings": 0,
                  "latency_ms": 0, "error": None}  # fmt: skip
        if agent not in route:
            status["status"] = "skipped" if reasons else "queued"
            out.append(status)
            continue
        for e in mine:
            status["findings"] += e["data"].get("findings", 0)
            status["latency_ms"] += e["data"].get("latency_ms", 0)
        last = mine[-1]["type"] if mine else None
        status["status"] = {"agent_started": "running", "agent_finished": "done",
                            "agent_failed": "failed"}.get(last, "queued")  # fmt: skip
        if last == "agent_failed":
            status["error"] = mine[-1]["data"].get("error")
        out.append(status)
    return out


def posted_view(posted: dict) -> dict:
    """The engine's post result in the API's Posted shape."""
    return {
        "review_url": posted.get("review_url"),
        "summary_url": posted.get("summary_url"),
        "inline_comments": posted.get("posted_inline", 0),
        "moved_to_summary": posted.get("moved_to_summary", 0),
        "skipped": bool(posted.get("skipped")),
    }
