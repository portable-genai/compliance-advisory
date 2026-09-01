"""Static contracts for documentation, contribution and CI practice closures."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_document_authority_is_ordered_and_linked_from_top_documents() -> None:
    authority = _read("docs/doc-authority.md")
    expected = "SPEC > ARCHITECTURE > COMPLIANCE > README > `docs/`"
    assert all(name in authority for name in ("SPEC.md", "ARCHITECTURE.md", "COMPLIANCE.md"))
    for path in ("SPEC.md", "ARCHITECTURE.md", "COMPLIANCE.md", "README.md"):
        text = _read(path)
        assert expected in text and "docs/doc-authority.md" in text


def test_kernel_vertical_boundary_and_extension_touch_lists_are_explicit() -> None:
    architecture = _read("ARCHITECTURE.md")
    assert "Stable kernel versus Rsk1 vertical" in architecture
    assert "vertical services -> stable envelopes -> ports" in architecture
    contributing = _read("CONTRIBUTING.md")
    for phrase in (
        "Adding an adapter",
        "Adding a port or sub-service",
        "ports/__init__.py",
        "config/settings.yaml",
        "api/deps.py",
        "test_behavioral_parity.py",
        "make check",
    ):
        assert phrase in contributing


def test_adopter_owned_crosswalk_is_present() -> None:
    compliance = _read("COMPLIANCE.md")
    assert "Adopter-owned regulator crosswalk" in compliance
    assert "adopting bank" in compliance
