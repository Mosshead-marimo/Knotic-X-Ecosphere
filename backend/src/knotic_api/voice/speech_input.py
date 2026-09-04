"""Realtime speech input turn aggregation (P4-T003).

Turns a stream of streaming-transcription provider events -- partial hypotheses, finalization,
per-utterance confidence, and language detection -- into an ordered, deduplicated stream of
:class:`SemanticTurn` values ready for ``understand_turn_node``. Only text, timestamps,
confidence, and language ever pass through this module: no raw audio byte is read, stored, or
logged here, so the "measured without storing unnecessary raw audio" requirement holds by
construction rather than by a separate redaction pass.

This module aggregates already-transcribed events; it does not itself speak to Agora, a
particular STT vendor, or a webhook transport -- those are a provider-specific adapter's job,
kept behind :class:`ProviderTranscriptEvent` so the vendor can change without touching this
ordering, deduplication, and confidence policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from knotic_api.domain.identifiers import new_uuid7
from knotic_api.workflow.contracts import SemanticTurn

DiscardReason = Literal["LOW_CONFIDENCE", "EMPTY_TEXT", "CANCELLED", "DUPLICATE", "TEXT_TOO_LONG"]

_DEFAULT_LOCALE = "en-US"
_LOCALE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_MAX_TEXT_LENGTH = 10_000


@dataclass(frozen=True, slots=True)
class ProviderTranscriptEvent:
    """One event from a streaming speech-to-text provider for a single Agora RTC session.

    ``provider_turn_id`` is whatever stable identifier the provider assigns to one spoken
    utterance; it is the basis for deduplicating retransmitted or replayed events (for example
    after a webhook redelivery or a reconnect), not a Knotic-issued identifier.
    """

    provider_turn_id: str
    is_final: bool
    text: str
    confidence: float
    language: str | None
    started_at: datetime
    ended_at: datetime
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class DiscardedTranscript:
    """A final provider event that was deliberately not turned into a semantic turn."""

    provider_turn_id: str
    reason: DiscardReason


class SpeechTurnAggregator:
    """Deterministic, per-session finalization of streaming transcription into semantic turns.

    One instance owns exactly one Agora RTC session's turn sequence; it is not thread-safe,
    matching that session's single-writer state ownership elsewhere in this codebase.
    """

    def __init__(
        self,
        *,
        tenant_id: UUID,
        session_id: UUID,
        actor_id: UUID,
        correlation_id: UUID,
        minimum_confidence: float = 0.55,
        default_locale: str = _DEFAULT_LOCALE,
    ) -> None:
        if not 0.0 <= minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be between 0 and 1")
        if not _LOCALE_PATTERN.fullmatch(default_locale):
            raise ValueError("default_locale must be a valid language tag")
        self._tenant_id = tenant_id
        self._session_id = session_id
        self._actor_id = actor_id
        self._correlation_id = correlation_id
        self._minimum_confidence = minimum_confidence
        self._default_locale = default_locale
        self._sequence = 0
        self._seen_provider_turn_ids: set[str] = set()

    def ingest(self, event: ProviderTranscriptEvent) -> SemanticTurn | DiscardedTranscript | None:
        """Process one provider event.

        Returns a finalized, ordered :class:`SemanticTurn`; a :class:`DiscardedTranscript` record
        naming why a *final* event produced no turn; or ``None`` for a still-streaming (non-final)
        event, which carries no decision yet.
        """
        if not event.is_final:
            return None
        if event.cancelled:
            return DiscardedTranscript(event.provider_turn_id, "CANCELLED")
        if event.provider_turn_id in self._seen_provider_turn_ids:
            return DiscardedTranscript(event.provider_turn_id, "DUPLICATE")
        text = event.text.strip()
        if not text:
            return DiscardedTranscript(event.provider_turn_id, "EMPTY_TEXT")
        if len(text) > _MAX_TEXT_LENGTH:
            return DiscardedTranscript(event.provider_turn_id, "TEXT_TOO_LONG")
        if event.confidence < self._minimum_confidence:
            return DiscardedTranscript(event.provider_turn_id, "LOW_CONFIDENCE")

        # Only IDs of *accepted* events are remembered: a duplicate of a discarded event (for
        # example, a noisy retry the provider itself resends unchanged) gets the same, stable
        # discard decision each time rather than flipping to DUPLICATE.
        self._seen_provider_turn_ids.add(event.provider_turn_id)
        self._sequence += 1
        locale = (
            event.language if event.language and _LOCALE_PATTERN.fullmatch(event.language) else self._default_locale
        )
        return SemanticTurn(
            tenant_id=self._tenant_id,
            session_id=self._session_id,
            turn_id=new_uuid7(),
            correlation_id=self._correlation_id,
            actor_id=self._actor_id,
            sequence=self._sequence,
            text=text[:_MAX_TEXT_LENGTH],
            locale=locale,
            occurred_at=event.ended_at,
        )
