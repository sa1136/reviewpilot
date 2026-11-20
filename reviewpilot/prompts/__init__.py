"""Prompt files, loaded at runtime so they can be tuned without touching code."""

from functools import cache
from pathlib import Path

PROMPT_DIR = Path(__file__).parent
AGENT_RULES_FILE = "agent_rules.md"


@cache
def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def agent_system_prompt(agent: str) -> str:
    """The agent's own lane plus the rules every agent shares."""
    return f"{load_prompt(agent)}\n\n{load_prompt(AGENT_RULES_FILE.removesuffix('.md'))}"
