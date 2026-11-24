from reviewpilot.config import MAX_LOOPS_CAP, Settings


def test_defaults():
    s = Settings()
    assert (s.num_agents, s.max_retries, s.max_files, s.max_comments) == (3, 2, 40, 20)
    assert s.max_patch_chars == 12000
    assert s.effective_max_retries == 2
    # Picked from the Stage 9 holdout evals: Haiku everywhere, 3 agents, 2 retries.
    assert s.model_for("correctness") == s.model_for("judge") == "claude-haiku-5-5"


def test_retries_clamped_to_hard_cap():
    # 15 rounds total = first pass + 14 retries
    assert Settings(max_retries=99).effective_max_retries == MAX_LOOPS_CAP - 1
    assert Settings(max_retries=99, max_loops_cap=50).effective_max_retries == MAX_LOOPS_CAP - 1
    assert Settings(max_retries=99, max_loops_cap=4).effective_max_retries == 3
    assert Settings(max_retries=-3).effective_max_retries == 0


def test_from_env_reads_overrides(monkeypatch):
    monkeypatch.setenv("REVIEWPILOT_MAX_RETRIES", "5")
    monkeypatch.setenv("GITHUB_TOKEN", "abc")
    s = Settings.from_env()
    assert s.max_retries == 5
    assert s.github_token == "abc"


def test_reviewers_follow_num_agents():
    assert Settings().reviewers() == ["correctness", "security", "quality"]
    assert Settings(num_agents=1).reviewers() == ["correctness"]
    assert Settings(num_agents=2).reviewers() == ["correctness", "quality"]
    assert Settings(num_agents=3).reviewers() == ["correctness", "security", "quality"]
    assert Settings(num_agents=0).reviewers() == ["correctness"]
    assert Settings(num_agents=9).reviewers() == ["correctness", "security", "quality"]


def test_model_for_role():
    s = Settings(agent_model="a", judge_model="j")
    assert [s.model_for(r) for r in ("correctness", "security", "quality", "judge")] == [
        "a",
        "a",
        "a",
        "j",
    ]


def test_model_override_per_role(monkeypatch):
    from reviewpilot.config import Settings

    monkeypatch.setenv("REVIEWPILOT_AGENT_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("REVIEWPILOT_MODEL_CORRECTNESS", "claude-sonnet-5-5")
    settings = Settings.from_env()
    assert settings.model_for("correctness") == "claude-sonnet-5-5"
    assert settings.model_for("quality") == "claude-haiku-5-5"
    assert settings.model_for("judge") == settings.judge_model
