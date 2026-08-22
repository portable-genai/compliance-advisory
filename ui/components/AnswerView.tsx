"use client";

import { useEffect, useState } from "react";
import type { Answer } from "@/lib/types";
import { CitationList, WebCitationList } from "./CitationCard";
import { ConfidenceMeter, EmptyState, HumanReviewBanner } from "./ui";

/**
 * Renders a grounded Answer. The body is revealed with a lightweight
 * "streaming-ish" typewriter so the console feels responsive even though the
 * backend returns the answer as a single structured payload.
 */
export function AnswerView({
  answer,
  streaming = true,
}: {
  answer: Answer | null;
  streaming?: boolean;
}) {
  const shown = useTypewriter(answer?.answer ?? "", streaming);

  if (!answer) {
    return (
      <EmptyState
        title="Ask a grounded compliance question"
        hint="e.g. “What are MAS expectations for material cloud outsourcing to a hyperscaler?” Answers come back with page-level citations."
      />
    );
  }

  const done = shown.length >= (answer.answer?.length ?? 0);

  return (
    <div className="space-y-4">
      {answer.requires_human_review && (
        <HumanReviewBanner reason="Low-confidence or high-severity answer — flagged for checker sign-off (maker–checker, P-06)." />
      )}

      <div className="rounded-lg border border-ink-200 bg-white p-4 shadow-sm">
        <div className="mb-2 flex items-center justify-between gap-3">
          <span className="text-xs font-semibold uppercase tracking-wide text-ink-500">
            Grounded answer
          </span>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-ink-400">confidence</span>
            <ConfidenceMeter value={answer.confidence} />
          </div>
        </div>
        <p className="whitespace-pre-wrap text-[15px] leading-relaxed text-ink-800">
          {shown}
          {streaming && !done && <span className="caret">&nbsp;</span>}
        </p>
      </div>

      {answer.caveats && answer.caveats.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-3">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-amber-700">
            Caveats
          </div>
          <ul className="list-disc space-y-1 pl-5 text-sm text-amber-900">
            {answer.caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}

      <CitationList citations={answer.citations} />
      <WebCitationList items={answer.web_citations} />
    </div>
  );
}

function useTypewriter(full: string, enabled: boolean): string {
  const [shown, setShown] = useState(enabled ? "" : full);

  useEffect(() => {
    if (!enabled) {
      setShown(full);
      return;
    }
    if (!full) {
      setShown("");
      return;
    }
    setShown("");
    let i = 0;
    // Reveal in chunks for a smooth but quick stream.
    const step = Math.max(2, Math.round(full.length / 240));
    const id = window.setInterval(() => {
      i = Math.min(full.length, i + step);
      setShown(full.slice(0, i));
      if (i >= full.length) window.clearInterval(id);
    }, 16);
    return () => window.clearInterval(id);
  }, [full, enabled]);

  return shown;
}
