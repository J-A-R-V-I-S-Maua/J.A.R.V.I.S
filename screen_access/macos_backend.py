"""Adaptação da AX API existente ao contrato compartilhado."""
from dataclasses import replace
import os
import threading
import time
import uuid

from . import macos, macos_shell
from .backend import Backend, Element, ScreenError, Snapshot, Window
from .matching import find_in_text, last_word
from .scroll import SCROLL_DURATIONS, SCROLL_FRAME_SECONDS, ScrollMixin, ScrollOutcome, ScrollTargets
from ApplicationServices import AXUIElementIsAttributeSettable, AXUIElementSetAttributeValue

# Navegadores conhecidos: Cmd+W em outro aplicativo fecharia uma janela ou um documento.
BROWSERS = frozenset({
    "com.google.Chrome", "com.google.Chrome.beta", "com.google.Chrome.canary", "org.chromium.Chromium",
    "com.apple.Safari", "com.apple.SafariTechnologyPreview", "org.mozilla.firefox",
    "org.mozilla.firefoxdeveloperedition", "com.microsoft.edgemac", "com.brave.Browser",
    "company.thebrowser.Browser", "com.operasoftware.Opera", "com.vivaldi.Vivaldi",
})
BROWSER_DONE = {
    "new_tab": "Nova aba aberta.", "close_tab": "Aba fechada. Diga reabrir aba para trazê-la de volta.",
    "reopen_tab": "Aba reaberta.", "next_tab": "Próxima aba.", "previous_tab": "Aba anterior.",
    "last_tab": "Última aba.", "back": "Página anterior.", "forward": "Próxima página.",
    "reload": "Página recarregada.",
}
BROWSER_UNAVAILABLE = {
    "back": "Não há página anterior nesta aba.", "forward": "Não há próxima página nesta aba.",
    "reopen_tab": "Não há aba fechada para reabrir.", "next_tab": "Só há uma aba aberta.",
    "previous_tab": "Só há uma aba aberta.",
}
# Papéis que abrem um item com AXOpen numa janela comum (o nome de um arquivo no Finder, a barra
# lateral, o caminho). Imagens só contam no Finder (visualização por ícones): numa página, uma foto
# com AXOpen não é um item para abrir.
OPEN_ROLES = frozenset({"AXTextField", "AXCell", "AXStaticText", "AXRow"})
DESKTOP_PREFIX = "desktop:"
MISSION_CONTROL_PREFIX = "missioncontrol:"
MENU_PREFIX = "menu:"  # um menu de contexto aberto, tratado como a janela da frente enquanto existir
FIELD_NAME_LIMIT = 60
MENU_WAIT = 2.5  # o menu aparece em menos de 1 s
MAX_ANCESTORS = 64  # nenhuma árvore real é tão funda; o limite só impede um laço sem fim


def utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


def from_utf16(text, offset):
    """Posição em caracteres do Python para uma posição em unidades UTF-16 (emojis contam 2)."""
    count = 0
    for index, char in enumerate(text):
        if count >= offset:
            return index
        count += 2 if ord(char) > 0xFFFF else 1
    return len(text)


def short_name(text):
    """Um campo sem título tem como nome o próprio texto dele: fica em uma linha só, e curta."""
    text = " ".join(text.split())
    return text if len(text) <= FIELD_NAME_LIMIT else text[:FIELD_NAME_LIMIT].rstrip() + "…"


class AXScrollPattern:
    """Barra de rolagem AX com a mesma interface do ScrollPattern do UIA (porcentagens de 0 a 100)."""

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
        """Quanto do conteúdo cabe na área. Sem a altura do conteúdo, 100: só o passo nativo da barra."""
        area = macos.element_frame(self.area)
        contents = macos.read_attribute(self.area, "AXContents") or ()
        heights = [frame[3] for frame in map(macos.element_frame, contents) if frame]
        if area is None or not heights or max(heights) <= area[3]:
            return 100.0
        return 100 * area[3] / max(heights)

    def SetScrollPercent(self, horizontal, vertical, waitTime=0):
        return macos.set_attribute(self.bar, "AXValue", vertical / 100) == 0

    def Scroll(self, horizontal, vertical, waitTime=0):
        action = "AXIncrement" if vertical in (3, 4) else "AXDecrement"  # UIA: 3 e 4 descem
        return action in macos.action_names(self.bar) and macos.perform_action(self.bar, action) == 0


