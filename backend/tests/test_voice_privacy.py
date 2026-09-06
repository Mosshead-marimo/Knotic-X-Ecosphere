from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from knotic_api.domain.identifiers import new_uuid7
from knotic_api.voice.privacy import (
    AudioTransmissionGate,
    InMemoryVoiceConsentStore,
    VoiceConsentRequest,
    VoiceConsentRequired,
    VoicePrivacyService,
)


def request(*, recording: bool = False, region: str = "EU") -> VoiceConsentRequest:
    return VoiceConsentRequest(
        consent_id=new_uuid7(),
        processing_allowed=True,
        recording_allowed=recording,
        policy_version="voice-processing-v1",
        media_region=region,
    )


def test_explicit_consent_is_required_and_expires() -> None:
    tenant_id, actor_id, session_id = new_uuid7(), new_uuid7(), new_uuid7()
    now = datetime.now(UTC)
    service = VoicePrivacyService(
        InMemoryVoiceConsentStore(), allowed_media_regions=frozenset({"EU"}), consent_ttl=timedelta(minutes=5)
    )
    with pytest.raises(VoiceConsentRequired):
        service.require(tenant_id=tenant_id, actor_id=actor_id, session_id=session_id, now=now)
    service.grant(tenant_id=tenant_id, actor_id=actor_id, session_id=session_id, request=request(), now=now)
    assert service.require(tenant_id=tenant_id, actor_id=actor_id, session_id=session_id, now=now)
    with pytest.raises(VoiceConsentRequired):
        service.require(tenant_id=tenant_id, actor_id=actor_id, session_id=session_id, now=now + timedelta(minutes=5))


def test_revocation_stops_future_processing() -> None:
    tenant_id, actor_id, session_id, now = new_uuid7(), new_uuid7(), new_uuid7(), datetime.now(UTC)
    service = VoicePrivacyService(InMemoryVoiceConsentStore(), allowed_media_regions=frozenset({"EU"}))
    service.grant(tenant_id=tenant_id, actor_id=actor_id, session_id=session_id, request=request(), now=now)
    service.revoke(tenant_id=tenant_id, actor_id=actor_id, session_id=session_id, now=now)
    with pytest.raises(VoiceConsentRequired):
        service.require(tenant_id=tenant_id, actor_id=actor_id, session_id=session_id, now=now)


def test_recording_and_unapproved_region_are_denied() -> None:
    with pytest.raises(ValidationError):
        request(recording=True)
    service = VoicePrivacyService(InMemoryVoiceConsentStore(), allowed_media_regions=frozenset({"EU"}))
    with pytest.raises(ValueError):
        service.grant(
            tenant_id=new_uuid7(),
            actor_id=new_uuid7(),
            session_id=new_uuid7(),
            request=request(region="US"),
            now=datetime.now(UTC),
        )


@pytest.mark.parametrize(
    ("consent", "muted", "allowed"),
    [(False, False, False), (False, True, False), (True, True, False), (True, False, True)],
)
def test_audio_transmission_requires_consent_and_unmuted_state(consent: bool, muted: bool, allowed: bool) -> None:
    assert AudioTransmissionGate(consent_active=consent, muted=muted).allows_transmission() is allowed
