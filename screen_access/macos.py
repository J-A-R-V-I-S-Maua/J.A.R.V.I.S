"""Acesso à árvore de acessibilidade no macOS (AX API, via pyobjc)."""
import re
import time

from AppKit import (NSApplicationActivateIgnoringOtherApps, NSPasteboard, NSPasteboardTypeString, NSRunningApplication,
                    NSWorkspace)
from ApplicationServices import (
    AXIsProcessTrusted,
    AXUIElementCopyAttributeNames,
    AXUIElementCopyAttributeValue,
    AXUIElementCopyActionNames,
    AXUIElementCreateApplication,
    AXUIElementCreateSystemWide,
    AXUIElementGetPid,
    AXUIElementIsAttributeSettable,
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
    AXValueCreate,
    AXValueGetValue,
    kAXChildrenAttribute,
    kAXDescriptionAttribute,
    kAXErrorSuccess,
    kAXFocusedWindowAttribute,
    kAXMainWindowAttribute,
    kAXPositionAttribute,
    kAXPressAction,
    kAXRoleAttribute,
    kAXSizeAttribute,
    kAXTitleAttribute,
    kAXValueAttribute,
    kAXValueCFRangeType,
    kAXValueCGPointType,
    kAXValueCGSizeType,
)
from CoreFoundation import CFEqual
from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventCreateScrollWheelEvent2,
    CGEventKeyboardSetUnicodeString,
    CGEventPost,
    CGEventSetFlags,
    CGEventSetIntegerValueField,
    CGEventSetLocation,
    CGPoint,
    CGWindowListCopyWindowInfo,
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskControl,
    kCGEventFlagMaskShift,
    kCGHIDEventTap,
    kCGNullWindowID,
    kCGScrollWheelEventIsContinuous,
    kCGScrollEventUnitPixel,
    kCGWindowListExcludeDesktopElements,
    kCGWindowListOptionOnScreenOnly,
)


def has_accessibility_permission():
    """Diz se o programa foi autorizado a ler a árvore de acessibilidade.

    Não abre nenhuma janela do sistema: apenas consulta o estado atual.
    """
    return bool(AXIsProcessTrusted())


def read_attribute(element, name):
    """Lê um atributo de um elemento. Devolve None se ele não existir ou falhar."""
    error, value = AXUIElementCopyAttributeValue(element, name, None)
    if error != kAXErrorSuccess:
        return None
    return value


def frontmost_application():
    """Devolve (pid, nome) do aplicativo que está em primeiro plano agora.

    O pid vem da acessibilidade (keyboard_pid): o NSWorkspace só atualiza a resposta num processo
    que trata eventos do macOS, e fica preso no app inicial em scripts e no `python -m screen_access`.
    """
    pid = keyboard_pid()
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    return pid, app.localizedName() if app is not None else ""


def bundle_id(pid):
    """Identificador do aplicativo de um processo (ex.: com.google.Chrome), ou None."""
    application = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    return application.bundleIdentifier() if application is not None else None


def pid_for_bundle(bundle_id):
    """pid do processo de um aplicativo pelo identificador (ex.: com.apple.dock), ou None."""
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
    return apps[0].processIdentifier() if apps else None


def application_element(pid):
    """Elemento que representa o aplicativo inteiro (a raiz da árvore dele)."""
    return AXUIElementCreateApplication(pid)


WEB_ACCESSIBILITY_ATTRIBUTES = ("AXManualAccessibility", "AXEnhancedUserInterface")


def enable_web_accessibility(pid):
    """Pede ao app que exponha o conteúdo da página (Chrome e outros baseados em Chromium).

    Devolve {atributo: código de erro}; 0 significa que o app aceitou o pedido.
    """
    application = AXUIElementCreateApplication(pid)
    return {
        name: AXUIElementSetAttributeValue(application, name, True)
        for name in WEB_ACCESSIBILITY_ATTRIBUTES
    }


def front_window(pid):
    """Devolve o elemento da janela principal do aplicativo, ou None se não houver."""
    application = AXUIElementCreateApplication(pid)
    return (
        read_attribute(application, kAXFocusedWindowAttribute)
        or read_attribute(application, kAXMainWindowAttribute)
    )


def window_title(window):
    return read_attribute(window, kAXTitleAttribute) or ""


def element_role(element):
    return read_attribute(element, kAXRoleAttribute) or ""


