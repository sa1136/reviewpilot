"""Stage 9 eval code: answer keys, evaluators, target. Fakes only, nothing leaves the machine."""

import pytest

from evals import run as eval_run
from evals.keys import example as key_example
from evals.keys import load_keys, parse_key
from evals.scoring import (
    false_positives,
    precision,
    recall,
    routing,
    score_all,
    trajectory,
    untested_functions,
    valid_trajectory,
)
from evals.target import ReadOnlyGitHub, make_target
from reviewpilot.config import Settings
from reviewpilot.github import PullRequest
from reviewpilot.llm import FakeModel, fake_factory
from reviewpilot.models import AgentOutput, Finding, FindingDecision, JudgeVerdict

REFERENCE = {
    "bugs": [
        {"file": "a.py", "line": 10, "alt_line": 12, "kind": "logic",
         "expected_agent": "correctness", "function": None},
        {"file": "a.py", "line": 20, "alt_line": None, "kind": "missing_test",
         "expected_agent": "quality", "function": "save"},
    ],
    "clean_files": [],
    "neutral_files": ["tests/test_a.py"],
    "expected_route": ["correctness", "security", "quality"],
}  # fmt: skip


def comment(file="a.py", line=10):
    return {"file": file, "line": line, "agent": "correctness", "category": "bug",
            "severity": "major", "title": "t"}  # fmt: skip


def scores(outputs, reference=REFERENCE):
    return score_all({"route": [], "trajectory": [], **outputs}, reference)


def test_every_answer_key_parses_with_the_right_split():
    keys = load_keys()
    assert [k["pr"] for k in keys] == list(range(1, 16))
    splits = {k["pr"]: k["split"] for k in keys}
    assert {splits[1], splits[2]} == {"tuned"}
    assert {splits[n] for n in (3, 4, 5)} == {"seen"}
    assert {splits[n] for n in range(6, 16)} == {"holdout"}
    clean = [k["pr"] for k in keys if not k["bugs"]]
    assert len(clean) == 3
    for key in keys:
        for bug in key["bugs"]:
            assert bug["expected_agent"] in ("correctness", "security", "quality")
            assert (bug["kind"] == "missing_test") == bool(bug["function"])


def test_holdout_has_non_python_prs_and_each_buggy_pr_has_3_to_5_bugs():
    holdout = [k for k in load_keys() if k["split"] == "holdout"]
    assert sum(k["language"] in ("TypeScript", "JavaScript") for k in holdout) >= 2
    assert all(3 <= len(k["bugs"]) <= 5 for k in holdout if k["bugs"])


def test_example_shape():
    ex = key_example(load_keys()[0])
    assert ex["inputs"] == {"repo": "sa1136/reviewpilot-sandbox", "pr": 1}
    assert set(ex["outputs"]) == {"bugs", "clean_files", "neutral_files", "expected_route"}
    assert ex["split"] == "tuned"


def test_parse_key_needs_the_block():
    with pytest.raises(ValueError):
        parse_key("# no key here")


def test_recall_within_one_line_or_alt_line_and_tests_line():
    near = scores({"inline": [comment(line=11)], "tests_line": "New functions without tests: save"})
    assert near["recall"] == 1.0 and near["bugs_caught"] == 2
    alt = scores({"inline": [comment(line=13)]})
    assert alt["bugs_caught"] == 1  # alt line 12, within 1
    far = scores({"inline": [comment(line=14)]})
    assert far["bugs_caught"] == 0
    wrong_file = scores({"inline": [comment(file="b.py", line=10)]})
    assert wrong_file["bugs_caught"] == 0


def test_missing_test_only_counts_from_the_tests_line_not_inline():
    result = scores({"inline": [comment(line=20)]})
    assert result["bugs_caught"] == 0
    assert result["false_positives"] == 1  # an inline comment on the missing test line is extra


def test_false_positives_split_by_clean_and_buggy_prs_and_neutral_files_ignored():
    outputs = {"inline": [comment(line=10), comment(line=40), comment("tests/test_a.py", 3)]}
    fp = {r["key"]: r["score"] for r in false_positives(outputs, REFERENCE)["results"]}
    assert fp == {"false_positives": 1, "fp_clean_pr": None, "fp_extra_buggy_pr": 1}
    clean = {**REFERENCE, "bugs": [], "neutral_files": []}
    fp = {r["key"]: r["score"] for r in false_positives(outputs, clean)["results"]}
    assert fp == {"false_positives": 3, "fp_clean_pr": 3, "fp_extra_buggy_pr": None}


def test_precision_and_recall_on_nothing():
    p = {r["key"]: r["score"] for r in precision({"inline": []}, REFERENCE)["results"]}
    assert p["precision"] is None
    clean = {**REFERENCE, "bugs": []}
    r = {x["key"]: x["score"] for x in recall({"inline": []}, clean)["results"]}
    assert r["recall"] is None and r["bugs_total"] == 0


