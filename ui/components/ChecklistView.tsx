"use client";

import { useState } from "react";
import type { ControlChecklist } from "@/lib/types";
import { CitationList } from "./CitationCard";
import { HumanReviewBanner, SeverityBadge, EmptyState } from "./ui";

export function ChecklistView({
  checklist,
}: {
  checklist: ControlChecklist | null;
}) {
  if (!checklist) {
    return (
      <EmptyState
        title="No control checklist yet"
        hint="Generate a use-case control checklist from the chat input above (e.g. “cloud outsourcing to a hyperscaler”)."
      />
    );
  }

  return (
    <div className="space-y-4">
      {checklist.requires_human_review && (
        <HumanReviewBanner reason="Control checklists are consequential outputs — a checker must approve before use." />
      )}

      <div className="flex items-baseline justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-ink-500">
            Control checklist
          </div>
          <h3 className="text-base font-semibold text-ink-900">
            {checklist.use_case}
          </h3>
        </div>
        <span className="rounded-full bg-ink-100 px-2 py-0.5 text-xs font-medium text-ink-600">
          {checklist.items.length} controls
        </span>
      </div>

      <ol className="space-y-2.5">
        {checklist.items.map((item, i) => (
          <ChecklistRow key={`${item.control_id}-${i}`} item={item} index={i} />
        ))}
      </ol>
    </div>
  );
}

function ChecklistRow({
  item,
  index,
}: {
  item: ControlChecklist["items"][number];
  index: number;
}) {
  const [open, setOpen] = useState(false);
  const hasCitations = item.citations && item.citations.length > 0;

  return (
    <li className="overflow-hidden rounded-lg border border-ink-200 bg-white shadow-sm">
      <div className="flex items-start gap-3 p-3.5">
        <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-regblue-50 text-xs font-bold text-regblue-700 ring-1 ring-inset ring-regblue-200">
          {index + 1}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs font-semibold text-ink-500">
              {item.control_id}
            </span>
            <SeverityBadge severity={item.severity} />
          </div>
          <p className="mt-1 text-sm font-medium leading-snug text-ink-900">
            {item.control}
          </p>
          <p className="mt-1 text-xs leading-relaxed text-ink-600">
            {item.rationale}
          </p>
        </div>
      </div>

      {hasCitations && (
        <div className="border-t border-ink-100 bg-ink-50/50 px-3.5 py-2">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="flex items-center gap-1.5 text-xs font-medium text-regblue-700 hover:text-regblue-800"
          >
            <svg
              viewBox="0 0 20 20"
              className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-90" : ""}`}
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              aria-hidden="true"
            >
              <path d="M7 5l5 5-5 5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {open ? "Hide" : "Show"} {item.citations.length} citation
            {item.citations.length === 1 ? "" : "s"}
          </button>
          {open && (
            <div className="mt-2.5">
              <CitationList citations={item.citations} title="Sources" />
            </div>
          )}
        </div>
      )}
    </li>
  );
}
