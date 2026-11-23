"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, friendlyError, type PendingItem } from "@/lib/api";
import { approveAll, choose, initialChoices, outcome, tally, toDecisions, type Choices } from "@/lib/decisions";
import { Button, ErrorBox, SeverityBadge, Spinner } from "./ui";

type Section = { key: string; title: string; note?: string; items: PendingItem[] };

export function ApprovalPanel({
  reviewId,
  repo,
  pr,
  items,
  demo,
  onPosted,
}: {
  reviewId: string;
  repo: string;
  pr: number;
  items: PendingItem[];
  demo: boolean;
  onPosted: () => void;
}) {
  const [choices, setChoices] = useState<Choices>(() => initialChoices(items));
  const [editing, setEditing] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState<{ title: string; detail: string } | null>(null);
  const [focus, setFocus] = useState(0);
  const refs = useRef<Record<string, HTMLDivElement | null>>({});

  const sections: Section[] = useMemo(
    () =>
      [
        { key: "inline", title: "Inline comments", items: items.filter((i) => i.kind === "inline" || i.kind === "more") },
        {
          key: "possible",
          title: "Possible issues",
          note: "The judge rejected these correctness or security findings. Off by default: approve adds one to the summary, promote makes it an inline comment.",
          items: items.filter((i) => i.kind === "possible"),
        },
        { key: "nit", title: "Nits", note: "Summary only, never inline.", items: items.filter((i) => i.kind === "nit") },
        { key: "tests", title: "New functions without tests", note: "One grouped line in the summary.", items: items.filter((i) => i.kind === "tests") },
      ].filter((s) => s.items.length > 0),
    [items],
  );
  const order = useMemo(() => sections.flatMap((s) => s.items), [sections]);
  const counts = tally(items, choices);

  const set = useCallback((id: string, patch: Parameters<typeof choose>[2]) => {
    setChoices((c) => choose(c, id, patch));
    setConfirming(false);
  }, []);

  // Keyboard: j/k move, a approve, r reject, e edit, p promote (possible issues only).
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      if (target.closest("textarea, input, [contenteditable=true]") || event.metaKey || event.ctrlKey || event.altKey) return;
      const item = order[focus];
      const move = (delta: number) => {
        const next = Math.min(Math.max(focus + delta, 0), order.length - 1);
        setFocus(next);
        refs.current[order[next]?.id]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      };
      if (event.key === "j") move(1);
      else if (event.key === "k") move(-1);
      else if (!item) return;
      else if (event.key === "a") set(item.id, { action: "approve" });
      else if (event.key === "r") set(item.id, { action: "reject" });
      else if (event.key === "p" && item.kind === "possible") set(item.id, { action: "promote" });
      else if (event.key === "e") {
        event.preventDefault();
        setEditing(item.id);
      } else return;
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [order, focus, set]);

  async function post() {
    setPosting(true);
    setError(null);
    try {
      await api.decide(reviewId, toDecisions(items, choices), true);
      onPosted();
    } catch (e) {
      setError(friendlyError(e));
      setPosting(false);
      setConfirming(false);
    }
  }

  return (
    <section aria-labelledby="approval-title" className="space-y-5" data-testid="approval-panel">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="approval-title" className="text-lg font-semibold tracking-tight">
            Your approval
          </h2>
          <p className="text-sm text-muted">
            Nothing is posted until you say so.{" "}
            <span className="hidden text-faint sm:inline">
              Keys: <Kbd>j</Kbd>/<Kbd>k</Kbd> move, <Kbd>a</Kbd> approve, <Kbd>r</Kbd> reject, <Kbd>e</Kbd> edit
            </span>
          </p>
        </div>
        <Button size="sm" onClick={() => setChoices((c) => approveAll(items, c))} data-testid="approve-all">
          Approve all
        </Button>
      </div>

      {items.length === 0 && (
        <p className="rounded-xl border border-dashed border-line-strong p-6 text-center text-sm text-muted">
          No comments to approve. The agents found nothing worth posting; you can still post the summary.
        </p>
      )}

      {sections.map((section) => (
        <div key={section.key} className="space-y-2.5" data-testid={`section-${section.key}`}>
          <div className="flex items-baseline gap-2">
            <h3 className="text-sm font-semibold">{section.title}</h3>
            <span className="text-xs tabular-nums text-faint">{section.items.length}</span>
            {section.key === "possible" && (
              <span className="rounded-full bg-surface-2 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-faint">off by default</span>
            )}
          </div>
          {section.note && <p className="-mt-1 text-xs text-faint">{section.note}</p>}
          {section.items.map((item) => (
            <ItemCard
              key={item.id}
              item={item}
              choice={choices[item.id]}
              focused={order[focus]?.id === item.id}
              editing={editing === item.id}
              refCallback={(el) => {
                refs.current[item.id] = el;
              }}
              onFocus={() => setFocus(order.findIndex((o) => o.id === item.id))}
              onChoose={(patch) => set(item.id, patch)}
              onEdit={(open) => setEditing(open ? item.id : null)}
            />
          ))}
        </div>
      ))}

      <div className="sticky bottom-3 z-10 rounded-xl border border-line-strong bg-surface/95 p-3 shadow-lg shadow-black/5 backdrop-blur">
        {error && (
          <div className="mb-3">
            <ErrorBox title={error.title} detail={error.detail} />
          </div>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <p className="mr-auto text-sm text-muted" data-testid="post-tally">
            <span className="font-medium tabular-nums text-fg">{counts.inline}</span> inline ·{" "}
            <span className="tabular-nums text-fg">{counts.summary}</span> in the summary ·{" "}
            <span className="tabular-nums">{counts.off}</span> not posted
          </p>
          {!confirming ? (
            <Button variant="primary" onClick={() => setConfirming(true)} disabled={posting} data-testid="post-button">
              Post {counts.inline} to GitHub
            </Button>
          ) : (
            <div className="flex flex-wrap items-center gap-2" data-testid="confirm-post">
              <span className="text-sm">
                Post {counts.inline} inline comment{counts.inline === 1 ? "" : "s"} and the summary to{" "}
                <span className="font-mono">
                  {repo}#{pr}
                </span>
                {demo && <span className="text-faint"> (demo: nothing leaves this machine)</span>}?
              </span>
              <Button variant="ghost" onClick={() => setConfirming(false)} disabled={posting}>
                Cancel
              </Button>
              <Button variant="primary" onClick={post} disabled={posting} data-testid="confirm-post-button">
                {posting ? (
                  <>
                    <Spinner /> Posting
                  </>
                ) : (
                  "Confirm and post"
                )}
              </Button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

const OUTCOME = {
  inline: { label: "Inline comment", cls: "text-accent-text" },
  summary: { label: "Summary", cls: "text-muted" },
  off: { label: "Not posted", cls: "text-faint" },
};

function ItemCard({
  item,
  choice,
  focused,
  editing,
  refCallback,
  onFocus,
  onChoose,
  onEdit,
}: {
  item: PendingItem;
  choice: Choices[string] | undefined;
  focused: boolean;
  editing: boolean;
  refCallback: (el: HTMLDivElement | null) => void;
  onFocus: () => void;
  onChoose: (patch: { action?: "approve" | "reject" | "promote"; body?: string | null }) => void;
  onEdit: (open: boolean) => void;
}) {
  const isTests = item.kind === "tests";
  const original = (isTests ? item.text : item.body) ?? "";
  const body = choice?.body ?? original;
  const edited = choice?.body != null && choice.body !== original;
  const [draft, setDraft] = useState(body);
  const where = outcome(item, choice);
  const rejected = where === "off";
  const action = choice?.action ?? (item.default === "reject" ? "reject" : "approve");

  return (
    <div
      ref={refCallback}
      onClick={onFocus}
      data-testid={`item-${item.id}`}
      data-outcome={where}
      className={`rounded-xl border bg-surface p-4 transition-[opacity,border-color] ${
        focused ? "border-accent/60 ring-1 ring-accent/30" : "border-line"
      } ${rejected ? "opacity-60" : ""}`}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        {!isTests && <SeverityBadge severity={item.severity} />}
        {item.agent && <span className="font-medium capitalize text-muted">{item.agent}</span>}
        {item.file && (
          <span className="min-w-0 truncate font-mono text-muted">
            {item.file}:{item.line}
          </span>
        )}
        {item.kind === "more" && <span className="text-faint">over the comment budget</span>}
        <span className={`ml-auto font-medium ${OUTCOME[where].cls}`} data-testid="outcome">
          {OUTCOME[where].label}
          {edited && " · edited"}
        </span>
      </div>

      {item.code && (
        <pre className="mt-3 overflow-x-auto rounded-lg bg-code px-3 py-2 font-mono text-[12.5px] leading-relaxed">
          <span className="mr-3 select-none text-faint">{item.line}</span>
          {item.code.trim()}
        </pre>
      )}

      {item.title && <p className={`mt-3 font-medium ${rejected ? "line-through decoration-faint" : ""}`}>{item.title}</p>}

      {editing ? (
        <div className="mt-2 space-y-2">
          <label className="sr-only" htmlFor={`edit-${item.id}`}>
            Comment text
          </label>
          <textarea
            id={`edit-${item.id}`}
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onEdit(false);
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && draft.trim()) {
                onChoose({ body: draft, action: action === "reject" ? "approve" : action });
                onEdit(false);
              }
            }}
            rows={Math.min(8, Math.max(3, draft.split("\n").length + 1))}
            className="w-full rounded-lg border border-line-strong bg-canvas p-2.5 text-sm outline-none focus:border-accent"
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="primary"
              disabled={!draft.trim()}
              onClick={() => {
                onChoose({ body: draft, action: action === "reject" ? "approve" : action });
                onEdit(false);
              }}
            >
              Save
            </Button>
            <Button size="sm" variant="ghost" onClick={() => onEdit(false)}>
              Cancel
            </Button>
            {edited && (
              <Button
                size="sm"
                variant="ghost"
                className="ml-auto"
                onClick={() => {
                  onChoose({ body: null });
                  setDraft(original);
                  onEdit(false);
                }}
              >
                Restore original
              </Button>
            )}
          </div>
        </div>
      ) : (
        <p className={`mt-1 whitespace-pre-wrap text-sm ${isTests ? "font-mono text-[12.5px]" : "text-fg/85"}`}>{body}</p>
      )}

      {item.suggestion && !editing && (
        <div className="mt-3">
          <p className="text-[11px] font-medium uppercase tracking-wide text-faint">Suggestion</p>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded-lg bg-code px-3 py-2 font-mono text-[12.5px]">{item.suggestion.trim()}</pre>
        </div>
      )}

      {item.reason && <p className="mt-2 text-xs text-faint">Judge: {item.reason}</p>}

      <div className="mt-3 flex flex-wrap gap-1.5" role="group" aria-label={`Decision for ${item.title ?? item.id}`}>
        <Toggle on={action === "approve"} onClick={() => onChoose({ action: "approve" })} testId="approve">
          {item.kind === "possible" ? "Approve (summary)" : "Approve"}
        </Toggle>
        <Toggle on={action === "reject"} onClick={() => onChoose({ action: "reject" })} testId="reject" tone="danger">
          Reject
        </Toggle>
        <Toggle
          on={editing}
          onClick={() => {
            setDraft(body);
            onEdit(!editing);
          }}
          testId="edit"
        >
          Edit
        </Toggle>
        {item.kind === "possible" && (
          <Toggle on={action === "promote"} onClick={() => onChoose({ action: "promote" })} testId="promote">
            Promote to inline
          </Toggle>
        )}
      </div>
    </div>
  );
}

function Toggle({
  on,
  onClick,
  children,
  testId,
  tone,
}: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
  testId: string;
  tone?: "danger";
}) {
  const active = tone === "danger" ? "border-critical/40 bg-critical-soft text-critical" : "border-accent/40 bg-accent-soft text-accent-text";
  return (
    <button
      type="button"
      aria-pressed={on}
      data-testid={testId}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className={`h-7 rounded-md border px-2.5 text-xs font-medium transition-colors ${
        on ? active : "border-line-strong bg-surface text-muted hover:bg-surface-2 hover:text-fg"
      }`}
    >
      {children}
    </button>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className="rounded border border-line-strong bg-surface-2 px-1 font-mono text-[10px]">{children}</kbd>;
}
