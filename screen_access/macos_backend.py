"""Adaptação da AX API ao contrato compartilhado."""
import os
import time
import uuid

from . import macos
from .backend import Backend, Element, Snapshot, ScreenError, Window
from .permissions import has_accessibility_permission, request_accessibility_permission
from .scroll import ScrollMixin, ScrollTargets


class AXScrollPattern:
    """Barra de rolagem AX com a mesma interface do ScrollPattern do UIA."""

    def __init__(self, area):
        self.area = area
        self.bar = macos.read_attribute(area, "AXVerticalScrollBar")

    @property
    def VerticallyScrollable(self):
        return self.bar is not None and macos.is_settable(self.bar, "AXValue")

    @property
    def VerticalScrollPercent(self):
        return float(macos.read_attribute(self.bar, "AXValue")) * 100

    @property
    def VerticalViewSize(self):
        area = macos.element_frame(self.area)
        contents = macos.read_attribute(self.area, "AXContents") or ()
        heights = [frame[3] for frame in map(macos.element_frame, contents) if frame]
        if area is None or not heights or max(heights) <= area[3]:
            return 100.0
        return 100 * area[3] / max(heights)

    def SetScrollPercent(self, horizontal, vertical, waitTime=0):
        return macos.set_attribute(self.bar, "AXValue", vertical / 100) == 0

    def Scroll(self, horizontal, vertical, waitTime=0):
        action = "AXIncrement" if vertical in (3, 4) else "AXDecrement"
        return action in macos.action_names(self.bar) and macos.perform_action(self.bar, action) == 0


