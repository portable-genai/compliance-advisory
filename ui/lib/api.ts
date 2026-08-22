/**
 * Typed fetch client for the C1 Compliance Assistant FastAPI backend.
 *
 * Routes (SPEC §1 + inferred shapes):
 *   POST /ask                  -> Answer
 *   POST /checklist            -> ControlChecklist
 *   POST /testcases            -> TestCase[]
 *   POST /regulator-questions  -> RegulatorQuestion[]
 *   GET  /corpus/status        -> FreshnessRecord[]
 *   GET  /healthz              -> { status: "ok", profile, region }
 *   GET  /personas             -> Persona[] (local profile only; empty otherwise)
 *
 * The four POST artifacts MAY arrive either bare (just the domain object) or
 * wrapped in an envelope alongside governance metadata, e.g.
 *   { "answer": Answer, "observability": Observability }
 * The client normalises both shapes into { data, observability }.
 */

import type {
  Answer,
  ControlChecklist,
  ControlGap,
  ControlMapping,
  EvidencePack,
  FreshnessRecord,
  Observability,
  RegulatorQuestion,
  Regulator,
  TestCase,
} from "./types";
import { ConfiguredEmptyError, readEnvSetting } from "./env-setting.mjs";

// The API base is resolved in THREE states, not two.
//
// Reading `process.env.NEXT_PUBLIC_API_BASE || "<loopback default>"` hands a
// variable an operator DELIBERATELY EMPTIED the loopback default. That is a widening: the
// console then talks to a local API instead of the configured one, and `connect-src` is built
// from the same value, so the emptied deployment is byte-identical to one that never configured
// the variable at all. Next inlines NEXT_PUBLIC_* AT BUILD TIME, so the wrong value is frozen
// into the bundle and cannot be corrected by fixing the environment at start-up.
//
// Unset keeps the documented loopback default, which is what a laptop wants. Set-and-empty
// refuses, because an emptied value names nothing and the default is the more permissive branch.
const DEFAULT_API_BASE = "http://localhost:8000";
const API_BASE_SETTING = readEnvSetting(process.env, "NEXT_PUBLIC_API_BASE");
if (API_BASE_SETTING.isConfiguredEmpty) {
  throw new ConfiguredEmptyError(
    "NEXT_PUBLIC_API_BASE is set to an empty value. An emptied variable names nothing, " +
      "so it cannot inherit the unset default (" + DEFAULT_API_BASE + "), which points this " +
      "console at a loopback API and widens connect-src to match. Unset it to take that " +
      "default deliberately, or give it the API origin this deployment should call.",
  );
}
export const API_BASE = (API_BASE_SETTING.hasValue ? API_BASE_SETTING.value : DEFAULT_API_BASE).replace(
  /\/+$/,
  "",
);

export class ApiError extends Error {
  status: number;
  body: string;
  constructor(message: string, status: number, body: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/** A normalised artifact result: the domain payload plus optional observability. */
export interface ArtifactResult<T> {
  data: T;
  observability: Observability | null;
}

/** Filters the left rail can apply to a request. */
export interface AskFilters {
  regulator?: Regulator;
  jurisdiction?: string;
}

// The audit `actor` is deliberately NOT part of any request body: the backend
// resolves it server-side from the verified Principal (IdentityPort) and ignores
// anything a client asserts.
interface AskBody {
  question: string;
  grounding_enabled?: boolean;
  filters?: Record<string, string>;
}

interface UseCaseBody {
  use_case: string;
  filters?: Record<string, string>;
}

// Scope body for the control-mapping routes (/map, /gaps, /evidence-pack).
// `scope` is a regulator id, a notice reference or a use-case phrase; `regulator`
// optionally narrows the requirement set. As with every other route the audit actor
// is resolved server-side and never asserted by the client.
export interface ScopeBody {
  scope: string;
  regulator?: string;
}

// Dev-only identity selection. In LOCAL mode the backend resolves identity from
// the X-Dev-Persona header (seeded personas, no IdP); in secure profiles the header
// is ignored and identity comes from the IAP assertion injected by the platform.
let devPersona = "";

export function setDevPersona(id: string): void {
  devPersona = id;
}

export function getDevPersona(): string {
  return devPersona;
}

function requestHeaders(): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (devPersona) headers["X-Dev-Persona"] = devPersona;
  return headers;
}

async function parseJsonOrThrow(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!res.ok) {
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      detail =
        (parsed && (parsed.detail || parsed.message || parsed.error)) || text;
    } catch {
      /* keep raw text */
    }
    throw new ApiError(
      `${res.status} ${res.statusText}: ${detail || "request failed"}`,
      res.status,
      text,
    );
  }
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    throw new ApiError("Malformed JSON in response", res.status, text);
  }
}

