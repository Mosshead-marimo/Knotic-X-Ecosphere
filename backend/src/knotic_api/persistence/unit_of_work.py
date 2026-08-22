"""Explicit PostgreSQL transaction and tenant-context boundary."""

from __future__ import annotations

from types import TracebackType
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from .repositories import (
    EventRepository,
    FollowupRepository,
    IdempotencyRepository,
    LeadRepository,
    MeetingRepository,
    MessageRepository,
    ObjectionRepository,
    OutcomeRepository,
    PrivacyRepository,
    ProjectionRepository,
    RequirementRepository,
    SessionRepository,
    ToolRepository,
)


class UnitOfWork:
    """One short transaction; never perform external network I/O inside it."""

    def __init__(self, engine: Engine, *, tenant_id: UUID, actor_id: UUID | None = None) -> None:
        if tenant_id.version != 7:
            raise ValueError("tenant_id must be UUIDv7")
        if actor_id is not None and actor_id.version != 7:
            raise ValueError("actor_id must be UUIDv7")
        self._engine = engine
        self.tenant_id = tenant_id
        self.actor_id = actor_id
        self._connection: Connection | None = None
        self._transaction: sa.engine.Transaction | None = None

    def __enter__(self) -> UnitOfWork:
        if self._connection is not None:
            raise RuntimeError("unit of work is already active")
        connection = self._engine.connect()
        transaction = connection.begin()
        try:
            connection.execute(
                sa.text("select set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(self.tenant_id)},
            )
            if self.actor_id is not None:
                connection.execute(
                    sa.text("select set_config('app.actor_id', :actor_id, true)"),
                    {"actor_id": str(self.actor_id)},
                )
        except BaseException:
            transaction.rollback()
            connection.close()
            raise
        self._connection = connection
        self._transaction = transaction
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exception, traceback
        connection = self._require_connection()
        transaction = self._transaction
        if transaction is None:
            raise RuntimeError("unit of work transaction is unavailable")
        try:
            if exception_type is None:
                transaction.commit()
            else:
                transaction.rollback()
        finally:
            connection.close()
            self._connection = None
            self._transaction = None
        return False

    @property
    def sessions(self) -> SessionRepository:
        return SessionRepository(self._require_connection(), self.tenant_id)

    @property
    def leads(self) -> LeadRepository:
        return LeadRepository(self._require_connection(), self.tenant_id)

    @property
    def messages(self) -> MessageRepository:
        return MessageRepository(self._require_connection(), self.tenant_id)

    @property
    def requirements(self) -> RequirementRepository:
        return RequirementRepository(self._require_connection(), self.tenant_id)

    @property
    def objections(self) -> ObjectionRepository:
        return ObjectionRepository(self._require_connection(), self.tenant_id)

    @property
    def meetings(self) -> MeetingRepository:
        return MeetingRepository(self._require_connection(), self.tenant_id)

    @property
    def followups(self) -> FollowupRepository:
        return FollowupRepository(self._require_connection(), self.tenant_id)

    @property
    def tools(self) -> ToolRepository:
        return ToolRepository(self._require_connection(), self.tenant_id)

    @property
    def outcomes(self) -> OutcomeRepository:
        return OutcomeRepository(self._require_connection(), self.tenant_id)

    @property
    def events(self) -> EventRepository:
        return EventRepository(self._require_connection(), self.tenant_id)

    @property
    def projections(self) -> ProjectionRepository:
        return ProjectionRepository(self._require_connection(), self.tenant_id)

    @property
    def privacy(self) -> PrivacyRepository:
        return PrivacyRepository(self._require_connection(), self.tenant_id)

    @property
    def idempotency(self) -> IdempotencyRepository:
        return IdempotencyRepository(self._require_connection(), self.tenant_id)

    def _require_connection(self) -> Connection:
        if self._connection is None:
            raise RuntimeError("unit of work must be used as a context manager")
        return self._connection
