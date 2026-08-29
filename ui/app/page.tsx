"use client";

import { useCallback, useEffect, useState } from "react";
import type {
  ArtifactKind,
  Observability,
  Regulator,
} from "@/lib/types";
import { REGULATOR_LABEL, REGULATOR_JURISDICTION } from "@/lib/types";
import {
  api,
  API_BASE,
  setDevPersona,
  type AskFilters,
  type Persona,
} from "@/lib/api";
import { ChatPanel, type ChatMessage } from "@/components/ChatPanel";
import { ArtifactTabs, type ArtifactState } from "@/components/ArtifactTabs";
import { ObservabilityPanel } from "@/components/ObservabilityPanel";
import { CorpusStatus } from "@/components/CorpusStatus";

// EMBED mode: the host application owns the chrome, so the console drops its own
// top bar (see docs/embedding-and-identity.md).
const IS_EMBEDDED = process.env.NEXT_PUBLIC_EMBED === "1";
const REGULATORS: Regulator[] = ["MAS", "HKMA", "APRA", "FSA", "CROSS"];

const EMPTY_ARTIFACTS: ArtifactState = {
  answer: null,
  checklist: null,
  testcases: null,
  regulatorQuestions: null,
  controlMapping: null,
  gaps: null,
  evidencePack: null,
};

let msgSeq = 0;
const nextId = () => `m${++msgSeq}-${Date.now()}`;