/**
 * Pull the domain payload + observability out of a possibly-wrapped response.
 * `keys` lists the field names under which the payload may be nested.
 */
function unwrap<T>(raw: unknown, keys: string[]): ArtifactResult<T> {
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const obj = raw as Record<string, unknown>;
    const observability =
      (obj.observability as Observability | undefined) ??
      (obj.governance as Observability | undefined) ??
      null;
    for (const k of keys) {
      const candidate = obj[k];
      // Artifact payloads are objects or arrays. A bare Answer also has an `answer` STRING
      // field, so treating every matching key as an envelope unwraps the response to plain text
      // and crashes the renderer when it reads citations. Only structured values are envelopes.
      if (candidate !== null && typeof candidate === "object") {
        return { data: candidate as T, observability };
      }
    }
    // No nesting key matched: the object itself is the payload. Still surface
    // any sibling observability block if one was present.
    return { data: raw as T, observability };
  }
  return { data: raw as T, observability: null };
}

function withTimeout(signal?: AbortSignal, ms = 60_000): AbortSignal {
  if (signal) return signal;
  const ctor = AbortSignal as typeof AbortSignal & {
    timeout?: (ms: number) => AbortSignal;
  };
  if (typeof ctor.timeout === "function") {
    return ctor.timeout(ms);
  }
  return new AbortController().signal;
}

// --------------------------------------------------------------------------- //
// Endpoints
// --------------------------------------------------------------------------- //
export async function ask(
  body: AskBody,
  signal?: AbortSignal,
): Promise<ArtifactResult<Answer>> {
  const res = await fetch(`${API_BASE}/ask`, {
    method: "POST",
    headers: requestHeaders(),
    body: JSON.stringify(body),
    signal: withTimeout(signal),
  });
  return unwrap<Answer>(await parseJsonOrThrow(res), ["answer", "data"]);
}

export async function checklist(
  body: UseCaseBody,
  signal?: AbortSignal,
): Promise<ArtifactResult<ControlChecklist>> {
  const res = await fetch(`${API_BASE}/checklist`, {
    method: "POST",
    headers: requestHeaders(),
    body: JSON.stringify(body),
    signal: withTimeout(signal),
  });
  return unwrap<ControlChecklist>(await parseJsonOrThrow(res), [
    "checklist",
    "control_checklist",
    "data",
  ]);
}

export async function testcases(
  body: UseCaseBody,
  signal?: AbortSignal,
): Promise<ArtifactResult<TestCase[]>> {
  const res = await fetch(`${API_BASE}/testcases`, {
    method: "POST",
    headers: requestHeaders(),
    body: JSON.stringify(body),
    signal: withTimeout(signal),
  });
  const raw = await parseJsonOrThrow(res);
  if (Array.isArray(raw)) return { data: raw as TestCase[], observability: null };
  return unwrap<TestCase[]>(raw, ["testcases", "test_cases", "items", "data"]);
}

export async function regulatorQuestions(
  body: UseCaseBody,
  signal?: AbortSignal,
): Promise<ArtifactResult<RegulatorQuestion[]>> {
  const res = await fetch(`${API_BASE}/regulator-questions`, {
    method: "POST",
    headers: requestHeaders(),
    body: JSON.stringify(body),
    signal: withTimeout(signal),
  });
  const raw = await parseJsonOrThrow(res);
  if (Array.isArray(raw))
    return { data: raw as RegulatorQuestion[], observability: null };
  return unwrap<RegulatorQuestion[]>(raw, [
    "regulator_questions",
    "questions",
    "items",
    "data",
  ]);
}

// --------------------------------------------------------------------------- //
// Control-mapping routes (merged C2 toolkit): /map, /gaps, /evidence-pack
// --------------------------------------------------------------------------- //
async function postScope(
  path: string,
  body: ScopeBody,
  signal?: AbortSignal,
): Promise<unknown> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: requestHeaders(),
    body: JSON.stringify(body),
    signal: withTimeout(signal),
  });
  return parseJsonOrThrow(res);
}

/** POST /map -> { scope, mappings: ControlMapping[] }. */
export async function mapControls(
  body: ScopeBody,
  signal?: AbortSignal,
): Promise<ControlMapping[]> {
  const raw = (await postScope("/map", body, signal)) as {
    mappings?: ControlMapping[];
  } | null;
  return raw?.mappings ?? [];
}

