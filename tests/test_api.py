"""API tests: FakeModel, a fake GitHub transport, httpx AsyncClient on the ASGI app, temp SQLite.
No network and no real model calls."""

import json
import threading

import httpx
import pytest

from api.main import create_app
from reviewpilot.config import Settings
from reviewpilot.github import GitHubClient
from reviewpilot.llm import FakeModel, fake_factory
from reviewpilot.models import AgentOutput, Finding, FindingDecision, JudgeVerdict

pytestmark = pytest.mark.anyio

OWNER_REPO = "o/r"
PATCH = "@@ -0,0 +1,3 @@\n+a = 1\n+b = 2\n+c = 3"
SECRET = "sk-test-very-secret-123"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeGitHub:
    """GitHub REST API stand in serving PRs 1 and 2 of o/r; records every request."""

    def __init__(self, fail_files: bool = False):
        self.requests: list[tuple[str, str, dict | None]] = []
        self.fail_files = fail_files  # answer the PR files request with a 500

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        method, path = request.method, request.url.path
        self.requests.append((method, path, body))
        prefix = f"/repos/{OWNER_REPO}"
        for n in (1, 2):
            if method == "GET" and path == f"{prefix}/pulls/{n}":
                head = {"sha": f"sha{n}abcdef", "ref": "f", "repo": {"full_name": OWNER_REPO}}
                base = {"ref": "main", "repo": {"full_name": OWNER_REPO}}
                pr = {"title": f"PR {n}", "body": "", "head": head, "base": base}
                return httpx.Response(200, json=pr)
            if method == "GET" and path == f"{prefix}/pulls/{n}/files":
                if self.fail_files:
                    return httpx.Response(500, json={"message": "boom"})
                return httpx.Response(200, json=[{"filename": "app.py", "status": "added",
                                                  "patch": PATCH}])  # fmt: skip
        if method == "GET" and path.endswith(("/reviews", "/comments")):
            return httpx.Response(200, json=[])
        if method == "POST" and path.endswith("/reviews"):
            return httpx.Response(200, json={"html_url": "https://gh/review/1"})
        if method == "POST" and path.endswith("/comments"):
            return httpx.Response(201, json={"html_url": "https://gh/summary/1"})
        return httpx.Response(404, json={"message": "Not Found"})

    def writes(self) -> list[tuple[str, str]]:
        return [(m, p) for m, p, _ in self.requests if m != "GET"]


def finding(line: int, title: str) -> Finding:
    return Finding(file="app.py", line=line, severity="major", category="bug", title=title,
                   body="b", evidence="abc"[line - 1] + f" = {line}", confidence=0.9)  # fmt: skip


def confirm_all(msgs):
    ids = [part.split()[0] for part in msgs[-1].content.split("- id=")[1:]]
    return JudgeVerdict(decisions=[FindingDecision(finding_id=i, decision="confirm") for i in ids])


def models(correctness=None, judge=None):
    return {
        "correctness": correctness
        or FakeModel(lambda m: AgentOutput(findings=[finding(1, "A"), finding(2, "B")])),
        "quality": FakeModel(lambda m: AgentOutput()),
        "judge": judge or FakeModel(confirm_all),
    }


def gated(gate: threading.Event) -> FakeModel:
    """An agent that waits for `gate`, to keep a review in the running state."""

    def respond(_msgs):
        gate.wait(10)
        return AgentOutput()

    return FakeModel(respond)


