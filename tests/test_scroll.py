"""Rolagem nativa simulada, diálogo e isolamento da captura de continuação."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from screen_access.backend import Element, Snapshot, Window, ScreenError, Backend
from screen_access.scroll import ScrollRequest, ScrollTargets, ScrollOutcome, parse_scroll, SCROLL_DURATIONS
from screen_access.session import ScreenSession, ActionState
from screen_access.windows import WindowsBackend
from wakeword.events import Reason, State
from wakeword.detect_microphone_service import VoiceService
from wakeword.speech_gate import SpeechConfig
from wakeword.streaming_service import StreamingVoiceService, NoSpeech


def node(role, name, identity, frame=(0, 0, 800, 600), parent=None, scrollable=False):
    obj = Mock()
    obj.ControlTypeName, obj.Name = role, name
    obj.IsEnabled, obj.IsOffscreen, obj.ProcessId = True, False, 12345
    obj.NativeWindowHandle = 100 if role == "WindowControl" else 0
    x, y, width, height = frame
    obj.BoundingRectangle = SimpleNamespace(left=x, top=y, right=x+width, bottom=y+height)
    obj.GetRuntimeId.return_value = [identity]
    obj.GetParentControl.return_value = parent
    obj.GetChildren.return_value = []
    obj.GetPattern.return_value = None
    if scrollable:
        pattern = Mock()
        pattern.VerticallyScrollable, pattern.VerticalScrollPercent, pattern.VerticalViewSize = True, 25., 20.
        def set_percent(horizontal, vertical, waitTime):
            pattern.VerticalScrollPercent = vertical
            return True
        pattern.SetScrollPercent.side_effect = set_percent
        obj.GetPattern.return_value = pattern
    return obj


class ScrollParsingTest(unittest.TestCase):
    def test_explicit_variants_and_intensities(self):
        for phrase in ("role para baixo", "rolar para baixo", "descer a tela", "desça a tela", "Role a tela para baixo!"):
            self.assertEqual(parse_scroll(phrase), ScrollRequest("down"))
        for phrase in ("role um pouco para cima", "role para cima um pouco"):
            self.assertEqual(parse_scroll(phrase), ScrollRequest("up", "small"))
        self.assertEqual(parse_scroll("role uma página para baixo"), ScrollRequest("down", "page"))
        self.assertEqual(parse_scroll("subir a tela"), ScrollRequest("up"))

    def test_continuation_is_contextual(self):
        old = ScrollRequest("down", "page")
        self.assertIsNone(parse_scroll("mais"))
        self.assertIsNone(parse_scroll("para cima"))
        self.assertEqual(parse_scroll("mais", old), old)
        self.assertEqual(parse_scroll("para cima", old), ScrollRequest("up", "page"))
        for text in ("digitar role para baixo", "role um pouco para baixo uma pagina", "role para baixo e clique em enviar", "role para a direita"):
            self.assertIsNone(parse_scroll(text, old))


class ScrollBackendTest(unittest.TestCase):
    def setUp(self):
        self.now = 1000.
        def advance(seconds):
            self.now += seconds
        clock = patch("screen_access.scroll.time.monotonic", side_effect=lambda: self.now)
        sleep = patch("screen_access.scroll.time.sleep", side_effect=advance)
        clock.start()
        sleep.start()
        self.addCleanup(clock.stop)
        self.addCleanup(sleep.stop)
        self.root = node("WindowControl", "Teste", 1)
        self.left = node("PaneControl", "Esquerda", 2, (0, 0, 350, 500), self.root, True)
        self.right = node("PaneControl", "Direita", 3, (400, 0, 350, 500), self.root, True)
        self.focused = node("ButtonControl", "Campo", 4, (10, 10, 50, 20), self.left)
        self.root.GetChildren.return_value = [self.left, self.right]
        self.api = Mock()
        self.api.ControlFromHandle.return_value = self.root
        self.api.GetForegroundControl.return_value = self.root
        self.api.GetFocusedControl.return_value = self.focused
        self.backend = WindowsBackend(self.api)
        self.window = self.backend.foreground()

    def prepare(self):
        return self.backend.prepare_scroll(self.window)

    def test_nearest_scrollable_ancestor_and_explicit_ambiguity(self):
        result = self.prepare()
        self.assertEqual(len(result.snapshot.elements), 2)
        self.assertEqual(result.preferred, 1)
        self.api.GetFocusedControl.return_value = self.root
        self.assertIsNone(self.prepare().preferred)

    def test_one_area_automatically_selected_unless_scan_partial(self):
        self.root.GetChildren.return_value = [self.left]
        self.api.GetFocusedControl.return_value = self.root
        self.assertEqual(self.prepare().preferred, 1)
        partial = self.backend.prepare_scroll(self.window, max_elements=2)
        # Só há dois elementos e a caminhada completa não foi truncada.
        self.assertFalse(partial.snapshot.truncated)
        self.root.GetChildren.return_value = [self.left, self.right]
        partial = self.backend.prepare_scroll(self.window, max_elements=2)
        self.assertTrue(partial.snapshot.truncated)
        self.assertIsNone(partial.preferred)

    def test_remembers_external_focus_when_bar_is_focused(self):
        self.backend.remember_focus(self.window)
        ours = node("ButtonControl", "Speakbar", 99)
        import os
        ours.ProcessId = os.getpid()
        self.api.GetFocusedControl.return_value = ours
        self.assertEqual(self.prepare().preferred, 1)

    def test_intensities_use_viewport_and_preserve_horizontal_position(self):
        for intensity, delta in (("small", 3.75), ("normal", 12.5), ("page", 22.5)):
            self.left.GetPattern.return_value.VerticalScrollPercent = 25
            snapshot = self.prepare().snapshot
            result = self.backend.scroll(snapshot, 1, ScrollRequest("down", intensity))
            self.assertEqual(result, ScrollOutcome.MOVED)
            self.left.GetPattern.return_value.SetScrollPercent.assert_called_with(-1, 25 + delta, waitTime=0)

    def test_boundaries_do_not_issue_native_actions(self):
        for percent, direction, outcome in ((0, "up", ScrollOutcome.START), (100, "down", ScrollOutcome.END)):
            self.left.GetPattern.return_value.VerticalScrollPercent = percent
            snapshot = self.prepare().snapshot
            self.assertEqual(self.backend.scroll(snapshot, 1, ScrollRequest(direction)), outcome)
        self.left.GetPattern.return_value.SetScrollPercent.assert_not_called()

    def test_smooth_motion_is_monotonic_eased_and_reaches_same_distance(self):
        for direction in ("down", "up"):
            pattern = self.left.GetPattern.return_value
            pattern.VerticalScrollPercent = 50
            pattern.SetScrollPercent.reset_mock()
            snapshot = self.prepare().snapshot
            began = self.now
            self.backend.scroll(snapshot, 1, ScrollRequest(direction))
            positions = [call.args[1] for call in pattern.SetScrollPercent.call_args_list]
            distances = [(position - 50) * (1 if direction == "down" else -1) for position in positions]
            self.assertGreater(len(positions), 10)
            self.assertEqual(distances, sorted(distances))
            self.assertAlmostEqual(distances[-1], 12.5)
            self.assertLess(distances[0], .15)
            increments = [distances[0]] + [b-a for a, b in zip(distances, distances[1:])]
            self.assertGreater(max(increments), increments[0] * 3)
            self.assertLess(increments[-1], max(increments) / 2)
            self.assertAlmostEqual(self.now - began, SCROLL_DURATIONS["normal"])

    def test_cancel_during_animation_stops_before_target(self):
        snapshot = self.prepare().snapshot
        pattern = self.left.GetPattern.return_value
        with self.assertRaises(ScreenError):
            self.backend.scroll(snapshot, 1, ScrollRequest("down"), cancelled=lambda: pattern.SetScrollPercent.call_count >= 3)
        self.assertEqual(pattern.SetScrollPercent.call_count, 3)
        self.assertLess(pattern.VerticalScrollPercent, 37.5)

    def test_window_or_area_change_during_animation_stops_remaining_frames(self):
        for change in ("window", "area"):
            self.left.IsOffscreen = False
            self.api.GetForegroundControl.return_value = self.root
            pattern = self.left.GetPattern.return_value
            pattern.SetScrollPercent.reset_mock()
            pattern.VerticalScrollPercent = 25
            snapshot = self.prepare().snapshot
            def move(horizontal, vertical, waitTime):
                pattern.VerticalScrollPercent = vertical
                if change == "area":
                    self.left.IsOffscreen = True
                else:
                    other = node("WindowControl", "Outra", 22)
                    other.NativeWindowHandle = 200
                    self.api.GetForegroundControl.return_value = other
                return True
            pattern.SetScrollPercent.side_effect = move
            with self.assertRaises(ScreenError):
                self.backend.scroll(snapshot, 1, ScrollRequest("down"))
            pattern.SetScrollPercent.assert_called_once()

    def test_window_change_stale_area_and_cancel_prevent_execution(self):
        snapshot = self.prepare().snapshot
        with self.assertRaises(ScreenError):
            self.backend.scroll(snapshot, 1, ScrollRequest("down"), cancelled=lambda: True)
        self.left.IsOffscreen = True
        with self.assertRaises(ScreenError):
            self.backend.scroll(snapshot, 1, ScrollRequest("down"))
        self.left.IsOffscreen = False
        self.left.Name = "Outro painel"
        with self.assertRaises(ScreenError):
            self.backend.scroll(snapshot, 1, ScrollRequest("down"))
        self.left.Name = "Esquerda"
        other = node("WindowControl", "Outra", 5)
        other.NativeWindowHandle = 200
        self.api.GetForegroundControl.return_value = other
        with self.assertRaises(ScreenError):
            self.backend.scroll(snapshot, 1, ScrollRequest("down"))
        self.left.GetPattern.return_value.SetScrollPercent.assert_not_called()

    def test_scan_for_numbers_does_not_destroy_scroll_reference(self):
        snapshot = self.prepare().snapshot
        self.backend.scan(self.window)
        self.assertEqual(self.backend.scroll(snapshot, 1, ScrollRequest("down")), ScrollOutcome.MOVED)

    def test_refusal_is_not_retried_and_unknown_position_is_not_end(self):
        snapshot = self.prepare().snapshot
        pattern = self.left.GetPattern.return_value
        pattern.SetScrollPercent.side_effect = None
        pattern.SetScrollPercent.return_value = False
        with self.assertRaises(ScreenError):
            self.backend.scroll(snapshot, 1, ScrollRequest("down"))
        pattern.SetScrollPercent.assert_called_once()
        pattern.VerticalScrollPercent, pattern.VerticalViewSize = -1, -1
        pattern.Scroll.return_value = True
        with patch("screen_access.scroll.time.monotonic", side_effect=[0, 1]):
            self.assertEqual(self.backend.scroll(snapshot, 1, ScrollRequest("down", "small")), ScrollOutcome.REQUESTED)
        pattern.Scroll.assert_called_once_with(2, 4, waitTime=0)

    def test_other_platforms_have_explicit_failure(self):
        with self.assertRaisesRegex(ScreenError, "Windows"):
            Backend().prepare_scroll(self.window)


class ScrollSessionTest(unittest.TestCase):
    def setUp(self):
        self.executor, self.views = Mock(), []
        self.session = ScreenSession(self.executor, self.views.append)
        self.window = Window("100", 12345, "Teste", (0, 0, 800, 600))
        self.session.last_window = self.window
        self.session.begin_capture()
        self.snapshot = Snapshot("scroll", self.window, (
            Element(1, "Esquerda", "PaneControl", (0, 0, 350, 500), "scroll"),
            Element(2, "Direita", "PaneControl", (400, 0, 350, 500), "scroll")))

    def start(self, preferred=1, text="role para baixo"):
        self.assertTrue(self.session.handle(text))
        self.assertEqual(self.executor.submit.call_args.args[1], "prepare_scroll")
        self.session.receive(self.session.token, "prepare_scroll", ScrollTargets(self.snapshot, preferred))

    def complete(self, outcome=ScrollOutcome.MOVED):
        self.session.receive(self.session.token, "scroll", outcome)

    def test_direct_scroll_repeat_reverse_and_stop(self):
        self.start(text="role uma página para baixo")
        self.assertEqual(self.executor.submit.call_args.args[1:], ("scroll", self.snapshot, 1, ScrollRequest("down", "page")))
        self.complete()
        self.assertTrue(self.views[-1].capture)
        self.assertEqual(self.session.capture_timeout, 5)
        self.session.handle("mais")
        self.assertEqual(self.executor.submit.call_args.args[-1], ScrollRequest("down", "page"))
        self.complete()
        self.session.handle("para cima")
        self.assertEqual(self.executor.submit.call_args.args[-1], ScrollRequest("up", "page"))
        self.complete()
        count = self.executor.submit.call_count
        self.session.handle("parar")
        self.assertFalse(self.session.active)
        self.assertIn("Rolagem encerrada", self.views[-1].text)
        self.assertEqual(self.executor.submit.call_count, count)

    def test_area_choice_executes_without_confirmation(self):
        self.start(preferred=None)
        self.assertEqual(self.session.state, ActionState.SCROLL_CHOOSING)
        self.assertEqual(self.views[-1].markers, self.snapshot.elements)
        self.session.handle("dois")
        self.assertEqual(self.executor.submit.call_args.args[1:3], ("scroll", self.snapshot))
        self.assertEqual(self.executor.submit.call_args.args[3], 2)

    def test_numbers_are_hidden_then_rescanned_and_selection_still_confirms(self):
        self.session.handle("mostrar números")
        controls = Snapshot("old", self.window, (Element(1, "Antigo", "ButtonControl", (0, 0, 20, 20), "invoke"),))
        self.session.receive(self.session.token, "scan", controls)
        self.start()
        self.assertFalse(self.views[-1].markers)
        self.complete()
        self.assertEqual(self.executor.submit.call_args.args[1], "scan")
        fresh = Snapshot("new", self.window, (Element(1, "Novo", "ButtonControl", (0, 0, 20, 20), "invoke"),))
        self.session.receive(self.session.token, "scan", fresh)
        self.assertEqual(self.views[-1].markers, fresh.elements)
        self.assertIn("Números atualizados", self.views[-1].text)
        self.session.handle("um")
        self.assertEqual(self.session.state, ActionState.CONFIRMING)
        self.assertIsNone(self.session.scroll_request)

    def test_window_change_cancel_and_late_response_never_restart(self):
        self.start()
        token = self.session.token
        self.session.receive(0, "foreground", Window("200", 22, "Outra", (0, 0, 800, 600)))
        self.session.receive(token, "scroll", ScrollOutcome.MOVED)
        self.assertFalse(self.session.active)
        self.assertFalse(self.views[-1].capture)

    def test_boundary_feedback_still_allows_reverse(self):
        self.start()
        self.complete(ScrollOutcome.END)
        self.assertIn("chegou ao fim", self.views[-1].text)
        self.session.handle("para cima")
        self.assertEqual(self.executor.submit.call_args.args[-1].direction, "up")

    def test_dictation_is_literal_and_pending_confirmation_is_preserved(self):
        self.session.handle("digitar")
        self.session.receive(self.session.token, "prepare_dictation", self.snapshot)
        self.session.handle("role para baixo")
        self.assertEqual(self.session.dictation_text, "role para baixo")
        count, token = self.executor.submit.call_count, self.session.token
        self.session.handle("role para cima")
        self.assertEqual(self.session.state, ActionState.CONFIRMING)
        self.assertEqual(self.session.token, token)
        self.assertEqual(self.executor.submit.call_count, count)
        self.assertIn("confirmar ou cancelar antes", self.views[-1].text)

    def test_no_area_and_invalid_area_never_scroll(self):
        self.start(preferred=None)
        self.session.handle("99")
        self.assertTrue(self.views[-1].capture)
        self.session.handle("99")
        self.assertFalse(self.session.active)
        self.session.begin_capture()
        self.session.handle("role para cima")
        self.session.receive(self.session.token, "prepare_scroll", ScrollTargets(Snapshot("empty", self.window, ())))
        self.assertFalse(self.session.active)
        self.assertIn("Não consegui identificar", self.views[-1].text)


class ScrollCaptureTest(unittest.TestCase):
    def test_timeout_is_per_capture_and_cancel_clears_it(self):
        service = VoiceService()
        service._publish(State.IDLE)
        self.assertTrue(service.request_capture(start_timeout=5))
        self.assertEqual(service._next_start_timeout, 5)
        service.cancel_interaction()
        self.assertIsNone(service._next_start_timeout)
        for value in (0, -1, 31, True, "5"):
            with self.assertRaises(ValueError):
                service.request_capture(start_timeout=value)

    def test_streaming_gate_uses_five_seconds_without_changing_preferences(self):
        service = StreamingVoiceService(speech_config=SpeechConfig(start_timeout=10))
        service.current_start_timeout = 5
        service.vad, service.connection = Mock(), Mock()
        service.vad.predict.return_value = 0
        chunks = []
        def read(*args):
            chunks.append(1)
            return b"\0" * 2560
        with patch("wakeword.streaming_service._read_chunk", side_effect=read):
            with self.assertRaises(NoSpeech):
                service._stream_audio(None, None, 1)
        self.assertEqual(len(chunks), 63)
        self.assertEqual(service.speech_config.start_timeout, 10)


class ScrollExecutorTest(unittest.TestCase):
    def test_native_operations_run_on_executor_and_return_typed_results(self):
        import queue
        import threading
        from screen_access.executor import ScreenExecutor
        replies, calls = queue.Queue(), []
        window = Window("100", 12345, "Teste", (0, 0, 800, 600))
        snapshot = Snapshot("scroll", window, (Element(1, "Área", "PaneControl", window.frame, "scroll"),))
        class FakeBackend(Backend):
            def foreground(self):
                return window
            def prepare_scroll(self, *args, **kwargs):
                calls.append(("prepare", threading.get_ident()))
                return ScrollTargets(snapshot, 1)
            def scroll(self, *args, **kwargs):
                calls.append(("scroll", threading.get_ident()))
                return ScrollOutcome.MOVED
        executor = ScreenExecutor(lambda *args: replies.put(args), FakeBackend)
        def reply(kind):
            while True:
                token, got, data = replies.get(timeout=2)
                if got == kind:
                    return token, data
        try:
            executor.start()
            executor.submit(1, "prepare_scroll", window, 32, 3000)
            self.assertEqual(reply("prepare_scroll"), (1, ScrollTargets(snapshot, 1)))
            executor.submit(2, "scroll", snapshot, 1, ScrollRequest("down"))
            self.assertEqual(reply("scroll"), (2, ScrollOutcome.MOVED))
            self.assertEqual([kind for kind, _ in calls], ["prepare", "scroll"])
            self.assertTrue(all(identity != threading.get_ident() for _, identity in calls))
        finally:
            executor.stop()
            executor.thread.join(2)
            self.assertFalse(executor.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
