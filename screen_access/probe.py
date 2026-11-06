"""Sonda do macOS: mostra o que cada controle visível oferece de verdade.

Uso: python -m screen_access.probe --delay 5
Serve para decidir, com dados reais, como implementar cada ação nativa.
"""
import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import time

SETTABLE_OF_INTEREST = ("AXValue", "AXSelectedText", "AXFocused", "AXSelected")
ACTIONS_OF_INTEREST = frozenset({
    "AXOpen", "AXShowMenu", "AXConfirm", "AXIncrement", "AXDecrement",
    "AXCancel", "AXPick", "AXScrollToVisible",
})
# AXPress aparece em quase todo nó do Chrome; por isso a sonda lista por papel, não por ação.
LISTED_ROLES = frozenset({
    "AXButton", "AXLink", "AXRadioButton", "AXCheckBox", "AXPopUpButton", "AXMenuButton",
    "AXMenuItem", "AXTextField", "AXTextArea", "AXComboBox", "AXRow", "AXOutlineRow",
    "AXCell", "AXTable", "AXOutline", "AXList", "AXScrollArea", "AXTabGroup", "AXSlider",
})
TEXT_ROLES = frozenset({"AXTextField", "AXTextArea", "AXComboBox"})
# O Dock é outro processo e os ícones da mesa pertencem ao Finder: nenhum deles é "o app da frente".
TARGETS = {"front": None, "dock": "com.apple.dock", "desktop": "com.apple.finder"}


@dataclass(frozen=True)
class Row:
    role: str
    name: str
    size: tuple | None  # (largura, altura)
    actions: tuple
    settable: tuple
    notes: tuple = ()
    path: str = ""  # papéis dos ancestrais, só no modo de processo inteiro


def should_list(role, actions):
    return role in LISTED_ROLES or bool(ACTIONS_OF_INTEREST & set(actions))


def format_row(number, row):
    name = f" {row.name[:60]!r}" if row.name else ""
    size = f"  {row.size[0]}x{row.size[1]}" if row.size else ""
    actions = ", ".join(row.actions) or "nenhuma"
    line = f"[{number}] {row.role}{name}{size}  ações: {actions}"
    if row.settable:
        line += f"  | graváveis: {', '.join(row.settable)}"
    for note in row.notes:
        line += f"  | {note}"
    if row.path:
        line += f"  | caminho: {row.path}"
    return line


def _by_role(roles):
    counts = Counter(roles)
    return ", ".join(f"{role} {count}" for role, count in counts.most_common(4))


def summarize(rows):
    """Linhas de resumo: quem oferece cada ação e cada atributo gravável."""
    actions, settable = defaultdict(list), defaultdict(list)
    for row in rows:
        for name in row.actions:
            actions[name].append(row.role)
        for name in row.settable:
            settable[name].append(row.role)
    lines = [f"Papéis encontrados: {_by_role([row.role for row in rows]) or 'nenhum'}",
             "Ações oferecidas (quantos controles; principais papéis):"]
    for name, roles in sorted(actions.items(), key=lambda item: (-len(item[1]), item[0])):
        lines.append(f"  {name}: {len(roles)}  ({_by_role(roles)})")
    lines.append("Atributos graváveis:")
    for name in SETTABLE_OF_INTEREST:
        roles = settable.get(name, [])
        lines.append(f"  {name}: {len(roles)}" + (f"  ({_by_role(roles)})" if roles else ""))
    missing = sorted(ACTIONS_OF_INTEREST - set(actions))
    lines.append("Ações de interesse que nenhum controle desta tela ofereceu: "
                 + (", ".join(missing) if missing else "nenhuma (todas apareceram)"))
    return lines


def read_row(element, with_position=False, path=""):
    from . import macos

    role = macos.element_role(element)
    frame = macos.element_frame(element)
    notes = []
    if with_position and frame:
        notes.append(f"posição {round(frame[0])},{round(frame[1])}")
    if role == "AXScrollArea":
        bar = macos.read_attribute(element, "AXVerticalScrollBar")
        if bar is None:
            notes.append("sem barra de rolagem vertical")
        else:
            value = macos.read_attribute(bar, "AXValue")
            mode = "gravável" if macos.is_settable(bar, "AXValue") else "somente leitura"
            shown = f"{float(value):.2f}" if isinstance(value, (int, float)) else "?"
            notes.append(f"barra vertical: valor {shown}, {mode}, ações {', '.join(macos.action_names(bar)) or 'nenhuma'}")
    return Row(
        role=role,
        # Em campos de texto o "nome" pode ser o conteúdo digitado; não imprimir.
        name="" if role in TEXT_ROLES else macos.element_name(element),
        size=(round(frame[2]), round(frame[3])) if frame else None,
        actions=macos.action_names(element),
        settable=tuple(name for name in SETTABLE_OF_INTEREST if macos.is_settable(element, name)),
        notes=tuple(notes),
        path=path,
    )


