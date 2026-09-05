"""Voice consent, data-minimization, regional, and mute policy (P4-T008)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.engine import Engine


class VoiceConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    consent_id: UUID
    processing_allowed: bool
    recording_allowed: bool = False
    policy_version: str = Field(min_length=1, max_length=64)
    media_region: str = Field(min_length=2, max_length=32)

    @model_validator(mode="after")
    def enforce_minimization(self) -> VoiceConsentRequest:
        if self.consent_id.version != 7:
            raise ValueError("consent_id must be UUIDv7")
        if not self.processing_allowed:
            raise ValueError("processing consent must be explicit")
        if self.recording_allowed:
            raise ValueError("raw call recording is disabled by policy")
        return self


@dataclass(frozen=True, slots=True)
class VoiceConsent:
    consent_id: UUID
    tenant_id: UUID
    actor_id: UUID
    session_id: UUID
    policy_version: str
    media_region: str
    granted_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    @property
    def active(self) -> bool:
        now = datetime.now(UTC)
        return self.revoked_at is None and self.expires_at > now


class VoiceConsentRequired(Exception):
    pass


class VoicePrivacyStoreUnavailable(RuntimeError):
    pass


class VoiceConsentStore(Protocol):
    def grant(self, consent: VoiceConsent) -> VoiceConsent: ...

    def active(self, *, tenant_id: UUID, actor_id: UUID, session_id: UUID, now: datetime) -> VoiceConsent | None: ...

    def revoke(self, *, tenant_id: UUID, actor_id: UUID, session_id: UUID, now: datetime) -> None: ...


class InMemoryVoiceConsentStore:
    def __init__(self) -> None:
        self._records: dict[tuple[UUID, UUID, UUID], VoiceConsent] = {}

    def grant(self, consent: VoiceConsent) -> VoiceConsent:
        key = (consent.tenant_id, consent.actor_id, consent.session_id)
        current = self._records.get(key)
        if current and current.consent_id != consent.consent_id:
            raise ValueError("active consent already exists")
        self._records[key] = consent
        return consent

    def active(self, *, tenant_id: UUID, actor_id: UUID, session_id: UUID, now: datetime) -> VoiceConsent | None:
        consent = self._records.get((tenant_id, actor_id, session_id))
        if consent is None or consent.revoked_at is not None or consent.expires_at <= now:
            return None
        return consent

    def revoke(self, *, tenant_id: UUID, actor_id: UUID, session_id: UUID, now: datetime) -> None:
        key = (tenant_id, actor_id, session_id)
        current = self._records.get(key)
        if current:
            self._records[key] = replace(current, revoked_at=now)


class PostgresVoiceConsentStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def grant(self, consent: VoiceConsent) -> VoiceConsent:
        try:
            with self._engine.begin() as connection:
                self._set_tenant(connection, consent.tenant_id)
                result = connection.execute(
                    sa.text(
                        "insert into voice_consents "
                        "(consent_id,tenant_id,actor_id,session_id,policy_version,media_region,granted_at,expires_at) "
                        "values (:consent_id,:tenant_id,:actor_id,:session_id,:policy_version,:media_region,"
                        ":granted_at,:expires_at) on conflict (tenant_id,actor_id,session_id) do update set "
                        "consent_id=excluded.consent_id,policy_version=excluded.policy_version,"
                        "media_region=excluded.media_region,granted_at=excluded.granted_at,"
                        "expires_at=excluded.expires_at,revoked_at=null "
                        "where voice_consents.consent_id=excluded.consent_id"
                    ),
                    asdict(consent),
                )
                if result.rowcount != 1:
                    raise ValueError("active consent already exists")
            return consent
        except sa.exc.SQLAlchemyError as error:
            raise VoicePrivacyStoreUnavailable("voice consent store is unavailable") from error

    def active(self, *, tenant_id: UUID, actor_id: UUID, session_id: UUID, now: datetime) -> VoiceConsent | None:
        try:
            with self._engine.begin() as connection:
                self._set_tenant(connection, tenant_id)
                row = (
                    connection.execute(
                        sa.text(
                            "select consent_id,tenant_id,actor_id,session_id,policy_version,media_region,"
                            "granted_at,expires_at,revoked_at from voice_consents where tenant_id=:tenant_id "
                            "and actor_id=:actor_id and session_id=:session_id and revoked_at is null "
                            "and expires_at>:now"
                        ),
                        {"tenant_id": tenant_id, "actor_id": actor_id, "session_id": session_id, "now": now},
                    )
                    .mappings()
                    .one_or_none()
                )
                return None if row is None else VoiceConsent(**row)
        except ValueError:
            raise
        except sa.exc.SQLAlchemyError as error:
            raise VoicePrivacyStoreUnavailable("voice consent store is unavailable") from error

    def revoke(self, *, tenant_id: UUID, actor_id: UUID, session_id: UUID, now: datetime) -> None:
        try:
            with self._engine.begin() as connection:
                self._set_tenant(connection, tenant_id)
                connection.execute(
                    sa.text(
                        "update voice_consents set revoked_at=:now where tenant_id=:tenant_id "
                        "and actor_id=:actor_id and session_id=:session_id and revoked_at is null"
                    ),
                    {"tenant_id": tenant_id, "actor_id": actor_id, "session_id": session_id, "now": now},
                )
        except sa.exc.SQLAlchemyError as error:
            raise VoicePrivacyStoreUnavailable("voice consent store is unavailable") from error

    @staticmethod
    def _set_tenant(connection: sa.Connection, tenant_id: UUID) -> None:
        connection.execute(sa.text("select set_config('app.tenant_id', :tenant, true)"), {"tenant": str(tenant_id)})


class VoicePrivacyService:
    def __init__(
        self,
        store: VoiceConsentStore,
        *,
        policy_version: str = "voice-processing-v1",
        allowed_media_regions: frozenset[str] = frozenset({"GLOBAL"}),
        consent_ttl: timedelta = timedelta(hours=8),
    ) -> None:
        if not policy_version or not allowed_media_regions:
            raise ValueError("voice privacy policy must be configured")
        self._store = store
        self._policy_version = policy_version
        self._allowed_media_regions = allowed_media_regions
        self._consent_ttl = consent_ttl

    def grant(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        session_id: UUID,
        request: VoiceConsentRequest,
        now: datetime,
    ) -> VoiceConsent:
        if request.policy_version != self._policy_version or request.media_region not in self._allowed_media_regions:
            raise ValueError("voice consent policy or media region is not allowed")
        consent = VoiceConsent(
            consent_id=request.consent_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            session_id=session_id,
            policy_version=request.policy_version,
            media_region=request.media_region,
            granted_at=now.astimezone(UTC),
            expires_at=now.astimezone(UTC) + self._consent_ttl,
        )
        return self._store.grant(consent)

    def require(self, *, tenant_id: UUID, actor_id: UUID, session_id: UUID, now: datetime) -> VoiceConsent:
        consent = self._store.active(tenant_id=tenant_id, actor_id=actor_id, session_id=session_id, now=now)
        if consent is None:
            raise VoiceConsentRequired("active voice processing consent is required")
        return consent

    def revoke(self, *, tenant_id: UUID, actor_id: UUID, session_id: UUID, now: datetime) -> None:
        self._store.revoke(tenant_id=tenant_id, actor_id=actor_id, session_id=session_id, now=now)


@dataclass(frozen=True, slots=True)
class AudioTransmissionGate:
    """Worker-side invariant: muted or unconsented audio can never be transmitted."""

    consent_active: bool
    muted: bool

    def allows_transmission(self) -> bool:
        return self.consent_active and not self.muted
