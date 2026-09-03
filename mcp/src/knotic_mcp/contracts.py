"""Versioned MCP invocation contracts shared by the gateway and tool adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ToolStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    PENDING = "PENDING"


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str = Field(min_length=1, max_length=255)
    retryable: bool = False
    retry_after_seconds: int | None = Field(default=None, ge=1, le=3600)
    details: dict[str, str] = Field(default_factory=dict)


class TrustedContext(BaseModel):
    """Context accepted only from the authenticated backend workload."""

    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    actor_id: UUID
    session_id: UUID | None = None
    turn_id: UUID | None = None
    correlation_id: UUID
    scopes: frozenset[str]
    deadline_at: datetime
    approval_reference: str | None = Field(default=None, max_length=128)

    @field_validator("tenant_id", "actor_id", "session_id", "turn_id", "correlation_id")
    @classmethod
    def require_uuid7(cls, value: UUID | None) -> UUID | None:
        if value is not None and value.version != 7:
            raise ValueError("trusted identifiers must be UUIDv7")
        return value

    @field_validator("deadline_at")
    @classmethod
    def require_aware_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("deadline_at must include a timezone")
        return value.astimezone(UTC)


class ToolInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_call_id: UUID
    tool: str = Field(pattern=r"^[a-z][a-z0-9_.]{2,127}$")
    version: int = Field(ge=1, le=99)
    arguments: dict[str, Any]
    idempotency_key: str | None = Field(default=None, min_length=16, max_length=128)

    @field_validator("tool_call_id")
    @classmethod
    def require_uuid7(cls, value: UUID) -> UUID:
        if value.version != 7:
            raise ValueError("tool_call_id must be UUIDv7")
        return value


class ToolEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_call_id: UUID
    tool: str
    version: int
    status: ToolStatus
    data: dict[str, Any] | None = None
    error: ToolError | None = None
    provider_reference: str | None = None
    started_at: datetime
    completed_at: datetime | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.status == ToolStatus.SUCCEEDED and (self.data is None or self.error is not None):
            raise ValueError("successful results require data and no error")
        if self.status == ToolStatus.FAILED and self.error is None:
            raise ValueError("failed results require an error")
