import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from knotic_api.domain import (
    BuyingStage,
    Customer,
    DomainEvent,
    EventType,
    MemoryFact,
    Message,
    MessageSource,
    Objection,
    Outcome,
    OutcomeType,
    Qualification,
    Requirement,
    RequirementField,
    SalesState,
    SessionStatus,
    Speaker,
    ToolCall,
    new_uuid7,
)

NOW = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
SNAPSHOT = Path(__file__).with_name("snapshots") / "domain-models.v1.sha256.json"


def ids() -> tuple:
    return tuple(new_uuid7(timestamp_ms=1_777_000_000_000 + index) for index in range(10))


def state() -> SalesState:
    tenant_id, session_id, customer_id, *_ = ids()
    return SalesState(
        session_id=session_id,
        tenant_id=tenant_id,
        status=SessionStatus.CREATED,
        version=1,
        customer=Customer(customer_id=customer_id, tenant_id=tenant_id, company="Acme"),
        created_at=NOW,
        updated_at=NOW,
    )


def test_sales_state_round_trip_and_transition() -> None:
    original = state()
    active = original.transition_to(SessionStatus.ACTIVE, at=NOW + timedelta(seconds=1))
    ending = active.transition_to(SessionStatus.ENDING, at=NOW + timedelta(seconds=2))
    ended = ending.transition_to(SessionStatus.ENDED, at=NOW + timedelta(seconds=3))

    assert SalesState.model_validate_json(ended.model_dump_json()) == ended
    assert ended.version == 4
    assert ended.ended_at == NOW + timedelta(seconds=3)


def test_invalid_transition_and_non_uuid7_are_rejected() -> None:
    with pytest.raises(ValueError, match="CREATED -> ENDED"):
        state().transition_to(SessionStatus.ENDED, at=NOW + timedelta(seconds=1))

    payload = state().model_dump()
    payload["session_id"] = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    with pytest.raises(ValidationError, match="UUIDv7"):
        SalesState.model_validate(payload)


def test_requirement_types_and_qualification_score_are_enforced() -> None:
    tenant_id, session_id, requirement_id, turn_id, qualification_id, *_ = ids()
    budget = Requirement(
        requirement_id=requirement_id,
        tenant_id=tenant_id,
        session_id=session_id,
        field=RequirementField.BUDGET,
        value=Decimal("12500.00"),
        currency="USD",
        confirmed=True,
        confidence=1,
        source_turn_id=turn_id,
        updated_at=NOW,
        version=1,
    )
    assert Requirement.model_validate_json(budget.model_dump_json()) == budget

    with pytest.raises(ValidationError, match="positive integer"):
        Requirement.model_validate({**budget.model_dump(), "field": "users", "value": 0, "currency": None})

    with pytest.raises(ValidationError, match="component sum"):
        Qualification(
            qualification_id=qualification_id,
            tenant_id=tenant_id,
            session_id=session_id,
            need=20,
            product_fit=10,
            deployment_fit=5,
            timeline=5,
            authority=5,
            budget=5,
            purchase_intent=5,
            total_score=99,
            buying_stage=BuyingStage.QUALIFIED,
            source_turn_id=turn_id,
            calculated_at=NOW,
        )


def test_message_outcome_and_event_business_invariants() -> None:
    tenant_id, session_id, message_id, turn_id, outcome_id, event_id, correlation_id, actor_id, *_ = ids()
    with pytest.raises(ValidationError, match="assistant messages require"):
        Message(
            message_id=message_id,
            tenant_id=tenant_id,
            session_id=session_id,
            turn_id=turn_id,
            sequence=1,
            speaker=Speaker.ASSISTANT,
            source=MessageSource.TEXT,
            content="Hello",
            locale="en-IN",
            created_at=NOW,
        )

    with pytest.raises(ValidationError, match="provider confirmation"):
        Outcome(
            outcome_id=outcome_id,
            tenant_id=tenant_id,
            session_id=session_id,
            outcome=OutcomeType.ENTERPRISE_DEMO_BOOKED,
            source="SYSTEM",
            assigned_at=NOW,
        )

    with pytest.raises(ValidationError, match="confirmed old/new"):
        DomainEvent(
            event_id=event_id,
            event_type=EventType.REQUIREMENT_UPDATED,
            occurred_at=NOW,
            tenant_id=tenant_id,
            session_id=session_id,
            sequence=1,
            correlation_id=correlation_id,
            actor_type="CUSTOMER",
            actor_id=actor_id,
            payload={"field": "users", "new_value": 250, "confirmed": False},
        )


def test_domain_json_schemas_match_versioned_snapshot() -> None:
    model_types = (
        Customer,
        MemoryFact,
        Requirement,
        Objection,
        Qualification,
        Message,
        ToolCall,
        Outcome,
        DomainEvent,
        SalesState,
    )
    actual = {
        model.__name__: hashlib.sha256(
            json.dumps(model.model_json_schema(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for model in model_types
    }
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    assert actual == expected, "intentional schema changes require a new reviewed snapshot"
