"use client";

import type { Citation, Regulator, WebCitation } from "@/lib/types";
import { REGULATOR_LABEL } from "@/lib/types";

const REGULATOR_BADGE: Record<Regulator, string> = {
  MAS: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  HKMA: "bg-rose-50 text-rose-700 ring-rose-200",
  APRA: "bg-amber-50 text-amber-700 ring-amber-200",
  FSA: "bg-violet-50 text-violet-700 ring-violet-200",
  CROSS: "bg-regblue-50 text-regblue-700 ring-regblue-200",
};

function ExternalLinkIcon() {
  return (
    <svg
      viewBox="0 0 20 20"
      aria-hidden="true"
      className="h-3.5 w-3.5"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
    >
      <path
        d="M11 3h6v6M17 3l-8 8M14 12v4a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function CitationCard({ citation }: { citation: Citation }) {
  const badge = REGULATOR_BADGE[citation.regulator] ?? REGULATOR_BADGE.CROSS;
  const fullName = REGULATOR_LABEL[citation.regulator] ?? citation.regulator;

  return (
    <div className="group rounded-lg border border-ink-200 bg-white p-3 shadow-sm transition hover:border-regblue-300 hover:shadow-panel">
      <div className="flex items-center gap-2">
        <span
          title={fullName}
          className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ring-1 ring-inset ${badge}`}
        >
          {citation.regulator}
        </span>
        <span className="inline-flex items-center rounded-md bg-ink-100 px-1.5 py-0.5 text-[11px] font-medium text-ink-600 ring-1 ring-inset ring-ink-200">
          {citation.jurisdiction}
        </span>
        {typeof citation.score === "number" && (
          <span className="ml-auto text-[11px] tabular-nums text-ink-400">
            score {citation.score.toFixed(2)}
          </span>
        )}
      </div>

      <a
        href={citation.url || "#"}
        target="_blank"
        rel="noreferrer noopener"
        className="mt-2 flex items-start gap-1.5 text-sm font-medium leading-snug text-ink-900 hover:text-regblue-700"
      >
        <span className="line-clamp-2">{citation.title}</span>
        {citation.url && (
          <span className="mt-0.5 shrink-0 text-ink-400 group-hover:text-regblue-600">
            <ExternalLinkIcon />
          </span>
        )}
      </a>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-500">
        <span className="font-mono">
          v{citation.version || "unknown"}
        </span>
        {citation.page != null && (
          <span className="inline-flex items-center gap-1">
            <span className="text-ink-400">page</span>
            <span className="rounded bg-ink-100 px-1 font-mono font-semibold text-ink-700">
              {citation.page}
            </span>
          </span>
        )}
        <span className="font-mono text-ink-400">{citation.source_id}</span>
      </div>

      {citation.snippet && (
        <p className="mt-2 border-l-2 border-ink-200 pl-2 text-xs italic leading-relaxed text-ink-500 line-clamp-3">
          &ldquo;{citation.snippet}&rdquo;
        </p>
      )}
    </div>
  );
}

export function CitationList({
  citations,
  title = "Citations",
}: {
  citations: Citation[];
  title?: string;
}) {
  if (!citations || citations.length === 0) return null;
  return (
    <div>
      <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-500">
        <span>{title}</span>
        <span className="rounded-full bg-ink-100 px-1.5 text-[11px] font-medium text-ink-600">
          {citations.length}
        </span>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {citations.map((c, i) => (
          <CitationCard key={`${c.source_id}-${c.page ?? "x"}-${i}`} citation={c} />
        ))}
      </div>
    </div>
  );
}

export function WebCitationList({ items }: { items: WebCitation[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div>
      <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-500">
        <span>Web grounding</span>
        <span className="rounded-full bg-regblue-50 px-1.5 text-[11px] font-medium text-regblue-700 ring-1 ring-inset ring-regblue-200">
          secondary
        </span>
      </div>
      <ul className="space-y-1.5">
        {items.map((w, i) => (
          <li key={`${w.url}-${i}`}>
            <a
              href={w.url || "#"}
              target="_blank"
              rel="noreferrer noopener"
              className="flex items-start gap-1.5 rounded-md border border-ink-200 bg-white px-2.5 py-1.5 text-sm text-ink-700 hover:border-regblue-300 hover:text-regblue-700"
            >
              <ExternalLinkIcon />
              <span className="line-clamp-1">{w.title || w.url}</span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
