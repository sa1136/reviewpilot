"""Command line entry point: python -m reviewpilot --repo owner/name --pr N --dry-run"""

import argparse
import sys

import httpx

from reviewpilot.config import Settings
from reviewpilot.diff import build_file_diffs
from reviewpilot.github import GitHubClient


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="reviewpilot", description="Review a GitHub PR.")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--pr", required=True, type=int, help="pull request number")
    parser.add_argument("--dry-run", action="store_true", help="print instead of posting to GitHub")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = Settings.from_env()

    if not args.dry_run:
        print("Posting is not implemented yet (Stage 5). Running as --dry-run.", file=sys.stderr)

    try:
        with GitHubClient(settings.github_token, settings.github_api_url) as gh:
            pr = gh.get_pull_request(args.repo, args.pr)
            files = gh.list_files(args.repo, args.pr)
    except httpx.HTTPStatusError as exc:
        print(f"GitHub API error: {exc.response.status_code} {exc.request.url}", file=sys.stderr)
        return 1

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
        print(d.numbered_patch)
        print()

    if skipped:
        print("Skipped:")
        for s in skipped:
            print(f"  {s.path}: {s.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
