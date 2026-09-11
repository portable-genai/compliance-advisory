"""The Agent Search adapters call a host that exists for the configured location.

Both adapters built ``f"{location}-discoveryengine.googleapis.com"``, so the default ``global``
location named ``global-discoveryengine.googleapis.com``, which does not resolve. Nothing
offline could notice: the host is only dialled on the first live retrieval. And the location
was a literal in ``config/settings.yaml``, so a deployment whose residency policy refuses
``global`` could not point the API at the ``us`` store it had to create.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from compliance_advisory.adapters.gcp._agent_search import api_endpoint
from compliance_advisory.adapters.gcp.agent_search_ingestion import AgentSearchIngestionAdapter
from compliance_advisory.adapters.gcp.agent_search_retrieval import AgentSearchRetrievalAdapter
from compliance_advisory.config import Settings
from compliance_advisory.envread import ConfiguredEmptyError


@pytest.mark.parametrize(
    ("location", "host"),
    [
        ("global", "discoveryengine.googleapis.com"),
        ("us", "us-discoveryengine.googleapis.com"),
        ("eu", "eu-discoveryengine.googleapis.com"),
    ],
)
@pytest.mark.parametrize(
    "adapter_class", [AgentSearchRetrievalAdapter, AgentSearchIngestionAdapter]
)
def test_each_served_location_reaches_its_own_host(
    adapter_class: type, location: str, host: str
) -> None:
    base = Settings.load("config/settings.yaml")
    settings = replace(base, agent_search=replace(base.agent_search, location=location))
    assert adapter_class(settings)._endpoint == host  # noqa: SLF001


@pytest.mark.parametrize("location", ["asia-southeast1", "", "US"])
def test_a_location_agent_search_does_not_serve_is_refused(location: str) -> None:
    with pytest.raises(ValueError, match="does not serve"):
        api_endpoint(location)


def test_the_location_is_a_three_state_deployment_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPLIANCE_AGENT_SEARCH_LOCATION", raising=False)
    assert Settings.load("config/settings.yaml").agent_search.location == "global"

    monkeypatch.setenv("COMPLIANCE_AGENT_SEARCH_LOCATION", "us")
    assert Settings.load("config/settings.yaml").agent_search.location == "us"

    monkeypatch.setenv("COMPLIANCE_AGENT_SEARCH_LOCATION", "")
    with pytest.raises(ConfiguredEmptyError):
        Settings.load("config/settings.yaml")
