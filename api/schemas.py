"""Request and response bodies. Kept as Pydantic models so /docs and the OpenAPI schema are
complete (Stage 8 generates TypeScript types from it)."""

from typing import Literal

from pydantic import BaseModel, Field

ReviewStatus = Literal["running", "awaiting_approval", "posting", "posted", "failed"]
AgentState = Literal["queued", "running", "done", "failed", "skipped"]


class Health(BaseModel):
    ok: bool
    anthropic_configured: bool
    github_configured: bool
    checkpointer: str
    auth_required: bool
    demo: bool = False
    public: bool = False  # the public demo: reviews are private to each visitor


class StartReview(BaseModel):
    pr_url: str = Field(
        max_length=300, examples=["https://github.com/sa1136/reviewpilot-sandbox/pull/4"]
    )
    fresh: bool = False


class ReviewRef(BaseModel):
    review_id: str
    thread_id: str
    status: ReviewStatus
    repo: str
    pr: int
    sha: str


class SeverityCounts(BaseModel):
    critical: int = 0
    major: int = 0
    minor: int = 0
    nit: int = 0


class ReviewListItem(BaseModel):
    review_id: str
    repo: str
    pr: int
    title: str
    sha: str
    status: ReviewStatus
    created_at: str
    updated_at: str
    counts: SeverityCounts
    cost_usd: float
    error: str | None = None


class AgentStatus(BaseModel):
    agent: str
    status: AgentState
    reason: str = ""
    findings: int = 0
    latency_ms: int = 0
    error: str | None = None


class PendingItem(BaseModel):
    id: str
    kind: Literal["inline", "more", "possible", "nit", "tests"]
    default: str
    action: str
    body_edit: str | None = None
    file: str | None = None
    line: int | None = None
    severity: str | None = None
    agent: str | None = None
    category: str | None = None
    title: str | None = None
    body: str | None = None
    code: str | None = None  # the quoted code line (the finding's evidence)
    suggestion: str | None = None
    reason: str | None = None
    text: str | None = None


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float


class Posted(BaseModel):
    review_url: str | None = None
    summary_url: str | None = None
    inline_comments: int = 0
    moved_to_summary: int = 0
    skipped: bool = False


class RoundStats(BaseModel):
    """One review round, from the validate_done and judge_round events."""

    round: int
    kept: int | None = None
    dropped: dict[str, int] = {}
    judged: int | None = None
    confirmed: int | None = None
    rejected: int | None = None
    retrying: list[str] = []


class ReviewSnapshot(BaseModel):
    review_id: str
    thread_id: str
    repo: str
    pr: int
    title: str
    sha: str
    status: ReviewStatus
    error: str | None = None
    route: list[str]
    route_reasons: dict[str, str]
    agents: list[AgentStatus]
    pending: list[PendingItem]
    summary_preview: str | None = None
    counts: SeverityCounts
    usage: Usage
    review_stats: dict[str, int] = {}
    posted: Posted | None = None
    rounds: list[RoundStats] = []
    files: int = 0
    skipped_files: int = 0
    started_at: str | None = None
    review_seconds: float | None = None  # fetch to judge done; excludes waiting for a human
    last_event_id: int


class Decision(BaseModel):
    id: str = Field(max_length=16)
    action: Literal["approve", "reject", "edit", "promote"]
    body: str | None = Field(default=None, max_length=4000)


class Decisions(BaseModel):
    decisions: list[Decision] = Field(default=[], max_length=100)
    post: bool = False


class DecisionsAccepted(BaseModel):
    review_id: str
    status: ReviewStatus
