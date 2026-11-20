"""LangGraph state for one review run."""

import operator
import time
from typing import Annotated, TypedDict

from reviewpilot.models import FileDiff, Finding, SkippedFile


class ReviewState(TypedDict):
    repo: str
    pr_number: int
    head_sha: str
    pr_title: str
    pr_body: str
    files: list[FileDiff]
    skipped: list[SkippedFile]

    # Appended to by parallel nodes, so they need a reducer.
    findings: Annotated[list[Finding], operator.add]  # every agent finding, all rounds
    accepted: Annotated[list[Finding], operator.add]  # confirmed or downgraded by the judge
    judged: Annotated[list[Finding], operator.add]  # everything the judge decided on, all rounds
    dropped: Annotated[list[dict], operator.add]  # {"finding", "round", "reason"} from validate
    # correctness / security findings the judge rejected, kept for a human: {"finding", "reason"}
    possible_issues: Annotated[list[dict], operator.add]
    errors: Annotated[list[dict], operator.add]  # {"node", "round", "error"}
    usage: Annotated[list[dict], operator.add]  # per call: node, round, model, tokens, ms, cost

    # Written by a single node at a time.
    route: list[str]  # agents the router chose for this PR
    route_reasons: dict[str, str]  # why each agent runs or was skipped
    candidates: list[Finding]  # this round's findings after validate; what the judge sees
    round: int  # current review round, 1 = first pass
    retry_agents: list[str]
    judge_feedback: dict[str, str]
    summary: str
    inline: list[Finding]  # post_review: findings that become inline comments
    nits: list[Finding]  # post_review: below min_inline_severity, summary only
    overflow: list[Finding]  # post_review: over the max_comments budget, summary only
    untested: list[Finding]  # post_review: missing test findings, one summary line
    posted: dict | None  # post_review: what was written to GitHub (None in a dry run)

    # Human approval (Stage 6).
    thread_id: str  # owner/repo#pr@head_sha (plus ~fresh-... for --fresh)
    fresh: bool  # --fresh: post even if this commit was already posted
    fake: bool  # reviewed with --fake: never posted
    human_decisions: dict[str, dict]  # item id -> {"action", "body"}, saved across pauses
    decisions_log: list[dict]  # every item's final action, with source human or default
    review_stats: dict  # counts: approve, reject, edit, promote, total, by_human
    approved_possible: list[dict]  # possible issues the human approved for the summary
    tests_line: str | None  # approved (or edited) "New functions without tests" line
    status: str  # success | partial | failed
    started_at: float  # time.time() when the run began, for the footer
    reviewed_at: float | None  # set by the judge; review time excludes waiting for a human


def initial_state(
    repo: str, pr_number: int, thread_id: str = "", fresh: bool = False
) -> ReviewState:
    return ReviewState(
        repo=repo,
        pr_number=pr_number,
        head_sha="",
        pr_title="",
        pr_body="",
        files=[],
        skipped=[],
        findings=[],
        accepted=[],
        judged=[],
        dropped=[],
        possible_issues=[],
        errors=[],
        usage=[],
        candidates=[],
        route=[],
        route_reasons={},
        round=1,
        retry_agents=[],
        judge_feedback={},
        summary="",
        inline=[],
        nits=[],
        overflow=[],
        untested=[],
        posted=None,
        thread_id=thread_id,
        fresh=fresh,
        fake=False,
        human_decisions={},
        decisions_log=[],
        review_stats={},
        approved_possible=[],
        tests_line=None,
        status="",
        started_at=time.time(),
        reviewed_at=None,
    )
