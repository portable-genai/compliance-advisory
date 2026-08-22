"""RegSourceCatalogPort adapter — the in-repo regulatory source registry.

Horizon scanning needs the regulator, jurisdiction, document type and topics behind each
freshness-ledger row. Those already live in ONE place: the registry YAML the corpus
pipeline fetches from (``settings.corpus.registry_path``). This adapter reads that same
file, so there is no second source-of-truth for what a regulatory instrument IS.

The registry is a repo-local file, not a managed service, so a single class serves every
profile (the same pattern as
:class:`~compliance_advisory.adapters.requirements.RetrievalRequirementSourceAdapter`):
there is nothing cloud-specific to place an on-prem placeholder in front of, and pinning
the same adapter across profiles is what keeps the horizon diff identical offline and in
production. The parse result is cached per instance because the registry is immutable for
the lifetime of a process.
"""

from __future__ import annotations

from ..config import Settings
from ..domain.models import RegSource


class RegistrySourceCatalogAdapter:
    """Serve :class:`RegSource` metadata from the corpus source registry."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sources: list[RegSource] | None = None
        self._by_id: dict[str, RegSource] = {}

    # ------------------------------------------------------------------ #
    # RegSourceCatalogPort
    # ------------------------------------------------------------------ #
    def sources(self) -> list[RegSource]:
        """Every registered regulatory source, parsed once per adapter instance."""
        if self._sources is None:
            self._sources = self._load()
            self._by_id = {source.id: source for source in self._sources}
        return list(self._sources)

    def get(self, source_id: str) -> RegSource | None:
        self.sources()
        return self._by_id.get(source_id)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _load(self) -> list[RegSource]:
        """Parse the registry, degrading to an empty catalog if it is absent.

        An absent registry is not fatal: detection simply reports no changes (an
        unregistered ledger row carries no provenance and is skipped by design), which is
        strictly safer than assessing a change on guessed regulator metadata.
        """
        from ..pipelines.fetch import load_registry  # local import: keeps this module light

        try:
            return load_registry(self._settings.corpus.registry_path)
        except (OSError, ValueError):
            return []
