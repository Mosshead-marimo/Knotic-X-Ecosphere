from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from knotic_mcp.calendar import InMemoryCalendarProvider, validate_timezone
from knotic_mcp.integrations import IntegrationProviderError

_TENANT = uuid4()
# A Monday, so a five-business-day window always contains working-hours slots.
_MONDAY = datetime(2026, 9, 7, tzinfo=UTC)


def test_validate_timezone_accepts_iana_names_and_rejects_junk() -> None:
    assert validate_timezone("America/New_York") == ZoneInfo("America/New_York")
    with pytest.raises(IntegrationProviderError) as excinfo:
        validate_timezone("Not/AZone")
    assert excinfo.value.code == "INVALID_ARGUMENT"


def test_list_slots_only_offers_weekday_working_hours() -> None:
    provider = InMemoryCalendarProvider()
    snapshot = provider.list_slots(
        tenant_id=_TENANT,
        window_from=_MONDAY,
        window_to=_MONDAY + timedelta(days=7),
        duration_minutes=30,
        now=_MONDAY,
    )
    assert snapshot.slots
    for slot in snapshot.slots:
        assert slot.starts_at.weekday() < 5
        assert 9 <= slot.starts_at.hour < 17
        assert slot.ends_at - slot.starts_at == timedelta(minutes=30)


def test_list_slots_rejects_an_inverted_window() -> None:
    provider = InMemoryCalendarProvider()
    with pytest.raises(IntegrationProviderError) as excinfo:
        provider.list_slots(
            tenant_id=_TENANT,
            window_from=_MONDAY,
            window_to=_MONDAY - timedelta(hours=1),
            duration_minutes=30,
            now=_MONDAY,
        )
    assert excinfo.value.code == "INVALID_ARGUMENT"


def test_list_slots_excludes_already_booked_slots() -> None:
    provider = InMemoryCalendarProvider()
    window_to = _MONDAY + timedelta(days=1)
    first = provider.list_slots(
        tenant_id=_TENANT, window_from=_MONDAY, window_to=window_to, duration_minutes=30, now=_MONDAY
    )
    booked_slot = first.slots[0]
    provider.book(
        tenant_id=_TENANT,
        slot_id=booked_slot.slot_id,
        snapshot_reference=first.snapshot_reference,
        attendees=["prospect@example.com"],
        title="Discovery call",
        now=_MONDAY,
    )
    second = provider.list_slots(
        tenant_id=_TENANT, window_from=_MONDAY, window_to=window_to, duration_minutes=30, now=_MONDAY
    )
    assert booked_slot.slot_id not in {slot.slot_id for slot in second.slots}


def test_book_requires_a_slot_from_a_still_valid_snapshot() -> None:
    provider = InMemoryCalendarProvider()
    snapshot = provider.list_slots(
        tenant_id=_TENANT,
        window_from=_MONDAY,
        window_to=_MONDAY + timedelta(days=1),
        duration_minutes=30,
        now=_MONDAY,
    )
    slot = snapshot.slots[0]

    with pytest.raises(IntegrationProviderError) as unknown_snapshot:
        provider.book(
            tenant_id=_TENANT,
            slot_id=slot.slot_id,
            snapshot_reference="not-a-real-snapshot",
            attendees=["a@example.com"],
            title="Call",
            now=_MONDAY,
        )
    assert unknown_snapshot.value.code == "CONFLICT"

    with pytest.raises(IntegrationProviderError) as unknown_slot:
        provider.book(
            tenant_id=_TENANT,
            slot_id="not-a-real-slot",
            snapshot_reference=snapshot.snapshot_reference,
            attendees=["a@example.com"],
            title="Call",
            now=_MONDAY,
        )
    assert unknown_slot.value.code == "CONFLICT"

    with pytest.raises(IntegrationProviderError) as expired:
        provider.book(
            tenant_id=_TENANT,
            slot_id=slot.slot_id,
            snapshot_reference=snapshot.snapshot_reference,
            attendees=["a@example.com"],
            title="Call",
            now=snapshot.expires_at + timedelta(seconds=1),
        )
    assert expired.value.code == "CONFLICT"


def test_book_confirms_once_and_rejects_a_second_booking_of_the_same_slot() -> None:
    provider = InMemoryCalendarProvider()
    snapshot = provider.list_slots(
        tenant_id=_TENANT,
        window_from=_MONDAY,
        window_to=_MONDAY + timedelta(days=1),
        duration_minutes=30,
        now=_MONDAY,
    )
    slot = snapshot.slots[0]
    booked = provider.book(
        tenant_id=_TENANT,
        slot_id=slot.slot_id,
        snapshot_reference=snapshot.snapshot_reference,
        attendees=["a@example.com"],
        title="Call",
        now=_MONDAY,
    )
    assert booked.slot.slot_id == slot.slot_id
    assert booked.provider_reference

    with pytest.raises(IntegrationProviderError) as excinfo:
        provider.book(
            tenant_id=_TENANT,
            slot_id=slot.slot_id,
            snapshot_reference=snapshot.snapshot_reference,
            attendees=["a@example.com"],
            title="Call",
            now=_MONDAY,
        )
    assert excinfo.value.code == "CONFLICT"
