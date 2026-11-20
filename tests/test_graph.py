"""Graph path tests. FakeModel and a fake GitHub only: no network, no real LLM."""

from reviewpilot.config import MAX_LOOPS_CAP, Settings
from reviewpilot.github import PullRequest
from reviewpilot.graph import RECURSION_LIMIT, build_graph, run_review
from reviewpilot.llm import FakeModel, fake_factory
from reviewpilot.models import (
    LANE_CATEGORIES,
    AgentFeedback,
    AgentOutput,
    Finding,
    FindingDecision,
    JudgeVerdict,
)
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


CODE = {2: "x = 1", 3: "y = x / 0"}  # added lines in PATCH


def finding(title: str, line: int = 2, severity: str = "major", category="bug") -> Finding:
    return Finding(
        file="app.py",
        line=line,
        severity=severity,
        category=category,
        title=title,
        body="Why it matters.",
        evidence=CODE[line],
        confidence=0.9,
    )


def role_of(msgs) -> str:
    """Which agent a prompt is for, read from its system prompt ("Role: quality reviewer")."""
    return msgs[0].content[0]["text"].split("Role: ", 1)[1].split()[0]


def in_lane(title: str, msgs, line: int = 2) -> Finding:
    return finding(title, line=line, category=LANE_CATEGORIES[role_of(msgs)][0])


def agent_says(*titles: str) -> FakeModel:
    """Fake agent that returns the same in lane findings on every call."""
    return FakeModel(lambda msgs: AgentOutput(findings=[in_lane(t, msgs) for t in titles]))


def agent_says_new(prefix: str) -> FakeModel:
    """Fake agent that reports a different issue every call, so nothing is a repeat."""
    calls = []

    def respond(msgs):
        calls.append(1)
        return AgentOutput(findings=[in_lane(f"{prefix}{len(calls)}", msgs)])

    return FakeModel(respond)


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
        notes = [AgentFeedback(agent=a, feedback=t) for a, t in (feedback or {}).items()]
        return JudgeVerdict(decisions=out, retry_agents=list(retry), feedback=notes)

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
    """Run the graph. Enabled agents the test does not care about find nothing."""
    settings = settings or Settings()
    for name in settings.reviewers():
        models.setdefault(name, agent_says())
    graph = build_graph(settings, fake_factory(models), github or FakeGitHub())
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

    # num_agents=1 builds and runs correctness only; no quality model is even requested.
    models = {"correctness": agent_says("c1"), "judge": FakeModel(judge())}
    result = run(models, Settings(num_agents=1))
    assert {u["node"] for u in result["usage"]} == {"correctness", "judge"}


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
    q, c = agent_says_new("q"), agent_says_new("c")
    j = FakeModel(judge("reject", retry=["quality", "correctness"]))
    result = run({"quality": q, "correctness": c, "judge": j}, Settings(max_retries=2))

    # first pass + 2 retries
    assert len(q.calls) == len(c.calls) == len(j.calls) == 3
    assert result["accepted"] == []


# 6
def test_max_retries_fifty_still_stops_at_hard_cap():
    q, c = agent_says_new("q"), agent_says_new("c")
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


def test_all_agents_raise_status_failed():
    models = {
        name: FakeModel([RuntimeError(f"{name} down")])
        for name in ("correctness", "security", "quality")
    }
    result = run({**models, "judge": FakeModel([])})
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
    q, c = agent_says("q1"), agent_says_new("c")
    j = FakeModel(sequence(judge("reject", retry=["correctness"]), judge()))
    models = {"quality": q, "correctness": c, "security": agent_says(), "judge": j}
    graph = build_graph(Settings(), fake_factory(models), FakeGitHub())

    trace = [
        node
        for update in graph.stream(
            initial_state("o/r", 1), {"recursion_limit": RECURSION_LIMIT}, stream_mode="updates"
        )
        for node in update
    ]

    assert trace[:2] == ["fetch_pr", "router"]
    assert sorted(trace[2:5]) == ["correctness", "quality", "security"]  # parallel, any order
    assert trace[5:] == ["validate", "judge", "correctness", "validate", "judge", "post_review"]


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


def test_router_runs_only_routed_agents_and_records_reasons():
    docs_patch = "@@ -1 +1,2 @@\n # Title\n+More words."
    github = FakeGitHub([{"filename": "README.md", "status": "modified", "patch": docs_patch}])
    c, s, q = agent_says("c1"), agent_says("s1"), agent_says("q1")
    result = run(
        {"correctness": c, "security": s, "quality": q, "judge": FakeModel(judge())},
        github=github,
    )

    assert result["route"] == ["quality"]
    assert result["route_reasons"]["quality"] == "docs only PR"
    assert c.calls == s.calls == []
    assert len(q.calls) == 1
    assert "Route: quality\n- correctness: skipped: docs only PR" in result["summary"]


