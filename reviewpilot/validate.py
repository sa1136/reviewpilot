"""Deterministic checks on findings before the judge sees them.

Stage 2: pass through, keeping only this round's findings. Stage 4 adds the real rules
(line in diff, evidence really there, dedupe).
"""


def validate(state: dict) -> dict:
    current = [f for f in state["findings"] if f.round == state["round"]]
    return {"candidates": current}
