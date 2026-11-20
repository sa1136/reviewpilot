"""Human approval and durability: SQLite checkpoints in a temp dir, fake models, fake GitHub.

Each "process" opens its own checkpointer connection and builds its own graph, like a new run
of the CLI would."""

import pytest

from reviewpilot.checkpoint import list_threads, open_checkpointer, thread_id
from reviewpilot.cli import (
    cmd_approve,
    cmd_pending,
    interactive_decisions,
    parse_args,
    start_review,
)
from reviewpilot.config import Settings
from reviewpilot.graph import build_graph, config_for
from reviewpilot.llm import FakeModel, fake_factory
from reviewpilot.models import AgentOutput, Finding, FindingDecision, JudgeVerdict
from tests.test_post import REPO, SHA, FakeGitHubAPI, client

PATCH = "@@ -0,0 +1,4 @@\n+a = 1\n+b = 2\n+c = 3\n+d = 4"


def finding(line, title, category="bug"):
    return Finding(file="app.py", line=line, severity="major", category=category, title=title,
                   body=f"Body {title}.", evidence="abcd"[line - 1] + f" = {line}",
                   confidence=0.9)  # fmt: skip


def judge_responder(msgs):
    """Confirm everything except titles starting with 'Maybe' (rejected -> possible issue)."""
    decisions = []
    for chunk in msgs[-1].content.split("- id=")[1:]:
        fid, title = chunk.split()[0], chunk.split("] ", 1)[1].split("\n")[0]
        action = "reject" if title.startswith("Maybe") else "confirm"
        decisions.append(FindingDecision(finding_id=fid, decision=action, reason="not sure"))
    return JudgeVerdict(decisions=decisions)


class World:
    """Fake models shared by every simulated process, so calls can be counted in total."""

    def __init__(self, tmp_path, judge=None):
        self.settings = Settings(
            num_agents=2, github_token="t", checkpoint_db=str(tmp_path / "checkpoints.db")
        )
        self.api = FakeGitHubAPI(files=[{"filename": "app.py", "status": "added", "patch": PATCH}])
        self.correctness = FakeModel(
            lambda m: AgentOutput(
                findings=[
                    finding(1, "Wrong value for a"),
                    finding(2, "Wrong value for b"),
                    finding(3, "Maybe c overflows"),
                ]
            )
        )
        self.quality = FakeModel(lambda m: AgentOutput())
        self.judge = judge or FakeModel(judge_responder)
        self.models = {
            "correctness": self.correctness,
            "quality": self.quality,
            "judge": self.judge,
        }

    def review(self, **kwargs):
        """One CLI process: `reviewpilot review`."""
        with open_checkpointer(self.settings) as saver:
            gh = client(self.api)
            return start_review(
                self.settings, gh, fake_factory(self.models), saver, REPO, 1, **kwargs
            )

    def approve(self, *flags, ask=input):
        """One CLI process: `reviewpilot approve ...`."""
        args = parse_args(["approve", "--repo", REPO, "--pr", "1", *flags])
        with open_checkpointer(self.settings) as saver:
            return cmd_approve(args, self.settings, client(self.api), saver, ask=ask)

    def state(self, tid=None):
        with open_checkpointer(self.settings) as saver:
            graph = build_graph(self.settings, fake_factory(self.models), client(self.api),
                                checkpointer=saver)  # fmt: skip
            return graph.get_state(config_for(tid or thread_id(REPO, 1, SHA)))

    def writes(self):
        return [(m, p) for m, p, _ in self.api.requests if m != "GET"]


def test_review_in_one_process_approve_and_post_in_another(tmp_path, capsys):
    w = World(tmp_path)
    assert w.review() == 0
    out = capsys.readouterr().out
    assert "Waiting for approval: o/r#1@abc1234def" in out
    assert "i1" in out and "i2" in out and "p1" in out
    assert w.writes() == []  # nothing posted at the approval step

    assert w.approve("--approve", "all", "--post") == 0
    (review,) = w.api.sent("POST", "/reviews")
    assert [c["line"] for c in review["comments"]] == [1, 2]
    assert len(w.api.sent("POST", "/issues/1/comments")) == 1
    # the agents ran exactly once in total, across both processes
    assert len(w.correctness.calls) == 1 and len(w.quality.calls) == 1 and len(w.judge.calls) == 1


def test_crash_in_judge_resumes_from_checkpoint_without_rerunning_agents(tmp_path):
    calls = []

    def crash_once(msgs):
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt("process killed")  # not an Exception: nothing catches it
        return judge_responder(msgs)

    judge = FakeModel(crash_once)
    w = World(tmp_path, judge=judge)
    with pytest.raises(KeyboardInterrupt):
        w.review()
    assert len(w.correctness.calls) == 1 and len(judge.calls) == 1

    assert w.review() == 0  # same command again, new process
    assert len(w.correctness.calls) == 1  # agents were not rerun
    assert len(w.quality.calls) == 1
    assert len(judge.calls) == 2  # only the judge ran again
    assert w.state().next == ("approval",)


