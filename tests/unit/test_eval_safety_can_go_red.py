"""The strictest safety metric must detect a planted PII regression."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def _gate() -> Any:
    path = Path(__file__).resolve().parents[2] / "eval" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("rsk1_eval_safety", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_redactor_probe_passes_the_strictest_gate() -> None:
    gate = _gate()
    assert gate.THRESHOLDS["safety"] == max(gate.THRESHOLDS.values())
    assert gate.runtime_pii_safety_probe() == 1.0


def test_independent_planted_literal_oracle_can_go_red() -> None:
    gate = _gate()
    planted = ("S1234567A", "jane.doe@example.com")
    assert gate.score_pii_surface_safety("[NRIC], [EMAIL]", planted) == 1.0
    assert gate.score_pii_surface_safety("leaked S1234567A", planted) == 0.0
