"""Adaptação da AX API existente ao contrato compartilhado."""
import os
import uuid

from . import macos, macos_shell
from .backend import Backend, Element, ScreenError, Snapshot, Window
from .permissions import has_accessibility_permission
from ApplicationServices import AXUIElementIsAttributeSettable, AXUIElementSetAttributeValue

DESKTOP_PREFIX = "desktop:"
FIELD_NAME_LIMIT = 60
MAX_ANCESTORS = 64  # nenhuma árvore real é tão funda; o limite só impede um laço sem fim


def short_name(text):
    """Um campo sem título tem como nome o próprio texto dele: fica em uma linha só, e curta."""
    text = " ".join(text.split())
    return text if len(text) <= FIELD_NAME_LIMIT else text[:FIELD_NAME_LIMIT].rstrip() + "…"


class MacOSBackend(Backend):
    def __init__(self):
        super().__init__()
        # Controles da mesa e do Dock lidos na última varredura; só eles usam a classificação de shell.
        self.shell_elements = set()
        # (identidade da janela, elemento): o campo de texto onde o cursor estava da última vez.
        self.last_field = None
        # (identidade da janela, elemento): o foco já analisado, para não refazer a conta a cada 0,2 s.
        self.last_focus = None

    def __enter__(self):
        if not has_accessibility_permission():
            raise ScreenError("Autorize Acessibilidade em Ajustes do Sistema > Privacidade e Segurança.")
        return self

    def __exit__(self, *args):
        super().__exit__(*args)
        self.last_field = None
        self.last_focus = None

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
        if role in macos.TEXT_INPUT_ROLES:
            name = short_name(name)  # o "nome" pode ser o documento inteiro que está no campo
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

    # --- Digitação no campo ativo -------------------------------------------------------------

    @staticmethod
    def editable(element):
        return (macos.element_role(element) in macos.TEXT_INPUT_ROLES
                and macos.read_attribute(element, "AXEnabled") is not False)

    def belongs_to(self, element, window):
        """O elemento está dentro desta janela?"""
        if self.is_desktop(window):
            return False
        try:
            root = self.root(window)
        except ScreenError:
            return False
        if macos.same_element(macos.read_attribute(element, "AXWindow"), root):
            return True  # o caminho curto; nem todo aplicativo informa a janela do elemento
        for _ in range(MAX_ANCESTORS):
            if element is None:
                return False
            if macos.same_element(element, root):
                return True
            element = macos.parent(element)
        return False

    def remember_focus(self, window):
        """Guarda o campo de texto que está com o cursor. O executor chama isto a cada 0,2 s."""
        if window is None or window.pid == os.getpid() or self.is_desktop(window):
            return  # a barra do Jarvis (ou a mesa) na frente não apaga o último campo lembrado
        element = macos.focused_element(window.pid)
        if element is None:
            return
        if (self.last_focus and self.last_focus[0] == window.identity
                and macos.same_element(self.last_focus[1], element)):
            return  # o foco não saiu do lugar desde a última olhada
        self.last_focus = (window.identity, element)
        self.last_field = None
        if self.editable(element) and self.belongs_to(element, window):
            self.last_field = (window.identity, element)

    def prepare_dictation(self, window):
        """Antes da confirmação: descobre o campo ativo e devolve uma lista com só ele, o número 1."""
        self.remember_focus(window)
        if not self.last_field or self.last_field[0] != window.identity:
            raise ScreenError("Dê foco a um campo de texto e repita digitar.")
        element = self.last_field[1]
        if not self.window_exists(window):
            raise ScreenError("A janela mudou. Dê foco ao campo e repita digitar.")
        name, role, frame, action, visible, identity = self.describe(element, window.frame)
        if not visible or not self.editable(element):
            raise ScreenError("O campo mudou. Dê foco ao campo e repita digitar.")
        self.references = {1: (element, identity, window.frame)}
        self.snapshot = Snapshot(uuid.uuid4().hex, window, (Element(1, name, role, frame, action),))
        return self.snapshot

    def _still_focused(self, snapshot, element):
        """O teclado ainda vai para este campo? Tecla simulada cai em quem estiver com o foco."""
        pid = snapshot.window.pid
        return macos.keyboard_pid() == pid and macos.same_element(macos.focused_element(pid), element)

    def _focus_editable(self, snapshot, cancelled):
        """Depois do "confirmar": garante que o teclado está no campo. Na dúvida, não envia nada."""
        element, _ = self.validate(snapshot, 1)
        window = snapshot.window
        if not self.editable(element) or not self.belongs_to(element, window):
            raise ScreenError("O campo não está mais disponível para digitação.")
        current = macos.focused_element(window.pid)
        if current is not None and not macos.same_element(current, element):
            raise ScreenError("O campo ativo mudou. Repita o comando no campo desejado.")
        if cancelled():
            raise ScreenError("Digitação cancelada.")
        if macos.keyboard_pid() != window.pid and not macos.activate_application(window.pid):
            raise ScreenError("Não foi possível devolver o foco ao campo. Clique nele e repita o comando.")
        if current is None:
            # Só quando o aplicativo perdeu o foco: dar foco de novo a um campo que já o tem
            # seleciona todo o texto dele, e a digitação apagaria o que estava escrito.
            macos.set_attribute(element, "AXFocused", True)
        if not self._still_focused(snapshot, element):
            raise ScreenError("O foco mudou. Nenhum texto foi enviado.")
        if cancelled():
            raise ScreenError("Digitação cancelada.")
        return element

    def dictate(self, snapshot, text, cancelled=lambda: False):
        if not text or any(ord(char) < 32 for char in text):
            raise ScreenError("O texto deve conter uma frase sem teclas de controle.")
        element = self._focus_editable(snapshot, cancelled)
        # Conferido antes de cada letra: se a pessoa trocar de campo ou de aplicativo, a digitação para.
        if not macos.type_text(text, lambda: not cancelled() and self._still_focused(snapshot, element)):
            raise ScreenError("Digitação interrompida. Confira o texto já inserido antes de repetir.")

    def press_enter(self, snapshot, number=1, cancelled=lambda: False):
        if number != 1:
            raise ScreenError("Campo ativo inválido.")
        self._focus_editable(snapshot, cancelled)
        macos.press_key(macos.KEY_RETURN)

    def clear_text(self, snapshot, number=1, cancelled=lambda: False):
        if number != 1:
            raise ScreenError("Campo ativo inválido.")
        element = self._focus_editable(snapshot, cancelled)
        # Primeiro pela acessibilidade: age exatamente neste campo, sem depender de teclas.
        # Só vale se o campo ficou mesmo vazio; há aplicativo que aceita o pedido e não faz nada.
        if (macos.is_settable(element, "AXValue") and macos.set_attribute(element, "AXValue", "") == 0
                and not macos.read_attribute(element, "AXValue")):
            return
        macos.press_key(macos.KEY_A, macos.COMMAND)  # selecionar tudo
        if cancelled() or not self._still_focused(snapshot, element):
            raise ScreenError("O foco mudou. A limpeza do campo foi interrompida.")
        macos.press_key(macos.KEY_DELETE)
