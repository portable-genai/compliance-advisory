/**
 * TypeScript mirrors of the C1 domain dataclasses.
 *
 * Source of truth: `src/compliance_advisory/domain/models.py`.
 * The backend serialises dataclasses with `domain/serialization.to_jsonable`
 * (SPEC §5): dataclass field names are preserved (snake_case) and every enum is
 * rendered as its `.value` string. These types follow that contract exactly.
 */

// --------------------------------------------------------------------------- //
// Regulatory taxonomy
// --------------------------------------------------------------------------- //
export type Regulator = "MAS" | "HKMA" | "APRA" | "FSA" | "CROSS";

export type Jurisdiction = "SG" | "HK" | "AU" | "JP" | "GLOBAL";

export type DocType =
  | "notice"
  | "guideline"
  | "circular"
  | "standard"
  | "consultation"
  | "advisory"
  | "other";

/** Severity enum used by checklist items. */
export type Severity = "low" | "medium" | "high" | "critical";

/** Stable regulator -> jurisdiction mapping (mirrors REGULATOR_JURISDICTION). */
export const REGULATOR_JURISDICTION: Record<Regulator, Jurisdiction> = {
  MAS: "SG",
  HKMA: "HK",
  APRA: "AU",
  FSA: "JP",
  CROSS: "GLOBAL",
};

/** Human-readable labels for each regulator. */
export const REGULATOR_LABEL: Record<Regulator, string> = {
  MAS: "Monetary Authority of Singapore",
  HKMA: "Hong Kong Monetary Authority",
  APRA: "Australian Prudential Regulation Authority",
  FSA: "Financial Services Agency (Japan)",
  CROSS: "Cross-jurisdiction cloud / AI guidance",
};

// --------------------------------------------------------------------------- //
// Retrieval & citation
// --------------------------------------------------------------------------- //
export interface Citation {
  source_id: string;
  regulator: Regulator;
  jurisdiction: Jurisdiction;
  title: string;
  url: string;
  version: string;
  page: number | null;
  snippet: string;
  score: number | null;
}

export interface WebCitation {
  title: string;
  url: string;
  snippet: string;
}

// --------------------------------------------------------------------------- //
// Safety (guardrail + PII redaction) — A1 Guardrail Gateway
// --------------------------------------------------------------------------- //
export type GuardrailCategory =
  | "prompt_injection"
  | "jailbreak"
  | "sensitive_data"
  | "malicious_url"
  | "hate"
  | "harassment"
  | "sexual"
  | "dangerous"
  | "other";

export type Direction = "input" | "output";

export interface GuardrailFinding {
  category: GuardrailCategory;
  confidence: string; // "low" | "medium" | "high"
  detail: string;
}

export interface GuardrailVerdict {
  allowed: boolean;
  direction: Direction;
  findings: GuardrailFinding[];
  sanitized_text: string | null;
  reason: string;
}

export interface RedactionFinding {
  info_type: string;
  count: number;
}

// --------------------------------------------------------------------------- //
// Evaluation gate — A4 AI Quality
// --------------------------------------------------------------------------- //
export interface EvalMetricResult {
  metric: string; // "groundedness" | "citation_accuracy" | "faithfulness" | "safety"
  score: number;
  threshold: number;
  passed: boolean;
}

export interface EvalReport {
  dataset: string;
  results: EvalMetricResult[];
  n_examples: number;
  passed?: boolean;
}

// --------------------------------------------------------------------------- //
// Top-level assistant outputs (the four artifacts C1 produces)
// --------------------------------------------------------------------------- //
export interface Answer {
  question: string;
  answer: string;
  citations: Citation[];
  web_citations: WebCitation[];
  confidence: number;
  requires_human_review: boolean;
  caveats: string[];
}

export interface ChecklistItem {
  control_id: string;
  control: string;
  rationale: string;
  severity: Severity;
  citations: Citation[];
}

export interface ControlChecklist {
  use_case: string;
  items: ChecklistItem[];
  requires_human_review: boolean;
}

