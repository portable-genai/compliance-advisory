"""Live GCP smoke test — deselected in CI via ``-m 'not integration'``.

Requires real Google Cloud credentials and the ``[gcp]`` extra installed. It is
skipped automatically when ``GOOGLE_CLOUD_PROJECT`` is unset, so the default on-prem
/ test profile (no Google Cloud SDK) never executes any of this. It constructs the
managed-service adapters in ``asia-southeast1`` and does one trivial liveness call
per adapter.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("GOOGLE_CLOUD_PROJECT"),
        reason="set GOOGLE_CLOUD_PROJECT (and install the [gcp] extra) to run GCP smoke tests",
    ),
]


@pytest.fixture(scope="module")
def gcp_settings():
    from compliance_advisory.config import Settings

    settings = Settings.load("config/settings.yaml")
    # Force the managed stack regardless of the ambient COMPLIANCE_PROFILE.
    return Settings(
        project_id=os.environ["GOOGLE_CLOUD_PROJECT"],
        region="asia-southeast1",
        profile="gcp",
        kms_key=settings.kms_key,
        grounding_enabled=settings.grounding_enabled,
        models=settings.models,
        agent_search=settings.agent_search,
        alloydb=settings.alloydb,
        model_armor=settings.model_armor,
        dlp=settings.dlp,
        logging=settings.logging,
        agent_engine=settings.agent_engine,
        corpus=settings.corpus,
        adapters=settings.adapters,
    )


@pytest.fixture(scope="module")
def container(gcp_settings):
    from compliance_advisory.config import Container

    return Container(gcp_settings)


def test_region_is_singapore(gcp_settings):
    assert gcp_settings.region == "asia-southeast1"


def test_agent_runtime_health(container):
    runtime = container.agent_runtime
    assert runtime.health() is True


def test_retrieval_liveness(container):
    from compliance_advisory.domain.models import RetrievalQuery

    passages = container.retrieval.retrieve(
        RetrievalQuery(text="MAS cloud outsourcing due diligence", top_k=3)
    )
    assert isinstance(passages, list)


def test_guardrail_liveness(container):
    from compliance_advisory.domain.models import Direction

    verdict = container.guardrail.screen("hello", Direction.INPUT)
    assert verdict.direction is Direction.INPUT


def test_redaction_liveness(container):
    result = container.redaction.redact("Contact me at jane@example.com")
    assert isinstance(result.text, str)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q", "-m", "integration"]))
