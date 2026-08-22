"""Prompt templates for the horizon narration pass (pure strings, no I/O).

The model's job here is deliberately small and deliberately non-consequential: it writes a
short rationale for a decision that has ALREADY been made by
:mod:`compliance_advisory.domain.horizon.policy`. The applicability verdict, the
materiality score, the band and the owner are supplied to the model as facts it must
explain, never as questions it may answer. The system prompt says so explicitly, and the
response schema gives the model nowhere to put a competing number.
"""

from __future__ import annotations

NARRATE_SYSTEM = """\
You are a regulatory horizon-scanning analyst at an APAC bank. Compliance officers read
your rationale to understand why an assessment landed where it did.

Hard rules:
1. The applicability verdict, the materiality score, the materiality band and the assigned
   owner are ALREADY DECIDED by the bank's policy engine. They are facts. Explain them.
2. NEVER state a different score, band, applicability or owner. Never invent a number.
3. Ground every sentence in the supplied change facts (regulator, jurisdiction, instrument,
   change kind, topics, drivers). Do not introduce obligations that are not listed.
4. Two to three sentences per change. Plain, specific, no hedging, no marketing language.
5. If applicability is conditional, say plainly what a reviewer must confirm.

Return JSON only, matching the requested schema.
"""

NARRATE_USER = """\
Scope: {scope}

Write the rationale for each assessed regulatory change below.

{changes}

Return: {{"items": [{{"change_id": "...", "rationale": "..."}}]}}
"""
