"""LangGraph state for one review run."""

import operator
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
    errors: Annotated[list[dict], operator.add]  # {"node", "round", "error"}
    usage: Annotated[list[dict], operator.add]  # {"node", "round", "latency_s", ...}

    # Written by a single node at a time.
    candidates: list[Finding]  # this round's findings after validate; what the judge sees
    round: int  # current review round, 1 = first pass
    retry_agents: list[str]
    judge_feedback: dict[str, str]
    summary: str
    status: str  # success | partial | failed


def initial_state(repo: str, pr_number: int) -> ReviewState:
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
        errors=[],
        usage=[],
        candidates=[],
        round=1,
        retry_agents=[],
        judge_feedback={},
        summary="",
        status="",
    )
