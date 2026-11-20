"""Every validate rule, on hand built state (no model involved)."""

from reviewpilot.diff import build_file_diffs
from reviewpilot.models import Finding
from reviewpilot.validate import similar_titles, validate

APP = (
    "@@ -1,2 +1,6 @@\n"
    " import os\n"
    "+def total(items):\n"
    "+    return   sum(items)\n"
    "+def average(items):\n"
    "+    return sum(items) / len(items)\n"
    " # end"
)
TESTS = "@@ -0,0 +1,2 @@\n+def test_total():\n+    assert total([1]) == 1"


def files():
    diffs, _ = build_file_diffs(
        [
            {"filename": "app.py", "status": "modified", "patch": APP},
            {"filename": "tests/test_app.py", "status": "added", "patch": TESTS},
        ],
        (),
        40,
        12000,
    )
    return diffs


def f(agent="correctness", line=3, evidence="return sum(items)", category="bug", title="Sums wrong",
      file="app.py", rnd=1, severity="major"):  # fmt: skip
    return Finding(
        file=file,
        line=line,
        severity=severity,
        category=category,
        title=title,
        body="b",
        evidence=evidence,
        confidence=0.9,
        agent=agent,
        round=rnd,
    )


def run(findings, rnd=1, judged=(), accepted=()):
    state = {
        "round": rnd,
        "files": files(),
        "findings": findings,
        "judged": list(judged),
        "accepted": list(accepted),
    }
    out = validate(state)
    reasons = [d["reason"] for d in out["dropped"]]
    return out["candidates"], reasons


def test_keeps_a_good_finding():
    kept, reasons = run([f()])
    assert len(kept) == 1 and reasons == []


def test_drops_context_line_and_unknown_file():
    kept, reasons = run([f(line=1, evidence="import os"), f(file="other.py")])
    assert kept == []
    assert all(r.startswith("on_added_line") for r in reasons)


def test_evidence_must_be_on_that_line_whitespace_trimmed():
    kept, reasons = run(
        [
            f(evidence="  return sum(items)  "),  # extra and collapsed whitespace still matches
            f(evidence="return max(items)", title="Other"),  # not on the line
        ]
    )
    assert [k.evidence for k in kept] == ["  return sum(items)  "]
    assert reasons == ["evidence: quoted code is not on that line"]


def test_multi_line_evidence_on_consecutive_added_lines():
    kept, _ = run([f(line=4, evidence="def average(items):\n    return sum(items) / len(items)")])
    assert len(kept) == 1


def test_out_of_lane_dropped():
    kept, reasons = run([f(agent="quality", category="bug")])
    assert kept == []
    assert reasons == ["out_of_lane: quality reported a bug finding"]


def test_missing_test_must_be_on_a_def_line():
    kept, reasons = run([f(agent="quality", category="missing_test", line=3)])
    assert kept == [] and reasons == ["missing_test: not on the def line of a function"]


def test_missing_test_dropped_when_a_test_file_references_the_function():
    total = f(agent="quality", category="missing_test", line=2, evidence="def total(items):",
              title="total has no test")  # fmt: skip
    average = f(agent="quality", category="missing_test", line=4, evidence="def average(items):",
                title="average has no test")  # fmt: skip
    kept, reasons = run([total, average])
    assert [k.title for k in kept] == ["average has no test"]
    assert reasons == ["missing_test: total is referenced in a test file in this PR"]


def test_duplicate_across_agents_keeps_lane_owner():
    quality = f(agent="quality", category="error_handling", title="Sums wrong for empty input")
    correctness = f(agent="correctness", title="Sums wrong on empty input")
    kept, reasons = run([quality, correctness])  # quality listed first on purpose
    assert [k.agent for k in kept] == ["correctness"]
    assert reasons == ["duplicate: same issue as correctness's finding this round"]


def test_same_line_different_issue_is_not_a_duplicate():
    kept, _ = run([f(title="Sums wrong"), f(agent="security", category="injection",
                                            title="Eval of user input")])  # fmt: skip
    assert len(kept) == 2


def test_repeat_of_accepted_finding_dropped_before_judge():
    earlier = f(title="Sums wrong for floats", rnd=1)
    kept, reasons = run([f(title="Sums wrong with floats", rnd=2)], rnd=2, judged=[earlier],
                        accepted=[earlier])  # fmt: skip
    assert kept == []
    assert reasons == ["repeat: same as an accepted finding (correctness)"]


def test_repeat_of_rejected_finding_dropped():
    earlier = f(title="Sums wrong", rnd=1)
    kept, reasons = run([f(title="Sums wrong", rnd=2)], rnd=2, judged=[earlier])
    assert kept == []
    assert reasons == ["repeat: same as a finding judged in round 1 (correctness)"]


def test_only_current_round_is_validated():
    kept, reasons = run([f(rnd=1), f(rnd=2, title="New thing")], rnd=2)
    assert [k.title for k in kept] == ["New thing"] and reasons == []


def test_similar_titles():
    assert similar_titles("member_total has no test", "member_total has no test for coupon math")
    assert similar_titles("Cart test lives in the users test file", "Cart test in users module")
    assert not similar_titles("is_admin name is misleading", "Password compared with ==")
