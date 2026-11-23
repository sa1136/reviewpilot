import { describe, expect, it } from "vitest";
import log from "./__fixtures__/pr4-events.json";
import type { ReviewSnapshot } from "./api";
import { initialView, reviewReducer, reviewSeconds, STEPS, type ReviewEvent, type ReviewView } from "./review-reducer";

// The real event log of the Stage 7 API run of sandbox PR #4.
const events = log.events as ReviewEvent[];
const feed = (evs: ReviewEvent[], start: ReviewView = initialView(log.review_id)) =>
  evs.reduce((state, event) => reviewReducer(state, { type: "event", event }), start);
const upTo = (type: string) => events.slice(0, events.findIndex((e) => e.type === type) + 1);

describe("reviewReducer on the real PR #4 log", () => {
  it("covers the event types of a full review", () => {
    expect(new Set(events.map((e) => e.type))).toEqual(
      new Set(["review_started", "route_decided", "agent_started", "agent_finished", "validate_done", "judge_round", "awaiting_approval", "posting", "posted"]),
    );
  });

  it("review_started fills the header and moves past Fetch", () => {
    const v = feed(upTo("review_started"));
    expect(v).toMatchObject({ repo: "sa1136/reviewpilot-sandbox", pr: 4, title: "Add coupon helpers for the checkout page", files: 2 });
    expect(v.sha.startsWith("ff8fbbd")).toBe(true);
    expect(STEPS[v.phase]).toBe("Route");
  });

  it("route_decided queues the routed agents with their reasons", () => {
    const v = feed(upTo("route_decided"));
    expect(v.route).toEqual(["correctness", "security", "quality"]);
    expect(v.agents.security).toMatchObject({ status: "queued", reason: "code changed (1 file)" });
    expect(STEPS[v.phase]).toBe("Review");
  });

  it("agents run in parallel, then finish with findings and time", () => {
    const running = feed(events.slice(0, 5));
    expect(Object.values(running.agents).map((a) => a.status)).toEqual(["running", "running", "running"]);
    const done = feed(upTo("validate_done").slice(0, -1));
    expect(done.agents.correctness).toMatchObject({ status: "done", findings: 2, latencyMs: 8067, round: 1 });
    expect(done.agents.quality).toMatchObject({ status: "done", findings: 3, latencyMs: 10182 });
  });

  it("validate and judge lines, then approval with the pending items", () => {
    const v = feed(upTo("awaiting_approval"));
    expect(v.rounds).toEqual([{ round: 1, kept: 5, dropped: { duplicate: 2 }, judged: 5, confirmed: 4, rejected: 1, retrying: [] }]);
    expect(v.status).toBe("awaiting_approval");
    expect(v.pending.map((p) => p.id)).toEqual(["i1", "i2", "i3", "i4"]);
    expect(v.pending.slice(0, 3).map((p) => p.severity)).toEqual(["critical", "critical", "critical"]);
    expect(STEPS[v.phase]).toBe("Your approval");
    expect(reviewSeconds(v)).toBe(20); // 14:10:35 to the judge at 14:10:55
  });

  it("posting then posted ends with the GitHub link and comment count", () => {
    const posting = feed(upTo("posting"));
    expect(posting.status).toBe("posting");
    expect(STEPS[posting.phase]).toBe("Posted");
    const v = feed(events);
    expect(v.status).toBe("posted");
    expect(v.pending).toEqual([]);
    expect(v.posted).toEqual({
      review_url: "https://github.com/sa1136/reviewpilot-sandbox/pull/4#pullrequestreview-6023124655",
      summary_url: "https://github.com/sa1136/reviewpilot-sandbox/pull/4#issuecomment-7103609323",
      inline_comments: 4,
      moved_to_summary: 0,
      skipped: false,
    });
    expect(v.phase).toBe(STEPS.length);
    expect(v.lastEventId).toBe(13);
  });

  it("ignores replayed duplicates", () => {
    const once = feed(events);
    expect(feed(events, once)).toBe(once); // whole log again: same object, nothing applied
    const half = feed(events.slice(0, 7));
    const reconnect = feed(events.slice(3), half); // overlap after a reconnect
    expect(reconnect).toEqual(once);
    expect(reconnect.agents.correctness.findings).toBe(2);
  });
});

