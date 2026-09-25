"""``live`` profile adapters: the real regulatory corpus, answered by the local model.

The live profile is the laptop lane (owner decision 2026-09-23, amending 2026-08-30): the
CORE runs on the fleet's local open-weight model through the shared
``hex_service_kit.localmodel`` client, and retrieval serves only the REAL regulator
instruments ingested by ``pipelines.refresh_job``, never the fictional built-in seed.
Everything else reuses the SDK-free local adapters, so custody of the index and the audit
trail stays on the machine.

Gemini appears in exactly one place: the OPTIONAL web-grounding leg
(:mod:`.grounding`), and only while ``COMPLIANCE_GROUNDING_ENABLED`` is on. With it off,
which is the default here, the profile needs no cloud credentials at all. The UI provenance
banner states that the runtime is local and names the local model that answers.
"""
