"use client";

import { useState } from "react";
import type { RegulatorQuestion, Regulator } from "@/lib/types";
import { REGULATOR_LABEL } from "@/lib/types";
import { CitationList } from "./CitationCard";
import { EmptyState } from "./ui";

const REGULATOR_DOT: Record<Regulator, string> = {
  MAS: "bg-emerald-500",
  HKMA: "bg-rose-500",
  APRA: "bg-amber-500",
  FSA: "bg-violet-500",
  CROSS: "bg-regblue-500",
};

export function RegulatorQuestionsView({
  questions,
}: {
  questions: RegulatorQuestion[] | null;
}) {
  if (!questions) {
    return (
      <EmptyState
        title="No regulator questions yet"
        hint="Generate the exact questions a regulator or CRO is likely to ask for a given use case, each with a model answer."
      />
    );
  }
  if (questions.length === 0) {
    return <EmptyState title="The backend returned no regulator questions." />;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <div className="text-xs font-semibold uppercase tracking-wide text-ink-500">
          Anticipated regulator / CRO questions
        </div>
        <span className="rounded-full bg-ink-100 px-2 py-0.5 text-xs font-medium text-ink-600">
          {questions.length} questions
        </span>
      </div>
      <div className="space-y-3">
        {questions.map((q, i) => (
          <QuestionCard key={i} q={q} index={i} />
        ))}
      </div>
    </div>
  );
}

function QuestionCard({ q, index }: { q: RegulatorQuestion; index: number }) {
  const [open, setOpen] = useState(index === 0);
  const dot = REGULATOR_DOT[q.regulator] ?? REGULATOR_DOT.CROSS;

  return (
    <div className="overflow-hidden rounded-lg border border-ink-200 bg-white shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-3 px-3.5 py-3 text-left hover:bg-ink-50/60"
      >
        <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${dot}`} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              title={REGULATOR_LABEL[q.regulator] ?? q.regulator}
              className="text-[11px] font-semibold uppercase tracking-wide text-ink-500"
            >
              {q.regulator}
            </span>
          </div>
          <p className="mt-0.5 text-sm font-medium leading-snug text-ink-900">
            {q.question}
          </p>
        </div>
        <svg
          viewBox="0 0 20 20"
          className={`mt-1 h-4 w-4 shrink-0 text-ink-400 transition-transform ${open ? "rotate-90" : ""}`}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          aria-hidden="true"
        >
          <path d="M7 5l5 5-5 5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div className="space-y-3 border-t border-ink-100 px-3.5 py-3">
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-500">
              Why it&rsquo;s asked
            </div>
            <p className="text-xs leading-relaxed text-ink-600">{q.why_asked}</p>
          </div>
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-500">
              Model answer
            </div>
            <p className="rounded-md border border-regblue-200 bg-regblue-50 px-2.5 py-2 text-sm leading-relaxed text-ink-800">
              {q.model_answer}
            </p>
          </div>
          {q.citations && q.citations.length > 0 && (
            <CitationList citations={q.citations} title="Sources" />
          )}
        </div>
      )}
    </div>
  );
}
