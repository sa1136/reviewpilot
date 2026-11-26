// Pure reducer for a live review. Fed a snapshot (GET /reviews/{id}) and then the SSE events
// after the snapshot's last_event_id. Events at or below the last applied id are replays and are
// ignored, so a reconnect that repeats events can never double count.
import type {
  AgentStatus,
  PendingItem,
  Posted,
  ReviewSnapshot,
  ReviewStatus,
  RoundStats,
  SeverityCounts,
  Usage,
} from "./api";

export const AGENTS = ["correctness", "security", "quality"] as const;
export const STEPS = ["Fetch", "Route", "Review", "Validate", "Judge", "Your approval", "Posted"] as const;
const S = { fetch: 0, route: 1, review: 2, validate: 3, judge: 4, approval: 5, posted: 6, done: 7 };

export type AgentView = {
  agent: string;
  status: AgentStatus["status"];
  reason: string;
  findings: number;
  latencyMs: number;
  error: string | null;
  round: number;
};

export type ReviewView = {
  loaded: boolean;
  reviewId: string;
  repo: string;
  pr: number;
  title: string;
  sha: string;
  status: ReviewStatus;
  error: string | null;
  route: string[];
  routeReasons: Record<string, string>;
  routed: boolean;
  agents: Record<string, AgentView>;
  rounds: RoundStats[];
  pending: PendingItem[];
  posted: Posted | null;
  usage: Usage | null;
  counts: SeverityCounts | null;
  reviewStats: Record<string, number>;
  files: number;
  skippedFiles: number;
  startedAt: string | null;
  reviewedAt: string | null;
  reviewSeconds: number | null;
  /** Index into STEPS of the active step; STEPS.length means every step is done. */
  phase: number;
  /** The step that failed, when status is failed. */
  failedStep: number | null;
  lastEventId: number;
};

export type ReviewEvent = { id: number; type: string; data: Record<string, unknown> };

export type ReviewAction =
  | { type: "snapshot"; snapshot: ReviewSnapshot }
  | { type: "event"; event: ReviewEvent };

export function emptyAgents(): Record<string, AgentView> {
  return Object.fromEntries(
    AGENTS.map((agent) => [
      agent,
      { agent, status: "queued", reason: "", findings: 0, latencyMs: 0, error: null, round: 0 } as AgentView,
    ]),
  );
}

export function initialView(reviewId = ""): ReviewView {
  return {
    loaded: false,
    reviewId,
    repo: "",
    pr: 0,
    title: "",
    sha: "",
    status: "running",
    error: null,
    route: [],
    routeReasons: {},
    routed: false,
    agents: emptyAgents(),
    rounds: [],
    pending: [],
    posted: null,
    usage: null,
    counts: null,
    reviewStats: {},
    files: 0,
    skippedFiles: 0,
    startedAt: null,
    reviewedAt: null,
    reviewSeconds: null,
    phase: S.fetch,
    failedStep: null,
    lastEventId: 0,
  };
}

export function reviewReducer(state: ReviewView, action: ReviewAction): ReviewView {
  if (action.type === "snapshot") return applySnapshot(state, action.snapshot);
  const { event } = action;
  if (event.id <= state.lastEventId) return state; // replayed duplicate
  return { ...applyEvent(state, event), lastEventId: event.id };
}

function applySnapshot(state: ReviewView, snap: ReviewSnapshot): ReviewView {
  const extras = {
    usage: snap.usage,
    counts: snap.counts,
    reviewStats: snap.review_stats ?? {},
  };
  // A snapshot older than what the stream already applied only refreshes the totals.
  if (snap.last_event_id < state.lastEventId) return { ...state, ...extras };

  const agents = emptyAgents();
  for (const a of snap.agents) {
    agents[a.agent] = {
      agent: a.agent,
      status: a.status,
      reason: a.reason ?? "",
      findings: a.findings ?? 0,
      latencyMs: a.latency_ms ?? 0,
      error: a.error ?? null,
      round: 0,
    };
  }
  const routed = Object.keys(snap.route_reasons).length > 0;
  const view: ReviewView = {
    ...state,
    ...extras,
    loaded: true,
    reviewId: snap.review_id,
    repo: snap.repo,
    pr: snap.pr,
    title: snap.title,
    sha: snap.sha,
    status: snap.status,
    error: snap.error ?? null,
    route: snap.route,
    routeReasons: snap.route_reasons,
    routed,
    agents,
    rounds: snap.rounds ?? [],
    pending: snap.pending,
    posted: snap.posted ?? null,
    files: snap.files ?? 0,
    skippedFiles: snap.skipped_files ?? 0,
    startedAt: snap.started_at ?? null,
    reviewSeconds: snap.review_seconds ?? null,
    reviewedAt: null,
    lastEventId: snap.last_event_id,
  };
  view.phase = phaseFromSnapshot(view);
  view.failedStep = view.status === "failed" ? Math.min(view.phase, S.posted) : null;
  return view;
}

