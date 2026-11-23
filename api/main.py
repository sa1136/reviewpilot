"""FastAPI app around the review engine.

    uv run uvicorn api.main:app --reload

Endpoints: GET /health, POST /reviews, GET /reviews, GET /reviews/{id},
GET /reviews/{id}/events (Server Sent Events), POST /reviews/{id}/decisions.
"""

import asyncio
import re
import secrets
from contextlib import ExitStack, asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from reviewpilot.checkpoint import open_checkpointer
from reviewpilot.config import Settings
from reviewpilot.github import GitHubClient
from reviewpilot.llm import ModelFactory, real_model_factory

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
) -> FastAPI:
    """Build the app. Tests pass fakes for `github` and `model_factory`."""
    settings = settings or Settings.from_env()
    resources = ExitStack()
    saver = resources.enter_context(open_checkpointer(settings))
    store = Store(settings.api_db)
    resources.callback(store.close)
    github = github or resources.enter_context(
        GitHubClient(settings.github_token, settings.github_api_url)
    )
    manager = ReviewManager(
        settings, store, saver, github, model_factory or real_model_factory(settings)
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        for task in list(manager.active.values()):
            task.cancel()
        resources.close()

    app = FastAPI(title="ReviewPilot API", version="0.1.0", lifespan=lifespan)
    app.state.manager = manager
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
        )

    api = APIRouter(dependencies=[Depends(require_token)])

    @api.post(
        "/reviews",
        response_model=ReviewRef,
        status_code=202,
        responses={200: {"model": ReviewRef, "description": "existing review"}},
    )
    async def start_review(body: StartReview):
        repo, pr = parse_pr_url(body.pr_url)
        status, ref = await manager.start(repo, pr, body.fresh)
        return JSONResponse(status_code=status, content=ReviewRef(**ref).model_dump())

    @api.get("/reviews", response_model=list[ReviewListItem])
    def list_reviews() -> list[ReviewListItem]:
        return [ReviewListItem(**row) for row in store.list_reviews()]

    @api.get("/reviews/{review_id}", response_model=ReviewSnapshot)
    def get_review(review_id: str) -> ReviewSnapshot:
        return ReviewSnapshot(**manager.snapshot(review_id))

    @api.post("/reviews/{review_id}/decisions", response_model=DecisionsAccepted, status_code=202)
    async def decide(review_id: str, body: Decisions) -> DecisionsAccepted:
        decisions = [d.model_dump(exclude_none=True) for d in body.decisions]
        return DecisionsAccepted(**await manager.decide(review_id, decisions, body.post))

    @api.get("/reviews/{review_id}/events")
    async def events(
        review_id: str,
        request: Request,
        until: str | None = Query(
            default=None, description="close after one of these event types, comma separated"
        ),
        last_event_id: str | None = Header(default=None),
    ):
        """Server Sent Events. Replays the log after Last-Event-ID (or from the start), then
        follows the run live. Closes when the review is posted or failed (or at `until`)."""
        if store.get_review(review_id) is None:
            raise ApiError(404, "unknown review")
        stop = {t.strip() for t in until.split(",")} if until else set()
        try:
            after = int(last_event_id or 0)
        except ValueError:
            after = 0

        async def stream():
            nonlocal after
            while not await request.is_disconnected():
                new = store.events_after(review_id, after)
                for event in new:
                    after = event["id"]
                    yield {"id": str(event["id"]), "event": event["type"],
                           "data": _json(event["data"])}  # fmt: skip
                    if event["type"] in stop:
                        return
                if not new:
                    if store.get_review(review_id)["status"] in TERMINAL:
                        return
                    await asyncio.sleep(POLL_SECONDS)

        return EventSourceResponse(stream(), ping=KEEPALIVE_SECONDS)

    app.include_router(api)
    return app


def _json(data: dict) -> str:
    import json

    return json.dumps(data, separators=(",", ":"))


app = create_app()
