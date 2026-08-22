"""Built-in synthetic regulatory corpus for the ``local`` profile.

A tiny, clearly-fictional set of MAS / HKMA / APRA passages (with page-level
citations) so the local retrieval adapter has something to ground answers on out of
the box, and the end-to-end CLI smoke run returns a real cited artifact with no
external corpus. The text is invented; the source ids / titles are plausible but
fictional and must not be treated as the real instruments.

This mirrors ``tests/fixtures/sample_regs`` so the local adapters and the unit-test
fixtures share one deterministic corpus, but it lives under ``src`` (not ``tests``) so
the shipped package can seed itself without importing the test tree.
"""

from __future__ import annotations

from ...domain.models import (
    Citation,
    Jurisdiction,
    Regulator,
    RetrievedPassage,
)


def _passage(
    *,
    source_id: str,
    regulator: Regulator,
    jurisdiction: Jurisdiction,
    title: str,
    url: str,
    version: str,
    page: int,
    text: str,
    score: float,
) -> RetrievedPassage:
    return RetrievedPassage(
        text=text,
        citation=Citation(
            source_id=source_id,
            regulator=regulator,
            jurisdiction=jurisdiction,
            title=title,
            url=url,
            version=version,
            page=page,
            snippet=text[:120],
            score=score,
        ),
        score=score,
    )


# A small, deterministic corpus. Page numbers are required for compliance provenance.
SEED_PASSAGES: tuple[RetrievedPassage, ...] = (
    _passage(
        source_id="mas-trm-guidelines",
        regulator=Regulator.MAS,
        jurisdiction=Jurisdiction.SG,
        title="MAS Technology Risk Management Guidelines",
        url="https://example.test/mas/trm",
        version="2021",
        page=42,
        text=(
            "A financial institution should conduct due diligence on a cloud service "
            "provider before entering into an outsourcing arrangement, covering data "
            "residency, exit strategy and concentration risk, and retain audit rights."
        ),
        score=0.93,
    ),
    _passage(
        source_id="hkma-cloud-circular",
        regulator=Regulator.HKMA,
        jurisdiction=Jurisdiction.HK,
        title="HKMA Circular on Cloud Computing",
        url="https://example.test/hkma/cloud",
        version="2022",
        page=7,
        text=(
            "Authorized institutions should notify the HKMA of material cloud "
            "outsourcing and retain the ability to access and audit the service "
            "provider's controls on an ongoing basis."
        ),
        score=0.88,
    ),
    _passage(
        source_id="apra-cps-230",
        regulator=Regulator.APRA,
        jurisdiction=Jurisdiction.AU,
        title="APRA CPS 230 Operational Risk Management",
        url="https://example.test/apra/cps230",
        version="2025",
        page=15,
        text=(
            "An APRA-regulated entity must maintain a register of its material service "
            "providers and ensure tolerance levels for disruption to critical "
            "operations are defined, tested and monitored."
        ),
        score=0.82,
    ),
    # The APRA CPS 234 information-security / encryption obligation, an instrument the rest of
    # the C1 corpus does not carry. It is seeded here so the shared reg KB grounds the
    # encryption control mapping as well as the advisory corpus.
    _passage(
        source_id="apra-cps-234",
        regulator=Regulator.APRA,
        jurisdiction=Jurisdiction.AU,
        title="APRA CPS 234 Information Security",
        url="https://example.test/apra/cps234",
        version="2019",
        page=15,
        text=(
            "An APRA-regulated entity must protect its information assets with information "
            "security controls commensurate with their sensitivity and criticality, "
            "including encryption of data at rest and in transit while retaining control of "
            "the encryption keys."
        ),
        score=0.8,
    ),
)
