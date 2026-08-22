"use client";

import type { ControlMapping } from "@/lib/types";
import { FAMILY_LABEL } from "@/lib/types";
import { CitationList } from "./CitationCard";
import { CoverageBadge, EmptyState, HumanReviewBanner, StateBadge } from "./ui";

function ObservationRow({
  controlName,
  family,
  state,
  detail,
}: {
  controlName: string;
  family: string;
  state: string;
  detail: string;
}) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-ink-200 bg-white px-2.5 py-1.5">
      <StateBadge state={state} />
      <div className="min-w-0">
        <div className="text-xs font-medium text-ink-800">
          {controlName}{" "}
          <span className="text-ink-400">
            ({FAMILY_LABEL[family as keyof typeof FAMILY_LABEL] ?? family})
          </span>
        </div>
        {detail && <div className="text-[11px] text-ink-500">{detail}</div>}
      </div>
    </div>
  );
}

export function MappingCard({ mapping }: { mapping: ControlMapping }) {
  const obsByControl = new Map(
    mapping.observations.map((o) => [o.control_id, o]),
  );
  return (
    <article className="rounded-xl border border-ink-200 bg-white p-4 shadow-sm">
      <header className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-ink-400">
          {mapping.requirement.id}
        </span>
        <CoverageBadge coverage={mapping.coverage} />
        {mapping.requires_human_review && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-800">
            review
          </span>
        )}
      </header>

      <h3 className="mt-1 text-sm font-semibold text-ink-900">
        {mapping.requirement.title}
      </h3>
      <p className="mt-0.5 text-xs leading-relaxed text-ink-600">
        {mapping.requirement.text}
      </p>

      {mapping.rationale && (
        <p className="mt-2 rounded-md bg-ink-50 px-2.5 py-1.5 text-xs text-ink-700">
          {mapping.rationale}
        </p>
      )}

      {mapping.controls.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {mapping.controls.map((c) => {
            const obs = obsByControl.get(c.id);
            return (
              <ObservationRow
                key={c.id}
                controlName={c.name}
                family={c.family}
                state={obs?.state ?? "unknown"}
                detail={obs?.detail ?? ""}
              />
            );
          })}
        </div>
      )}

      <div className="mt-3">
        <CitationList citations={mapping.citations} title="Requirement source" />
      </div>
    </article>
  );
}

export function MappingView({
  mappings,
}: {
  mappings: ControlMapping[] | null;
}) {
  if (!mappings) {
    return (
      <EmptyState
        title="No control mapping yet"
        hint="Enter a scope (a regulator, a notice reference or a use case) to map its requirements to GCP controls with a coverage verdict for each."
      />
    );
  }
  if (mappings.length === 0) {
    return <EmptyState title="The backend returned no mappings for this scope." />;
  }
  const anyReview = mappings.some((m) => m.requires_human_review);
  return (
    <div className="space-y-3">
      {anyReview && (
        <HumanReviewBanner reason="One or more mappings have PARTIAL or NONE coverage and require a checker's sign-off." />
      )}
      {mappings.map((m) => (
        <MappingCard key={m.requirement.id} mapping={m} />
      ))}
    </div>
  );
}
