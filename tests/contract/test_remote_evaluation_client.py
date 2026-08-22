"""Contract test: the platform eval adapter speaks Hrz4's hardened, bundle-driven HTTP shape.

The ``RemoteEvaluationAdapter`` had no test at all. It is the one component that decides
whether C4 may be promoted, it delegates that decision to
``agent_eval_kit.gate_client.PromotionGateClient``, and until now nothing in this repo
asserted what it sends or what it is willing to believe. That is the gap this file closes:
the sibling verticals already pin the same contract, and a promotion gate nobody tests is a
promotion gate nobody can trust.

HTTP is intercepted with ``respx`` (a dev dependency); no live Hrz4 is contacted.

Request side: a *structured* target, a top-level ``dataset_id`` equal to
``target.dataset_id`` (Hrz4 422s on divergence), and metric selection by the registered
bundle name only, never a metric-name list.

Response side, the hardened contract: the client RE-DERIVES every verdict from the evidence
and raises on any contradiction, on the plain evaluations path as well as inside ``gate``.
An evaluation response needs durable identifiers (``run_id``, ``dataset_version``,
``dataset_digest``, ``evaluator``, ``schema_version``), a non-empty ``artifact_refs``, an
``attested`` flag, a positive ``n_examples``, and per-metric rows whose ``passed`` equals
``score >= threshold``. A gate response needs all of that inside ``eval_report``, plus a
``redteam_report`` whose aggregate matches its rows and whose every row's ``passed`` and
``blocked`` agree, durable ``model_card_ref`` and ``mrm_evidence_ref``, and a top-level
``passed`` equal to (eval passed AND attested AND red-team passed).

The refusal tests are the point: a promotion certified by a naked ``{"passed": true}`` is a
promotion certified by nothing. Every value is obviously fictional.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from compliance_advisory.adapters.platform.remote_evaluation import (
    RemoteEvaluationAdapter,
    RemoteEvaluationError,
)
from compliance_advisory.config import Settings
from compliance_advisory.domain.errors import ComplianceError
from compliance_advisory.domain.models import EvalReport

_BASE = "https://hrz4.test"
_DATASET = "eval/datasets/golden_qa.jsonl"
_DATASET_ID = "golden_qa"
_DIGEST = "sha256:feedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedface"
_BUNDLE = "rsk1-compliance-advisory"

_PASSING_RESULTS = [
    {"metric": "groundedness", "score": 0.94, "threshold": 0.80, "passed": True},
    {"metric": "citation_accuracy", "score": 0.97, "threshold": 0.90, "passed": True},
]

#: A consistent FAILING set: the row misses its bar and says so, because the client
#: re-derives each verdict and a contradictory body raises instead of returning False.
_FAILING_RESULTS = [
    {"metric": "groundedness", "score": 0.71, "threshold": 0.80, "passed": False},
    {"metric": "citation_accuracy", "score": 0.97, "threshold": 0.90, "passed": True},
]


def _evidence(**overrides: Any) -> dict[str, Any]:
    """Durable evaluation evidence in the full hardened shape, obviously fictional."""
    body: dict[str, Any] = {
        "results": _PASSING_RESULTS,
        "n_examples": 18,
        "run_id": "run-fictional-0001",
        "dataset_version": f"{_DATASET_ID}@2026-08-01",
        "dataset_digest": _DIGEST,
        "evaluator": "hrz4-ai-quality (FICTIONAL)",
        "schema_version": "v1",
        "artifact_refs": ["gs://fictional-hrz4-evidence/run-fictional-0001/report.json"],
        "attested": True,
    }
    body.update(overrides)
    return body


def _gate_body(**overrides: Any) -> dict[str, Any]:
    """The complete GateDecision the promotion gate now demands."""
    body: dict[str, Any] = {
        "passed": True,
        "eval_report": _evidence(),
        "redteam_report": {
            "passed": True,
            "results": [
                {"case": "prompt-injection-01", "passed": True, "blocked": True},
                {"case": "regulatory-advice-exfil-01", "passed": True, "blocked": True},
            ],
        },
        "model_card_ref": f"gs://fictional-hrz4-evidence/model-cards/{_BUNDLE}.md",
        "mrm_evidence_ref": f"gs://fictional-hrz4-evidence/mrm/{_BUNDLE}-2026-08.json",
    }
    body.update(overrides)
    return body


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> RemoteEvaluationAdapter:
    monkeypatch.setenv("HRZ_QUALITY_URL", _BASE)
    return RemoteEvaluationAdapter(Settings())


@respx.mock
def test_evaluate_posts_a_structured_bundle_request_and_parses_results(
    adapter: RemoteEvaluationAdapter,
) -> None:
    route = respx.post(f"{_BASE}/v1/evaluations").mock(
        return_value=httpx.Response(200, json=_evidence(results=_FAILING_RESULTS, passed=False))
    )

    report = adapter.evaluate(_DATASET)

    assert route.called
    body = json.loads(route.calls.last.request.content)
    target = body["target"]

    # Structured target, not a flat string.
    assert isinstance(target, dict)
    assert target["model"] == Settings().models.reasoning
    assert target["prompt_version"] == "v1"
    assert target["system"] == ""

    # dataset_id is the basename without .jsonl, and the top level mirrors target's.
    assert target["dataset_id"] == _DATASET_ID
    assert body["dataset_id"] == target["dataset_id"]

    # Metrics are selected by bundle only; never a metric-name list.
    assert body["bundle"] == _BUNDLE
    assert "metrics" not in body
    assert "metrics" not in target

    # results[] parsed into the domain EvalReport.
    assert isinstance(report, EvalReport)
    assert report.dataset == _DATASET
    assert report.n_examples == 18
    assert [r.metric for r in report.results] == ["groundedness", "citation_accuracy"]
    assert report.results[0].passed is False
    assert report.passed is False


@respx.mock
def test_evaluate_returns_the_attested_evidence_UNCHANGED(
    adapter: RemoteEvaluationAdapter,
) -> None:
    """The adapter must not rebuild the report; rebuilding drops the evidence.

    This adapter used to map the client's report onto a locally declared ``EvalReport`` with
    three fields. Once the domain type became the commons type, that mapper was an identity
    function that lost data: every durable identifier and the ``attested`` flag the client
    had just validated were dropped on the floor, so a promotion decision could not be
    reconstructed from what the port returned. Reinstate the rebuild and these assertions fail
    with ``run_id == ''``.
    """
    respx.post(f"{_BASE}/v1/evaluations").mock(
        return_value=httpx.Response(200, json=_evidence(trace_id="trace-fictional-77"))
    )

    report = adapter.evaluate(_DATASET)

    assert report.run_id == "run-fictional-0001"
    assert report.dataset_version == f"{_DATASET_ID}@2026-08-01"
    assert report.dataset_digest == _DIGEST
    assert report.evaluator == "hrz4-ai-quality (FICTIONAL)"
    assert report.schema_version == "v1"
    assert report.trace_id == "trace-fictional-77"
    assert report.artifact_refs == ("gs://fictional-hrz4-evidence/run-fictional-0001/report.json",)
    assert report.attested is True


@respx.mock
def test_evaluate_REFUSES_metric_rows_with_no_examples_behind_them(
    adapter: RemoteEvaluationAdapter,
) -> None:
    """``all(())`` is vacuously true; a report that scored nothing must not parse."""
    respx.post(f"{_BASE}/v1/evaluations").mock(
        return_value=httpx.Response(200, json=_evidence(n_examples=0))
    )
    with pytest.raises(RemoteEvaluationError):
        adapter.evaluate(_DATASET)


@respx.mock
def test_evaluate_REFUSES_a_verdict_that_contradicts_its_score(
    adapter: RemoteEvaluationAdapter,
) -> None:
    """A row claiming PASS below its own threshold is evidence of a broken evaluator."""
    rows = [{"metric": "groundedness", "score": 0.10, "threshold": 0.80, "passed": True}]
    respx.post(f"{_BASE}/v1/evaluations").mock(
        return_value=httpx.Response(200, json=_evidence(results=rows))
    )
    with pytest.raises(RemoteEvaluationError):
        adapter.evaluate(_DATASET)


@respx.mock
def test_evaluate_REFUSES_evidence_with_no_durable_identifiers(
    adapter: RemoteEvaluationAdapter,
) -> None:
    """Without a run id or an artifact ref the score is unreproducible and unauditable."""
    respx.post(f"{_BASE}/v1/evaluations").mock(
        return_value=httpx.Response(200, json=_evidence(run_id="", artifact_refs=[]))
    )
    with pytest.raises(RemoteEvaluationError):
        adapter.evaluate(_DATASET)


@respx.mock
def test_gate_posts_to_v1_gate_and_accepts_a_full_consistent_decision(
    adapter: RemoteEvaluationAdapter,
) -> None:
    route = respx.post(f"{_BASE}/v1/gate").mock(return_value=httpx.Response(200, json=_gate_body()))

    assert adapter.gate(_DATASET) is True
    assert route.called
    request = route.calls.last.request
    assert request.method == "POST"  # a POST, never a GET
    body = json.loads(request.content)
    assert body["bundle"] == _BUNDLE
    assert body["dataset_id"] == body["target"]["dataset_id"] == _DATASET_ID


@respx.mock
def test_gate_returns_false_through_consistent_failing_evidence(
    adapter: RemoteEvaluationAdapter,
) -> None:
    """A FAIL is reached through a failing metric row, never a contradictory body."""
    body = _gate_body(passed=False, eval_report=_evidence(results=_FAILING_RESULTS))
    respx.post(f"{_BASE}/v1/gate").mock(return_value=httpx.Response(200, json=body))
    assert adapter.gate(_DATASET) is False


@respx.mock
def test_gate_REFUSES_a_naked_boolean_with_no_evidence(adapter: RemoteEvaluationAdapter) -> None:
    """The unhardened response shape. Accepting it is how a promotion gets certified by
    nothing, so the refusal is the contract."""
    respx.post(f"{_BASE}/v1/gate").mock(return_value=httpx.Response(200, json={"passed": True}))
    with pytest.raises(RemoteEvaluationError):
        adapter.gate(_DATASET)


@respx.mock
def test_gate_REFUSES_an_unattested_report_even_when_every_metric_passes(
    adapter: RemoteEvaluationAdapter,
) -> None:
    """A laptop evaluator can score the same corpus; that is not release authority."""
    respx.post(f"{_BASE}/v1/gate").mock(
        return_value=httpx.Response(200, json=_gate_body(eval_report=_evidence(attested=False)))
    )
    with pytest.raises(RemoteEvaluationError):
        adapter.gate(_DATASET)


@respx.mock
def test_gate_REFUSES_a_redteam_aggregate_that_contradicts_its_rows(
    adapter: RemoteEvaluationAdapter,
) -> None:
    body = _gate_body(
        redteam_report={
            "passed": True,
            "results": [{"case": "prompt-injection-01", "passed": False, "blocked": False}],
        }
    )
    respx.post(f"{_BASE}/v1/gate").mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(RemoteEvaluationError):
        adapter.gate(_DATASET)


@respx.mock
def test_gate_REFUSES_a_decision_with_no_model_card_or_mrm_reference(
    adapter: RemoteEvaluationAdapter,
) -> None:
    """Promotion evidence a model-risk reviewer cannot later retrieve is not evidence."""
    respx.post(f"{_BASE}/v1/gate").mock(
        return_value=httpx.Response(200, json=_gate_body(model_card_ref="", mrm_evidence_ref=""))
    )
    with pytest.raises(RemoteEvaluationError):
        adapter.gate(_DATASET)


@respx.mock
def test_non_2xx_raises_a_domain_error(adapter: RemoteEvaluationAdapter) -> None:
    respx.post(f"{_BASE}/v1/evaluations").mock(
        return_value=httpx.Response(503, text="service unavailable")
    )
    with pytest.raises(RemoteEvaluationError):
        adapter.evaluate(_DATASET)
    # The repo error is a domain error, so callers can catch it without an httpx dependency.
    assert issubclass(RemoteEvaluationError, ComplianceError)


@respx.mock
def test_transport_error_raises_a_domain_error(adapter: RemoteEvaluationAdapter) -> None:
    respx.post(f"{_BASE}/v1/gate").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(RemoteEvaluationError):
        adapter.gate(_DATASET)
