"""Local entry point, configured from the environment and .env:

    uv run uvicorn api.main:app --reload

It shares one review history with everyone who can reach it, so it refuses to start on Render.
The public deployment uses api.public:app.
"""

import os

from .app import create_app, parse_pr_url

__all__ = ["app", "create_app", "parse_pr_url"]

if os.getenv("RENDER"):  # set by Render on every service
    raise RuntimeError("api.main is for local use. On Render, start api.public:app instead.")

app = create_app()
