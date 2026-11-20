"""Command line entry point.

python -m reviewpilot --repo owner/name --pr N --dry-run          # real Claude review, printed
python -m reviewpilot --repo owner/name --pr N --dry-run --fake   # full graph, fake model
python -m reviewpilot --repo owner/name --pr N --diff-only        # just the numbered diff
"""

import argparse
import sys

import httpx

from reviewpilot.config import Settings
from reviewpilot.diff import build_file_diffs
from reviewpilot.github import GitHubClient
from reviewpilot.graph import build_graph, run_review
from reviewpilot.llm import demo_fake_factory, real_model_factory


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="reviewpilot", description="Review a GitHub PR.")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--pr", required=True, type=int, help="pull request number")
    parser.add_argument("--dry-run", action="store_true", help="print instead of posting")
    parser.add_argument(
        "--fake", action="store_true", help="use a fake model (no Anthropic key, no LLM cost)"
    )
    parser.add_argument(
        "--diff-only", action="store_true", help="only print the numbered diff and exit"
    )
    return parser.parse_args(argv)


def print_diff(gh: GitHubClient, settings: Settings, repo: str, number: int) -> None:
    pr = gh.get_pull_request(repo, number)
    files = gh.list_files(repo, number)
    diffs, skipped = build_file_diffs(
        files, settings.skip_globs, settings.max_files, settings.max_patch_chars
    )
    print(f"PR {pr.repo}#{pr.number}: {pr.title}")
    print(f"head {pr.head_sha[:12]}  {pr.head_ref} -> {pr.base_ref}  fork={pr.is_fork}")
    print(f"{len(files)} changed files, {len(diffs)} reviewable, {len(skipped)} skipped\n")
    for d in diffs:
        rename = f" (renamed from {d.previous_path})" if d.previous_path else ""
        note = " [truncated]" if d.truncated else ""
        print(f"=== {d.path}{rename} [{d.status}]{note}")
        print(d.numbered_patch + "\n")
    for s in skipped:
        print(f"skipped {s.path}: {s.reason}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = Settings.from_env()

    if not (args.fake or args.diff_only or settings.anthropic_api_key):
        print("ANTHROPIC_API_KEY is not set (add it to .env), or use --fake.", file=sys.stderr)
        return 2
    factory = demo_fake_factory() if args.fake else real_model_factory(settings)

    try:
        with GitHubClient(settings.github_token, settings.github_api_url) as gh:
            if args.diff_only:
                print_diff(gh, settings, args.repo, args.pr)
                return 0
            graph = build_graph(settings, factory, github=gh, dry_run=args.dry_run)
            result = run_review(graph, args.repo, args.pr)
    except httpx.HTTPStatusError as exc:
        print(f"GitHub API error: {exc.response.status_code} {exc.request.url}", file=sys.stderr)
        return 1
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
