"""Acesso à árvore de acessibilidade no macOS (AX API, via pyobjc)."""

import time

from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication, NSWorkspace
from CoreFoundation import CFEqual
from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXUIElementCopyActionNames,
    AXUIElementCreateApplication,
    AXUIElementCreateSystemWide,
    AXUIElementGetPid,
    AXUIElementIsAttributeSettable,
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
    AXValueGetValue,
    kAXChildrenAttribute,
    kAXDescriptionAttribute,
    kAXErrorSuccess,
    kAXFocusedApplicationAttribute,
    kAXFocusedUIElementAttribute,
    kAXFocusedWindowAttribute,
    kAXMainWindowAttribute,
    kAXParentAttribute,
    kAXRaiseAction,
    kAXWindowAttribute,
    kAXPositionAttribute,
    kAXPressAction,
    kAXRoleAttribute,
    kAXSizeAttribute,
    kAXTitleAttribute,
    kAXValueAttribute,
    kAXValueCGPointType,
    kAXValueCGSizeType,
)
from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventKeyboardSetUnicodeString,
    CGEventPost,
    CGEventSetFlags,
    kCGEventFlagMaskCommand,
    kCGHIDEventTap,
)


def read_attribute(element, name):
    """Lê um atributo de um elemento. Devolve None se ele não existir ou falhar."""
    error, value = AXUIElementCopyAttributeValue(element, name, None)
    if error != kAXErrorSuccess:
        return None
    return value


def frontmost_pid():
    application = read_attribute(AXUIElementCreateSystemWide(), kAXFocusedApplicationAttribute)
    if application is not None:
        error, pid = AXUIElementGetPid(application, None)
        if error == kAXErrorSuccess:
            return pid
    return NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()


def frontmost_application():
    """Devolve (pid, nome) do aplicativo que está em primeiro plano agora."""
    pid = frontmost_pid()
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    return pid, app.localizedName() if app is not None else ""


def activate_application(pid, timeout=1.0):
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is not None:
        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    deadline = time.monotonic() + timeout
    while frontmost_pid() != pid:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.03)
    return True


def focused_element(pid):
    return read_attribute(AXUIElementCreateApplication(pid), kAXFocusedUIElementAttribute)


def same_element(a, b):
    return a is not None and b is not None and bool(CFEqual(a, b))


def parent(element):
    return read_attribute(element, kAXParentAttribute)


def owner_window(element):
    return read_attribute(element, kAXWindowAttribute)


def action_names(element):
    error, actions = AXUIElementCopyActionNames(element, None)
    return tuple(actions or ()) if error == kAXErrorSuccess else ()


def perform_action(element, name):
    return AXUIElementPerformAction(element, name)


def is_settable(element, name):
    error, settable = AXUIElementIsAttributeSettable(element, name, None)
    return error == kAXErrorSuccess and bool(settable)


def set_attribute(element, name, value):
    return AXUIElementSetAttributeValue(element, name, value)


def raise_window(window):
    return AXUIElementPerformAction(window, kAXRaiseAction)


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


AX_ERRORS = {
    -25200: "falha genérica",
    -25202: "elemento inválido (a tela provavelmente mudou)",
    -25204: "o aplicativo não conseguiu completar a ação",
    -25205: "atributo não suportado",
    -25206: "o elemento não aceita essa ação",
    -25208: "não implementado pelo aplicativo",
    -25211: "API de acessibilidade desativada (falta permissão)",
}


def press(element):
    """Aciona o elemento (AXPress), sem mover o cursor. Devolve o código de erro; 0 = aceito."""
    return AXUIElementPerformAction(element, kAXPressAction)


def describe_error(code):
    return AX_ERRORS.get(code, "erro desconhecido")


ACTION_ROLES = frozenset({
    "AXLink", "AXButton", "AXRadioButton", "AXCheckBox", "AXPopUpButton", "AXMenuItem",
    "AXMenuButton", "AXDisclosureTriangle",
})
TEXT_INPUT_ROLES = frozenset({"AXTextField", "AXTextArea", "AXComboBox"})
SELECTABLE_ROLES = frozenset({"AXRow"})
SHOW_MENU_ACTION = "AXShowMenu"
OPEN_ACTION = "AXOpen"


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


def walk(element, max_depth, depth=0):
    """Percorre a árvore em profundidade, devolvendo (profundidade, elemento)."""
    yield depth, element
    if depth >= max_depth:
        return
    for child in read_attribute(element, kAXChildrenAttribute) or ():
        yield from walk(child, max_depth, depth + 1)


KEY_RETURN = 36
KEY_DELETE = 51
KEY_A = 0
COMMAND = kCGEventFlagMaskCommand


def press_key(keycode, flags=0, text=None):
    for down in (True, False):
        event = CGEventCreateKeyboardEvent(None, keycode, down)
        # Sem isso, um Shift ou Cmd segurado pelo usuário vira parte do evento.
        CGEventSetFlags(event, flags)
        if text is not None:
            CGEventKeyboardSetUnicodeString(event, len(text.encode("utf-16-le")) // 2, text)
        CGEventPost(kCGHIDEventTap, event)


def type_text(text, allowed=lambda: True):
    """Digita o texto letra por letra; para assim que allowed() devolver False."""
    for char in text:
        if not allowed():
            return False
        press_key(0, text=char)
        time.sleep(0.01)
    return True
