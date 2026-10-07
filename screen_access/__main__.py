import argparse
import time

from . import macos, matching
from .permissions import has_accessibility_permission

NO_NAME = "(sem nome)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Lê a janela em foco pela API de acessibilidade.")
    parser.add_argument(
        "--delay", type=int, default=3,
        help="segundos de espera antes de ler, para você trocar para o aplicativo desejado",
    )
    parser.add_argument("--max-depth", type=int, default=12, help="profundidade máxima da árvore")
    parser.add_argument("--max-elements", type=int, default=200, help="quantidade máxima de elementos")
    parser.add_argument("--clickable", action="store_true", help="lista só os elementos acionáveis (AXPress)")
    parser.add_argument(
        "--include-hidden", action="store_true",
        help="com --clickable, inclui também os elementos fora da área visível",
    )
    parser.add_argument(
        "--show-frames", action="store_true",
        help="com --clickable, mostra a posição e o tamanho de cada item (diagnóstico)",
    )
    parser.add_argument(
        "--press", action="store_true",
        help="lista os itens acionáveis e aciona o que você escolher pelo número (pede confirmação)",
    )
    args = parser.parse_args()

    if not has_accessibility_permission():
        print("Sem permissão de Acessibilidade.")
        print("Abra Ajustes do Sistema > Privacidade e Segurança > Acessibilidade,")
        print("autorize o aplicativo que está rodando este comando (Terminal ou VS Code)")
        print("e execute de novo.")
        return 1

    print(f"Lendo em {args.delay}s: clique agora no aplicativo que quer inspecionar.")
    time.sleep(args.delay)

    pid, name = macos.frontmost_application()
    print(f"Aplicativo em foco: {name} (pid {pid})")

    results = macos.enable_web_accessibility(pid)
    for attribute, code in results.items():
        print(f"  {attribute}: {'aceito' if code == 0 else f'recusado (código {code})'}")
    if 0 in results.values():
        time.sleep(1)

    window = macos.front_window(pid)
    if window is None:
        print("Esse aplicativo não tem uma janela acessível agora.")
        return 1
    print(f"Janela: {macos.window_title(window)!r}")
    print()

    if args.clickable or args.press:
        items = print_clickable(window, args)
        return press_by_number(items) if args.press else 0

    count = 0
    for depth, element in macos.walk(window, args.max_depth):
        if count >= args.max_elements:
            print(f"... limite de {args.max_elements} elementos atingido")
            break
        count += 1
        name = macos.element_name(element)
        label = f" {name[:60]!r}" if name else ""
        print(f"{'  ' * depth}{macos.element_role(element)}{label}")
    print(f"\n{count} elementos listados.")
    return 0


def describe_frame(frame) -> str:
    if frame is None:
        return "sem posição"
    x, y, width, height = (round(value) for value in frame)
    return f"x={x} y={y} larg={width} alt={height}"


def print_clickable(window, args) -> list:
    """Lista numerada dos controles interativos (por papel e ação), sem repetições.

    Devolve [(elemento, papel, nome)] na mesma ordem dos números impressos.
    """
    visited = 0
    seen = set()
    items = []
    unnamed = 0
    window_frame = macos.element_frame(window)
    if args.include_hidden or window_frame is None:
        nodes = ((element, None) for _, element in macos.walk(window, args.max_depth))
        print("(incluindo elementos fora da tela)\n")
    else:
        nodes = (
            (element, clip)
            for _, element, clip in macos.walk_with_clip(window, args.max_depth, window_frame)
        )
    if args.show_frames:
        print(f"Janela: {describe_frame(window_frame)}\n")
    for element, clip in nodes:
        if visited >= args.max_elements:
            print(f"... limite de {args.max_elements} elementos visitados atingido")
            break
        visited += 1
        role = macos.element_role(element)
        if args.show_frames and role in macos.CLIP_ROLES:
            print(f"    região {role}: {describe_frame(macos.element_frame(element))}")
        if element in seen or not macos.is_interactive(element, role):
            continue
        if clip is not None and not macos.is_visible(element, clip):
            continue
        seen.add(element)
        name = macos.element_name(element)
        if not name:
            unnamed += 1
            name = NO_NAME
        items.append((element, role, name))
        where = f"  @ {describe_frame(macos.element_frame(element))}" if args.show_frames else ""
        print(f"[{len(seen)}] {role} {name[:120]!r}{where}")
    print(f"\n{visited} elementos visitados, {len(seen)} acionáveis ({unnamed} sem nome).")
    return items


def choose_item(items):
    """Pergunta qual item (número ou frase) e devolve a posição dele, ou None se não houver escolha.

    Se a frase combinar com vários itens, mostra as opções e aceita um único esclarecimento.
    """
    names = ["" if name == NO_NAME else name for _, _, name in items]
    answer = input("\nNúmero ou frase do item a acionar (Enter para sair): ").strip()
    if not answer:
        return None
    choice = matching.choose(answer, names)
    if choice.status == "none":
        print("Não encontrei nenhum item que combine com isso.")
        return None
    if choice.status == "match":
        return choice.index
    print("Vários itens combinam com isso:")
    for index in choice.candidates:
        print(f"  [{index + 1}] {items[index][1]} {items[index][2][:80]!r}")
    answer = input("Qual deles? (número, Enter para cancelar): ").strip()
    if not answer:
        return None
    if answer.isdigit() and int(answer) - 1 in choice.candidates:
        return int(answer) - 1
    print("Esse número não está entre as opções.")
    return None


def press_by_number(items) -> int:
    """Escolhe um item por número ou frase, confirma e aciona. Uma ação por execução."""
    if not items:
        print("Nenhum item para acionar.")
        return 1
    index = choose_item(items)
    if index is None:
        return 0
    element, role, name = items[index]
    if input(f"Acionar [{index + 1}] {role} {name[:80]!r}? (s/n): ").strip().lower() != "s":
        print("Cancelado.")
        return 0
    code = macos.press(element)
    if code == 0:
        print("Pedido aceito pelo aplicativo.")
        return 0
    print(f"Recusado: {macos.describe_error(code)} (código {code}).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