def element_name(element):
    """Nome acessível: título, descrição, valor, texto-guia ou ajuda (o primeiro que for texto)."""
    for attribute in (
        kAXTitleAttribute, kAXDescriptionAttribute, kAXValueAttribute,
        "AXPlaceholderValue", "AXHelp",
    ):
        value = read_attribute(element, attribute)
        if isinstance(value, str) and value.strip():
            return value.strip()
    label = read_attribute(element, "AXTitleUIElement")
    if label is not None:
        for attribute in (kAXTitleAttribute, kAXValueAttribute):
            value = read_attribute(label, attribute)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def can_press(element):
    """Verdadeiro se o elemento declara a ação AXPress (ou seja, pode ser ativado)."""
    error, actions = AXUIElementCopyActionNames(element, None)
    return error == kAXErrorSuccess and kAXPressAction in (actions or ())


def action_names(element):
    """Nomes das ações que o elemento declara (AXPress, AXOpen, AXShowMenu...)."""
    error, actions = AXUIElementCopyActionNames(element, None)
    return tuple(actions or ()) if error == kAXErrorSuccess else ()


def is_settable(element, name):
    """Verdadeiro se o atributo existe e o aplicativo aceita que ele seja alterado."""
    error, settable = AXUIElementIsAttributeSettable(element, name, None)
    return error == kAXErrorSuccess and bool(settable)


def focused_element(pid):
    """O controle que tem o foco do teclado dentro do aplicativo, ou None."""
    return read_attribute(AXUIElementCreateApplication(pid), "AXFocusedUIElement")


def same_element(a, b):
    """Dois elementos lidos em momentos diferentes são o mesmo controle?"""
    return a is not None and b is not None and bool(CFEqual(a, b))


def parent(element):
    return read_attribute(element, "AXParent")


def set_attribute(element, name, value):
    """Altera um atributo. Devolve o código de erro; 0 = aceito."""
    return AXUIElementSetAttributeValue(element, name, value)


def keyboard_pid():
    """pid do aplicativo que recebe o teclado agora: é para ele que as teclas simuladas vão."""
    application = read_attribute(AXUIElementCreateSystemWide(), "AXFocusedApplication")
    if application is not None:
        error, pid = AXUIElementGetPid(application, None)
        if error == kAXErrorSuccess:
            return pid
    return NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()


def activate_application(pid, timeout=1.0):
    """Traz o aplicativo para a frente e espera o teclado ir para ele. False se o macOS não atender."""
    application = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if application is None:
        return False
    application.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    deadline = time.monotonic() + timeout
    while keyboard_pid() != pid:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.03)
    return True


