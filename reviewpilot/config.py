"""Settings loaded from the environment (and .env if present)."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Absolute ceiling on review rounds (first pass + retries). Enforced in code; config can only
# lower it, never raise it.
MAX_LOOPS_CAP = 15

# Sub agents, in display and fan out order.
AVAILABLE_AGENTS = ("correctness", "security", "quality")

# Which agents num_agents enables. Correctness first: bugs matter more than style.
AGENTS_BY_COUNT = {
    1: ("correctness",),
    2: ("correctness", "quality"),
    3: ("correctness", "security", "quality"),
}

# USD per million tokens, from https://platform.claude.com/docs/en/about-claude/pricing
# (checked 2026-11-20). cache_write is the 5 minute TTL price.
PRICING_CHECKED = "2026-11-20"
PRICING = {
    "claude-haiku-5-5": {"input": 0.10, "cache_write": 0.125, "cache_read": 0.01, "output": 0.50},
    "claude-sonnet-5-5": {"input": 2.00, "cache_write": 2.50, "cache_read": 0.10, "output": 10.00},
    "claude-opus-5-5": {"input": 4.00, "cache_write": 5.00, "cache_read": 0.20, "output": 20.00},
}
# Claude Haiku 5.5 charges more when a single request's prompt is over 100,000 tokens.
LONG_PROMPT_THRESHOLD = 100_000
LONG_PROMPT_PRICING = {
    "claude-haiku-5-5": {"input": 0.50, "cache_write": 0.625, "cache_read": 0.05, "output": 2.50},
}


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Estimated USD for one call. `input_tokens` is the full prompt (cached tokens included).
    Returns None for a model missing from the table."""
    prices = PRICING.get(model)
    if prices is None:
        return None
    if input_tokens > LONG_PROMPT_THRESHOLD and model in LONG_PROMPT_PRICING:
        prices = LONG_PROMPT_PRICING[model]
    uncached = max(input_tokens - cache_read_tokens - cache_write_tokens, 0)
    total = (
        uncached * prices["input"]
        + cache_read_tokens * prices["cache_read"]
        + cache_write_tokens * prices["cache_write"]
        + output_tokens * prices["output"]
    )
    return total / 1_000_000


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

    num_agents: int = 3
    max_retries: int = 2
    max_loops_cap: int = MAX_LOOPS_CAP
    max_files: int = 40
    max_patch_chars: int = 12000
    max_comments: int = 20

    agent_model: str = "claude-sonnet-5-5"
    judge_model: str = "claude-haiku-5-5"

    skip_globs: tuple[str, ...] = field(default=DEFAULT_SKIP_GLOBS)

    # API client behavior. These retries are for 429 / 5xx / network errors and are separate
    # from the judge's retry loop.
    max_output_tokens: int = 16000
    request_timeout_s: float = 120.0
    api_max_retries: int = 3

    @property
    def effective_max_retries(self) -> int:
        """Retry rounds actually allowed. Total rounds (1 + retries) never exceed the hard cap."""
        cap = min(self.max_loops_cap, MAX_LOOPS_CAP)
        return max(0, min(self.max_retries, cap - 1))

    def reviewers(self) -> list[str]:
        """Sub agents enabled by num_agents (clamped to 1..3). The router may run fewer."""
        count = min(max(self.num_agents, 1), len(AVAILABLE_AGENTS))
        return list(AGENTS_BY_COUNT[count])

    def model_for(self, role: str) -> str:
        return self.judge_model if role == "judge" else self.agent_model

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            github_token=os.getenv("GITHUB_TOKEN") or None,
            github_api_url=os.getenv("GITHUB_API_URL", "https://api.github.com"),
            num_agents=_int("REVIEWPILOT_NUM_AGENTS", 3),
            max_retries=_int("REVIEWPILOT_MAX_RETRIES", 2),
            max_files=_int("REVIEWPILOT_MAX_FILES", 40),
            max_patch_chars=_int("REVIEWPILOT_MAX_PATCH_CHARS", 12000),
            max_comments=_int("REVIEWPILOT_MAX_COMMENTS", 20),
            agent_model=os.getenv("REVIEWPILOT_AGENT_MODEL", "claude-sonnet-5-5"),
            judge_model=os.getenv("REVIEWPILOT_JUDGE_MODEL", "claude-haiku-5-5"),
        )
