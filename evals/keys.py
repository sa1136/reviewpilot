"""Answer keys: the JSON block at the end of each evals/planted/prN.md."""

import json
import re
from pathlib import Path

PLANTED = Path(__file__).parent / "planted"
SANDBOX_REPO = "sa1136/reviewpilot-sandbox"
LINE_TOLERANCE = 1  # a finding within 1 line of the planted line (or alt line) counts
SPLITS = ("tuned", "seen", "holdout")

_KEY_BLOCK = re.compile(r"## Machine readable key.*?```json\n(.*?)\n```", re.DOTALL)


def parse_key(text: str) -> dict:
    match = _KEY_BLOCK.search(text)
    if not match:
        raise ValueError("no machine readable key block")
    key = json.loads(match.group(1))
    if key["split"] not in SPLITS:
        raise ValueError(f"unknown split {key['split']!r}")
    return key


def load_keys(folder: Path = PLANTED) -> list[dict]:
    """Every answer key, sorted by PR number."""
    keys = [parse_key(p.read_text()) for p in folder.glob("pr*.md")]
    return sorted(keys, key=lambda k: k["pr"])


def example(key: dict, repo: str = SANDBOX_REPO) -> dict:
    """One LangSmith example: inputs, reference outputs and metadata."""
    return {
        "inputs": {"repo": repo, "pr": key["pr"]},
        "outputs": {
            "bugs": key["bugs"],
            "clean_files": key["clean_files"],
            "neutral_files": key["neutral_files"],
            "expected_route": key["expected_route"],
        },
        "metadata": {
            "split": key["split"],
            "language": key.get("language", "Python"),
            "clean_pr": not key["bugs"],
        },
        "split": key["split"],
    }
