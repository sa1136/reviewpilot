"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, friendlyError, type ReviewListItem } from "@/lib/api";
import { DEMO_PR_URL } from "@/lib/demo";
import { prUrlProblem } from "@/lib/pr-url";
import { useHealth } from "@/lib/use-health";
import { Button, Card, ErrorBox, formatCost, formatDate, Skeleton, Spinner, StatusPill } from "./ui";

export function HomeView() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [touched, setTouched] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<{ title: string; detail: string } | null>(null);
  const [recent, setRecent] = useState<ReviewListItem[] | null>(null);
  const [recentError, setRecentError] = useState<string | null>(null);
  const { health, waking, unreachable } = useHealth();

  const ready = health !== null || unreachable;
  useEffect(() => {
    if (!ready) return; // wait for a sleeping server to wake
    api.listReviews().then(
      (rows) => setRecent(rows.slice(0, 5)),
      (e) => setRecentError(friendlyError(e).detail),
    );
  }, [ready]);

  const problem = prUrlProblem(url);
  const showProblem = touched && problem;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setTouched(true);
    if (!url.trim() || problem) return;
    await start(url.trim(), false);
  }

  async function start(prUrl: string, fresh: boolean) {
    setSubmitting(true);
    setError(null);
    try {
      const ref = await api.startReview(prUrl, fresh);
      router.push(`/reviews/${ref.review_id}`);
    } catch (e) {
      setError(friendlyError(e));
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-10">
      <section className="pt-6 sm:pt-12">
        <h1 className="max-w-2xl text-3xl font-semibold tracking-tight sm:text-4xl">
          Agents do the first pass. <span className="text-muted">You do the last.</span>
        </h1>
        <p className="mt-3 max-w-2xl text-muted">
          Paste a GitHub pull request. Specialist reviewers check correctness, security and quality in parallel, every
          finding is checked against the real diff and a judge, and nothing reaches GitHub until you approve it.
        </p>

        {waking && (
          <p role="status" data-testid="waking" className="mt-5 flex items-center gap-2 text-sm text-muted">
            <Spinner /> Waking up the server. The free demo sleeps when idle, so this can take about a minute.
          </p>
        )}

        {health?.demo && (
          <Card className="mt-6 max-w-2xl border-accent/30 p-4" data-testid="demo-notice">
            <p className="font-semibold">This is a demo</p>
            <p className="mt-1 text-sm text-muted">
              It replays a recorded ReviewPilot review of one sandbox pull request, so you&apos;ll see the same findings
              whatever link you paste. No model is called, nothing is sent to GitHub, and the cost shown is from the
              recorded run.
            </p>
            <Button variant="primary" className="mt-3" disabled={submitting} onClick={() => void start(DEMO_PR_URL, true)}>
              {submitting ? <Spinner /> : null} Try the demo
            </Button>
            <p className="mt-2 text-xs text-faint">No link or sign up needed. Approve, edit or reject the comments yourself.</p>
          </Card>
        )}

        <form onSubmit={submit} noValidate className="mt-7 max-w-2xl">
          <label htmlFor="pr-url" className="mb-1.5 block text-sm font-medium">
            Pull request link
          </label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              id="pr-url"
              name="pr-url"
              type="url"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              placeholder="https://github.com/owner/repo/pull/123"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                setError(null);
              }}
              onBlur={() => setTouched(true)}
              aria-invalid={Boolean(showProblem)}
              aria-describedby="pr-url-help"
              className={`h-11 w-full min-w-0 rounded-lg border bg-surface px-3 font-mono text-sm outline-none transition-colors placeholder:font-sans placeholder:text-faint focus:border-accent ${
                showProblem ? "border-critical" : "border-line-strong"
              }`}
            />
            <Button type="submit" variant="primary" className="h-11 shrink-0 px-5" disabled={submitting}>
              {submitting ? (
                <>
                  <Spinner /> Starting
                </>
              ) : (
                "Review"
              )}
            </Button>
          </div>
          <p id="pr-url-help" className={`mt-1.5 text-xs ${showProblem ? "text-critical" : "text-faint"}`}>
            {showProblem ? (
              problem
            ) : (
              <>
                For example{" "}
                <button type="button" className="font-mono underline decoration-dotted underline-offset-2 hover:text-fg" onClick={() => setUrl(DEMO_PR_URL)}>
                  {DEMO_PR_URL.replace("https://", "")}
                </button>
              </>
            )}
          </p>
        </form>
        {error && (
          <div className="mt-4 max-w-2xl">
            <ErrorBox title={error.title} detail={error.detail} />
          </div>
        )}
      </section>

      <section>
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">Recent reviews</h2>
          <Link href="/history" className="text-sm text-accent-text hover:underline">
            All history
          </Link>
        </div>
        <Card>
          {recentError ? (
            <p className="p-4 text-sm text-muted">Couldn&apos;t load reviews: {recentError}</p>
          ) : recent === null ? (
            <ul className="divide-y divide-line">
              {[0, 1, 2].map((i) => (
                <li key={i} className="flex items-center gap-3 p-4">
                  <Skeleton className="h-4 w-48" />
                  <Skeleton className="ml-auto h-4 w-20" />
                </li>
              ))}
            </ul>
          ) : recent.length === 0 ? (
            <p className="p-6 text-center text-sm text-muted">No reviews yet. Paste a pull request link above.</p>
          ) : (
            <ul className="divide-y divide-line" data-testid="recent-reviews">
              {recent.map((r) => (
                <li key={r.review_id}>
                  <Link href={`/reviews/${r.review_id}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 transition-colors hover:bg-surface-2">
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{r.title || `${r.repo}#${r.pr}`}</span>
                      <span className="block truncate font-mono text-xs text-faint">
                        {r.repo}#{r.pr}
                      </span>
                    </span>
                    <StatusPill status={r.status} />
                    <span className="w-16 text-right text-xs tabular-nums text-muted">{formatCost(r.cost_usd)}</span>
                    <span className="w-20 text-right text-xs text-faint">{formatDate(r.created_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>
    </div>
  );
}
