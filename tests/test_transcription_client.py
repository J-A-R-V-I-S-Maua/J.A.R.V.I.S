import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from wakeword.events import Cancelled
from wakeword.transcription_client import TranscriptionClient, TranscriptionError


def response(data):
    value = MagicMock()
    value.json.return_value = data
    value.__enter__.return_value = value
    return value


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.audio = Path(self.directory.name) / "test.wav"
        self.audio.write_bytes(b"audio")
        self.session = MagicMock()
        self.session.post.return_value = response({"task_id": "task-1"})
        self.client = TranscriptionClient(session=self.session)
        self.cancel = threading.Event()

    def result(self, status="SUCCESS", transcript=None, **extra):
        return response({"task_id": "task-1", "status": status,
                         "result": {"transcript": transcript if transcript is not None else [{"text": " Olá  mundo. "}]},
                         **extra})

    def test_pending_then_success_joins_segments(self):
        self.session.get.side_effect = [
            self.result("PENDING"),
            self.result(transcript=[{"text": " Abrir  o navegador "}, {"text": "e pesquisar.\n"}]),
        ]
        submitted = []
        with patch.object(self.cancel, "wait") as wait:
            result = self.client.transcribe(self.audio, self.cancel, submitted.append)
        self.assertEqual(result.text, "Abrir o navegador e pesquisar.")
        self.assertEqual(result.task_id, "task-1")
        self.assertEqual(submitted, ["task-1"])
        wait.assert_called_once()
        self.assertLessEqual(wait.call_args.args[0], 0.5)

    def test_empty_transcription_is_valid(self):
        self.session.get.return_value = self.result(transcript=[])
        self.assertEqual(self.client.transcribe(self.audio, self.cancel).text, "")

    def test_failure_and_malformed_responses(self):
        for data in (
            {"task_id": "task-1", "status": "FAILURE", "error": "bad audio"},
            {"task_id": "wrong", "status": "SUCCESS"},
            {"task_id": "task-1", "status": "SUCCESS", "result": {}},
            {"task_id": "task-1", "status": "SUCCESS", "result": {"transcript": [{"text": 4}]}},
            {"task_id": "task-1", "status": "UNKNOWN"}, [],
        ):
            with self.subTest(data=data):
                self.session.get.return_value = response(data)
                with self.assertRaises(TranscriptionError):
                    self.client.transcribe(self.audio, self.cancel)

    def test_api_unavailable(self):
        self.session.post.side_effect = requests.ConnectionError("offline")
        with self.assertRaisesRegex(TranscriptionError, "API"):
            self.client.transcribe(self.audio, self.cancel)

    def test_cancel_before_upload_and_after_late_response(self):
        self.cancel.set()
        with self.assertRaises(Cancelled):
            self.client.transcribe(self.audio, self.cancel)
        self.session.post.assert_not_called()
        self.cancel.clear()
        def late_response(*args, **kwargs):
            self.cancel.set()
            return self.result()
        self.session.get.side_effect = late_response
        with self.assertRaises(Cancelled):
            self.client.transcribe(self.audio, self.cancel)

    def test_polling_deadline(self):
        self.session.get.return_value = self.result("PENDING")
        with patch("wakeword.transcription_client.time.monotonic", side_effect=[0, 1, 2, 121]):
            with self.assertRaisesRegex(TranscriptionError, "demorou"):
                self.client.transcribe(self.audio, self.cancel)

    def test_cancel_during_wait(self):
        self.session.get.return_value = self.result("PENDING")
        with patch.object(self.cancel, "wait", side_effect=lambda _: self.cancel.set()):
            with self.assertRaises(Cancelled):
                self.client.transcribe(self.audio, self.cancel)
        self.assertEqual(self.session.get.call_count, 1)
