"""Small SQLite store next to the checkpoints: review history plus an append only event log per
review (for SSE replay). Thread safe: the engine runs in worker threads."""

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
create table if not exists reviews (
    review_id text primary key,
    thread_id text not null,
    repo text not null,
    pr integer not null,
    title text not null default '',
    sha text not null,
    status text not null,
    created_at text not null,
    updated_at text not null,
    counts text not null default '{}',
    cost_usd real not null default 0,
    error text,
    posted text,
    owner text not null default '',
    client text not null default '',
    used_at text not null default ''
);
create table if not exists events (
    review_id text not null,
    seq integer not null,
    type text not null,
    data text not null,
    primary key (review_id, seq)
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            columns = [r["name"] for r in self._conn.execute("pragma table_info(reviews)")]
            for column in ("owner", "client", "used_at"):  # history files from before the demo
                if column not in columns:
                    self._conn.execute(
                        f"alter table reviews add column {column} text not null default ''"
                    )

    def close(self) -> None:
        self._conn.close()

    def create_review(
        self, review_id, thread_id, repo, pr, sha, status, title="", owner="", client=""
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "insert or ignore into reviews (review_id, thread_id, repo, pr, title, sha, "
                "status, created_at, updated_at, owner, client, used_at) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    review_id,
                    thread_id,
                    repo,
                    pr,
                    title,
                    sha,
                    status,
                    now(),
                    now(),
                    owner,
                    client,
                    now(),
                ),  # fmt: skip
            )

    def update_review(self, review_id: str, **fields) -> None:
        for key in ("counts", "posted"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
        fields["updated_at"] = now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, self._conn:
            self._conn.execute(
                f"update reviews set {cols} where review_id = ?", (*fields.values(), review_id)
            )

    def get_review(self, review_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "select * from reviews where review_id = ?", (review_id,)
            ).fetchone()
        return _review(row) if row else None

    def list_reviews(self, owner: str | None = None, client: str | None = None) -> list[dict]:
        """Newest first. All reviews, or only `owner`'s, or only `client`'s."""
        where, args = "", ()
        if owner is not None:
            where, args = "where owner = ? ", (owner,)
        elif client is not None:
            where, args = "where client = ? ", (client,)
        with self._lock:
            rows = self._conn.execute(
                f"select * from reviews {where}order by created_at desc", args
            ).fetchall()
        return [_review(r) for r in rows]

    def thread_ids(self, owner: str, base: str) -> list[str]:
        """`owner`'s threads for `base` (the thread itself and its fresh runs), latest first."""
        with self._lock:
            rows = self._conn.execute(
                "select thread_id from reviews where owner = ? order by updated_at desc, "
                "rowid desc",
                (owner,),
            ).fetchall()
        return [
            r["thread_id"]
            for r in rows
            if r["thread_id"] == base or r["thread_id"].startswith(f"{base}~")
        ]

    def touch(self, review_id: str) -> None:
        """Record that someone looked at or acted on this review."""
        with self._lock, self._conn:
            self._conn.execute(
                "update reviews set used_at = ? where review_id = ?", (now(), review_id)
            )

    def delete_review(self, review_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("delete from events where review_id = ?", (review_id,))
            self._conn.execute("delete from reviews where review_id = ?", (review_id,))

    def add_event(self, review_id: str, event_type: str, data: dict, **review_fields) -> int:
        """Append an event. `review_fields` update the review row in the same transaction, so
        a reader never sees a final event without its status (or the other way round)."""
        for key in ("counts", "posted"):
            if key in review_fields and not isinstance(review_fields[key], str):
                review_fields[key] = json.dumps(review_fields[key])
        with self._lock, self._conn:
            if review_fields:
                review_fields["updated_at"] = now()
                cols = ", ".join(f"{k} = ?" for k in review_fields)
                self._conn.execute(
                    f"update reviews set {cols} where review_id = ?",
                    (*review_fields.values(), review_id),
                )
            (last,) = self._conn.execute(
                "select coalesce(max(seq), 0) from events where review_id = ?", (review_id,)
            ).fetchone()
            seq = last + 1
            payload = {"type": event_type, "at": now(), **data}
            self._conn.execute(
                "insert into events (review_id, seq, type, data) values (?, ?, ?, ?)",
                (review_id, seq, event_type, json.dumps(payload)),
            )
        return seq

    def events_after(self, review_id: str, after: int = 0) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "select seq, type, data from events where review_id = ? and seq > ? order by seq",
                (review_id, after),
            ).fetchall()
        return [{"id": r["seq"], "type": r["type"], "data": json.loads(r["data"])} for r in rows]


def _review(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["counts"] = json.loads(out["counts"] or "{}")
    out["posted"] = json.loads(out["posted"]) if out["posted"] else None
    return out
