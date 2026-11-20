from reviewpilot.config import Settings
from reviewpilot.diff import build_file_diffs
from reviewpilot.models import Finding
from reviewpilot.output import plan_comments

PATCH = "@@ -0,0 +1,30 @@\n" + "\n".join(f"+line {i}" for i in range(1, 31))


def files():
    diffs, _ = build_file_diffs(
        [{"filename": "a.py", "status": "added", "patch": PATCH}], (), 40, 12000
    )
    return diffs


def f(line, severity):
    return Finding(
        file="a.py",
        line=line,
        severity=severity,
        category="bug",
        title=f"t{line}",
        body="b",
        evidence=f"line {line}",
        confidence=0.9,
        agent="correctness",
    )


def test_nits_never_inline():
    plan = plan_comments([f(1, "nit"), f(2, "minor"), f(3, "critical")], files(), Settings())
    assert [x.severity for x in plan.inline] == ["critical", "minor"]
    assert [x.line for x in plan.nits] == [1]


def test_min_inline_severity_is_configurable():
    plan = plan_comments(
        [f(1, "minor"), f(2, "major")], files(), Settings(min_inline_severity="major")
    )
    assert [x.line for x in plan.inline] == [2]
    assert [x.line for x in plan.nits] == [1]


def test_budget_keeps_most_severe_and_overflows_the_rest():
    accepted = [f(i, "minor") for i in range(1, 24)] + [f(24, "critical")]
    plan = plan_comments(accepted, files(), Settings(max_comments=20))
    assert len(plan.inline) == 20
    assert plan.inline[0].severity == "critical"
    assert len(plan.overflow) == 4


def test_line_not_added_never_inline():
    plan = plan_comments([f(99, "major")], files(), Settings())
    assert plan.inline == [] and [x.line for x in plan.nits] == [99]
