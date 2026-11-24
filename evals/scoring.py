"""Plain code evaluators. Each takes the target's outputs and the example's reference outputs.

A planted bug is caught when an inline comment is on the same file within LINE_TOLERANCE of its
line (or alt line), or, for a planted missing test, when its function is on the grouped "New
functions without tests" line. Inline comments on `neutral_files` count neither way; every other
inline comment that matches no planted bug is a false positive.
"""

import re

from evals.keys import LINE_TOLERANCE
from reviewpilot.config import AVAILABLE_AGENTS, MAX_LOOPS_CAP

TESTS_LINE_PREFIX = "New functions without tests:"


def untested_functions(tests_line: str | None) -> set[str]:
    if not tests_line or TESTS_LINE_PREFIX not in tests_line:
        return set()
    names = tests_line.split(TESTS_LINE_PREFIX, 1)[1]
    return {n.strip().strip("`") for n in names.split(",") if n.strip()}


def near(finding: dict, bug: dict) -> bool:
    if finding["file"] != bug["file"]:
        return False
    lines = [bug["line"]] + ([bug["alt_line"]] if bug.get("alt_line") else [])
    return any(abs(finding["line"] - line) <= LINE_TOLERANCE for line in lines)


def is_test_bug(bug: dict) -> bool:
    return bug["kind"] == "missing_test"


def match(outputs: dict, reference: dict, comments: str = "inline") -> dict:
    """Which bugs were caught and which comments are true or false positives."""
    bugs = reference["bugs"]
    neutral = set(reference.get("neutral_files", []))
    findings = outputs.get(comments, [])
    names = untested_functions(outputs.get("tests_line"))
    caught = []
    for bug in bugs:
        if is_test_bug(bug):
            caught.append(bug.get("function") in names)
        else:
            caught.append(any(near(f, bug) for f in findings))
    true_pos, false_pos = [], []
    for f in findings:
        if f["file"] in neutral:
            continue
        hit = any(near(f, b) for b in bugs if not is_test_bug(b))
        (true_pos if hit else false_pos).append(f)
    return {"caught": caught, "true_positives": true_pos, "false_positives": false_pos}


def recall(outputs: dict, reference_outputs: dict) -> dict:
    caught = match(outputs, reference_outputs)["caught"]
    with_possible = match(
        {**outputs, "inline": outputs.get("inline", []) + outputs.get("possible_issues", [])},
        reference_outputs,
    )["caught"]
    total = len(caught)
    return {
        "results": [
            {"key": "recall", "score": sum(caught) / total if total else None},
            {"key": "bugs_caught", "score": sum(caught)},
            {"key": "bugs_total", "score": total},
            {"key": "recall_with_possible", "score": sum(with_possible) / total if total else None},
        ]
    }


def false_positives(outputs: dict, reference_outputs: dict) -> dict:
    """Reported separately: on clean PRs (no planted bugs) and extras on buggy PRs."""
    fps = len(match(outputs, reference_outputs)["false_positives"])
    clean_pr = not reference_outputs["bugs"]
    return {
        "results": [
            {"key": "false_positives", "score": fps},
            {"key": "fp_clean_pr", "score": fps if clean_pr else None},
            {"key": "fp_extra_buggy_pr", "score": None if clean_pr else fps},
        ]
    }


def precision(outputs: dict, reference_outputs: dict) -> dict:
    m = match(outputs, reference_outputs)
    counted = len(m["true_positives"]) + len(m["false_positives"])
    score = len(m["true_positives"]) / counted if counted else None
    return {
        "results": [
            {"key": "precision", "score": score},
            {"key": "true_positives", "score": len(m["true_positives"])},
        ]
    }


def routing(outputs: dict, reference_outputs: dict) -> dict:
    """The expected agents (limited to the ones num_agents enables) ran, and no others."""
    enabled = outputs.get("enabled", list(AVAILABLE_AGENTS))
    expected = [a for a in reference_outputs["expected_route"] if a in enabled]
    ok = sorted(outputs.get("route", [])) == sorted(expected)
    return {"key": "routing", "score": int(ok), "comment": f"ran {outputs.get('route')}"}


def valid_trajectory(nodes: list[str], route: list[str], max_rounds: int) -> tuple[bool, str]:
    """fetch_pr, router, then rounds of (agents, validate, judge), then approval, post_review.
    Round 1 runs the whole route; a retry round runs a subset of it."""
    if nodes[:2] != ["fetch_pr", "router"]:
        return False, "does not start with fetch_pr, router"
    if nodes[-2:] != ["approval", "post_review"]:
        return False, "does not end with approval, post_review"
    middle = nodes[2:-2]
    if any(n not in (*AVAILABLE_AGENTS, "validate", "judge") for n in middle):
        return False, "unexpected node"
    shape = "".join("a" if n in AVAILABLE_AGENTS else n[0] for n in middle)  # a, v, j
    if route and not re.fullmatch(r"(a+vj)+", shape):
        return False, f"bad round shape {shape or '(no rounds)'}"
    if not route and middle:
        return False, "agents ran with an empty route"
    rounds, i = [], 0
    for group in re.findall(r"a+", shape):
        rounds.append(middle[i : i + len(group)])
        i += len(group) + 2  # skip validate, judge
    if rounds and sorted(rounds[0]) != sorted(route):
        return False, "round 1 did not run the whole route"
    if any(not set(r) <= set(route) for r in rounds[1:]):
        return False, "a retry ran an agent outside the route"
    if len(rounds) > min(max_rounds, MAX_LOOPS_CAP):
        return False, f"{len(rounds)} rounds is over the cap"
    return True, f"{len(rounds)} round(s)"


def trajectory(outputs: dict, reference_outputs: dict) -> dict:
    ok, why = valid_trajectory(
        outputs.get("trajectory", []), outputs.get("route", []), outputs.get("max_rounds", 3)
    )
    return {"key": "trajectory", "score": int(ok), "comment": why}


def cost_and_latency(outputs: dict, reference_outputs: dict) -> dict:
    return {
        "results": [
            {"key": "cost_usd", "score": outputs.get("cost_usd")},
            {"key": "latency_s", "score": outputs.get("latency_s")},
            {"key": "rounds", "score": outputs.get("rounds")},
        ]
    }


EVALUATORS = [recall, false_positives, precision, routing, trajectory, cost_and_latency]


def score_all(outputs: dict, reference_outputs: dict) -> dict[str, float | None]:
    """Run every evaluator locally and flatten to {key: score}."""
    scores = {}
    for evaluator in EVALUATORS:
        result = evaluator(outputs, reference_outputs)
        for r in result.get("results", [result]):
            scores[r["key"]] = r["score"]
    return scores
