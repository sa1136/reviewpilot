"""Router: pick which sub agents review this PR, from the changed paths alone (no LLM).

Rules:
- docs only (md, txt, rst)       -> quality
- tests only (tests plus docs)   -> correctness, quality
- anything with code             -> every enabled agent
- security also runs whenever a path looks security sensitive (auth, sql, secrets, config, ...)
num_agents is the upper limit: an agent that is not enabled never runs.
"""

import re
from pathlib import PurePosixPath

from reviewpilot.config import AVAILABLE_AGENTS

DOC_EXTENSIONS = {".md", ".txt", ".rst"}

SECURITY_WORDS = re.compile(r"auth|login|token|secret|password|crypto|sql|query", re.IGNORECASE)
CONFIG_EXTENSIONS = {".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".env"}
CONFIG_NAME = re.compile(r"(^\.env)|config|settings|^dockerfile$", re.IGNORECASE)


def is_doc(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in DOC_EXTENSIONS


def is_test(path: str) -> bool:
    p = PurePosixPath(path)
    name = p.name.lower()
    in_test_dir = any(part.lower() in {"test", "tests", "__tests__"} for part in p.parts[:-1])
    return (
        in_test_dir
        or name.startswith("test_")
        or re.search(r"(_test|\.test|\.spec)\.[a-z]+$", name) is not None
    )


def is_security_sensitive(path: str) -> bool:
    p = PurePosixPath(path)
    return (
        SECURITY_WORDS.search(path) is not None
        or p.suffix.lower() in CONFIG_EXTENSIONS
        or CONFIG_NAME.search(p.name) is not None
    )


def route(paths: list[str], enabled: list[str]) -> tuple[list[str], dict[str, str]]:
    """Return (agents to run, reason per agent). Reasons cover skipped agents too, so routing can
    be scored later."""
    docs = [p for p in paths if is_doc(p)]
    tests = [p for p in paths if not is_doc(p) and is_test(p)]
    code = [p for p in paths if not is_doc(p) and not is_test(p)]
    sensitive = [p for p in paths if is_security_sensitive(p)]

    wanted: dict[str, str] = {}
    if code:
        reason = f"code changed ({len(code)} file{'s' if len(code) != 1 else ''})"
        wanted = {agent: reason for agent in AVAILABLE_AGENTS}
    elif tests:
        wanted = {
            "correctness": "tests only PR",
            "quality": "tests only PR",
        }
    elif docs:
        wanted = {"quality": "docs only PR"}
    if sensitive:
        wanted["security"] = f"security sensitive path: {sensitive[0]}"

    chosen, reasons = [], {}
    for agent in AVAILABLE_AGENTS:
        if agent in wanted and agent in enabled:
            chosen.append(agent)
            reasons[agent] = wanted[agent]
        elif agent in wanted:
            reasons[agent] = f"skipped: not enabled by num_agents ({wanted[agent]})"
        else:
            kind = "docs only PR" if docs and not (tests or code) else "tests only PR"
            reasons[agent] = f"skipped: {kind}" if paths else "skipped: no reviewable files"
    return chosen, reasons