export default function Page() {
  const [regulator, setRegulator] = useState<Regulator | "ALL">("ALL");
  const [groundingEnabled, setGroundingEnabled] = useState(true);
  const [mode, setMode] = useState<ArtifactKind>("answer");

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);

  const [artifacts, setArtifacts] = useState<ArtifactState>(EMPTY_ARTIFACTS);
  const [activeTab, setActiveTab] = useState<ArtifactKind>("answer");
  const [streamKey, setStreamKey] = useState(0);
  const [observability, setObservability] = useState<Observability | null>(null);

  const [health, setHealth] = useState<"checking" | "up" | "down">("checking");
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [selectedPersona, setSelectedPersona] = useState("");

  // Backend health probe (and periodic re-check).
  useEffect(() => {
    let active = true;
    const probe = async () => {
      const res = await api.healthz();
      if (active) setHealth(res.ok ? "up" : "down");
    };
    void probe();
    const id = window.setInterval(probe, 30_000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, []);

  // "Demo identity" persona picker bootstrap: LOCAL profile only. Local mode runs
  // with no IdP; the backend resolves identity from the X-Dev-Persona header. In
  // secure profiles /healthz reports a non-local profile and the picker never shows
  // (identity comes from the platform-verified IAP assertion).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await api.healthz();
        if (status.profile !== "local") return;
        const list = await api.listPersonas();
        if (cancelled || list.length === 0) return;
        setPersonas(list);
        setSelectedPersona(list[0].id);
        setDevPersona(list[0].id);
      } catch {
        // Persona picker is dev-only convenience; ignore lookup failures.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const onPersonaChange = useCallback((id: string) => {
    setSelectedPersona(id);
    setDevPersona(id);
  }, []);

  const buildFilters = useCallback((): AskFilters | undefined => {
    if (regulator === "ALL") return undefined;
    return {
      regulator,
      jurisdiction: REGULATOR_JURISDICTION[regulator],
    };
  }, [regulator]);

  const pushMessage = (m: ChatMessage) => setMessages((prev) => [...prev, m]);

  const handleSubmit = useCallback(
    async (text: string) => {
      setBusy(true);
      const userMsg: ChatMessage = {
        id: nextId(),
        role: "user",
        kind: mode,
        text,
        ts: Date.now(),
      };
      const pendingId = nextId();
      pushMessage(userMsg);
      pushMessage({
        id: pendingId,
        role: "assistant",
        kind: mode,
        text: "Working…",
        ts: Date.now(),
        pending: true,
      });

      const filters = buildFilters();
      const filterRecord = filters
        ? Object.fromEntries(
            Object.entries(filters).filter(([, v]) => v != null),
          )
        : undefined;

      try {
        let summary = "";
        let obs: Observability | null = null;

        if (mode === "answer") {
          const { data, observability } = await api.ask({
            question: text,
            grounding_enabled: groundingEnabled,
            filters: filterRecord,
          });
          setArtifacts((prev) => ({ ...prev, answer: data }));
          obs = mergeObservability(observability, {
            confidence: data.confidence,
            decision: data.requires_human_review ? "escalated" : "allowed",
          });
          summary = `Grounded answer with ${data.citations.length} citation${
            data.citations.length === 1 ? "" : "s"
          }${data.requires_human_review ? " · human review required" : ""}.`;
          setActiveTab("answer");
        } else if (mode === "checklist") {
          const { data, observability } = await api.checklist({
            use_case: text,
            filters: filterRecord,
          });
          setArtifacts((prev) => ({ ...prev, checklist: data }));
          obs = mergeObservability(observability, {
            decision: data.requires_human_review ? "escalated" : "allowed",
          });
          summary = `Control checklist with ${data.items.length} control${
            data.items.length === 1 ? "" : "s"
          }${data.requires_human_review ? " · human review required" : ""}.`;
          setActiveTab("checklist");
        } else if (mode === "testcases") {
          const { data, observability } = await api.testcases({
            use_case: text,
            filters: filterRecord,
          });
          setArtifacts((prev) => ({ ...prev, testcases: data }));
          obs = mergeObservability(observability, {
            decision: data.length ? "escalated" : "allowed",
          });
          summary = `${data.length} verification test case${
            data.length === 1 ? "" : "s"
          } · human review required.`;
          setActiveTab("testcases");
        } else if (mode === "regulator_questions") {
          const { data, observability } = await api.regulatorQuestions({
            use_case: text,
            filters: filterRecord,
          });
          setArtifacts((prev) => ({ ...prev, regulatorQuestions: data }));
          obs = mergeObservability(observability, { decision: "allowed" });
          summary = `${data.length} anticipated regulator / CRO question${
            data.length === 1 ? "" : "s"
          }.`;
          setActiveTab(mode);
        } else if (mode === "control_mapping") {
          const data = await api.mapControls({
            scope: text,
            regulator: regulator === "ALL" ? undefined : regulator,
          });
          setArtifacts((prev) => ({ ...prev, controlMapping: data }));
          const review = data.some((m) => m.requires_human_review);
          obs = mergeObservability(null, {
            decision: review ? "escalated" : "allowed",
          });
          summary = `${data.length} requirement${
            data.length === 1 ? "" : "s"
          } mapped to GCP controls${
            review ? " · human review required" : ""
          }.`;
          setActiveTab(mode);
        } else if (mode === "gaps") {
          const data = await api.controlGaps({
            scope: text,
            regulator: regulator === "ALL" ? undefined : regulator,
          });
          setArtifacts((prev) => ({ ...prev, gaps: data }));
          obs = mergeObservability(null, {
            decision: data.length ? "escalated" : "allowed",
          });
          summary = `${data.length} control gap${
            data.length === 1 ? "" : "s"
          } found${data.length ? " · human review required" : ""}.`;
          setActiveTab(mode);
        } else {
          const data = await api.evidencePack({
            scope: text,
            regulator: regulator === "ALL" ? undefined : regulator,
          });
          setArtifacts((prev) => ({ ...prev, evidencePack: data }));
          obs = mergeObservability(null, { decision: "escalated" });
          summary = `Evidence pack for ${data.scope} with ${
            data.mappings.length
          } mapping${data.mappings.length === 1 ? "" : "s"} and ${
            data.gaps.length
          } gap${
            data.gaps.length === 1 ? "" : "s"
          } · human review required.`;
          setActiveTab(mode);
        }

        setObservability(obs);
        setStreamKey((k) => k + 1);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === pendingId
              ? { ...m, text: summary, pending: false }
              : m,
          ),
        );
      } catch (e) {
        const detail = e instanceof Error ? e.message : String(e);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === pendingId
              ? {
                  ...m,
                  text: `Request failed: ${detail}`,
                  pending: false,
                  error: true,
                }
              : m,
          ),
        );
      } finally {
        setBusy(false);
      }
    },
    [mode, groundingEnabled, buildFilters, regulator],
  );

  const requiresHumanReview = currentReviewFlag(activeTab, artifacts);
  const activeConfidence =
    activeTab === "answer" && artifacts.answer
      ? artifacts.answer.confidence
      : (observability?.confidence ?? null);

  return (
    <div className="flex h-screen flex-col bg-ink-50">
      {IS_EMBEDDED ? null : <TopBar health={health} />}

      <div className="grid flex-1 grid-cols-1 gap-px overflow-hidden bg-ink-200 lg:grid-cols-[16rem_minmax(0,1fr)_22rem] xl:grid-cols-[17rem_minmax(0,1fr)_24rem]">
        {/* Left config rail */}
        <aside className="hidden overflow-y-auto bg-white lg:block scroll-thin">
          <ConfigRail
            regulator={regulator}
            onRegulator={setRegulator}
            groundingEnabled={groundingEnabled}
            onGrounding={setGroundingEnabled}
            personas={personas}
            selectedPersona={selectedPersona}
            onPersonaChange={onPersonaChange}
          />
        </aside>

        {/* Main: chat + artifacts */}
        <main className="grid min-w-0 grid-rows-[minmax(0,1fr)_minmax(0,1fr)] bg-white xl:grid-rows-1 xl:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
          <section className="min-h-0 border-b border-ink-200 xl:border-b-0 xl:border-r">
            <ChatPanel
              messages={messages}
              busy={busy}
              mode={mode}
              onModeChange={setMode}
              onSubmit={handleSubmit}
              groundingEnabled={groundingEnabled}
              regulator={regulator}
            />
          </section>
          <section className="min-h-0 px-3">
            <ArtifactTabs
              key={streamKey}
              active={activeTab}
              onSelect={setActiveTab}
              state={artifacts}
              streaming
            />
          </section>
        </main>

        {/* Right observability rail */}
        <aside className="hidden flex-col overflow-hidden bg-white lg:flex">
          <div className="min-h-0 flex-1">
            <ObservabilityPanel
              observability={observability}
              requiresHumanReview={requiresHumanReview}
              confidence={activeConfidence}
            />
          </div>
          <div className="border-t border-ink-200 p-3">
            <CorpusStatus />
          </div>
        </aside>
      </div>
    </div>
  );
}

