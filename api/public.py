"""Entry point for the public deployment. It can only ever run the demo.

    uv run uvicorn api.public:app --workers 1

It refuses to start unless REVIEWPILOT_DEMO=1, REVIEWPILOT_API_TOKEN is long enough and no real
credential is present. It builds its settings here, never from .env, and passes the demo GitHub
and demo models in explicitly, so no real client is ever created. Reviews are private to each
visitor and limited (api/limits.py).
"""

import os
from collections.abc import Mapping

from fastapi import FastAPI

from reviewpilot.config import Settings
from reviewpilot.demo import DemoGitHub, demo_model_factory

from .app import create_app
from .limits import PublicLimits

MIN_TOKEN_LENGTH = 32
FORBIDDEN = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "DATABASE_URL",
)
TRACING = ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2")


def public_settings(env: Mapping[str, str]) -> Settings:
    """Demo settings, or RuntimeError saying why the server must not start. Names only: a
    value never appears in the message."""
    problems = []
    if env.get("REVIEWPILOT_DEMO") != "1":
        problems.append("REVIEWPILOT_DEMO must be exactly 1")
    present = [name for name in FORBIDDEN if env.get(name)]
    if present:
        problems.append(f"remove these variables: {', '.join(present)}")
    token = env.get("REVIEWPILOT_API_TOKEN", "")
    if len(token) < MIN_TOKEN_LENGTH:
        problems.append(f"REVIEWPILOT_API_TOKEN needs at least {MIN_TOKEN_LENGTH} characters")
    if problems:
        raise RuntimeError("The public demo will not start: " + "; ".join(problems))
    return Settings(
        demo=True,
        demo_delay_scale=float(env.get("REVIEWPILOT_DEMO_DELAY_SCALE") or 0.3),
        api_token=token,
        checkpointer="sqlite",
        checkpoint_db=env.get("REVIEWPILOT_CHECKPOINT_DB") or ".reviewpilot/public-checkpoints.db",
        api_db=env.get("REVIEWPILOT_API_DB") or ".reviewpilot/public-api.db",
        cors_origins=(),  # browsers only ever talk to the web proxy
    )


def create_public_app(env: Mapping[str, str] | None = None) -> FastAPI:
    settings = public_settings(os.environ if env is None else env)
    for name in TRACING:
        os.environ[name] = "false"
    scale = settings.demo_delay_scale
    return create_app(
        settings,
        github=DemoGitHub(delay_scale=scale),
        model_factory=demo_model_factory(delay_scale=scale),
        limits=PublicLimits(),
    )


def __getattr__(name: str):
    """Builds `app` when uvicorn asks for it, so tests can import this module freely."""
    if name == "app":
        globals()["app"] = create_public_app()
        return globals()["app"]
    raise AttributeError(name)
