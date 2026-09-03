"""Versioned offline conversation quality evaluation and CI gate."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from knotic_api.domain import BuyingStage, NextBestAction

from .contracts import SalesRoute

DATASET_VERSION = "sales-conversations-v1"
DEFAULT_THRESHOLDS = {
    "routing": 1.0,
    "extraction": 1.0,
    "score": 1.0,
    "policy": 1.0,
    "groundedness": 1.0,
    "response_quality": 0.95,
}
_SUCCESS_CLAIM = re.compile(
    r"\b(?:has been|successfully|i(?:'ve| have))\s+(?:booked|scheduled|created|updated|sent|transferred)\b",
    re.IGNORECASE,
)


class _EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationCase(_EvaluationModel):
    case_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{2,63}$")]
    scenario: Literal[
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
    ]
    expected_route: SalesRoute
    expected_entities: dict[str, str]
    expected_score: int = Field(ge=0, le=100)
    expected_stage: BuyingStage
    expected_action: NextBestAction
    requires_grounding: bool
    spoken_response: bool = True


class EvaluationDataset(_EvaluationModel):
    version: Literal["sales-conversations-v1"]
    cases: tuple[EvaluationCase, ...]


class EvaluationPrediction(_EvaluationModel):
    case_id: str
    route: SalesRoute
    entities: dict[str, str]
    score: int = Field(ge=0, le=100)
    stage: BuyingStage
    action: NextBestAction
    response_text: str | None
    citation_ids: tuple[str, ...] = ()
    supported_citation_ids: tuple[str, ...] = ()
    unsupported_claim: bool = False
    transaction_confirmed: bool = False


class EvaluationPredictions(_EvaluationModel):
    dataset_version: Literal["sales-conversations-v1"]
    predictions: tuple[EvaluationPrediction, ...]


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    dataset_version: str
    case_count: int
    metrics: dict[str, float]
    thresholds: dict[str, float]
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures


def load_dataset(path: Path) -> EvaluationDataset:
    return EvaluationDataset.model_validate_json(path.read_text(encoding="utf-8"))


def load_predictions(path: Path) -> EvaluationPredictions:
    return EvaluationPredictions.model_validate_json(path.read_text(encoding="utf-8"))


def evaluate_conversations(
    dataset: EvaluationDataset,
    predictions: EvaluationPredictions,
    *,
    thresholds: dict[str, float] | None = None,
) -> EvaluationReport:
    approved = DEFAULT_THRESHOLDS if thresholds is None else thresholds
    if set(approved) != set(DEFAULT_THRESHOLDS) or any(not 0 <= value <= 1 for value in approved.values()):
        raise ValueError("evaluation thresholds must define every approved metric between zero and one")
    if predictions.dataset_version != dataset.version:
        raise ValueError("prediction dataset version does not match the evaluation dataset")
    expected = {case.case_id: case for case in dataset.cases}
    actual = {prediction.case_id: prediction for prediction in predictions.predictions}
    if len(expected) != len(dataset.cases) or len(actual) != len(predictions.predictions):
        raise ValueError("evaluation case identifiers must be unique")
    if set(expected) != set(actual):
        raise ValueError("predictions must contain every dataset case exactly once")
    case_count = len(expected)
    if case_count == 0:
        raise ValueError("evaluation dataset must not be empty")

    routing = extraction = score = policy = grounding = response = 0
    for case_id in sorted(expected):
        case, prediction = expected[case_id], actual[case_id]
        routing += prediction.route == case.expected_route
        extraction += prediction.entities == case.expected_entities
        score += prediction.score == case.expected_score and prediction.stage == case.expected_stage
        policy += prediction.action == case.expected_action
        grounding += _grounding_passes(case, prediction)
        response += _response_passes(case, prediction)
    metrics = {
        "routing": routing / case_count,
        "extraction": extraction / case_count,
        "score": score / case_count,
        "policy": policy / case_count,
        "groundedness": grounding / case_count,
        "response_quality": response / case_count,
    }
    failures = tuple(
        f"{metric}={metrics[metric]:.4f} is below {minimum:.4f}"
        for metric, minimum in approved.items()
        if metrics[metric] < minimum
    )
    return EvaluationReport(dataset.version, case_count, metrics, dict(approved), failures)


def _grounding_passes(case: EvaluationCase, prediction: EvaluationPrediction) -> bool:
    if prediction.unsupported_claim:
        return False
    if not case.requires_grounding:
        return True
    citations = set(prediction.citation_ids)
    return bool(citations) and citations <= set(prediction.supported_citation_ids)


def _response_passes(case: EvaluationCase, prediction: EvaluationPrediction) -> bool:
    text = prediction.response_text
    if not case.spoken_response:
        return text is None
    if text is None or not text.strip() or len(text) > 600:
        return False
    if len(re.findall(r"[.!?](?:\s|$)", text)) > 3:
        return False
    return prediction.transaction_confirmed or _SUCCESS_CLAIM.search(text) is None


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).parents[4]
    parser = argparse.ArgumentParser(description="Evaluate versioned Knotic conversation predictions")
    parser.add_argument("--dataset", type=Path, default=root / "backend/evaluations/sales-conversations-v1.json")
    parser.add_argument("--predictions", type=Path, default=root / "backend/evaluations/reference-predictions-v1.json")
    arguments = parser.parse_args(argv)
    report = evaluate_conversations(load_dataset(arguments.dataset), load_predictions(arguments.predictions))
    print(json.dumps({**asdict(report), "passed": report.passed}, indent=2, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
