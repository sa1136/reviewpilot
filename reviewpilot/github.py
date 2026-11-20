"""Minimal GitHub REST client: read PR metadata and changed files."""

from dataclasses import dataclass

import httpx

PER_PAGE = 100


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

    def list_files(self, repo: str, number: int) -> list[dict]:
        """All changed files of a PR, following pagination via the Link header."""
        files: list[dict] = []
        url: str | None = f"/repos/{repo}/pulls/{number}/files"
        params: dict | None = {"per_page": PER_PAGE}
        while url:
            resp = self._http.get(url, params=params)
            resp.raise_for_status()
            files.extend(resp.json())
            url = resp.links.get("next", {}).get("url")
            params = None  # the next URL already carries its query string
        return files
