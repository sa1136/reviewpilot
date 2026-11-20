"""Graph path tests. FakeModel and a fake GitHub only: no network, no real LLM."""

from reviewpilot.config import MAX_LOOPS_CAP, Settings
from reviewpilot.github import PullRequest
from reviewpilot.graph import RECURSION_LIMIT, build_graph, run_review
from reviewpilot.llm import FakeModel, fake_factory
from reviewpilot.models import AgentOutput, Finding, FindingDecision, JudgeVerdict
from reviewpilot.state import initial_state

PATCH = "@@ -1,2 +1,4 @@\n import os\n+x = 1\n+y = x / 0\n def f():"


class FakeGitHub:
    def __init__(self, files=None):
        default = [{"filename": "app.py", "status": "modified", "patch": PATCH}]
        self.files = default if files is None else files

    def get_pull_request(self, repo, number):
        return PullRequest(repo, number, "Title", "Body", "sha1", "main", "feat", False)

    def list_files(self, repo, number):
        return self.files


def finding(title: str, line: int = 2, severity: str = "major") -> Finding:
    return Finding(
        file="app.py",
        line=line,
        severity=severity,
        category="bug",
        title=title,
        body="Why it matters.",
        evidence="x = 1",
        confidence=0.9,
    )


def agent_says(*titles: str) -> FakeModel:
    """Fake agent that returns the same findings on every call."""
    return FakeModel(lambda _msgs: AgentOutput(findings=[finding(t) for t in titles]))


def ids_shown(msgs) -> list[str]:
    return [part.split()[0] for part in msgs[-1].content.split("id=")[1:]]


def judge(decision="confirm", retry=(), feedback=None, decisions=None):
    """Judge responder: apply `decision` to every finding shown (or use `decisions` by title)."""

    def respond(msgs):
        out = []
        for chunk in msgs[-1].content.split("- id=")[1:]:
            fid = chunk.split()[0]
            title = chunk.split("] ", 1)[1].split("\n")[0]
            choice = (decisions or {}).get(title, decision)
            if choice is not None:  # None = the judge says nothing about this finding
                out.append(FindingDecision(finding_id=fid, decision=choice))
        return JudgeVerdict(decisions=out, retry_agents=list(retry), feedback=feedback or {})

    return respond


def sequence(*responders):
    """Use responders[i] for call i; the last one repeats."""
    calls = []

    def respond(msgs):
        calls.append(1)
        return responders[min(len(calls), len(responders)) - 1](msgs)

    return respond


def judge_text(model: FakeModel, call: int) -> str:
    return model.calls[call][-1].content


def run(models: dict, settings: Settings | None = None, github=None) -> dict:
    graph = build_graph(settings or Settings(), fake_factory(models), github or FakeGitHub())
    return run_review(graph, "o/r", 1)


# 1
def test_both_agents_run_and_reach_judge():
    q, c, j = agent_says("q1"), agent_says("c1"), FakeModel(judge())
    result = run({"quality": q, "correctness": c, "judge": j})

    assert len(q.calls) == len(c.calls) == len(j.calls) == 1
    assert "q1" in judge_text(j, 0) and "c1" in judge_text(j, 0)
    assert {f.agent for f in result["accepted"]} == {"quality", "correctness"}


# 2
def test_num_agents_two_runs_exactly_quality_and_correctness():
    q, c, j = agent_says("q1"), agent_says("c1"), FakeModel(judge())
    result = run({"quality": q, "correctness": c, "judge": j}, Settings(num_agents=2))
    assert {u["node"] for u in result["usage"]} == {"quality", "correctness", "judge"}

    # num_agents=1 builds and runs quality only; no correctness model is even requested.
    result = run({"quality": agent_says("q1"), "judge": FakeModel(judge())}, Settings(num_agents=1))
    assert {u["node"] for u in result["usage"]} == {"quality", "judge"}


# 3
def test_judge_confirms_round_one_no_retry():
    q, c, j = agent_says("q1"), agent_says("c1"), FakeModel(judge())
    result = run({"quality": q, "correctness": c, "judge": j})

    assert len(q.calls) == len(c.calls) == len(j.calls) == 1
    assert len(result["accepted"]) == 2
    assert result["status"] == "success"


# 4
def test_retry_only_correctness_and_round_two_is_judged():
    q = agent_says("q1")
    c = FakeModel(
        [
            AgentOutput(findings=[finding("c-round1")]),
            AgentOutput(findings=[finding("c-round2", line=3)]),
        ]
    )
    j = FakeModel(
        sequence(
            judge("reject", retry=["correctness"], feedback={"correctness": "check line 3"}),
            judge("confirm"),
        )
    )
    result = run({"quality": q, "correctness": c, "judge": j})

    assert len(q.calls) == 1
    assert len(c.calls) == 2
    assert "check line 3" in c.calls[1][-1].content
    assert len(j.calls) == 2
    assert "c-round2" in judge_text(j, 1)
    assert "q1" not in judge_text(j, 1)  # only this round's findings are judged
    assert [(f.title, f.agent, f.round) for f in result["accepted"]] == [
        ("c-round2", "correctness", 2)
    ]