export interface TestCase {
  id: string;
  title: string;
  control_id: string;
  steps: string[];
  expected_result: string;
  automated_check: string | null;
  citations: Citation[];
}

export interface RegulatorQuestion {
  question: string;
  why_asked: string;
  model_answer: string;
  regulator: Regulator;
  citations: Citation[];
}

// --------------------------------------------------------------------------- //
// Corpus freshness — the 7-day fetch-at-runtime ledger
// --------------------------------------------------------------------------- //
export type FreshnessStatus = "fresh" | "expired" | "missing" | "failed";

export interface FreshnessRecord {
  source_id: string;
  url: string;
  version: string;
  fetched_at: string; // ISO-8601 datetime
  expires_at: string; // ISO-8601 datetime
  checksum: string;
  status: FreshnessStatus;
}

// --------------------------------------------------------------------------- //
// Observability envelope
// --------------------------------------------------------------------------- //
/**
 * Optional governance/observability metadata the API may attach to a response
 * (A1 guardrail verdict, A4 eval/confidence, A5 audit linkage). All fields are
 * optional so the UI degrades gracefully when the backend omits them.
 */
export interface Observability {
  guardrail_input?: GuardrailVerdict | null;
  guardrail_output?: GuardrailVerdict | null;
  eval?: EvalReport | null;
  redactions?: RedactionFinding[];
  decision?: "allowed" | "blocked" | "escalated";
  trace_id?: string | null;
  confidence?: number | null;
}

// --------------------------------------------------------------------------- //
// Control-mapping toolkit (C2) domain types
// --------------------------------------------------------------------------- //
/**
 * Mirrors of the merged C2 control-mapping dataclasses, surfaced by the
 * /map, /gaps and /evidence-pack routes. Regulator, Jurisdiction, Severity and
 * Citation are shared with C1 above and reused here.
 */
export type Coverage = "full" | "partial" | "none";

export type ControlState =
  | "enabled"
  | "partial"
  | "disabled"
  | "misconfigured"
  | "unknown";

export type ControlFamily =
  | "vpc_sc"
  | "cmek"
  | "assured_workloads"
  | "access_transparency"
  | "sovereign_controls"
  | "org_policy"
  | "iam"
  | "vpc"
  | "logging"
  | "encryption"
  | "other";

/** Human-readable labels for each control family. */
export const FAMILY_LABEL: Record<ControlFamily, string> = {
  vpc_sc: "VPC Service Controls",
  cmek: "CMEK",
  assured_workloads: "Assured Workloads",
  access_transparency: "Access Transparency",
  sovereign_controls: "Sovereign Controls",
  org_policy: "Org Policy",
  iam: "IAM",
  vpc: "VPC",
  logging: "Logging",
  encryption: "Encryption",
  other: "Other",
};

export interface GcpControl {
  id: string;
  name: string;
  family: ControlFamily;
  description: string;
  config_ref: string;
}

export interface RegRequirement {
  id: string;
  regulator: Regulator;
  jurisdiction: Jurisdiction;
  title: string;
  text: string;
  citation: Citation;
}

export interface ControlObservation {
  control_id: string;
  resource: string;
  state: ControlState;
  detail: string;
  source: string;
  observed_at: string;
}

export interface ControlMapping {
  requirement: RegRequirement;
  controls: GcpControl[];
  observations: ControlObservation[];
  coverage: Coverage;
  rationale: string;
  citations: Citation[];
  requires_human_review: boolean;
}

export interface ControlGap {
  requirement: RegRequirement;
  missing_controls: ControlFamily[];
  severity: Severity;
  remediation: string;
  citations: Citation[];
}

export interface EvidencePack {
  scope: string;
  mappings: ControlMapping[];
  gaps: ControlGap[];
  coverage_summary: Record<string, number>;
  generated_at: string;
  requires_human_review: boolean;
}

export type ArtifactKind =
  | "answer"
  | "checklist"
  | "testcases"
  | "regulator_questions"
  | "control_mapping"
  | "gaps"
  | "evidence_pack";
