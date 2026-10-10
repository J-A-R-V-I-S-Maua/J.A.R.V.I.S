"""Adaptação da AX API existente ao contrato compartilhado."""
from . import macos
from .backend import Backend, ScreenError, Window
from .permissions import has_accessibility_permission
from ApplicationServices import AXUIElementIsAttributeSettable, AXUIElementSetAttributeValue


class MacOSBackend(Backend):
    def __enter__(self):
        if not has_accessibility_permission():
            raise ScreenError("Autorize Acessibilidade em Ajustes do Sistema > Privacidade e Segurança.")
        return self

    @staticmethod
    def identity(pid, native):
        number = macos.read_attribute(native, "AXWindowNumber")
        return f"{pid}:{number if number is not None else macos.window_title(native)}"

    def foreground(self):
        pid, name = macos.frontmost_application()
        native = macos.front_window(pid)
        if native is None:
            return None
        return Window(self.identity(pid, native), pid, macos.window_title(native) or name, macos.element_frame(native))

    def root(self, window):
        native = macos.front_window(window.pid)
        if native is None or self.identity(window.pid, native) != window.identity:
            raise ScreenError("A janela mudou.")
        return native

    def window_exists(self, window):
        try:
            return macos.element_frame(self.root(window)) == window.frame
        except ScreenError:
            return False

    def walk(self, window, max_depth):
        macos.enable_web_accessibility(window.pid)
        if window.frame is None:
            for depth, element in macos.walk(self.root(window), max_depth):
                yield depth, element, None
        else:
            yield from macos.walk_with_clip(self.root(window), max_depth, window.frame)

    def describe(self, element, clip):
        role = macos.element_role(element)
        enabled = bool(macos.read_attribute(element, "AXEnabled"))
        action = ""
        if enabled and role in macos.TEXT_INPUT_ROLES:
            error, settable = AXUIElementIsAttributeSettable(element, "AXFocused", None)
            if error == 0 and settable:
                action = "focus"
        elif enabled and role in macos.ACTION_ROLES and macos.can_press(element):
            action = "press"
        visible = clip is None or macos.is_visible(element, clip)
        return (macos.element_name(element), role, macos.element_frame(element), action, visible, element)

    def perform(self, element, action):
        code = AXUIElementSetAttributeValue(element, "AXFocused", True) if action == "focus" else macos.press(element)
        if code != 0:
            raise ScreenError(macos.describe_error(code))
