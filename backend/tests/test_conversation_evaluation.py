from __future__ import annotations

from pathlib import Path

from knotic_api.workflow import SalesRoute
from knotic_api.workflow.evaluation import (
    DATASET_VERSION,
    DEFAULT_THRESHOLDS,
    EvaluationPredictions,
    evaluate_conversations,
    load_dataset,
    load_predictions,
    main,
)

ROOT = Path(__file__).parents[2]
DATASET = ROOT / "backend/evaluations/sales-conversations-v1.json"
PREDICTIONS = ROOT / "backend/evaluations/reference-predictions-v1.json"


def test_versioned_corpus_covers_every_required_sales_and_failure_scenario() -> None:
    dataset = load_dataset(DATASET)
    assert dataset.version == DATASET_VERSION
    assert {case.scenario for case in dataset.cases} == {
        "discovery",
        "revision",
        "objections",
        "pricing",
        "competitors",
        "demo",
        "follow_up",
        "handoff",
        "closing",
        "unsafe_request",
        "failure",
    }


def test_reference_evaluation_is_reproducible_and_clears_every_threshold() -> None:
    dataset, predictions = load_dataset(DATASET), load_predictions(PREDICTIONS)
    first = evaluate_conversations(dataset, predictions)
    second = evaluate_conversations(dataset, predictions)
    assert first == second
    assert first.passed
    assert first.metrics == {metric: 1.0 for metric in DEFAULT_THRESHOLDS}
    assert main(["--dataset", str(DATASET), "--predictions", str(PREDICTIONS)]) == 0


def test_routing_regression_fails_the_quality_gate() -> None:
    dataset, predictions = load_dataset(DATASET), load_predictions(PREDICTIONS)
    changed = list(predictions.predictions)
    changed[0] = changed[0].model_copy(update={"route": SalesRoute.PRICING})
    report = evaluate_conversations(
        dataset,
        EvaluationPredictions(dataset_version=DATASET_VERSION, predictions=tuple(changed)),
    )
    assert not report.passed
    assert report.metrics["routing"] == 10 / 11
    assert any(failure.startswith("routing=") for failure in report.failures)


def test_unconfirmed_transaction_success_regression_fails_response_threshold() -> None:
    dataset, predictions = load_dataset(DATASET), load_predictions(PREDICTIONS)
    changed = list(predictions.predictions)
    index = next(index for index, item in enumerate(changed) if item.case_id == "follow_up_email")
    changed[index] = changed[index].model_copy(update={"response_text": "I have successfully created the follow-up."})
    report = evaluate_conversations(
        dataset,
        EvaluationPredictions(dataset_version=DATASET_VERSION, predictions=tuple(changed)),
    )
    assert not report.passed
    assert report.metrics["response_quality"] == 10 / 11


def test_unsupported_or_unknown_citation_fails_groundedness_threshold() -> None:
    dataset, predictions = load_dataset(DATASET), load_predictions(PREDICTIONS)
    changed = list(predictions.predictions)
    index = next(index for index, item in enumerate(changed) if item.case_id == "pricing_annual")
    changed[index] = changed[index].model_copy(update={"citation_ids": ("cite_unknown01",)})
    report = evaluate_conversations(
        dataset,
        EvaluationPredictions(dataset_version=DATASET_VERSION, predictions=tuple(changed)),
    )
    assert not report.passed
    assert report.metrics["groundedness"] == 10 / 11
