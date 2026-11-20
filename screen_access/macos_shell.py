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
MISSION_CONTROL_SETTLE = 1.0  # limite para as miniaturas terminarem de chegar ao lugar


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


def wait_mission_control_settled(dock_pid, clock=time.monotonic, sleep=time.sleep):
    """Espera as miniaturas pararem de se mexer antes de numerar.

    Logo que o Mission Control aparece, elas ainda estão crescendo até o lugar; números lidos nessa
    hora ficariam fora das janelas. Duas leituras iguais seguidas bastam.
    """
    deadline = clock() + MISSION_CONTROL_SETTLE
    previous = None
    while True:
        root = mission_control(dock_pid)
        if root is None:
            return  # fechou no meio do caminho; quem chamou percebe
        current = tuple(macos.element_frame(item) for item in mission_control_items(root))
        if (current and current == previous) or clock() >= deadline:
            return
        previous = current
        sleep(0.1)


def menu_item_with_shortcut(pid, char, modifiers=0):
    """O item da barra de menus de um app com esse atalho, ou None. Entre dois iguais, o ativo.

    Procura pelo atalho, não pelo nome: funciona em qualquer idioma e em qualquer layout de
    teclado (no ABNT2 o "[" fica em outra tecla). Os modificadores seguem a AX: 0 é só o ⌘,
    1 soma o Shift, 4 soma o Control e 8 tira o ⌘ (o Ctrl+Tab é 12).
    """
    if pid is None:
        return None
    bar = macos.read_attribute(macos.application_element(pid), "AXMenuBar")
    found = None
    for top in children(bar) if bar is not None else ():
        for menu in children(top):
            for item in children(menu):
                if (macos.read_attribute(item, "AXMenuItemCmdChar") == char
                        and macos.read_attribute(item, "AXMenuItemCmdModifiers") == modifiers):
                    if macos.read_attribute(item, "AXEnabled"):
                        return item
                    found = found or item
    return found


def menu_near(element, levels=15):
    """O menu (AXMenu) aberto mais perto do elemento, subindo pelos ancestrais, ou None.

    O menu de contexto não fica nos filhos do aplicativo: o macOS o pendura num ancestral do item
    clicado (no Finder, na lista de arquivos). Subindo até o app, os dois casos são cobertos.
    """
    node = element
    for _ in range(levels):
        if node is None:
            return None
        for child in children(node):
            if macos.element_role(child) == "AXMenu":
                return child
        node = macos.parent(node)
    return None


def menu_options(menu):
    """As opções que aparecem no menu: sem separadores, sem opções desativadas e sem as alternativas.

    As alternativas (as que surgem segurando Option, como "Mostrar Inspetor" no lugar de "Obter
    Informações") ficam escondidas na mesma posição da opção visível; vale a primeira de cada lugar.
    """
    last = None
    for item in children(menu):
        if (macos.element_role(item) != "AXMenuItem" or not macos.element_name(item)
                or not macos.read_attribute(item, "AXEnabled")):
            continue
        frame = macos.element_frame(item)
        if frame is not None and frame == last:
            continue
        last = frame
        yield item