/** POST /gaps -> { scope, gaps: ControlGap[] }. */
export async function controlGaps(
  body: ScopeBody,
  signal?: AbortSignal,
): Promise<ControlGap[]> {
  const raw = (await postScope("/gaps", body, signal)) as {
    gaps?: ControlGap[];
  } | null;
  return raw?.gaps ?? [];
}

/** POST /evidence-pack -> EvidencePack (always sent for human review). */
export async function evidencePack(
  body: ScopeBody,
  signal?: AbortSignal,
): Promise<EvidencePack> {
  return (await postScope("/evidence-pack", body, signal)) as EvidencePack;
}

export async function corpusStatus(
  signal?: AbortSignal,
): Promise<FreshnessRecord[]> {
  const res = await fetch(`${API_BASE}/corpus/status`, {
    method: "GET",
    headers: requestHeaders(),
    signal: withTimeout(signal, 20_000),
  });
  const raw = await parseJsonOrThrow(res);
  if (Array.isArray(raw)) return raw as FreshnessRecord[];
  if (raw && typeof raw === "object") {
    const obj = raw as Record<string, unknown>;
    for (const k of ["records", "sources", "items", "corpus", "data"]) {
      if (Array.isArray(obj[k])) return obj[k] as FreshnessRecord[];
    }
  }
  return [];
}

/** Where the corpus upload contract can be downloaded (CSV, one row per field). */
export const CORPUS_UPLOAD_TEMPLATE_URL = `${API_BASE}/corpus/upload-template`;

export interface CorpusUploadMeta {
  title: string;
  regulator?: string;
  jurisdiction?: string;
  version?: string;
  url?: string;
}

export interface CorpusUploadResult {
  source_id: string;
  ok: boolean;
  chunks: number;
  detail: string;
}

/** Ingest one document (PDF or text) into the corpus with page-level citations. */
export async function corpusUpload(
  file: File,
  meta: CorpusUploadMeta,
  signal?: AbortSignal,
): Promise<CorpusUploadResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("title", meta.title);
  if (meta.regulator) form.append("regulator", meta.regulator);
  if (meta.jurisdiction) form.append("jurisdiction", meta.jurisdiction);
  if (meta.version) form.append("version", meta.version);
  if (meta.url) form.append("url", meta.url);
  // No Content-Type header: the browser sets the multipart boundary itself.
  const headers: Record<string, string> = {};
  if (devPersona) headers["X-Dev-Persona"] = devPersona;
  const res = await fetch(`${API_BASE}/corpus/documents`, {
    method: "POST",
    headers,
    body: form,
    signal: withTimeout(signal, 60_000),
  });
  return (await parseJsonOrThrow(res)) as CorpusUploadResult;
}

export interface HealthStatus {
  ok: boolean;
  /** Active backend profile ("local" | "gcp" | "platform" | "onprem" | ""). */
  profile: string;
  raw: unknown;
}

export async function healthz(signal?: AbortSignal): Promise<HealthStatus> {
  try {
    const res = await fetch(`${API_BASE}/healthz`, {
      method: "GET",
      signal: withTimeout(signal, 8_000),
    });
    if (!res.ok) return { ok: false, profile: "", raw: { status: res.status } };
    const raw = await res.json().catch(() => ({}));
    const body =
      raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
    const status = body.status;
    const profile = typeof body.profile === "string" ? body.profile : "";
    return {
      ok: status === undefined ? res.ok : status === "ok",
      profile,
      raw,
    };
  } catch (e) {
    return { ok: false, profile: "", raw: { error: String(e) } };
  }
}

/** A seeded local-profile dev persona (id drives the X-Dev-Persona header). */
export interface Persona {
  id: string;
  subject: string;
  tenant: string;
  principals: string;
}

/**
 * List the seeded dev personas for the local persona picker. Secure profiles
 * return an empty list (identity comes from the IAP assertion, not a header).
 */
export async function listPersonas(signal?: AbortSignal): Promise<Persona[]> {
  const res = await fetch(`${API_BASE}/personas`, {
    method: "GET",
    signal: withTimeout(signal, 8_000),
  });
  const raw = await parseJsonOrThrow(res);
  return Array.isArray(raw) ? (raw as Persona[]) : [];
}

export const api = {
  ask,
  checklist,
  testcases,
  regulatorQuestions,
  mapControls,
  controlGaps,
  evidencePack,
  corpusStatus,
  corpusUpload,
  healthz,
  listPersonas,
};
