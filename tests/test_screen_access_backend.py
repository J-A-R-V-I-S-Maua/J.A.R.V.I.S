import os
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from screen_access.backend import Backend, ScreenError, Window
from screen_access.executor import ScreenExecutor
from screen_access.windows import WindowsBackend


def control(role="ButtonControl", name="Pesquisar", identity=1):
    obj = Mock()
    obj.Name = name
    obj.ControlTypeName = role
    obj.IsEnabled = True
    obj.IsOffscreen = False
    obj.IsKeyboardFocusable = True
    obj.NativeWindowHandle = 100
    obj.ProcessId = 99999
    obj.BoundingRectangle = SimpleNamespace(left=10, top=10, right=110, bottom=40)
    obj.GetRuntimeId.return_value = [identity]
    obj.GetChildren.return_value = []
    obj.GetInvokePattern.return_value.Invoke.return_value = True
    obj.GetTogglePattern.return_value.Toggle.return_value = True
    obj.GetSelectionItemPattern.return_value.Select.return_value = True
    return obj


class WindowsBackendTest(unittest.TestCase):
    def setUp(self):
        self.root = control("WindowControl")
        self.root.BoundingRectangle = SimpleNamespace(left=0, top=0, right=800, bottom=600)
        self.button = control()
        self.api = Mock()
        self.api.ControlFromHandle.return_value = self.root
        self.api.GetForegroundControl.return_value = self.root
        self.backend = WindowsBackend(self.api)
        self.window = self.backend.foreground()
        self.root.GetChildren.return_value = [self.button]

    def test_supported_patterns_and_refusal(self):
        cases = [("ButtonControl", "invoke"), ("HyperlinkControl", "invoke"),
                 ("CheckBoxControl", "toggle"), ("ListItemControl", "select"),
                 ("TreeItemControl", "select"), ("EditControl", "focus")]
        for role, action in cases:
            obj = control(role)
            self.assertEqual(self.backend.action(obj), action)
            self.backend.perform(obj, action)
        self.button.GetInvokePattern.return_value.Invoke.return_value = False
        with self.assertRaises(ScreenError):
            self.backend.perform(self.button, "invoke")

    def test_filter_disabled_offscreen_duplicate_and_no_pattern(self):
        disabled, hidden, unsupported = control(identity=2), control(identity=3), control(identity=4)
        disabled.IsEnabled = False
        hidden.IsOffscreen = True
        unsupported.GetInvokePattern.return_value = None
        self.root.GetChildren.return_value = [self.button, self.button, disabled, hidden, unsupported]
        snapshot = self.backend.scan(self.window)
        self.assertEqual(len(snapshot.elements), 1)

    def test_changes_and_cancellation_prevent_invocation(self):
        snapshot = self.backend.scan(self.window)
        self.button.Name = "Outro botão"
        with self.assertRaises(ScreenError):
            self.backend.activate(snapshot, 1)
        self.button.Name = "Pesquisar"
        with self.assertRaises(ScreenError):
            self.backend.activate(snapshot, 1, cancelled=lambda: True)
        self.button.GetInvokePattern.return_value.Invoke.assert_not_called()

    def test_focus_in_our_app_allowed_other_window_rejected(self):
        snapshot = self.backend.scan(self.window)
        ours = control("WindowControl")
        ours.ProcessId = os.getpid()
        self.api.GetForegroundControl.return_value = ours
        self.backend.validate(snapshot, 1)
        ours.ProcessId = 99998
        ours.NativeWindowHandle = 200
        with self.assertRaises(ScreenError):
            self.backend.validate(snapshot, 1)

    def test_limit_and_window_geometry_changes(self):
        self.root.GetChildren.return_value = [self.button, control(identity=2)]
        snapshot = self.backend.scan(self.window, max_elements=2)
        self.assertTrue(snapshot.truncated)
        self.root.BoundingRectangle.right = 900
        with self.assertRaises(ScreenError):
            self.backend.activate(snapshot, 1)

    def test_stale_snapshot(self):
        first = self.backend.scan(self.window)
        self.backend.scan(self.window)
        with self.assertRaises(ScreenError):
            self.backend.validate(first, 1)

    def test_double_click_opens_item_with_single_invoke_not_selection(self):
        self.button.ControlTypeName = "ListItemControl"
        snapshot = self.backend.scan(self.window)
        self.backend.open_item(snapshot, 1)
        self.button.GetPattern.assert_called_with(10000)
        self.button.GetPattern.return_value.Invoke.assert_called_once()
        self.button.GetSelectionItemPattern.return_value.Select.assert_not_called()

    def test_double_click_unsupported_or_cancelled_never_invokes(self):
        snapshot = self.backend.scan(self.window)
        with self.assertRaises(ScreenError):
            self.backend.open_item(snapshot, 1)
        self.button.ControlTypeName = "ListItemControl"
        snapshot = self.backend.scan(self.window)
        with self.assertRaises(ScreenError):
            self.backend.open_item(snapshot, 1, cancelled=lambda: True)
        self.button.GetPattern.return_value.Invoke.assert_not_called()
        self.button.GetPattern.return_value = None
        with self.assertRaises(ScreenError):
            self.backend.open_item(snapshot, 1)

    def test_context_menu_checks_focus_and_uses_keyboard_without_invoke(self):
        snapshot = self.backend.scan(self.window)
        self.api.GetFocusedControl.return_value = self.button
        self.api.GetForegroundWindow.return_value = 100
        self.backend.context_menu(snapshot, 1)
        self.api.SendKeys.assert_called_once_with("{Shift}{F10}", waitTime=0)
        self.button.GetInvokePattern.return_value.Invoke.assert_not_called()

    def test_context_menu_wrong_focus_disabled_and_cancellation(self):
        snapshot = self.backend.scan(self.window)
        self.api.GetFocusedControl.return_value = control(identity=99)
        self.api.GetForegroundWindow.return_value = 100
        with self.assertRaises(ScreenError):
            self.backend.context_menu(snapshot, 1)
        with self.assertRaises(ScreenError):
            self.backend.context_menu(snapshot, 1, cancelled=lambda: True)
        self.button.IsEnabled = False
        with self.assertRaises(ScreenError):
            self.backend.context_menu(snapshot, 1)
        self.api.SendKeys.assert_not_called()

    def test_empty_layout_pane_does_not_hide_visible_child(self):
        pane = control("PaneControl", identity=8)
        pane.BoundingRectangle = SimpleNamespace(left=0, top=0, right=0, bottom=0)
        pane.GetChildren.return_value = [self.button]
        self.root.GetChildren.return_value = [pane]
        self.assertEqual(len(self.backend.scan(self.window).elements), 1)

    def test_deep_web_controls_reached_by_new_defaults(self):
        child = self.button
        for index in range(20):
            parent = control("GroupControl", identity=index+10)
            parent.GetChildren.return_value = [child]
            child = parent
        self.root.GetChildren.return_value = [child]
        self.assertEqual(len(self.backend.scan(self.window).elements), 1)
        limited = self.backend.scan(self.window, max_depth=12)
        self.assertEqual(len(limited.elements), 0)
        self.assertTrue(limited.truncated)

    def test_web_control_after_two_hundred_structural_nodes(self):
        self.root.GetChildren.return_value = [control("TextControl", identity=10+i) for i in range(220)] + [self.button]
        self.assertEqual(len(self.backend.scan(self.window).elements), 1)
        limited = self.backend.scan(self.window, max_elements=200)
        self.assertEqual(len(limited.elements), 0)
        self.assertTrue(limited.truncated)

    def test_inaccessible_subtree_does_not_discard_other_controls(self):
        denied = control("GroupControl", identity=8)
        denied.GetChildren.side_effect = RuntimeError("Access denied")
        self.root.GetChildren.return_value = [denied, self.button]
        snapshot = self.backend.scan(self.window)
        self.assertEqual(len(snapshot.elements), 1)
        self.assertTrue(snapshot.truncated)

    def prepare_field(self):
        field = control("EditControl", identity=3)
        field.GetPattern.return_value.IsReadOnly = False
        field.GetParentControl.return_value = self.root
        self.api.GetFocusedControl.return_value = field
        self.backend.remember_focus(self.window)
        return field

    def test_dictation_remembers_field_when_speakbar_takes_focus(self):
        field = self.prepare_field()
        ours = control("EditControl")
        ours.ProcessId = os.getpid()
        self.api.GetFocusedControl.return_value = ours
        snapshot = self.backend.prepare_dictation(self.window)
        self.assertEqual(snapshot.elements[0].role, "EditControl")
        self.assertEqual(self.backend.references[1][0], field)

    def test_enter_and_clear_only_target_validated_active_field(self):
        field = self.prepare_field()
        self.api.GetForegroundWindow.return_value = 100
        snapshot = self.backend.prepare_dictation(self.window)
        self.backend.press_enter(snapshot)
        self.api.SendKeys.assert_called_once_with("{Enter}", waitTime=0)
        self.api.SendKeys.reset_mock()
        self.backend.clear_text(snapshot)
        field.GetPattern.return_value.SetValue.assert_called_once_with("")
        self.api.SendKeys.assert_not_called()

    def test_clear_text_pattern_only_editor_uses_shortcut(self):
        field = self.prepare_field()
        field.GetPattern.return_value = None
        self.api.GetForegroundWindow.return_value = 100
        snapshot = self.backend.prepare_dictation(self.window)
        self.backend.clear_text(snapshot)
        self.assertEqual([call.args[0] for call in self.api.SendKeys.call_args_list], ["{Ctrl}a", "{Backspace}"])

    def test_field_keyboard_commands_reject_wrong_focus_readonly_and_cancel(self):
        field = self.prepare_field()
        snapshot = self.backend.prepare_dictation(self.window)
        self.api.GetFocusedControl.return_value = control(identity=88)
        with self.assertRaises(ScreenError):
            self.backend.press_enter(snapshot)
        with self.assertRaises(ScreenError):
            self.backend.clear_text(snapshot)
        self.api.GetFocusedControl.return_value = field
        with self.assertRaises(ScreenError):
            self.backend.clear_text(snapshot, cancelled=lambda: True)
        field.GetPattern.return_value.IsReadOnly = True
        with self.assertRaises(ScreenError):
            self.backend.clear_text(snapshot)
        self.api.SendKeys.assert_not_called()
        field.GetPattern.return_value.SetValue.assert_not_called()

    def test_dictation_refuses_readonly_stale_or_different_focus(self):
        field = self.prepare_field()
        snapshot = self.backend.prepare_dictation(self.window)
        other = control("EditControl", identity=4)
        self.api.GetFocusedControl.return_value = other
        with self.assertRaises(ScreenError):
            self.backend.dictate(snapshot, "teste")
        field.GetPattern.return_value.IsReadOnly = True
        self.api.GetFocusedControl.return_value = field
        with self.assertRaises(ScreenError):
            self.backend.prepare_dictation(self.window)
        field.GetPattern.return_value.IsReadOnly = False
        self.backend.remember_focus(self.window)
        field.Name = "Mudou"
        self.api.GetFocusedControl.return_value = None
        with self.assertRaises(ScreenError):
            self.backend.prepare_dictation(self.window)