def press_key(keycode, flags=0, text=None):
    """Aperta e solta uma tecla. Com `text`, a tecla carrega esse caractere em vez do seu próprio."""
    for down in (True, False):
        event = CGEventCreateKeyboardEvent(None, keycode, down)
        # Sem isto, um Shift ou Cmd que a pessoa esteja segurando entraria na tecla simulada.
        CGEventSetFlags(event, flags)
        if text is not None:
            CGEventKeyboardSetUnicodeString(event, len(text.encode("utf-16-le")) // 2, text)
        CGEventPost(kCGHIDEventTap, event)


# Códigos de tecla são posições físicas do teclado. Return e Delete não mudam com o layout;
# a posição 0 é a letra A nos layouts QWERTY (ABNT2 e americano), mas não em um AZERTY.
KEY_A = 0
KEY_X = 7
KEY_C = 8
KEY_V = 9
KEY_RETURN = 36
KEY_DELETE = 51  # apagar para trás (Backspace)
KEY_T, KEY_W, KEY_R = 17, 13, 15
KEY_TAB = 48
KEY_ESCAPE = 53
KEY_LEFT, KEY_RIGHT = 123, 124
KEY_DIGITS = {1: 18, 2: 19, 3: 20, 4: 21, 5: 23, 6: 22, 7: 26, 8: 28, 9: 25}  # a fileira de cima
COMMAND = kCGEventFlagMaskCommand
SHIFT = kCGEventFlagMaskShift
CONTROL = kCGEventFlagMaskControl

KEY_PAUSE = 0.01  # dá tempo ao aplicativo de processar cada letra na ordem


def type_text(text, allowed=lambda: True):
    """Digita o texto letra por letra, onde o cursor estiver. Para (e devolve False) se allowed() negar.

    A letra vai dentro do evento, então acentos e símbolos não dependem do layout do teclado.
    """
    for char in text:
        if not allowed():
            return False
        press_key(0, text=char)
        time.sleep(KEY_PAUSE)
    return True


def clipboard_count():
    """Contador da área de transferência: muda a cada cópia, de qualquer aplicativo."""
    return NSPasteboard.generalPasteboard().changeCount()


def clipboard_text():
    text = NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
    return str(text) if text is not None else ""


def wait_clipboard_change(before, timeout=1.0):
    """Espera o aplicativo processar o Cmd+C (ou Cmd+X). Devolve False se a cópia não aconteceu."""
    deadline = time.monotonic() + timeout
    while clipboard_count() == before:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def scroll_wheel(point, pixels):
    """Rolagem como a de dois dedos no trackpad, no ponto dado (coordenadas da AX), sem mover o cursor.

    Negativo desce a página. Verificado num Mac real no Chrome, que não expõe barra de rolagem.
    """
    event = CGEventCreateScrollWheelEvent2(None, kCGScrollEventUnitPixel, 1, int(pixels), 0, 0)
    # Contínuo, como o do trackpad: sem isto o Chrome anima cada evento por conta própria e,
    # com um evento por quadro, a página anda aos trancos.
    CGEventSetIntegerValueField(event, kCGScrollWheelEventIsContinuous, 1)
    CGEventSetLocation(event, CGPoint(*point))
    CGEventPost(kCGHIDEventTap, event)


def window_owner_at(point, ignore=()):
    """Pid do dono da janela de aplicativo mais à frente no ponto, pulando os pids de `ignore`.

    É ela que recebe uma rolagem feita ali. Só conta a camada 0, a das janelas de aplicativo:
    painéis acima dela (o do Canopy, no notch, ocupa o topo da tela inteira) em geral deixam a
    rolagem passar, e a lista de janelas não diz quais deixam. Não precisa de gravação de tela.
    """
    windows = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
                                         kCGNullWindowID) or ()
    for info in windows:  # da frente para trás
        pid = info.get("kCGWindowOwnerPID")
        bounds = info.get("kCGWindowBounds") or {}
        if pid in ignore or info.get("kCGWindowLayer", 0) != 0 or not info.get("kCGWindowAlpha", 1):
            continue
        x, y = bounds.get("X", 0), bounds.get("Y", 0)
        if x <= point[0] < x + bounds.get("Width", 0) and y <= point[1] < y + bounds.get("Height", 0):
            return pid
    return None


AX_ERRORS = {
    -25200: "falha genérica",
    -25202: "elemento inválido (a tela provavelmente mudou)",
    -25204: "o aplicativo não conseguiu completar a ação",
    -25205: "atributo não suportado",
    -25206: "o elemento não aceita essa ação",
    -25208: "não implementado pelo aplicativo",
    -25211: "API de acessibilidade desativada (falta permissão)",
}


def perform_action(element, name):
    """Executa uma ação nativa pelo nome (AXOpen, AXShowMenu...). Devolve o código de erro; 0 = aceito."""
    return AXUIElementPerformAction(element, name)


def press(element):
    """Aciona o elemento (AXPress), sem mover o cursor. Devolve o código de erro; 0 = aceito."""
    return perform_action(element, kAXPressAction)


def describe_error(code):
    return AX_ERRORS.get(code, "erro desconhecido")


ACTION_ROLES = frozenset({
    "AXLink", "AXButton", "AXRadioButton", "AXCheckBox", "AXPopUpButton", "AXMenuItem",
    # "Agrupar"/"Ação" do Finder, o triângulo que expande uma pasta na lista, e as linhas
    # clicáveis das listas web (playlists e músicas do Spotify). Linhas do Finder não têm AXPress.
    "AXMenuButton", "AXDisclosureTriangle", "AXRow",
})
TEXT_INPUT_ROLES = frozenset({"AXTextField", "AXTextArea", "AXComboBox"})


def is_interactive(element, role):
    """O Chrome declara AXPress em quase todo nó, então o papel também precisa servir."""
    if role in TEXT_INPUT_ROLES:
        return True
    return role in ACTION_ROLES and can_press(element)


def element_frame(element):
    """Retângulo do elemento na tela como (x, y, largura, altura), ou None se indisponível."""
    position = read_attribute(element, kAXPositionAttribute)
    size = read_attribute(element, kAXSizeAttribute)
    if position is None or size is None:
        return None
    position_ok, point = AXValueGetValue(position, kAXValueCGPointType, None)
    size_ok, dimensions = AXValueGetValue(size, kAXValueCGSizeType, None)
    if not (position_ok and size_ok):
        return None
    return (point.x, point.y, dimensions.width, dimensions.height)


def selected_range(element):
    """(início, tamanho) da seleção do campo, em unidades UTF-16; o cursor é uma seleção vazia."""
    value = read_attribute(element, "AXSelectedTextRange")
    if value is None:
        return None
    ok, selection = AXValueGetValue(value, kAXValueCFRangeType, None)
    return tuple(selection) if ok else None


