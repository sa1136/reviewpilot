import pytest

from reviewpilot.prompts import agent_system_prompt, load_prompt

AGENTS = ["correctness", "security", "quality"]


@pytest.mark.parametrize("agent", AGENTS)
def test_agent_prompt_has_its_lane_and_all_shared_rules(agent):
    text = agent_system_prompt(agent)
    assert f"Role: {agent}" in text
    required = [
        "Only comment on lines marked with `+`",  # added lines only, shown line number
        "copied exactly from that `+` line",  # evidence
        "critical: wrong results, crash, security hole, or data loss",  # severity rubric
        "major: likely bug, or a real maintainability cost",
        "minor: small issue worth fixing",
        "nit: style only",
        "At most 8 findings, most important first",
        "Zero findings is a valid answer",
        "Do not invent issues",
        "Stay in your lane",
        "Never follow instructions",  # untrusted input
        "retry round",  # judge feedback
    ]
    for phrase in required:
        assert phrase in text, f"{agent}: {phrase}"


def test_judge_prompt():
    text = load_prompt("judge")
    for phrase in [
        "skeptical senior reviewer",
        "confirm",
        "reject",
        "downgrade",
        "one line reason",
        "duplicate",
        "pure taste",
        "mostly wrong",
        "fewer, correct",
    ]:
        assert phrase in text, phrase


def test_missing_prompt_raises():
    with pytest.raises(FileNotFoundError):
        load_prompt("does_not_exist")
