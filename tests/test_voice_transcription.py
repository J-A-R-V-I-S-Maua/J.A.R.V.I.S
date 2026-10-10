import asyncio
import unittest
from unittest.mock import Mock, patch

from worker.streaming_engine import StreamingSession
from wakeword.speech_gate import SpeechConfig, SpeechGate


class TranscriptionSessionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.decoded = []
        self.messages = []

        async def decode(pcm):
            self.decoded.append(pcm)
            return "Abra o navegador"

        async def send(message):
            self.messages.append(message)

        self.session = StreamingSession(decode, send)
        self.session.start("command-1")

    async def asyncTearDown(self):
        await self.session.close()

    async def test_waits_for_finish_then_decodes_complete_audio_once(self):
        chunks = [bytes([index % 256, 0]) * 1280 for index in range(250)]
        for chunk in chunks:  # 20 seconds: no partial responses are required.
            self.session.feed(chunk)
            await asyncio.sleep(0)
        self.assertEqual(self.decoded, [])
        self.assertEqual(self.messages, [])

        self.session.finish()
        await self.session.task
        self.assertEqual(self.decoded, [b"".join(chunks)])
        self.assertEqual(self.messages, [{
            "type": "final", "interaction_id": "command-1",
            "sequence": 1, "text": "Abra o navegador",
        }])

    async def test_cancel_during_capture_does_not_decode(self):
        self.session.feed(b"\x01\x00" * 1280)
        self.session.cancel()
        await self.session.task
        self.assertEqual(self.decoded, [])
        self.assertEqual(self.messages, [])

    async def test_cancel_during_inference_discards_result(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def decode(pcm):
            started.set()
            await release.wait()
            return "Feche o bloco de notas"

        self.session.decode = decode
        self.session.feed(b"\x01\x00" * 1280)
        self.session.finish()
        await asyncio.wait_for(started.wait(), timeout=1)
        self.session.cancel()
        release.set()
        await self.session.task
        self.assertEqual(self.messages, [])

    async def test_audio_cannot_be_added_after_finish(self):
        self.session.feed(b"\x01\x00" * 1280)
        self.session.finish()
        with self.assertRaises(ValueError):
            self.session.feed(b"\x01\x00" * 1280)
        await self.session.task


class SpeechEndTest(unittest.TestCase):
    def test_short_pause_keeps_capture_open_and_two_seconds_end_it(self):
        gate = SpeechGate(SpeechConfig())
        for _ in range(3):
            self.assertIsNone(gate.feed(0.9))
        for _ in range(10):
            self.assertIsNone(gate.feed(0.0))
        self.assertIsNone(gate.feed(0.9))
        for _ in range(24):
            self.assertIsNone(gate.feed(0.0))
        self.assertEqual(gate.feed(0.0), "finish")

    def test_no_speech_ends_without_transcription(self):
        gate = SpeechGate(SpeechConfig())
        for _ in range(124):
            self.assertIsNone(gate.feed(0.0))
        self.assertEqual(gate.feed(0.0), "no_speech")


class VoiceCaptureTest(unittest.TestCase):
    def test_preserves_quiet_onset_before_voice_detection(self):
        from wakeword.streaming_service import StreamingVoiceService

        service = StreamingVoiceService(speech_config=SpeechConfig())
        service.connection = Mock()
        service.vad = Mock()
        service._interaction_id = 1
        # 560 ms abaixo do limiar + 160 ms para confirmar voz = 720 ms.
        service.vad.predict.side_effect = [0.1] * 7 + [0.9] * 2 + [0.0] * 25
        chunks = [bytes([index, 0]) * 1280 for index in range(34)]
        with patch("wakeword.streaming_service._read_chunk", side_effect=chunks):
            service._stream_audio(None, None, 1)
        sent = [call.args[0] for call in service.connection.send_audio.call_args_list]
        self.assertEqual(sent, chunks)

    def test_bar_waits_until_end_of_speech_and_audio_is_preserved(self):
        from wakeword.events import State
        from wakeword.streaming_service import StreamingVoiceService

        events = []
        service = StreamingVoiceService(events.append, speech_config=SpeechConfig())
        service.connection = Mock()
        service.vad = Mock()
        service._interaction_id = 1
        service._publish(State.LISTENING, interaction_id=1)
        # 20 seconds of speech followed by the two-second silence threshold.
        probabilities = [0.9] * 250 + [0.0] * 25
        service.vad.predict.side_effect = probabilities
        chunks = [bytes([index % 256, 0]) * 1280 for index in range(275)]

        def read_chunk(stream, cancelled):
            self.assertEqual(events[-1].state, State.LISTENING)
            return chunks[read_chunk.index]

        read_chunk.index = 0

        def next_chunk(stream, cancelled):
            chunk = read_chunk(stream, cancelled)
            read_chunk.index += 1
            return chunk

        with patch("wakeword.streaming_service._read_chunk", side_effect=next_chunk):
            service._stream_audio(None, None, 1)

        service.connection.begin.assert_called_once_with(1)
        sent = [call.args[0] for call in service.connection.send_audio.call_args_list]
        self.assertEqual(sent, chunks)
        self.assertEqual([event.state for event in events], [State.LISTENING, State.PROCESSING])
        self.assertEqual([event.text for event in events], ["Ouvindo…", "Transcrevendo…"])


class MicrophoneSelectionTest(unittest.TestCase):
    def setUp(self):
        self.audio = Mock()
        self.audio.get_device_count.return_value = 3
        self.audio.get_default_input_device_info.return_value = {
            "index": 1, "name": "Default microphone", "maxInputChannels": 1,
        }
        self.audio.get_device_info_by_index.return_value = {
            "index": 2, "name": "Selected microphone", "maxInputChannels": 1,
        }

    def test_default_and_explicit_input(self):
        from wakeword.microphone import select_input_device
        with patch.dict("os.environ", {"MICROPHONE_DEVICE_INDEX": ""}):
            self.assertEqual(select_input_device(self.audio), 1)
        with patch.dict("os.environ", {"MICROPHONE_DEVICE_INDEX": "2"}):
            self.assertEqual(select_input_device(self.audio), 2)
            self.audio.get_device_info_by_index.assert_called_once_with(2)

    def test_rejects_invalid_index_and_output_device(self):
        from wakeword.microphone import select_input_device
        for configured in ("-1", "3", "invalid"):
            with self.subTest(configured=configured):
                with patch.dict("os.environ", {"MICROPHONE_DEVICE_INDEX": configured}):
                    with self.assertRaises(ValueError):
                        select_input_device(self.audio)
        self.audio.get_device_info_by_index.return_value["maxInputChannels"] = 0
        with patch.dict("os.environ", {"MICROPHONE_DEVICE_INDEX": "2"}):
            with self.assertRaises(ValueError):
                select_input_device(self.audio)


if __name__ == "__main__":
    unittest.main()