def test_rejected_never_posted_edited_used_promoted_posted(tmp_path):
    w = World(tmp_path)
    w.review()
    assert w.approve("--reject", "i1", "--edit", "i2=Please use 2.0 here.", "--promote", "p1",
                     "--post") == 0  # fmt: skip

    (review,) = w.api.sent("POST", "/reviews")
    bodies = {c["line"]: c["body"] for c in review["comments"]}
    assert set(bodies) == {2, 3}  # i1 (line 1) rejected; p1 (line 3) promoted
    assert bodies[2].endswith("Please use 2.0 here.")
    assert "Body Wrong value for b." not in bodies[2]
    assert "Maybe c overflows" in bodies[3]

    values = w.state().values
    edit = next(e for e in values["decisions_log"] if e["id"] == "i2")
    assert edit["original"] == "Body Wrong value for b." and edit["body"] == "Please use 2.0 here."
    assert values["review_stats"] | {} == {"approve": 0, "reject": 1, "edit": 1, "promote": 1,
                                           "total": 3, "by_human": 3}  # fmt: skip


def test_save_without_post_then_post_later_keeps_decisions(tmp_path, capsys):
    w = World(tmp_path)
    w.review()
    assert w.approve("--reject", "i1") == 0
    out = capsys.readouterr().out
    assert "Not posted yet" in out
    assert "inline [major] app.py:2 Wrong value for b" in out
    assert "app.py:1" not in out.split("This is what would be posted")[1].split("##")[0]
    assert w.writes() == []

    assert w.approve("--post") == 0  # no new decisions: the saved ones apply
    (review,) = w.api.sent("POST", "/reviews")
    assert [c["line"] for c in review["comments"]] == [2]


def test_already_posted_exits_without_writing_and_fresh_starts_a_new_thread(tmp_path, capsys):
    w = World(tmp_path)
    w.review()
    w.approve("--approve", "all", "--post")
    requests_before = len(w.api.requests)

    assert w.review() == 0
    assert "already posted for abc1234" in capsys.readouterr().out
    new_requests = w.api.requests[requests_before:]
    assert [(m, p) for m, p, _ in new_requests] == [("GET", f"/repos/{REPO}/pulls/1")]
    assert len(w.correctness.calls) == 1

    assert w.review(fresh=True) == 0
    assert len(w.correctness.calls) == 2  # a real new review
    with open_checkpointer(w.settings) as saver:
        threads = list_threads(saver, thread_id(REPO, 1, SHA))
    assert len(threads) == 2 and "~fresh-" in threads[0]


def test_review_again_while_waiting_resumes_instead_of_reviewing(tmp_path, capsys):
    w = World(tmp_path)
    w.review()
    assert w.review() == 0
    assert "waiting for approval" in capsys.readouterr().out
    assert len(w.correctness.calls) == 1


def test_pending_lists_waiting_reviews(tmp_path, capsys):
    w = World(tmp_path)
    w.review()
    capsys.readouterr()
    with open_checkpointer(w.settings) as saver:
        assert cmd_pending(w.settings, saver) == 0
    out = capsys.readouterr().out
    assert out.startswith("o/r#1@abc1234def  (2 inline, 1 possible")


def test_invalid_decisions_are_refused_and_nothing_saved(tmp_path, capsys):
    w = World(tmp_path)
    w.review()
    assert w.approve("--promote", "i1", "--reject", "x9") == 2
    err = capsys.readouterr().err
    assert "x9: no such item" in err and "only possible issues" in err
    assert w.state().values["human_decisions"] == {}


def test_interactive_mode():
    items = [
        {"id": "i1", "kind": "inline", "default": "approve", "action": "approve",
         "severity": "major", "file": "a.py", "line": 1, "title": "t", "agent": "correctness"},
        {"id": "p1", "kind": "possible", "default": "reject", "action": "reject",
         "severity": "major", "file": "a.py", "line": 2, "title": "u", "agent": "security",
         "reason": "r"},
        {"id": "t1", "kind": "tests", "default": "approve", "action": "approve", "text": "x"},
    ]  # fmt: skip
    answers = iter(["e", "Better text", "p", ""])
    decisions = interactive_decisions(items, ask=lambda _prompt: next(answers))
    assert decisions == [
        {"id": "i1", "action": "edit", "body": "Better text"},
        {"id": "p1", "action": "promote"},
    ]


def test_auto_approve_posts_inline_but_no_possible_issues(tmp_path):
    w = World(tmp_path)
    assert w.review(auto_approve=True) == 0
    (review,) = w.api.sent("POST", "/reviews")
    assert [c["line"] for c in review["comments"]] == [1, 2]
    (summary,) = w.api.sent("POST", "/issues/1/comments")
    assert "Maybe c overflows" not in summary["body"]
