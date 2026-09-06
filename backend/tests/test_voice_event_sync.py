from datetime import UTC, datetime

import pytest

from knotic_api.domain.identifiers import new_uuid7
from knotic_api.voice.event_sync import (
    InMemoryVoiceEventStore,
    VoiceControlEvent,
    VoiceEventKind,
    VoiceEventSequenceConflict,
    VoiceEventSynchronizer,
)


def event(*, stream_id, sequence: int, event_id=None, kind=VoiceEventKind.RTC_CONNECTED):
    return VoiceControlEvent(
        event_id=event_id or new_uuid7(),
        stream_id=stream_id,
        sequence=sequence,
        event_type=kind,
        occurred_at=datetime.now(UTC),
    )


def test_duplicate_and_out_of_order_events_cannot_corrupt_timeline() -> None:
    tenant_id, session_id, stream_id = new_uuid7(), new_uuid7(), new_uuid7()
    synchronizer = VoiceEventSynchronizer(InMemoryVoiceEventStore())
    first_event = event(stream_id=stream_id, sequence=1)

    first = synchronizer.accept(tenant_id=tenant_id, session_id=session_id, event=first_event)
    duplicate = synchronizer.accept(tenant_id=tenant_id, session_id=session_id, event=first_event)

    assert first.server_sequence == duplicate.server_sequence == 1
    assert not first.duplicate and duplicate.duplicate
    with pytest.raises(VoiceEventSequenceConflict) as conflict:
        synchronizer.accept(tenant_id=tenant_id, session_id=session_id, event=event(stream_id=stream_id, sequence=3))
    assert conflict.value.expected_sequence == 2


def test_multi_tab_streams_share_one_server_timeline_and_replay_after_restart() -> None:
    tenant_id, session_id = new_uuid7(), new_uuid7()
    store = InMemoryVoiceEventStore()
    first_process = VoiceEventSynchronizer(store)
    tab_a, tab_b = new_uuid7(), new_uuid7()
    ack_a = first_process.accept(tenant_id=tenant_id, session_id=session_id, event=event(stream_id=tab_a, sequence=1))
    ack_b = first_process.accept(tenant_id=tenant_id, session_id=session_id, event=event(stream_id=tab_b, sequence=1))

    restarted_process = VoiceEventSynchronizer(store)
    replay = restarted_process.reconcile(tenant_id=tenant_id, session_id=session_id, after=0)

    assert [ack.server_sequence for ack in replay] == [ack_a.server_sequence, ack_b.server_sequence] == [1, 2]


def test_same_sequence_with_changed_payload_is_a_conflict() -> None:
    tenant_id, session_id, stream_id = new_uuid7(), new_uuid7(), new_uuid7()
    synchronizer = VoiceEventSynchronizer(InMemoryVoiceEventStore())
    event_id = new_uuid7()
    synchronizer.accept(
        tenant_id=tenant_id, session_id=session_id, event=event(stream_id=stream_id, sequence=1, event_id=event_id)
    )

    with pytest.raises(VoiceEventSequenceConflict):
        synchronizer.accept(
            tenant_id=tenant_id,
            session_id=session_id,
            event=event(stream_id=stream_id, sequence=1, event_id=event_id, kind=VoiceEventKind.RTC_DISCONNECTED),
        )


def test_sensitive_or_oversized_control_payloads_are_rejected() -> None:
    with pytest.raises(ValueError):
        VoiceControlEvent(
            event_id=new_uuid7(),
            stream_id=new_uuid7(),
            sequence=1,
            event_type=VoiceEventKind.CLIENT_READY,
            occurred_at=datetime.now(UTC),
            payload={"transcript": "secret"},
        )
