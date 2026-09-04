import unittest
from datetime import UTC, datetime

from knotic_api.voice.barge_in import BargeInController, BargeInPolicy, VoiceActivitySignal
from knotic_api.voice.speech_output import SpeechOutputSession, VoiceConfig

_NOW = datetime(2026, 9, 4, tzinfo=UTC)
_VOICE = VoiceConfig("agent-voice-1")


class _RecordingSynthesisPort:
    def synthesize(self, text: str, *, voice: VoiceConfig) -> bytes:
        return text.encode()


def _playing_session(text: str = "One. Two. Three.") -> SpeechOutputSession:
    return SpeechOutputSession(text, primary=_RecordingSynthesisPort(), voice=_VOICE)


def _interrupted_record():
    controller = BargeInController()
    session = _playing_session()
    next(session.stream())
    return controller.interrupt(session, at=_NOW)


class BargeInPolicyTests(unittest.TestCase):
    def test_rejects_low_confidence_burst_as_a_false_positive(self) -> None:
        policy = BargeInPolicy(minimum_confidence=0.6, minimum_duration_ms=200)

        signal = VoiceActivitySignal(detected_at=_NOW, confidence=0.2, duration_ms=500)

        self.assertFalse(policy.is_genuine_interruption(signal))

    def test_rejects_too_brief_burst_as_a_false_positive(self) -> None:
        policy = BargeInPolicy(minimum_confidence=0.6, minimum_duration_ms=200)

        signal = VoiceActivitySignal(detected_at=_NOW, confidence=0.95, duration_ms=50)

        self.assertFalse(policy.is_genuine_interruption(signal))

    def test_accepts_a_confident_and_sustained_burst(self) -> None:
        policy = BargeInPolicy(minimum_confidence=0.6, minimum_duration_ms=200)

        signal = VoiceActivitySignal(detected_at=_NOW, confidence=0.9, duration_ms=400)

        self.assertTrue(policy.is_genuine_interruption(signal))

    def test_rejects_invalid_policy_bounds(self) -> None:
        with self.assertRaises(ValueError):
            BargeInPolicy(minimum_confidence=1.5)
        with self.assertRaises(ValueError):
            BargeInPolicy(minimum_duration_ms=-1)


class BargeInControllerTests(unittest.TestCase):
    def test_should_interrupt_matches_the_configured_policy(self) -> None:
        controller = BargeInController(BargeInPolicy(minimum_confidence=0.6, minimum_duration_ms=200))

        self.assertFalse(controller.should_interrupt(VoiceActivitySignal(_NOW, confidence=0.1, duration_ms=500)))
        self.assertTrue(controller.should_interrupt(VoiceActivitySignal(_NOW, confidence=0.9, duration_ms=500)))

    def test_interrupt_cancels_playback_and_records_the_delivered_and_truncated_boundary(self) -> None:
        controller = BargeInController()
        session = _playing_session()
        iterator = session.stream()
        next(iterator)  # "One." is delivered before the customer starts talking.

        record = controller.interrupt(session, at=_NOW)

        self.assertTrue(session.is_cancelled)
        self.assertEqual(record.delivered_text, "One.")
        self.assertEqual(record.truncated_text, "Two. Three.")
        self.assertEqual(list(iterator), [])

    def test_repeated_barge_in_on_the_same_response_keeps_the_first_boundary(self) -> None:
        controller = BargeInController()
        session = _playing_session()
        iterator = session.stream()
        next(iterator)

        first = controller.interrupt(session, at=_NOW)
        second = controller.interrupt(session, at=_NOW)

        self.assertEqual(first, second)

    def test_two_near_simultaneous_interrupt_calls_produce_one_consistent_boundary(self) -> None:
        # Models a race: two barge-in signals arrive for the same response in the same instant.
        controller = BargeInController()
        session = _playing_session()
        iterator = session.stream()
        next(iterator)

        first = controller.interrupt(session, at=_NOW)
        second = controller.interrupt(session, at=_NOW)

        self.assertEqual(first.delivered_text, second.delivered_text)
        self.assertEqual(first.truncated_text, second.truncated_text)

    def test_rapid_turn_interruptions_across_separate_responses_stay_independent(self) -> None:
        controller = BargeInController()
        first_session = _playing_session("First response sentence one. First response sentence two.")
        second_session = _playing_session("Second response sentence one. Second response sentence two.")

        next(first_session.stream())
        first_record = controller.interrupt(first_session, at=_NOW)
        next(second_session.stream())
        second_record = controller.interrupt(second_session, at=_NOW)

        self.assertEqual(first_record.delivered_text, "First response sentence one.")
        self.assertEqual(second_record.delivered_text, "Second response sentence one.")

    def test_resumes_previous_topic_when_the_interruption_was_a_false_start(self) -> None:
        record = _interrupted_record()

        resume = BargeInController.should_resume_previous_topic(
            interruption=record, next_turn_text=None, previous_topic="enterprise pricing"
        )

        self.assertTrue(resume)

    def test_resumes_previous_topic_when_the_new_utterance_still_references_it(self) -> None:
        record = _interrupted_record()

        resume = BargeInController.should_resume_previous_topic(
            interruption=record,
            next_turn_text="Sorry, can you repeat the enterprise pricing details?",
            previous_topic="enterprise pricing",
        )

        self.assertTrue(resume)

    def test_does_not_resume_when_the_customer_changed_topic(self) -> None:
        record = _interrupted_record()

        resume = BargeInController.should_resume_previous_topic(
            interruption=record,
            next_turn_text="Actually, do you support single sign-on?",
            previous_topic="enterprise pricing",
        )

        self.assertFalse(resume)


if __name__ == "__main__":
    unittest.main()