class MacOSBackend(ScrollMixin, Backend):
    def __init__(self):
        super().__init__()
        self.last_external = None
        self.last_field = None
        self.last_scroll_focus = None

    def __enter__(self):
        if not has_accessibility_permission():
            request_accessibility_permission()
            raise ScreenError("Autorize o J.A.R.V.I.S. (ou o Terminal que o executa) em Ajustes do Sistema > "
                              "Privacidade e Segurança > Acessibilidade e reinicie o aplicativo.")
        return self

    def __exit__(self, *args):
        super().__exit__(*args)
        self.last_external = None
        self.last_field = None
        self.last_scroll_focus = None

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

    def tracked_foreground(self):
        current = self.foreground()
        if current is not None and current.pid != os.getpid():
            self.last_external = current
            return current
        # Clicar na speakbar não deve trocar o alvo.
        if self.last_external is not None and self.window_exists(self.last_external):
            return self.last_external
        return current

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
            if macos.is_settable(element, "AXFocused"):
                action = "focus"
        elif enabled and role in macos.ACTION_ROLES and macos.can_press(element):
            action = "press"
        elif role in macos.SELECTABLE_ROLES and macos.is_settable(element, "AXSelected"):
            action = "select"
        visible = clip is None or macos.is_visible(element, clip)
        return (macos.element_name(element), role, macos.element_frame(element), action, visible, element)

    def perform(self, element, action):
        if action == "focus":
            code = macos.set_attribute(element, "AXFocused", True)
        elif action == "select":
            code = macos.set_attribute(element, "AXSelected", True)
        elif action == "press":
            code = macos.press(element)
        else:
            raise ScreenError("Esse controle não oferece uma ação nativa suportada.")
        if code != 0:
            raise ScreenError(f"O aplicativo recusou a ação: {macos.describe_error(code)}.")

    def _perform_named(self, snapshot, number, name, missing, cancelled):
        element, _ = self.validate(snapshot, number)
        if name not in macos.action_names(element):
            raise ScreenError(missing)
        if cancelled():
            raise ScreenError("Ação cancelada antes da execução.")
        code = macos.perform_action(element, name)
        if code != 0:
            raise ScreenError(f"O aplicativo recusou a ação: {macos.describe_error(code)}.")

    def open_item(self, snapshot, number, cancelled=lambda: False):
        self._perform_named(snapshot, number, macos.OPEN_ACTION,
                            "Este item não oferece abertura nativa por duplo clique.", cancelled)

    def context_menu(self, snapshot, number, cancelled=lambda: False):
        self._perform_named(snapshot, number, macos.SHOW_MENU_ACTION,
                            "Este controle não oferece menu de contexto pela acessibilidade.", cancelled)

    def belongs_to(self, element, window):
        try:
            root = self.root(window)
        except ScreenError:
            return False
        owner = macos.owner_window(element)
        if owner is not None:
            return macos.same_element(owner, root)
        for _ in range(64):
            if element is None:
                return False
            if macos.same_element(element, root):
                return True
            element = macos.parent(element)
        return False

    @staticmethod
    def editable(element):
        return (macos.element_role(element) in macos.TEXT_INPUT_ROLES
                and macos.read_attribute(element, "AXEnabled") is not False)

    def remember_focus(self, window):
        if window is None or window.pid == os.getpid():
            return
        element = macos.focused_element(window.pid)
        if element is None:
            return
        inside = self.belongs_to(element, window)
        self.last_scroll_focus = (window.identity, element) if inside else None
        self.last_field = None
        if inside and self.editable(element):
            self.last_field = (window.identity, element, self.describe(element, window.frame))

    def prepare_dictation(self, window):
        self.remember_focus(window)
        if not self.last_field or self.last_field[0] != window.identity:
            raise ScreenError("Dê foco a um campo de texto e repita digitar.")
        _, element, old = self.last_field
        name, role, frame, action, visible, identity = self.describe(element, window.frame)
        if not self.window_exists(window) or not visible or (name, role, frame, action) != old[:4]:
            raise ScreenError("O campo mudou. Dê foco ao campo e repita digitar.")
        self.references = {1: (element, identity, window.frame)}
        self.snapshot = Snapshot(uuid.uuid4().hex, window, (Element(1, name, role, frame, action),))
        return self.snapshot

    def _still_focused(self, snapshot, element):
        return (macos.frontmost_pid() == snapshot.window.pid
                and macos.same_element(macos.focused_element(snapshot.window.pid), element))

    def _focus_editable(self, snapshot, cancelled):
        element, _ = self.validate(snapshot, 1)
        if not self.editable(element) or not self.belongs_to(element, snapshot.window):
            raise ScreenError("O campo não está mais disponível para digitação.")
        current = macos.focused_element(snapshot.window.pid)
        if current is not None and not macos.same_element(current, element):
            raise ScreenError("O campo ativo mudou. Repita o comando no campo desejado.")
        if cancelled():
            raise ScreenError("Digitação cancelada.")
        if not macos.activate_application(snapshot.window.pid):
            raise ScreenError("Não foi possível devolver o foco ao campo. Repita o comando.")
        macos.raise_window(self.root(snapshot.window))
        # Campos Cocoa selecionam todo o texto quando recebem foco de novo.
        if current is None:
            macos.set_attribute(element, "AXFocused", True)
        if not self._still_focused(snapshot, element):
            raise ScreenError("O foco mudou. Nenhum texto foi enviado.")
        if cancelled():
            raise ScreenError("Digitação cancelada.")
        return element

    def press_enter(self, snapshot, number=1, cancelled=lambda: False):
        if number != 1:
            raise ScreenError("Campo ativo inválido.")
        self._focus_editable(snapshot, cancelled)
        macos.press_key(macos.KEY_RETURN)

    def clear_text(self, snapshot, number=1, cancelled=lambda: False):
        if number != 1:
            raise ScreenError("Campo ativo inválido.")
        element = self._focus_editable(snapshot, cancelled)
        if macos.is_settable(element, "AXValue"):
            if macos.set_attribute(element, "AXValue", "") != 0:
                raise ScreenError("O aplicativo recusou a limpeza do campo.")
            return
        macos.press_key(macos.KEY_A, macos.COMMAND)
        if cancelled() or not self._still_focused(snapshot, element):
            raise ScreenError("O foco mudou. A limpeza do campo foi interrompida.")
        macos.press_key(macos.KEY_DELETE)

    def dictate(self, snapshot, text, cancelled=lambda: False):
        if not text or any(ord(char) < 32 for char in text):
            raise ScreenError("O texto deve conter uma frase sem teclas de controle.")
        element = self._focus_editable(snapshot, cancelled)
        if not macos.type_text(text, lambda: not cancelled() and self._still_focused(snapshot, element)):
            raise ScreenError("Digitação interrompida. Confira o texto já inserido antes de repetir.")

    @staticmethod
    def scroll_pattern(native):
        return AXScrollPattern(native)

    def prepare_scroll(self, window, max_depth=32, max_elements=3000, cancelled=lambda: False):
        if max_depth < 0 or not 1 <= max_elements <= 10000:
            raise ScreenError("Limites de leitura inválidos.")
        if window is None or window.pid == os.getpid():
            raise ScreenError("Selecione uma janela de aplicativo antes de rolar.")
        self._check_scroll_window(window, cancelled)
        self.scroll_references = {}
        self.scroll_snapshot = None
        elements, truncated = [], False
        began = time.monotonic()
        for visited, (_, native, clip) in enumerate(self.walk(window, max_depth)):
            if cancelled():
                raise ScreenError("Rolagem cancelada.")
            if visited >= max_elements or time.monotonic() - began >= 5:
                truncated = True
                break
            try:
                if macos.element_role(native) != "AXScrollArea":
                    continue
                frame = macos.element_frame(native)
                if frame is None or (clip is not None and not macos.is_visible(native, clip)):
                    continue
                if not self.scroll_pattern(native).VerticallyScrollable:
                    continue
                if len(elements) >= 200:
                    truncated = True
                    break
                number = len(elements) + 1
                name = macos.element_name(native)
                elements.append(Element(number, name or f"Área rolável {number}", "AXScrollArea", frame, "scroll"))
                self.scroll_references[number] = (native, frame, clip, name)
            except Exception:
                truncated = True
        self.scroll_snapshot = Snapshot(uuid.uuid4().hex, window, tuple(elements), truncated=truncated)
        preferred = None if truncated else self._focused_area(window)
        if preferred is None and len(elements) == 1 and not truncated:
            preferred = 1
        self._check_scroll_window(window, cancelled)
        return ScrollTargets(self.scroll_snapshot, preferred)

    def _focused_area(self, window):
        focused = macos.focused_element(window.pid)
        remembered = self.last_scroll_focus
        if focused is None and remembered and remembered[0] == window.identity:
            focused = remembered[1]
        for _ in range(64):
            if focused is None:
                return None
            for number, reference in self.scroll_references.items():
                if macos.same_element(reference[0], focused):
                    return number
            focused = macos.parent(focused)
        return None

    def _validate_scroll_area(self, snapshot, number, cancelled):
        self._check_scroll_window(snapshot.window, cancelled)
        if self.scroll_snapshot is None or snapshot.token != self.scroll_snapshot.token or number not in self.scroll_references:
            raise ScreenError("A área rolável expirou. Repita o comando.")
        native, frame, clip, name = self.scroll_references[number]
        try:
            unchanged = (macos.element_role(native) == "AXScrollArea" and macos.element_frame(native) == frame
                         and macos.element_name(native) == name
                         and (clip is None or macos.is_visible(native, clip))
                         and self.belongs_to(native, snapshot.window))
        except Exception as exc:
            raise ScreenError("A área rolável ficou indisponível. Repita o comando.") from exc
        if not unchanged:
            raise ScreenError("A área rolável mudou. Repita o comando.")
        return native