def make_client(tmp_path, model_map=None, github=None, **settings):
    github = github or FakeGitHub()
    config = {
        "num_agents": 2,
        "github_token": "gh-token",
        "anthropic_api_key": SECRET,
        "checkpoint_db": str(tmp_path / "checkpoints.db"),
        "api_db": str(tmp_path / "api.db"),
        **settings,
    }
    app = create_app(
        Settings(**config),
        github=GitHubClient("gh-token", transport=httpx.MockTransport(github)),
        model_factory=fake_factory(model_map or models()),
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return client, github


def parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if line.startswith(":") or ": " not in line:
                continue  # keepalive comment
            key, value = line.split(": ", 1)
            fields[key] = value
        if "event" in fields:
            events.append({"id": int(fields["id"]), "type": fields["event"],
                           "data": json.loads(fields["data"])})  # fmt: skip
    return events


async def read_events(client, rid, until=None, last_id=None, headers=None) -> list[dict]:
    headers = dict(headers or {})
    if last_id is not None:
        headers["Last-Event-ID"] = str(last_id)
    params = {"until": until} if until else {}
    response = await client.get(f"/reviews/{rid}/events", params=params, headers=headers)
    assert response.status_code == 200
    return parse_sse(response.text)


async def start(client, pr=1, **body):
    return await client.post(
        "/reviews", json={"pr_url": f"https://github.com/{OWNER_REPO}/pull/{pr}", **body}
    )


async def test_full_flow_start_stream_approve_post(tmp_path):
    client, github = make_client(tmp_path)
    async with client:
        response = await start(client)
        assert response.status_code == 202
        ref = response.json()
        assert ref["status"] == "running" and ref["thread_id"] == "o/r#1@sha1abcdef"
        rid = ref["review_id"]

        events = await read_events(client, rid, until="awaiting_approval")
        types = [e["type"] for e in events]
        assert types[:2] == ["review_started", "route_decided"]
        assert sorted(types[2:6]) == sorted(["agent_started"] * 2 + ["agent_finished"] * 2)
        assert types[6:] == ["validate_done", "judge_round", "awaiting_approval"]
        assert [e["id"] for e in events] == list(range(1, len(events) + 1))
        assert events[1]["data"]["agents"] == ["correctness", "quality"]
        finished = {e["data"]["agent"]: e["data"] for e in events if e["type"] == "agent_finished"}
        assert finished["correctness"]["findings"] == 2
        assert events[-2]["data"]["confirmed"] == 2
        assert [i["id"] for i in events[-1]["data"]["items"]] == ["i1", "i2"]
        assert github.writes() == []

        snapshot = (await client.get(f"/reviews/{rid}")).json()
        assert snapshot["status"] == "awaiting_approval"
        assert [p["id"] for p in snapshot["pending"]] == ["i1", "i2"]
        agents = {a["agent"]: a for a in snapshot["agents"]}
        assert agents["correctness"]["status"] == "done"
        assert agents["security"]["status"] == "skipped"

        decisions = {"decisions": [{"id": "i2", "action": "reject"}], "post": True}
        response = await client.post(f"/reviews/{rid}/decisions", json=decisions)
        assert response.status_code == 202 and response.json()["status"] == "posting"

        tail = await read_events(client, rid, last_id=events[-1]["id"])
        assert [e["type"] for e in tail] == ["posting", "posted"]
        assert tail[-1]["data"]["review_url"] == "https://gh/review/1"
        assert tail[-1]["data"]["inline_comments"] == 1

        snapshot = (await client.get(f"/reviews/{rid}")).json()
        assert snapshot["status"] == "posted"
        assert snapshot["posted"]["review_url"] == "https://gh/review/1"
        assert snapshot["posted"]["inline_comments"] == 1
        assert snapshot["review_stats"]["reject"] == 1
        (review,) = [b for m, p, b in github.requests if m == "POST" and p.endswith("/reviews")]
        assert [c["line"] for c in review["comments"]] == [1]

        history = (await client.get("/reviews")).json()
        assert history[0]["status"] == "posted" and history[0]["title"] == "PR 1"
        assert history[0]["counts"]["major"] == 1


async def test_reconnect_replays_missed_events_and_finished_stream_replays_all(tmp_path):
    client, _ = make_client(tmp_path)
    async with client:
        rid = (await start(client)).json()["review_id"]
        first = await read_events(client, rid, until="awaiting_approval")
        await client.post(f"/reviews/{rid}/decisions", json={"post": True})
        await read_events(client, rid, last_id=first[-1]["id"])  # until posted

        everything = await read_events(client, rid)  # after completion: whole history, closes
        assert everything[-1]["type"] == "posted"
        assert [e["id"] for e in everything] == list(range(1, len(everything) + 1))

        missed = await read_events(client, rid, last_id=3)
        assert [e["id"] for e in missed] == [e["id"] for e in everything if e["id"] > 3]


async def test_same_pr_twice_returns_the_same_review(tmp_path):
    gate = threading.Event()
    client, _ = make_client(tmp_path, models(correctness=gated(gate)))
    try:
        async with client:
            first = await start(client)
            second = await start(client)
            assert first.status_code == 202 and second.status_code == 200
            assert second.json()["review_id"] == first.json()["review_id"]
            assert second.json()["status"] == "running"
            gate.set()
            await read_events(client, first.json()["review_id"], until="awaiting_approval")
            third = await start(client)
            assert third.status_code == 200 and third.json()["status"] == "awaiting_approval"
    finally:
        gate.set()


async def test_already_posted_sha_returns_posted_with_no_github_writes(tmp_path):
    client, github = make_client(tmp_path)
    async with client:
        rid = (await start(client)).json()["review_id"]
        await read_events(client, rid, until="awaiting_approval")
        await client.post(f"/reviews/{rid}/decisions", json={"post": True})
        await read_events(client, rid)
        writes = len(github.writes())

        again = await start(client)
        assert again.status_code == 200 and again.json()["status"] == "posted"
        assert len(github.writes()) == writes


async def test_decisions_409_when_not_waiting_and_400_when_invalid(tmp_path):
    gate = threading.Event()
    client, _ = make_client(tmp_path, models(correctness=gated(gate)))
    try:
        async with client:
            rid = (await start(client)).json()["review_id"]
            running = await client.post(f"/reviews/{rid}/decisions", json={"post": True})
            assert running.status_code == 409
            gate.set()
            await read_events(client, rid, until="awaiting_approval")

            bad = await client.post(
                f"/reviews/{rid}/decisions",
                json={"decisions": [{"id": "x9", "action": "approve"}], "post": False},
            )
            assert bad.status_code == 400 and "x9: no such item" in bad.json()["detail"]

            await client.post(f"/reviews/{rid}/decisions", json={"post": True})
            await read_events(client, rid)
            posted = await client.post(f"/reviews/{rid}/decisions", json={"post": True})
            assert posted.status_code == 409
    finally:
        gate.set()


async def test_bad_url_unknown_pr_and_unknown_review(tmp_path):
    client, _ = make_client(tmp_path)
    async with client:
        bad = await client.post("/reviews", json={"pr_url": "https://gitlab.com/o/r/pull/1"})
        assert bad.status_code == 400
        missing = await start(client, pr=999)
        assert missing.status_code == 404
        assert (await client.get("/reviews/bm9wZQ")).status_code == 404
        assert (await client.get("/reviews/bm9wZQ/events")).status_code == 404


async def test_concurrency_cap_returns_429(tmp_path):
    gate = threading.Event()
    client, _ = make_client(tmp_path, models(correctness=gated(gate)), max_concurrent_reviews=1)
    try:
        async with client:
            assert (await start(client, pr=1)).status_code == 202
            capped = await start(client, pr=2)
            assert capped.status_code == 429
            assert "limit 1" in capped.json()["detail"]
    finally:
        gate.set()


async def test_auth_token_required_except_health(tmp_path):
    client, _ = make_client(tmp_path, api_token="s3cret")
    async with client:
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"ok": True, "anthropic_configured": True,
                                 "github_configured": True, "checkpointer": "sqlite",
                                 "auth_required": True, "demo": False, "public": False}  # fmt: skip
        assert SECRET not in health.text
        assert (await client.get("/reviews")).status_code == 401
        wrong = await client.get("/reviews", headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        ok = await client.get("/reviews", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200


async def test_failing_judge_marks_failed_emits_error_and_never_posts(tmp_path):
    judge = FakeModel([RuntimeError(f"upstream rejected key {SECRET}")])
    client, github = make_client(tmp_path, models(judge=judge))
    async with client:
        rid = (await start(client)).json()["review_id"]
        events = await read_events(client, rid)  # closes on failed
        assert events[-1]["type"] == "error"
        assert "awaiting_approval" not in [e["type"] for e in events]
        assert SECRET not in json.dumps(events) and "***" in events[-1]["data"]["message"]

        snapshot = (await client.get(f"/reviews/{rid}")).json()
        assert snapshot["status"] == "failed" and "upstream rejected key" in snapshot["error"]
        assert SECRET not in json.dumps(snapshot)
        assert github.writes() == []
        decide = await client.post(f"/reviews/{rid}/decisions", json={"post": True})
        assert decide.status_code == 409


async def test_a_run_that_throws_marks_failed_and_never_posts(tmp_path):
    client, github = make_client(tmp_path, github=FakeGitHub(fail_files=True))
    async with client:
        rid = (await start(client)).json()["review_id"]
        events = await read_events(client, rid)  # closes on failed
        assert [e["type"] for e in events] == ["error"]
        assert "HTTPStatusError" in events[0]["data"]["message"]
        snapshot = (await client.get(f"/reviews/{rid}")).json()
        assert snapshot["status"] == "failed" and "500" in snapshot["error"]
        assert github.writes() == []
