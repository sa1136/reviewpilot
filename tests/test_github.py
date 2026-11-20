import httpx

from reviewpilot.github import GitHubClient

BASE = "https://api.github.com"


def pr_payload(head_repo="o/r"):
    return {
        "title": "Add feature",
        "body": None,
        "head": {"sha": "abc123", "ref": "feature", "repo": {"full_name": head_repo}},
        "base": {"ref": "main", "repo": {"full_name": "o/r"}},
    }


def test_get_pull_request_parses_metadata_and_sends_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        assert request.url.path == "/repos/o/r/pulls/7"
        return httpx.Response(200, json=pr_payload())

    with GitHubClient("t0ken", BASE, transport=httpx.MockTransport(handler)) as gh:
        pr = gh.get_pull_request("o/r", 7)

    assert seen["auth"] == "Bearer t0ken"
    assert pr.head_sha == "abc123"
    assert pr.body == ""
    assert pr.is_fork is False


def test_get_pull_request_detects_fork():
    def handler(request):
        return httpx.Response(200, json=pr_payload(head_repo="someone/r"))

    with GitHubClient(None, BASE, transport=httpx.MockTransport(handler)) as gh:
        assert gh.get_pull_request("o/r", 1).is_fork is True


def test_list_files_follows_pagination():
    page2 = f"{BASE}/repos/o/r/pulls/7/files?page=2"
    pages = {
        "1": ([{"filename": "a.py"}, {"filename": "b.py"}], page2),
        "2": ([{"filename": "c.py"}], None),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        body, next_url = pages[page]
        headers = {"Link": f'<{next_url}>; rel="next"'} if next_url else {}
        return httpx.Response(200, json=body, headers=headers)

    with GitHubClient(None, BASE, transport=httpx.MockTransport(handler)) as gh:
        files = gh.list_files("o/r", 7)

    assert [f["filename"] for f in files] == ["a.py", "b.py", "c.py"]
