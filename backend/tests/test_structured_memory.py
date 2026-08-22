from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from knotic_api.domain import (
    Customer,
    MemoryFact,
    MemoryField,
    NextBestAction,
    Objection,
    ObjectionCategory,
    ObjectionStatus,
    RequirementField,
    SalesState,
    SessionStatus,
    new_uuid7,
)
from knotic_api.domain.memory import MemoryConflict, merge_memory_fact, merge_objection

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _ids(count: int = 20) -> tuple:
    return tuple(new_uuid7(timestamp_ms=1_777_100_000_000 + index) for index in range(count))


def _state() -> SalesState:
    tenant_id, session_id, customer_id, *_ = _ids()
    return SalesState(
        session_id=session_id,
        tenant_id=tenant_id,
        status=SessionStatus.ACTIVE,
        version=1,
        customer=Customer(customer_id=customer_id, tenant_id=tenant_id),
        created_at=NOW,
        updated_at=NOW,
    )


def _fact(
    state: SalesState,
    field: MemoryField,
    value: object,
    *,
    offset: int,
    version: int = 1,
    confirmed: bool = True,
    confidence: float = 0.95,
    currency: str | None = None,
    source_turn_id: object | None = None,
) -> MemoryFact:
    identifiers = _ids(40)
    return MemoryFact(
        fact_id=identifiers[10 + offset],
        tenant_id=state.tenant_id,
        session_id=state.session_id,
        field=field,
        value=value,
        currency=currency,
        confirmed=confirmed,
        confidence=confidence,
        source_turn_id=source_turn_id or identifiers[20 + offset],
        actor_type="CUSTOMER",
        captured_at=NOW + timedelta(seconds=offset + 1),
        version=version,
    )


@pytest.mark.parametrize(
    ("field", "value", "currency", "assertion"),
    [
        (MemoryField.CUSTOMER_NAME, "Ada", None, lambda state: state.customer.name == "Ada"),
        (MemoryField.COMPANY, "Analytical Engines", None, lambda state: state.customer.company == "Analytical Engines"),
        (MemoryField.ROLE, "Founder", None, lambda state: state.customer.role == "Founder"),
        (MemoryField.USERS, 250, None, lambda state: state.requirements[0].value == 250),
        (
            MemoryField.USE_CASES,
            ("sales", "support"),
            None,
            lambda state: state.requirements[0].field == RequirementField.USE_CASES,
        ),
        (
            MemoryField.INTEGRATIONS,
            ("Salesforce", "Slack"),
            None,
            lambda state: state.requirements[0].field == RequirementField.INTEGRATIONS,
        ),
        (MemoryField.BUDGET, Decimal("12500"), "USD", lambda state: state.requirements[0].currency == "USD"),
        (MemoryField.TIMELINE, "Q4", None, lambda state: state.requirements[0].value == "Q4"),
        (
            MemoryField.COMPETITORS,
            ("Competitor A", "Competitor B"),
            None,
            lambda state: state.competitors == ("Competitor A", "Competitor B"),
        ),
        (MemoryField.CURRENT_TOPIC, "security", None, lambda state: state.current_topic == "security"),
        (
            MemoryField.NEXT_ACTION,
            NextBestAction.HANDLE_OBJECTION.value,
            None,
            lambda state: state.next_best_action == NextBestAction.HANDLE_OBJECTION,
        ),
    ],
)
def test_table_driven_memory_fields_project_into_structured_state(
    field: MemoryField,
    value: object,
    currency: str | None,
    assertion: object,
) -> None:
    state = _state()
    result = merge_memory_fact(state, _fact(state, field, value, offset=0, currency=currency))

    assert result.changed
    assert result.state.version == 2
    assert result.state.memory_facts[0].confirmed
    assert result.state.memory_facts[0].confidence == 0.95
    assert result.state.memory_facts[0].source_turn_id is not None
    assert callable(assertion) and assertion(result.state)


def test_confirmed_memory_resists_tentative_replacement_and_replay_is_idempotent() -> None:
    state = _state()
    confirmed = _fact(state, MemoryField.USERS, 50, offset=0)
    first = merge_memory_fact(state, confirmed)
    replay = merge_memory_fact(first.state, confirmed)
    tentative = _fact(
        first.state,
        MemoryField.USERS,
        250,
        offset=1,
        version=2,
        confirmed=False,
        confidence=1,
    )

    assert not replay.changed and replay.state is first.state
    ignored = merge_memory_fact(first.state, tentative)
    assert not ignored.changed
    assert ignored.state.requirements[0].value == 50


def test_equal_authority_conflict_and_cross_session_fact_fail_closed() -> None:
    state = _state()
    current = _fact(state, MemoryField.CURRENT_TOPIC, "pricing", offset=0)
    merged = merge_memory_fact(state, current).state
    conflict = current.model_copy(
        update={
            "fact_id": _ids(40)[35],
            "source_turn_id": _ids(40)[36],
            "value": "security",
            "version": 2,
        }
    )
    with pytest.raises(MemoryConflict, match="equally authoritative"):
        merge_memory_fact(merged, conflict)

    foreign = current.model_copy(update={"tenant_id": _ids(40)[37]})
    with pytest.raises(ValueError, match="does not belong"):
        merge_memory_fact(state, foreign)


def test_objection_updates_preserve_identity_and_turn_provenance() -> None:
    state = _state()
    identifiers = _ids(40)
    objection = Objection(
        objection_id=identifiers[30],
        tenant_id=state.tenant_id,
        session_id=state.session_id,
        category=ObjectionCategory.SECURITY,
        detail="Needs a security review",
        status=ObjectionStatus.OPEN,
        first_turn_id=identifiers[31],
        latest_turn_id=identifiers[31],
        version=1,
    )
    opened = merge_objection(state, objection)
    resolved = merge_objection(
        opened,
        objection.model_copy(
            update={"status": ObjectionStatus.RESOLVED, "latest_turn_id": identifiers[32], "version": 2}
        ),
    )

    assert len(resolved.objections) == 1
    assert resolved.objections[0].status == ObjectionStatus.RESOLVED
    assert resolved.objections[0].first_turn_id == identifiers[31]
