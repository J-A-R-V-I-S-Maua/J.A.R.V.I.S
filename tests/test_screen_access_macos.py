"""Contrato do backend AX validado com APIs simuladas fora do macOS."""
import importlib
import os
import sys
import unittest
from types import ModuleType
from unittest.mock import Mock, patch

import screen_access

EXTERNAL_PID = 99999
FRAMES = {"window": (0, 0, 800, 600), "area": (0, 0, 800, 400), "content": (0, 0, 800, 1600)}
ROLES = {"window": "AXWindow", "button": "AXButton", "field": "AXTextField", "row": "AXRow",
         "area": "AXScrollArea", "bar": "AXScrollBar", "content": "AXGroup"}
SETTABLE = {("field", "AXFocused"), ("field", "AXValue"), ("row", "AXSelected"), ("bar", "AXValue")}
ACTIONS = {"button": ("AXPress", "AXShowMenu"), "row": ("AXOpen",)}


def fake_world():
    state = {"pid": EXTERNAL_PID, "focused": None, "bar": 0.0}
    macos = ModuleType("screen_access.macos")

    def read_attribute(element, name):
        if name == "AXWindowNumber":
            return 1 if element == "window" else None
        if name == "AXEnabled":
            return None if element == "row" else True
        if element == "area":
            return {"AXVerticalScrollBar": "bar", "AXContents": ("content",)}.get(name)
        if element == "bar" and name == "AXValue":
            return state["bar"]
        return None

    def set_attribute(element, name, value):
        if (element, name) == ("bar", "AXValue"):
            state["bar"] = value
        return 0

    def activate(pid):
        state["pid"] = pid
        return True

    macos.read_attribute = Mock(side_effect=read_attribute)
    macos.set_attribute = Mock(side_effect=set_attribute)
    macos.is_settable = Mock(side_effect=lambda element, name: (element, name) in SETTABLE)
    macos.action_names = Mock(side_effect=lambda element: ACTIONS.get(element, ()))
    macos.perform_action = Mock(return_value=0)
    macos.press = Mock(return_value=0)
    macos.can_press = Mock(return_value=True)
    macos.frontmost_application = Mock(side_effect=lambda: (state["pid"], "Teste"))
    macos.frontmost_pid = Mock(side_effect=lambda: state["pid"])
    macos.activate_application = Mock(side_effect=activate)
    macos.raise_window = Mock(return_value=0)
    macos.front_window = Mock(return_value="window")
    macos.focused_element = Mock(side_effect=lambda pid: state["focused"])
    macos.same_element = Mock(side_effect=lambda a, b: a is not None and a == b)
    macos.owner_window = Mock(side_effect=lambda element: None if element == "window" else "window")
    macos.parent = Mock(return_value=None)
    macos.window_title = Mock(return_value="Teste")
    macos.element_frame = Mock(side_effect=lambda element: FRAMES.get(element, (10, 10, 100, 30)))
    macos.element_role = Mock(side_effect=lambda element: ROLES[element])
    macos.element_name = Mock(side_effect=lambda element: element)
    macos.enable_web_accessibility = Mock()
    macos.walk_with_clip = Mock(side_effect=lambda root, depth, clip: iter(
        [(1, element, clip) for element in ("button", "field", "row", "area")]))
    macos.is_visible = Mock(return_value=True)
    macos.describe_error = Mock(return_value="recusado")
    macos.TEXT_INPUT_ROLES = {"AXTextField"}
    macos.ACTION_ROLES = {"AXButton"}
    macos.SELECTABLE_ROLES = {"AXRow"}
    macos.SHOW_MENU_ACTION = "AXShowMenu"
    macos.OPEN_ACTION = "AXOpen"
    macos.KEY_RETURN, macos.KEY_DELETE, macos.KEY_A, macos.COMMAND = 36, 51, 0, 1 << 20
    macos.press_key = Mock()
    macos.type_text = Mock(return_value=True)
    permissions = ModuleType("screen_access.permissions")
    permissions.has_accessibility_permission = Mock(return_value=True)
    permissions.request_accessibility_permission = Mock(return_value=False)
    return state, macos, permissions


