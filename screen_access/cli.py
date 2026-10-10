"""Inspeção de janela e uma ação confirmada pelo terminal, em Windows/macOS."""
import argparse
import queue
import time
from .commands import parse_number
from .executor import ScreenExecutor
from .matching import choose


def describe_frame(frame):
    if frame is None:
        return "sem posição"
    x, y, width, height = (round(value) for value in frame)
    return f"x={x} y={y} larg={width} alt={height}"


def choose_item(elements):
    answer = input("\nNúmero ou frase do item a acionar (Enter para sair): ").strip()
    if not answer:
        return None
    number = parse_number(answer)
    if number is not None:
        return number if number <= len(elements) else None
    choice = choose(answer, [element.name for element in elements])
    if choice.status == "match":
        return choice.index+1
    if choice.status == "ambiguous":
        for index in choice.candidates:
            print(f"[{index+1}] {elements[index].name or '(sem nome)'}")
        number = parse_number(input("Qual deles? (número, Enter para cancelar): "))
        return number if number is not None and number-1 in choice.candidates else None
    print("Não encontrei nenhum item que combine com isso.")
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lê a janela em foco pela API de acessibilidade.")
    parser.add_argument("--delay", type=float, default=3)
    parser.add_argument("--max-depth", type=int, default=32)
    parser.add_argument("--max-elements", type=int, default=3000)
    parser.add_argument("--clickable", action="store_true")
    parser.add_argument("--include-hidden", action="store_true")
    parser.add_argument("--show-frames", action="store_true")
    parser.add_argument("--press", action="store_true", help="Escolher um item e confirmar uma ação")
    args = parser.parse_args(argv)
    if args.delay < 0 or args.max_depth < 0 or args.max_elements < 1:
        parser.error("delay e max-depth devem ser >= 0; max-elements deve ser positivo")
    print(f"Lendo em {args.delay:g}s: selecione o aplicativo que quer inspecionar.")
    time.sleep(args.delay)
    replies = queue.Queue()
    executor = ScreenExecutor(lambda token, kind, data: replies.put((token, kind, data)), require_foreground=False)

    def response(expected):
        deadline = time.monotonic()+15
        while True:
            token, kind, data = replies.get(timeout=max(0.01, deadline-time.monotonic()))
            if kind in ("error", "unavailable"):
                raise RuntimeError(data)
            if token == expected:
                return data

    executor.start()
    try:
        executor.submit(1, "scan", None, args.max_depth, args.max_elements, args.include_hidden)
        snapshot = response(1)
        print(f"Janela: {snapshot.window.title!r} (pid {snapshot.window.pid})")
        if args.show_frames:
            print(f"Janela: {describe_frame(snapshot.window.frame)}")
        if args.clickable or args.press:
            for element in snapshot.elements:
                where = f" @ {describe_frame(element.frame)}" if args.show_frames else ""
                print(f"[{element.number}] {element.role} {element.name or '(sem nome)'!r}{where}")
            print(f"{len(snapshot.nodes)} elementos visitados; {len(snapshot.elements)} acionáveis.")
        else:
            for node in snapshot.nodes:
                print(f"{'  '*node.depth}{node.role} {node.name[:60]!r}")
        if snapshot.truncated:
            print("Lista parcial: limite de leitura atingido. Ajuste --max-depth/--max-elements.")
        if args.press:
            number = choose_item(snapshot.elements)
            if number is not None:
                element = snapshot.elements[number-1]
                if input(f"Acionar [{number}] {element.name!r}? (s/n): ").strip().lower() == "s":
                    executor.submit(2, "activate", snapshot, number)
                    response(2)
                    print("Pedido aceito pelo aplicativo.")
                else:
                    print("Cancelado.")
        return 0
    except (RuntimeError, queue.Empty) as exc:
        print(str(exc) or "O acesso à interface demorou demais.")
        return 1
    finally:
        executor.stop()
        executor.thread.join(timeout=2)