def collect(window, max_depth, max_elements):
    """Percorre a janela e devolve (linhas, visitados, truncado)."""
    from . import macos

    frame = macos.element_frame(window)
    nodes = (
        ((element, None) for _, element in macos.walk(window, max_depth)) if frame is None else
        ((element, clip) for _, element, clip in macos.walk_with_clip(window, max_depth, frame))
    )
    rows, seen, visited, truncated = [], set(), 0, False
    for element, clip in nodes:
        if visited >= max_elements:
            truncated = True
            break
        visited += 1
        if element in seen or (clip is not None and not macos.is_visible(element, clip)):
            continue
        row = read_row(element)
        if should_list(row.role, row.actions):
            seen.add(element)
            rows.append(row)
    return rows, visited, truncated


def collect_tree(root, max_depth, max_elements):
    """Lê o processo inteiro a partir da raiz, guardando o caminho de cada controle.

    Usado para o Dock e a mesa, que não são "a janela em foco". Devolve (linhas, visitados, truncado).
    """
    from . import macos

    rows, visited, truncated = [], 0, False
    stack = [(root, 0, ())]
    while stack:
        element, depth, ancestors = stack.pop()
        if visited >= max_elements:
            truncated = True
            break
        visited += 1
        row = read_row(element, with_position=True, path=" > ".join(ancestors[-5:]))
        if row.name or row.actions or row.role in LISTED_ROLES or row.role == "AXDockItem":
            rows.append(row)
        if depth < max_depth:
            children = macos.read_attribute(element, "AXChildren") or ()
            for child in reversed(children):
                stack.append((child, depth + 1, ancestors + (row.role,)))
    return rows, visited, truncated


def focused_lines(pid):
    from . import macos

    element = macos.focused_element(pid)
    if element is None:
        return ["  Nenhum controle com o foco do teclado. Clique no campo desejado antes de rodar."]
    row = read_row(element)
    value = macos.read_attribute(element, "AXValue")
    if isinstance(value, str):
        detail = f"AXValue é texto com {len(value)} caracteres"
    else:
        detail = f"AXValue do tipo {type(value).__name__}"
    selection = macos.read_attribute(element, "AXSelectedTextRange") is not None
    return [
        f"  papel: {row.role}",
        f"  ações: {', '.join(row.actions) or 'nenhuma'}",
        f"  graváveis: {', '.join(row.settable) or 'nenhum'}",
        f"  {detail}; seleção de texto legível: {'sim' if selection else 'não'}",
    ]


def probe_process(target, max_depth, max_elements):
    """Lê o Dock ou o Finder pelo identificador do processo, sem depender do app da frente."""
    from . import macos

    bundle = TARGETS[target]
    pid = macos.pid_for_bundle(bundle)
    if pid is None:
        print(f"O processo {bundle} não está em execução.")
        return 1
    began = time.monotonic()
    rows, visited, truncated = collect_tree(macos.application_element(pid), max_depth, max_elements)
    seconds = time.monotonic() - began
    print(f"Alvo: {target} ({bundle}, pid {pid})")
    print(f"{visited} elementos visitados em {seconds:.1f}s; {len(rows)} listados"
          + ("; LEITURA INCOMPLETA (limite atingido)" if truncated else ""))
    print("\n== Controles (com o caminho na árvore) ==")
    for number, row in enumerate(rows, 1):
        print(format_row(number, row))
    print("\n== Resumo ==")
    print("\n".join(summarize(rows)))
    return 0


def main() -> int:
    from . import macos
    from .permissions import has_accessibility_permission

    parser = argparse.ArgumentParser(description="Mostra o que cada controle visível oferece.")
    parser.add_argument("--delay", type=int, default=5, help="segundos para trocar para o aplicativo alvo")
    parser.add_argument("--max-depth", type=int, default=32)
    parser.add_argument("--max-elements", type=int, default=3000)
    parser.add_argument(
        "--target", choices=sorted(TARGETS), default="front",
        help="front: janela em foco; dock: o Dock; desktop: o Finder (ícones da mesa e janelas dele)")
    args = parser.parse_args()

    if not has_accessibility_permission():
        print("Sem permissão de Acessibilidade. Veja Ajustes do Sistema > Privacidade e Segurança.")
        return 1
    if args.target != "front":
        return probe_process(args.target, args.max_depth, args.max_elements)
    print(f"Lendo em {args.delay}s: deixe o aplicativo alvo em primeiro plano e, para testar digitação,\n"
          "clique no campo de texto desejado (não toque no terminal até acabar).")
    time.sleep(args.delay)

    pid, app = macos.frontmost_application()
    macos.enable_web_accessibility(pid)
    window = macos.front_window(pid)
    if window is None:
        print(f"{app} não tem uma janela acessível agora.")
        return 1
    began = time.monotonic()
    rows, visited, truncated = collect(window, args.max_depth, args.max_elements)
    seconds = time.monotonic() - began
    print(f"\nAplicativo: {app} (pid {pid})  Janela: {macos.window_title(window)!r}")
    print(f"{visited} elementos visitados em {seconds:.1f}s; {len(rows)} listados"
          + ("; LEITURA INCOMPLETA (limite atingido)" if truncated else ""))
    print("\n== Controles visíveis ==")
    for number, row in enumerate(rows, 1):
        print(format_row(number, row))
    print("\n== Resumo ==")
    print("\n".join(summarize(rows)))
    print("\n== Controle com o foco do teclado ==")
    print("\n".join(focused_lines(pid)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
