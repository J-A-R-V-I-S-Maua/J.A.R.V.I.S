import tempfile
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from wakeword.events import Cancelled
from wakeword import model_loader


class DownloadTests(unittest.TestCase):
    def test_cancel_download_does_not_leave_partial_file(self):
        cancel = threading.Event()
        def chunks(**kwargs):
            yield b"partial model"
            cancel.set()
            yield b"more data"
        with tempfile.TemporaryDirectory() as directory, patch.object(model_loader.requests, "get") as get:
            response = get.return_value.__enter__.return_value
            response.iter_content.side_effect = chunks
            with self.assertRaises(Cancelled):
                model_loader._download_model("https://example.invalid/model.onnx",
                                             Path(directory) / "model.onnx", cancel.is_set)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cancel_before_model_preparation_avoids_network(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(model_loader, "model_directory", return_value=Path(directory)), \
                patch.object(model_loader.requests, "get") as get:
            with self.assertRaises(Cancelled):
                model_loader.create_model(cancelled=lambda: True)
            get.assert_not_called()
