"""Minimal GitHub REST client: read a PR, post one review and a sticky summary comment."""

from dataclasses import dataclass, field

import httpx

from reviewpilot.models import Finding

PER_PAGE = 100
SUMMARY_MARKER = "<!-- reviewpilot:summary -->"


def sha_marker(head_sha: str) -> str:
    """Hidden marker in the review body: one review per head commit."""
    return f"<!-- reviewpilot:sha={head_sha} -->"


def comment_body(f: Finding) -> str:
    """Inline comment: label line, bold title, explanation, suggestion in a plain code block."""
    parts = [f"`{f.severity}` · {f.agent} · {f.category}", "", f"**{f.title}**", "", f.body]
    if f.suggestion:
        parts += ["", "Suggestion:", "```", f.suggestion.strip(), "```"]
    return "\n".join(parts)


@dataclass
class PostResult:
    skipped_review: bool  # a review for this head commit already existed
    review_url: str | None
    summary_url: str
    summary_created: bool  # False when an existing sticky summary was updated
    posted_inline: list[Finding] = field(default_factory=list)
    moved_to_summary: list[Finding] = field(default_factory=list)  # GitHub rejected them (422)


@dataclass(frozen=True)
class PullRequest:
    repo: str
    number: int
    title: str
    body: str
    head_sha: str
    base_ref: str
    head_ref: str
    is_fork: bool


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        base_url: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
    ):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "reviewpilot",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=base_url, headers=headers, transport=transport, timeout=30.0
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get_pull_request(self, repo: str, number: int) -> PullRequest:
        resp = self._http.get(f"/repos/{repo}/pulls/{number}")
        resp.raise_for_status()
        data = resp.json()
        head_repo = (data["head"].get("repo") or {}).get("full_name")
        return PullRequest(
            repo=repo,
            number=number,
            title=data.get("title") or "",
            body=data.get("body") or "",
            head_sha=data["head"]["sha"],
            base_ref=data["base"]["ref"],
            head_ref=data["head"]["ref"],
            is_fork=head_repo != data["base"]["repo"]["full_name"],
        )

    def _paginate(self, path: str) -> list[dict]:
        """Every item of a list endpoint, following pagination via the Link header."""
        items: list[dict] = []
        url: str | None = path
        params: dict | None = {"per_page": PER_PAGE}
        while url:
            resp = self._http.get(url, params=params)
            resp.raise_for_status()
            items.extend(resp.json())
            url = resp.links.get("next", {}).get("url")
            params = None  # the next URL already carries its query string
        return items

    def list_files(self, repo: str, number: int) -> list[dict]:
        """All changed files of a PR."""
        return self._paginate(f"/repos/{repo}/pulls/{number}/files")

    def post_review(
        self,
        repo: str,
        number: int,
        head_sha: str,
        inline_findings: list[Finding],
        summary_markdown: str,
    ) -> PostResult:
        """Post one review with inline comments for this head commit (skipped if one already
        exists), then create or update the sticky summary comment."""
        marker = sha_marker(head_sha)
        reviews = self._paginate(f"/repos/{repo}/pulls/{number}/reviews")
        skipped = any(marker in (r.get("body") or "") for r in reviews)

        review_url, posted, moved = None, [], []
        if not skipped and inline_findings:
            review_url, posted, moved = self._create_review(
                repo, number, head_sha, inline_findings, marker
            )

        summary = summary_markdown
        if SUMMARY_MARKER not in summary:
            summary = f"{SUMMARY_MARKER}\n{summary}"
        if moved:
            summary += "\n\n### Could not be posted inline\n" + "\n".join(
                f"- `{f.severity}` `{f.file}:{f.line}` {f.title} ({f.agent})" for f in moved
            )
        summary_url, created = self._upsert_summary(repo, number, summary)
        return PostResult(skipped, review_url, summary_url, created, posted, moved)

    def _create_review(self, repo, number, head_sha, findings, marker):
        """One review with all comments. If GitHub rejects it (422), post the review body alone
        and then each comment separately, moving any comment GitHub still rejects to the
        summary instead of failing the run."""
        n = len(findings)
        body = f"{marker}\nReviewPilot left {n} inline comment{'s' if n != 1 else ''}. "
        body += "The summary comment has the full review."
        comments = [
            {"path": f.file, "line": f.line, "side": "RIGHT", "body": comment_body(f)}
            for f in findings
        ]
        payload = {"commit_id": head_sha, "event": "COMMENT", "body": body}
        resp = self._http.post(
            f"/repos/{repo}/pulls/{number}/reviews", json={**payload, "comments": comments}
        )
        if resp.status_code != 422:
            resp.raise_for_status()
            return resp.json().get("html_url"), list(findings), []

        resp = self._http.post(f"/repos/{repo}/pulls/{number}/reviews", json=payload)
        resp.raise_for_status()
        review_url = resp.json().get("html_url")
        posted, moved = [], []
        for f, comment in zip(findings, comments, strict=True):
            one = self._http.post(
                f"/repos/{repo}/pulls/{number}/comments", json={**comment, "commit_id": head_sha}
            )
            if one.status_code == 422:
                moved.append(f)
                continue
            one.raise_for_status()
            posted.append(f)
        return review_url, posted, moved

    def _upsert_summary(self, repo: str, number: int, body: str) -> tuple[str, bool]:
        comments = self._paginate(f"/repos/{repo}/issues/{number}/comments")
        existing = next((c for c in comments if SUMMARY_MARKER in (c.get("body") or "")), None)
        if existing:
            resp = self._http.patch(
                f"/repos/{repo}/issues/comments/{existing['id']}", json={"body": body}
            )
            created = False
        else:
            resp = self._http.post(f"/repos/{repo}/issues/{number}/comments", json={"body": body})
            created = True
        resp.raise_for_status()
        return resp.json().get("html_url", ""), created
