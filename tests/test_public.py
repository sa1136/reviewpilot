"""The public demo: it can only run the demo, visitors cannot reach each other's reviews, and
simple abuse is limited. No keys, no network."""

import asyncio
import os
import sqlite3
import subprocess
import sys

import httpx
import pytest

from api.app import create_app
from api.limits import HourlyRate, PublicLimits
from api.public import FORBIDDEN, create_public_app, public_settings
from api.store import Store
from reviewpilot.config import Settings
from reviewpilot.demo import DemoGitHub, demo_model_factory
from tests.test_api import read_events

pytestmark = pytest.mark.anyio

TOKEN = "t" * 44
PR_URL = "https://github.com/sa1136/reviewpilot-sandbox/pull/4"
ALICE, BOB = "a" * 32, "b" * 32


@pytest.fixture
def anyio_backend():
    return "asyncio"


def env(tmp_path, **extra) -> dict:
    return {
        "REVIEWPILOT_DEMO": "1",
        "REVIEWPILOT_API_TOKEN": TOKEN,
        "REVIEWPILOT_DEMO_DELAY_SCALE": "0",
        "REVIEWPILOT_API_DB": str(tmp_path / "api.db"),
        "REVIEWPILOT_CHECKPOINT_DB": str(tmp_path / "checkpoints.db"),
        **extra,
    }


def headers(visitor: str, ip: str = "203.0.113.1", token: str = TOKEN) -> dict:
    return {
        "authorization": f"Bearer {token}",
        "x-reviewpilot-visitor": visitor,
        "x-reviewpilot-client": ip,
    }


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def limited_app(tmp_path, delay_scale: float = 0, **limits):
    settings = public_settings(env(tmp_path))
    return create_app(
        settings,
        github=DemoGitHub(delay_scale=delay_scale),
        model_factory=demo_model_factory(delay_scale=delay_scale),
        limits=PublicLimits(**limits),
    )


async def start(client, visitor, url=PR_URL, ip="203.0.113.1", **body):
    return await client.post("/reviews", json={"pr_url": url, **body}, headers=headers(visitor, ip))


async def reach_approval(client, visitor, **kwargs) -> str:
    response = await start(client, visitor, **kwargs)
    assert response.status_code == 202, response.text
    rid = response.json()["review_id"]
    await read_events(client, rid, until="awaiting_approval", headers=headers(visitor))
    return rid


# --- demo only ----------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "0", "false", "true", "yes", "1 ", "demo"])
def test_refuses_to_start_unless_demo_is_exactly_1(tmp_path, value):
    variables = env(tmp_path)
    del variables["REVIEWPILOT_DEMO"]
    if value is not None:
        variables["REVIEWPILOT_DEMO"] = value
    with pytest.raises(RuntimeError, match="REVIEWPILOT_DEMO must be exactly 1"):
        create_public_app(variables)


@pytest.mark.parametrize("name", FORBIDDEN)
def test_refuses_to_start_with_a_real_credential_and_does_not_print_it(tmp_path, name):
    with pytest.raises(RuntimeError) as error:
        create_public_app(env(tmp_path, **{name: "very-secret-value"}))
    assert name in str(error.value)
    assert "very-secret-value" not in str(error.value)


@pytest.mark.parametrize("token", ["", "short", "x" * 31])
def test_refuses_to_start_without_a_long_api_token(tmp_path, token):
    with pytest.raises(RuntimeError, match="REVIEWPILOT_API_TOKEN"):
        create_public_app(env(tmp_path, REVIEWPILOT_API_TOKEN=token))


def test_public_limits_cannot_be_used_outside_demo_mode(tmp_path):
    with pytest.raises(ValueError, match="demo mode"):
        create_app(Settings(demo=False, api_token=TOKEN), limits=PublicLimits())


