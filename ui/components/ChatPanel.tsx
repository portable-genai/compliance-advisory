"use client";

import { useRef, useState } from "react";
import type { ArtifactKind, Regulator } from "@/lib/types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  kind: ArtifactKind;
  text: string;
  ts: number;
  error?: boolean;
  pending?: boolean;
}

const MODES: { key: ArtifactKind; label: string; verb: string; placeholder: string }[] =
  [
    {
      key: "answer",
      label: "Ask",
      verb: "Ask",
      placeholder:
        "Ask a grounded compliance question — e.g. “MAS expectations for material cloud outsourcing to a hyperscaler”",
    },
    {
      key: "checklist",
      label: "Checklist",
      verb: "Build checklist",
      placeholder: "Describe a use case — e.g. “GenAI chatbot for retail customers on public cloud”",
    },
    {
      key: "testcases",
      label: "Test cases",
      verb: "Generate tests",
      placeholder: "Describe a use case to generate verification test cases for its controls",
    },
    {
      key: "regulator_questions",
      label: "Regulator Qs",
      verb: "Anticipate questions",
      placeholder: "Describe a use case — surface the questions a regulator / CRO will ask",
    },
    {
      key: "control_mapping",
      label: "Control Mapping",
      verb: "Map controls",
      placeholder:
        "Enter a scope to map its requirements to GCP controls, e.g. “MAS” or a notice reference",
    },
    {
      key: "gaps",
      label: "Gaps",
      verb: "Find gaps",
      placeholder:
        "Enter a scope to find requirements that are not fully covered, with severity and remediation",
    },
    {
      key: "evidence_pack",
      label: "Evidence Pack",
      verb: "Build pack",
      placeholder:
        "Enter a scope to build an audit-ready evidence pack for a regulator or use case",
    },
  ];

export function ChatPanel({
  messages,
  busy,
  mode,
  onModeChange,
  onSubmit,
  groundingEnabled,
  regulator,
}: {
  messages: ChatMessage[];
  busy: boolean;
  mode: ArtifactKind;
  onModeChange: (k: ArtifactKind) => void;
  onSubmit: (text: string) => void;
  groundingEnabled: boolean;
  regulator: Regulator | "ALL";
}) {
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const current = MODES.find((m) => m.key === mode) ?? MODES[0];

  const submit = () => {
    const text = draft.trim();
    if (!text || busy) return;
    onSubmit(text);
    setDraft("");
  };

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-ink-200 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-ink-900">Console</h2>
          <p className="text-[11px] text-ink-400">
            Grounded Q&amp;A and artifact generation
          </p>
        </div>
        <div className="flex items-center gap-2 text-[11px]">
          <span
            className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-medium ring-1 ring-inset ${
              groundingEnabled
                ? "bg-regblue-50 text-regblue-700 ring-regblue-200"
                : "bg-ink-100 text-ink-500 ring-ink-200"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                groundingEnabled ? "bg-regblue-500" : "bg-ink-300"
              }`}
            />
            grounding {groundingEnabled ? "on" : "off"}
          </span>
          {regulator !== "ALL" && (
            <span className="inline-flex items-center rounded-md bg-ink-100 px-1.5 py-0.5 font-medium text-ink-600 ring-1 ring-inset ring-ink-200">
              {regulator}
            </span>
          )}
        </div>
      </div>

      {/* Message stream */}
      <div
        ref={scrollRef}
        className="scroll-thin flex-1 space-y-3 overflow-y-auto px-4 py-4"
      >
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-regblue-50 text-regblue-600 ring-1 ring-inset ring-regblue-200">
              <svg
                viewBox="0 0 24 24"
                className="h-6 w-6"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                aria-hidden="true"
              >
                <path
                  d="M12 3 4 6v6c0 4.5 3.4 7.7 8 9 4.6-1.3 8-4.5 8-9V6l-8-3Z"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <path d="m9 12 2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
            <p className="text-sm font-medium text-ink-700">
              Ask a grounded compliance question
            </p>
            <p className="mt-1 max-w-sm text-xs text-ink-400">
              Every answer is backed by page-level citations from MAS / HKMA /
              APRA / FSA and cross-jurisdiction cloud &amp; AI guidance.
            </p>
          </div>
        ) : (
          messages.map((m) => <Bubble key={m.id} message={m} />)
        )}
      </div>

      {/* Mode + composer */}
      <div className="border-t border-ink-200 bg-white px-4 py-3">
        <div className="mb-2 flex flex-wrap gap-1">
          {MODES.map((m) => (
            <button
              key={m.key}
              type="button"
              onClick={() => onModeChange(m.key)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${
                m.key === mode
                  ? "bg-regblue-600 text-white"
                  : "bg-ink-100 text-ink-600 hover:bg-ink-200"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                submit();
              }
            }}
            rows={2}
            placeholder={current.placeholder}
            className="scroll-thin flex-1 resize-none rounded-lg border border-ink-200 bg-ink-50 px-3 py-2 text-sm text-ink-900 placeholder:text-ink-400 focus:border-regblue-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-regblue-100"
          />
          <button
            type="button"
            onClick={submit}
            disabled={busy || !draft.trim()}
            className="inline-flex h-[42px] items-center gap-1.5 rounded-lg bg-regblue-600 px-3.5 text-sm font-semibold text-white shadow-sm transition hover:bg-regblue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? (
              <svg
                viewBox="0 0 24 24"
                className="h-4 w-4 animate-spin"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                aria-hidden="true"
              >
                <path d="M21 12a9 9 0 1 1-6.2-8.5" strokeLinecap="round" />
              </svg>
            ) : (
              current.verb
            )}
          </button>
        </div>
        <p className="mt-1.5 text-[11px] text-ink-400">
          Press <kbd className="rounded bg-ink-100 px-1 font-mono">⌘/Ctrl</kbd> +{" "}
          <kbd className="rounded bg-ink-100 px-1 font-mono">Enter</kbd> to send.
          Results render in the panel on the right.
        </p>
      </div>
    </div>
  );
}

function Bubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  if (message.role === "system") {
    return (
      <div className="flex justify-center">
        <span
          className={`rounded-full px-2.5 py-1 text-[11px] ${
            message.error
              ? "bg-rose-50 text-rose-600 ring-1 ring-inset ring-rose-200"
              : "bg-ink-100 text-ink-500"
          }`}
        >
          {message.text}
        </span>
      </div>
    );
  }
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-sm leading-relaxed shadow-sm ${
          isUser
            ? "rounded-br-sm bg-regblue-600 text-white"
            : message.error
              ? "rounded-bl-sm border border-rose-200 bg-rose-50 text-rose-700"
              : "rounded-bl-sm border border-ink-200 bg-white text-ink-800"
        }`}
      >
        {!isUser && (
          <div className="mb-0.5 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-ink-400">
            {KIND_LABEL[message.kind]}
            {message.pending && (
              <span className="inline-flex gap-0.5">
                <Dot /> <Dot /> <Dot />
              </span>
            )}
          </div>
        )}
        <span className="whitespace-pre-wrap">{message.text}</span>
      </div>
    </div>
  );
}

const KIND_LABEL: Record<ArtifactKind, string> = {
  answer: "Answer",
  checklist: "Checklist",
  testcases: "Test cases",
  regulator_questions: "Regulator questions",
  control_mapping: "Control mapping",
  gaps: "Gaps",
  evidence_pack: "Evidence pack",
};

function Dot() {
  return (
    <span className="inline-block h-1 w-1 animate-pulse rounded-full bg-ink-400" />
  );
}
