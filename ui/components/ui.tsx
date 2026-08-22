"use client";

import type { Coverage, Severity } from "@/lib/types";

/**
 * Prominent maker-checker banner (General Principle P-06). Rendered whenever an
 * artifact carries `requires_human_review`. Consequential outputs (checklists,
 * test cases) and low-confidence / high-severity answers are gated here before a
 * human "checker" signs off.
 */
export function HumanReviewBanner({
  reason,
  compact = false,
}: {
  reason?: string;
  compact?: boolean;
}) {
  return (
    <div
      role="status"
      className={`flex items-start gap-3 rounded-lg border-l-4 border-amber-500 bg-amber-50 ${
        compact ? "p-2.5" : "p-3.5"
      } text-amber-900 ring-1 ring-inset ring-amber-200`}
    >
      <svg
        viewBox="0 0 24 24"
        aria-hidden="true"
        className="mt-0.5 h-5 w-5 shrink-0 text-amber-600"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
      >
        <path
          d="M12 9v4m0 4h.01M10.3 3.86 1.82 18a2 2 0 0 0 1.7 3h16.96a2 2 0 0 0 1.7-3L13.7 3.86a2 2 0 0 0-3.42 0Z"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold">Human review required</span>
          <span className="rounded-full bg-amber-200/70 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-amber-800">
            Maker–Checker
          </span>
        </div>
        <p className="mt-0.5 text-xs leading-relaxed text-amber-800">
          {reason ??
            "This artifact is gated for sign-off by a second reviewer (checker) before it can be relied upon."}
        </p>
      </div>
    </div>
  );
}

const SEVERITY_STYLES: Record<Severity, string> = {
  low: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  medium: "bg-amber-50 text-amber-700 ring-amber-200",
  high: "bg-orange-50 text-orange-700 ring-orange-200",
  critical: "bg-rose-50 text-rose-700 ring-rose-200",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  const style = SEVERITY_STYLES[severity] ?? SEVERITY_STYLES.medium;
  return (
    <span
      className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ring-1 ring-inset ${style}`}
    >
      {severity}
    </span>
  );
}

const COVERAGE_STYLES: Record<Coverage, string> = {
  full: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  partial: "bg-amber-50 text-amber-700 ring-amber-200",
  none: "bg-rose-50 text-rose-700 ring-rose-200",
};

export function CoverageBadge({ coverage }: { coverage: Coverage }) {
  const style = COVERAGE_STYLES[coverage] ?? COVERAGE_STYLES.none;
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide ring-1 ring-inset ${style}`}
    >
      {coverage}
    </span>
  );
}

const STATE_STYLES: Record<string, string> = {
  enabled: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  partial: "bg-amber-50 text-amber-700 ring-amber-200",
  disabled: "bg-rose-50 text-rose-700 ring-rose-200",
  misconfigured: "bg-orange-50 text-orange-700 ring-orange-200",
  unknown: "bg-ink-100 text-ink-600 ring-ink-200",
};

export function StateBadge({ state }: { state: string }) {
  const style = STATE_STYLES[state] ?? STATE_STYLES.unknown;
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ring-inset ${style}`}
    >
      {state}
    </span>
  );
}

export function ConfidenceMeter({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const tone =
    pct >= 75
      ? "bg-emerald-500"
      : pct >= 50
        ? "bg-amber-500"
        : "bg-rose-500";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-ink-200">
        <div
          className={`h-full rounded-full ${tone}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs font-semibold tabular-nums text-ink-700">
        {pct.toFixed(0)}%
      </span>
    </div>
  );
}

export function EmptyState({
  title,
  hint,
}: {
  title: string;
  hint?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-ink-200 bg-white/60 px-6 py-12 text-center">
      <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-ink-100 text-ink-400">
        <svg
          viewBox="0 0 24 24"
          aria-hidden="true"
          className="h-5 w-5"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
        >
          <path
            d="M9 12h6m-6 4h6M9 8h6M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
      <p className="text-sm font-medium text-ink-700">{title}</p>
      {hint && <p className="mt-1 max-w-xs text-xs text-ink-400">{hint}</p>}
    </div>
  );
}

export function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-xs font-semibold uppercase tracking-wide text-ink-500">
      {children}
    </div>
  );
}
