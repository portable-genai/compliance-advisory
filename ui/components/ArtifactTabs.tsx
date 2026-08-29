"use client";

import type {
  Answer,
  ArtifactKind,
  ControlChecklist,
  ControlGap,
  ControlMapping,
  EvidencePack,
  RegulatorQuestion,
  TestCase,
} from "@/lib/types";
import { AnswerView } from "./AnswerView";
import { ChecklistView } from "./ChecklistView";
import { TestCaseView } from "./TestCaseView";
import { RegulatorQuestionsView } from "./RegulatorQuestionsView";
import { MappingView } from "./MappingView";
import { GapView } from "./GapView";
import { EvidencePackView } from "./EvidencePackView";

export interface ArtifactState {
  answer: Answer | null;
  checklist: ControlChecklist | null;
  testcases: TestCase[] | null;
  regulatorQuestions: RegulatorQuestion[] | null;
  controlMapping: ControlMapping[] | null;
  gaps: ControlGap[] | null;
  evidencePack: EvidencePack | null;
}

const TABS: { key: ArtifactKind; label: string }[] = [
  { key: "answer", label: "Answer" },
  { key: "checklist", label: "Checklist" },
  { key: "testcases", label: "Test Cases" },
  { key: "regulator_questions", label: "Regulator Questions" },
  { key: "control_mapping", label: "Control Mapping" },
  { key: "gaps", label: "Gaps" },
  { key: "evidence_pack", label: "Evidence Pack" },
];

function countFor(kind: ArtifactKind, s: ArtifactState): number | null {
  switch (kind) {
    case "answer":
      return s.answer ? s.answer.citations.length : null;
    case "checklist":
      return s.checklist ? s.checklist.items.length : null;
    case "testcases":
      return s.testcases ? s.testcases.length : null;
    case "regulator_questions":
      return s.regulatorQuestions ? s.regulatorQuestions.length : null;
    case "control_mapping":
      return s.controlMapping ? s.controlMapping.length : null;
    case "gaps":
      return s.gaps ? s.gaps.length : null;
    case "evidence_pack":
      return s.evidencePack ? s.evidencePack.mappings.length : null;
  }
}

function reviewFlag(kind: ArtifactKind, s: ArtifactState): boolean {
  switch (kind) {
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

export function ArtifactTabs({
  active,
  onSelect,
  state,
  streaming,
}: {
  active: ArtifactKind;
  onSelect: (k: ArtifactKind) => void;
  state: ArtifactState;
  streaming: boolean;
}) {
  return (
    <div className="flex h-full flex-col">
      <div
        role="tablist"
        aria-label="Compliance artifacts"
        className="scroll-thin flex items-center gap-1 overflow-x-auto border-b border-ink-200 px-1"
      >
        {TABS.map((t) => {
          const count = countFor(t.key, state);
          const isActive = t.key === active;
          const flagged = reviewFlag(t.key, state);
          return (
            <button
              key={t.key}
              role="tab"
              aria-selected={isActive}
              onClick={() => onSelect(t.key)}
              className={`relative -mb-px flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm font-medium transition ${
                isActive
                  ? "border-regblue-600 text-regblue-700"
                  : "border-transparent text-ink-500 hover:text-ink-800"
              }`}
            >
              {t.label}
              {count != null && (
                <span
                  className={`rounded-full px-1.5 text-[11px] font-semibold tabular-nums ${
                    isActive
                      ? "bg-regblue-100 text-regblue-700"
                      : "bg-ink-100 text-ink-500"
                  }`}
                >
                  {count}
                </span>
              )}
              {flagged && (
                <span
                  title="Requires human review"
                  className="h-1.5 w-1.5 rounded-full bg-amber-500"
                />
              )}
            </button>
          );
        })}
      </div>

      <div className="scroll-thin flex-1 overflow-y-auto px-1 py-4">
        {active === "answer" && (
          <AnswerView answer={state.answer} streaming={streaming} />
        )}
        {active === "checklist" && <ChecklistView checklist={state.checklist} />}
        {active === "testcases" && <TestCaseView testcases={state.testcases} />}
        {active === "regulator_questions" && (
          <RegulatorQuestionsView questions={state.regulatorQuestions} />
        )}
        {active === "control_mapping" && (
          <MappingView mappings={state.controlMapping} />
        )}
        {active === "gaps" && <GapView gaps={state.gaps} />}
        {active === "evidence_pack" && (
          <EvidencePackView pack={state.evidencePack} />
        )}
      </div>
    </div>
  );
}