function TopBar({ health }: { health: "checking" | "up" | "down" }) {
  const tone =
    health === "up"
      ? "bg-emerald-500"
      : health === "down"
        ? "bg-rose-500"
        : "bg-amber-400";
  const label =
    health === "up" ? "backend up" : health === "down" ? "backend down" : "checking…";
  return (
    <header className="flex flex-wrap items-center justify-between gap-2 border-b border-ink-200 bg-white px-4 py-2.5">
      <div className="flex items-center gap-2.5">
        <img
          src="/logo.jpg"
          alt=""
          width={32}
          height={32}
          className="h-8 w-8 rounded-lg object-cover"
        />
        <div>
          <h1 className="text-sm font-semibold leading-tight text-ink-900">
            Compliance Assistant
          </h1>
          <p className="text-[11px] leading-tight text-ink-400">
            rsk · MAS / HKMA / APRA / FSA · asia-southeast1
          </p>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <span
          className="hidden font-mono text-[11px] text-ink-400 sm:inline"
          title="Configured via NEXT_PUBLIC_API_BASE"
        >
          {API_BASE}
        </span>
        <span className="inline-flex items-center gap-1.5 rounded-md bg-ink-50 px-2 py-1 text-[11px] font-medium text-ink-600 ring-1 ring-inset ring-ink-200">
          <span className={`h-1.5 w-1.5 rounded-full ${tone}`} />
          {label}
        </span>
      </div>
    </header>
  );
}