function phaseFromSnapshot(v: ReviewView): number {
  switch (v.status) {
    case "posted":
      return S.done;
    case "posting":
      return S.posted;
    case "awaiting_approval":
      return S.approval;
  }
  // running or failed: work out how far the log got
  const last = v.rounds[v.rounds.length - 1];
  if (Object.values(v.agents).some((a) => a.status === "running")) return S.review;
  if (last && last.judged != null) return last.retrying?.length ? S.review : S.approval;
  if (last && last.kept != null) return S.judge;
  if (v.routed) return S.review;
  if (v.startedAt) return S.route;
  return S.fetch;
}

function upsertRound(rounds: RoundStats[], round: number, patch: Partial<RoundStats>): RoundStats[] {
  const found = rounds.some((r) => r.round === round);
  const next = found
    ? rounds.map((r) => (r.round === round ? { ...r, ...patch } : r))
    : [...rounds, { round, dropped: {}, retrying: [], ...patch } as RoundStats];
  return next.sort((a, b) => a.round - b.round);
}

const num = (v: unknown, fallback = 0) => (typeof v === "number" ? v : fallback);
const str = (v: unknown) => (typeof v === "string" ? v : "");

function applyEvent(state: ReviewView, { type, data }: ReviewEvent): ReviewView {
  switch (type) {
    case "review_started":
      return {
        ...state,
        repo: str(data.repo) || state.repo,
        pr: num(data.pr, state.pr),
        sha: str(data.sha) || state.sha,
        title: str(data.title) || state.title,
        files: num(data.files),
        skippedFiles: num(data.skipped),
        startedAt: state.startedAt ?? (str(data.at) || null),
        status: "running",
        error: null,
        failedStep: null,
        phase: S.route,
      };

    case "route_decided": {
      const route = (data.agents as string[]) ?? [];
      const reasons = (data.reasons as Record<string, string>) ?? {};
      const agents = { ...state.agents };
      for (const name of Object.keys(agents)) {
        agents[name] = {
          ...agents[name],
          reason: reasons[name] ?? "",
          status: route.includes(name) ? "queued" : "skipped",
        };
      }
      return { ...state, route, routeReasons: reasons, routed: true, agents, phase: S.review };
    }

    case "agent_started":
    case "agent_finished":
    case "agent_failed": {
      const name = str(data.agent);
      const prev = state.agents[name] ?? { ...emptyAgents().correctness, agent: name };
      const next: AgentView = { ...prev, round: num(data.round, prev.round) };
      if (type === "agent_started") {
        next.status = "running";
        next.error = null;
      } else {
        next.status = type === "agent_finished" ? "done" : "failed";
        next.findings += num(data.findings);
        next.latencyMs += num(data.latency_ms);
        next.error = type === "agent_failed" ? str(data.error) || "agent failed" : null;
      }
      return { ...state, agents: { ...state.agents, [name]: next }, phase: S.review };
    }

    case "validate_done":
      return {
        ...state,
        rounds: upsertRound(state.rounds, num(data.round, 1), {
          kept: num(data.kept),
          dropped: (data.dropped as Record<string, number>) ?? {},
        }),
        phase: S.judge,
      };

    case "judge_round": {
      const retrying = (data.retrying as string[]) ?? [];
      const agents = { ...state.agents };
      for (const name of retrying) if (agents[name]) agents[name] = { ...agents[name], status: "queued" };
      return {
        ...state,
        agents,
        rounds: upsertRound(state.rounds, num(data.round, 1), {
          judged: num(data.judged),
          confirmed: num(data.confirmed),
          rejected: num(data.rejected),
          retrying,
        }),
        reviewedAt: str(data.at) || state.reviewedAt,
        phase: retrying.length ? S.review : S.approval,
      };
    }

    case "awaiting_approval":
      return {
        ...state,
        status: "awaiting_approval",
        pending: (data.items as PendingItem[]) ?? [],
        reviewedAt: state.reviewedAt ?? (str(data.at) || null),
        phase: S.approval,
      };

    case "posting":
      return { ...state, status: "posting", phase: S.posted };

    case "posted":
      return {
        ...state,
        status: "posted",
        pending: [],
        posted: {
          review_url: (data.review_url as string | null) ?? null,
          summary_url: (data.summary_url as string | null) ?? null,
          inline_comments: num(data.inline_comments),
          moved_to_summary: num(data.moved_to_summary),
          skipped: Boolean(data.skipped),
        },
        phase: S.done,
      };

    case "error":
      return {
        ...state,
        status: "failed",
        error: str(data.message) || "review failed",
        failedStep: Math.min(state.phase, S.posted),
      };

    default:
      return state; // unknown event types from a newer API are ignored
  }
}

/** Seconds the automated review took (fetch to judge), excluding time waiting for a human. */
export function reviewSeconds(v: ReviewView): number | null {
  if (v.reviewSeconds != null) return v.reviewSeconds;
  if (v.startedAt && v.reviewedAt) return (Date.parse(v.reviewedAt) - Date.parse(v.startedAt)) / 1000;
  return null;
}

export function isTerminal(status: ReviewStatus): boolean {
  return status === "posted" || status === "failed";
}

export function waitsForPerson(status: ReviewStatus): boolean {
  return status === "awaiting_approval";
}
