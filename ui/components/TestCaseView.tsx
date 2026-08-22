"use client";

import { useState } from "react";
import type { TestCase } from "@/lib/types";
import { CitationList } from "./CitationCard";
import { EmptyState, HumanReviewBanner } from "./ui";

export function TestCaseView({ testcases }: { testcases: TestCase[] | null }) {
  if (!testcases) {
    return (
      <EmptyState
        title="No test cases yet"
        hint="Generate automated test cases that verify each control for a given use case."
      />
    );
  }
  if (testcases.length === 0) {
    return <EmptyState title="The backend returned no test cases for this use case." />;
  }

  return (
    <div className="space-y-4">
      <HumanReviewBanner reason="Test cases are consequential, automatable artifacts — a checker must approve before they are wired into a control-assurance pipeline." />

      <div className="flex items-baseline justify-between">
        <div className="text-xs font-semibold uppercase tracking-wide text-ink-500">
          Verification test cases
        </div>
        <span className="rounded-full bg-ink-100 px-2 py-0.5 text-xs font-medium text-ink-600">
          {testcases.length} cases
        </span>
      </div>

      <div className="space-y-3">
        {testcases.map((tc, i) => (
          <TestCaseCard key={`${tc.id}-${i}`} tc={tc} />
        ))}
      </div>
    </div>
  );
}

function TestCaseCard({ tc }: { tc: TestCase }) {
  const [showCheck, setShowCheck] = useState(false);
  return (
    <div className="overflow-hidden rounded-lg border border-ink-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center gap-2 border-b border-ink-100 bg-ink-50/60 px-3.5 py-2.5">
        <span className="font-mono text-xs font-bold text-regblue-700">
          {tc.id}
        </span>
        <span className="text-sm font-semibold text-ink-900">{tc.title}</span>
        <span className="ml-auto inline-flex items-center gap-1 rounded-md bg-ink-100 px-1.5 py-0.5 text-[11px] font-medium text-ink-600 ring-1 ring-inset ring-ink-200">
          <span className="text-ink-400">verifies</span>
          <span className="font-mono">{tc.control_id}</span>
        </span>
      </div>

      <div className="space-y-3 p-3.5">
        <div>
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-500">
            Steps
          </div>
          <ol className="space-y-1">
            {tc.steps.map((step, i) => (
              <li key={i} className="flex gap-2 text-sm leading-snug text-ink-700">
                <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-ink-100 text-[10px] font-bold text-ink-500">
                  {i + 1}
                </span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
        </div>

        <div>
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-500">
            Expected result
          </div>
          <p className="rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-1.5 text-sm leading-snug text-emerald-900">
            {tc.expected_result}
          </p>
        </div>

        {tc.automated_check && (
          <div>
            <button
              type="button"
              onClick={() => setShowCheck((v) => !v)}
              className="flex items-center gap-1.5 text-xs font-medium text-regblue-700 hover:text-regblue-800"
            >
              <svg
                viewBox="0 0 20 20"
                className={`h-3.5 w-3.5 transition-transform ${showCheck ? "rotate-90" : ""}`}
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                aria-hidden="true"
              >
                <path d="M7 5l5 5-5 5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              {showCheck ? "Hide" : "Show"} automated check
            </button>
            {showCheck && (
              <pre className="scroll-thin mt-2 overflow-x-auto rounded-md border border-ink-800 bg-ink-950 px-3 py-2.5 text-xs leading-relaxed text-ink-100">
                <code>{tc.automated_check}</code>
              </pre>
            )}
          </div>
        )}

        {tc.citations && tc.citations.length > 0 && (
          <CitationList citations={tc.citations} title="Backing controls" />
        )}
      </div>
    </div>
  );
}
