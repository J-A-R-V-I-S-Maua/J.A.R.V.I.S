"""Microsoft UI Automation, sem simulação de cliques por coordenadas."""
import sys
import os
import uuid
from .backend import Backend, Element, Snapshot, ScreenError, Window, intersection
from .shell import DESKTOP_CLASSES, TASKBAR_CLASSES


class WindowsBackend(Backend):
    def __init__(self, api=None):
        super().__init__()
        self.native = api is None
        if api is None:
            # Importado somente na thread dedicada; UIA deve usar COM MTA.
            sys.coinit_flags = 0
            import uiautomation as api
        self.api = api
        self.context = None
        self.tracker = None
        self.last_field = None
        self._node_windows = {}
        self._element_windows = {}

    def __enter__(self):
        self.context = self.api.UIAutomationInitializerInThread()
        self.context.__enter__()
        if self.native:
            from .foreground import ForegroundTracker
            try:
                self.tracker = ForegroundTracker()
            except Exception:
                self.context.__exit__(None, None, None)
                raise
        return self

    def __exit__(self, *args):
        super().__exit__(*args)
        self.last_field = None
        self._node_windows.clear()
        self._element_windows.clear()
        if self.tracker:
            self.tracker.close()
        self.context.__exit__(*args)

    @staticmethod
    def frame(control):
        rect = control.BoundingRectangle
        return (rect.left, rect.top, rect.right-rect.left, rect.bottom-rect.top)

    def foreground(self):
        control = self.api.GetForegroundControl()
        control = self.desktop_root(control)
        if control is None or not control.NativeWindowHandle:
            return None
        return Window(str(control.NativeWindowHandle), control.ProcessId, control.Name, self.frame(control))

    def desktop_root(self, control):
        """Normaliza fundo, ícones e barras de tarefas para a mesma sessão do shell."""
        if control is None or control.ClassName not in DESKTOP_CLASSES + TASKBAR_CLASSES:
            return control
        for root in self.shell_controls():
            if root.ClassName in DESKTOP_CLASSES[:2]:
                return root
        return control

    def shell_controls(self):
        if not self.native:
            return [control for control in self.api.GetRootControl().GetChildren()
                    if control.ClassName in DESKTOP_CLASSES[:2] + TASKBAR_CLASSES]
        from .shell import shell_handles
        desktops, taskbars = shell_handles()
        controls = []
        for hwnd in desktops + taskbars:
            try:
                control = self.api.ControlFromHandle(hwnd)
                if control is not None:
                    controls.append(control)
            except Exception:
                self.walk_incomplete = True
        return controls

    def minimized_or_closed(self, handle):
        if not self.native:
            return False
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsWindow.argtypes = [wintypes.HWND]
        return not user32.IsWindow(handle) or bool(user32.IsIconic(handle))

    def tracked_foreground(self):
        if self.tracker:
            self.tracker.pump()
        current = self.foreground()
        if current and current.pid != os.getpid():
            if self.tracker:
                self.tracker.handle = int(current.identity)
            return current
        if self.tracker and self.tracker.handle:
            if self.minimized_or_closed(self.tracker.handle):
                for root in self.shell_controls():
                    if root.ClassName in DESKTOP_CLASSES[:2]:
                        self.tracker.handle = root.NativeWindowHandle
                        return Window(str(root.NativeWindowHandle), root.ProcessId, root.Name, self.frame(root))
            control = self.desktop_root(self.api.ControlFromHandle(self.tracker.handle))
            if control:
                return Window(str(control.NativeWindowHandle), control.ProcessId, control.Name, self.frame(control))
        return current

    def window_exists(self, window):
        control = self.api.ControlFromHandle(int(window.identity))
        return control is not None and control.ProcessId == window.pid and self.frame(control) == window.frame

    def walk(self, window, max_depth):
        control = self.api.ControlFromHandle(int(window.identity))
        if control is None or control.ProcessId != window.pid:
            raise ScreenError("Janela indisponível para leitura.")
        roots = [control]
        if control.ClassName in DESKTOP_CLASSES + TASKBAR_CLASSES:
            roots = self.shell_controls()
            # O host original deve continuar presente para preservar sua identidade.
            if not any(root.NativeWindowHandle == int(window.identity) for root in roots):
                raise ScreenError("A área de trabalho mudou. Solicite nova leitura.")
        stack = []
        for root in reversed(roots):
            source = Window(str(root.NativeWindowHandle), root.ProcessId, root.Name, self.frame(root))
            stack.append((0, root, source.frame, source))
        while stack:
            depth, node, clip, source = stack.pop()
            self._node_windows[id(node)] = source
            yield depth, node, clip
            try:
                children = node.GetChildren()
            except Exception:
                self.walk_incomplete = True
                continue
            if depth >= max_depth and children:
                self.walk_incomplete = True
            if depth < max_depth:
                # Panes são estruturas de layout, não necessariamente viewports.
                # Alguns têm retângulo vazio embora seus filhos sejam visíveis.
                if node.ControlTypeName == "DocumentControl":
                    frame = self.frame(node)
                    if frame[2] > 0 and frame[3] > 0:
                        clip = intersection(clip, frame)
                stack.extend((depth+1, child, clip, source) for child in reversed(children))

    def scan(self, *args, **kwargs):
        self._node_windows.clear()
        self._element_windows.clear()
        snapshot = super().scan(*args, **kwargs)
        self._element_windows = {number: self._node_windows[id(native)]
                                 for number, (native, _, _) in self.references.items()}
        self._node_windows.clear()
        return snapshot

    def validate(self, snapshot, number):
        native, action = super().validate(snapshot, number)
        source = self._element_windows.get(number)
        if source and (not self.window_exists(source) or not self.belongs_to(native, source)):
            raise ScreenError("O controle saiu da janela original. Solicite nova leitura.")
        return native, action

    def remember_focus(self, window):
        control = self.api.GetFocusedControl()
        if control is None or control.ProcessId == os.getpid():
            return  # A speakbar não substitui o último campo externo.
        self.last_field = None
        if self.editable(control) and self.belongs_to(control, window):
            self.last_field = (window.identity, control, self.describe(control, window.frame))

    def open_item(self, snapshot, number, cancelled=lambda: False):
        control, _ = self.validate(snapshot, number)
        # Invoke representa a ação padrão de abrir um item, sem repetir a seleção.
        # Não enviar dois Invokes: isso poderia abrir duas instâncias do aplicativo.
        if control.ControlTypeName not in ("ListItemControl", "TreeItemControl", "DataItemControl"):
            raise ScreenError("Duplo clique disponível para itens e atalhos que oferecem abertura nativa.")
        pattern = control.GetPattern(10000)  # UIA_InvokePatternId
        if pattern is None:
            raise ScreenError("Este item não oferece abertura nativa por duplo clique.")
        if cancelled():
            raise ScreenError("Abertura cancelada.")
        if not pattern.Invoke():
            raise ScreenError("O aplicativo recusou a abertura do item.")

    def context_menu(self, snapshot, number, cancelled=lambda: False):
        control, _ = self.validate(snapshot, number)
        if not control.IsKeyboardFocusable:
            raise ScreenError("Este controle não permite abrir o menu de contexto pelo teclado.")
        source = self._element_windows.get(number, snapshot.window)
        if cancelled():
            raise ScreenError("Menu de contexto cancelado.")
        if not self.api.SetForegroundWindow(int(source.identity)):
            raise ScreenError("Não foi possível devolver o foco à janela do controle.")
        if control.ControlTypeName in ("ListItemControl", "TreeItemControl", "DataItemControl"):
            pattern = control.GetPattern(10010)  # UIA_SelectionItemPatternId
            if pattern and not pattern.Select():
                raise ScreenError("Não foi possível selecionar o item para abrir seu menu.")
        if not control.SetFocus():
            raise ScreenError("Não foi possível dar foco ao controle para abrir seu menu.")
        # Validação após seleção/foco: não mandar o atalho para outro controle.
        self.validate(snapshot, number)
        focused = self.api.GetFocusedControl()
        if (focused is None or tuple(focused.GetRuntimeId()) != tuple(control.GetRuntimeId())
                or self.api.GetForegroundWindow() != int(source.identity)):
            raise ScreenError("O foco mudou. O menu de contexto não foi solicitado.")
        if cancelled():
            raise ScreenError("Menu de contexto cancelado.")
        self.api.SendKeys("{Shift}{F10}", waitTime=0)

    def editable(self, control):
        if control.ControlTypeName not in ("EditControl", "ComboBoxControl", "DocumentControl"):
            return False
        if not control.IsEnabled or not control.IsKeyboardFocusable or control.IsOffscreen:
            return False
        # Documentos editáveis (ex.: Bloco de Notas) podem expor apenas TextPattern.
        value = control.GetPattern(10002)  # UIA_ValuePatternId
        if value is not None:
            return not value.IsReadOnly
        if control.ControlTypeName == "DocumentControl":
            pattern = control.GetPattern(10014)  # UIA_TextPatternId
            return bool(pattern and pattern.DocumentRange.GetAttributeValue(40015) is False)
        return True

    def belongs_to(self, control, window):
        if window is None:
            return False
        parent = control
        for _ in range(64):
            if parent is None:
                return False
            if str(parent.NativeWindowHandle) == window.identity:
                return True
            parent = parent.GetParentControl()
        return False

    def prepare_dictation(self, window):
        self._element_windows.clear()
        self.remember_focus(window)
        if not self.last_field or self.last_field[0] != window.identity:
            raise ScreenError("Dê foco a um campo de texto e repita digitar.")
        _, control, old = self.last_field
        name, role, frame, action, visible, identity = self.describe(control, window.frame)
        if not self.window_exists(window) or not visible or old != (name, role, frame, action, visible, identity):
            raise ScreenError("O campo mudou. Dê foco ao campo e repita digitar.")
        if not self.editable(control):
            raise ScreenError("O campo não permite digitação.")
        element = Element(1, name, role, frame, action)
        self.references = {1: (control, identity, window.frame)}
        self.snapshot = Snapshot(uuid.uuid4().hex, window, (element,))
        return self.snapshot

    def _focus_editable(self, snapshot, cancelled):
        control, _ = self.validate(snapshot, 1)
        if not self.editable(control) or not self.belongs_to(control, snapshot.window):
            raise ScreenError("O campo não está mais disponível para digitação.")
        current = self.api.GetFocusedControl()
        if current and current.ProcessId != os.getpid() and tuple(current.GetRuntimeId()) != tuple(control.GetRuntimeId()):
            raise ScreenError("O campo ativo mudou. Repita o comando no campo desejado.")
        if cancelled():
            raise ScreenError("Digitação cancelada.")
        if not self.api.SetForegroundWindow(int(snapshot.window.identity)) or not control.SetFocus():
            raise ScreenError("Não foi possível devolver o foco ao campo. Repita o comando.")
        focused = self.api.GetFocusedControl()
        foreground = self.foreground()
        if (focused is None or tuple(focused.GetRuntimeId()) != tuple(control.GetRuntimeId())
                or foreground is None or foreground.identity != snapshot.window.identity):
            raise ScreenError("O foco mudou. Nenhum texto foi enviado.")
        if cancelled():
            raise ScreenError("Digitação cancelada.")
        return control

    def press_enter(self, snapshot, number=1, cancelled=lambda: False):
        if number != 1:
            raise ScreenError("Campo ativo inválido.")
        self._focus_editable(snapshot, cancelled)
        self.api.SendKeys("{Enter}", waitTime=0)

    def clear_text(self, snapshot, number=1, cancelled=lambda: False):
        if number != 1:
            raise ScreenError("Campo ativo inválido.")
        control = self._focus_editable(snapshot, cancelled)
        value = control.GetPattern(10002)
        if value is not None:
            if value.IsReadOnly or not value.SetValue(""):
                raise ScreenError("O aplicativo recusou a limpeza do campo.")
        else:
            # Editores que expõem só TextPattern usam o atalho no campo validado.
            self.api.SendKeys("{Ctrl}a", waitTime=0)
            focused = self.api.GetFocusedControl()
            if (cancelled() or self.api.GetForegroundWindow() != int(snapshot.window.identity)
                    or focused is None or tuple(focused.GetRuntimeId()) != tuple(control.GetRuntimeId())):
                raise ScreenError("O foco mudou. A limpeza do campo foi interrompida.")
            self.api.SendKeys("{Backspace}", waitTime=0)

    def dictate(self, snapshot, text, cancelled=lambda: False):
        control = self._focus_editable(snapshot, cancelled)
        from .typing import send_literal_text
        identity = tuple(control.GetRuntimeId())

        def allowed():
            if cancelled() or self.api.GetForegroundWindow() != int(snapshot.window.identity):
                return False
            focused = self.api.GetFocusedControl()
            return focused is not None and tuple(focused.GetRuntimeId()) == identity

        send_literal_text(text, allowed)  # Inserção no cursor/seleção; não apaga o restante do campo.

    @staticmethod
    def action(control):
        if not control.IsEnabled:
            return ""
        role = control.ControlTypeName
        if role in ("EditControl", "ComboBoxControl") and control.IsKeyboardFocusable:
            return "focus"
        if role == "CheckBoxControl" and control.GetTogglePattern():
            return "toggle"
        if role in ("ListItemControl", "TreeItemControl", "TabItemControl", "RadioButtonControl"):
            if control.GetSelectionItemPattern():
                return "select"
        if role in ("ButtonControl", "HyperlinkControl", "MenuItemControl", "SplitButtonControl"):
            if control.GetInvokePattern():
                return "invoke"
        return ""

    def describe(self, control, clip):
        frame = self.frame(control)
        visible = not control.IsOffscreen and frame[2] >= 4 and frame[3] >= 4 and intersection(frame, clip) is not None
        return (control.Name.strip(), control.ControlTypeName, frame, self.action(control),
                visible, tuple(control.GetRuntimeId()))

    def perform(self, control, action):
        if action == "focus":
            if not control.SetFocus():
                raise ScreenError("O aplicativo recusou o foco no campo.")
        elif action == "invoke":
            if not control.GetInvokePattern().Invoke():
                raise ScreenError("O aplicativo recusou o acionamento.")
        elif action == "toggle":
            if not control.GetTogglePattern().Toggle():
                raise ScreenError("O aplicativo recusou a alteração da opção.")
        elif action == "select":
            if not control.GetSelectionItemPattern().Select():
                raise ScreenError("O aplicativo recusou a seleção.")
        else:
            raise ScreenError("Esse controle não oferece uma ação nativa suportada.")