class DesktopBackendTest(unittest.TestCase):
    def setUp(self):
        self.desktop = control("PaneControl", "Desktop", 1)
        self.desktop.ClassName = "Progman"
        self.desktop.BoundingRectangle = SimpleNamespace(left=0, top=0, right=800, bottom=600)
        self.icon = control("ListItemControl", "Lixeira", 2)
        self.icon.NativeWindowHandle = 0
        self.icon.GetParentControl.return_value = self.desktop
        self.desktop.GetChildren.return_value = [self.icon]
        self.primary = control("PaneControl", "Barra de tarefas", 3)
        self.primary.ClassName = "Shell_TrayWnd"
        self.primary.NativeWindowHandle = 200
        self.primary.BoundingRectangle = SimpleNamespace(left=0, top=600, right=800, bottom=640)
        self.start = control("ButtonControl", "Iniciar", 4)
        self.start.NativeWindowHandle = 0
        self.start.GetParentControl.return_value = self.primary
        self.start.BoundingRectangle = SimpleNamespace(left=0, top=600, right=48, bottom=640)
        self.primary.GetChildren.return_value = [self.start]
        self.secondary = control("PaneControl", "Segunda barra", 5)
        self.secondary.ClassName = "Shell_SecondaryTrayWnd"
        self.secondary.NativeWindowHandle = 300
        self.secondary.BoundingRectangle = SimpleNamespace(left=-800, top=600, right=0, bottom=640)
        self.secondary_button = control("ButtonControl", "Aplicativo", 6)
        self.secondary_button.NativeWindowHandle = 0
        self.secondary_button.GetParentControl.return_value = self.secondary
        self.secondary_button.BoundingRectangle = SimpleNamespace(left=-780, top=600, right=-730, bottom=640)
        self.secondary.GetChildren.return_value = [self.secondary_button]
        self.browser = control("WindowControl", "Navegador", 7)
        self.browser.ClassName = "Chrome_WidgetWin_1"
        self.browser.NativeWindowHandle = 400
        self.browser.GetChildren.return_value = [control(name="Não incluir este botão")]
        self.roots = {100: self.desktop, 200: self.primary, 300: self.secondary, 400: self.browser}
        self.api = Mock()
        self.api.ControlFromHandle.side_effect = self.roots.get
        self.api.GetRootControl.return_value.GetChildren.return_value = list(self.roots.values())
        self.api.GetForegroundControl.return_value = self.desktop
        self.backend = WindowsBackend(self.api)
        self.window = self.backend.foreground()

    def test_desktop_and_both_taskbars_share_stable_numbers_and_separate_clips(self):
        snapshot = self.backend.scan(self.window)
        self.assertEqual([(e.number, e.name) for e in snapshot.elements],
                         [(1, "Lixeira"), (2, "Iniciar"), (3, "Aplicativo")])
        self.assertEqual(snapshot.elements[2].frame[0], -780)
        self.assertFalse(snapshot.truncated)
        for number in (1, 2, 3):
            self.backend.validate(snapshot, number)

    def test_focus_switch_between_wallpaper_taskbar_and_speakbar_preserves_desktop(self):
        snapshot = self.backend.scan(self.window)
        self.api.GetForegroundControl.return_value = self.secondary
        self.assertEqual(self.backend.foreground().identity, self.window.identity)
        self.backend.validate(snapshot, 2)
        own = control("WindowControl")
        own.ProcessId = os.getpid()
        own.ClassName = "Speakbar"
        self.api.GetForegroundControl.return_value = own
        self.backend.tracker = SimpleNamespace(handle=200, pump=lambda: None)
        self.assertEqual(self.backend.tracked_foreground().identity, self.window.identity)
        self.backend.validate(snapshot, 2)

    def test_minimized_app_remembered_by_speakbar_resolves_to_desktop(self):
        own = control("WindowControl")
        own.ProcessId = os.getpid()
        own.ClassName = "Speakbar"
        self.api.GetForegroundControl.return_value = own
        self.backend.tracker = SimpleNamespace(handle=400, pump=lambda: None)
        self.backend.minimized_or_closed = lambda handle: handle == 400
        self.assertEqual(self.backend.tracked_foreground().identity, self.window.identity)

    def test_source_taskbar_disappeared_or_moved_prevents_action(self):
        snapshot = self.backend.scan(self.window)
        self.primary.BoundingRectangle.right = 850
        with self.assertRaises(ScreenError):
            self.backend.activate(snapshot, 2)
        self.start.GetInvokePattern.return_value.Invoke.assert_not_called()

    def test_other_external_app_invalidates_desktop_session(self):
        snapshot = self.backend.scan(self.window)
        self.api.GetForegroundControl.return_value = self.browser
        with self.assertRaises(ScreenError):
            self.backend.activate(snapshot, 2)


class ExecutorTest(unittest.TestCase):
    def test_native_lifetime_owned_by_worker_and_stop_discards_pending(self):
        replies = queue.Queue()
        owner = []
        entered = threading.Event()
        release = threading.Event()

        class FakeBackend(Backend):
            def __enter__(self):
                owner.append(threading.get_ident())
                entered.set()
                return self

            def foreground(self):
                return Window("1", 99999, "Teste", (0, 0, 800, 600))

            def scan(self, *args):
                owner.append(threading.get_ident())
                release.wait(2)
                return "snapshot"

            def __exit__(self, *args):
                owner.append(threading.get_ident())

        executor = ScreenExecutor(lambda *reply: replies.put(reply), FakeBackend)
        executor.start()
        self.assertTrue(entered.wait(1))
        executor.submit(1, "scan", None, 12, 200, False)
        executor.stop()
        release.set()
        executor.thread.join(2)
        self.assertFalse(executor.thread.is_alive())
        self.assertTrue(all(item == owner[0] for item in owner))
        self.assertNotEqual(owner[0], threading.get_ident())
        collected = []
        while not replies.empty():
            collected.append(replies.get())
        self.assertFalse(any(token == 1 for token, _, _ in collected))
