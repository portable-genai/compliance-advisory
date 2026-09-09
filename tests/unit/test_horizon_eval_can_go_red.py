"""The horizon eval metrics must be able to FAIL (systemic finding 8).

A metric that re-reads the product's own output cannot go red and proves nothing. The
horizon metrics score the service's decisions against the golden set's independent
``expected_outcome``, and this test gates that property directly with
``agent_eval_kit.assert_each_can_go_red``: per golden row, the real assessment must PASS
and a degraded assessment must FAIL.

It also pins the oracle itself: the golden expectations must not silently become whatever
the engine currently produces, so a real service run is compared with the dataset.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agent_eval_kit import assert_each_can_go_red

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVAL_SCRIPT = _REPO_ROOT / "eval" / "run_eval.py"
_DATASET = _REPO_ROOT / "eval" / "datasets" / "golden_horizon.jsonl"

#: The metrics this suite plants a red case for. Names only: the gate's own HORIZON_SCORERS map
#: supplies the scorer and the rubrics supply the bar, so neither is restated here.
_SCORERS: tuple[str, ...] = (
    "horizon_applicability_accuracy",
    "horizon_materiality_accuracy",
    "horizon_routing_accuracy",
    "horizon_citation_accuracy",
)


def _bars(gate: Any) -> dict[str, float]:
    """The bars the GATE enforces, read from the rubrics it reads.

    This was a literal dict here, held equal to a literal dict in the gate by a test below.
    Both were stale: the rubric files had said 1.00 for three of these for some time, and the
    two copies agreed with each other and with nothing that ran. Two homes for a number is a
    duplication problem; two homes that agree with each other and not with the artifact is a
    test that certifies the drift.
    """
    return {metric: gate.load_thresholds_from_rubrics()[metric] for metric in gate.SCORED_HORIZON}


def _load_gate() -> Any:
    """Import ``eval/run_eval.py`` as a module (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("compliance_eval_gate", _EVAL_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before executing: the gate defines dataclasses whose string annotations are
    # resolved through ``sys.modules[cls.__module__]`` at class-creation time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate() -> Any:
    return _load_gate()


@pytest.fixture(scope="module")
def assessed(gate: Any) -> list[tuple[Any, Any]]:
    """The REAL service's assessment for every golden row, paired with its expectation."""
    examples = gate.load_golden_horizon(_DATASET)
    pairs = []
    for example in examples:
        assessment = gate.assess_example(gate._make_horizon_service(example), example)
        assert assessment is not None, f"the service produced no assessment for {example.id}"
        pairs.append((assessment, example))
    return pairs


# --------------------------------------------------------------------------- #
# Degradations: one per metric, each breaking exactly what its metric measures
# --------------------------------------------------------------------------- #
def _degrade_applicability(assessment: Any, gate: Any) -> Any:
    from compliance_advisory.domain.horizon import Applicability

    wrong = (
        Applicability.NOT_APPLICABLE
        if assessment.applicability is not Applicability.NOT_APPLICABLE
        else Applicability.APPLICABLE
    )
    return replace(assessment, applicability=wrong)


def _degrade_materiality(assessment: Any, gate: Any) -> Any:
    from compliance_advisory.domain.horizon import MATERIALITY_BAND_ORDER

    current = MATERIALITY_BAND_ORDER.index(assessment.materiality_band)
    wrong = MATERIALITY_BAND_ORDER[(current + 1) % len(MATERIALITY_BAND_ORDER)]
    return replace(assessment, materiality_band=wrong)


def _degrade_routing(assessment: Any, gate: Any) -> Any:
    owner = assessment.owner
    wrong = replace(owner, owner="unassigned-queue") if owner is not None else None
    return replace(assessment, owner=wrong)


def _degrade_citation(assessment: Any, gate: Any) -> Any:
    """The defect this metric exists to catch: a decision with no provenance."""
    return replace(assessment, citations=())


_DEGRADATIONS = {
    "horizon_applicability_accuracy": _degrade_applicability,
    "horizon_materiality_accuracy": _degrade_materiality,
    "horizon_routing_accuracy": _degrade_routing,
    "horizon_citation_accuracy": _degrade_citation,
}


@pytest.mark.parametrize("metric", sorted(_SCORERS))
def test_each_horizon_metric_can_go_red(
    metric: str, gate: Any, assessed: list[tuple[Any, Any]]
) -> None:
    """Per golden row: the real assessment passes and a degraded one fails."""
    scorer = gate.HORIZON_SCORERS[metric]
    degrade = _DEGRADATIONS[metric]
    cases = {
        example.id: ((assessment, example), (degrade(assessment, gate), example))
        for assessment, example in assessed
    }
    assert cases, "the golden horizon dataset produced no cases"
    assert_each_can_go_red(scorer, cases, threshold=_bars(gate)[metric], metric=metric)


def test_every_horizon_metric_the_gate_scores_is_falsified_here(gate: Any) -> None:
    """The set, not the numbers. A metric added to the gate with no red case is the gap."""
    assert set(gate.SCORED_HORIZON) == set(_SCORERS), (
        "the gate's horizon metrics and this suite's red cases are not the same set: "
        f"scored with no proof {sorted(set(gate.SCORED_HORIZON) - set(_SCORERS))}, "
        f"proved but not scored {sorted(set(_SCORERS) - set(gate.SCORED_HORIZON))}"
    )


def test_a_missing_assessment_scores_zero_on_every_metric(gate: Any, assessed) -> None:
    """A service that produced nothing must not score a vacuous pass."""
    _, example = assessed[0]
    for scorer in gate.HORIZON_SCORERS.values():
        assert scorer((None, example)) == 0.0


def test_golden_expectations_are_an_independent_oracle(gate: Any, assessed) -> None:
    """The dataset states outcomes a human wrote, and the service must meet them.

    If this ever fails, either the engine regressed or the bank's policy changed: the fix is
    a deliberate dataset edit, never a metric that reads back the engine's own answer.
    """
    for assessment, example in assessed:
        assert assessment.applicability.value == example.expected_applicability, example.id
        assert assessment.materiality_band.value == example.expected_band, example.id
        owner = assessment.owner.owner if assessment.owner is not None else ""
        assert owner == example.expected_owner, example.id
