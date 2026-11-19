"""AT-SPI2 (Linux), equivalente ao Microsoft UI Automation, sem cliques por coordenadas."""
import os
import time
import uuid

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi, GLib

from .backend import Backend, Element, Snapshot, ScreenError, Window, intersection
from . import keys

# O restante do projeto (Backend, regras de busca, testes) já fala os nomes de
# papel do UIA ("ButtonControl"...). Traduzir aqui evita tocar no código comum.
_ROLE_NAMES = {
    "PUSH_BUTTON": "ButtonControl", "TOGGLE_BUTTON": "CheckBoxControl",
    "CHECK_BOX": "CheckBoxControl", "RADIO_BUTTON": "RadioButtonControl",
    "ENTRY": "EditControl", "TEXT": "EditControl", "PASSWORD_TEXT": "EditControl",
    "COMBO_BOX": "ComboBoxControl", "LIST_ITEM": "ListItemControl",
    "TREE_ITEM": "TreeItemControl", "TABLE_CELL": "DataItemControl",
    "PAGE_TAB": "TabItemControl", "LINK": "HyperlinkControl",
    "MENU_ITEM": "MenuItemControl", "CHECK_MENU_ITEM": "MenuItemControl",
    "RADIO_MENU_ITEM": "MenuItemControl",
    "DOCUMENT_WEB": "DocumentControl", "DOCUMENT_FRAME": "DocumentControl",
    "DOCUMENT_TEXT": "DocumentControl", "DOCUMENT_EMAIL": "DocumentControl",
    "LABEL": "TextControl", "FRAME": "WindowControl", "WINDOW": "WindowControl",
    "DIALOG": "WindowControl", "PANEL": "PaneControl", "FILLER": "PaneControl",
    "LIST": "ListControl", "TREE": "TreeControl", "MENU": "MenuControl",
    "MENU_BAR": "MenuBarControl", "TOOL_BAR": "ToolBarControl",
}
ROLES = {getattr(Atspi.Role, n): v for n, v in _ROLE_NAMES.items() if hasattr(Atspi.Role, n)}
WINDOW_ROLES = {getattr(Atspi.Role, n) for n in ("FRAME", "WINDOW", "DIALOG", "ALERT", "FILE_CHOOSER")
                if hasattr(Atspi.Role, n)}
ITEM_ROLES = ("ListItemControl", "TreeItemControl", "DataItemControl")
CLICK = ("click", "press", "activate", "jump", "open")
OPEN = ("activate", "open", "click", "press", "jump")
MAX_CHILDREN = 500  # Tabelas/listas enormes custam uma chamada D-Bus por filho.


class InsertIgnored(ScreenError):
    """O campo respondeu True ao insert_text, mas o texto não mudou."""


def safe(fn, default=None):
    """Objetos AT-SPI morrem a qualquer momento; GLib.Error vira 'indisponível'."""
    try:
        return fn()
    except GLib.Error:
        return default


def has_state(acc, name):
    return acc.get_state_set().contains(getattr(Atspi.StateType, name))


def interfaces(acc):
    return safe(lambda: list(acc.get_interfaces()), [])


def identity(acc):
    """Substitui GetRuntimeId(): nome no barramento + caminho do objeto."""
    try:
        return (acc.app.bus_name, acc.path)
    except Exception:
        return ("ptr", hash(acc))  # Wrappers de um mesmo objeto são únicos enquanto vivos.


def window_id(acc):
    return str(hash(identity(acc)))  # Substitui o HWND; estável apenas dentro do processo.


def do_named(acc, names=CLICK):
    count = Atspi.Action.get_n_actions(acc)
    labels = [Atspi.Action.get_action_name(acc, i) for i in range(count)]
    for name in names:
        if name in labels:
            return Atspi.Action.do_action(acc, labels.index(name))
    return Atspi.Action.do_action(acc, 0) if count else False


