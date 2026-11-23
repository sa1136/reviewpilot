"use client";

import { useEffect, useState } from "react";
import { STEPS, type AgentView, type ReviewView } from "@/lib/review-reducer";
import type { Connection } from "@/lib/use-review-stream";
import { formatCost, formatSeconds, formatTokens, Spinner } from "./ui";

const AGENT_INFO: Record<string, { label: string; blurb: string }> = {
  correctness: { label: "Correctness", blurb: "Bugs, logic, edge cases" },
  security: { label: "Security", blurb: "Injection, secrets, auth" },
  quality: { label: "Quality", blurb: "Readability, duplication, tests" },
};

export function StepBar({ view }: { view: ReviewView }) {
  return (
    <ol className="grid grid-cols-7 gap-1" aria-label="Review progress">
      {STEPS.map((label, i) => {
        const failed = view.status === "failed" && view.failedStep === i;
        const done = !failed && i < view.phase && !(view.status === "failed" && view.failedStep != null && i > view.failedStep);
        const active = !failed && i === view.phase && view.status !== "failed";
        const waiting = active && label === "Your approval";
        const state = failed ? "failed" : done ? "done" : active ? "active" : "pending";
        return (
          <li key={label} className="min-w-0" data-step={label} data-state={state} aria-current={active ? "step" : undefined}>
            <div
              className={`h-1.5 rounded-full transition-colors duration-500 ${
                failed ? "bg-critical" : done ? "bg-accent" : active ? "bg-accent/45" : "bg-line"
              } ${active && !waiting ? "animate-pulse" : ""}`}
            />
            <p
              className={`mt-1.5 text-[10px] leading-tight sm:text-xs ${
                failed ? "font-medium text-critical" : active ? "font-medium text-fg" : done ? "text-muted" : "text-faint"
              }`}
            >
              {label}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

export function RouterLine({ view }: { view: ReviewView }) {
  if (!view.routed) return null;
  const label = (a: string) => AGENT_INFO[a]?.label ?? a;
  // Agents that share a reason are listed together: "Correctness, Security: code changed".
  const groups = (agents: string[]) => {
    const byReason = new Map<string, string[]>();
    for (const a of agents) byReason.set(view.routeReasons[a] ?? "", [...(byReason.get(view.routeReasons[a] ?? "") ?? []), a]);
    return [...byReason.entries()];
  };
  const skipped = Object.keys(view.routeReasons).filter((a) => !view.route.includes(a));
  return (
    <div className="text-sm text-muted" data-testid="router-line">
      <span className="font-medium text-fg">Router</span> {view.route.length > 0 && "picked "}
      {view.route.length === 0 && "picked no reviewers for these files"}
      {groups(view.route).map(([reason, agents], i) => (
        <span key={reason}>
          {i > 0 && "; "}
          <span className="text-fg">{agents.map(label).join(", ")}</span>
          {reason && <span>: {reason}</span>}
        </span>
      ))}
      {skipped.length > 0 && (
        <span className="text-faint">
          {" "}
          · skipped{" "}
          {groups(skipped).map(([reason, agents], i) => (
            <span key={reason}>
              {i > 0 && "; "}
              {agents.map(label).join(", ")}
              {reason && `: ${reason}`}
            </span>
          ))}
        </span>
      )}
      {view.files > 0 && (
        <span className="text-faint">
          {" "}
          · {view.files} file{view.files === 1 ? "" : "s"} reviewed
          {view.skippedFiles ? `, ${view.skippedFiles} skipped` : ""}
        </span>
      )}
    </div>
  );
}

export function AgentCard({ agent }: { agent: AgentView }) {
  const info = AGENT_INFO[agent.agent] ?? { label: agent.agent, blurb: "" };
  const running = agent.status === "running";
  return (
    <div
      data-testid={`agent-${agent.agent}`}
      data-status={agent.status}
      className={`relative overflow-hidden rounded-xl border bg-surface p-4 transition-colors duration-300 ${
        running ? "border-accent/50" : agent.status === "failed" ? "border-critical/40" : "border-line"
      } ${agent.status === "skipped" ? "opacity-60" : ""}`}
    >
      {running && (
        <div className="pointer-events-none absolute inset-x-0 top-0 h-0.5 overflow-hidden">
          <div className="animate-sweep h-full w-1/3 bg-accent" />
        </div>
      )}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium">{info.label}</p>
          <p className="truncate text-xs text-faint">{info.blurb}</p>
        </div>
        <AgentState agent={agent} />
      </div>
      <div className="mt-4 min-h-10">
        {agent.status === "done" && (
          <div className="flex items-end justify-between">
            <p>
              <span className="text-2xl font-semibold tabular-nums">{agent.findings}</span>{" "}
              <span className="text-sm text-muted">finding{agent.findings === 1 ? "" : "s"}</span>
            </p>
            <p className="text-xs tabular-nums text-faint">
              {formatSeconds(agent.latencyMs / 1000)}
              {agent.round > 1 && ` · round ${agent.round}`}
            </p>
          </div>
        )}
        {running && <p className="text-sm text-muted">Reading the diff{agent.round > 1 ? ` (round ${agent.round})` : ""}…</p>}
        {agent.status === "queued" && <p className="text-sm text-faint">Waiting to start</p>}
        {agent.status === "skipped" && <p className="text-sm text-muted">{agent.reason || "Not needed for these files"}</p>}
        {agent.status === "failed" && (
          <p className="break-words text-sm text-critical" title={agent.error ?? undefined}>
            {agent.error ?? "Agent failed"}
          </p>
        )}
      </div>
    </div>
  );
}

function AgentState({ agent }: { agent: AgentView }) {
  const map = {
    queued: { label: "Queued", cls: "text-faint" },
    running: { label: "Running", cls: "text-accent-text" },
    done: { label: "Done", cls: "text-ok" },
    failed: { label: "Failed", cls: "text-critical" },
    skipped: { label: "Skipped", cls: "text-faint" },
  } as const;
  const s = map[agent.status];
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 text-xs font-medium ${s.cls}`}>
      {agent.status === "running" && <Spinner className="h-3 w-3" />}
      {agent.status === "done" && (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true">
          <path d="M5 12.5l4.5 4.5L19 7.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
      {s.label}
    </span>
  );
}

export function RoundLines({ view }: { view: ReviewView }) {
  if (!view.rounds.length) return null;
  return (
    <ul className="space-y-1.5 text-sm" data-testid="round-lines">
      {view.rounds.map((r) => {
        const dropped = Object.entries(r.dropped ?? {});
        const droppedTotal = dropped.reduce((n, [, c]) => n + c, 0);
        return (
          <li key={r.round} className="flex flex-col gap-x-6 gap-y-1 sm:flex-row">
            {view.rounds.length > 1 && <span className="w-16 shrink-0 text-faint">Round {r.round}</span>}
            {r.kept != null && (
              <span className="text-muted">
                <span className="font-medium text-fg">Validate</span> kept {r.kept}, dropped {droppedTotal}
                {dropped.length > 0 && (
                  <span className="text-faint"> ({dropped.map(([reason, c]) => `${c} ${reason.replaceAll("_", " ")}`).join(", ")})</span>
                )}
              </span>
            )}
            {r.judged != null && (
              <span className="text-muted">
                <span className="font-medium text-fg">Judge</span>{" "}
                {r.judged === 0 ? (
                  "nothing to judge"
                ) : (
                  <>
                    confirmed {r.confirmed} of {r.judged}, rejected {r.rejected}
                  </>
                )}
                {r.retrying && r.retrying.length > 0 && (
                  <span className="text-accent-text"> · sent back {r.retrying.map((a) => AGENT_INFO[a]?.label ?? a).join(", ")}</span>
                )}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function ConnectionNote({ connection }: { connection: Connection }) {
  if (connection !== "reconnecting") return null;
  return (
    <span role="status" data-testid="reconnecting" className="inline-flex items-center gap-1.5 rounded-full bg-major-soft px-2 py-0.5 text-xs font-medium text-major">
      <Spinner className="h-3 w-3" /> Reconnecting
    </span>
  );
}

export function UsageFooter({ view }: { view: ReviewView }) {
  const [now, setNow] = useState(() => Date.now());
  const ticking = view.status === "running" && view.startedAt && !view.reviewedAt && view.reviewSeconds == null;
  useEffect(() => {
    if (!ticking) return;
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer);
  }, [ticking]);

  let seconds: number | null = view.reviewSeconds;
  if (seconds == null && view.startedAt && view.reviewedAt) seconds = (Date.parse(view.reviewedAt) - Date.parse(view.startedAt)) / 1000;
  if (seconds == null && ticking && view.startedAt) seconds = Math.max(0, (now - Date.parse(view.startedAt)) / 1000);

  const u = view.usage;
  const cache = u && (u.cache_read_tokens || u.cache_write_tokens) ? `${formatTokens(u.cache_read_tokens)} read / ${formatTokens(u.cache_write_tokens)} written` : null;
  return (
    <footer className="flex flex-wrap gap-x-6 gap-y-1 border-t border-line pt-4 text-xs text-muted" data-testid="usage-footer">
      <span>
        Tokens <span className="tabular-nums text-fg">{u ? `${formatTokens(u.input_tokens)} in / ${formatTokens(u.output_tokens)} out` : "–"}</span>
      </span>
      {cache && (
        <span>
          Cache <span className="tabular-nums text-fg">{cache}</span>
        </span>
      )}
      <span>
        Cost <span className="tabular-nums text-fg">{u ? formatCost(u.cost_usd) : "–"}</span>
      </span>
      <span title="From fetching the PR to the judge's verdict; excludes time waiting for you">
        Review time <span className="tabular-nums text-fg">{formatSeconds(seconds)}</span>
      </span>
    </footer>
  );
}