def test_recall_with_possible_issues():
    r = recall({"inline": [], "possible_issues": [comment(line=10)]}, REFERENCE)
    got = {x["key"]: x["score"] for x in r["results"]}
    assert got["recall"] == 0 and got["recall_with_possible"] == 0.5


def test_untested_functions_parsing():
    assert untested_functions("New functions without tests: a, `b`, c") == {"a", "b", "c"}
    assert untested_functions(None) == set()


def test_routing_respects_num_agents():
    ref = {**REFERENCE, "expected_route": ["correctness", "security", "quality"]}
    two = {"route": ["correctness", "quality"], "enabled": ["correctness", "quality"]}
    assert routing(two, ref)["score"] == 1
    assert (
        routing({"route": ["correctness"], "enabled": ["correctness", "quality"]}, ref)["score"]
        == 0
    )
    docs = {**REFERENCE, "expected_route": ["quality"]}
    assert routing({"route": ["quality"], "enabled": ["correctness", "security", "quality"]},
                   docs)["score"] == 1  # fmt: skip


def test_valid_trajectories():
    route = ["correctness", "quality"]
    start, end = ["fetch_pr", "router"], ["approval", "post_review"]
    one = start + ["quality", "correctness", "validate", "judge"] + end
    assert valid_trajectory(one, route, 3)[0]
    retry = start + ["correctness", "quality", "validate", "judge", "quality", "validate",
                     "judge"] + end  # fmt: skip
    assert valid_trajectory(retry, route, 3)[0]
    assert not valid_trajectory(retry, route, 1)[0]  # 2 rounds, cap 1
    assert not valid_trajectory(start + ["correctness", "validate", "judge"] + end, route, 3)[0]
    assert not valid_trajectory(start + ["validate", "judge"] + end, route, 3)[0]
    assert not valid_trajectory(["router", *one[1:]], route, 3)[0]
    assert valid_trajectory(start + end, [], 3)[0]  # nothing to review
    out = trajectory({"trajectory": one, "route": route, "max_rounds": 3}, REFERENCE)
    assert out["score"] == 1


PATCH = "@@ -0,0 +1,3 @@\n+def save(x):\n+    return x / 0\n+y = 1"


class FakeGitHub:
    def __init__(self):
        self.calls = 0

    def get_pull_request(self, repo, number):
        self.calls += 1
        return PullRequest(repo, number, "T", "B", "sha9", "main", "feat", False)

    def list_files(self, repo, number):
        return [{"filename": "app.py", "status": "added", "patch": PATCH}]

    def post_review(self, *args, **kwargs):  # must never be reached
        raise AssertionError("evals must never post")


def test_read_only_github_cannot_post_and_caches():
    gh = FakeGitHub()
    ro = ReadOnlyGitHub(gh)
    assert not hasattr(ro, "post_review")
    ro.get_pull_request("o/r", 1)
    ro.get_pull_request("o/r", 1)
    assert gh.calls == 1


def test_target_runs_through_judge_auto_approves_and_never_posts():
    bug = Finding(file="app.py", line=2, severity="major", category="bug", title="Divides by 0",
                  body="b", evidence="return x / 0", confidence=0.9)  # fmt: skip

    def judge(msgs):
        ids = [p.split()[0] for p in msgs[-1].content.split("- id=")[1:]]
        return JudgeVerdict(decisions=[FindingDecision(finding_id=i, decision="confirm")
                                       for i in ids])  # fmt: skip

    models = {
        "correctness": FakeModel(lambda m: AgentOutput(findings=[bug])),
        "security": FakeModel(lambda m: AgentOutput()),
        "quality": FakeModel(lambda m: AgentOutput()),
        "judge": FakeModel(judge),
    }
    target = make_target(Settings(), fake_factory(models), github=FakeGitHub())
    out = target({"repo": "o/r", "pr": 3})

    assert out["route"] == ["correctness", "security", "quality"]
    assert [(c["file"], c["line"]) for c in out["inline"]] == [("app.py", 2)]
    assert out["rounds"] == 1 and out["max_rounds"] == 3
    assert out["trajectory"][:2] == ["fetch_pr", "router"]
    assert out["trajectory"][-2:] == ["approval", "post_review"]
    assert out["status"] == "success"
    assert models["judge"].configs[0]["metadata"]["pr"] == 3
    ref = {**REFERENCE, "bugs": [{**REFERENCE["bugs"][0], "file": "app.py", "line": 2,
                                  "alt_line": None}], "neutral_files": []}  # fmt: skip
    s = score_all(out, ref)
    assert s["recall"] == 1 and s["false_positives"] == 0 and s["trajectory"] == 1


def test_budget_guard_refuses_an_experiment_over_budget(monkeypatch, capsys):
    monkeypatch.setattr(eval_run, "spent", lambda: 1.40)
    monkeypatch.setattr(eval_run, "examples_for", lambda client, splits: [object()] * 10)
    monkeypatch.setattr(eval_run, "per_review_estimate", lambda name: 0.01)
    assert eval_run.run_experiment(None, "A", ["holdout"], 3, 1) == 1
    assert "Refused" in capsys.readouterr().out
