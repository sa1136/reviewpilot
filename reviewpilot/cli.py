"""Command line entry point.

    python -m reviewpilot review  --repo owner/name --pr N            # review, stop at approval
    python -m reviewpilot pending                                      # reviews waiting for you
    python -m reviewpilot approve --repo owner/name --pr N --approve all --post
    python -m reviewpilot review  --repo owner/name --pr N --auto-approve   # GitHub Action mode

Review state is saved in a checkpointer (SQLite at .reviewpilot/checkpoints.db by default), one
thread per commit: owner/repo#pr@head_sha. Nothing is posted without `approve --post` or
`--auto-approve`.
"""

import argparse
import sys

import httpx
from langgraph.types import Command

from reviewpilot.approval import decision_errors
from reviewpilot.checkpoint import fresh_thread_id, list_threads, open_checkpointer, thread_id
from reviewpilot.config import Settings
from reviewpilot.diff import build_file_diffs
from reviewpilot.github import GitHubClient
from reviewpilot.graph import build_graph, config_for
from reviewpilot.llm import demo_fake_factory, real_model_factory
from reviewpilot.state import initial_state

POST_TOKEN_HELP = """Posting needs GITHUB_TOKEN (set it in .env).
The token needs, on the target repository:
  - Pull requests: Read and write  (read the PR, post the review and the summary comment)
  - Metadata: Read                 (granted automatically for fine-grained tokens)
A classic token needs the `repo` scope (or `public_repo` for public repositories)."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="reviewpilot", description="Review a GitHub PR.")
    sub = parser.add_subparsers(dest="command", required=True)

    review = sub.add_parser("review", help="review a PR and stop at the approval step")
    review.add_argument("--repo", required=True, help="owner/name")
    review.add_argument("--pr", required=True, type=int, help="pull request number")
    review.add_argument("--fake", action="store_true", help="fake model: no API key, no cost")
    review.add_argument("--fresh", action="store_true", help="new review on a new thread id")
    review.add_argument(
        "--auto-approve",
        action="store_true",
        help="approve every inline finding, post no possible issues, and post (GitHub Action)",
    )
    review.add_argument("--diff-only", action="store_true", help="print the numbered diff only")

    sub.add_parser("pending", help="list saved reviews waiting for approval")

    approve = sub.add_parser("approve", help="decide on a pending review, optionally post it")
    approve.add_argument("--repo", required=True, help="owner/name")
    approve.add_argument("--pr", required=True, type=int, help="pull request number")
    approve.add_argument("--approve", default="", help="'all' or comma separated ids")
    approve.add_argument("--reject", default="", help="comma separated ids")
    approve.add_argument("--promote", default="", help="comma separated possible issue ids")
    approve.add_argument(
        "--edit", action="append", default=[], metavar='ID="new text"', help="replace the text"
    )
    approve.add_argument("--post", action="store_true", help="post to GitHub after deciding")
    approve.add_argument("-i", "--interactive", action="store_true", help="decide item by item")
    return parser.parse_args(argv)


# --- printing -------------------------------------------------------------------------------


def describe(item: dict) -> str:
    if item["kind"] == "tests":
        return item["text"]
    text = f"[{item['severity']}] {item['file']}:{item['line']} {item['title']} ({item['agent']})"
    if item["kind"] == "possible":
        text += f"\n       judge: {item['reason']}"
    return text


def action_text(item: dict) -> str:
    action = item["action"]
    default = " (default)" if action == item["default"] else ""
    if action == "edit":
        return f'edit: "{item["body_edit"]}"'
    if item["kind"] == "possible" and action == "reject":
        return "not posted" + default
    return action + default


def print_pending(payload: dict, repo: str, pr: int) -> None:
    items = payload["items"]
    print(f"Waiting for approval: {payload['thread_id']} (status {payload['status']})")
    if not items:
        print("  nothing to decide; approve to post the summary only")
    for item in items:
        print(f"  {item['id']:<4} {describe(item)}")
        print(f"       -> {action_text(item)}")
    print(
        f"\nNext: reviewpilot approve --repo {repo} --pr {pr} [--approve all|ids] "
        '[--reject ids] [--edit id="text"] [--promote ids] [--post]'
    )


def pending_payload(graph, tid: str) -> dict | None:
    snapshot = graph.get_state(config_for(tid))
    if snapshot.next != ("approval",) or not snapshot.interrupts:
        return None
    return snapshot.interrupts[0].value


# --- commands -------------------------------------------------------------------------------


def start_review(
    settings, gh, factory, saver, repo, pr, fresh=False, auto_approve=False, fake=False
) -> int:
    """Review a PR up to the approval step. Resumes a saved thread for the same commit instead
    of reviewing again, and refuses to redo a commit that was already posted."""
    head_sha = gh.get_pull_request(repo, pr).head_sha
    base = thread_id(repo, pr, head_sha)
    graph = build_graph(settings, factory, github=gh, dry_run=not auto_approve, checkpointer=saver)

    existing = [] if fresh else list_threads(saver, base)
    if existing:
        tid = existing[0]
        snapshot = graph.get_state(config_for(tid))
        if not snapshot.next:
            print(f"already posted for {head_sha[:7]} ({tid}). Use --fresh to review again.")
            return 0
        if snapshot.next != ("approval",):
            print(f"Resuming an interrupted review: {tid}")
            graph.invoke(None, config_for(tid))
        else:
            print(f"Review already done for {head_sha[:7]}; it is waiting for approval.")
    else:
        tid = fresh_thread_id(base) if fresh else base
        state = initial_state(repo, pr, tid, fresh=fresh) | {"fake": fake}
        graph.invoke(state, config_for(tid))

    payload = pending_payload(graph, tid)
    if payload is None:
        print(f"Review {tid} finished.")
        return 0
    if auto_approve:
        return resume(graph, tid, decisions=[], post=True)
    print_pending(payload, repo, pr)
    return 0


def cmd_pending(settings, saver) -> int:
    graph = build_graph(settings, demo_fake_factory(), github=_NoGitHub(), checkpointer=saver)
    found = 0
    for tid in list_threads(saver):
        payload = pending_payload(graph, tid)
        if payload is None:
            continue
        found += 1
        kinds = [i["kind"] for i in payload["items"]]
        counts = ", ".join(f"{kinds.count(k)} {k}" for k in dict.fromkeys(kinds)) or "no items"
        print(f"{tid}  ({counts}; status {payload['status']})")
    if not found:
        print("No reviews waiting for approval.")
    return 0


def build_decisions(args, items: list[dict]) -> tuple[list[dict], list[str]]:
    by_id = {i["id"]: i for i in items}
    decisions: dict[str, dict] = {}

    def ids(text: str) -> list[str]:
        return [x.strip() for x in text.split(",") if x.strip()]

    if args.approve.strip() == "all":
        # every item except possible issues, which need an explicit approve or promote
        for i, item in by_id.items():
            if item["kind"] != "possible":
                decisions[i] = {"id": i, "action": "approve"}
    else:
        decisions |= {i: {"id": i, "action": "approve"} for i in ids(args.approve)}
    decisions |= {i: {"id": i, "action": "reject"} for i in ids(args.reject)}
    decisions |= {i: {"id": i, "action": "promote"} for i in ids(args.promote)}
    for raw in args.edit:
        key, sep, text = raw.partition("=")
        decisions[key.strip()] = {"id": key.strip(), "action": "edit", "body": text if sep else ""}

    return list(decisions.values()), decision_errors(items, list(decisions.values()))


def interactive_decisions(items: list[dict], ask=input) -> list[dict]:
    """Walk each item: a approve, r reject, e edit, p promote, enter keeps the current choice."""
    decisions = []
    for item in items:
        print(f"\n{item['id']}  {describe(item)}\n  current: {action_text(item)}")
        keys = "a/r/e" + ("/p" if item["kind"] == "possible" else "")
        choice = ask(f"  [{keys}, enter to keep] ").strip().lower()
        if choice == "a":
            decisions.append({"id": item["id"], "action": "approve"})
        elif choice == "r":
            decisions.append({"id": item["id"], "action": "reject"})
        elif choice == "p" and item["kind"] == "possible":
            decisions.append({"id": item["id"], "action": "promote"})
        elif choice == "e":
            text = ask("  new text: ").strip()
            if text:
                decisions.append({"id": item["id"], "action": "edit", "body": text})
    return decisions


def cmd_approve(args, settings, gh, saver, ask=input) -> int:
    graph = build_graph(settings, demo_fake_factory(), github=gh, dry_run=False, checkpointer=saver)
    prefix = f"{args.repo}#{args.pr}@"
    tid = next((t for t in list_threads(saver, prefix) if pending_payload(graph, t)), None)
    if tid is None:
        print(f"No review waiting for approval for {args.repo}#{args.pr}.", file=sys.stderr)
        return 1
    payload = pending_payload(graph, tid)
    if args.post and graph.get_state(config_for(tid)).values.get("fake"):
        print("This review used --fake; it cannot be posted.", file=sys.stderr)
        return 2
    if args.post and not settings.github_token:
        print(POST_TOKEN_HELP, file=sys.stderr)
        return 2

    if args.interactive:
        decisions, errors = interactive_decisions(payload["items"], ask), []
    else:
        decisions, errors = build_decisions(args, payload["items"])
    if errors:
        print("Nothing saved:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 2
    return resume(graph, tid, decisions, post=args.post)


def resume(graph, tid: str, decisions: list[dict], post: bool) -> int:
    graph.invoke(Command(resume={"decisions": decisions, "post": post}), config_for(tid))
    if post:
        stats = graph.get_state(config_for(tid)).values.get("review_stats", {})
        print(
            f"Decisions: {stats.get('approve', 0)} approved, {stats.get('edit', 0)} edited, "
            f"{stats.get('promote', 0)} promoted, {stats.get('reject', 0)} rejected"
        )
        return 0
    payload = pending_payload(graph, tid)
    print("Decisions saved. Not posted yet (add --post). This is what would be posted:\n")
    for c in payload["would_post_inline"]:
        print(f"  inline [{c['severity']}] {c['file']}:{c['line']} {c['title']}")
    print("\n" + payload["summary_preview"])
    return 0


class _NoGitHub:
    """For listing saved state only; any GitHub call is a bug."""

    def __getattr__(self, name):
        raise RuntimeError(f"unexpected GitHub call: {name}")


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


def run_review_command(args, settings, gh, saver) -> int:
    if args.diff_only:
        print_diff(gh, settings, args.repo, args.pr)
        return 0
    if args.auto_approve and args.fake:
        print("--auto-approve cannot be combined with --fake.", file=sys.stderr)
        return 2
    if args.auto_approve and not settings.github_token:
        print(POST_TOKEN_HELP, file=sys.stderr)
        return 2
    if not (args.fake or settings.anthropic_api_key):
        print("ANTHROPIC_API_KEY is not set (add it to .env), or use --fake.", file=sys.stderr)
        return 2
    factory = demo_fake_factory() if args.fake else real_model_factory(settings)
    return start_review(
        settings,
        gh,
        factory,
        saver,
        args.repo,
        args.pr,
        fresh=args.fresh,
        auto_approve=args.auto_approve,
        fake=args.fake,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = Settings.from_env()
    try:
        with open_checkpointer(settings) as saver:
            if args.command == "pending":
                return cmd_pending(settings, saver)
            with GitHubClient(settings.github_token, settings.github_api_url) as gh:
                if args.command == "approve":
                    return cmd_approve(args, settings, gh, saver)
                return run_review_command(args, settings, gh, saver)
    except httpx.HTTPStatusError as exc:
        print(f"GitHub API error: {exc.response.status_code} {exc.request.url}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
