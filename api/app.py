"""FastAPI app factory around the review engine. Importing this builds nothing; the entry points are
api.main (local) and api.public (the public demo).

Endpoints: GET /health, POST /reviews, GET /reviews, GET /reviews/{id},
GET /reviews/{id}/events (Server Sent Events), POST /reviews/{id}/decisions.
"""

import asyncio
import json
import re
import secrets
from collections import Counter
from contextlib import ExitStack, asynccontextmanager
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from reviewpilot.checkpoint import open_checkpointer
from reviewpilot.config import Settings
from reviewpilot.demo import DemoGitHub, demo_model_factory, load_fixture
from reviewpilot.github import GitHubClient
from reviewpilot.llm import ModelFactory, real_model_factory

from .limits import BodyLimit, PublicLimits
from .runner import TERMINAL, ApiError, ReviewManager
from .schemas import (
    Decisions,
    DecisionsAccepted,
    Health,
    ReviewListItem,
    ReviewRef,
    ReviewSnapshot,
    StartReview,
)
from .store import Store
from .visitors import Caller, caller_from

PR_URL = re.compile(r"^https?://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)(?:[/?#].*)?$")
KEEPALIVE_SECONDS = 15
POLL_SECONDS = 0.2


def parse_pr_url(url: str) -> tuple[str, int]:
    match = PR_URL.match(url.strip())
    if not match:
        raise ApiError(400, "pr_url must look like https://github.com/owner/repo/pull/123")
    owner, repo, number = match.groups()
    return f"{owner}/{repo}", int(number)


def create_app(
    settings: Settings | None = None,
    github=None,
    model_factory: ModelFactory | None = None,
    limits: PublicLimits | None = None,
) -> FastAPI:
    """Build the app. Tests pass fakes for `github` and `model_factory`. With settings.demo the
    fixture PR, replayed model answers and a fake poster are used instead (no keys needed).
    `limits` makes it the public demo: reviews are private to each visitor and capped."""
    settings = settings or Settings.from_env()
    if limits and not (settings.demo and settings.api_token):
        raise ValueError("public limits need demo mode and an API token")
    if settings.demo:
        # Price the replayed calls with the model the recorded run used.
        model = load_fixture()["model"]
        settings = replace(settings, agent_model=model, judge_model=model)
        github = github or DemoGitHub(delay_scale=settings.demo_delay_scale)
        model_factory = model_factory or demo_model_factory(delay_scale=settings.demo_delay_scale)
    resources = ExitStack()
    saver = resources.enter_context(open_checkpointer(settings))
    store = Store(settings.api_db)
    resources.callback(store.close)
    github = github or resources.enter_context(
        GitHubClient(settings.github_token, settings.github_api_url)
    )
    manager = ReviewManager(
        settings, store, saver, github, model_factory or real_model_factory(settings), limits
    )
    streams: Counter[str] = Counter()  # open event streams per visitor

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        for task in list(manager.active.values()):
            task.cancel()
        resources.close()

    hidden = {"docs_url": None, "redoc_url": None, "openapi_url": None} if limits else {}
    app = FastAPI(title="ReviewPilot API", version="0.1.0", lifespan=lifespan, **hidden)
    app.state.manager = manager
    if limits:
        app.add_middleware(BodyLimit, max_bytes=limits.max_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if not settings.api_token:
            return
        expected = f"Bearer {settings.api_token}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise HTTPException(401, "missing or wrong API token")

    @app.get("/health", response_model=Health)
    def health() -> Health:
        return Health(
            ok=True,
            anthropic_configured=bool(settings.anthropic_api_key),
            github_configured=bool(settings.github_token),
            checkpointer=settings.checkpointer,
            auth_required=bool(settings.api_token),
            demo=settings.demo,
            public=limits is not None,
        )

    api = APIRouter(dependencies=[Depends(require_token)])

    def caller(request: Request) -> Caller:
        return caller_from(request.headers) if limits else Caller()

    Who = Annotated[Caller, Depends(caller)]

    def streams_full(owner: str) -> bool:
        return limits is not None and (
            streams[owner] >= limits.streams_per_visitor or streams.total() >= limits.streams_total
        )

    @api.post(
        "/reviews",
        response_model=ReviewRef,
        status_code=202,
        responses={200: {"model": ReviewRef, "description": "existing review"}},
    )
    async def start_review(body: StartReview, who: Who):
        repo, pr = parse_pr_url(body.pr_url)
        status, ref = await manager.start(repo, pr, body.fresh, who)
        return JSONResponse(status_code=status, content=ReviewRef(**ref).model_dump())

    @api.get("/reviews", response_model=list[ReviewListItem])
    async def list_reviews(who: Who) -> list[ReviewListItem]:
        await manager.expire()
        return [ReviewListItem(**row) for row in store.list_reviews(who.owner or None)]

    @api.get("/reviews/{review_id}", response_model=ReviewSnapshot)
    def get_review(review_id: str, who: Who) -> ReviewSnapshot:
        manager.require(review_id, who)
        return ReviewSnapshot(**manager.snapshot(review_id))

    @api.post("/reviews/{review_id}/decisions", response_model=DecisionsAccepted, status_code=202)
    async def decide(review_id: str, body: Decisions, who: Who) -> DecisionsAccepted:
        manager.require(review_id, who)
        decisions = [d.model_dump(exclude_none=True) for d in body.decisions]
        return DecisionsAccepted(**await manager.decide(review_id, decisions, body.post))

    @api.get("/reviews/{review_id}/events")
    async def events(
        review_id: str,
        request: Request,
        who: Who,
        until: str | None = Query(
            default=None, description="close after one of these event types, comma separated"
        ),
        last_event_id: str | None = Header(default=None),
    ):
        """Server Sent Events. Replays the log after Last-Event-ID (or from the start), then
        follows the run live. Closes when the review is posted or failed (or at `until`)."""
        manager.require(review_id, who)
        if streams_full(who.owner):
            raise ApiError(429, "too many open event streams; close a tab and try again")
        stop = {t.strip() for t in until.split(",")} if until else set()
        try:
            after = int(last_event_id or 0)
        except ValueError:
            after = 0

        async def follow():
            nonlocal after
            loop = asyncio.get_running_loop()
            deadline = loop.time() + limits.stream_max_seconds if limits else None
            while not await request.is_disconnected():
                new = store.events_after(review_id, after)
                for event in new:
                    after = event["id"]
                    yield {"id": str(event["id"]), "event": event["type"],
                           "data": _json(event["data"])}  # fmt: skip
                    if event["type"] in stop:
                        return
                if deadline and loop.time() >= deadline:
                    return  # the client reconnects with Last-Event-ID
                if not new:
                    row = store.get_review(review_id)
                    if row is None or row["status"] in TERMINAL:  # None: expired meanwhile
                        return
                    await asyncio.sleep(POLL_SECONDS)

        async def stream():
            if streams_full(who.owner):  # checked again where the count is taken
                return
            streams[who.owner] += 1
            try:
                async for message in follow():
                    yield message
            finally:
                streams[who.owner] -= 1
                if not streams[who.owner]:
                    del streams[who.owner]

        return EventSourceResponse(stream(), ping=KEEPALIVE_SECONDS)

    app.include_router(api)
    return app


def _json(data: dict) -> str:
    return json.dumps(data, separators=(",", ":"))
