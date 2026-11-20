"""Real API smoke test. Skipped unless RUN_LIVE=1, so CI never calls the API (and spends nothing).

RUN_LIVE=1 uv run pytest -m live
"""

import os

import pytest

from reviewpilot.config import Settings
from reviewpilot.graph import build_graph, run_review
from tests.test_graph import FakeGitHub

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to call the API"),
]


def test_live_review_on_a_tiny_diff():
    settings = Settings.from_env()
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    patch = "@@ -1,2 +1,4 @@\n def first(items):\n+    return items[0]\n+\n"
    github = FakeGitHub([{"filename": "util.py", "status": "modified", "patch": patch}])
    result = run_review(build_graph(settings, github=github), "o/r", 1)

    assert result["status"] in {"success", "partial"}
    assert any(u.get("input_tokens", 0) > 0 for u in result["usage"])
