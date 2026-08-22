"""Pure, deterministic structured-memory merge rules."""

from __future__ import annotations

from dataclasses import dataclass

from .models import MemoryFact, Objection, Requirement, SalesState
from .types import MemoryField, NextBestAction, RequirementField


class MemoryConflict(ValueError):
    """Two equally authoritative facts disagree without a deterministic order."""


@dataclass(frozen=True, slots=True)
class MemoryMergeResult:
    state: SalesState
    changed: bool
    replaced: MemoryFact | None = None


_REQUIREMENT_FIELDS = {
    MemoryField.USERS: RequirementField.USERS,
    MemoryField.USE_CASES: RequirementField.USE_CASES,
    MemoryField.INTEGRATIONS: RequirementField.INTEGRATIONS,
    MemoryField.BUDGET: RequirementField.BUDGET,
    MemoryField.TIMELINE: RequirementField.TIMELINE,
}


def merge_memory_fact(state: SalesState, proposal: MemoryFact) -> MemoryMergeResult:
    """Merge one fact using confirmation, chronology, confidence, and version order."""

    if proposal.tenant_id != state.tenant_id or proposal.session_id != state.session_id:
        raise ValueError("memory proposal does not belong to the state session")
    current = next((fact for fact in state.memory_facts if fact.field == proposal.field), None)
    if current is not None and proposal.source_turn_id == current.source_turn_id:
        if proposal == current:
            return MemoryMergeResult(state=state, changed=False)
        raise MemoryConflict("the same source turn cannot assert conflicting values for one field")
    if current is None:
        if proposal.version != 1:
            raise MemoryConflict("the first memory fact version must be 1")
    else:
        if proposal.version != current.version + 1:
            raise MemoryConflict("memory fact version must increase exactly once")
        if not _should_replace(current, proposal):
            return MemoryMergeResult(state=state, changed=False)

    facts = (*(fact for fact in state.memory_facts if fact.field != proposal.field), proposal)
    update: dict[str, object] = {
        "memory_facts": facts,
        "version": state.version + 1,
        "updated_at": max(state.updated_at, proposal.captured_at),
    }
    _project_fact(state, proposal, update)
    return MemoryMergeResult(state=state.model_copy(update=update), changed=True, replaced=current)


def merge_memory_facts(state: SalesState, proposals: tuple[MemoryFact, ...]) -> MemoryMergeResult:
    """Apply ordered proposals and report whether any authoritative value changed."""

    current = state
    first_replaced: MemoryFact | None = None
    changed = False
    for proposal in proposals:
        result = merge_memory_fact(current, proposal)
        current = result.state
        changed = changed or result.changed
        first_replaced = first_replaced or result.replaced
    return MemoryMergeResult(state=current, changed=changed, replaced=first_replaced)


def merge_objection(state: SalesState, proposal: Objection) -> SalesState:
    """Upsert an objection by identity while rejecting stale or ambiguous revisions."""

    if proposal.tenant_id != state.tenant_id or proposal.session_id != state.session_id:
        raise ValueError("objection does not belong to the state session")
    current = next((item for item in state.objections if item.objection_id == proposal.objection_id), None)
    if current is not None:
        if proposal == current:
            return state
        if proposal.version != current.version + 1 or proposal.latest_turn_id == current.latest_turn_id:
            raise MemoryConflict("objection revision is stale or has conflicting provenance")
    elif proposal.version != 1:
        raise MemoryConflict("the first objection version must be 1")
    objections = (*(item for item in state.objections if item.objection_id != proposal.objection_id), proposal)
    return state.model_copy(update={"objections": objections, "version": state.version + 1})


def _should_replace(current: MemoryFact, proposal: MemoryFact) -> bool:
    if current.confirmed != proposal.confirmed:
        return proposal.confirmed
    if proposal.captured_at != current.captured_at:
        return proposal.captured_at > current.captured_at
    if proposal.confidence != current.confidence:
        return proposal.confidence > current.confidence
    if proposal.value != current.value or proposal.currency != current.currency:
        raise MemoryConflict("equally authoritative memory facts conflict")
    return False


def _project_fact(state: SalesState, fact: MemoryFact, update: dict[str, object]) -> None:
    if fact.field in {MemoryField.CUSTOMER_NAME, MemoryField.COMPANY, MemoryField.ROLE}:
        if state.customer is None:
            raise MemoryConflict("customer identity must exist before customer fields can be merged")
        attribute = {
            MemoryField.CUSTOMER_NAME: "name",
            MemoryField.COMPANY: "company",
            MemoryField.ROLE: "role",
        }[fact.field]
        update["customer"] = state.customer.model_copy(update={attribute: str(fact.value)})
        return
    requirement_field = _REQUIREMENT_FIELDS.get(fact.field)
    if requirement_field is not None:
        current = next((item for item in state.requirements if item.field == requirement_field), None)
        requirement = Requirement(
            requirement_id=current.requirement_id if current is not None else fact.fact_id,
            tenant_id=fact.tenant_id,
            session_id=fact.session_id,
            field=requirement_field,
            value=fact.value,
            currency=fact.currency,
            confirmed=fact.confirmed,
            confidence=fact.confidence,
            source_turn_id=fact.source_turn_id,
            updated_at=fact.captured_at,
            version=1 if current is None else current.version + 1,
        )
        update["requirements"] = (
            *(item for item in state.requirements if item.field != requirement_field),
            requirement,
        )
        return
    if fact.field == MemoryField.COMPETITORS:
        update["competitors"] = tuple(str(item) for item in fact.value) if isinstance(fact.value, tuple) else ()
    elif fact.field == MemoryField.CURRENT_TOPIC:
        update["current_topic"] = str(fact.value)
    elif fact.field == MemoryField.NEXT_ACTION:
        update["next_best_action"] = NextBestAction(str(fact.value))


def memory_value_for_log(fact: MemoryFact) -> str:
    """Return only a safe field label; structured values must not enter logs."""

    del fact
    return "[structured-memory-redacted]"
