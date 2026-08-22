"use client";

import type { EvidencePack } from "@/lib/types";
import { GapCard } from "./GapView";
import { MappingCard } from "./MappingView";
import { EmptyState, HumanReviewBanner, SectionLabel } from "./ui";

function CoverageSummary({ summary }: { summary: Record<string, number> }) {
  const order: Array<[string, string]> = [
    ["full", "text-emerald-700 bg-emerald-50 ring-emerald-200"],
    ["partial", "text-amber-700 bg-amber-50 ring-amber-200"],
    ["none", "text-rose-700 bg-rose-50 ring-rose-200"],
  ];
  return (
    <div className="flex flex-wrap gap-2">
      {order.map(([key, style]) => (
        <div
          key={key}
          className={`flex items-center gap-2 rounded-lg px-3 py-2 ring-1 ring-inset ${style}`}
        >
          <span className="text-xl font-bold tabular-nums">
            {summary[key] ?? 0}
          </span>
          <span className="text-[11px] font-semibold uppercase tracking-wide">
            {key}
          </span>
        </div>
      ))}
    </div>
  );
}

export function EvidencePackView({ pack }: { pack: EvidencePack | null }) {
  if (!pack) {
    return (
      <EmptyState
        title="No evidence pack yet"
        hint="Build an audit-ready pack for a regulator or use-case scope. It bundles the mappings, gaps and coverage summary an auditor expects."
      />
    );
  }
  return (
    <div className="space-y-4">
      <HumanReviewBanner reason="An evidence pack is the regulator deliverable and always requires a checker's sign-off before it leaves the bank." />

      <div className="rounded-xl border border-ink-200 bg-white p-4 shadow-sm">
        <div className="flex items-center justify-between">
          <div>
            <SectionLabel>Evidence pack</SectionLabel>
            <div className="font-mono text-sm text-ink-800">{pack.scope}</div>
          </div>
          <div className="text-[11px] text-ink-400">
            generated {new Date(pack.generated_at).toLocaleString()}
          </div>
        </div>
        <div className="mt-3">
          <CoverageSummary summary={pack.coverage_summary} />
        </div>
      </div>

      <div>
        <SectionLabel>Mappings ({pack.mappings.length})</SectionLabel>
        <div className="mt-2 space-y-3">
          {pack.mappings.map((m) => (
            <MappingCard key={m.requirement.id} mapping={m} />
          ))}
        </div>
      </div>

      {pack.gaps.length > 0 && (
        <div>
          <SectionLabel>Gaps ({pack.gaps.length})</SectionLabel>
          <div className="mt-2 space-y-3">
            {pack.gaps.map((g) => (
              <GapCard key={g.requirement.id} gap={g} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
