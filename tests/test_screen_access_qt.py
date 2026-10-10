import unittest
import time
from unittest.mock import Mock
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication
from screen_access.backend import Element
from screen_access.session import ActionView, ActionState
from speakbar.controller import VoiceController, DemoController
from speakbar.overlay import MarkerWindow, convert_frame, monitor_for_screen
from wakeword.detect_microphone_service import VoiceService
from wakeword.events import State, VoiceEvent

APP = QApplication.instance() or QApplication([])


class FakeScreen(QObject):
    changed = Signal(object)
    invalidated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = Mock()
        self.session.active = False
        self.session.handle.return_value = False
        self.start = Mock()
        self.cancel = Mock()
        self.shutdown = Mock()


class QtIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.controller = VoiceController(screen_factory=FakeScreen)
        self.service = VoiceService()
        self.controller.thread = Mock()
        self.controller.thread.service = self.service

    def event(self, state, text=None):
        self.service._publish(state, text)
        event = self.service.snapshot
        self.controller._receive(event)
        return event

    def test_final_once_stale_result_and_cancel(self):
        self.service._interaction_id = 1
        event = self.event(State.RESULT, "clique em pesquisar")
        self.controller._receive(event)
        self.controller.screen.session.handle.assert_called_once_with("clique em pesquisar")
        stale = VoiceEvent(State.RESULT, "clique em fechar", 0, is_final=True)
        self.controller._receive(stale)
        self.assertEqual(self.controller.screen.session.handle.call_count, 1)
        self.controller.screen.session.active = True
        self.event(State.LISTENING)
        self.controller.screen.invalidated.emit()
        self.assertTrue(self.service.cancel_requested.is_set())
        self.assertTrue(self.controller._ignore_result)

    def test_automatic_response_uses_explicit_capture_not_toggle(self):
        self.service._interaction_id = 1
        self.event(State.RESULT, "clique em pesquisar")
        self.controller.screen.session.active = True
        self.controller.screen.changed.emit(ActionView("Confirmar?", capture=True, state=ActionState.CONFIRMING))
        APP.processEvents()
        self.assertTrue(self.service.trigger_requested.is_set())
        self.assertFalse(self.service.cancel_requested.is_set())
        self.assertEqual(self.controller.text, "Confirmar?")

    def test_no_speech_and_shutdown_invalidate_action(self):
        self.controller.screen.session.active = True
        self.event(State.ERROR, "Nenhuma fala")
        self.controller.screen.cancel.assert_called_once()
        self.controller.shutdown()
        self.controller.screen.shutdown.assert_called_once()
        self.assertTrue(self.service.stop_requested.is_set())

    def test_demo_has_no_screen_backend(self):
        demo = DemoController()
        self.assertFalse(hasattr(demo, "screen"))
        demo.shutdown()

    def test_capture_request_does_not_cancel_active_capture(self):
        self.service._publish(State.LISTENING)
        self.assertFalse(self.service.request_capture())
        self.assertFalse(self.service.cancel_requested.is_set())
        self.service._publish(State.RESULT)
        self.assertTrue(self.service.request_capture())


class OverlayTest(unittest.TestCase):
    def test_dpi_and_negative_origin(self):
        for ratio in (1, 1.25, 1.5):
            physical = (-1920, 0, 1920, 1080)
            logical = (-1920, 0, 1920/ratio, 1080/ratio)
            frame = (-1820, 100, 200, 60)
            result = convert_frame(frame, physical, logical)
            self.assertAlmostEqual(result[0], -1920+100/ratio)
            self.assertAlmostEqual(result[2], 200/ratio)
            self.assertEqual(monitor_for_screen("Friendly name", logical, ratio, {"DISPLAY2": physical}), physical)

    def test_overlay_does_not_accept_focus_or_mouse(self):
        window = MarkerWindow(APP.primaryScreen())
        self.assertTrue(window.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        self.assertTrue(window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        window.deleteLater()


class ControllerFlowTest(unittest.TestCase):
    def test_background_read_auto_capture_confirm_and_shutdown(self):
        from screen_access.backend import Backend, Window, Snapshot
        from screen_access.executor import ScreenExecutor
        from screen_access.session import ActionState
        from speakbar.screen_controller import ScreenController
        actions = []
        target = Window("test", 99999, "Teste", (0, 0, 800, 600))

        class FakeBackend(Backend):
            def foreground(self):
                return target

            def scan(self, *args):
                return Snapshot("test", target, (Element(1, "Pesquisar", "ButtonControl", (10, 10, 100, 30), "invoke"),))

            def activate(self, snapshot, number, cancelled):
                if not cancelled():
                    actions.append(number)

        class FakeVoice(VoiceService):
            def run(self):
                self._publish(State.IDLE)
                self.stop_requested.wait(3)
                self._publish(State.STOPPED)

        controller = VoiceController(service_factory=lambda callback: FakeVoice(callback),
                                     screen_factory=lambda parent: ScreenController(parent, lambda callback: ScreenExecutor(callback, FakeBackend)))

        def wait_for(predicate):
            deadline = time.monotonic()+2
            while not predicate() and time.monotonic() < deadline:
                APP.processEvents()
                time.sleep(0.005)
            self.assertTrue(predicate())

        try:
            controller.start()
            wait_for(lambda: controller.screen.session.last_window is not None)
            service = controller.thread.service
            service._interaction_id = 1
            service._publish(State.LISTENING)
            service._publish(State.RESULT, "clique em pesquisar")
            wait_for(lambda: controller.screen.session.state is ActionState.CONFIRMING)
            wait_for(service.trigger_requested.is_set)
            self.assertEqual(actions, [])
            service.trigger_requested.clear()
            service._interaction_id = 2
            service._publish(State.LISTENING)
            service._publish(State.RESULT, "confirmar")
            wait_for(lambda: actions == [1] and not controller.screen.session.active)
            self.assertEqual(controller.text, "Pedido aceito pelo aplicativo.")
        finally:
            controller.shutdown()
            controller.thread.wait(2000)
            controller.screen.executor.thread.join(2)
            APP.processEvents()
            self.assertFalse(controller.thread.isRunning())
