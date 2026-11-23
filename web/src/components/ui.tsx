// Small shared pieces: badges, status pills, buttons, error and skeleton blocks, formatters.
import type { ReactNode } from "react";
import type { ReviewStatus, SeverityCounts } from "@/lib/api";

export const SEVERITIES = ["critical", "major", "minor", "nit"] as const;

const SEVERITY_CLASS: Record<string, string> = {
  critical: "bg-critical-soft text-critical border-critical/25",
  major: "bg-major-soft text-major border-major/25",
  minor: "bg-minor-soft text-minor border-minor/20",
  nit: "bg-transparent text-faint border-line-strong",
};

export function SeverityBadge({ severity }: { severity: string | null | undefined }) {
  const s = severity ?? "minor";
  return (
    <span
      className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${
        SEVERITY_CLASS[s] ?? SEVERITY_CLASS.minor
      }`}
    >
      {s}
    </span>
  );
}

export function SeverityCountsRow({ counts }: { counts: SeverityCounts | null | undefined }) {
  if (!counts) return <span className="text-faint">–</span>;
  const shown = SEVERITIES.filter((s) => (counts[s] ?? 0) > 0);
  if (!shown.length) return <span className="text-faint">none</span>;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {shown.map((s) => (
        <span key={s} className={`rounded-md border px-1.5 py-0.5 text-[11px] font-medium tabular-nums ${SEVERITY_CLASS[s]}`}>
          {counts[s]} {s}
        </span>
      ))}
    </span>
  );
}

const STATUS: Record<ReviewStatus, { label: string; cls: string; dot?: boolean }> = {
  running: { label: "Reviewing", cls: "bg-accent-soft text-accent-text", dot: true },
  awaiting_approval: { label: "Needs approval", cls: "bg-accent-soft text-accent-text" },
  posting: { label: "Posting", cls: "bg-accent-soft text-accent-text", dot: true },
  posted: { label: "Posted", cls: "bg-ok-soft text-ok" },
  failed: { label: "Failed", cls: "bg-critical-soft text-critical" },
};

export function StatusPill({ status }: { status: ReviewStatus }) {
  const s = STATUS[status] ?? STATUS.running;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${s.cls}`}>
      {s.dot && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
      {s.label}
    </span>
  );
}

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
};

export function Button({ variant = "secondary", size = "md", className = "", ...props }: ButtonProps) {
  const base =
    "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50";
  const sizes = { sm: "h-7 px-2.5 text-xs", md: "h-9 px-3.5 text-sm" };
  const variants = {
    primary: "bg-accent text-accent-fg hover:opacity-90",
    secondary: "border border-line-strong bg-surface text-fg hover:bg-surface-2",
    ghost: "text-muted hover:bg-surface-2 hover:text-fg",
    danger: "border border-critical/30 bg-critical-soft text-critical hover:opacity-90",
  };
  return <button className={`${base} ${sizes[size]} ${variants[variant]} ${className}`} {...props} />;
}

export function Card({ children, className = "", ...rest }: React.HTMLAttributes<HTMLDivElement> & { "data-testid"?: string }) {
  return (
    <div className={`rounded-xl border border-line bg-surface ${className}`} {...rest}>
      {children}
    </div>
  );
}

export function ErrorBox({ title, detail, action }: { title: string; detail?: string; action?: ReactNode }) {
  return (
    <div role="alert" className="rounded-xl border border-critical/30 bg-critical-soft p-4 text-sm">
      <p className="font-semibold text-critical">{title}</p>
      {detail && <p className="mt-1 break-words text-fg/80">{detail}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />;
}

export function Spinner({ className = "h-3.5 w-3.5" }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function formatCost(usd: number | null | undefined): string {
  if (usd == null) return "–";
  if (usd === 0) return "$0";
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

export function formatTokens(n: number | null | undefined): string {
  if (n == null) return "–";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

export function formatSeconds(s: number | null | undefined): string {
  if (s == null) return "–";
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

export function formatDate(iso: string): string {
  const date = new Date(iso);
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function shortSha(sha: string): string {
  return sha.slice(0, 7);
}