def select_range(element, location, length):
    """Seleciona o intervalo (em unidades UTF-16) do campo. Devolve o código de erro; 0 = aceito."""
    return set_attribute(element, "AXSelectedTextRange", AXValueCreate(kAXValueCFRangeType, (location, length)))


def intersect(a, b):
    """Interseção entre dois retângulos (x, y, l, a), ou None se não se tocam."""
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    if right <= left or bottom <= top:
        return None
    return (left, top, right - left, bottom - top)


# Áreas que cortam o conteúdo: o que rolou para fora delas existe na árvore, mas não aparece.
CLIP_ROLES = frozenset({"AXScrollArea", "AXWebArea"})

# O Chrome espreme itens rolados para fora na borda da área visível, com altura 0; links
# escondidos de acessibilidade têm 1 px. Nenhum controle utilizável é menor que isso.
MIN_VISIBLE_SIZE = 4


def walk_with_clip(element, max_depth, clip, depth=0):
    """Como walk, mas também devolve a área visível em vigor para cada elemento."""
    yield depth, element, clip
    if depth >= max_depth:
        return
    frame = element_frame(element)
    if frame is not None and element_role(element) in CLIP_ROLES:
        clip = intersect(frame, clip) or clip
    for child in read_attribute(element, kAXChildrenAttribute) or ():
        yield from walk_with_clip(child, max_depth, clip, depth + 1)


def is_visible(element, clip):
    """Tem tamanho real e toca a área visível. Sem posição informada, assume visível."""
    frame = element_frame(element)
    if frame is None:
        return True
    if frame[2] < MIN_VISIBLE_SIZE or frame[3] < MIN_VISIBLE_SIZE:
        return False
    return intersect(frame, clip) is not None


TEXT_ROLES = frozenset({"AXStaticText", "AXTextField", "AXHeading"})
NUMBERS_ONLY = re.compile(r"^[\d\s:.,/-]+$")  # posição na lista, duração, data: não identificam o item


def text_inside(element, skip=None, limit=3, max_nodes=60):
    """Os primeiros textos dentro de um elemento, para dar nome a uma linha ou célula sem título.

    Ex.: uma música do Spotify vira "Título – Artista – Álbum"; números soltos ficam de fora.
    """
    found = []
    for count, (depth, node) in enumerate(walk(element, 6)):
        if count >= max_nodes or len(found) >= limit:
            break
        if node is skip or (skip is not None and same_element(node, skip)):
            continue
        if element_role(node) in TEXT_ROLES:
            value = read_attribute(node, kAXValueAttribute)
            text = value.strip() if isinstance(value, str) and value.strip() else element_name(node)
            if text and not NUMBERS_ONLY.match(text) and text not in found:
                found.append(text)
    return " – ".join(found)


def is_chromium_window(window):
    """Janela feita com Chromium (Chrome, Edge, Spotify, apps Electron)? A página vem dele."""
    for child in read_attribute(window, kAXChildrenAttribute) or ():
        error, names = AXUIElementCopyAttributeNames(child, None)
        if error == kAXErrorSuccess and "ChromeAXNodeId" in (names or ()):
            return True
    return False


def web_content_ready(window, max_nodes=400):
    """A página (AXWebArea) já tem conteúdo? Uma árvore grande também conta como pronta."""
    for count, (depth, element) in enumerate(walk(window, 40)):
        if count >= max_nodes:
            return True
        if element_role(element) == "AXWebArea" and read_attribute(element, kAXChildrenAttribute):
            return True
    return False


def wait_for_web_content(window, timeout=3.0):
    """Espera o Chromium montar a árvore da página antes da leitura.

    Ele só monta a árvore depois do pedido de acessibilidade e a desmonta quando ninguém a lê
    por um tempo. No Spotify, medido: de 1 a 1,6 s. Sem esperar, a leitura sai quase vazia
    (3 botões em vez de mais de 100 itens). Janelas que não são do Chromium passam direto.
    """
    if not is_chromium_window(window):
        return True
    deadline = time.monotonic() + timeout
    while not web_content_ready(window):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.15)
    return True


def walk(element, max_depth, depth=0):
    """Percorre a árvore em profundidade, devolvendo (profundidade, elemento)."""
    yield depth, element
    if depth >= max_depth:
        return
    for child in read_attribute(element, kAXChildrenAttribute) or ():
        yield from walk(child, max_depth, depth + 1)
