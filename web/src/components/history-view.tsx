"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, friendlyError, type ReviewListItem } from "@/lib/api";
import { useHealth } from "@/lib/use-health";
import { Card, ErrorBox, formatCost, formatDate, SeverityCountsRow, Skeleton, StatusPill } from "./ui";

export function HistoryView() {
  const [rows, setRows] = useState<ReviewListItem[] | null>(null);
  const [error, setError] = useState<{ title: string; detail: string } | null>(null);
  const router = useRouter();
  const isPublic = useHealth().health?.public ?? false;

  useEffect(() => {
    api.listReviews().then(setRows, (e) => setError(friendlyError(e)));
  }, []);

  const total = rows?.reduce((sum, r) => sum + r.cost_usd, 0) ?? 0;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">History</h1>
          <p className="text-sm text-muted">
            {isPublic
              ? "Your demo reviews, newest first. Only this browser can see them, and they don't last: a day at most."
              : "Every review this server has run, newest first."}
          </p>
        </div>
        {rows && rows.length > 0 && (
          <p className="text-sm text-muted">
            {rows.length} review{rows.length === 1 ? "" : "s"} · <span className="tabular-nums text-fg">{formatCost(total)}</span> total
          </p>
        )}
      </div>

      {error && <ErrorBox title={error.title} detail={error.detail} />}

      {!error && (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm" data-testid="history-table">
              <thead>
                <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-faint">
                  <th className="px-4 py-2.5 font-medium">Pull request</th>
                  <th className="px-4 py-2.5 font-medium">Status</th>
                  <th className="px-4 py-2.5 font-medium">Findings</th>
                  <th className="px-4 py-2.5 text-right font-medium">Cost</th>
                  <th className="px-4 py-2.5 text-right font-medium">Date</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows === null &&
                  [0, 1, 2, 3].map((i) => (
                    <tr key={i}>
                      <td className="px-4 py-3" colSpan={5}>
                        <Skeleton className="h-4 w-full" />
                      </td>
                    </tr>
                  ))}
                {rows?.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-4 py-10 text-center text-muted">
                      No reviews yet.{" "}
                      <Link href="/" className="text-accent-text hover:underline">
                        Start one
                      </Link>
                    </td>
                  </tr>
                )}
                {rows?.map((r) => (
                  <tr
                    key={r.review_id}
                    className="cursor-pointer transition-colors hover:bg-surface-2"
                    onClick={() => router.push(`/reviews/${r.review_id}`)}
                  >
                    <td className="max-w-[18rem] px-4 py-3">
                      <Link href={`/reviews/${r.review_id}`} className="block truncate font-medium hover:underline" onClick={(e) => e.stopPropagation()}>
                        {r.title || `${r.repo}#${r.pr}`}
                      </Link>
                      <span className="block truncate font-mono text-xs text-faint">
                        {r.repo}#{r.pr} @ {r.sha.slice(0, 7)}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <StatusPill status={r.status} />
                      {r.error && (
                        <span className="mt-1 block max-w-[14rem] truncate text-xs text-critical" title={r.error}>
                          {r.error}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <SeverityCountsRow counts={r.counts} />
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-muted">{formatCost(r.cost_usd)}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-muted" title={new Date(r.created_at).toLocaleString()}>
                      {formatDate(r.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