async def test_never_builds_a_real_github_client_or_model(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("the public demo built a real client")

    monkeypatch.setattr("api.app.GitHubClient", forbidden)
    monkeypatch.setattr("api.app.real_model_factory", forbidden)
    monkeypatch.setattr("reviewpilot.graph.GitHubClient", forbidden)
    monkeypatch.setattr("reviewpilot.graph.real_model_factory", forbidden)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    async with client_for(create_public_app(env(tmp_path))) as client:
        rid = await reach_approval(client, ALICE)
        body = {"decisions": [], "post": True}
        response = await client.post(f"/reviews/{rid}/decisions", json=body,
                                     headers=headers(ALICE))  # fmt: skip
        assert response.status_code == 202
        await read_events(client, rid, headers=headers(ALICE))
        health = (await client.get("/health")).json()
    assert health["demo"] is True and health["public"] is True
    assert health["anthropic_configured"] is False and health["github_configured"] is False
    assert os.environ["LANGSMITH_TRACING"] == "false"


def import_in_new_process(module: str, extra: dict) -> subprocess.CompletedProcess:
    variables = {k: v for k, v in os.environ.items() if not k.startswith("REVIEWPILOT_")}
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        env=variables | extra, capture_output=True, text=True, timeout=60,
    )  # fmt: skip


def test_local_entry_point_refuses_to_start_on_render():
    result = import_in_new_process("api.main", {"RENDER": "true", "REVIEWPILOT_DEMO": "1"})
    assert result.returncode != 0
    assert "api.public:app" in result.stderr


async def test_api_docs_are_off_and_everything_but_health_needs_the_token(tmp_path):
    async with client_for(create_public_app(env(tmp_path))) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await client.get(path)).status_code == 404
        for bad in ({}, headers(ALICE, token="x" * 44), {"x-reviewpilot-visitor": ALICE}):
            assert (await client.get("/reviews", headers=bad)).status_code == 401
        assert (await client.get("/health")).status_code == 200


# --- visitor separation -------------------------------------------------------------------------


@pytest.mark.parametrize("visitor", ["", "short", "has spaces " * 3, "x" * 65])
async def test_a_valid_visitor_id_is_required(tmp_path, visitor):
    async with client_for(create_public_app(env(tmp_path))) as client:
        assert (await client.get("/reviews", headers=headers(visitor))).status_code == 400
        assert (await start(client, visitor)).status_code == 400


async def test_one_visitor_cannot_see_or_change_anothers_review(tmp_path):
    async with client_for(create_public_app(env(tmp_path))) as client:
        rid = await reach_approval(client, ALICE)
        edit = {"decisions": [{"id": "i1", "action": "edit", "body": "ALICE PRIVATE"}],
                "post": False}  # fmt: skip
        url = f"/reviews/{rid}/decisions"
        assert (await client.post(url, json=edit, headers=headers(ALICE))).status_code == 202
        await asyncio.sleep(0.3)

        unknown = await client.get("/reviews/bm9wZQ", headers=headers(BOB))
        for path in (f"/reviews/{rid}", f"/reviews/{rid}/events"):
            response = await client.get(path, headers=headers(BOB))
            assert response.status_code == 404
            assert response.json() == unknown.json()  # same answer as a review that never was
        steal = {"decisions": [{"id": "i2", "action": "reject"}], "post": True}
        assert (await client.post(url, json=steal, headers=headers(BOB))).status_code == 404
        assert (await client.get("/reviews", headers=headers(BOB))).json() == []

        mine = (await client.get(f"/reviews/{rid}", headers=headers(ALICE))).json()
        assert mine["status"] == "awaiting_approval"
        assert mine["pending"][0]["body_edit"] == "ALICE PRIVATE"
        assert [r["review_id"] for r in
                (await client.get("/reviews", headers=headers(ALICE))).json()] == [rid]  # fmt: skip


