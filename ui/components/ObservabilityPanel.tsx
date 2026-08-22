"use client";

import type {
  GuardrailVerdict,
  Observability,
  EvalReport,
} from "@/lib/types";
import { ConfidenceMeter, HumanReviewBanner, SectionLabel } from "./ui";

/**
 * Right-rail governance panel. Surfaces the A1 guardrail verdict (input/output),
 * the A4 eval/confidence signal, PII redaction tallies, and the maker-checker
 * banner when the active artifact requires human review.
 */
export function ObservabilityPanel({
  observability,
  requiresHumanReview,
  confidence,
}: {
  observability: Observability | null;
  requiresHumanReview: boolean;
  confidence: number | null;
}) {
  const obs = observability ?? {};
  const decision = obs.decision;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-ink-200 px-4 py-3">
        <h2 className="text-sm font-semibold text-ink-900">Observability &amp; Governance</h2>
        <p className="mt-0.5 text-[11px] text-ink-400">
          A1 guardrail · A4 eval · A5 audit
        </p>
      </div>

      <div className="scroll-thin flex-1 space-y-4 overflow-y-auto p-4">
        {requiresHumanReview && (
          <HumanReviewBanner compact />
        )}

        {/* Decision pill */}
        <div className="flex items-center justify-between">
          <SectionLabel>Decision</SectionLabel>
          <DecisionPill decision={decision ?? (requiresHumanReview ? "escalated" : undefined)} />
        </div>

        {/* Confidence */}
        {confidence != null && (
          <div className="rounded-lg border border-ink-200 bg-white p-3">
            <SectionLabel>Eval / confidence</SectionLabel>
            <div className="mt-2">
              <ConfidenceMeter value={confidence} />
            </div>
          </div>
        )}

        {/* Eval report metrics */}
        {obs.eval && <EvalBlock report={obs.eval} />}

        {/* Guardrail verdicts */}
        <GuardrailBlock label="Guardrail — input" verdict={obs.guardrail_input} />
        <GuardrailBlock label="Guardrail — output" verdict={obs.guardrail_output} />

        {/* Redactions */}
        {obs.redactions && obs.redactions.length > 0 && (
          <div className="rounded-lg border border-ink-200 bg-white p-3">
            <SectionLabel>PII redaction (DLP)</SectionLabel>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {obs.redactions.map((r, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-1 rounded-md bg-ink-100 px-1.5 py-0.5 text-[11px] font-medium text-ink-700 ring-1 ring-inset ring-ink-200"
                >
                  <span className="font-mono">{r.info_type}</span>
                  <span className="text-ink-400">×{r.count}</span>
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Trace id */}
        {obs.trace_id && (
          <div className="rounded-lg border border-ink-200 bg-white p-3">
            <SectionLabel>Trace (Cloud Trace)</SectionLabel>
            <p className="mt-1 break-all font-mono text-[11px] text-ink-500">
              {obs.trace_id}
            </p>
          </div>
        )}

        {!observability && !requiresHumanReview && confidence == null && (
          <p className="rounded-lg border border-dashed border-ink-200 bg-white/50 p-3 text-xs leading-relaxed text-ink-400">
            Governance signals (guardrail verdict, eval score, redactions, trace)
            appear here once a request returns. If the backend does not attach an
            observability envelope, this panel stays minimal.
          </p>
        )}
      </div>
    </div>
  );
}

function DecisionPill({
  decision,
}: {
  decision?: "allowed" | "blocked" | "escalated";
}) {
  if (!decision) {
    return <span className="text-xs text-ink-400">—</span>;
  }
  const styles: Record<string, string> = {
    allowed: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    blocked: "bg-rose-50 text-rose-700 ring-rose-200",
    escalated: "bg-amber-50 text-amber-700 ring-amber-200",
  };
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ring-1 ring-inset ${styles[decision]}`}
    >
      {decision}
    </span>
  );
}

function EvalBlock({ report }: { report: EvalReport }) {
  const allPassed =
    report.passed ?? report.results.every((r) => r.passed);
  return (
    <div className="rounded-lg border border-ink-200 bg-white p-3">
      <div className="flex items-center justify-between">
        <SectionLabel>Eval gate (A4)</SectionLabel>
        <span
          className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-semibold uppercase ring-1 ring-inset ${
            allPassed
              ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
              : "bg-rose-50 text-rose-700 ring-rose-200"
          }`}
        >
          {allPassed ? "pass" : "fail"}
        </span>
      </div>
      <div className="mt-2 space-y-1.5">
        {report.results.map((m, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="w-32 truncate text-xs text-ink-600">{m.metric}</span>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-200">
              <div
                className={`h-full rounded-full ${m.passed ? "bg-emerald-500" : "bg-rose-500"}`}
                style={{ width: `${Math.max(0, Math.min(1, m.score)) * 100}%` }}
              />
            </div>
            <span className="w-9 text-right text-[11px] tabular-nums text-ink-500">
              {m.score.toFixed(2)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function GuardrailBlock({
  label,
  verdict,
}: {
  label: string;
  verdict?: GuardrailVerdict | null;
}) {
  if (!verdict) return null;
  return (
    <div className="rounded-lg border border-ink-200 bg-white p-3">
      <div className="flex items-center justify-between">
        <SectionLabel>{label}</SectionLabel>
        <span
          className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-semibold uppercase ring-1 ring-inset ${
            verdict.allowed
              ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
              : "bg-rose-50 text-rose-700 ring-rose-200"
          }`}
        >
          {verdict.allowed ? "allowed" : "blocked"}
        </span>
      </div>
      {verdict.reason && (
        <p className="mt-1.5 text-xs leading-relaxed text-ink-600">
          {verdict.reason}
        </p>
      )}
      {verdict.findings && verdict.findings.length > 0 && (
        <ul className="mt-2 space-y-1">
          {verdict.findings.map((f, i) => (
            <li
              key={i}
              className="flex items-center gap-2 rounded-md bg-ink-50 px-2 py-1 text-[11px]"
            >
              <span className="font-mono font-semibold text-ink-700">
                {f.category}
              </span>
              <span className="rounded bg-ink-200 px-1 text-ink-600">
                {f.confidence}
              </span>
              {f.detail && (
                <span className="truncate text-ink-500" title={f.detail}>
                  {f.detail}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
