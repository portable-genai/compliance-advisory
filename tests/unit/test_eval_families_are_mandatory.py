"""A missing golden dataset must fail the gate, never quietly remove metrics from it.

The gate scores three families over three versioned datasets: QA over
``golden_qa.jsonl``, control mapping over ``golden_mappings.jsonl`` and horizon scanning
over ``golden_horizon.jsonl``. The primary dataset already failed hard when absent or
empty (``load_golden`` raises ``SystemExit``). The other two did not: each returned
``[], 0, dataset`` and its four metrics were simply left out of the report, so a rename or
a refactor that moved a file produced a green run over four of twelve gated metrics, with
``mapping_safety`` (0.99, the strictest bar here) among the eight that vanished. Nothing
in the output said a family had gone missing.

These tests pin the fixed behaviour from both ends: the datasets that must exist do exist,
and pointing either family at a path that does not exist exits non-zero with a message
naming the metrics that would have been skipped.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVAL_SCRIPT = _REPO_ROOT / "eval" / "run_eval.py"


def _load_gate() -> Any:
    """Import ``eval/run_eval.py`` as a module (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("compliance_eval_gate", _EVAL_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate() -> Any:
    return _load_gate()


def _unused_result(metric: str, mean: float) -> None:  # pragma: no cover - never called
    raise AssertionError("a metric was scored from a dataset that does not exist")


def test_every_gated_dataset_is_present_in_the_repository(gate: Any) -> None:
    """The three defaults are versioned assets, so their absence is a defect, not a choice."""
    for dataset in (
        gate.DEFAULT_DATASET,
        gate.DEFAULT_MAPPING_DATASET,
        gate.DEFAULT_HORIZON_DATASET,
    ):
        assert dataset.exists(), f"{dataset} is gated but not present"


def test_a_missing_mapping_dataset_exits_instead_of_dropping_four_metrics(
    gate: Any, tmp_path: Path
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        gate.run_mapping_offline({}, _unused_result, dataset=tmp_path / "gone.jsonl")
    message = str(excinfo.value)
    assert "mapping golden dataset is missing" in message
    # The message has to name what stopped being scored, or the operator reads a bare path.
    for metric in gate.MAPPING_THRESHOLDS:
        assert metric in message


def test_a_missing_horizon_dataset_exits_instead_of_dropping_four_metrics(
    gate: Any, tmp_path: Path
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        gate.run_horizon_offline({}, _unused_result, dataset=tmp_path / "gone.jsonl")
    message = str(excinfo.value)
    assert "horizon golden dataset is missing" in message
    for metric in gate.HORIZON_THRESHOLDS:
        assert metric in message


def test_the_strictest_mapping_metric_is_one_of_the_ones_at_risk(gate: Any) -> None:
    """``mapping_safety`` is why this matters: it is the 0.99 bar, and it lived in the
    family that used to disappear."""
    assert gate.MAPPING_THRESHOLDS["mapping_safety"] == 0.99
