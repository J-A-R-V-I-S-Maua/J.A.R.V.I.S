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

# O app "Mission Control" só alterna a visão. Quem desenha as janelas e as mesas mudou com o
# macOS: nas versões antigas é o Dock; no Tahoe, o WindowManager. O código procura nos dois.
MISSION_CONTROL_APP = "com.apple.exposelauncher"
WINDOW_MANAGER = "com.apple.WindowManager"
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


def mission_control(dock_pid=None):
    """O Mission Control aberto como (pid de quem o desenha, telas), ou None se estiver fechado.

    Há uma "mc.display" por monitor. Nas versões antigas do macOS elas ficam no Dock, dentro de
    um grupo "mc"; no Tahoe ficam direto no app WindowManager. Os identificadores não mudam com
    o idioma. Medido nos dois sistemas.
    """
    dock = dock_pid if dock_pid is not None else macos.pid_for_bundle(DOCK)
    if dock is not None:
        root = _child(macos.application_element(dock), "mc")
        if root is not None:
            return dock, tuple(children(root))
    manager = macos.pid_for_bundle(WINDOW_MANAGER)
    if manager is not None:
        displays = tuple(child for child in children(macos.application_element(manager))
                         if macos.read_attribute(child, "AXIdentifier") == "mc.display")
        if displays:
            return manager, displays
    return None


def mission_control_frame(displays):
    """O retângulo que cobre todas as telas do Mission Control (um ou mais monitores)."""
    frames = [frame for frame in (macos.element_frame(display) for display in displays) if frame]
    if not frames:
        return None
    left, top = min(f[0] for f in frames), min(f[1] for f in frames)
    right, bottom = max(f[0] + f[2] for f in frames), max(f[1] + f[3] for f in frames)
    return (left, top, right - left, bottom - top)


def mission_control_items(displays):
    """Miniaturas das janelas e, depois, as mesas da barra do Spaces, monitor por monitor.

    Nas versões antigas as janelas ficam num grupo "mc.windows"; no Tahoe, direto na tela.
    O botão de adicionar mesa fica de fora: não leva a nenhuma janela. No Tahoe também vem uma
    miniatura para a janela de um app em tela cheia noutra mesa, que não aparece (no Mac real: uma
    caixa enorme por cima das outras janelas); ela tem o número da janela ("wid") e não está na tela.
    """
    onscreen = None
    for display in displays:
        windows = _child(display, "mc.windows")
        for item in children(windows if windows is not None else display):
            if macos.element_role(item) != MISSION_CONTROL_ITEM:
                continue
            number = macos.read_attribute(item, "wid")
            if number is not None:
                if onscreen is None:
                    onscreen = macos.onscreen_window_numbers()
                if number not in onscreen:
                    continue
            yield item
        spaces = _child(display, "mc.spaces")
        listing = _child(spaces, "mc.spaces.list") if spaces is not None else None
        if listing is not None:
            yield from (item for item in children(listing) if macos.element_role(item) == MISSION_CONTROL_ITEM)


def toggle_mission_control(opened, dock_pid=None, runner=None, clock=time.monotonic, sleep=time.sleep):
    """Abre (ou fecha) o Mission Control e espera a mudança aparecer. Devolve False se não apareceu."""
    if (mission_control(dock_pid) is not None) == opened:
        return True
    # Um pedido feito durante a animação de abrir ou fechar é ignorado pelo macOS ("fechar janelas"
    # e logo "mostrar janelas"); então, se nada mudou no prazo, pede mais uma vez.
    for attempt in range(2):
        (runner or subprocess.run)(["/usr/bin/open", "-b", MISSION_CONTROL_APP], capture_output=True,
                                   stdin=subprocess.DEVNULL, timeout=10, check=True)
        deadline = clock() + MISSION_CONTROL_WAIT
        while (mission_control(dock_pid) is not None) != opened:
            if clock() >= deadline:
                break
            sleep(0.05)
        else:
            return True
    return False


def wait_mission_control_settled(dock_pid=None, clock=time.monotonic, sleep=time.sleep):
    """Espera as miniaturas pararem de se mexer antes de numerar.

    Logo que o Mission Control aparece, elas ainda estão crescendo até o lugar; números lidos nessa
    hora ficariam fora das janelas. Duas leituras iguais seguidas bastam.
    """
    deadline = clock() + MISSION_CONTROL_SETTLE
    previous = None
    while True:
        opened = mission_control(dock_pid)
        if opened is None:
            return  # fechou no meio do caminho; quem chamou percebe
        current = tuple(macos.element_frame(item) for item in mission_control_items(opened[1]))
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