function ConfigRail({
  regulator,
  onRegulator,
  groundingEnabled,
  onGrounding,
  personas,
  selectedPersona,
  onPersonaChange,
}: {
  regulator: Regulator | "ALL";
  onRegulator: (r: Regulator | "ALL") => void;
  groundingEnabled: boolean;
  onGrounding: (v: boolean) => void;
  personas: Persona[];
  selectedPersona: string;
  onPersonaChange: (id: string) => void;
}) {
  return (
    <div className="space-y-6 p-4">
      {!IS_EMBEDDED && personas.length > 0 ? (
        <div>
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">
            Demo identity
          </div>
          <label className="block text-sm">
            <span className="sr-only">Persona</span>
            <select
              className="w-full rounded-lg border border-ink-200 bg-white px-2 py-2 text-sm text-ink-800"
              value={selectedPersona}
              onChange={(e) => onPersonaChange(e.target.value)}
            >
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.subject} · {p.tenant}
                </option>
              ))}
            </select>
          </label>
          <p className="mt-1.5 text-[11px] leading-relaxed text-ink-400">
            Local profile only: sets the{" "}
            <span className="font-mono">X-Dev-Persona</span> header. Secure
            deployments resolve identity from the platform-verified IAP
            assertion, never from the client.
          </p>
        </div>
      ) : null}

      <div>
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">
          Regulator filter
        </div>
        <div className="space-y-1">
          <RegulatorButton
            active={regulator === "ALL"}
            label="All regulators"
            sub="No jurisdiction filter"
            onClick={() => onRegulator("ALL")}
          />
          {REGULATORS.map((r) => (
            <RegulatorButton
              key={r}
              active={regulator === r}
              label={r}
              sub={`${REGULATOR_LABEL[r]} · ${REGULATOR_JURISDICTION[r]}`}
              onClick={() => onRegulator(r)}
            />
          ))}
        </div>
      </div>

      <div>
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">
          Web grounding
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={groundingEnabled}
          onClick={() => onGrounding(!groundingEnabled)}
          className="flex w-full items-center justify-between rounded-lg border border-ink-200 px-3 py-2.5 text-left hover:bg-ink-50"
        >
          <div>
            <div className="text-sm font-medium text-ink-800">
              google_search tool
            </div>
            <div className="text-[11px] text-ink-400">
              Secondary cross-border facts
            </div>
          </div>
          <span
            className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition ${
              groundingEnabled ? "bg-regblue-600" : "bg-ink-300"
            }`}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition ${
                groundingEnabled ? "translate-x-4" : "translate-x-0.5"
              }`}
            />
          </span>
        </button>
        <p className="mt-1.5 text-[11px] leading-relaxed text-ink-400">
          Isolated in a grounding sub-agent. Toggle reflects the{" "}
          <span className="font-mono">grounding_enabled</span> request flag.
        </p>
      </div>

      <div>
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">
          Grounding posture
        </div>
        <ul className="space-y-1.5 text-[11px] leading-relaxed text-ink-500">
          <li className="flex items-center gap-2">
            <Check /> Page-level citations required
          </li>
          <li className="flex items-center gap-2">
            <Check /> Guardrail screen on I/O (A1)
          </li>
          <li className="flex items-center gap-2">
            <Check /> PII redacted before model + audit
          </li>
          <li className="flex items-center gap-2">
            <Check /> Maker–checker on consequential outputs
          </li>
        </ul>
      </div>

      <div className="rounded-lg bg-ink-50 p-3 text-[11px] leading-relaxed text-ink-500 ring-1 ring-inset ring-ink-200">
        Demo console. All four artifacts are produced by the C1 backend; this UI
        only renders and never bypasses the guardrail or maker–checker gates.
      </div>
    </div>
  );
}

function RegulatorButton({
  active,
  label,
  sub,
  onClick,
}: {
  active: boolean;
  label: string;
  sub: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left transition ${
        active
          ? "bg-regblue-50 ring-1 ring-inset ring-regblue-200"
          : "hover:bg-ink-50"
      }`}
    >
      <span
        className={`h-2 w-2 shrink-0 rounded-full ${
          active ? "bg-regblue-600" : "bg-ink-300"
        }`}
      />
      <span className="min-w-0">
        <span
          className={`block text-sm font-semibold ${
            active ? "text-regblue-700" : "text-ink-800"
          }`}
        >
          {label}
        </span>
        <span className="block truncate text-[11px] text-ink-400">{sub}</span>
      </span>
    </button>
  );
}

function Check() {
  return (
    <svg
      viewBox="0 0 20 20"
      className="h-3.5 w-3.5 shrink-0 text-emerald-500"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden="true"
    >
      <path d="m4 10 4 4 8-8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function mergeObservability(
  fromApi: Observability | null,
  derived: Partial<Observability>,
): Observability {
  // Prefer real backend observability; fall back to values the UI can derive
  // from the artifact (e.g. confidence on Answer) so the panel is never empty.
  return {
    ...derived,
    ...(fromApi ?? {}),
    confidence: fromApi?.confidence ?? derived.confidence ?? null,
    decision: fromApi?.decision ?? derived.decision,
  };
}

function currentReviewFlag(tab: ArtifactKind, s: ArtifactState): boolean {
  switch (tab) {
    case "answer":
      return !!s.answer?.requires_human_review;
    case "checklist":
      return !!s.checklist?.requires_human_review;
    case "testcases":
      return !!s.testcases && s.testcases.length > 0;
    case "regulator_questions":
      return false;
    case "control_mapping":
      return !!s.controlMapping?.some((m) => m.requires_human_review);
    case "gaps":
      return !!s.gaps && s.gaps.length > 0;
    case "evidence_pack":
      return !!s.evidencePack;
  }
}
