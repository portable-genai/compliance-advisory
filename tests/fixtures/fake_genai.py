"""A stand-in for the ``google.genai`` SDK, enough to drive the two Gemini adapters offline.

The offline gate installs no cloud SDK, so the Gemini adapters' own mapping code (which
temperature they send, which model they note as having answered, whether they note a search)
could only ever be read, never run. This fake is what lets those tests EXECUTE the real
adapters: :func:`install` puts a ``google.genai.types`` module into ``sys.modules`` for the
duration of one test, and :class:`FakeClient` records every ``generate_content`` call instead
of reaching Gemini.

Every SDK type is a recording namespace, so a test reads exactly the keyword arguments the
adapter built: a ``GenerateContentConfig`` with no ``temperature`` key is one that sent none.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


class Recorded(SimpleNamespace):
    """An SDK value object that keeps the keyword arguments it was built with."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.kwargs = dict(kwargs)


def _types_module() -> ModuleType:
    types = ModuleType("google.genai.types")
    types.Content = Recorded  # type: ignore[attr-defined]
    types.Part = SimpleNamespace(from_text=lambda text: Recorded(text=text))  # type: ignore[attr-defined]
    types.GenerateContentConfig = Recorded  # type: ignore[attr-defined]
    types.ThinkingConfig = Recorded  # type: ignore[attr-defined]
    types.ThinkingLevel = SimpleNamespace(LOW="LOW", HIGH="HIGH")  # type: ignore[attr-defined]
    types.Tool = Recorded  # type: ignore[attr-defined]
    types.GoogleSearch = Recorded  # type: ignore[attr-defined]
    return types


def install(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Make ``from google.genai import types`` resolve to the fake for this test only."""
    types = _types_module()
    genai = ModuleType("google.genai")
    genai.types = types  # type: ignore[attr-defined]
    if "google" not in sys.modules:
        try:
            import google  # noqa: F401 - a namespace package some other dependency ships
        except ImportError:
            google = ModuleType("google")
            google.__path__ = []  # type: ignore[attr-defined]
            monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", types)
    return types


def web_chunk(uri: str, title: str) -> SimpleNamespace:
    """One ``grounding_chunks[]`` entry, as the google_search tool returns it."""
    return SimpleNamespace(web=SimpleNamespace(uri=uri, title=title, domain="example.test"))


class FakeClient:
    """Records each ``models.generate_content`` call and answers with a canned response."""

    def __init__(self, text: str = "{}", chunks: tuple[SimpleNamespace, ...] = ()) -> None:
        self.calls: list[dict[str, Any]] = []
        metadata = SimpleNamespace(grounding_chunks=list(chunks))
        self._response = SimpleNamespace(
            text=text,
            usage_metadata=None,
            candidates=[SimpleNamespace(grounding_metadata=metadata)],
        )
        self.models = SimpleNamespace(generate_content=self._generate_content)

    def _generate_content(self, *, model: str, contents: Any, config: Any) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._response
