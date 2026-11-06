"""Adaptação da AX API existente ao contrato compartilhado."""
from . import macos, macos_shell
from .backend import Backend, ScreenError, Window
from .permissions import has_accessibility_permission
from ApplicationServices import AXUIElementIsAttributeSettable, AXUIElementSetAttributeValue

DESKTOP_PREFIX = "desktop:"


class MacOSBackend(Backend):
    def __init__(self):
        super().__init__()
        # Controles da mesa e do Dock lidos na última varredura; só eles usam a classificação de shell.
        self.shell_elements = set()

    def __enter__(self):
        if not has_accessibility_permission():
            raise ScreenError("Autorize Acessibilidade em Ajustes do Sistema > Privacidade e Segurança.")
        return self

    @staticmethod
    def identity(pid, native):
        number = macos.read_attribute(native, "AXWindowNumber")
        return f"{pid}:{number if number is not None else macos.window_title(native)}"

    @staticmethod
    def is_desktop(window):
        return window.identity.startswith(DESKTOP_PREFIX)

    def desktop_window(self, pid):
        roots = macos_shell.desktop_roots(pid)
        frame = macos.element_frame(roots[0]) if roots else None
        if frame is None:
            return None
        return Window(f"{DESKTOP_PREFIX}{pid}", pid, "Área de trabalho", frame)

    def foreground(self):
        pid, name = macos.frontmost_application()
        native = macos.front_window(pid)
        # Clicar na mesa deixa o Finder ativo, e ele entrega a própria mesa (uma rolagem, não uma
        # janela de verdade) como "janela em foco". Janelas de pasta têm o papel AXWindow.
        if macos_shell.is_finder(pid) and (native is None or macos.element_role(native) != "AXWindow"):
            desktop = self.desktop_window(pid)
            if desktop is not None:
                return desktop
        if native is None:
            return None
        return Window(self.identity(pid, native), pid, macos.window_title(native) or name, macos.element_frame(native))

    def root(self, window):
        native = macos.front_window(window.pid)
        if native is None or self.identity(window.pid, native) != window.identity:
            raise ScreenError("A janela mudou.")
        return native

    def window_exists(self, window):
        if self.is_desktop(window):
            roots = macos_shell.desktop_roots(window.pid)
            return bool(roots) and macos.element_frame(roots[0]) == window.frame
        try:
            return macos.element_frame(self.root(window)) == window.frame
        except ScreenError:
            return False

    def walk(self, window, max_depth):
        if self.is_desktop(window):
            yield from self._walk_shell(window)
            return
        macos.enable_web_accessibility(window.pid)
        if window.frame is None:
            for depth, element in macos.walk(self.root(window), max_depth):
                yield depth, element, None
        else:
            yield from macos.walk_with_clip(self.root(window), max_depth, window.frame)

    def _walk_shell(self, window):
        """Ícones da mesa e itens do Dock, sempre juntos e com a mesma numeração."""
        self.shell_elements = set()
        for root in macos_shell.desktop_roots(window.pid):
            clip = macos.element_frame(root)
            for icon in macos_shell.desktop_icons(root):
                self.shell_elements.add(icon)
                yield 1, icon, clip
        dock_pid = macos.pid_for_bundle(macos_shell.DOCK)
        if dock_pid is not None:
            # Sem recorte: com o Dock recolhido os itens ficam fora da tela, mas continuam acionáveis.
            for item in macos_shell.dock_items(dock_pid):
                self.shell_elements.add(item)
                yield 1, item, None

    @staticmethod
    def shell_action(element, role):
        actions = macos.action_names(element)
        if role == macos_shell.DOCK_ITEM and "AXPress" in actions:
            return "press"
        if macos_shell.OPEN_ACTION in actions:
            return "open"
        return ""

    def describe(self, element, clip):
        role = macos.element_role(element)
        name = macos.element_name(element)
        enabled = bool(macos.read_attribute(element, "AXEnabled"))
        action = ""
        if element in self.shell_elements:
            # Itens da mesa e do Dock não declaram AXEnabled; os separadores do Dock não têm nome.
            action = self.shell_action(element, role) if name else ""
        elif enabled and role in macos.TEXT_INPUT_ROLES:
            error, settable = AXUIElementIsAttributeSettable(element, "AXFocused", None)
            if error == 0 and settable:
                action = "focus"
        elif enabled and role in macos.ACTION_ROLES and macos.can_press(element):
            action = "press"
        visible = clip is None or macos.is_visible(element, clip)
        return (name, role, macos.element_frame(element), action, visible, element)

    def perform(self, element, action):
        if action == "focus":
            code = AXUIElementSetAttributeValue(element, "AXFocused", True)
        elif action == "open":
            code = macos.perform_action(element, macos_shell.OPEN_ACTION)
        else:
            code = macos.press(element)
        if code != 0 and not (action == "open" and code in macos_shell.OPEN_ACCEPTED_ERRORS):
            raise ScreenError(f"{macos.describe_error(code)} (ação {action}, código {code}).")

    def open_item(self, snapshot, number, cancelled=lambda: False):
        native, action = self.validate(snapshot, number)
        if action != "open":
            raise ScreenError("Este item não oferece abertura nativa por duplo clique.")
        if cancelled():
            raise ScreenError("Ação cancelada antes da execução.")
        self.perform(native, action)
