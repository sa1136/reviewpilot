"""Deterministic checks on this round's findings before the judge sees them.

Rules, in order (a finding is dropped at the first rule it fails, with the reason recorded):
1. on_added_line   the file is in the PR and the line is an added line
2. evidence        the evidence appears in that line (whitespace trimmed and collapsed)
3. out_of_lane     the category belongs to the agent's own lane
4. missing_test    a missing test finding names a function on its def line, and no test file in
                   the PR references that function
5. repeat          not the same issue (same file, same line, similar title) as a finding the judge
                   already saw in an earlier round, accepted or not
6. duplicate       not the same issue as another finding this round; when agents collide, the
                   lane owner (agent order: correctness, security, quality) keeps its finding
"""

import re
from collections import Counter

from reviewpilot.config import AVAILABLE_AGENTS
from reviewpilot.events import emit
from reviewpilot.models import LANE_CATEGORIES, FileDiff, Finding
from reviewpilot.router import is_test

# Words that carry no meaning when comparing titles.
STOPWORDS = {
    "a", "an", "the", "is", "are", "be", "and", "or", "of", "for", "to", "in", "on", "at", "by",
    "with", "its", "it", "this", "that", "has", "have", "no", "not", "can", "may", "when", "if",
}  # fmt: skip
SIMILAR_TITLE = 0.5  # overlap coefficient of title tokens at or above which titles match
DEF_NAME = re.compile(r"\b(?:def|function)\s+([A-Za-z_]\w*)")


def squash(text: str) -> str:
    return " ".join(text.split())


def title_tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_]+", title.lower()) if w not in STOPWORDS}


def similar_titles(a: str, b: str) -> bool:
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return squash(a.lower()) == squash(b.lower())
    return len(ta & tb) / min(len(ta), len(tb)) >= SIMILAR_TITLE


def same_issue(a: Finding, b: Finding) -> bool:
    return a.file == b.file and a.line == b.line and similar_titles(a.title, b.title)


def evidence_matches(evidence: str, file: FileDiff, line: int) -> bool:
    """Every non empty evidence line must appear, in order, in the added lines starting at
    `line` (so multi line evidence is fine)."""
    wanted = [squash(e) for e in evidence.splitlines() if e.strip()]
    if not wanted:
        return False
    for offset, piece in enumerate(wanted):
        code = file.added_code.get(line + offset)
        if code is None or piece not in squash(code):
            return False
    return True


def test_references(files: list[FileDiff]) -> str:
    """All text of the test files in the PR diff, to look up function names."""
    return "\n".join(f.patch for f in files if is_test(f.path))


def check_one(f: Finding, files_by_path: dict[str, FileDiff], tests_text: str) -> str | None:
    """Reason to drop the finding on its own (rules 1 to 4), or None to keep it."""
    file = files_by_path.get(f.file)
    if file is None or f.line not in file.added_lines:
        return "on_added_line: not an added line in this PR"
    if not evidence_matches(f.evidence, file, f.line):
        return "evidence: quoted code is not on that line"
    if f.category not in LANE_CATEGORIES.get(f.agent, ()):
        return f"out_of_lane: {f.agent} reported a {f.category} finding"
    if f.category == "missing_test":
        match = DEF_NAME.search(file.added_code[f.line])
        if match is None:
            return "missing_test: not on the def line of a function"
        name = match.group(1)
        if re.search(rf"\b{re.escape(name)}\b", tests_text):
            return f"missing_test: {name} is referenced in a test file in this PR"
    return None


def agent_rank(agent: str) -> int:
    return AVAILABLE_AGENTS.index(agent) if agent in AVAILABLE_AGENTS else len(AVAILABLE_AGENTS)


def validate(state: dict) -> dict:
    rnd = state["round"]
    files_by_path = {f.path: f for f in state["files"]}
    tests_text = test_references(state["files"])
    earlier = state.get("judged", [])  # every finding the judge already decided on

    kept: list[Finding] = []
    dropped: list[dict] = []

    def drop(f: Finding, reason: str) -> None:
        dropped.append({"finding": f, "round": rnd, "reason": reason})

    # Lane owners first, so on a collision the owner's finding is the one kept.
    current = [f for f in state["findings"] if f.round == rnd]
    for f in sorted(current, key=lambda f: agent_rank(f.agent)):
        reason = check_one(f, files_by_path, tests_text)
        if reason:
            drop(f, reason)
            continue
        prior = next((p for p in earlier if same_issue(f, p)), None)
        if prior is not None:
            accepted = any(same_issue(f, a) for a in state.get("accepted", []))
            what = "an accepted finding" if accepted else f"a finding judged in round {prior.round}"
            drop(f, f"repeat: same as {what} ({prior.agent})")
            continue
        twin = next((k for k in kept if same_issue(f, k)), None)
        if twin is not None:
            drop(f, f"duplicate: same issue as {twin.agent}'s finding this round")
            continue
        kept.append(f)

    reasons = Counter(d["reason"].split(":")[0] for d in dropped)
    emit("validate_done", round=rnd, kept=len(kept), dropped=dict(reasons))
    return {"candidates": kept, "dropped": dropped}
