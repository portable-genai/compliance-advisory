"""RequirementSourcePort adapter — in-process bind to Rsk1's shared reg-KB retrieval.

The POINT of the C2->C1 merge: the control-mapping capability does not run its own
regulatory knowledge base. There is ONE reg KB — Rsk1's retrieval port (the
``compliance-reg-kb`` Agent Search store on the ``gcp`` profile, the SQLite FTS5 corpus
on ``local``, the on-prem placeholder on ``onprem``). This adapter satisfies
:class:`~compliance_advisory.ports.requirements.RequirementSourcePort` by delegating,
**in the same process, with no HTTP hop**, to whichever retrieval adapter the active
profile binds, and projecting each :class:`RetrievedPassage` onto a cited
:class:`RegRequirement`.

This single adapter replaces three retired C2 paths:

* the standalone Gemini File Search store ``control-mapping-reg-kb``,
* the ``RemoteComplianceAdapter`` HTTP hop to C1's ``/ask``,
* the duplicate local FTS5 reg seed.

Because it resolves the retrieval binding from the same ``Settings.adapters`` table the
container uses, it follows the profile automatically: no per-profile requirement-source
adapter is needed, and the on-prem retrieval placeholder's fail-fast behaviour is
inherited unchanged.
"""

from __future__ import annotations

from typing import Any

from ..config import Settings, instantiate
from ..domain.control_mapping.models import RegRequirement
from ..domain.models import RetrievalQuery, RetrievedPassage


class RetrievalRequirementSourceAdapter:
    """Fetch regulatory obligations from Rsk1's shared retrieval port (in-process)."""

    #: How many passages to pull from the reg KB for a scope. The reg KB is global
    #: (obligations are not scoped to a single project), so this bounds recall.
    _TOP_K = 12

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._retrieval: Any | None = None

    # ------------------------------------------------------------------ #
    # Retrieval binding (resolved for the active profile, same as the container)
    # ------------------------------------------------------------------ #
    def _get_retrieval(self) -> Any:
        if self._retrieval is None:
            binding = self._settings.adapters.get("retrieval", {})
            dotted = binding.get(self._settings.profile)
            if not dotted:
                raise KeyError(
                    "No 'retrieval' adapter configured for profile "
                    f"'{self._settings.profile}'; the reg KB is required for control mapping."
                )
            self._retrieval = instantiate(dotted, self._settings)
        return self._retrieval

    # ------------------------------------------------------------------ #
    # RequirementSourcePort
    # ------------------------------------------------------------------ #
    def fetch(self, scope: str, regulator: str | None = None) -> list[RegRequirement]:
        """Return the regulatory obligations for ``scope`` (optionally one regulator).

        Queries the shared reg KB for the obligations relevant to the scope's cloud
        control posture and projects each retrieved, cited passage onto a
        :class:`RegRequirement`. The optional ``regulator`` narrows the query exactly as
        the assistant's own retrieval filters do.
        """
        filters = {"regulator": regulator.strip().upper()} if regulator else {}
        query = RetrievalQuery(
            text=self._build_query(scope, regulator),
            top_k=self._TOP_K,
            filters=filters,
        )
        passages = self._get_retrieval().retrieve(query) or []
        return [self._to_requirement(scope, i, p) for i, p in enumerate(passages)]

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_query(scope: str, regulator: str | None) -> str:
        reg = f" under {regulator}" if regulator else ""
        return (
            f"Regulatory cloud control and security obligations a bank must satisfy for "
            f"'{scope}'{reg}: data residency, encryption, key management, audit logging, "
            "access controls and sovereignty."
        )

    @staticmethod
    def _to_requirement(scope: str, index: int, passage: RetrievedPassage) -> RegRequirement:
        """Project a retrieved, cited passage onto a control-mapping ``RegRequirement``.

        The passage's citation carries the regulator-grade provenance; the passage text
        is the obligation. A stable id keeps each obligation distinct so the mapping
        pipeline can echo it back.
        """
        citation = passage.citation
        rid = f"{citation.source_id or scope}-{citation.page or index + 1}"
        return RegRequirement(
            id=rid,
            regulator=citation.regulator,
            jurisdiction=citation.jurisdiction,
            title=citation.title or scope,
            text=passage.text,
            citation=citation,
        )
