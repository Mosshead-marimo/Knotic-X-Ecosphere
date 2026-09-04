import unittest

from knotic_api.voice.speech_output import (
    SpeechOutputSession,
    SynthesisProviderError,
    VoiceConfig,
    chunk_response_text,
)

_VOICE = VoiceConfig("agent-voice-1")


class RecordingSynthesisPort:
    def __init__(self, *, fails: bool = False) -> None:
        self.calls: list[str] = []
        self._fails = fails

    def synthesize(self, text: str, *, voice: VoiceConfig) -> bytes:
        self.calls.append(text)
        if self._fails:
            raise SynthesisProviderError("provider failed")
        return text.encode()


class CancelingSynthesisPort:
    """Cancels the session, from inside a synthesis call, after a chosen number of calls."""

    def __init__(self, session_holder: list[SpeechOutputSession], *, cancel_after_call_count: int) -> None:
        self._session_holder = session_holder
        self._call_count = 0
        self._cancel_after_call_count = cancel_after_call_count

    def synthesize(self, text: str, *, voice: VoiceConfig) -> bytes:
        self._call_count += 1
        if self._call_count == self._cancel_after_call_count:
            self._session_holder[0].cancel()
        return text.encode()


class ChunkResponseTextTests(unittest.TestCase):
    def test_splits_on_sentence_boundaries(self) -> None:
        chunks = chunk_response_text("First sentence. Second sentence! Third one?")

        self.assertEqual(chunks, ("First sentence.", "Second sentence!", "Third one?"))

    def test_empty_or_whitespace_text_yields_no_chunks(self) -> None:
        self.assertEqual(chunk_response_text("   "), ())
        self.assertEqual(chunk_response_text(""), ())

    def test_oversized_sentence_is_split_on_word_boundaries(self) -> None:
        sentence = "word " * 100
        chunks = chunk_response_text(sentence.strip() + ".", max_chunk_chars=40)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 40)
        self.assertEqual(" ".join(chunks), sentence.strip() + ".")

    def test_extremely_long_response_is_truncated_to_max_total_chunks(self) -> None:
        text = ". ".join(f"Sentence {i}" for i in range(500)) + "."

        chunks = chunk_response_text(text, max_total_chunks=10)

        self.assertEqual(len(chunks), 10)


class SpeechOutputSessionTests(unittest.TestCase):
    def test_stream_yields_every_chunk_in_order_for_a_normal_response(self) -> None:
        port = RecordingSynthesisPort()
        session = SpeechOutputSession("First sentence. Second sentence.", primary=port, voice=_VOICE)

        chunks = list(session.stream())

        self.assertEqual([chunk.text for chunk in chunks], ["First sentence.", "Second sentence."])
        self.assertEqual([chunk.index for chunk in chunks], [0, 1])
        self.assertEqual(session.delivered_text(), "First sentence. Second sentence.")
        self.assertEqual(session.truncated_text(), "")

    def test_cancel_before_streaming_yields_nothing(self) -> None:
        port = RecordingSynthesisPort()
        session = SpeechOutputSession("First sentence. Second sentence.", primary=port, voice=_VOICE)

        session.cancel()
        chunks = list(session.stream())

        self.assertEqual(chunks, [])
        self.assertEqual(port.calls, [])
        self.assertEqual(session.truncated_text(), "First sentence. Second sentence.")

    def test_cancel_between_chunks_stops_playback_at_the_confirmed_boundary(self) -> None:
        port = RecordingSynthesisPort()
        session = SpeechOutputSession("One. Two. Three.", primary=port, voice=_VOICE)

        delivered = []
        for chunk in session.stream():
            delivered.append(chunk)
            if chunk.index == 0:
                session.cancel()

        self.assertEqual([chunk.text for chunk in delivered], ["One."])
        self.assertEqual(session.delivered_text(), "One.")
        self.assertEqual(session.truncated_text(), "Two. Three.")

    def test_cancel_during_in_flight_synthesis_prevents_that_chunk_from_playing(self) -> None:
        holder: list[SpeechOutputSession] = []
        port = CancelingSynthesisPort(holder, cancel_after_call_count=2)
        session = SpeechOutputSession("One. Two. Three.", primary=port, voice=_VOICE)
        holder.append(session)

        chunks = list(session.stream())

        self.assertEqual([chunk.text for chunk in chunks], ["One."])
        self.assertEqual(session.truncated_text(), "Two. Three.")

    def test_repeated_cancel_never_moves_the_boundary_later(self) -> None:
        port = RecordingSynthesisPort()
        session = SpeechOutputSession("One. Two. Three.", primary=port, voice=_VOICE)

        iterator = session.stream()
        next(iterator)
        session.cancel()
        session.cancel()
        list(iterator)

        self.assertEqual(session.delivered_text(), "One.")

    def test_primary_failure_falls_back_to_the_fallback_provider(self) -> None:
        primary = RecordingSynthesisPort(fails=True)
        fallback = RecordingSynthesisPort()
        session = SpeechOutputSession("One sentence.", primary=primary, fallback=fallback, voice=_VOICE)

        chunks = list(session.stream())

        self.assertEqual(len(chunks), 1)
        self.assertEqual(fallback.calls, ["One sentence."])

    def test_failure_with_no_fallback_configured_propagates(self) -> None:
        primary = RecordingSynthesisPort(fails=True)
        session = SpeechOutputSession("One sentence.", primary=primary, voice=_VOICE)

        with self.assertRaises(SynthesisProviderError):
            list(session.stream())


class VoiceConfigTests(unittest.TestCase):
    def test_rejects_empty_voice_id(self) -> None:
        with self.assertRaises(ValueError):
            VoiceConfig("")

    def test_rejects_out_of_range_speaking_rate(self) -> None:
        with self.assertRaises(ValueError):
            VoiceConfig("voice", speaking_rate=3.0)


if __name__ == "__main__":
    unittest.main()
