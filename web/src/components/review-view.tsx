"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";
import { api, friendlyError } from "@/lib/api";
import { AGENTS } from "@/lib/review-reducer";
import { useReviewStream } from "@/lib/use-review-stream";
import { ApprovalPanel } from "./approval-panel";
import { AgentCard, ConnectionNote, RoundLines, RouterLine, StepBar, UsageFooter } from "./review-parts";
import { Button, Card, ErrorBox, shortSha, Skeleton, Spinner, StatusPill } from "./ui";

export function ReviewLoader({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <ReviewView reviewId={id} />;
}

export function ReviewView({ reviewId }: { reviewId: string }) {
  const { view, connection, loadError, refresh } = useReviewStream(reviewId);
  const [demo, setDemo] = useState(false);
  const router = useRouter();
  const [rerun, setRerun] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });

  useEffect(() => {
    api.health().then((h) => setDemo(h.demo ?? false), () => undefined);
  }, []);

  if (loadError) {
    const { title, detail } = friendlyError(loadError);
    return (
      <div className="mx-auto max-w-xl pt-10">
        <ErrorBox
          title={loadError.status === 404 ? "Review not found" : title}
          detail={loadError.status === 404 ? "There's no review with this id. It may have been started on another server." : detail}
          action={
            <Link href="/" className="text-sm font-medium text-accent-text hover:underline">
              Start a new review
            </Link>
          }
        />
      </div>
    );
  }

  if (!view.loaded) return <ReviewSkeleton />;

  const prUrl = view.repo && view.pr ? `https://github.com/${view.repo}/pull/${view.pr}` : null;

  async function runAgain() {
    if (!prUrl) return;
    setRerun({ busy: true, error: null });
    try {
      const ref = await api.startReview(prUrl, true);
      router.push(`/reviews/${ref.review_id}`);
    } catch (e) {
      setRerun({ busy: false, error: friendlyError(e).detail });
    }
  }

  return (
    <div className="space-y-6" data-testid="review" data-status={view.status}>
      {/* Header */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill status={view.status} />
          <ConnectionNote connection={connection} />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight" data-testid="pr-title">
          {view.title || <Skeleton className="h-7 w-72" />}
        </h1>
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
          <span className="font-mono">
            {view.repo}#{view.pr}
          </span>
          {view.sha && <span className="font-mono text-faint">@ {shortSha(view.sha)}</span>}
          {prUrl && (
            <a href={prUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent-text hover:underline">
              Open on GitHub
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M7 17L17 7M9 7h8v8" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </a>
          )}
        </p>
      </div>

      <StepBar view={view} />

      <Card className="space-y-4 p-4 sm:p-5">
        {view.routed ? <RouterLine view={view} /> : <Skeleton className="h-4 w-64" />}
        <div className="grid gap-3 sm:grid-cols-3">
          {AGENTS.map((name) => (
            <AgentCard key={name} agent={view.agents[name]} />
          ))}
        </div>
        <RoundLines view={view} />
      </Card>

      {view.status === "failed" && (
        <ErrorBox
          title="This review failed"
          detail={`${view.error ?? "Unknown error"}. Nothing was posted to GitHub.`}
          action={
            <Button size="sm" onClick={runAgain} disabled={rerun.busy}>
              {rerun.busy ? <Spinner /> : null} Run again
            </Button>
          }
        />
      )}

      {view.status === "awaiting_approval" && (
        <ApprovalPanel
          key={view.lastEventId}
          reviewId={reviewId}
          repo={view.repo}
          pr={view.pr}
          items={view.pending}
          demo={demo}
          onPosted={() => void refresh()}
        />
      )}

      {view.status === "posting" && (
        <Card className="flex items-center gap-3 p-5 text-sm">
          <Spinner className="h-4 w-4 text-accent" /> Posting the review to GitHub…
        </Card>
      )}

      {view.status === "posted" && <PostedPanel view={view} onRunAgain={runAgain} busy={rerun.busy} demo={demo} />}
      {rerun.error && <ErrorBox title="Couldn't start a new review" detail={rerun.error} />}

      <UsageFooter view={view} />
    </div>
  );
}

function PostedPanel({
  view,
  onRunAgain,
  busy,
  demo,
}: {
  view: ReturnType<typeof useReviewStream>["view"];
  onRunAgain: () => void;
  busy: boolean;
  demo: boolean;
}) {
  const posted = view.posted;
  const already = posted?.skipped;
  return (
    <Card className="p-5" data-testid="posted-panel">
      <div className="flex flex-wrap items-start gap-4">
        <div className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-ok-soft text-ok">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true">
            <path d="M5 12.5l4.5 4.5L19 7.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
        <div className="min-w-0 flex-1">
          {already || !posted ? (
            <>
              <p className="font-semibold">Already posted for this commit</p>
              <p className="mt-1 text-sm text-muted">
                ReviewPilot posts once per commit, so nothing new was written. Push a new commit, or run a fresh review to post again.
              </p>
            </>
          ) : (
            <>
              <p className="font-semibold" data-testid="posted-title">
                Posted {posted.inline_comments} inline comment{posted.inline_comments === 1 ? "" : "s"} and the summary
                {demo && <span className="font-normal text-faint"> (demo)</span>}
              </p>
              {posted.moved_to_summary > 0 && (
                <p className="mt-1 text-sm text-muted">{posted.moved_to_summary} comment(s) were rejected by GitHub and moved to the summary.</p>
              )}
            </>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            {posted?.review_url && (
              <a href={posted.review_url} target="_blank" rel="noreferrer" data-testid="review-link">
                <Button variant="primary" size="sm" tabIndex={-1}>
                  View review on GitHub
                </Button>
              </a>
            )}
            {posted?.summary_url && (
              <a href={posted.summary_url} target="_blank" rel="noreferrer">
                <Button size="sm" tabIndex={-1}>
                  Summary comment
                </Button>
              </a>
            )}
            <Button size="sm" variant="ghost" onClick={onRunAgain} disabled={busy}>
              {busy && <Spinner />} Run a fresh review
            </Button>
          </div>
        </div>
      </div>
    </Card>
  );
}

export function ReviewSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading review">
      <div className="space-y-2">
        <Skeleton className="h-5 w-24 rounded-full" />
        <Skeleton className="h-7 w-80 max-w-full" />
        <Skeleton className="h-4 w-56" />
      </div>
      <div className="grid grid-cols-7 gap-1">
        {Array.from({ length: 7 }, (_, i) => (
          <Skeleton key={i} className="h-1.5" />
        ))}
      </div>
      <Card className="space-y-4 p-5">
        <Skeleton className="h-4 w-64" />
        <div className="grid gap-3 sm:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-28 rounded-xl" />
          ))}
        </div>
      </Card>
    </div>
  );
}
