"""Calendar availability (P5-T004) and confirmed meeting booking (P5-T005).

Slots are always offered as part of one *snapshot*: a single batch of currently-available slots
that expires together (``snapshot_expires_at``). Booking requires the caller to hand back the
exact ``slot_id`` and ``snapshot_reference`` from an offer this service actually made, and the
provider re-checks both the snapshot's freshness and the slot's continued availability at booking
time -- a customer's selection is never assumed still valid just because it was valid when
offered, and a slot is never presented as booked before the provider confirms it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .integrations import IntegrationProviderError

_WORKING_HOUR_START = 9
_WORKING_HOUR_END = 17
_BUFFER_MINUTES = 15
_SNAPSHOT_FRESHNESS_SECONDS = 300
_MAX_SLOTS_PER_SNAPSHOT = 20
_ORGANIZER_TIMEZONE = "UTC"


@dataclass(frozen=True, slots=True)
class CalendarSlot:
    slot_id: str
    starts_at: datetime
    ends_at: datetime
    organizer: str


@dataclass(frozen=True, slots=True)
class SlotSnapshot:
    snapshot_reference: str
    slots: tuple[CalendarSlot, ...]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class BookedMeeting:
    meeting_id: UUID
    provider_reference: str
    slot: CalendarSlot


def validate_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise IntegrationProviderError(
            "INVALID_ARGUMENT", f"'{name}' is not a recognized IANA timezone.", retryable=False
        ) from error


def _working_hours_slots(
    *, tenant_id: UUID, window_from: datetime, window_to: datetime, duration_minutes: int, organizer: str
) -> list[CalendarSlot]:
    """Deterministic organizer-policy slot generation: weekday working hours only, each slot
    separated from the next by a buffer so back-to-back meetings never get scheduled flush against
    each other.

    ``slot_id`` is derived from ``(tenant_id, organizer, starts_at, ends_at)`` rather than
    randomly generated, so the *same* real-world slot maps to the same id across separate
    snapshots -- otherwise a slot booked from one snapshot could never be recognized and excluded
    from the next snapshot's listing.
    """
    organizer_tz = ZoneInfo(_ORGANIZER_TIMEZONE)
    step = timedelta(minutes=duration_minutes + _BUFFER_MINUTES)
    slot_span = timedelta(minutes=duration_minutes)
    window_start = window_from.astimezone(organizer_tz)
    window_end = window_to.astimezone(organizer_tz)
    slots: list[CalendarSlot] = []
    day_cursor = window_start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day_cursor < window_end and len(slots) < _MAX_SLOTS_PER_SNAPSHOT:
        if day_cursor.weekday() < 5:
            day_start = day_cursor.replace(hour=_WORKING_HOUR_START)
            day_end = day_cursor.replace(hour=_WORKING_HOUR_END)
            slot_start = max(day_start, window_start)
            while slot_start + slot_span <= day_end and slot_start < window_end and len(slots) < _MAX_SLOTS_PER_SNAPSHOT:
                starts_at = slot_start.astimezone(UTC)
                ends_at = (slot_start + slot_span).astimezone(UTC)
                slot_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"calendar-slot:{tenant_id}:{organizer}:{starts_at.isoformat()}:{ends_at.isoformat()}",
                    )
                )
                slots.append(CalendarSlot(slot_id=slot_id, starts_at=starts_at, ends_at=ends_at, organizer=organizer))
                slot_start = slot_start + step
        day_cursor = day_cursor + timedelta(days=1)
    return slots


class CalendarProviderPort(Protocol):
    def list_slots(
        self, *, tenant_id: UUID, window_from: datetime, window_to: datetime, duration_minutes: int, now: datetime
    ) -> SlotSnapshot: ...

    def book(
        self,
        *,
        tenant_id: UUID,
        slot_id: str,
        snapshot_reference: str,
        attendees: list[str],
        title: str,
        now: datetime,
    ) -> BookedMeeting: ...


class InMemoryCalendarProvider:
    """Deterministic sandbox calendar with one fixed organizer. A production adapter implements
    the same port against a real calendar API and is wired in behind ``call_with_resilience``."""

    def __init__(self, *, organizer: str = "sales@knotic.example") -> None:
        self._organizer = organizer
        self._snapshots: dict[tuple[UUID, str], SlotSnapshot] = {}
        self._booked_slot_ids: dict[UUID, set[str]] = {}

    def list_slots(
        self, *, tenant_id: UUID, window_from: datetime, window_to: datetime, duration_minutes: int, now: datetime
    ) -> SlotSnapshot:
        if window_to <= window_from:
            raise IntegrationProviderError("INVALID_ARGUMENT", "'to' must be after 'from'.", retryable=False)
        booked = self._booked_slot_ids.get(tenant_id, set())
        generated = _working_hours_slots(
            tenant_id=tenant_id,
            window_from=window_from,
            window_to=window_to,
            duration_minutes=duration_minutes,
            organizer=self._organizer,
        )
        slots = tuple(slot for slot in generated if slot.slot_id not in booked)
        reference = str(uuid5(NAMESPACE_URL, f"calendar-snapshot:{tenant_id}:{now.isoformat()}"))
        snapshot = SlotSnapshot(
            snapshot_reference=reference,
            slots=slots,
            expires_at=now + timedelta(seconds=_SNAPSHOT_FRESHNESS_SECONDS),
        )
        self._snapshots[(tenant_id, reference)] = snapshot
        return snapshot

    def book(
        self,
        *,
        tenant_id: UUID,
        slot_id: str,
        snapshot_reference: str,
        attendees: list[str],
        title: str,
        now: datetime,
    ) -> BookedMeeting:
        snapshot = self._snapshots.get((tenant_id, snapshot_reference))
        if snapshot is None:
            raise IntegrationProviderError(
                "CONFLICT", "The availability snapshot was not found or belongs to another tenant.", retryable=False
            )
        if now >= snapshot.expires_at:
            raise IntegrationProviderError(
                "CONFLICT", "The availability snapshot has expired; fetch new slots.", retryable=False
            )
        slot = next((item for item in snapshot.slots if item.slot_id == slot_id), None)
        if slot is None:
            raise IntegrationProviderError(
                "CONFLICT", "The selected slot is not part of this availability snapshot.", retryable=False
            )
        booked = self._booked_slot_ids.setdefault(tenant_id, set())
        if slot_id in booked:
            raise IntegrationProviderError(
                "CONFLICT", "The selected slot was already booked by another request.", retryable=False
            )
        booked.add(slot_id)
        meeting_id = uuid4()
        return BookedMeeting(
            meeting_id=meeting_id,
            provider_reference=str(uuid5(NAMESPACE_URL, f"calendar-meeting:{meeting_id}")),
            slot=slot,
        )


__all__ = [
    "BookedMeeting",
    "CalendarProviderPort",
    "CalendarSlot",
    "InMemoryCalendarProvider",
    "SlotSnapshot",
    "validate_timezone",
]