class FocusTracker:
    """AT-SPI não tem GetFocusedControl(): ouvimos o evento de foco e guardamos o último.

    Sem thread própria: a libatspi não é thread-safe, então os eventos são
    despachados na mesma thread das chamadas síncronas, via pump().
    """

    EVENT = "object:state-changed:focused"

    def __init__(self):
        self.last = None
        self.context = GLib.MainContext.default()
        self.listener = Atspi.EventListener.new(self._on_event)
        self.listener.register(self.EVENT)

    def _on_event(self, event):
        if event.detail1:  # 1 = ganhou foco
            self.last = event.source

    def pump(self):
        while self.context.pending():
            self.context.iteration(False)

    def close(self):
        safe(lambda: self.listener.deregister(self.EVENT))


class LinuxBackend(Backend):
    def __init__(self):
        super().__init__()
        self.native = True
        self.tracker = None
        self.last_field = None
        self.last_scroll_focus = None
        self.last_foreign = None
        self._windows = {}
        self._node_windows = {}
        self._element_windows = {}

    def __enter__(self):
        Atspi.init()
        self.tracker = FocusTracker()
        return self

    def __exit__(self, *args):
        super().__exit__(*args)
        self.last_field = None
        self.last_scroll_focus = None
        self.last_foreign = None
        self._windows.clear()
        self._node_windows.clear()
        self._element_windows.clear()
        if self.tracker:
            self.tracker.close()

    # ---- janelas -------------------------------------------------------

    @staticmethod
    def frame(acc):
        rect = safe(lambda: Atspi.Component.get_extents(acc, Atspi.CoordType.SCREEN))
        return (rect.x, rect.y, rect.width, rect.height) if rect else (0, 0, 0, 0)

    def windows(self):
        desktop = Atspi.get_desktop(0)
        for i in range(safe(desktop.get_child_count, 0)):
            app = safe(lambda: desktop.get_child_at_index(i))
            if app is None:
                continue
            for j in range(safe(app.get_child_count, 0)):
                win = safe(lambda: app.get_child_at_index(j))
                if win is not None and safe(win.get_role) in WINDOW_ROLES:
                    yield win

    def make_window(self, acc):
        ident = window_id(acc)
        self._windows[ident] = acc
        return Window(ident, acc.get_process_id(), acc.get_name(), self.frame(acc))

    def resolve(self, window):
        acc = self._windows.get(window.identity)
        if acc is None or safe(acc.get_process_id) is None:
            acc = next((w for w in self.windows() if window_id(w) == window.identity), None)
            if acc is not None:
                self._windows[window.identity] = acc
        return acc

    def foreground(self):
        for win in self.windows():
            if safe(lambda: has_state(win, "ACTIVE"), False):
                return self.make_window(win)
        return None

    def tracked_foreground(self):
        if self.tracker:
            self.tracker.pump()
        current = self.foreground()
        if current and current.pid != os.getpid():
            self.last_foreign = current
            return current
        # A speakbar (nosso processo) ficou ativa: continuar na última janela externa.
        if self.last_foreign and self.window_exists(self.last_foreign):
            return self.last_foreign
        return current

    def window_exists(self, window):
        acc = self.resolve(window)
        return (acc is not None and safe(acc.get_process_id) == window.pid
                and self.frame(acc) == window.frame)

    def raise_window(self, window, timeout=0.5):
        """Substitui SetForegroundWindow. No Wayland o compositor pode recusar."""
        acc = self.resolve(window)
        if acc is None or not safe(lambda: Atspi.Component.grab_focus(acc), False):
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if safe(lambda: has_state(acc, "ACTIVE"), False):
                return True
            time.sleep(0.05)
        return False

    def children(self, node):
        count = node.get_child_count()
        out = []
        for i in range(min(count, MAX_CHILDREN)):
            child = safe(lambda: node.get_child_at_index(i))
            if child is not None:
                out.append(child)
        if count > MAX_CHILDREN:
            self.walk_incomplete = True
        return out

    def belongs_to(self, acc, window):
        if window is None:
            return False
        node = acc
        for _ in range(64):
            if node is None:
                return False
            if window_id(node) == window.identity:
                return True
            node = safe(node.get_parent)
        return False

    def walk(self, window, max_depth):
        if window is None:
            raise ScreenError("Nenhuma janela ativa encontrada pelo AT-SPI.")
        root = self.resolve(window)
        if root is None or safe(root.get_process_id) != window.pid:
            raise ScreenError("Janela indisponível para leitura.")
        stack = [(0, root, window.frame, window)]
        while stack:
            depth, node, clip, source = stack.pop()
            self._node_windows[identity(node)] = source
            yield depth, node, clip
            try:
                children = self.children(node)
            except GLib.Error:
                self.walk_incomplete = True
                continue
            if depth >= max_depth and children:
                self.walk_incomplete = True
            if clip is not None and not safe(lambda: has_state(node, "SHOWING"), False):
                clip = None  # Ex.: menu de contexto fechado: os filhos dizem 'showing', mas não aparecem.
            if depth < max_depth:
                if ROLES.get(safe(node.get_role)) == "DocumentControl":
                    frame = self.frame(node)
                    if frame[2] > 0 and frame[3] > 0:
                        clip = intersection(clip, frame)
                stack.extend((depth+1, child, clip, source) for child in reversed(children))

    def scan(self, *args, **kwargs):
        self._node_windows.clear()
        self._element_windows.clear()
        snapshot = super().scan(*args, **kwargs)
        self._element_windows = {number: self._node_windows[identity(native)]
                                 for number, (native, _, _) in self.references.items()}
        self._node_windows.clear()
        return snapshot

    def validate(self, snapshot, number):
        native, action = super().validate(snapshot, number)
        source = self._element_windows.get(number)
        if source and (not self.window_exists(source) or not self.belongs_to(native, source)):
            raise ScreenError("O controle saiu da janela original. Solicite nova leitura.")
        return native, action

    # ---- foco e campos -------------------------------------------------

    def focused_in(self, window):
        """Último foco observado; se ainda não houve evento, procura no próprio janela."""
        if self.tracker:
            self.tracker.pump()
        last = self.tracker.last if self.tracker else None
        if last is not None and safe(lambda: has_state(last, "FOCUSED"), False):
            return last
        root = self.resolve(window)
        queue, seen = [root] if root else [], 0
        while queue and seen < 4000:
            node = queue.pop(0)
            seen += 1
            if safe(lambda: has_state(node, "FOCUSED"), False):
                return node
            queue.extend(safe(lambda: self.children(node), []))
        return None

    def remember_focus(self, window):
        control = self.focused_in(window)
        if control is None or safe(control.get_process_id) == os.getpid():
            return  # A speakbar não substitui o último campo externo.
        belongs = self.belongs_to(control, window)
        self.last_scroll_focus = (window.identity, control) if belongs else None
        self.last_field = None
        if belongs and self.editable(control):
            self.last_field = (window.identity, control, self.describe(control, window.frame))

    def editable(self, acc):
        if ROLES.get(acc.get_role()) not in ("EditControl", "ComboBoxControl", "DocumentControl"):
            return False
        if not all(has_state(acc, s) for s in ("ENABLED", "FOCUSABLE", "SHOWING")):
            return False
        return has_state(acc, "EDITABLE") and "EditableText" in interfaces(acc)

    @staticmethod
    def action(acc):
        state = acc.get_state_set()
        if not state.contains(Atspi.StateType.ENABLED):
            return ""
        role = ROLES.get(acc.get_role(), "")
        actions = Atspi.Action.get_n_actions(acc) if "Action" in interfaces(acc) else 0
        if role in ("EditControl", "ComboBoxControl") and state.contains(Atspi.StateType.FOCUSABLE):
            return "focus"
        if role == "CheckBoxControl" and actions:
            return "toggle"
        if role in ITEM_ROLES + ("TabItemControl", "RadioButtonControl"):
            parent = acc.get_parent()
            if actions or (parent is not None and "Selection" in interfaces(parent)):
                return "select"
        if role in ("ButtonControl", "HyperlinkControl", "MenuItemControl") and actions:
            return "invoke"
        return ""

    def describe(self, acc, clip):
        frame = self.frame(acc)
        visible = (has_state(acc, "SHOWING") and frame[2] >= 4 and frame[3] >= 4
                   and intersection(frame, clip) is not None)
        role = ROLES.get(acc.get_role()) or acc.get_role_name().title().replace(" ", "") + "Control"
        return (acc.get_name().strip(), role, frame, self.action(acc), visible, identity(acc))

    def perform(self, acc, action):
        if action == "focus":
            if not Atspi.Component.grab_focus(acc):
                raise ScreenError("O aplicativo recusou o foco no campo.")
        elif action in ("invoke", "toggle"):
            if not do_named(acc):
                raise ScreenError("O aplicativo recusou o acionamento.")
        elif action == "select":
            parent = acc.get_parent()
            if parent is not None and "Selection" in interfaces(parent):
                if Atspi.Selection.select_child(parent, acc.get_index_in_parent()):
                    return
            if not do_named(acc):
                raise ScreenError("O aplicativo recusou a seleção.")
        else:
            raise ScreenError("Esse controle não oferece uma ação nativa suportada.")

    # ---- comandos compostos -------------------------------------------

    def open_item(self, snapshot, number, cancelled=lambda: False):
        control, _ = self.validate(snapshot, number)
        if ROLES.get(control.get_role()) not in ITEM_ROLES:
            raise ScreenError("Duplo clique disponível para itens e atalhos que oferecem abertura nativa.")
        if "Action" not in interfaces(control) or not Atspi.Action.get_n_actions(control):
            raise ScreenError("Este item não oferece abertura nativa por duplo clique.")
        if cancelled():
            raise ScreenError("Abertura cancelada.")
        if not do_named(control, OPEN):
            raise ScreenError("O aplicativo recusou a abertura do item.")

    def context_menu(self, snapshot, number, cancelled=lambda: False):
        control, _ = self.validate(snapshot, number)
        if not has_state(control, "FOCUSABLE"):
            raise ScreenError("Este controle não permite abrir o menu de contexto pelo teclado.")
        source = self._element_windows.get(number, snapshot.window)
        if cancelled():
            raise ScreenError("Menu de contexto cancelado.")
        if not self.raise_window(source):
            raise ScreenError("Não foi possível devolver o foco à janela do controle.")
        if ROLES.get(control.get_role()) in ITEM_ROLES:
            self.perform(control, "select")
        if not Atspi.Component.grab_focus(control):
            raise ScreenError("Não foi possível dar foco ao controle para abrir seu menu.")
        self.validate(snapshot, number)
        focused = self.focused_in(source)
        if focused is None or identity(focused) != identity(control):
            raise ScreenError("O foco mudou. O menu de contexto não foi solicitado.")
        if cancelled():
            raise ScreenError("Menu de contexto cancelado.")
        keys.press("shift", "F10")

    def prepare_dictation(self, window):
        self._element_windows.clear()
        self.remember_focus(window)
        if not self.last_field or self.last_field[0] != window.identity:
            raise ScreenError("Dê foco a um campo de texto e repita digitar.")
        _, control, old = self.last_field
        name, role, frame, action, visible, ident = self.describe(control, window.frame)
        if not self.window_exists(window) or not visible or old != (name, role, frame, action, visible, ident):
            raise ScreenError("O campo mudou. Dê foco ao campo e repita digitar.")
        if not self.editable(control):
            raise ScreenError("O campo não permite digitação.")
        element = Element(1, name, role, frame, action)
        self.references = {1: (control, ident, window.frame)}
        self.snapshot = Snapshot(uuid.uuid4().hex, window, (element,))
        return self.snapshot

    def _focus_editable(self, snapshot, cancelled):
        control, _ = self.validate(snapshot, 1)
        if not self.editable(control) or not self.belongs_to(control, snapshot.window):
            raise ScreenError("O campo não está mais disponível para digitação.")
        current = self.focused_in(snapshot.window)
        if (current is not None and safe(current.get_process_id) != os.getpid()
                and identity(current) != identity(control)):
            raise ScreenError("O campo ativo mudou. Repita o comando no campo desejado.")
        if cancelled():
            raise ScreenError("Digitação cancelada.")
        if not self.raise_window(snapshot.window) or not Atspi.Component.grab_focus(control):
            raise ScreenError("Não foi possível devolver o foco ao campo. Repita o comando.")
        time.sleep(0.1)  # O evento de foco chega de forma assíncrona.
        focused, foreground = self.focused_in(snapshot.window), self.foreground()
        if (focused is None or identity(focused) != identity(control)
                or foreground is None or foreground.identity != snapshot.window.identity):
            raise ScreenError("O foco mudou. Nenhum texto foi enviado.")
        if cancelled():
            raise ScreenError("Digitação cancelada.")
        return control

    def press_enter(self, snapshot, number=1, cancelled=lambda: False):
        if number != 1:
            raise ScreenError("Campo ativo inválido.")
        self._focus_editable(snapshot, cancelled)
        keys.press("Return")

    def clear_text(self, snapshot, number=1, cancelled=lambda: False):
        if number != 1:
            raise ScreenError("Campo ativo inválido.")
        control = self._focus_editable(snapshot, cancelled)
        if not Atspi.EditableText.set_text_contents(control, ""):
            total = Atspi.Text.get_character_count(control)
            if not Atspi.EditableText.delete_text(control, 0, total):
                raise ScreenError("O aplicativo recusou a limpeza do campo.")

    def dictate(self, snapshot, text, cancelled=lambda: False):
        control = self._focus_editable(snapshot, cancelled)
        ident = identity(control)

        def still_focused():
            focused = self.focused_in(snapshot.window)
            return not cancelled() and focused is not None and identity(focused) == ident

        if not still_focused():
            raise ScreenError("O foco mudou. Nenhum texto foi enviado.")
        try:
            self._insert_atspi(control, text)
        except InsertIgnored:
            # Plano B: o campo ignora a escrita por AT-SPI (ex.: Firefox). Revalida o
            # foco imediatamente antes de mandar teclas para outro aplicativo.
            if not still_focused():
                raise ScreenError("O foco mudou. Nenhum texto foi enviado.")
            keys.type_text(text)

    def _insert_atspi(self, control, text):
        # Insere no cursor e substitui a seleção, sem apagar o resto do campo.
        position = Atspi.Text.get_caret_offset(control)
        if Atspi.Text.get_n_selections(control):
            selection = Atspi.Text.get_selection(control, 0)
            start, end = sorted((selection.start_offset, selection.end_offset))
            if end > start and Atspi.EditableText.delete_text(control, start, end):
                position = start
        base = Atspi.Text.get_text(control, 0, -1)
        # O comprimento de insert_text é em bytes UTF-8; posições são em caracteres.
        if not Atspi.EditableText.insert_text(control, position, text, len(text.encode("utf-8"))):
            raise ScreenError("O aplicativo recusou a inserção do texto.")
        deadline = time.monotonic() + 0.5
        while Atspi.Text.get_text(control, 0, -1) == base:
            if time.monotonic() > deadline:
                raise InsertIgnored("O aplicativo aceitou o comando, mas não inseriu o texto.")
            time.sleep(0.05)
        Atspi.Text.set_caret_offset(control, position + len(text))