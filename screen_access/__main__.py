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


def print_clickable(window, args) -> int:
    """Lista numerada só dos elementos que aceitam AXPress, sem repetições."""
    visited = 0
    seen = set()
    unnamed = 0
    for _, element in macos.walk(window, args.max_depth):
        if visited >= args.max_elements:
            print(f"... limite de {args.max_elements} elementos visitados atingido")
            break
        visited += 1
        if element in seen or not macos.can_press(element):
            continue
        seen.add(element)
        name = macos.element_name(element)
        if not name:
            unnamed += 1
            name = "(sem nome)"
        print(f"[{len(seen)}] {macos.element_role(element)} {name[:90]!r}")
    print(f"\n{visited} elementos visitados, {len(seen)} acionáveis ({unnamed} sem nome).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
