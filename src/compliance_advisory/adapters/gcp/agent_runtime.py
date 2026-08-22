"""Agent Runtime adapter — the hosted reasoning engine for system C1.

Wraps a deployed **Agent Runtime** resource (formerly Vertex AI Agent Engine; the
managed ``reasoningEngine`` on the Gemini Enterprise Agent Platform) and speaks the
domain ``AgentRuntimePort``. The agent itself is an ADK app packaged and deployed to
Agent Runtime out of band; this adapter only *invokes* the deployed resource named by
``settings.agent_engine.resource_name`` and maps its reply onto a domain ``Answer``.

All Google Cloud SDK imports are lazy (inside ``__init__`` / methods) so the on-prem and
test profiles import this module with no Vertex AI SDK installed.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import Answer, Citation, Jurisdiction, Regulator, Session


class AgentRuntimeAdapter:
    """Invoke a deployed Agent Runtime (``reasoningEngine``) resource via ``vertexai``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._resource_name = settings.agent_engine.resource_name
        # Cached handle to the remote engine; built lazily on first use so module
        # import never requires the Vertex AI SDK (on-prem/test profile parity).
        self._engine: Any | None = None

    # ------------------------------------------------------------------ #
    # Lazy SDK plumbing
    # ------------------------------------------------------------------ #
    def _agent_engine(self) -> Any:
        """Return (and cache) the deployed Agent Runtime handle.

        Uses the ``vertexai`` Agent Engines surface. The newer client style is
        ``vertexai.Client(project, location).agent_engines.get(name)``; the established
        module style is ``from vertexai import agent_engines; agent_engines.get(name)``.
        We prefer the client style and fall back to the module helper.
        """
        if self._engine is not None:
            return self._engine
        if not self._resource_name:
            raise RuntimeError(
                "agent_engine.resource_name is not set; deploy the Agent Runtime "
                "resource and configure its reasoningEngine id before querying."
            )
        import vertexai  # lazy: Vertex AI SDK only on the gcp profile

        try:
            # verify: https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview
            client = vertexai.Client(
                project=self._settings.project_id,
                location=self._settings.region,
            )
            self._engine = client.agent_engines.get(name=self._resource_name)
        except AttributeError:
            from vertexai import agent_engines  # type: ignore[attr-defined]

            vertexai.init(
                project=self._settings.project_id,
                location=self._settings.region,
            )
            self._engine = agent_engines.get(self._resource_name)
        return self._engine

    # ------------------------------------------------------------------ #
    # AgentRuntimePort
    # ------------------------------------------------------------------ #
    def query(self, session: Session, message: str) -> Answer:
        """Send ``message`` to the hosted agent in ``session`` and parse the reply.

        ``session.user_id`` is threaded through as the engine ``user_id`` and
        ``session.id`` as the ``session_id`` so the managed Sessions service keeps
        per-case conversation state and the audit trail stays joinable.
        """
        engine = self._agent_engine()
        raw = self._invoke(engine, session, message)
        text, citations = self._parse_reply(raw)
        return Answer(question=message, answer=text, citations=tuple(citations))

    def health(self) -> bool:
        """Liveness check: a ``get`` on the deployed resource must succeed."""
        try:
            self._agent_engine()
        except Exception:  # noqa: BLE001 — health probe must never raise
            return False
        return True

    # ------------------------------------------------------------------ #
    # Invocation + reply parsing
    # ------------------------------------------------------------------ #
    def _invoke(self, engine: Any, session: Session, message: str) -> Any:
        """Call the engine's ``query`` (preferred) or drain ``stream_query``.

        ADK apps deployed to Agent Runtime expose a ``query`` method and a
        ``stream_query`` generator. We pass the message and the session identity; the
        exact kwarg names track the deployed app's registered operation schema.
        """
        # verify: https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/use
        kwargs = {
            "message": message,
            "user_id": session.user_id,
            "session_id": session.id,
        }
        if hasattr(engine, "query"):
            return engine.query(**kwargs)
        if hasattr(engine, "stream_query"):
            chunks = list(engine.stream_query(**kwargs))
            return chunks[-1] if chunks else {}
        raise RuntimeError(
            "Deployed Agent Runtime resource exposes neither 'query' nor "
            "'stream_query'; cannot invoke the agent."
        )

    def _parse_reply(self, raw: Any) -> tuple[str, list[Citation]]:
        """Best-effort mapping of the engine reply onto (text, citations).

        Agent Runtime replies vary by the deployed app: a dict with an ``output`` /
        ``text`` field, an ADK event-style dict, or a plain string. Citations are taken
        only if the agent emitted structured ``citations``; otherwise we return none
        (the domain pipeline keeps page-level provenance on the retrieval path).
        """
        text = self._extract_text(raw)
        citations = self._extract_citations(raw)
        return text, citations

    @staticmethod
    def _extract_text(raw: Any) -> str:
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
            for key in ("output", "text", "response", "answer", "content"):
                value = raw.get(key)
                if isinstance(value, str) and value:
                    return value
            # ADK content-parts shape: {"content": {"parts": [{"text": "..."}]}}
            content = raw.get("content")
            if isinstance(content, dict):
                parts = content.get("parts") or []
                joined = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
                if joined:
                    return joined
        return str(raw) if raw is not None else ""

    @staticmethod
    def _extract_citations(raw: Any) -> list[Citation]:
        if not isinstance(raw, dict):
            return []
        items = raw.get("citations")
        if not isinstance(items, list):
            return []
        citations: list[Citation] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            citations.append(
                Citation(
                    source_id=str(item.get("source_id", "")),
                    regulator=_as_regulator(item.get("regulator")),
                    jurisdiction=_as_jurisdiction(item.get("jurisdiction")),
                    title=str(item.get("title", "")),
                    url=str(item.get("url", "")),
                    version=str(item.get("version", "unknown")),
                    page=_as_int(item.get("page")),
                    snippet=str(item.get("snippet", "")),
                    score=_as_float(item.get("score")),
                )
            )
        return citations


def _as_regulator(value: Any) -> Regulator:
    try:
        return Regulator(str(value))
    except ValueError:
        return Regulator.CROSS


def _as_jurisdiction(value: Any) -> Jurisdiction:
    try:
        return Jurisdiction(str(value))
    except ValueError:
        return Jurisdiction.GLOBAL


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
