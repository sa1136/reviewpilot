"""Where review state is saved, so a run can stop at human approval and resume later, even in a
new process. SQLite by default; Postgres when REVIEWPILOT_CHECKPOINTER=postgres."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from reviewpilot.config import Settings

# Our own types stored in checkpoints, registered so LangGraph will deserialize them.
ALLOWED_TYPES = [("reviewpilot.models", name) for name in ("Finding", "FileDiff", "SkippedFile")]


def serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_TYPES)


def thread_id(repo: str, pr: int, head_sha: str) -> str:
    """One saved review per commit."""
    return f"{repo}#{pr}@{head_sha}"


def fresh_thread_id(base: str) -> str:
    """A new thread for the same commit (--fresh). `base` comes from thread_id()."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    return f"{base}~fresh-{stamp}"


@contextmanager
def open_checkpointer(settings: Settings) -> Iterator:
    if settings.checkpointer == "postgres":
        if not settings.database_url:
            raise ValueError("REVIEWPILOT_CHECKPOINTER=postgres needs DATABASE_URL")
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError as exc:  # pragma: no cover - optional extra
            raise RuntimeError("Install the postgres extra: uv sync --extra postgres") from exc
        with PostgresSaver.from_conn_string(settings.database_url) as saver:
            saver.serde = serializer()
            saver.setup()
            yield saver
        return

    path = Path(settings.checkpoint_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    try:
        yield SqliteSaver(conn, serde=serializer())
    finally:
        conn.close()


def list_threads(saver, prefix: str = "") -> list[str]:
    """Thread ids that start with `prefix`, most recently updated first."""
    latest: dict[str, str] = {}
    for item in saver.list(None):
        tid = item.config["configurable"]["thread_id"]
        ts = item.checkpoint["ts"]
        if tid.startswith(prefix) and ts > latest.get(tid, ""):
            latest[tid] = ts
    return sorted(latest, key=latest.get, reverse=True)
