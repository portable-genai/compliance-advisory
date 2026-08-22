"""CI gates may not be gated on a hardcoded calendar date.

The defect this pins down: the ``npm audit`` step carried a "TEMPORARY: expires
2026-08-06" exception implemented as ``[[ "$(date -u +%F)" > "2026-08-06" ]]``. That shape
rots in two directions and both are silent. Before the date it downgrades a hard gate to an
allowlist nobody re-reads; after the date it is dead code that survives only because the
finding it excepted has moved on. The exception here was never even load-bearing: the
advisory it named had left the report, while the finding that actually failed the build was
never on its allowlist.

So the rule is structural rather than advisory: no workflow step may compare a hardcoded
``YYYY-MM-DD`` literal against the clock. A gate is either enforced or removed; "enforced
until a date" is neither.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# A calendar date written into the file, e.g. 2026-08-06.
_DATE_LITERAL = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# Anything that reads the current date at run time: `date -u +%F`, $(date ...), `date +%s`.
_CLOCK_READ = re.compile(r"(?<![\w-])date\s+[-+]")


def _workflow_files() -> list[Path]:
    return sorted(p for p in _WORKFLOWS.iterdir() if p.suffix in {".yaml", ".yml"})


def _run_scripts(document: object) -> list[str]:
    """Every ``run:`` script and every ``if:`` expression anywhere in the workflow."""
    found: list[str] = []
    if isinstance(document, dict):
        for key, value in document.items():
            if key in {"run", "if"} and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_run_scripts(value))
    elif isinstance(document, list):
        for item in document:
            found.extend(_run_scripts(item))
    return found


def test_workflows_directory_is_not_empty() -> None:
    """A checker that silently scans nothing is a checker that can never go red."""
    assert _workflow_files(), f"no workflows found under {_WORKFLOWS}"


@pytest.mark.parametrize("workflow", _workflow_files(), ids=lambda p: p.name)
def test_no_date_literal_conditional(workflow: Path) -> None:
    scripts = _run_scripts(yaml.safe_load(workflow.read_text(encoding="utf-8")))
    offenders: list[str] = []
    for script in scripts:
        if not _CLOCK_READ.search(script):
            continue
        for line_number, line in enumerate(script.splitlines(), start=1):
            if _DATE_LITERAL.search(line):
                offenders.append(f"  line {line_number} of a run/if block: {line.strip()}")
        # The literal and the clock read may sit on different lines of the same script.
        if not offenders and _DATE_LITERAL.search(script):
            offenders.append(f"  a run/if block reads the clock and hardcodes a date:\n{script}")
    assert not offenders, (
        f"{workflow.name} gates on a hardcoded calendar date:\n"
        + "\n".join(offenders)
        + "\n\nA CI gate is either enforced or removed. Delete the expiry conditional and fix "
        "the underlying finding instead of dating around it."
    )
