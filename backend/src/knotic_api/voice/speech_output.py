"""AI speech output streaming pipeline (P4-T004).

Splits an already safety-checked response (the text `response_generation.validate_response`
approved) into speakable, sentence-first chunks so synthesis and playback can begin on the first
sentence without waiting for the whole response, tracks exactly how much text was actually
delivered versus cancelled so a barge-in (P4-T005) never has to guess a truncation boundary, and
falls over to a configured fallback voice provider without ever inventing text the response
generation node did not approve. This module never sees or produces model output itself -- it
only synthesizes and sequences text that has already passed governance.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_MAX_CHUNK_CHARS = 280
_MAX_TOTAL_CHUNKS = 200


@dataclass(frozen=True, slots=True)
class VoiceConfig:
    voice_id: str
    speaking_rate: float = 1.0

    def __post_init__(self) -> None:
        if not self.voice_id.strip():
            raise ValueError("voice_id must not be empty")
        if not 0.5 <= self.speaking_rate <= 2.0:
            raise ValueError("speaking_rate must be between 0.5 and 2.0")


@dataclass(frozen=True, slots=True)
class SpeechChunk:
    index: int
    text: str
    audio: bytes


class SpeechSynthesisPort(Protocol):
    def synthesize(self, text: str, *, voice: VoiceConfig) -> bytes: ...


class SynthesisProviderError(Exception):
    """A speech synthesis provider failed; the caller should try its configured fallback."""


def chunk_response_text(
    text: str, *, max_chunk_chars: int = _MAX_CHUNK_CHARS, max_total_chunks: int = _MAX_TOTAL_CHUNKS
) -> tuple[str, ...]:
    """Split approved response text into playback-ordered, speakable chunks.

    Splits on sentence boundaries first so the first chunk is playable as soon as the first
    sentence is available; a single sentence longer than ``max_chunk_chars`` is further split on
    word boundaries rather than dropped or synthesized as one oversized request. A response
    longer than ``max_total_chunks`` chunks is truncated defensively -- ordinary approved
    responses never approach this bound, so hitting it signals an upstream defect, not a
    legitimate long answer.
    """
    stripped = text.strip()
    if not stripped:
        return ()
    sentences = [sentence for sentence in _SENTENCE_BOUNDARY.split(stripped) if sentence]
    chunks: list[str] = []
    for sentence in sentences:
        chunks.extend(_split_long_sentence(sentence, max_chunk_chars))
    return tuple(chunks[:max_total_chunks])


def _split_long_sentence(sentence: str, max_chunk_chars: int) -> list[str]:
    if len(sentence) <= max_chunk_chars:
        return [sentence]
    parts: list[str] = []
    current = ""
    for word in sentence.split(" "):
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chunk_chars and current:
            parts.append(current)
            current = word
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


class SpeechOutputSession:
    """One approved response's synthesis/playback lifecycle: chunk, synthesize, and cancel.

    Create a new session per response; a session is not reused across turns. Consumers pull
    chunks from :meth:`stream` one at a time (so buffering is naturally bounded by how far ahead
    of playback the caller chooses to read) and call :meth:`cancel` as soon as a barge-in is
    detected -- ``stream`` never yields, and never synthesizes, anything requested after that
    point.
    """

    def __init__(
        self,
        text: str,
        *,
        primary: SpeechSynthesisPort,
        voice: VoiceConfig,
        fallback: SpeechSynthesisPort | None = None,
        max_chunk_chars: int = _MAX_CHUNK_CHARS,
    ) -> None:
        self._chunks = chunk_response_text(text, max_chunk_chars=max_chunk_chars)
        self._primary = primary
        self._fallback = fallback
        self._voice = voice
        self._cancelled_at_chunk: int | None = None
        self._delivered_chunks: list[SpeechChunk] = []

    @property
    def total_chunks(self) -> int:
        return len(self._chunks)

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled_at_chunk is not None

    def cancel(self) -> None:
        """Confirm cancellation. Idempotent: a later call never moves the boundary later."""
        if self._cancelled_at_chunk is None:
            self._cancelled_at_chunk = len(self._delivered_chunks)

    def stream(self) -> Iterator[SpeechChunk]:
        """Yield synthesized chunks in order, stopping immediately once cancelled.

        A chunk already yielded before cancellation is never retracted: it was already confirmed
        delivered. Matches FR-02 -- interrupted text is not treated as heard beyond the delivered
        boundary, and nothing already spoken is claimed unheard.
        """
        for index, text in enumerate(self._chunks):
            if self.is_cancelled:
                return
            audio = self._synthesize(text)
            if self.is_cancelled:
                # Cancellation landed while synthesis was in flight: never play audio requested
                # after the confirmed cancellation boundary, even though it already exists.
                return
            chunk = SpeechChunk(index=index, text=text, audio=audio)
            self._delivered_chunks.append(chunk)
            yield chunk

    def delivered_text(self) -> str:
        """The text of every chunk actually handed to playback, in order."""
        return " ".join(chunk.text for chunk in self._delivered_chunks)

    def truncated_text(self) -> str:
        """The text cancelled before it was ever delivered; empty if never cancelled."""
        if not self.is_cancelled:
            return ""
        return " ".join(self._chunks[len(self._delivered_chunks) :])

    def _synthesize(self, text: str) -> bytes:
        try:
            return self._primary.synthesize(text, voice=self._voice)
        except SynthesisProviderError:
            if self._fallback is None:
                raise
            return self._fallback.synthesize(text, voice=self._voice)
