"""Demo mode (REVIEWPILOT_DEMO=1): the whole graph runs with no API key and no GitHub writes.

A fake GitHub serves one fixture PR for any link and records posts instead of sending them. Fake
models replay the findings, judge decisions and token counts of a real run (the Stage 7 review of
sandbox PR #4), with small delays scaled from the real latencies so the UI visibly animates.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from reviewpilot.github import PostResult, PullRequest
from reviewpilot.llm import FakeModel, ModelFactory, fake_factory
from reviewpilot.models import AgentOutput, Finding, FindingDecision, JudgeVerdict

FIXTURE = Path(__file__).parent / "fixtures" / "demo_pr4.json"
FETCH_SECONDS = 0.8  # before the scale is applied


def load_fixture(path: Path = FIXTURE) -> dict:
    return json.loads(path.read_text())


@dataclass
class DemoGitHub:
    """Serves the fixture PR under whatever repo and number were asked for."""

    fixture: dict = field(default_factory=load_fixture)
    delay_scale: float = 1.0
    posts: list[dict] = field(default_factory=list)

    def _wait(self, seconds: float) -> None:
        if self.delay_scale > 0:
            time.sleep(seconds * self.delay_scale)

    def get_pull_request(self, repo: str, number: int) -> PullRequest:
        pr = self.fixture["pr"]
        return PullRequest(repo=repo, number=number, title=pr["title"], body=pr["body"],
                           head_sha=pr["head_sha"], base_ref=pr["base_ref"],
                           head_ref=pr["head_ref"], is_fork=False)  # fmt: skip

    def list_files(self, repo: str, number: int) -> list[dict]:
        self._wait(FETCH_SECONDS)
        return [dict(f) for f in self.fixture["files"]]

    def post_review(self, repo, number, head_sha, inline_findings, summary_markdown, force=False):
        self._wait(FETCH_SECONDS)
        post = {"repo": repo, "pr": number, "sha": head_sha, "inline": len(inline_findings)}
        self.posts.append(post | {"summary": summary_markdown})
        base = f"https://github.com/{repo}/pull/{number}"
        return PostResult(
            skipped=False,
            review_url=f"{base}#demo-review",
            summary_url=f"{base}#demo-summary",
            summary_created=True,
            posted_inline=list(inline_findings),
        )


def _raw(usage: dict) -> SimpleNamespace:
    """Stands in for the AIMessage, so call_model records the real run's token counts."""
    return SimpleNamespace(
        usage_metadata={
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "input_token_details": {
                "cache_read": usage["cache_read_tokens"],
                "cache_creation": usage["cache_write_tokens"],
            },
        },
        response_metadata={"stop_reason": "end_turn"},
    )


def _wait_like(usage: dict, scale: float) -> None:
    if scale > 0:
        time.sleep(usage["latency_ms"] / 1000 * scale)


def demo_model_factory(fixture: dict | None = None, delay_scale: float = 1.0) -> ModelFactory:
    fixture = fixture or load_fixture()
    usage = fixture["usage"]

    def agent(role: str):
        findings = [Finding(**f) for f in fixture["agents"].get(role, [])]

        def respond(_messages: list) -> dict:
            _wait_like(usage[role], delay_scale)
            parsed = AgentOutput(findings=[f.model_copy() for f in findings], notes="demo")
            return {"raw": _raw(usage[role]), "parsed": parsed, "parsing_error": None}

        return FakeModel(respond)

    def finding_id(d: dict) -> str:
        fields = {"severity": "minor", "category": "bug", "body": "", "evidence": "",
                  "confidence": 0}  # fmt: skip
        return Finding(file=d["file"], line=d["line"], title=d["title"], agent=d["agent"],
                       **fields).id  # fmt: skip

    decisions = {finding_id(d): d for d in fixture["judge"]}

    def judge(messages: list) -> dict:
        _wait_like(usage["judge"], delay_scale)
        prompt = messages[-1].content
        verdict = JudgeVerdict(
            decisions=[
                FindingDecision(
                    finding_id=fid,
                    decision=d["decision"],
                    final_severity=d["final_severity"],
                    reason=d["reason"],
                )  # fmt: skip
                for fid, d in decisions.items()
                if f"id={fid}" in prompt
            ]
        )
        return {"raw": _raw(usage["judge"]), "parsed": verdict, "parsing_error": None}

    models = {role: agent(role) for role in fixture["agents"]}
    models["judge"] = FakeModel(judge)
    return fake_factory(models)
