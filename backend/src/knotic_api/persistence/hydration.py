"""Redis-first SalesState loading and durable recovery."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.engine import Engine, RowMapping

from knotic_api.domain.models import Customer, MemoryFact, Objection, Outcome, Qualification, Requirement, SalesState
from knotic_api.domain.types import (
    BuyingStage,
    MemoryField,
    NextBestAction,
    ObjectionCategory,
    ObjectionStatus,
    OutcomeType,
    RequirementField,
    SessionStatus,
)
from knotic_api.security import derive_key

from .active_state import ActiveStateEnvelope, CacheReadStatus, ConcurrentStateUpdate, RedisSalesStateRepository
from .repositories import DurableProjection
from .unit_of_work import UnitOfWork


class StateRecoveryError(RuntimeError):
    """Durable state could not be reconstructed safely."""


class StateNotFound(StateRecoveryError):
    """The tenant-scoped durable session does not exist."""


class HydrationSource(StrEnum):
    CACHE = "CACHE"
    DURABLE = "DURABLE"


@dataclass(frozen=True, slots=True)
class HydratedState:
    envelope: ActiveStateEnvelope
    source: HydrationSource
    cache_warmed: bool
    schema_migrated: bool = False


class StateFieldCipher:
    """AES-256-GCM envelope for recoverable sensitive projection fields."""

    def __init__(self, master_key: bytes) -> None:
        self._cipher = AESGCM(derive_key(master_key, b"durable-state-fields"))

    def encrypt(self, plaintext: str, *, tenant_id: UUID, aggregate_id: UUID, field: str) -> bytes:
        nonce = secrets.token_bytes(12)
        return (
            b"\x01"
            + nonce
            + self._cipher.encrypt(
                nonce,
                plaintext.encode(),
                self._associated_data(tenant_id, aggregate_id, field),
            )
        )

    def decrypt(self, ciphertext: bytes, *, tenant_id: UUID, aggregate_id: UUID, field: str) -> str:
        if len(ciphertext) < 30 or ciphertext[0] != 1:
            raise StateRecoveryError("unsupported durable field ciphertext")
        try:
            plaintext = self._cipher.decrypt(
                ciphertext[1:13],
                ciphertext[13:],
                self._associated_data(tenant_id, aggregate_id, field),
            )
            return plaintext.decode()
        except (UnicodeDecodeError, ValueError) as error:
            raise StateRecoveryError("durable field authentication failed") from error

    @staticmethod
    def _associated_data(tenant_id: UUID, aggregate_id: UUID, field: str) -> bytes:
        return f"knotic:v1:{tenant_id}:{aggregate_id}:{field}".encode()


class SalesStateHydrator:
    """Load Redis first, otherwise rebuild exactly from tenant-scoped durable rows."""

    def __init__(
        self,
        engine: Engine,
        active_states: RedisSalesStateRepository,
        *,
        field_cipher: StateFieldCipher,
    ) -> None:
        self._engine = engine
        self._active_states = active_states
        self._field_cipher = field_cipher

    def load(self, tenant_id: UUID, session_id: UUID, *, fencing_token: int = 0) -> HydratedState:
        cached = self._active_states.load(tenant_id, session_id)
        if cached.status == CacheReadStatus.HIT and cached.envelope is not None:
            return HydratedState(
                envelope=cached.envelope,
                source=HydrationSource.CACHE,
                cache_warmed=False,
                schema_migrated=cached.migrated,
            )
        with UnitOfWork(self._engine, tenant_id=tenant_id) as work:
            projection = work.projections.load(session_id)
        if projection is None:
            raise StateNotFound("durable sales session was not found")
        if projection.session.checkpoint_event_sequence > projection.event_watermark:
            raise StateRecoveryError("durable checkpoint is ahead of immutable event history")
        state = self._reconstruct(projection)
        try:
            envelope = self._active_states.create(
                state,
                event_watermark=projection.event_watermark,
                fencing_token=fencing_token,
            )
            return HydratedState(envelope=envelope, source=HydrationSource.DURABLE, cache_warmed=True)
        except ConcurrentStateUpdate:
            winner = self._active_states.load(tenant_id, session_id)
            if winner.status != CacheReadStatus.HIT or winner.envelope is None:
                raise StateRecoveryError("cache warming raced without a readable winner") from None
            if (
                winner.envelope.state_version < state.version
                or winner.envelope.event_watermark < projection.event_watermark
            ):
                raise StateRecoveryError("cache warming winner is older than durable state") from None
            return HydratedState(envelope=winner.envelope, source=HydrationSource.CACHE, cache_warmed=False)

    def checkpoint(self, state: SalesState, *, expected_version: int, event_watermark: int) -> None:
        with UnitOfWork(self._engine, tenant_id=state.tenant_id) as work:
            if not work.projections.checkpoint(
                state,
                expected_version=expected_version,
                event_watermark=event_watermark,
            ):
                raise StateRecoveryError("durable checkpoint lost an optimistic concurrency race")

    def _reconstruct(self, projection: DurableProjection) -> SalesState:
        session = projection.session
        requirements = tuple(self._requirement(row) for row in projection.requirements)
        changes_by_field = {row["field"]: row for row in projection.requirement_changes}
        facts = tuple(
            self._memory_fact(requirement, changes_by_field[requirement.field.value])
            for requirement in requirements
            if requirement.field.value in changes_by_field and requirement.field in _MEMORY_REQUIREMENTS
        )
        return SalesState(
            session_id=session.session_id,
            tenant_id=session.tenant_id,
            status=SessionStatus(session.status),
            version=session.version,
            current_intent=session.current_intent,
            current_topic=session.current_topic,
            buying_stage=BuyingStage(session.buying_stage) if session.buying_stage else BuyingStage.NURTURE,
            qualification=None if projection.qualification is None else self._qualification(projection.qualification),
            next_best_action=(
                NextBestAction(session.next_best_action) if session.next_best_action else NextBestAction.ASK_DISCOVERY
            ),
            conversation_summary=self._decrypt_optional(
                session.summary_ciphertext,
                tenant_id=session.tenant_id,
                aggregate_id=session.session_id,
                field="conversation_summary",
            ),
            latest_request=self._decrypt_optional(
                session.latest_request_ciphertext,
                tenant_id=session.tenant_id,
                aggregate_id=session.session_id,
                field="latest_request",
            )
            or None,
            customer=self._customer(projection),
            requirements=requirements,
            objections=tuple(self._objection(row) for row in projection.objections),
            competitors=tuple(str(row["normalized_name"]) for row in projection.competitors),
            memory_facts=facts,
            outcome=None if projection.outcome is None else self._outcome(projection.outcome),
            created_at=session.created_at,
            updated_at=session.updated_at,
            ended_at=session.ended_at,
        )

    def _customer(self, projection: DurableProjection) -> Customer | None:
        row = projection.lead
        if row is None:
            return None
        tenant_id = projection.session.tenant_id
        lead_id = row["id"]
        return Customer(
            customer_id=lead_id,
            tenant_id=tenant_id,
            lead_id=lead_id,
            name=self._decrypt_optional(row["name_ciphertext"], tenant_id=tenant_id, aggregate_id=lead_id, field="name")
            or None,
            company=self._decrypt_optional(
                row["company_ciphertext"], tenant_id=tenant_id, aggregate_id=lead_id, field="company"
            )
            or None,
            role=self._decrypt_optional(row["role_ciphertext"], tenant_id=tenant_id, aggregate_id=lead_id, field="role")
            or None,
            email=self._decrypt_optional(
                row["email_ciphertext"], tenant_id=tenant_id, aggregate_id=lead_id, field="email"
            )
            or None,
            phone=self._decrypt_optional(
                row["phone_ciphertext"], tenant_id=tenant_id, aggregate_id=lead_id, field="phone"
            )
            or None,
        )

    @staticmethod
    def _requirement(row: RowMapping) -> Requirement:
        value: Any = next(
            value
            for value in (
                row["value_integer"],
                row["value_text"],
                tuple(row["value_text_array"]) if row["value_text_array"] is not None else None,
                row["value_numeric"],
            )
            if value is not None
        )
        return Requirement(
            requirement_id=row["id"],
            tenant_id=row["tenant_id"],
            session_id=row["session_id"],
            field=RequirementField(row["field"]),
            value=value,
            currency=row["currency"],
            confirmed=row["confirmed_at"] is not None,
            confidence=float(row["confidence"]),
            source_turn_id=row["source_turn_id"],
            updated_at=row["updated_at"],
            version=row["version"],
        )

    @staticmethod
    def _memory_fact(requirement: Requirement, change: RowMapping) -> MemoryFact:
        return MemoryFact(
            fact_id=change["id"],
            tenant_id=requirement.tenant_id,
            session_id=requirement.session_id,
            field=_MEMORY_REQUIREMENTS[requirement.field],
            value=requirement.value,
            currency=requirement.currency,
            confirmed=requirement.confirmed,
            confidence=requirement.confidence,
            source_turn_id=requirement.source_turn_id,
            actor_type=change["actor_type"],
            captured_at=change["changed_at"],
            version=requirement.version,
        )

    def _objection(self, row: RowMapping) -> Objection:
        return Objection(
            objection_id=row["id"],
            tenant_id=row["tenant_id"],
            session_id=row["session_id"],
            category=ObjectionCategory(row["category"]),
            detail=self._field_cipher.decrypt(
                row["detail_ciphertext"],
                tenant_id=row["tenant_id"],
                aggregate_id=row["id"],
                field="objection_detail",
            ),
            status=ObjectionStatus(row["status"]),
            first_turn_id=row["first_turn_id"],
            latest_turn_id=row["latest_turn_id"],
            version=row["version"],
        )

    @staticmethod
    def _qualification(row: RowMapping) -> Qualification:
        return Qualification(
            qualification_id=row["id"],
            tenant_id=row["tenant_id"],
            session_id=row["session_id"],
            need=row["need_score"],
            product_fit=row["product_fit_score"],
            deployment_fit=row["deployment_fit_score"],
            timeline=row["timeline_score"],
            authority=row["authority_score"],
            budget=row["budget_score"],
            purchase_intent=row["purchase_intent_score"],
            total_score=row["total_score"],
            buying_stage=BuyingStage(row["buying_stage"]),
            source_turn_id=row["source_turn_id"],
            calculated_at=row["calculated_at"],
        )

    @staticmethod
    def _outcome(row: RowMapping) -> Outcome:
        return Outcome(
            outcome_id=row["id"],
            tenant_id=row["tenant_id"],
            session_id=row["session_id"],
            outcome=OutcomeType(row["outcome"]),
            source=row["source"],
            source_reference=row["source_reference"],
            assigned_at=row["assigned_at"],
        )

    def _decrypt_optional(
        self,
        value: bytes | None,
        *,
        tenant_id: UUID,
        aggregate_id: UUID,
        field: str,
    ) -> str:
        if value is None:
            return ""
        return self._field_cipher.decrypt(value, tenant_id=tenant_id, aggregate_id=aggregate_id, field=field)


_MEMORY_REQUIREMENTS = {
    RequirementField.USERS: MemoryField.USERS,
    RequirementField.USE_CASES: MemoryField.USE_CASES,
    RequirementField.INTEGRATIONS: MemoryField.INTEGRATIONS,
    RequirementField.BUDGET: MemoryField.BUDGET,
    RequirementField.TIMELINE: MemoryField.TIMELINE,
}
