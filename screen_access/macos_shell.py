"""Mesa (ícones do Finder) e Dock: controles que não pertencem a nenhuma janela de aplicativo."""

from . import macos

FINDER = "com.apple.finder"
DOCK = "com.apple.dock"
DOCK_ITEM = "AXDockItem"
OPEN_ACTION = "AXOpen"
# O Finder abre o item e mesmo assim responde -25205 (atributo não suportado). Verificado num Mac
# real com um PDF na mesa; só vale para AXOpen, nenhuma outra ação nem outro código.
OPEN_ACCEPTED_ERRORS = frozenset({-25205})
# Papéis cuja ação só se descobre perguntando ao próprio elemento.
SHELL_ROLES = frozenset({DOCK_ITEM, "AXImage"})


def children(element):
    return macos.read_attribute(element, "AXChildren") or ()


def is_finder(pid):
    return pid is not None and pid == macos.pid_for_bundle(FINDER)


def desktop_roots(finder_pid):
    """Áreas da mesa: rolagens penduradas direto no Finder, fora de qualquer janela.

    A estrutura identifica a mesa; o nome dela ("desktop") muda com o idioma do sistema.
    """
    application = macos.application_element(finder_pid)
    return [child for child in children(application) if macos.element_role(child) == "AXScrollArea"]


def desktop_icons(root):
    """Ícones da mesa. Não desce neles: o ícone de um PDF traz botões de página dentro."""
    for group in children(root):
        yield from children(group)


def dock_items(dock_pid):
    """Itens do Dock. Os separadores não têm nome e são descartados na leitura."""
    application = macos.application_element(dock_pid)
    for dock_list in children(application):
        if macos.element_role(dock_list) == "AXList":
            yield from (item for item in children(dock_list) if macos.element_role(item) == DOCK_ITEM)
