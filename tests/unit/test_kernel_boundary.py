"""A7: the kernel/vertical split is a real dependency direction, not a label.

The check that matters is not "a module named ``kernel`` exists". Before this file,
compliance-advisory had no kernel module at all and ARCHITECTURE.md section 1.1 plus a static doc
test were the whole of the boundary: comment banners inside a single mixed ``domain/models.py``. A
fork that wanted only the vertical-neutral envelopes still had to import the compliance artifacts it
was about to rewrite, so the boundary could not be enforced by anything.

So the primary assertion here is EXECUTED, not read: a fresh interpreter imports
``compliance_advisory.domain.kernel`` and reports which ``compliance_advisory`` modules
ended up in ``sys.modules``. Against a kernel that merely re-exported from the vertical
module, that subprocess prints ``compliance_advisory.domain.models`` and this test is
RED. The AST scan and the identity checks below are backstops, not the proof.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from compliance_advisory.domain import kernel, models

SRC = Path(__file__).resolve().parents[2] / "src"
KERNEL_PATH = SRC / "compliance_advisory" / "domain" / "kernel.py"
PACKAGE = "compliance_advisory"

# The vertical-neutral machinery A7 requires a fork to inherit untouched. The publisher
# taxonomy (Regulator / Jurisdiction / DocType / RegSource) is in this list because the
# citation, source-registry and freshness envelopes are TYPED with it: leaving it behind
# would have recreated the very import the split exists to remove. See ARCHITECTURE.md 1.1.
KERNEL_NAMES = (
    "REGULATOR_JURISDICTION",
    "AgentCard",
    "AgentSkill",
    "AuditEvent",
    "Citation",
    "Decision",
    "Direction",
    "DocType",
    "EvalMetricResult",
    "EvalReport",
    "FetchedDocument",
    "FreshnessRecord",
    "FreshnessStatus",
    "GuardrailCategory",
    "GuardrailFinding",
    "GuardrailVerdict",
    "IngestResult",
    "Jurisdiction",
    "LlmMessage",
    "LlmRequest",
    "LlmResponse",
    "MemoryItem",
    "RedactionFinding",
    "RedactionResult",
    "RegSource",
    "Regulator",
    "RetrievalQuery",
    "RetrievedPassage",
    "Session",
    "Severity",
    "StrEnum",
    "ThinkingLevel",
    "TokenUsage",
    "ToolSpec",
    "WebCitation",
    "utcnow",
)

# The compliance-advisory artifacts a fork rewrites. None of them may live in the kernel.
VERTICAL_NAMES = (
    "Answer",
    "ChecklistItem",
    "ControlChecklist",
    "RegulatorQuestion",
    "TestCase",
)


def _imported_package_modules(module: str) -> list[str]:
    """Import ``module`` in a FRESH interpreter and report what it pulled in."""
    program = (
        "import json, sys\n"
        f"import {module}\n"
        f"print(json.dumps(sorted(m for m in sys.modules if m.startswith('{PACKAGE}'))))\n"
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        cwd=str(SRC),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    modules = json.loads(completed.stdout.strip().splitlines()[-1])
    assert isinstance(modules, list)
    return [str(name) for name in modules]


def test_importing_the_kernel_does_not_import_the_vertical_models() -> None:
    """Executed proof of the dependency direction, in a process of its own."""
    imported = _imported_package_modules(f"{PACKAGE}.domain.kernel")
    assert f"{PACKAGE}.domain.kernel" in imported
    assert f"{PACKAGE}.domain.models" not in imported, (
        "the kernel pulled the vertical model module in; the split is a label, not a "
        f"boundary (imported: {imported})"
    )


def test_the_vertical_models_do_import_the_kernel() -> None:
    """The arrow must exist in the other direction, or nothing is actually shared."""
    imported = _imported_package_modules(f"{PACKAGE}.domain.models")
    assert f"{PACKAGE}.domain.kernel" in imported


def test_kernel_source_has_no_intra_package_imports() -> None:
    """Static backstop: the kernel depends on the stdlib and the commons only."""
    tree = ast.parse(KERNEL_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"kernel makes a relative import of {node.module!r}"
            assert not (node.module or "").startswith(PACKAGE), node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(PACKAGE), alias.name


@pytest.mark.parametrize("name", KERNEL_NAMES)
def test_kernel_names_are_defined_in_the_kernel_and_re_exported(name: str) -> None:
    """Backward-compatible re-exports keep every existing import site working."""
    assert hasattr(kernel, name), f"{name} is not in the kernel"
    assert getattr(models, name) is getattr(kernel, name), (
        f"models.{name} is not the same object as kernel.{name}"
    )


@pytest.mark.parametrize("name", VERTICAL_NAMES)
def test_vertical_artifacts_stay_out_of_the_kernel(name: str) -> None:
    assert hasattr(models, name)
    assert not hasattr(kernel, name), f"{name} is vertical and must not sit in the kernel"