# 5
def test_always_retry_stops_at_max_retries():
    q, c = agent_says("q"), agent_says("c")
    j = FakeModel(judge("reject", retry=["quality", "correctness"]))
    result = run({"quality": q, "correctness": c, "judge": j}, Settings(max_retries=2))

    # first pass + 2 retries
    assert len(q.calls) == len(c.calls) == len(j.calls) == 3
    assert result["accepted"] == []


# 6
def test_max_retries_fifty_still_stops_at_hard_cap():
    q, c = agent_says("q"), agent_says("c")
    j = FakeModel(judge("reject", retry=["quality", "correctness"]))
    result = run({"quality": q, "correctness": c, "judge": j}, Settings(max_retries=50))

    assert len(j.calls) == MAX_LOOPS_CAP == 15
    assert len(q.calls) == len(c.calls) == MAX_LOOPS_CAP
    assert max(f.round for f in result["findings"]) == MAX_LOOPS_CAP


# 7
def test_rejected_findings_never_accepted():
    q = agent_says("keep", "drop", "soften")
    c = agent_says("undecided")
    j = FakeModel(
        judge(
            decisions={
                "keep": "confirm",
                "drop": "reject",
                "soften": "downgrade",
                "undecided": None,
            }
        )
    )
    result = run({"quality": q, "correctness": c, "judge": j})

    accepted = {f.title: f.severity for f in result["accepted"]}
    assert accepted == {"keep": "major", "soften": "minor"}  # downgraded one level
    assert "drop" not in accepted and "undecided" not in accepted


# 8
def test_one_agent_raises_other_survives_status_partial():
    q = FakeModel([RuntimeError("quality blew up")])
    c, j = agent_says("c1"), FakeModel(judge())
    result = run({"quality": q, "correctness": c, "judge": j})

    assert [f.title for f in result["accepted"]] == ["c1"]
    assert result["status"] == "partial"
    assert result["errors"][0]["node"] == "quality"
    assert "quality blew up" in result["summary"]


def test_both_agents_raise_status_failed():
    q = FakeModel([RuntimeError("q down")])
    c = FakeModel([RuntimeError("c down")])
    result = run({"quality": q, "correctness": c, "judge": FakeModel([])})
    assert result["status"] == "failed"
    assert "No issues found" not in result["summary"]


# 9
def test_judge_raises_status_failed_nothing_accepted():
    q, c = agent_says("q1"), agent_says("c1")
    j = FakeModel([RuntimeError("judge down")])
    result = run({"quality": q, "correctness": c, "judge": j})

    assert result["status"] == "failed"
    assert result["accepted"] == []
    assert "q1" not in result["summary"] and "c1" not in result["summary"]
    assert "no findings are reported as confirmed" in result["summary"]


# 10
def test_trace_node_order_for_one_retry():
    q, c = agent_says("q1"), agent_says("c1")
    j = FakeModel(sequence(judge(retry=["correctness"]), judge()))
    graph = build_graph(
        Settings(), fake_factory({"quality": q, "correctness": c, "judge": j}), FakeGitHub()
    )

    trace = [
        node
        for update in graph.stream(
            initial_state("o/r", 1), {"recursion_limit": RECURSION_LIMIT}, stream_mode="updates"
        )
        for node in update
    ]

    assert trace[0] == "fetch_pr"
    assert sorted(trace[1:3]) == ["correctness", "quality"]  # parallel, order not fixed
    assert trace[3:] == ["validate", "judge", "correctness", "validate", "judge", "post_review"]


def test_no_reviewable_files_skips_agents():
    q, c, j = FakeModel([]), FakeModel([]), FakeModel([])
    github = FakeGitHub([{"filename": "uv.lock", "status": "modified", "patch": PATCH}])
    result = run({"quality": q, "correctness": c, "judge": j}, github=github)

    assert q.calls == c.calls == j.calls == []
    assert result["status"] == "success"
    assert "uv.lock" in result["summary"]


def test_finding_id_is_stable_and_hidden_from_model_schema():
    a = finding("same").model_copy(update={"agent": "quality", "round": 1})
    b = finding("same").model_copy(update={"agent": "quality", "round": 2})
    assert a.id == b.id and len(a.id) == 12
    assert finding("other").model_copy(update={"agent": "quality"}).id != a.id

    props = AgentOutput.model_json_schema()["$defs"]["Finding"]["properties"]
    assert "agent" not in props and "round" not in props
