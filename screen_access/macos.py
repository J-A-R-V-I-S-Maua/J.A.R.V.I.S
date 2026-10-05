"""Acesso à árvore de acessibilidade no macOS (AX API, via pyobjc)."""

from AppKit import NSWorkspace
from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXUIElementCopyActionNames,
    AXUIElementCreateApplication,
    AXUIElementSetAttributeValue,
    kAXChildrenAttribute,
    kAXDescriptionAttribute,
    kAXErrorSuccess,
    kAXFocusedWindowAttribute,
    kAXMainWindowAttribute,
    kAXPressAction,
    kAXRoleAttribute,
    kAXTitleAttribute,
    kAXValueAttribute,
)


def read_attribute(element, name):
    """Lê um atributo de um elemento. Devolve None se ele não existir ou falhar."""
    error, value = AXUIElementCopyAttributeValue(element, name, None)
    if error != kAXErrorSuccess:
        return None
    return value


def frontmost_application():
    """Devolve (pid, nome) do aplicativo que está em primeiro plano agora."""
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return app.processIdentifier(), app.localizedName()


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
    """Nome acessível: título, senão descrição, senão valor (só se for texto)."""
    for attribute in (kAXTitleAttribute, kAXDescriptionAttribute, kAXValueAttribute):
        value = read_attribute(element, attribute)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def can_press(element):
    """Verdadeiro se o elemento declara a ação AXPress (ou seja, pode ser ativado)."""
    error, actions = AXUIElementCopyActionNames(element, None)
    return error == kAXErrorSuccess and kAXPressAction in (actions or ())


def walk(element, max_depth, depth=0):
    """Percorre a árvore em profundidade, devolvendo (profundidade, elemento)."""
    yield depth, element
    if depth >= max_depth:
        return
    for child in read_attribute(element, kAXChildrenAttribute) or ():
        yield from walk(child, max_depth, depth + 1)
