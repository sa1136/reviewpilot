"""Settings loaded from the environment (and .env if present)."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Absolute ceiling on review rounds (first pass + retries). Enforced in code; config can only
# lower it, never raise it.
MAX_LOOPS_CAP = 15

# Sub agents in the order num_agents picks them.
AVAILABLE_AGENTS = ("quality", "correctness")

DEFAULT_SKIP_GLOBS = (
    # Lockfiles
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.lock",
    "go.sum",
    # Build output and generated assets
    "dist/*",
    "*/dist/*",
    "build/*",
    "*/build/*",
    "node_modules/*",
    "*/node_modules/*",
    "*.min.js",
    "*.min.css",
    "*.map",
    # Images and other binaries
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.ico",
    "*.svg",
    "*.pdf",
)


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = None
    github_token: str | None = None
    github_api_url: str = "https://api.github.com"

    num_agents: int = 2
    max_retries: int = 2
    max_loops_cap: int = MAX_LOOPS_CAP
    max_files: int = 40
    max_patch_chars: int = 12000
    max_comments: int = 20

    agent_model: str = "claude-sonnet-5-5"
    judge_model: str = "claude-haiku-5-5"

    skip_globs: tuple[str, ...] = field(default=DEFAULT_SKIP_GLOBS)

    @property
    def effective_max_retries(self) -> int:
        """Retry rounds actually allowed. Total rounds (1 + retries) never exceed the hard cap."""
        cap = min(self.max_loops_cap, MAX_LOOPS_CAP)
        return max(0, min(self.max_retries, cap - 1))

    def reviewers(self) -> list[str]:
        """Sub agents to run, picked by num_agents (at least one)."""
        return list(AVAILABLE_AGENTS[: max(1, self.num_agents)])

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            github_token=os.getenv("GITHUB_TOKEN") or None,
            github_api_url=os.getenv("GITHUB_API_URL", "https://api.github.com"),
            num_agents=_int("REVIEWPILOT_NUM_AGENTS", 2),
            max_retries=_int("REVIEWPILOT_MAX_RETRIES", 2),
            max_files=_int("REVIEWPILOT_MAX_FILES", 40),
            max_patch_chars=_int("REVIEWPILOT_MAX_PATCH_CHARS", 12000),
            max_comments=_int("REVIEWPILOT_MAX_COMMENTS", 20),
            agent_model=os.getenv("REVIEWPILOT_AGENT_MODEL", "claude-sonnet-5-5"),
            judge_model=os.getenv("REVIEWPILOT_JUDGE_MODEL", "claude-haiku-5-5"),
        )
