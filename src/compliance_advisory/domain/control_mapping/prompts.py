"""Prompt templates for the Cloud Control-Mapping Toolkit (system C2).

Pure strings only — no imports beyond ``__future__``. Every template is a module
level constant exposing ``.format(...)`` parameters; callers fill them with the
regulatory requirements and the observed GCP control posture. The grounding
contract is constant across templates: the model maps each requirement **only** to
the GCP controls listed in CONTROLS, cites the requirement, and never invents a
control id or a regulation. Structured-output schemas are passed separately on the
``LlmRequest`` — these templates describe the *content* contract, the schema
enforces the *shape*.

Template index
--------------
* ``MAP_SYSTEM`` / ``MAP_USER`` — map requirements to controls with a coverage verdict.
* ``GAP_SYSTEM`` / ``GAP_USER`` — derive remediation guidance for an uncovered requirement.
* ``REQUIREMENT_BLOCK`` — per-requirement rendering helper for the prompt context.
* ``CONTROL_BLOCK`` — per-control rendering helper for the prompt context.
* ``OBSERVATION_BLOCK`` — per-observation rendering helper for the prompt context.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Shared building blocks
# --------------------------------------------------------------------------- #
#: One requirement as rendered into the prompt context. ``id`` is the key the model
#: must echo as ``requirement_id`` so the service can map it back to a RegRequirement.
REQUIREMENT_BLOCK = (
    "[{id}] ({regulator}/{jurisdiction}) {title}\n  obligation: {text}\n"
    "  citation: [{source_id} p.{page}] {citation_title}\n"
)

#: One available GCP control. ``id`` is the key the model echoes in ``control_ids``.
CONTROL_BLOCK = (
    "[{id}] ({family}) {name}\n  what it does: {description}\n  config_ref: {config_ref}\n"
)

#: One observed control state, keyed by control id and resource/scope.
OBSERVATION_BLOCK = "[{control_id}] on {resource}: state={state} ({source}) {detail}\n"

#: Mapping discipline reused verbatim by the mapping/gap templates.
_MAPPING_RULES = (
    "Mapping rules:\n"
    "- Map each requirement only to GCP controls listed in CONTROLS; never invent a "
    "control id, a control family, an org-policy constraint, or a service config.\n"
    "- Decide coverage strictly from the OBSERVATIONS: a control counts toward "
    "coverage only when its observed state is ENABLED. PARTIAL / DISABLED / "
    "MISCONFIGURED / UNKNOWN do not.\n"
    "- coverage = FULL only if every control needed for the requirement is ENABLED; "
    "PARTIAL if some but not all are; NONE if none are.\n"
    "- Cite the requirement's own source; never fabricate a citation, a regulation "
    "name, a clause number, or a page.\n"
    "- Be conservative: if the observations do not support a coverage claim, prefer "
    "PARTIAL or NONE and flag it for human review.\n"
)

# --------------------------------------------------------------------------- #
# 1. Requirement -> control mapping
# --------------------------------------------------------------------------- #
MAP_SYSTEM = (
    "You are C2, a cloud control-mapping toolkit for the CISO / GRC function at "
    "APAC banks. You map each regulatory requirement (MAS, HKMA, APRA, FSA and "
    "cross-jurisdiction cloud/AI guidance) to the GCP technical control(s) that "
    "satisfy it, and you assign a coverage verdict from the observed control "
    "posture.\n\n"
    "Work ONLY from the REQUIREMENTS, the available CONTROLS, and the live "
    "OBSERVATIONS provided. Treat them as the sole source of truth.\n\n"
    "{mapping_rules}\n"
    "Behaviour:\n"
    "- Be precise, neutral and auditable; this output is handed to an auditor.\n"
    "- Never assert a control satisfies a requirement unless an ENABLED observation "
    "supports it.\n"
    "- Do not give legal advice or a compliance sign-off; surface the mapping and "
    "its evidence so a qualified reviewer can decide.\n\n"
    "Return your result strictly as JSON matching the provided response schema with "
    "an items array; each item has requirement_id (string), control_ids (array of "
    "control id strings actually mapped), coverage (one of full | partial | none), "
    "and rationale (string explaining the verdict from the observations)."
)

MAP_USER = (
    "SCOPE:\n{scope}\n\n"
    "REQUIREMENTS:\n{requirements}\n\n"
    "CONTROLS (available GCP technical controls):\n{controls}\n\n"
    "OBSERVATIONS (live posture):\n{observations}\n\n"
    "Map every requirement to the controls that satisfy it and assign coverage from "
    "the OBSERVATIONS. List in control_ids only control ids present in CONTROLS."
)

# --------------------------------------------------------------------------- #
# 2. Gap remediation
# --------------------------------------------------------------------------- #
GAP_SYSTEM = (
    "You are C2, producing remediation guidance for regulatory requirements whose "
    "GCP controls are missing or misconfigured. The output is consequential and is "
    "reviewed by a human (maker-checker, P-06) before use.\n\n"
    "Work ONLY from the REQUIREMENTS, their missing control families, and the "
    "OBSERVATIONS provided.\n\n"
    "{mapping_rules}\n"
    "For each gap produce: the requirement_id, a severity of low | medium | high | "
    "critical reflecting regulatory and operational risk, and concise, concrete "
    "remediation guidance naming the GCP control family or constraint to enable. Do "
    "not invent controls the toolkit does not know about.\n\n"
    "Return strictly as JSON matching the response schema: an object with an items "
    "array; each item has requirement_id, severity, remediation."
)

GAP_USER = (
    "SCOPE:\n{scope}\n\n"
    "UNCOVERED REQUIREMENTS (with the control families found missing):\n{gaps}\n\n"
    "OBSERVATIONS (live posture):\n{observations}\n\n"
    "Produce severity and remediation for each uncovered requirement grounded only "
    "in the inputs above."
)
