"""Pydantic models shared across the graph."""

import hashlib
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

Severity = Literal["critical", "major", "minor", "nit"]
SEVERITY_ORDER: tuple[Severity, ...] = ("critical", "major", "minor", "nit")


class FileDiff(BaseModel):
    path: str
    status: str  # added | modified | renamed | ...
    patch: str
    numbered_patch: str
    valid_lines: set[int]  # new side lines in the diff (added + context)
    added_lines: set[int]  # new side lines added by this PR; only these get inline comments
    previous_path: str | None = None
    truncated: bool = False


class SkippedFile(BaseModel):
    path: str
    reason: str


class Finding(BaseModel):
    file: str = Field(description="Path of the file in the PR")
    line: int = Field(description="New side line number taken from the numbered diff")
    severity: Severity
    category: str = Field(description="bug, edge_case, security, naming, tests, ...")
    title: str = Field(description="One line summary")
    body: str = Field(description="Why it matters, 1 to 3 sentences")
    suggestion: str | None = Field(default=None, description="Optional fix")
    evidence: str = Field(description="Exact code quoted from the diff")
    confidence: float = Field(description="0 to 1")
    # Set by our code after the model answers, so they are hidden from the model's schema.
    agent: SkipJsonSchema[str] = ""
    round: SkipJsonSchema[int] = 0

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, value: float) -> float:
        # The API cannot enforce min/max in the schema, so clamp instead of dropping the output.
        return min(max(value, 0.0), 1.0)

    @property
    def id(self) -> str:
        """Stable id from agent, file, line and title (round is not part of it)."""
        key = f"{self.agent}|{self.file}|{self.line}|{self.title}"
        return hashlib.sha1(key.encode()).hexdigest()[:12]


class AgentOutput(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    notes: str = ""


class FindingDecision(BaseModel):
    finding_id: str
    decision: Literal["confirm", "reject", "downgrade"]
    new_severity: Severity | None = None
    reason: str = ""


class AgentFeedback(BaseModel):
    agent: str
    feedback: str


class JudgeVerdict(BaseModel):
    decisions: list[FindingDecision] = Field(default_factory=list)
    retry_agents: list[str] = Field(default_factory=list)
    # A list, not a dict: the API's structured output cannot express free-form object keys.
    feedback: list[AgentFeedback] = Field(default_factory=list)

    def feedback_for(self, agent: str) -> str:
        return "\n".join(f.feedback for f in self.feedback if f.agent == agent)
