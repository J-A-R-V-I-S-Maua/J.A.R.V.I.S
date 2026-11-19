"""Mesa (ícones do Finder), Dock e Mission Control: controles fora de qualquer janela de aplicativo."""
import subprocess
import time

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

# O app "Mission Control" só alterna a visão; quem desenha as janelas e as mesas é o Dock.
MISSION_CONTROL_APP = "com.apple.exposelauncher"
MISSION_CONTROL_ITEM = "AXButton"
MISSION_CONTROL_WAIT = 2.0  # a animação de entrada e saída leva menos de meio segundo


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


def _child(element, identifier):
    return next((child for child in children(element)
                 if macos.read_attribute(child, "AXIdentifier") == identifier), None)


def mission_control(dock_pid):
    """O grupo do Mission Control ("mc"), que só existe enquanto ele está aberto."""
    if dock_pid is None:
        return None
    return _child(macos.application_element(dock_pid), "mc")


def mission_control_items(root):
    """Miniaturas das janelas e, depois, as mesas da barra do Spaces, monitor por monitor.

    Os identificadores ("mc.windows", "mc.spaces.list") não mudam com o idioma; os títulos, sim.
    O botão de adicionar mesa fica de fora: não leva a nenhuma janela.
    """
    for display in children(root):
        windows = _child(display, "mc.windows")
        if windows is not None:
            yield from (item for item in children(windows) if macos.element_role(item) == MISSION_CONTROL_ITEM)
        spaces = _child(display, "mc.spaces")
        listing = _child(spaces, "mc.spaces.list") if spaces is not None else None
        if listing is not None:
            yield from (item for item in children(listing) if macos.element_role(item) == MISSION_CONTROL_ITEM)


def toggle_mission_control(opened, dock_pid, runner=None, clock=time.monotonic, sleep=time.sleep):
    """Abre (ou fecha) o Mission Control e espera a mudança aparecer. Devolve False se não apareceu."""
    if (mission_control(dock_pid) is not None) == opened:
        return True
    (runner or subprocess.run)(["/usr/bin/open", "-b", MISSION_CONTROL_APP], capture_output=True,
                               stdin=subprocess.DEVNULL, timeout=10, check=True)
    deadline = clock() + MISSION_CONTROL_WAIT
    while (mission_control(dock_pid) is not None) != opened:
        if clock() >= deadline:
            return False
        sleep(0.05)
    return True
