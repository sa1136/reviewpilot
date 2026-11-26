"""Limits for the public demo. They exist only there (create_app(limits=...)); local runs have
none. Counters live in memory, so the demo must run as a single process."""

import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class PublicLimits:
    running_per_visitor: int = 1
    running_per_client: int = 2  # client = IP address
    running_total: int = 8
    new_per_client_per_hour: int = 20
    stored_per_visitor: int = 10
    stored_total: int = 200
    ttl_hours: float = 24
    decision_calls_per_review: int = 20
    streams_per_visitor: int = 4
    streams_total: int = 50
    stream_max_seconds: float = 290  # just under the web proxy's 300 s function limit
    max_body_bytes: int = 64_000


class HourlyRate:
    """At most `limit` hits per key in any rolling hour."""

    def __init__(self, limit: int, clock=time.monotonic):
        self.limit, self.clock = limit, clock
        self._hits: dict[str, deque[float]] = {}

    def _prune(self) -> None:
        cutoff = self.clock() - 3600
        for key, hits in list(self._hits.items()):
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                del self._hits[key]

    def full(self, key: str) -> bool:
        self._prune()
        return len(self._hits.get(key, ())) >= self.limit

    def hit(self, key: str) -> None:
        self._hits.setdefault(key, deque()).append(self.clock())


class BodyLimit:
    """ASGI middleware: 413 for a request body over `max_bytes`, with or without Content-Length."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        body, more = b"", True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > self.max_bytes:
                headers = [(b"content-type", b"application/json")]
                await send({"type": "http.response.start", "status": 413, "headers": headers})
                detail = b'{"detail":"request body is too large"}'
                return await send({"type": "http.response.body", "body": detail})
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)