describe("reviewReducer edge cases", () => {
  const start = feed(upTo("route_decided"));
  const ev = (id: number, type: string, data: Record<string, unknown>): ReviewEvent => ({ id, type, data });

  it("agent_failed shows the error on that card", () => {
    const v = feed([ev(3, "agent_started", { agent: "security", round: 1 }), ev(4, "agent_failed", { agent: "security", round: 1, error: "timeout" })], start);
    expect(v.agents.security).toMatchObject({ status: "failed", error: "timeout" });
  });

  it("a judge retry sends the agent back and starts a second round", () => {
    const v = feed(
      [
        ev(3, "validate_done", { round: 1, kept: 2, dropped: {} }),
        ev(4, "judge_round", { round: 1, judged: 2, confirmed: 0, rejected: 2, retrying: ["quality"] }),
      ],
      start,
    );
    expect(v.agents.quality.status).toBe("queued");
    expect(STEPS[v.phase]).toBe("Review");
    const r2 = feed([ev(5, "agent_started", { agent: "quality", round: 2 })], v);
    expect(r2.agents.quality).toMatchObject({ status: "running", round: 2 });
  });

  it("error fails the review at the active step", () => {
    const v = feed([ev(3, "error", { message: "RuntimeError: boom" })], start);
    expect(v).toMatchObject({ status: "failed", error: "RuntimeError: boom" });
    expect(STEPS[v.failedStep!]).toBe("Review");
  });

  it("skipped agents keep their reason", () => {
    const v = feed([ev(1, "route_decided", { agents: ["quality"], reasons: { correctness: "docs only", security: "docs only", quality: "docs changed" } })]);
    expect(v.agents.correctness).toMatchObject({ status: "skipped", reason: "docs only" });
    expect(v.agents.quality.status).toBe("queued");
  });

  it("unknown event types are ignored but still advance the cursor", () => {
    const v = feed([ev(3, "something_new", {})], start);
    expect(v.lastEventId).toBe(3);
    expect(v.status).toBe("running");
  });
});

describe("snapshot then replay (refresh mid run)", () => {
  const snapshot: ReviewSnapshot = {
    review_id: log.review_id,
    thread_id: "t",
    repo: "sa1136/reviewpilot-sandbox",
    pr: 4,
    title: "Add coupon helpers for the checkout page",
    sha: "ff8fbbdb",
    status: "running",
    route: ["correctness", "security", "quality"],
    route_reasons: { correctness: "code changed (1 file)", security: "code changed (1 file)", quality: "code changed (1 file)" },
    agents: [
      { agent: "correctness", status: "done", reason: "", findings: 2, latency_ms: 8067 },
      { agent: "security", status: "running", reason: "", findings: 0, latency_ms: 0 },
      { agent: "quality", status: "running", reason: "", findings: 0, latency_ms: 0 },
    ],
    pending: [],
    counts: { critical: 0, major: 0, minor: 0, nit: 0 },
    usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, cost_usd: 0 },
    review_stats: {},
    rounds: [],
    files: 2,
    skipped_files: 0,
    started_at: "2026-11-23T14:10:35+00:00",
    last_event_id: 6,
  };

  it("restores from the snapshot and applies only the events after it", () => {
    const restored = reviewReducer(initialView(), { type: "snapshot", snapshot });
    expect(restored.loaded).toBe(true);
    expect(STEPS[restored.phase]).toBe("Review");
    // The stream replays from last_event_id; anything older is ignored.
    const v = feed(events, restored);
    expect(v).toMatchObject({ status: "posted", lastEventId: 13 });
    expect(v.agents.correctness.findings).toBe(2); // event 6 was in the snapshot, not counted twice
    expect(v.agents.security.findings).toBe(2);
  });

  it("an older snapshot only refreshes the totals", () => {
    const live = feed(events);
    const usage = { ...snapshot.usage, cost_usd: 0.004 };
    const v = reviewReducer(live, { type: "snapshot", snapshot: { ...snapshot, usage } });
    expect(v.status).toBe("posted");
    expect(v.usage?.cost_usd).toBe(0.004);
  });

  it("a posted snapshot shows every step done", () => {
    const v = reviewReducer(initialView(), { type: "snapshot", snapshot: { ...snapshot, status: "posted", last_event_id: 13 } });
    expect(v.phase).toBe(STEPS.length);
  });
});