async def test_the_same_link_gives_each_visitor_their_own_fresh_demo(tmp_path):
    async with client_for(create_public_app(env(tmp_path))) as client:
        alice = await reach_approval(client, ALICE)
        await client.post(f"/reviews/{alice}/decisions", json={"post": True},
                          headers=headers(ALICE))  # fmt: skip
        await read_events(client, alice, headers=headers(ALICE))
        bob = await reach_approval(client, BOB)  # 202: a new run, though Alice's is posted
        assert bob != alice
        snapshot = (await client.get(f"/reviews/{bob}", headers=headers(BOB))).json()
        assert snapshot["status"] == "awaiting_approval"
        assert all(p["body_edit"] is None for p in snapshot["pending"])
        assert ALICE not in str(snapshot) and ALICE not in bob


async def test_a_visitor_gets_their_own_review_back_and_fresh_starts_a_new_one(tmp_path):
    async with client_for(create_public_app(env(tmp_path))) as client:
        rid = await reach_approval(client, ALICE)
        again = await start(client, ALICE)
        assert (again.status_code, again.json()["review_id"]) == (200, rid)

        fresh = await reach_approval(client, ALICE, fresh=True)
        assert fresh != rid
        latest = await start(client, ALICE)  # the newest of Alice's runs for this commit
        assert (latest.status_code, latest.json()["review_id"]) == (200, fresh)


async def test_review_history_survives_a_restart_of_the_same_files(tmp_path):
    async with client_for(create_public_app(env(tmp_path))) as client:
        rid = await reach_approval(client, ALICE)
    async with client_for(create_public_app(env(tmp_path))) as client:
        assert (await client.get(f"/reviews/{rid}", headers=headers(ALICE))).status_code == 200


# --- abuse limits -------------------------------------------------------------------------------


async def test_running_limits_per_visitor_per_network_and_in_total(tmp_path):
    app = limited_app(tmp_path, delay_scale=0.1, running_total=3)
    async with client_for(app) as client:
        pr = "https://github.com/o/r/pull/{}".format
        assert (await start(client, ALICE, pr(1))).status_code == 202
        second = await start(client, ALICE, pr(2))
        assert second.status_code == 429 and "your demo review" in second.json()["detail"]
        # Same network: one more visitor fits, a third (a new cookie) does not.
        assert (await start(client, BOB)).status_code == 202
        third = await start(client, "c" * 32)
        assert third.status_code == 429 and "network" in third.json()["detail"]
        # Someone elsewhere is not blocked by them, until the total is reached.
        assert (await start(client, "d" * 32, ip="198.51.100.7")).status_code == 202
        busy = await start(client, "e" * 32, ip="192.0.2.9")
        assert busy.status_code == 429 and "busy" in busy.json()["detail"]
        await asyncio.gather(*app.state.manager.active.values())
        assert (await start(client, ALICE, pr(2))).status_code == 202
        await asyncio.gather(*app.state.manager.active.values())


async def test_hourly_limit_counts_new_reviews_by_network_not_by_cookie(tmp_path):
    app = limited_app(tmp_path, new_per_client_per_hour=2)
    async with client_for(app) as client:
        await reach_approval(client, ALICE)
        await reach_approval(client, BOB)
        blocked = await start(client, "c" * 32)
        assert blocked.status_code == 429 and "hourly" in blocked.json()["detail"]
        assert (await start(client, ALICE)).status_code == 200  # reopening is not a new review
        await reach_approval(client, "c" * 32, ip="198.51.100.7")


def test_hourly_rate_forgets_old_hits_and_empty_keys():
    now = [0.0]
    rate = HourlyRate(2, clock=lambda: now[0])
    rate.hit("a")
    rate.hit("a")
    assert rate.full("a") and not rate.full("b")
    now[0] = 3601
    assert not rate.full("a")
    assert rate._hits == {}


def stored(app) -> list[dict]:
    return app.state.manager.store.list_reviews()


def checkpoints(app, thread_id: str) -> list:
    return list(app.state.manager.saver.list({"configurable": {"thread_id": thread_id}}))


