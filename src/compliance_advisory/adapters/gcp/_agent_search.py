"""The Discovery Engine host that serves one Agent Search location.

Agent Search serves exactly three locations, and they are not addressed the same way. ``us``
and ``eu`` are reached on a location-prefixed host (``us-discoveryengine.googleapis.com``);
``global`` is reached on the bare host, and ``global-discoveryengine.googleapis.com`` does not
exist. Both adapters used to build ``f"{location}-discoveryengine.googleapis.com"`` for every
location, so the default ``global`` setting named a host that never resolves, and the failure
would only have appeared on the first live retrieval.

A location outside the three is refused here, at construction, for the same reason
``infra/terraform/variables.tf`` refuses it at plan: no host serves it.
"""

from __future__ import annotations

#: The only locations Agent Search serves. Mirrors ``var.agent_search_location``'s validation.
SERVED_LOCATIONS = frozenset({"global", "us", "eu"})


def api_endpoint(location: str) -> str:
    """The API host for ``location``: the bare host for ``global``, prefixed for ``us``/``eu``."""
    if location not in SERVED_LOCATIONS:
        served = ", ".join(sorted(SERVED_LOCATIONS))
        raise ValueError(
            f"Agent Search does not serve {location!r}; it serves {served} and no Cloud region. "
            "Set COMPLIANCE_AGENT_SEARCH_LOCATION to the location the data store was created in."
        )
    if location == "global":
        return "discoveryengine.googleapis.com"
    return f"{location}-discoveryengine.googleapis.com"
