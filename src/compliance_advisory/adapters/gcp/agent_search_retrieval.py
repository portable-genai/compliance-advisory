"""Agent Search retrieval adapter (RetrievalPort).

Primary production retrieval backend for C1: **Agent Search** (formerly Vertex AI
Search) on the **Gemini Enterprise Agent Platform**. This adapter calls the
Discovery Engine ``SearchService`` through a **regional** endpoint pinned to
``asia-southeast1`` (Singapore) so that all regulatory-document retrieval stays
in-country for MAS/HKMA/APRA/FSA data-residency requirements.

Each search result is mapped to a domain :class:`RetrievedPassage` carrying a
regulator-grade :class:`Citation`. Page numbers are required for compliance
provenance, so the request asks for extractive segments (which carry a
``pageIdentifier``) plus snippets, and the adapter reads document metadata from
``struct_data`` / ``derived_struct_data``.

All Google Cloud SDK imports are lazy (inside ``__init__`` / methods) so the
on-prem / test profile imports this module without ``google-cloud-discoveryengine``
installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...config import Settings
from ...domain.models import (
    Citation,
    Jurisdiction,
    Regulator,
    RetrievalQuery,
    RetrievedPassage,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from google.cloud import discoveryengine_v1


class AgentSearchRetrievalAdapter:
    """Retrieve grounded passages from Agent Search (Discovery Engine v1)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        cfg = settings.agent_search
        # Region is pinned for residency; Agent Search location maps to the
        # collection/engine location segment of the serving-config resource path.
        self._location = cfg.location
        self._engine_id = cfg.engine_id
        self._serving_config_id = cfg.serving_config
        self._data_store_id = cfg.data_store_id
        # Regional endpoint — the global endpoint gives no residency guarantees.
        self._endpoint = f"{self._location}-discoveryengine.googleapis.com"
        self._client: Any | None = None

    # ------------------------------------------------------------------ #
    # Lazy client construction
    # ------------------------------------------------------------------ #
    def _get_client(self) -> discoveryengine_v1.SearchServiceClient:
        if self._client is None:
            from google.api_core.client_options import ClientOptions
            from google.cloud import discoveryengine_v1

            self._client = discoveryengine_v1.SearchServiceClient(
                client_options=ClientOptions(api_endpoint=self._endpoint),
            )
        return self._client

    def _serving_config_path(self) -> str:
        return (
            f"projects/{self._settings.project_id}"
            f"/locations/{self._location}"
            f"/collections/default_collection"
            f"/engines/{self._engine_id}"
            f"/servingConfigs/{self._serving_config_id}"
        )

    @staticmethod
    def _build_filter(filters: dict[str, str]) -> str:
        """Compose a SearchRequest.filter expression from structured filters.

        Filters are matched against the data-store ``struct_data`` fields
        (e.g. ``regulator``, ``jurisdiction``). The expression uses the
        Agent Search ``field: ANY("value")`` syntax.
        """
        clauses: list[str] = []
        for key, value in filters.items():
            if value:
                clauses.append(f'{key}: ANY("{value}")')
        return " AND ".join(clauses)

    # ------------------------------------------------------------------ #
    # RetrievalPort
    # ------------------------------------------------------------------ #
    def retrieve(self, query: RetrievalQuery) -> list[RetrievedPassage]:
        """Return ranked passages with regulator-grade citations for ``query``."""
        from google.cloud import discoveryengine_v1

        client = self._get_client()

        content_spec = discoveryengine_v1.SearchRequest.ContentSearchSpec(
            extractive_content_spec=(
                discoveryengine_v1.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                    max_extractive_segment_count=max(query.top_k, 1),
                    return_extractive_segment_score=True,
                )
            ),
            snippet_spec=discoveryengine_v1.SearchRequest.ContentSearchSpec.SnippetSpec(
                return_snippet=True,
            ),
        )

        request = discoveryengine_v1.SearchRequest(
            serving_config=self._serving_config_path(),
            query=query.text,
            page_size=query.top_k,
            filter=self._build_filter(query.filters),
            content_search_spec=content_spec,
        )

        response = client.search(request=request)

        passages: list[RetrievedPassage] = []
        for result in response.results:
            passages.extend(self._result_to_passages(result))
        return passages

    # ------------------------------------------------------------------ #
    # Result mapping
    # ------------------------------------------------------------------ #
    def _result_to_passages(self, result: Any) -> list[RetrievedPassage]:
        """Map one Discovery Engine search result to domain passages.

        A single document can yield several extractive segments (each with its
        own page), so this returns a list. Document-level metadata is read from
        ``struct_data`` (publisher fields we ingested) with a fallback to
        ``derived_struct_data`` (fields Agent Search derives, e.g. ``title``,
        ``link``).
        """
        document = result.document
        struct = self._to_dict(getattr(document, "struct_data", None))
        derived = self._to_dict(getattr(document, "derived_struct_data", None))

        source_id = (
            struct.get("source_id") or struct.get("id") or getattr(document, "id", "") or "unknown"
        )
        regulator = self._parse_regulator(struct.get("regulator"))
        jurisdiction = self._parse_jurisdiction(struct.get("jurisdiction"), regulator)
        title = struct.get("title") or derived.get("title") or source_id
        url = struct.get("url") or derived.get("link") or ""
        version = str(struct.get("version") or "unknown")

        segments = derived.get("extractive_segments") or []
        snippet = self._first_snippet(derived)

        if not segments:
            # No extractive segments — still emit one passage from the snippet so
            # the answer remains citable (page unknown).
            citation = Citation(
                source_id=str(source_id),
                regulator=regulator,
                jurisdiction=jurisdiction,
                title=str(title),
                url=str(url),
                version=version,
                page=None,
                snippet=snippet,
            )
            return [RetrievedPassage(text=snippet, citation=citation, score=0.0)]

        passages: list[RetrievedPassage] = []
        for segment in segments:
            seg = self._to_dict(segment)
            text = str(seg.get("content") or "")
            page = self._parse_page(seg.get("pageIdentifier"))
            score = self._parse_score(seg.get("relevanceScore"))
            citation = Citation(
                source_id=str(source_id),
                regulator=regulator,
                jurisdiction=jurisdiction,
                title=str(title),
                url=str(url),
                version=version,
                page=page,
                snippet=text[:280] or snippet,
                score=score,
            )
            passages.append(RetrievedPassage(text=text, citation=citation, score=score or 0.0))
        return passages

    # ------------------------------------------------------------------ #
    # Parsing helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_dict(value: Any) -> dict[str, Any]:
        """Normalise a proto ``Struct`` / mapping into a plain ``dict``."""
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        # proto-plus MapComposite and protobuf Struct are both dict-like.
        try:
            return dict(value)
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _first_snippet(derived: dict[str, Any]) -> str:
        snippets = derived.get("snippets") or []
        for snip in snippets:
            snip_d = AgentSearchRetrievalAdapter._to_dict(snip)
            text = snip_d.get("snippet")
            if text:
                return str(text)
        return ""

    @staticmethod
    def _parse_regulator(value: Any) -> Regulator:
        if value:
            try:
                return Regulator(str(value).upper())
            except ValueError:
                pass
        return Regulator.CROSS

    @staticmethod
    def _parse_jurisdiction(value: Any, regulator: Regulator) -> Jurisdiction:
        from ...domain.models import REGULATOR_JURISDICTION

        if value:
            try:
                return Jurisdiction(str(value).upper())
            except ValueError:
                pass
        return REGULATOR_JURISDICTION.get(regulator, Jurisdiction.GLOBAL)

    @staticmethod
    def _parse_page(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_score(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