async def test_storage_caps_delete_the_oldest_reviews_with_their_events_and_checkpoints(tmp_path):
    app = limited_app(tmp_path, stored_per_visitor=2, stored_total=3)
    async with client_for(app) as client:
        first = await reach_approval(client, ALICE)
        thread = stored(app)[0]["thread_id"]
        assert checkpoints(app, thread)
        second = await reach_approval(client, ALICE, fresh=True)
        third = await reach_approval(client, ALICE, fresh=True)
        assert {r["review_id"] for r in stored(app)} == {second, third}
        assert checkpoints(app, thread) == []
        assert app.state.manager.store.events_after(first) == []

        await reach_approval(client, BOB)
        await reach_approval(client, "c" * 32, ip="198.51.100.7")
        assert len(stored(app)) == 3


async def test_reviews_expire(tmp_path):
    app = limited_app(tmp_path, ttl_hours=1)
    async with client_for(app) as client:
        rid = await reach_approval(client, ALICE)
        thread = stored(app)[0]["thread_id"]
        app.state.manager.store.update_review(rid, created_at="2000-01-01T00:00:00+00:00")
        assert (await client.get("/reviews", headers=headers(ALICE))).json() == []
        assert (await client.get(f"/reviews/{rid}", headers=headers(ALICE))).status_code == 404
        assert checkpoints(app, thread) == []


async def test_decision_calls_are_capped(tmp_path):
    app = limited_app(tmp_path, decision_calls_per_review=2)
    async with client_for(app) as client:
        rid = await reach_approval(client, ALICE)
        codes = []
        for _ in range(3):
            response = await client.post(f"/reviews/{rid}/decisions", json={"post": False},
                                         headers=headers(ALICE))  # fmt: skip
            codes.append(response.status_code)
            await asyncio.gather(*app.state.manager.active.values())
        assert codes == [202, 202, 429]


async def test_oversized_input_is_refused(tmp_path):
    async with client_for(create_public_app(env(tmp_path))) as client:
        assert (await start(client, ALICE, PR_URL + "?" + "x" * 400)).status_code == 422
        rid = await reach_approval(client, ALICE)
        url = f"/reviews/{rid}/decisions"
        for body in (
            {"decisions": [{"id": "i1", "action": "edit", "body": "x" * 4001}]},
            {"decisions": [{"id": "i1", "action": "approve"}] * 101},
        ):
            assert (await client.post(url, json=body, headers=headers(ALICE))).status_code == 422
        huge = await client.post(url, content=b"x" * 70_000, headers=headers(ALICE))
        assert huge.status_code == 413

        async def chunks():  # no Content-Length
            for _ in range(70):
                yield b"x" * 1000

        chunked = await client.post(url, content=chunks(), headers=headers(ALICE))
        assert chunked.status_code == 413


async def test_event_streams_are_capped_and_time_limited(tmp_path):
    app = limited_app(tmp_path, streams_per_visitor=1, stream_max_seconds=0.6)
    async with client_for(app) as client:
        rid = await reach_approval(client, ALICE)
        url = f"/reviews/{rid}/events"
        # A review waiting for approval never ends on its own: the time limit closes it.
        open_stream = asyncio.create_task(client.get(url, headers=headers(ALICE)))
        await asyncio.sleep(0.2)
        assert (await client.get(url, headers=headers(ALICE))).status_code == 429
        assert (await asyncio.wait_for(open_stream, 5)).status_code == 200
        # The slot is free again once it closes.
        assert (await asyncio.wait_for(client.get(url, headers=headers(ALICE)), 5)).status_code \
            == 200  # fmt: skip


def test_a_history_file_from_before_the_public_demo_gets_the_owner_column(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "create table reviews (review_id text primary key, thread_id text not null, repo text "
        "not null, pr integer not null, title text not null default '', sha text not null, "
        "status text not null, created_at text not null, updated_at text not null, counts text "
        "not null default '{}', cost_usd real not null default 0, error text, posted text)"
    )
    conn.execute(
        "insert into reviews (review_id, thread_id, repo, pr, sha, status, created_at, "
        "updated_at) values ('r', 't', 'o/r', 1, 's', 'posted', 'now', 'now')"
    )
    conn.commit()
    conn.close()
    store = Store(str(path))
    assert [r["owner"] for r in store.list_reviews()] == [""]
    store.close()
