import argparse
import time

from . import macos
from .permissions import has_accessibility_permission


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

    if args.clickable:
        return print_clickable(window, args)

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


def print_clickable(window, args) -> int:
    """Lista numerada só dos controles interativos (por papel e ação), sem repetições."""
    visited = 0
    seen = set()
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
            name = "(sem nome)"
        where = f"  @ {describe_frame(macos.element_frame(element))}" if args.show_frames else ""
        print(f"[{len(seen)}] {role} {name[:120]!r}{where}")
    print(f"\n{visited} elementos visitados, {len(seen)} acionáveis ({unnamed} sem nome).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
