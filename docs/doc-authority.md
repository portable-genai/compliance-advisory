# Documentation authority

When documents conflict, use this order:

1. `SPEC.md` owns product and behavioral contracts.
2. `ARCHITECTURE.md` owns component boundaries and runtime flows.
3. `COMPLIANCE.md` owns control mappings and adopter evidence obligations.
4. `README.md` owns the concise operator and adopter entry point.
5. `docs/` supplies procedures and elaboration without redefining higher-level facts.

`docs/practices-audit.md` owns audit verdicts. A lower
document may add detail but must not contradict a higher one. Stale prose is a defect and must be
updated in the same change as the implementation.
