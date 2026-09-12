"""The regulatory store's server-added configuration is declared, so a plan never replaces it.

Discovery Engine attaches a ``document_processing_config`` to every content store when it
creates one. Terraform does not know that, so a configuration that omits the block reads the
live one as a removal, and removing it is a **replacing** change: the store is destroyed and
recreated, taking the indexed corpus with it.

This is not hypothetical here, and it is not hypothetical in the fleet:

* on 2026-09-12 the first plan taken against this stack's own freshly created store, for an
  unrelated Artifact Registry addition, reported ``google_discovery_engine_data_store.reg_kb
  must be replaced``, with ``document_processing_config { # forces replacement`` as the cause.
  The store was empty that day, so the cost would have been nothing; after the first corpus
  refresh the same plan destroys the whole regulatory index;
* ``cdd-sow-research`` found the identical defect on 2026-08-28 against its case knowledge base,
  and it blocked every apply of that stack until the block was declared.

Watched failing first: deleting the declaration from a copy of ``agent_search.tf`` turns this
test red, which is the only thing standing between a future edit and a silent corpus loss, since
the plan that reveals it runs with credentials and therefore never in the offline gate.
"""

from __future__ import annotations

import re
from pathlib import Path

_STORE = (
    Path(__file__).resolve().parents[2] / "infra" / "terraform" / "agent_search.tf"
).read_text(encoding="utf-8")


def _resource_block(text: str, kind: str, name: str) -> str:
    start = text.find(f'resource "{kind}" "{name}" {{')
    assert start != -1, f"agent_search.tf declares no {kind}.{name}"
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"unterminated block {kind}.{name}")


def test_the_data_store_declares_the_processing_config_the_api_adds() -> None:
    block = _resource_block(_STORE, "google_discovery_engine_data_store", "reg_kb")
    assert "document_processing_config" in block, (
        "the data store must declare document_processing_config. The API adds one at create "
        "time, and an undeclared block reads as a removal, which FORCES REPLACEMENT of the "
        "store and destroys the indexed regulatory corpus with it"
    )
    assert re.search(r"default_parsing_config\s*\{\s*digital_parsing_config\s*\{\s*\}", block), (
        "the declared block must match what the API creates: a default_parsing_config holding "
        "an empty digital_parsing_config. A different shape is still a diff, and a diff here "
        "still replaces the store"
    )
