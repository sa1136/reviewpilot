"""Demo mode: fixture PR, replayed model answers from the real PR #4 run, fake poster."""

import httpx
import pytest

from api.main import create_app
from reviewpilot.config import Settings
from reviewpilot.demo import DemoGitHub
from tests.test_api import read_events

pytestmark = pytest.mark.anyio
PR_URL = "https://github.com/sa1136/reviewpilot-sandbox/pull/4"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def demo_client(tmp_path):
    settings = Settings(
        demo=True,
        demo_delay_scale=0,
        checkpoint_db=str(tmp_path / "checkpoints.db"),
        api_db=str(tmp_path / "api.db"),
    )
    github = DemoGitHub(delay_scale=0)
    app = create_app(settings, github=github)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t"), github


def test_from_env_demo_uses_separate_databases(monkeypatch):
    monkeypatch.setenv("REVIEWPILOT_DEMO", "1")
    monkeypatch.delenv("REVIEWPILOT_API_DB", raising=False)
    monkeypatch.delenv("REVIEWPILOT_CHECKPOINT_DB", raising=False)
    settings = Settings.from_env()
    assert settings.demo
    assert settings.api_db == ".reviewpilot/demo-api.db"
    assert settings.checkpoint_db == ".reviewpilot/demo-checkpoints.db"


async def test_demo_review_replays_pr4_and_posts_to_the_fake(tmp_path):
    client, github = demo_client(tmp_path)
    async with client:
        health = (await client.get("/health")).json()
        assert health["demo"] is True and health["anthropic_configured"] is False

        rid = (await client.post("/reviews", json={"pr_url": PR_URL})).json()["review_id"]
        events = await read_events(client, rid, until="awaiting_approval")
        types = [e["type"] for e in events]
        assert types[:2] == ["review_started", "route_decided"]
        assert types[-3:] == ["validate_done", "judge_round", "awaiting_approval"]
        assert events[-3]["data"] == {**events[-3]["data"], "kept": 5,
                                      "dropped": {"duplicate": 2}}  # fmt: skip
        assert events[-2]["data"]["confirmed"] == 4 and events[-2]["data"]["rejected"] == 1

        snap = (await client.get(f"/reviews/{rid}")).json()
        inline = [p for p in snap["pending"] if p["kind"] == "inline"]
        assert [(p["file"], p["line"], p["severity"]) for p in inline][:3] == [
            ("coupons.py", 15, "critical"),
            ("coupons.py", 18, "critical"),
            ("coupons.py", 26, "critical"),
        ]
        assert inline[0]["code"].strip() == 'return normalize_code(code) is "WELCOME10"'
        assert "==" in inline[0]["suggestion"]
        assert snap["usage"]["cost_usd"] == pytest.approx(0.00395, abs=1e-4)
        assert snap["rounds"] == [{"round": 1, "kept": 5, "dropped": {"duplicate": 2},
                                   "judged": 5, "confirmed": 4, "rejected": 1,
                                   "retrying": []}]  # fmt: skip
        assert snap["files"] == 2 and snap["review_seconds"] is not None

        body = {"decisions": [{"id": "i2", "action": "reject"}], "post": True}
        assert (await client.post(f"/reviews/{rid}/decisions", json=body)).status_code == 202
        tail = await read_events(client, rid, last_id=events[-1]["id"])
        assert [e["type"] for e in tail] == ["posting", "posted"]
        assert tail[-1]["data"]["review_url"].endswith("/pull/4#demo-review")
        assert github.posts[0]["inline"] == len(inline) - 1