def test_retry_feedback_reaches_agent_in_feedback_block_with_file_prompt():
    c = agent_says("c1")
    j = FakeModel(
        sequence(
            judge("reject", retry=["correctness"], feedback={"correctness": "line 3 divides by 0"}),
            judge(),
        )
    )
    run({"correctness": c, "judge": j})

    first, second = c.calls
    assert "<judge_feedback>" not in first[-1].content
    assert "<judge_feedback>\nline 3 divides by 0\n</judge_feedback>" in second[-1].content
    system_text = second[0].content[0]["text"]
    assert "Role: correctness reviewer" in system_text
    assert second[0].content[0]["cache_control"] == {"type": "ephemeral"}
    assert "<untrusted_pr>" in second[-1].content


def verdict(decisions, retry=(), feedback=()):
    return lambda msgs: JudgeVerdict(
        decisions=[
            FindingDecision(finding_id=i, **d)
            for i, d in zip(ids_shown(msgs), decisions, strict=True)
        ],
        retry_agents=list(retry),
        feedback=list(feedback),
    )


def test_retry_needs_more_than_half_rejected():
    # correctness had 2 findings; exactly half rejected is not enough to retry
    c = FakeModel(
        lambda msgs: AgentOutput(findings=[in_lane("c-a", msgs), in_lane("c-b", msgs, 3)])
    )
    j = FakeModel(verdict([{"decision": "confirm"}, {"decision": "reject"}], retry=["correctness"]))
    run({"correctness": c, "judge": j}, Settings(num_agents=1))
    assert len(c.calls) == 1

    # its only finding rejected: more than half, so the retry goes through
    c = agent_says_new("c")
    j = FakeModel(sequence(verdict([{"decision": "reject"}], retry=["correctness"]), judge()))
    run({"correctness": c, "judge": j}, Settings(num_agents=1))
    assert len(c.calls) == 2


def test_clear_miss_allows_retry_even_when_findings_were_right():
    c = agent_says_new("c")
    miss = AgentFeedback(agent="correctness", feedback="line 3 divides by zero", clear_miss=True)
    j = FakeModel(sequence(verdict([{"decision": "confirm"}], ["correctness"], [miss]), judge()))
    run({"correctness": c, "judge": j}, Settings(num_agents=1))
    assert len(c.calls) == 2


def test_accepted_findings_reach_the_retry_prompt():
    c = FakeModel(
        [
            AgentOutput(findings=[finding("Divides by zero", line=3)]),
            AgentOutput(findings=[finding("Unused variable x", line=2)]),
        ]
    )
    miss = AgentFeedback(agent="correctness", feedback="look again at x", clear_miss=True)
    j = FakeModel(sequence(verdict([{"decision": "confirm"}], ["correctness"], [miss]), judge()))
    run({"correctness": c, "judge": j}, Settings(num_agents=1))

    retry_prompt = c.calls[1][-1].content
    assert "<accepted_findings>\n- app.py:3 [major] Divides by zero\n</accepted_findings>" in (
        retry_prompt
    )
    assert "look again at x" in retry_prompt


def test_repeat_of_accepted_is_dropped_before_the_judge_sees_it():
    c = agent_says("Divides by zero")  # says the same thing every round
    miss = AgentFeedback(agent="correctness", feedback="anything else?", clear_miss=True)
    j = FakeModel(sequence(verdict([{"decision": "confirm"}], ["correctness"], [miss]), judge()))
    result = run({"correctness": c, "judge": j}, Settings(num_agents=1))

    assert len(c.calls) == 2
    assert len(j.calls) == 1  # round 2 had only the repeat, so the judge was not called again
    assert [d["reason"] for d in result["dropped"]] == [
        "repeat: same as an accepted finding (correctness)"
    ]
    assert len(result["accepted"]) == 1


def test_judge_final_severity_is_applied():
    c = agent_says("Divides by zero")
    j = FakeModel(verdict([{"decision": "confirm", "final_severity": "critical"}]))
    result = run({"correctness": c, "judge": j}, Settings(num_agents=1))
    assert [f.severity for f in result["accepted"]] == ["critical"]


def test_out_of_lane_is_listed_in_the_summary_and_nits_stay_out_of_inline():
    q = FakeModel(
        lambda msgs: AgentOutput(
            findings=[
                finding("Crashes on zero", line=3, category="bug"),  # out of lane for quality
                finding("Name x is vague", line=2, category="naming", severity="nit"),
            ]
        )
    )
    result = run({"quality": q, "judge": FakeModel(judge())}, Settings(num_agents=2))

    assert result["inline"] == []
    assert [f.title for f in result["nits"]] == ["Name x is vague"]
    assert "Nits:\n- app.py:2 Name x is vague" in result["summary"]
    assert "out of lane: app.py:3 Crashes on zero" in result["summary"]
