"""Who is calling the public demo. The web proxy gives each browser a random id in an HttpOnly
cookie and passes it here, with the client's IP. Both headers are trusted only because every
request also needs the API token, which only the proxy has. We keep hashes, never raw values."""

import hashlib
import re
from dataclasses import dataclass

from .runner import ApiError

VISITOR_HEADER = "x-reviewpilot-visitor"
CLIENT_HEADER = "x-reviewpilot-client"
VISITOR_ID = re.compile(r"[A-Za-z0-9_-]{22,64}")


@dataclass(frozen=True)
class Caller:
    owner: str = ""  # hash of the visitor id; "" for local runs, which have no visitors
    client: str = ""  # hash of the IP address


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def caller_from(headers) -> Caller:
    visitor = headers.get(VISITOR_HEADER, "")
    if not VISITOR_ID.fullmatch(visitor):
        raise ApiError(400, "missing visitor id; use the ReviewPilot web app")
    owner = _hash(visitor)
    address = headers.get(CLIENT_HEADER, "")[:100]
    return Caller(owner=owner, client=_hash(address) if address else owner)
