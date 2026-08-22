"use client";

import type { ControlGap } from "@/lib/types";
import { FAMILY_LABEL } from "@/lib/types";
import { CitationList } from "./CitationCard";
import { EmptyState, SeverityBadge } from "./ui";

export function GapCard({ gap }: { gap: ControlGap }) {
  return (
    <article className="rounded-xl border border-ink-200 bg-white p-4 shadow-sm">
      <header className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-ink-400">
          {gap.requirement.id}
        </span>
        <SeverityBadge severity={gap.severity} />
      </header>

      <h3 className="mt-1 text-sm font-semibold text-ink-900">
        {gap.requirement.title}
      </h3>

      <div className="mt-2 flex flex-wrap gap-1.5">
        {gap.missing_controls.map((f) => (
          <span
            key={f}
            className="inline-flex items-center rounded-md bg-rose-50 px-1.5 py-0.5 text-[11px] font-medium text-rose-700 ring-1 ring-inset ring-rose-200"
          >
            missing: {FAMILY_LABEL[f] ?? f}
          </span>
        ))}
      </div>

      <p className="mt-2 rounded-md bg-ink-50 px-2.5 py-1.5 text-xs text-ink-700">
        <span className="font-semibold text-ink-800">Remediation: </span>
        {gap.remediation}
      </p>

      <div className="mt-3">
        <CitationList citations={gap.citations} title="Requirement source" />
      </div>
    </article>
  );
}

export function GapView({ gaps }: { gaps: ControlGap[] | null }) {
  if (!gaps) {
    return (
      <EmptyState
        title="No gap analysis yet"
        hint="Run a scope to list the requirements it does not fully cover, each with a severity and a remediation."
      />
    );
  }
  if (gaps.length === 0) {
    return (
      <EmptyState
        title="No gaps"
        hint="Every requirement in this scope is fully covered."
      />
    );
  }
  return (
    <div className="space-y-3">
      {gaps.map((g) => (
        <GapCard key={g.requirement.id} gap={g} />
      ))}
    </div>
  );
}
