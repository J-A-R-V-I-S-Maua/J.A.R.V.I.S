import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
import unittest
from unittest.mock import MagicMock
from PySide6.QtCore import QPoint, QThread, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from speakbar.controller import DemoController, VoiceController
from speakbar.window import Speakbar
from wakeword.detect_microphone_service import VoiceService
from wakeword.events import State, VoiceEvent


class SpeakbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.controller = DemoController()
        self.window = Speakbar(controller=self.controller, demo=True)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.controller.timer.stop()
        self.window.tray.hide()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()

    def show_text(self, text):
        self.controller.current_event = VoiceEvent(State.RESULT, text)
        self.controller.state_changed.emit(self.controller.current_event)
        self.app.processEvents()

    def test_full_text_expands_upwards_and_scrolls(self):
        bottom = self.window.y() + self.window.height()
        text = "Abrir o navegador e pesquisar por vídeos no YouTube. " * 20
        self.show_text(text)
        self.assertEqual(self.window.message.toPlainText(), text)
        self.assertGreater(self.window.height(), 60)
        self.assertLessEqual(self.window.height(), 100)
        self.assertEqual(self.window.y() + self.window.height(), bottom)
        bar = self.window.message.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self.window.message.setFocus()
        QTest.keyClick(self.window.message, Qt.Key.Key_PageDown)
        self.assertGreater(bar.value(), 0)
        self.show_text("Olá!")
        self.assertEqual(self.window.height(), 60)
        self.assertEqual(self.window.y() + self.window.height(), bottom)

    def test_text_stays_inside_screen_near_top(self):
        self.window.move(0, 0)
        self.show_text("Texto longo para mostrar na barra. " * 20)
        self.assertGreaterEqual(self.window.y(), 0)

    def test_keyboard_and_drag(self):
        self.window.activateWindow()
        self.window.voice.setFocus()
        self.app.processEvents()
        QTest.keyClick(self.window.voice, Qt.Key.Key_Return)
        self.assertEqual(self.controller.state, State.LISTENING)
        QTest.keyClick(self.window.voice, Qt.Key.Key_Space)
        self.assertEqual(self.controller.state, State.IDLE)
        original = self.window.pos()
        center = self.window.message.viewport().rect().center()
        QTest.mousePress(self.window.message.viewport(), Qt.MouseButton.LeftButton, pos=center)
        QTest.mouseMove(self.window.message.viewport(), center + QPoint(30,-20))
        QTest.mouseRelease(self.window.message.viewport(), Qt.MouseButton.LeftButton, pos=center)
        self.assertNotEqual(self.window.pos(), original)

    def test_controller_ignores_queued_old_interaction(self):
        controller = VoiceController()
        service = VoiceService()
        controller.thread = MagicMock()
        controller.thread.service = service
        old = service.snapshot
        service.stop()
        controller._receive(old)
        self.assertNotEqual(controller.current_event, service.snapshot)
        controller._receive(service.snapshot)
        self.assertEqual(controller.state, State.STOPPING)
        controller.thread = None

    def test_shutdown_waits_for_worker_during_model_preparation(self):
        worker_thread = []
        def factory(callback):
            def model(cancelled):
                worker_thread.append(QThread.currentThread())
                while not cancelled():
                    time.sleep(0.01)
                from wakeword.events import Cancelled
                raise Cancelled()
            return VoiceService(callback, model_factory=model, client_factory=MagicMock)
        controller = VoiceController(service_factory=factory)
        controller.start()
        QTest.qWait(50)
        controller.shutdown()
        deadline = time.monotonic() + 3
        while controller.thread.isRunning() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(controller.thread.isRunning())
        self.assertTrue(worker_thread)
        self.assertIsNot(worker_thread[0], self.app.thread())
        controller.deleteLater()
