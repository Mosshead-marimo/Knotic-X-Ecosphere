"""Barge-in detection and interrupted-response state (P4-T005).

Ties the speech input pipeline's voice-activity signal (P4-T003) to the speech output session's
cancellation boundary (P4-T004): customer speech strong enough to clear the false-positive guard
immediately cancels output -- input is prioritized over output -- and the exact delivered and
truncated text is recorded so nothing already spoken is claimed unheard, and nothing cut off is
claimed heard (FR-02). Interruption is idempotent and per-response, so a customer who interrupts
repeatedly, or interrupts again moments after the agent starts its next turn, always gets a
single, consistent delivered/truncated boundary for whichever response was actually playing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .speech_output import SpeechOutputSession


@dataclass(frozen=True, slots=True)
class VoiceActivitySignal:
    """A raw voice-activity detection burst, before speech-to-text has confirmed any words.

    Barge-in must react before transcription finishes -- waiting for a final transcript would
    defeat the purpose of low-latency interruption -- so this is deliberately cheaper and less
    certain than a :class:`~knotic_api.voice.speech_input.SemanticTurn`.
    """

    detected_at: datetime
    confidence: float
    duration_ms: int


@dataclass(frozen=True, slots=True)
class BargeInPolicy:
    """Thresholds a voice-activity burst must clear before it is treated as a real interruption.

    Both bounds exist to reject false positives -- a brief noise spike or a cough -- without
    delaying a genuine interruption: a burst must be both confident enough and long enough.
    """

    minimum_confidence: float = 0.6
    minimum_duration_ms: int = 200

    def __post_init__(self) -> None:
        if not 0.0 <= self.minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be between 0 and 1")
        if self.minimum_duration_ms < 0:
            raise ValueError("minimum_duration_ms must not be negative")

    def is_genuine_interruption(self, signal: VoiceActivitySignal) -> bool:
        return signal.confidence >= self.minimum_confidence and signal.duration_ms >= self.minimum_duration_ms


@dataclass(frozen=True, slots=True)
class InterruptionRecord:
    """The delivered/truncated boundary for one interrupted response.

    Constructed once per interrupted :class:`SpeechOutputSession`; repeated interruption of the
    same session returns the same record rather than a new one, since the true boundary was fixed
    the moment cancellation was first confirmed.
    """

    detected_at: datetime
    delivered_text: str
    truncated_text: str


class BargeInController:
    """Applies :class:`BargeInPolicy` and confirms interruption on a playing response."""

    def __init__(self, policy: BargeInPolicy | None = None) -> None:
        self._policy = policy or BargeInPolicy()

    def should_interrupt(self, signal: VoiceActivitySignal) -> bool:
        """Whether this voice-activity burst is strong enough to interrupt playback.

        A caller that gets ``True`` back should stop playback immediately -- input is
        prioritized over output -- rather than waiting for this method to also finalize the
        interruption record.
        """
        return self._policy.is_genuine_interruption(signal)

    def interrupt(self, session: SpeechOutputSession, *, at: datetime) -> InterruptionRecord:
        """Confirm interruption of ``session`` and return its delivered/truncated boundary.

        Idempotent: calling this more than once for the same session (a customer who keeps
        talking, or two barge-in signals arriving in quick succession) never changes the
        boundary, since :meth:`SpeechOutputSession.cancel` only ever records the earliest one.
        """
        session.cancel()
        return InterruptionRecord(
            detected_at=at, delivered_text=session.delivered_text(), truncated_text=session.truncated_text()
        )

    @staticmethod
    def should_resume_previous_topic(
        *, interruption: InterruptionRecord, next_turn_text: str | None, previous_topic: str | None
    ) -> bool:
        """Whether the agent's next turn should pick the interrupted response back up.

        ``next_turn_text`` is the customer's finalized interrupting utterance, if speech-to-text
        ever confirmed one; ``None`` or empty means the voice-activity burst never became a real
        turn (a false start), so there is nothing new to address and the interrupted topic is
        still the right one to resume. When a genuine new utterance did arrive, resuming is safe
        only while ``previous_topic`` is unset (nothing to have moved on from) or unmistakably
        still referenced by what the customer just said; anything else is treated as a topic
        change so the agent addresses what was actually just said instead of talking over it.
        """
        del interruption  # Reserved for future use (e.g. a minimum spoken-fraction threshold).
        if not next_turn_text or not next_turn_text.strip():
            return True
        if not previous_topic:
            return True
        return previous_topic.strip().casefold() in next_turn_text.casefold()
