from datetime import UTC, datetime, timedelta

import pytest

from knotic_api.domain.identifiers import new_uuid7
from knotic_api.voice.recovery import (
    InMemoryRecoveryStore,
    RecoveryAction,
    RecoveryPolicy,
    RecoveryStatus,
    VoiceFault,
    VoiceRecoveryCoordinator,
)


@pytest.mark.parametrize(
    "fault",
    [
        VoiceFault.AGORA_TOKEN,
        VoiceFault.AGORA_CONNECTION,
        VoiceFault.SPEECH_INPUT,
        VoiceFault.SPEECH_OUTPUT,
        VoiceFault.NETWORK,
        VoiceFault.BACKEND,
        VoiceFault.REDIS,
    ],
)
def test_recoverable_dependency_faults_start_bounded_recovery(fault: VoiceFault) -> None:
    coordinator = VoiceRecoveryCoordinator(InMemoryRecoveryStore())
    decision = coordinator.record_failure(
        tenant_id=new_uuid7(), session_id=new_uuid7(), fault=fault, now=datetime.now(UTC)
    )
    assert decision.action == RecoveryAction.RETRY
    assert decision.snapshot.status == RecoveryStatus.RECOVERING
    assert decision.snapshot.safe_message and "error" not in decision.snapshot.safe_message.casefold()


def test_speech_provider_fails_over_only_at_the_configured_boundary() -> None:
    tenant_id, session_id, now = new_uuid7(), new_uuid7(), datetime.now(UTC)
    coordinator = VoiceRecoveryCoordinator(InMemoryRecoveryStore(), RecoveryPolicy(max_attempts=2))
    assert (
        coordinator.record_failure(
            tenant_id=tenant_id, session_id=session_id, fault=VoiceFault.SPEECH_OUTPUT, now=now
        ).action
        == RecoveryAction.RETRY
    )
    assert (
        coordinator.record_failure(
            tenant_id=tenant_id, session_id=session_id, fault=VoiceFault.SPEECH_OUTPUT, now=now
        ).action
        == RecoveryAction.FAILOVER
    )
    assert (
        coordinator.record_failure(
            tenant_id=tenant_id, session_id=session_id, fault=VoiceFault.SPEECH_OUTPUT, now=now
        ).action
        == RecoveryAction.TERMINATE
    )


def test_timeout_terminates_without_claiming_recovery() -> None:
    tenant_id, session_id, now = new_uuid7(), new_uuid7(), datetime.now(UTC)
    coordinator = VoiceRecoveryCoordinator(InMemoryRecoveryStore(), RecoveryPolicy(overall_timeout_seconds=5))
    coordinator.record_failure(tenant_id=tenant_id, session_id=session_id, fault=VoiceFault.NETWORK, now=now)
    expired = coordinator.record_failure(
        tenant_id=tenant_id, session_id=session_id, fault=VoiceFault.NETWORK, now=now + timedelta(seconds=5)
    )
    assert expired.action == RecoveryAction.TERMINATE
    assert expired.snapshot.status == RecoveryStatus.FAILED


def test_new_coordinator_resumes_persisted_state_after_backend_restart() -> None:
    tenant_id, session_id, now = new_uuid7(), new_uuid7(), datetime.now(UTC)
    store = InMemoryRecoveryStore()
    VoiceRecoveryCoordinator(store).record_failure(
        tenant_id=tenant_id, session_id=session_id, fault=VoiceFault.BACKEND, now=now
    )
    restarted = VoiceRecoveryCoordinator(store)
    recovered = restarted.record_recovered(tenant_id=tenant_id, session_id=session_id, now=now)
    assert recovered.status == RecoveryStatus.ACTIVE
    assert recovered.attempt == 0


def test_policy_fault_and_clean_end_are_terminal_and_idempotently_preserved() -> None:
    tenant_id, session_id, now = new_uuid7(), new_uuid7(), datetime.now(UTC)
    store = InMemoryRecoveryStore()
    coordinator = VoiceRecoveryCoordinator(store)
    denied = coordinator.record_failure(tenant_id=tenant_id, session_id=session_id, fault=VoiceFault.POLICY, now=now)
    assert denied.action == RecoveryAction.TERMINATE
    ended = coordinator.terminate(tenant_id=tenant_id, session_id=session_id, now=now)
    assert ended.status == RecoveryStatus.ENDED
    assert ended.safe_message == "The call ended safely."