class MacOSBackend(ScrollMixin, Backend):
    def __init__(self):
        super().__init__()
        # Controles da mesa e do Dock lidos na última varredura; só eles usam a classificação de shell.
        self.shell_elements = set()
        self.finder_walk = False  # a última leitura foi de uma janela do Finder?
        self.open_menu = None  # (pid, AXMenu) do menu de contexto que o Jarvis abriu
        # (identidade da janela, elemento): o campo de texto onde o cursor estava da última vez.
        self.last_field = None
        # (identidade da janela, elemento): o foco já analisado, para não refazer a conta a cada 0,2 s.
        self.last_focus = None
        # O texto que a pergunta "Colar …?" mostrou; colar outra coisa depois do "confirmar", nunca.
        self.paste_preview = None

    def __enter__(self):
        if not macos.has_accessibility_permission():
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

    @staticmethod
    def is_mission_control(window):
        return window.identity.startswith(MISSION_CONTROL_PREFIX)

    @staticmethod
    def is_menu(window):
        return window.identity.startswith(MENU_PREFIX)

    def is_shell(self, window):
        """Mesa, Mission Control ou um menu aberto: não é uma janela com campos de texto."""
        return self.is_desktop(window) or self.is_mission_control(window) or self.is_menu(window)

    def menu_window(self):
        """O menu de contexto aberto pelo Jarvis, enquanto ainda estiver na tela."""
        if self.open_menu is None:
            return None
        pid, menu = self.open_menu
        frame = macos.element_frame(menu)
        # Fechado, o menu continua respondendo, mas com tamanho zero (medido no Finder).
        if frame is None or not frame[2] or not frame[3] or macos.element_role(menu) != "AXMenu":
            self.open_menu = None  # fechou: escolheram uma opção, apertaram Esc ou clicaram fora
            return None
        return Window(f"{MENU_PREFIX}{pid}", pid, "Menu de contexto", frame)

    def mission_control_window(self):
        """O Mission Control aberto como uma "janela" que cobre todos os monitores, ou None."""
        opened = macos_shell.mission_control()
        frame = macos_shell.mission_control_frame(opened[1]) if opened is not None else None
        if frame is None:
            return None
        return Window(f"{MISSION_CONTROL_PREFIX}{opened[0]}", opened[0], "Mission Control", frame)

    def desktop_window(self, pid):
        roots = macos_shell.desktop_roots(pid)
        frame = macos.element_frame(roots[0]) if roots else None
        if frame is None:
            return None
        return Window(f"{DESKTOP_PREFIX}{pid}", pid, "Área de trabalho", frame)

    def foreground(self):
        # Aberto, o Mission Control cobre tudo: ele é a "janela" que a pessoa está vendo.
        mission_control = self.mission_control_window()
        if mission_control is not None:
            return mission_control
        menu = self.menu_window()
        if menu is not None:
            return menu
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
        if self.is_menu(window):
            if self.open_menu is None or self.menu_window() is None:
                raise ScreenError("O menu foi fechado.")
            return self.open_menu[1]
        if self.is_mission_control(window):
            opened = macos_shell.mission_control()
            if opened is None or not opened[1]:
                raise ScreenError("O Mission Control foi fechado.")
            return opened[1][0]
        native = macos.front_window(window.pid)
        if native is None or self.identity(window.pid, native) != window.identity:
            raise ScreenError("A janela mudou.")
        return native

    def window_exists(self, window):
        if self.is_desktop(window):
            roots = macos_shell.desktop_roots(window.pid)
            return bool(roots) and macos.element_frame(roots[0]) == window.frame
        if self.is_mission_control(window):
            current = self.mission_control_window()
            return current is not None and current.frame == window.frame
        try:
            return macos.element_frame(self.root(window)) == window.frame
        except ScreenError:
            return False

    def walk(self, window, max_depth):
        if self.is_desktop(window):
            yield from self._walk_shell(window)
            return
        if self.is_mission_control(window):
            yield from self._walk_mission_control(window)
            return
        if self.is_menu(window):
            yield from ((1, item, window.frame) for item in macos_shell.menu_options(self.root(window)))
            return
        self.finder_walk = macos_shell.is_finder(window.pid)
        macos.enable_web_accessibility(window.pid)
        macos.wait_for_web_content(self.root(window))
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

    def scan(self, window, *args, **kwargs):
        snapshot = super().scan(window, *args, **kwargs)
        if self.is_mission_control(window):
            # Miniaturas grandes, vistas de longe: o número vai no meio, grande e sem contorno.
            snapshot = replace(snapshot, elements=tuple(replace(e, badge="center") for e in snapshot.elements))
            self.snapshot = snapshot
        return snapshot

    def _walk_mission_control(self, window):
        """Janelas e mesas do Mission Control; o AXPress de cada uma leva até ela."""
        self.shell_elements = set()
        opened = macos_shell.mission_control()
        if opened is None:
            raise ScreenError("O Mission Control foi fechado.")
        for item in macos_shell.mission_control_items(opened[1]):
            self.shell_elements.add(item)
            yield 1, item, window.frame

    @staticmethod
    def shell_action(element, role):
        actions = macos.action_names(element)
        if role in (macos_shell.DOCK_ITEM, macos_shell.MISSION_CONTROL_ITEM) and "AXPress" in actions:
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
        else:
            actions = macos.action_names(element)
            if enabled and role in macos.TEXT_INPUT_ROLES and self.focusable(element):
                action = "focus"
            elif enabled and role in macos.ACTION_ROLES and "AXPress" in actions:
                action = "press"
            elif macos_shell.OPEN_ACTION in actions and (role in OPEN_ROLES or (role == "AXImage" and self.finder_walk)):
                # Arquivos e pastas numa janela do Finder (o campo do nome), a barra lateral e o
                # caminho: não são botões, mas declaram AXOpen, que abre o item como o duplo clique.
                action = "open"
            if action and not name:
                name = self.derived_name(element, role)
            if action == "open" and not name:
                action = ""  # sem nome não dá para pedir nem conferir o que vai abrir
        visible = clip is None or macos.is_visible(element, clip)
        return (name, role, macos.element_frame(element), action, visible, element)

    @staticmethod
    def focusable(element):
        error, settable = AXUIElementIsAttributeSettable(element, "AXFocused", None)
        return error == 0 and bool(settable)

    @staticmethod
    def derived_name(element, role):
        """Nome para um controle sem título, tirado do texto que está em volta dele."""
        if role == "AXDisclosureTriangle":
            # Na lista do Finder o triângulo fica na mesma célula que o nome do arquivo.
            label = macos.text_inside(macos.parent(element), skip=element, limit=1)
            verb = "Recolher" if macos.read_attribute(element, "AXValue") else "Expandir"
            return f"{verb} {label}" if label else verb
        return short_name(macos.text_inside(element))

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

    def context_menu(self, snapshot, number, cancelled=lambda: False):
        """Abre o menu de contexto do item (como o clique direito) e devolve o menu como janela.

        O AXShowMenu fica preso enquanto o menu está aberto e volta com "não pôde completar"
        depois de 1,5 s, com o menu ainda na tela. Por isso ele roda numa thread à parte, e aqui
        só se procura o menu: o macOS o pendura num ancestral do item (no Finder, na lista).
        """
        native, action = self.validate(snapshot, number)
        if cancelled():
            raise ScreenError("Ação cancelada antes da execução.")
        target = native
        for _ in range(4):  # no Finder o menu é do campo do nome; em outros apps, de um ancestral
            if target is None or "AXShowMenu" in macos.action_names(target):
                break
            target = macos.parent(target)
        if target is None:
            raise ScreenError("Este item não oferece menu de contexto.")
        threading.Thread(target=macos.perform_action, args=(target, "AXShowMenu"),
                         name="abrir-menu", daemon=True).start()
        deadline = time.monotonic() + MENU_WAIT
        while time.monotonic() < deadline and not cancelled():
            menu = macos_shell.menu_near(target)
            if menu is not None:
                self.open_menu = (snapshot.window.pid, menu)
                window = self.menu_window()
                if window is not None:
                    return window
            time.sleep(0.05)
        raise ScreenError("O menu de contexto não abriu. Tente de novo.")

    def close_menu(self, cancelled=lambda: False):
        """Fecha o menu que o Jarvis abriu, se ele ainda estiver na tela (como apertar Esc)."""
        if self.menu_window() is not None:
            macos.press_key(macos.KEY_ESCAPE)
        self.open_menu = None

    def mission_control(self, opened, cancelled=lambda: False):
        if cancelled():
            raise ScreenError("Ação cancelada antes da execução.")
        if (macos.pid_for_bundle(macos_shell.DOCK) is None
                and macos.pid_for_bundle(macos_shell.WINDOW_MANAGER) is None):
            raise ScreenError("O Dock não está em execução; o Mission Control depende dele.")
        if not macos_shell.toggle_mission_control(opened):
            raise ScreenError("O Mission Control não " + ("abriu." if opened else "fechou.") + " Tente de novo.")
        if not opened:
            return None
        # Devolve a "janela" do Mission Control: a sessão já numera as janelas sem outro comando.
        macos_shell.wait_mission_control_settled()
        window = self.mission_control_window()
        if window is None:
            raise ScreenError("O Mission Control foi fechado.")
        return window

    # --- Navegador ------------------------------------------------------------------------------

    def browser(self, window, action, number=None, cancelled=lambda: False):
        """Abas e páginas do navegador da frente, pelos atalhos que todos os navegadores do Mac têm."""
        if cancelled():
            raise ScreenError("Ação cancelada antes da execução.")
        if (window is None or self.is_shell(window)
                or macos.bundle_id(window.pid) not in BROWSERS):
            raise ScreenError("Comandos de aba e página só funcionam com o navegador na frente.")
        current = self.foreground()
        if current is None or current.pid != window.pid or macos.keyboard_pid() != window.pid:
            raise ScreenError("O navegador saiu da frente. Clique nele e repita o comando.")
        if action == "tab":
            if number not in range(1, 9):
                raise ScreenError("Diga uma aba de 1 a 8, ou última aba.")
            macos.press_key(macos.KEY_DIGITS[number], macos.COMMAND)
            return f"Aba {number}."
        # (atalho no menu, modificadores AX, tecla para quando o menu não tem o item)
        shortcuts = {
            "new_tab": ("T", 0, (macos.KEY_T, macos.COMMAND)),
            "close_tab": ("W", 0, (macos.KEY_W, macos.COMMAND)),
            "reopen_tab": ("T", 1, (macos.KEY_T, macos.COMMAND | macos.SHIFT)),
            "reload": ("R", 0, (macos.KEY_R, macos.COMMAND)),
            "back": ("[", 0, (macos.KEY_LEFT, macos.COMMAND)),
            "forward": ("]", 0, (macos.KEY_RIGHT, macos.COMMAND)),
            "next_tab": ("\t", 12, (macos.KEY_TAB, macos.CONTROL)),
            "previous_tab": ("\t", 13, (macos.KEY_TAB, macos.CONTROL | macos.SHIFT)),
            "last_tab": (None, None, (macos.KEY_DIGITS[9], macos.COMMAND)),
        }
        if action not in shortcuts:
            raise ScreenError("Comando de navegador desconhecido.")
        char, modifiers, key = shortcuts[action]
        # Pelo menu do navegador sempre que ele tiver o item: com o navegador na frente, o menu
        # mostra o que dá para fazer agora (sem histórico, "Voltar" fica desativado).
        item = macos_shell.menu_item_with_shortcut(window.pid, char, modifiers) if char else None
        if item is not None:
            if not macos.read_attribute(item, "AXEnabled"):
                raise ScreenError(BROWSER_UNAVAILABLE.get(action, "O navegador não permite isso agora."))
            if macos.press(item) == 0:
                return BROWSER_DONE[action]
        macos.press_key(*key)
        return BROWSER_DONE[action]

    # --- Digitação no campo ativo -------------------------------------------------------------

    @staticmethod
    def editable(element):
        return (macos.element_role(element) in macos.TEXT_INPUT_ROLES
                and macos.read_attribute(element, "AXEnabled") is not False)

    def belongs_to(self, element, window):
        """O elemento está dentro desta janela?"""
        if self.is_shell(window):
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
        if window is None or window.pid == os.getpid() or self.is_shell(window):
            return  # a barra do Jarvis, a mesa ou o Mission Control na frente não apagam o último campo
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

    def select_text(self, snapshot, selection, cancelled=lambda: False):
        """Seleciona tudo, a última palavra ou um trecho falado do campo ativo, pela acessibilidade."""
        kind, phrase = selection
        element = self._focus_editable(snapshot, cancelled)
        text = macos.read_attribute(element, "AXValue")
        if not isinstance(text, str) or not text.strip():
            raise ScreenError("O campo está vazio; não há texto para selecionar.")
        current = macos.selected_range(element)
        cursor = from_utf16(text, current[0]) if current else None
        if kind == "all":
            span, message = (0, len(text)), "Todo o texto do campo foi selecionado."
        elif kind == "last_word":
            span = last_word(text, cursor)
            if span is None:
                raise ScreenError("Não há palavra antes do cursor.")
            message = f"Selecionado: “{text[span[0]:span[1]]}”."
        else:
            found = find_in_text(text, phrase, cursor)
            if found is None:
                raise ScreenError(f"Não encontrei “{phrase}” no campo.")
            span = found[:2]
            message = f"Selecionado: “{text[span[0]:span[1]]}”."
            if found[2] > 1:
                message += f" Aparece {found[2]} vezes; escolhi a mais perto antes do cursor."
        if cancelled():
            raise ScreenError("Seleção cancelada.")
        location, length = utf16_length(text[:span[0]]), utf16_length(text[span[0]:span[1]])
        if macos.select_range(element, location, length) != 0 or macos.selected_range(element) != (location, length):
            if kind != "all":
                raise ScreenError("Este campo não deixa selecionar texto pela acessibilidade.")
            macos.press_key(macos.KEY_A, macos.COMMAND)  # selecionar tudo pelo teclado, no campo validado
        return message

    def _require_selection(self, element, verb):
        # Nem todo campo informa a seleção (AXSelectedText); nesses, a área de transferência confere depois.
        if macos.read_attribute(element, "AXSelectedText") == "":
            raise ScreenError(f"Nada selecionado. Diga selecionar a palavra, o texto ou tudo antes de {verb}.")

    def copy_text(self, snapshot, number=1, cancelled=lambda: False):
        if number != 1:
            raise ScreenError("Campo ativo inválido.")
        element = self._focus_editable(snapshot, cancelled)
        self._require_selection(element, "copiar")
        before = macos.clipboard_count()
        macos.press_key(macos.KEY_C, macos.COMMAND)
        if not macos.wait_clipboard_change(before):
            raise ScreenError("O aplicativo não copiou o texto. Confira a seleção e repita.")
        return f"Copiado: “{short_name(macos.clipboard_text())}”."

    def cut_text(self, snapshot, number=1, cancelled=lambda: False):
        if number != 1:
            raise ScreenError("Campo ativo inválido.")
        element = self._focus_editable(snapshot, cancelled)
        self._require_selection(element, "recortar")
        before = macos.clipboard_count()
        macos.press_key(macos.KEY_X, macos.COMMAND)
        if not macos.wait_clipboard_change(before):
            raise ScreenError("O aplicativo não recortou o texto. Confira a seleção e repita.")
        return f"Recortado: “{short_name(macos.clipboard_text())}”. Diga colar texto onde quiser usá-lo."

    def prepare_paste(self, window):
        """Como prepare_dictation, mais o texto que seria colado, para a pergunta de confirmação."""
        snapshot = self.prepare_dictation(window)
        text = macos.clipboard_text()
        if not text.strip():
            raise ScreenError("A área de transferência não tem texto para colar. Copie algo antes.")
        self.paste_preview = text
        return snapshot, short_name(text)

    def paste_text(self, snapshot, number=1, cancelled=lambda: False):
        if number != 1:
            raise ScreenError("Campo ativo inválido.")
        if self.paste_preview is None or macos.clipboard_text() != self.paste_preview:
            raise ScreenError("A área de transferência mudou depois da pergunta. Diga colar texto de novo.")
        element = self._focus_editable(snapshot, cancelled)
        if cancelled() or not self._still_focused(snapshot, element):
            raise ScreenError("O foco mudou. Nada foi colado.")
        macos.press_key(macos.KEY_V, macos.COMMAND)
        self.paste_preview = None
        return "Texto colado no campo."

    # --- Rolagem ---------------------------------------------------------------------------

    @staticmethod
    def scroll_pattern(native):
        return AXScrollPattern(native)

    def prepare_scroll(self, window, max_depth=32, max_elements=3000, cancelled=lambda: False):
        """Áreas roláveis visíveis da janela; a que contém o foco já vem escolhida.

        Apps nativos expõem a barra de rolagem (modo "bar"). Páginas web e apps Electron não
        expõem: aí vale o evento de rolagem (modo "wheel") na área ou, sem área, na janela.
        """
        if max_depth < 0 or not 1 <= max_elements <= 10000:
            raise ScreenError("Limites de leitura inválidos.")
        if window is None or window.pid == os.getpid():
            raise ScreenError("Selecione uma janela de aplicativo antes de rolar.")
        self._check_scroll_window(window, cancelled)
        self.scroll_references = {}
        self.scroll_snapshot = None
        bars, wheels, truncated = [], [], False
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
                found = bars if self.scroll_pattern(native).VerticallyScrollable else wheels
                if len(found) >= 200:
                    truncated = True
                    break
                found.append((native, frame, clip))
            except Exception:
                truncated = True
        areas, mode = (bars, "bar") if bars else (wheels, "wheel")
        shell = self.is_shell(window)
        if not areas and not truncated and not shell and window.frame is not None:
            areas = [(self.root(window), window.frame, None)]  # Electron: nenhuma área exposta
        elements = []
        for native, frame, clip in areas:
            number = len(elements) + 1
            role = macos.element_role(native)
            name = macos.element_name(native) if role == "AXScrollArea" else ""
            elements.append(Element(number, name or f"Área rolável {number}", role, frame, "scroll"))
            self.scroll_references[number] = (native, frame, clip, name, role, mode)
        self.scroll_snapshot = Snapshot(uuid.uuid4().hex, window, tuple(elements), truncated=truncated)
        preferred = None if truncated else self._focused_area(window)
        if preferred is None and len(elements) == 1 and not truncated:
            preferred = 1
        self._check_scroll_window(window, cancelled)
        return ScrollTargets(self.scroll_snapshot, preferred)

    def scroll(self, snapshot, number, request, cancelled=lambda: False):
        reference = self.scroll_references.get(number)
        if reference is None or reference[5] != "wheel":
            return super().scroll(snapshot, number, request, cancelled)
        if request.direction not in ("up", "down") or request.intensity not in ("small", "normal", "page"):
            raise ScreenError("Comando de rolagem inválido.")
        native = self._validate_scroll_area(snapshot, number, cancelled)
        x, y, width, height = reference[1]
        point = (x + width / 2, y + height / 2)
        # O evento vai para quem estiver por cima naquele ponto, não necessariamente para o alvo.
        if macos.window_owner_at(point, ignore={os.getpid()}) != snapshot.window.pid:
            raise ScreenError("Outra janela está por cima da área. Rolagem cancelada.")
        distance = height * {"small": .15, "normal": .5, "page": .9}[request.intensity]
        sign = -1 if request.direction == "down" else 1
        steps = max(1, round(SCROLL_DURATIONS[request.intensity] / SCROLL_FRAME_SECONDS))
        sent = 0
        for step in range(1, steps + 1):
            if step > 1:
                time.sleep(SCROLL_FRAME_SECONDS)
                self._validate_scroll_area(snapshot, number, cancelled)
            progress = step / steps
            # A mesma curva da rolagem pela barra: começa e termina devagar.
            delta = round(distance * progress * progress * (3 - 2 * progress)) - sent
            if delta:
                macos.scroll_wheel(point, sign * delta)
                sent += delta
        # A página não informa a posição pela acessibilidade; só dá para dizer que foi pedido.
        return ScrollOutcome.REQUESTED

    def _focused_area(self, window):
        """O número da área rolável que contém o foco (ou o último foco lembrado nesta janela)."""
        focused = macos.focused_element(window.pid)
        if focused is None and self.last_focus and self.last_focus[0] == window.identity:
            focused = self.last_focus[1]
        for _ in range(MAX_ANCESTORS):
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
        native, frame, clip, name, role, _ = self.scroll_references[number]
        try:
            unchanged = (macos.element_role(native) == role and macos.element_frame(native) == frame
                         and (role != "AXScrollArea" or macos.element_name(native) == name)
                         and (clip is None or macos.is_visible(native, clip))
                         and self.belongs_to(native, snapshot.window))
        except Exception as exc:
            raise ScreenError("A área rolável ficou indisponível. Repita o comando.") from exc
        if not unchanged:
            raise ScreenError("A área rolável mudou. Repita o comando.")
        return native
