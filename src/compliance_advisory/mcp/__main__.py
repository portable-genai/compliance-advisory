"""Serve C1's governed tool catalog over MCP 2026-07-28 on stdio.

The actor is the audited caller and this transport verifies no end user, so it is read from the
environment and recorded as a SERVICE caller. There is deliberately no default that looks like a
person: an unset variable produces ``svc:unattributed``, which is honest and greppable in the
trail rather than a name that would make an unattributed call look attributed.

The read goes through ``setting_or_default`` rather than ``os.environ.get`` so it keeps the
three states this repository requires everywhere. An operator who EMPTIES the variable has said
something different from one who never set it, and inheriting the documented default there would
silently attribute their calls to the fallback identity. The repo's own guard caught exactly
that when this module first used a two-state read.
"""

from __future__ import annotations

import sys

from hex_service_kit.mcpserve import run_stdio

from ..envread import setting_or_default
from .server import build_server


def main() -> int:
    actor = setting_or_default("COMPLIANCE_MCP_ACTOR", "svc:unattributed")
    run_stdio(build_server(actor=actor))
    return 0


if __name__ == "__main__":
    sys.exit(main())
