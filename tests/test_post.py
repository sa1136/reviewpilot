"""Posting to GitHub, against a fake GitHub API (httpx.MockTransport). No network."""

import json

import httpx

from reviewpilot.cli import main
from reviewpilot.config import Settings
from reviewpilot.github import SUMMARY_MARKER, GitHubClient, comment_body, sha_marker
from reviewpilot.graph import build_graph, run_review
from reviewpilot.llm import FakeModel, fake_factory
from reviewpilot.models import AgentOutput, Finding, FindingDecision, JudgeVerdict

SHA = "abc1234def"
REPO = "o/r"


class FakeGitHubAPI:
    """Just enough of the GitHub REST API. Records every request it receives."""

    def __init__(self, reviews=(), comments=(), files=(), reject_lines=()):
        self.reviews = list(reviews)
        self.comments = list(comments)
        self.files = list(files)
        self.reject_lines = set(reject_lines)  # inline comments GitHub answers with 422
        self.requests: list[tuple[str, str, dict | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path, method = request.url.path, request.method
        self.requests.append((method, path, body))
        if method == "GET" and path == f"/repos/{REPO}/pulls/1":
            head = {"sha": SHA, "ref": "feat", "repo": {"full_name": REPO}}
            base = {"ref": "main", "repo": {"full_name": REPO}}
            return httpx.Response(200, json={"title": "T", "body": "", "head": head, "base": base})
        if method == "GET" and path.endswith("/files"):
            return httpx.Response(200, json=self.files)
        if method == "GET" and path.endswith("/reviews"):
            return httpx.Response(200, json=self.reviews)
        if method == "GET" and path.endswith("/comments"):
            return httpx.Response(200, json=self.comments)
        if method == "POST" and path.endswith("/reviews"):
            lines = {c["line"] for c in body.get("comments", [])}
            if lines & self.reject_lines:
                return httpx.Response(422, json={"message": "Line could not be resolved"})
            return httpx.Response(200, json={"html_url": "https://gh/review/1"})
        if method == "POST" and path == f"/repos/{REPO}/pulls/1/comments":
            if body["line"] in self.reject_lines:
                return httpx.Response(422, json={"message": "Line could not be resolved"})
            return httpx.Response(201, json={"html_url": f"https://gh/c/{body['line']}"})
        if method == "POST" and path == f"/repos/{REPO}/issues/1/comments":
            return httpx.Response(201, json={"html_url": "https://gh/summary/new"})
        if method == "PATCH" and path.startswith(f"/repos/{REPO}/issues/comments/"):
            return httpx.Response(200, json={"html_url": "https://gh/summary/updated"})
        return httpx.Response(404, json={"message": f"unexpected {method} {path}"})

    def sent(self, method: str, suffix: str) -> list[dict]:
        return [b for m, p, b in self.requests if m == method and p.endswith(suffix)]


def client(api: FakeGitHubAPI) -> GitHubClient:
    return GitHubClient("token", "https://api.github.com", transport=httpx.MockTransport(api))


def f(line: int, severity="major", title=None, suggestion=None) -> Finding:
    return Finding(
        file="app.py",
        line=line,
        severity=severity,
        category="bug",
        title=title or f"Problem on line {line}",
        body="Why it matters.",
        suggestion=suggestion,
        evidence=f"line {line}",
        confidence=0.9,
        agent="correctness",
    )


def test_review_payload_shape():
    api = FakeGitHubAPI()
    result = client(api).post_review(REPO, 1, SHA, [f(3, suggestion="return x")], "summary")

    (review,) = api.sent("POST", "/reviews")
    assert review["commit_id"] == SHA
    assert review["event"] == "COMMENT"
    assert sha_marker(SHA) in review["body"]
    (comment,) = review["comments"]
    assert {k: comment[k] for k in ("path", "line", "side")} == {
        "path": "app.py",
        "line": 3,
        "side": "RIGHT",
    }
    assert comment["body"] == comment_body(f(3, suggestion="return x"))
    assert comment["body"].splitlines()[0] == "`major` · correctness · bug"
    assert "**Problem on line 3**" in comment["body"]
    assert "```\nreturn x\n```" in comment["body"]
    assert "```suggestion" not in comment["body"]
    assert result.review_url == "https://gh/review/1"
    assert not result.skipped


def test_sticky_summary_is_created_then_updated():
    api = FakeGitHubAPI()
    result = client(api).post_review(REPO, 1, SHA, [], "## hello")
    assert result.summary_created
    (created,) = api.sent("POST", "/issues/1/comments")
    assert created["body"].startswith(SUMMARY_MARKER)

    api = FakeGitHubAPI(comments=[{"id": 77, "body": f"{SUMMARY_MARKER}\nold"}])
    result = client(api).post_review(REPO, 1, SHA, [], f"{SUMMARY_MARKER}\nnew")
    assert not result.summary_created
    assert api.sent("POST", "/issues/1/comments") == []
    assert [p for m, p, _ in api.requests if m == "PATCH"] == [f"/repos/{REPO}/issues/comments/77"]
    assert api.sent("PATCH", "/comments/77") == [{"body": f"{SUMMARY_MARKER}\nnew"}]


def writes(api: FakeGitHubAPI) -> list[tuple[str, str]]:
    return [(m, p) for m, p, _ in api.requests if m != "GET"]


def test_same_head_sha_marker_in_a_review_writes_nothing():
    api = FakeGitHubAPI(
        reviews=[{"body": f"{sha_marker(SHA)}\nReviewPilot left 1 inline comment."}],
        comments=[{"id": 5, "body": f"{SUMMARY_MARKER}\nold", "html_url": "https://gh/s"}],
    )
    result = client(api).post_review(REPO, 1, SHA, [f(3)], "new summary")
    assert result.skipped and result.summary_url == "https://gh/s"
    assert writes(api) == []

    # a new commit is not skipped
    result = client(api).post_review(REPO, 1, "newsha99", [f(3)], "new summary")
    assert not result.skipped
    assert len(api.sent("POST", "/reviews")) == 1


def test_sha_marker_in_the_summary_skips_a_run_with_no_inline_comments():
    summary = f"{SUMMARY_MARKER}\n{sha_marker(SHA)}\nold"
    api = FakeGitHubAPI(comments=[{"id": 5, "body": summary}])
    result = client(api).post_review(REPO, 1, SHA, [], "new summary")
    assert result.skipped
    assert writes(api) == []


def test_force_posts_even_when_already_posted():
    api = FakeGitHubAPI(
        reviews=[{"body": sha_marker(SHA)}], comments=[{"id": 5, "body": SUMMARY_MARKER}]
    )
    result = client(api).post_review(REPO, 1, SHA, [f(3)], "new summary", force=True)
    assert not result.skipped
    assert len(api.sent("POST", "/reviews")) == 1
    assert len(api.sent("PATCH", "/comments/5")) == 1


def test_422_moves_rejected_comment_to_summary_instead_of_failing():
    api = FakeGitHubAPI(reject_lines={4})
    result = client(api).post_review(REPO, 1, SHA, [f(3), f(4)], "summary")

    with_comments, body_only = api.sent("POST", "/reviews")
    assert len(with_comments["comments"]) == 2
    assert "comments" not in body_only and sha_marker(SHA) in body_only["body"]
    assert [x.line for x in result.posted_inline] == [3]
    assert [x.line for x in result.moved_to_summary] == [4]
    (summary,) = api.sent("POST", "/issues/1/comments")
    assert (
        "### Could not be posted inline\n- `major` `app.py:4` Problem on line 4" in summary["body"]
    )


def test_graph_posts_at_most_max_comments_and_overflows_by_severity():
    patch = "@@ -0,0 +1,30 @@\n" + "\n".join(f"+line {i}" for i in range(1, 31))
    api = FakeGitHubAPI(files=[{"filename": "app.py", "status": "added", "patch": patch}])
    severities = ["minor"] * 22 + ["major", "critical", "minor"]  # 25 findings
    findings = [f(i + 1, sev) for i, sev in enumerate(severities)]
    agent = FakeModel([AgentOutput(findings=findings)])

    def confirm_all(msgs):
        ids = [part.split()[0] for part in msgs[-1].content.split("- id=")[1:]]
        return JudgeVerdict(
            decisions=[FindingDecision(finding_id=i, decision="confirm") for i in ids]
        )

    settings = Settings(num_agents=1, max_comments=20)
    graph = build_graph(
        settings,
        fake_factory({"correctness": agent, "judge": FakeModel(confirm_all)}),
        github=client(api),
        dry_run=False,
    )
    result = run_review(graph, REPO, 1)

    (review,) = api.sent("POST", "/reviews")
    assert len(review["comments"]) == 20
    # the severity floor makes every correctness finding at least major; critical comes first
    assert review["comments"][0]["line"] == 24
    (summary,) = api.sent("POST", "/issues/1/comments")
    overflow = summary["body"].split("### Not posted inline")[1].split("###")[0]
    assert overflow.count("- `major`") == 5
    assert result["posted"]["posted_inline"] == 20


def test_dry_run_never_writes(capsys):
    api = FakeGitHubAPI(
        files=[{"filename": "app.py", "status": "added", "patch": "@@ -0,0 +1 @@\n+x"}]
    )
    agent = FakeModel([AgentOutput()])
    graph = build_graph(
        Settings(num_agents=1),
        fake_factory({"correctness": agent, "judge": FakeModel([])}),
        github=client(api),
        dry_run=True,
    )
    run_review(graph, REPO, 1)
    assert [m for m, _, _ in api.requests if m != "GET"] == []
    assert SUMMARY_MARKER in capsys.readouterr().out


def test_post_refuses_without_token(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("REVIEWPILOT_CHECKPOINT_DB", str(tmp_path / "c.db"))
    assert main(["review", "--repo", REPO, "--pr", "1", "--auto-approve"]) == 2
    err = capsys.readouterr().err
    assert "Posting needs GITHUB_TOKEN" in err
    assert "Pull requests: Read and write" in err


def test_auto_approve_cannot_combine_with_fake(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("REVIEWPILOT_CHECKPOINT_DB", str(tmp_path / "c.db"))
    assert main(["review", "--repo", REPO, "--pr", "1", "--auto-approve", "--fake"]) == 2
