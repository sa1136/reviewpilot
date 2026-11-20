from reviewpilot.router import is_security_sensitive, is_test, route

ALL = ["correctness", "security", "quality"]


def test_docs_only_runs_quality():
    agents, reasons = route(["README.md", "docs/guide.rst", "NOTES.txt"], ALL)
    assert agents == ["quality"]
    assert reasons["quality"] == "docs only PR"
    assert reasons["correctness"].startswith("skipped")


def test_tests_only_runs_correctness_and_quality():
    agents, reasons = route(["tests/test_cart.py", "web/cart.spec.ts", "CHANGELOG.md"], ALL)
    assert agents == ["correctness", "quality"]
    assert reasons["security"] == "skipped: tests only PR"


def test_code_runs_all_enabled_agents():
    agents, reasons = route(["src/cart.py", "README.md"], ALL)
    assert agents == ALL
    assert reasons["correctness"] == "code changed (1 file)"


def test_security_added_for_sensitive_path_even_in_docs_or_tests():
    agents, reasons = route(["docs/auth.md"], ALL)
    assert agents == ["security", "quality"]
    assert "docs/auth.md" in reasons["security"]

    agents, _ = route(["tests/test_login.py"], ALL)
    assert agents == ALL


def test_security_path_matching():
    for path in [
        "app/auth/views.py",
        "login.py",
        "lib/token_store.go",
        "secrets.py",
        "reset_password.ts",
        "crypto/hash.py",
        "db/sql_helpers.py",
        "query_builder.py",
        "config.py",
        "app/settings.py",
        ".github/workflows/ci.yml",
        "pyproject.toml",
        ".env.example",
        "Dockerfile",
    ]:
        assert is_security_sensitive(path), path
    for path in ["cart.py", "README.md", "utils/strings.py"]:
        assert not is_security_sensitive(path), path


def test_is_test():
    assert is_test("tests/test_cart.py")
    assert is_test("pkg/__tests__/cart.js")
    assert is_test("cart_test.go")
    assert not is_test("src/contest.py")
    assert not is_test("latest.py")


def test_num_agents_is_the_upper_limit():
    agents, reasons = route(["src/cart.py"], ["correctness", "quality"])
    assert agents == ["correctness", "quality"]
    assert reasons["security"].startswith("skipped: not enabled by num_agents")

    agents, reasons = route(["README.md"], ["correctness"])
    assert agents == []
    assert reasons["quality"].startswith("skipped: not enabled by num_agents")


def test_no_files():
    agents, reasons = route([], ALL)
    assert agents == []
    assert all(r == "skipped: no reviewable files" for r in reasons.values())