class MacOSBackendTest(unittest.TestCase):
    def setUp(self):
        self.state, self.macos, self.permissions = fake_world()
        modules = {"screen_access.macos": self.macos, "screen_access.permissions": self.permissions}
        sys.modules.pop("screen_access.macos_backend", None)
        patches = [patch.dict(sys.modules, modules)]
        patches += [patch.object(screen_access, name.rsplit(".", 1)[1], module, create=True)
                    for name, module in modules.items()]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(sys.modules.pop, "screen_access.macos_backend", None)
        self.module = importlib.import_module("screen_access.macos_backend")
        self.backend = self.module.MacOSBackend().__enter__()
        self.addCleanup(self.backend.__exit__, None, None, None)
        self.window = self.backend.tracked_foreground()

    def test_scan_press_focus_select_and_hidden_element(self):
        snapshot = self.backend.scan(self.window)
        self.assertEqual([(item.name, item.action) for item in snapshot.elements],
                         [("button", "press"), ("field", "focus"), ("row", "select")])
        self.backend.activate(snapshot, 1)
        self.macos.press.assert_called_once_with("button")
        self.backend.activate(snapshot, 2)
        self.macos.set_attribute.assert_called_with("field", "AXFocused", True)
        self.backend.activate(snapshot, 3)
        self.macos.set_attribute.assert_called_with("row", "AXSelected", True)
        self.macos.is_visible.return_value = False
        with self.assertRaises(self.module.ScreenError):
            self.backend.activate(snapshot, 1)

    def test_context_menu_and_open_use_named_ax_actions(self):
        snapshot = self.backend.scan(self.window)
        self.backend.context_menu(snapshot, 1)
        self.macos.perform_action.assert_called_once_with("button", "AXShowMenu")
        with self.assertRaises(self.module.ScreenError):
            self.backend.open_item(snapshot, 1)
        self.backend.open_item(snapshot, 3)
        self.macos.perform_action.assert_called_with("row", "AXOpen")
        with self.assertRaises(self.module.ScreenError):
            self.backend.context_menu(snapshot, 1, cancelled=lambda: True)
        self.assertEqual(self.macos.perform_action.call_count, 2)

    def test_speakbar_in_front_keeps_previous_window_as_target(self):
        snapshot = self.backend.scan(self.window)
        self.state["pid"] = os.getpid()
        self.assertEqual(self.backend.tracked_foreground(), self.window)
        self.backend.activate(snapshot, 1)
        self.macos.press.assert_called_once_with("button")

    def test_dictation_enter_and_clear_return_focus_to_the_field(self):
        self.state["focused"] = "field"
        snapshot = self.backend.prepare_dictation(self.window)
        self.assertEqual([item.name for item in snapshot.elements], ["field"])
        self.state["pid"] = os.getpid()
        self.backend.dictate(snapshot, "Olá, mundo!")
        self.macos.activate_application.assert_called_once_with(EXTERNAL_PID)
        text, allowed = self.macos.type_text.call_args.args
        self.assertEqual(text, "Olá, mundo!")
        self.assertTrue(allowed())
        self.assertNotIn(("field", "AXFocused", True), [call.args for call in self.macos.set_attribute.call_args_list])
        self.state["focused"] = "button"
        self.assertFalse(allowed())
        self.state["focused"] = "field"
        self.backend.press_enter(snapshot)
        self.macos.press_key.assert_called_once_with(36)
        self.backend.clear_text(snapshot)
        self.macos.set_attribute.assert_called_with("field", "AXValue", "")
        self.state["focused"] = "button"
        with self.assertRaises(self.module.ScreenError):
            self.backend.dictate(snapshot, "outro texto")
        self.assertEqual(self.macos.type_text.call_count, 1)

    def test_dictation_requires_a_focused_text_field(self):
        self.state["focused"] = "button"
        with self.assertRaises(self.module.ScreenError):
            self.backend.prepare_dictation(self.window)

    def test_clear_without_settable_value_uses_select_all_then_delete(self):
        self.state["focused"] = "field"
        snapshot = self.backend.prepare_dictation(self.window)
        self.macos.is_settable.side_effect = lambda element, name: (element, name) in SETTABLE - {("field", "AXValue")}
        self.backend.clear_text(snapshot)
        self.assertEqual([call.args for call in self.macos.press_key.call_args_list], [(0, 1 << 20), (51,)])

    def test_scroll_area_moves_scroll_bar_value(self):
        targets = self.backend.prepare_scroll(self.window)
        self.assertEqual([item.role for item in targets.snapshot.elements], ["AXScrollArea"])
        self.assertEqual(targets.preferred, 1)
        from screen_access.scroll import ScrollOutcome, ScrollRequest
        self.assertEqual(self.backend.scroll(targets.snapshot, 1, ScrollRequest("up", "small")), ScrollOutcome.START)
        outcome = self.backend.scroll(targets.snapshot, 1, ScrollRequest("down", "small"))
        self.assertEqual(outcome, ScrollOutcome.MOVED)
        self.assertAlmostEqual(self.state["bar"], 0.05)

    def test_missing_permission_shows_system_prompt(self):
        self.permissions.has_accessibility_permission.return_value = False
        with self.assertRaises(self.module.ScreenError):
            self.module.MacOSBackend().__enter__()
        self.permissions.request_accessibility_permission.assert_called_once()


if __name__ == "__main__":
    unittest.main()
