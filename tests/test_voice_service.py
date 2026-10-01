import threading
import unittest
from unittest.mock import MagicMock, patch

from wakeword.detect_microphone_service import VoiceService, CHUNK
from wakeword.events import Cancelled, State, check_cancelled
from wakeword.transcription_client import Transcript, TranscriptionError


class ServiceTests(unittest.TestCase):
    def setUp(self):
        logging = patch("wakeword.detect_microphone_service.logging")
        logging.start()
        self.addCleanup(logging.stop)

    def make_service(self, handler=None):
        self.events = []
        self.model = MagicMock()
        self.model.predict.return_value = {"hey_jarvis": 1.0}
        self.audio = MagicMock()
        self.stream = self.audio.open.return_value
        self.stream.get_read_available.return_value = CHUNK
        self.stream.read.return_value = b"\0\0" * CHUNK
        self.client = MagicMock()
        self.client.transcribe.return_value = Transcript("Abrir navegador.", "task-1")
        def on_event(event):
            self.events.append(event)
            if handler:
                handler(event)
        self.service = VoiceService(on_event, model_factory=lambda cancelled: self.model,
                                    audio_factory=lambda: self.audio, client_factory=lambda: self.client)
        return self.service

    def test_wake_word_upload_after_device_is_released_and_result_persists(self):
        def on_event(event):
            if event.state is State.RESULT:
                # A segunda abertura não deve apagar o resultado.
                self.model.predict.side_effect = lambda *a, **k: (self.service.stop() or {"hey_jarvis": 0})
        service = self.make_service(on_event)
        def transcribe(*args, **kwargs):
            self.stream.close.assert_called_once()
            self.audio.terminate.assert_called_once()
            return Transcript("Abrir navegador.", "task-1")
        self.client.transcribe.side_effect = transcribe
        with patch("wakeword.detect_microphone_service.record_audio", return_value="audio.wav"):
            service.run()
        states = [e.state for e in self.events]
        self.assertEqual(states[:4], [State.PREPARING, State.IDLE, State.LISTENING, State.PROCESSING])
        self.assertIn(State.RESULT, states)
        after_result = states[states.index(State.RESULT) + 1:]
        self.assertNotIn(State.IDLE, after_result)
        self.assertEqual(self.audio.open.call_count, 2)
        self.assertEqual(self.stream.close.call_count, 2)

    def test_manual_trigger_does_not_need_wake_prediction(self):
        def handler(event):
            if event.state is State.IDLE:
                for _ in range(10):
                    self.service.toggle()
            if event.state is State.RESULT:
                self.service.stop()
        service = self.make_service(handler)
        with patch("wakeword.detect_microphone_service.record_audio", return_value="audio.wav"):
            service.run()
        self.model.predict.assert_not_called()
        self.client.transcribe.assert_called_once()

    def test_cancel_recording_releases_device_without_upload(self):
        def handler(event):
            if event.state is State.LISTENING:
                self.service.toggle()
            if event.state is State.IDLE and any(e.state is State.CANCELLING for e in self.events):
                self.service.stop()
        service = self.make_service(handler)
        def recording(*args, **kwargs):
            check_cancelled(kwargs["cancelled"])
        with patch("wakeword.detect_microphone_service.record_audio", side_effect=recording):
            service.run()
        self.client.transcribe.assert_not_called()
        self.stream.close.assert_called_once()

    def test_cancel_processing_ignores_late_result(self):
        def handler(event):
            if event.state is State.IDLE and any(e.state is State.CANCELLING for e in self.events):
                self.service.stop()
        service = self.make_service(handler)
        def transcribe(*args, **kwargs):
            service.toggle()
            service._publish(State.RESULT, "stale", interaction_id=1)
            return Transcript("stale", "task-1")
        self.client.transcribe.side_effect = transcribe
        with patch("wakeword.detect_microphone_service.record_audio", return_value="audio.wav"):
            service.run()
        self.assertFalse(any(e.state is State.RESULT for e in self.events))
        self.stream.close.assert_called_once()

    def test_model_failure_retry_and_stop_during_preparation(self):
        def handler(event):
            if event.state is State.ERROR:
                self.service.toggle()
            if event.state is State.IDLE:
                self.service.stop()
        service = self.make_service(handler)
        service.model_factory = MagicMock(side_effect=[RuntimeError("model failure"), self.model])
        service.run()
        self.assertEqual(service.model_factory.call_count, 2)
        self.audio.terminate.assert_called_once()
        self.make_service()
        def cancelled_model(cancelled):
            self.service.stop()
            check_cancelled(cancelled)
        self.service.model_factory = cancelled_model
        self.service.run()
        self.audio.open.assert_not_called()

    def test_microphone_failure_can_retry(self):
        def handler(event):
            if event.state is State.ERROR:
                self.service.toggle()
            if event.state is State.IDLE:
                self.service.stop()
        service = self.make_service(handler)
        self.audio.open.side_effect = [OSError("no device"), self.stream]
        service.run()
        self.assertEqual(self.audio.terminate.call_count, 2)

    def test_transcription_failure_returns_to_listening(self):
        def handler(event):
            if event.state is State.ERROR:
                self.model.predict.side_effect = lambda *a, **k: (self.service.stop() or {"hey_jarvis": 0})
        service = self.make_service(handler)
        self.client.transcribe.side_effect = TranscriptionError("offline")
        with patch("wakeword.detect_microphone_service.record_audio", return_value="audio.wav"):
            service.run()
        self.assertEqual(self.audio.open.call_count, 2)
        self.assertTrue(any(e.state is State.ERROR for e in self.events))

    def test_stop_during_recording_or_processing_cleans_up(self):
        for stage in (State.LISTENING, State.PROCESSING):
            with self.subTest(stage=stage):
                def handler(event):
                    if event.state is stage:
                        self.service.stop()
                service = self.make_service(handler)
                def recording(*args, **kwargs):
                    check_cancelled(kwargs["cancelled"])
                    return "audio.wav"
                with patch("wakeword.detect_microphone_service.record_audio", side_effect=recording):
                    service.run()
                self.stream.close.assert_called_once()
                self.audio.terminate.assert_called_once()
                self.client.close.assert_called_once()
                self.assertEqual(service.snapshot.state, State.STOPPED)
                self.assertFalse(any(e.state is State.RESULT for e in self.events))
