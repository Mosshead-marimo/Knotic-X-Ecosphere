import unittest
from datetime import UTC, datetime, timedelta

from knotic_api.domain.identifiers import new_uuid7
from knotic_api.voice.speech_input import DiscardedTranscript, ProviderTranscriptEvent, SpeechTurnAggregator

_NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _event(
    provider_turn_id: str = "turn-1",
    *,
    is_final: bool = True,
    text: str = "I need pricing for fifty seats.",
    confidence: float = 0.9,
    language: str | None = "en-US",
    cancelled: bool = False,
    offset_seconds: float = 0.0,
) -> ProviderTranscriptEvent:
    started = _NOW + timedelta(seconds=offset_seconds)
    return ProviderTranscriptEvent(
        provider_turn_id=provider_turn_id,
        is_final=is_final,
        text=text,
        confidence=confidence,
        language=language,
        started_at=started,
        ended_at=started + timedelta(seconds=2),
        cancelled=cancelled,
    )


class SpeechTurnAggregatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.aggregator = SpeechTurnAggregator(
            tenant_id=new_uuid7(), session_id=new_uuid7(), actor_id=new_uuid7(), correlation_id=new_uuid7()
        )

    def test_non_final_events_produce_no_decision(self) -> None:
        self.assertIsNone(self.aggregator.ingest(_event(is_final=False)))

    def test_final_event_becomes_a_sequenced_semantic_turn(self) -> None:
        turn = self.aggregator.ingest(_event())

        self.assertIsNotNone(turn)
        assert turn is not None and not isinstance(turn, DiscardedTranscript)
        self.assertEqual(turn.sequence, 1)
        self.assertEqual(turn.text, "I need pricing for fifty seats.")
        self.assertEqual(turn.locale, "en-US")

    def test_turns_are_ordered_by_acceptance_even_out_of_provider_order(self) -> None:
        # Simulates packet loss/reordering: the second-spoken utterance's final event arrives
        # first over the network.
        first = self.aggregator.ingest(_event("turn-b", text="Second thing I said.", offset_seconds=5))
        second = self.aggregator.ingest(_event("turn-a", text="First thing I said.", offset_seconds=0))

        assert first is not None and not isinstance(first, DiscardedTranscript)
        assert second is not None and not isinstance(second, DiscardedTranscript)
        self.assertEqual(first.sequence, 1)
        self.assertEqual(second.sequence, 2)

    def test_duplicate_provider_turn_id_is_discarded_not_reprocessed(self) -> None:
        first = self.aggregator.ingest(_event("turn-1"))
        duplicate = self.aggregator.ingest(_event("turn-1"))

        assert first is not None and not isinstance(first, DiscardedTranscript)
        self.assertIsInstance(duplicate, DiscardedTranscript)
        assert isinstance(duplicate, DiscardedTranscript)
        self.assertEqual(duplicate.reason, "DUPLICATE")

    def test_low_confidence_noise_is_discarded_and_never_reaches_understanding(self) -> None:
        result = self.aggregator.ingest(_event(confidence=0.2))

        self.assertIsInstance(result, DiscardedTranscript)
        assert isinstance(result, DiscardedTranscript)
        self.assertEqual(result.reason, "LOW_CONFIDENCE")

    def test_silence_produces_empty_text_and_is_discarded(self) -> None:
        result = self.aggregator.ingest(_event(text="   "))

        self.assertIsInstance(result, DiscardedTranscript)
        assert isinstance(result, DiscardedTranscript)
        self.assertEqual(result.reason, "EMPTY_TEXT")

    def test_cancelled_final_event_is_discarded(self) -> None:
        result = self.aggregator.ingest(_event(cancelled=True))

        self.assertIsInstance(result, DiscardedTranscript)
        assert isinstance(result, DiscardedTranscript)
        self.assertEqual(result.reason, "CANCELLED")

    def test_oversized_transcript_is_discarded(self) -> None:
        result = self.aggregator.ingest(_event(text="x" * 10_001))

        self.assertIsInstance(result, DiscardedTranscript)
        assert isinstance(result, DiscardedTranscript)
        self.assertEqual(result.reason, "TEXT_TOO_LONG")

    def test_unrecognized_or_missing_language_falls_back_to_the_default_locale(self) -> None:
        unrecognized = self.aggregator.ingest(_event("turn-a", language="not a tag"))
        missing = self.aggregator.ingest(_event("turn-b", language=None))

        assert unrecognized is not None and not isinstance(unrecognized, DiscardedTranscript)
        assert missing is not None and not isinstance(missing, DiscardedTranscript)
        self.assertEqual(unrecognized.locale, "en-US")
        self.assertEqual(missing.locale, "en-US")

    def test_accent_variant_language_tags_are_accepted_as_is(self) -> None:
        turn = self.aggregator.ingest(_event(language="en-IN"))

        assert turn is not None and not isinstance(turn, DiscardedTranscript)
        self.assertEqual(turn.locale, "en-IN")

    def test_rejects_out_of_range_minimum_confidence(self) -> None:
        with self.assertRaises(ValueError):
            SpeechTurnAggregator(
                tenant_id=new_uuid7(),
                session_id=new_uuid7(),
                actor_id=new_uuid7(),
                correlation_id=new_uuid7(),
                minimum_confidence=1.5,
            )


if __name__ == "__main__":
    unittest.main()
