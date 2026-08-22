"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { FreshnessRecord, FreshnessStatus } from "@/lib/types";
import { CORPUS_UPLOAD_TEMPLATE_URL, corpusStatus, corpusUpload } from "@/lib/api";

const STATUS_STYLES: Record<FreshnessStatus, string> = {
  fresh: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  expired: "bg-amber-50 text-amber-700 ring-amber-200",
  missing: "bg-ink-100 text-ink-600 ring-ink-200",
  failed: "bg-rose-50 text-rose-700 ring-rose-200",
};

function relExpiry(iso: string): { label: string; stale: boolean } {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return { label: "—", stale: false };
  const deltaMs = t - Date.now();
  const stale = deltaMs <= 0;
  const days = Math.round(Math.abs(deltaMs) / 86_400_000);
  if (stale) return { label: days === 0 ? "expired today" : `expired ${days}d ago`, stale };
  return { label: days === 0 ? "expires today" : `in ${days}d`, stale };
}

export function CorpusStatus() {
  const [records, setRecords] = useState<FreshnessRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadNote, setUploadNote] = useState<{ ok: boolean; text: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const recs = await corpusStatus();
      setRecords(recs);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setRecords(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const counts = (records ?? []).reduce<Record<string, number>>((acc, r) => {
    acc[r.status] = (acc[r.status] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="rounded-lg border border-ink-200 bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-ink-100 px-3 py-2.5">
        <div>
          <h3 className="text-sm font-semibold text-ink-900">Corpus freshness</h3>
          <p className="text-[11px] text-ink-400">7-day fetch-at-runtime ledger</p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex items-center gap-1 rounded-md border border-ink-200 px-2 py-1 text-[11px] font-medium text-ink-600 hover:bg-ink-50 disabled:opacity-50"
        >
          <svg
            viewBox="0 0 20 20"
            className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            aria-hidden="true"
          >
            <path
              d="M3 10a7 7 0 0 1 12-5m2 0v4h-4M17 10a7 7 0 0 1-12 5m-2 0v-4h4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          Refresh
        </button>
      </div>

      {error && (
        <div className="px-3 py-3 text-xs text-rose-600">
          Could not reach <span className="font-mono">/corpus/status</span>.
          <span className="mt-0.5 block break-words text-rose-400">{error}</span>
        </div>
      )}

      {!error && records && records.length === 0 && (
        <div className="px-3 py-3 text-xs text-ink-400">
          No sources reported by the ledger.
        </div>
      )}

      {!error && records && records.length > 0 && (
        <>
          <div className="flex flex-wrap gap-1.5 px-3 py-2">
            {(["fresh", "expired", "missing", "failed"] as FreshnessStatus[])
              .filter((s) => counts[s])
              .map((s) => (
                <span
                  key={s}
                  className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${STATUS_STYLES[s]}`}
                >
                  {counts[s]} {s}
                </span>
              ))}
          </div>
          <div className="scroll-thin max-h-64 overflow-y-auto">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-ink-50 text-[10px] uppercase tracking-wide text-ink-400">
                <tr>
                  <th className="px-3 py-1.5 font-semibold">Source</th>
                  <th className="px-2 py-1.5 font-semibold">Ver</th>
                  <th className="px-3 py-1.5 text-right font-semibold">Expiry</th>
                </tr>
              </thead>
              <tbody>
                {records.map((r, i) => {
                  const exp = relExpiry(r.expires_at);
                  return (
                    <tr
                      key={`${r.source_id}-${i}`}
                      className="border-t border-ink-100 hover:bg-ink-50/50"
                    >
                      <td className="max-w-[10rem] px-3 py-1.5">
                        <div className="flex items-center gap-1.5">
                          <span
                            className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${
                              r.status === "fresh"
                                ? "bg-emerald-500"
                                : r.status === "expired"
                                  ? "bg-amber-500"
                                  : r.status === "failed"
                                    ? "bg-rose-500"
                                    : "bg-ink-300"
                            }`}
                          />
                          <span
                            title={r.source_id}
                            className="truncate font-mono text-ink-700"
                          >
                            {r.source_id}
                          </span>
                        </div>
                      </td>
                      <td className="px-2 py-1.5 font-mono text-ink-500">
                        {r.version || "—"}
                      </td>
                      <td
                        className={`px-3 py-1.5 text-right tabular-nums ${
                          exp.stale ? "text-amber-600" : "text-ink-500"
                        }`}
                      >
                        {exp.label}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {!error && !records && loading && (
        <div className="px-3 py-3 text-xs text-ink-400">Loading ledger…</div>
      )}

      <div className="border-t border-ink-100 px-3 py-2.5">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-semibold text-ink-900">Add a document</h4>
          <a
            href={CORPUS_UPLOAD_TEMPLATE_URL}
            download
            className="text-[11px] font-medium text-regblue-600 underline decoration-dotted hover:text-regblue-700"
          >
            Download upload template
          </a>
        </div>
        <form
          className="mt-2 space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            const file = fileRef.current?.files?.[0];
            if (!file || !uploadTitle.trim() || uploading) return;
            setUploading(true);
            setUploadNote(null);
            corpusUpload(file, { title: uploadTitle.trim() })
              .then((r) => {
                setUploadNote({
                  ok: r.ok,
                  text: r.ok
                    ? `Indexed ${r.chunks} page${r.chunks === 1 ? "" : "s"} as ${r.source_id}`
                    : r.detail || "upload failed",
                });
                setUploadTitle("");
                if (fileRef.current) fileRef.current.value = "";
                void load();
              })
              .catch((err) =>
                setUploadNote({
                  ok: false,
                  text: err instanceof Error ? err.message : String(err),
                }),
              )
              .finally(() => setUploading(false));
          }}
        >
          <input
            type="text"
            value={uploadTitle}
            onChange={(e) => setUploadTitle(e.target.value)}
            placeholder="Document title (shown in citations)"
            className="w-full rounded-md border border-ink-200 px-2 py-1 text-xs text-ink-700 placeholder:text-ink-300"
          />
          <div className="flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.txt,application/pdf,text/plain"
              className="min-w-0 flex-1 text-[11px] text-ink-500 file:mr-2 file:rounded-md file:border file:border-ink-200 file:bg-white file:px-2 file:py-1 file:text-[11px] file:font-medium file:text-ink-600 hover:file:bg-ink-50"
            />
            <button
              type="submit"
              disabled={uploading || !uploadTitle.trim()}
              className="rounded-md bg-ink-900 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-ink-700 disabled:opacity-40"
            >
              {uploading ? "Uploading…" : "Upload"}
            </button>
          </div>
        </form>
        {uploadNote && (
          <p
            className={`mt-1.5 text-[11px] ${uploadNote.ok ? "text-emerald-600" : "text-rose-600"}`}
          >
            {uploadNote.text}
          </p>
        )}
      </div>
    </div>
  );
}
