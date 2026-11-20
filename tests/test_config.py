from reviewpilot.config import MAX_LOOPS_CAP, Settings


def test_defaults():
    s = Settings()
    assert (s.num_agents, s.max_retries, s.max_files, s.max_comments) == (2, 2, 40, 20)
    assert s.max_patch_chars == 12000
    assert s.effective_max_retries == 2


def test_retries_clamped_to_hard_cap():
    assert Settings(max_retries=99).effective_max_retries == MAX_LOOPS_CAP
    assert Settings(max_retries=99, max_loops_cap=50).effective_max_retries == MAX_LOOPS_CAP
    assert Settings(max_retries=-3).effective_max_retries == 0


def test_from_env_reads_overrides(monkeypatch):
    monkeypatch.setenv("REVIEWPILOT_MAX_RETRIES", "5")
    monkeypatch.setenv("GITHUB_TOKEN", "abc")
    s = Settings.from_env()
    assert s.max_retries == 5
    assert s.github_token == "abc"
