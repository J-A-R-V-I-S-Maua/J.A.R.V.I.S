"""Overlay de números para Wayland (wlr-layer-shell): não rouba foco e deixa os cliques passarem.

Roda em processo separado (GTK3 + gtk-layer-shell), então o restante do app pode usar
qualquer toolkit. Uso a partir do orquestrador:

    overlay = NumberOverlay(snapshot.elements)   # mostra
    ...
    overlay.close()                              # esconde

Dependências do sistema (Pop!_OS/Ubuntu):
    sudo apt install libgtk-layer-shell0 gir1.2-gtklayershell-0.1 gir1.2-gtk-3.0

Teste rápido, sem AT-SPI (números de exemplo por 10 s):
    python3 -m screen_access.overlay
"""
import os
import sys

if __name__ == "__main__":
    # Rodado por caminho, o Python põe a pasta do script no sys.path, e o
    # screen_access/typing.py do projeto esconderia o módulo typing da stdlib.
    _here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _here]

import atexit
import ctypes.util
import json
import signal
import subprocess
import threading
import time

CSS = b"""
#jarvis-overlay { background: transparent; }
label.badge {
    background: #ffd400; color: #000; font-weight: bold; font-size: 13px;
    padding: 0 5px; border-radius: 5px; border: 1px solid #000;
}
"""


class NumberOverlay:
    """Processo-filho com a superfície de overlay. Fechar = encerrar o processo."""

    def __init__(self, elements, timeout=60):
        payload = [{"n": e.number, "x": e.frame[0], "y": e.frame[1]}
                   for e in elements if e.frame and e.frame[2] > 0 and e.frame[3] > 0
                   and e.frame[0] >= 0 and e.frame[1] >= 0]
        env = dict(os.environ, GDK_BACKEND="wayland", PYTHONSAFEPATH="1")  # Não pôr a pasta do pacote no sys.path.
        # gtk-layer-shell precisa ser carregado antes do libwayland-client; em Python
        # isso exige LD_PRELOAD (documentado pela própria biblioteca).
        library = ctypes.util.find_library("gtk-layer-shell")
        if library:
            env["LD_PRELOAD"] = library + (":" + env["LD_PRELOAD"] if env.get("LD_PRELOAD") else "")
        self.proc = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--child", str(timeout)],
            stdin=subprocess.PIPE, env=env)
        self.proc.stdin.write(json.dumps(payload).encode())
        self.proc.stdin.close()

    def alive(self):
        return self.proc.poll() is None

    @property
    def unsupported(self):
        """O filho sai com código 3 quando o compositor não tem layer-shell."""
        return self.proc.poll() == 3

    def close(self):
        # Sem bloqueio: pode ser chamado na thread da UI. O filho é encerrado e
        # recolhido em segundo plano (e, se o pai morrer, ele sai sozinho).
        if self.alive():
            self.proc.terminate()
            threading.Thread(target=self._reap, daemon=True).start()

    def _reap(self):
        try:
            self.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class OverlayController:
    """Liga o overlay ao ScreenSession: mostra quando há marcadores, esconde quando não há.

        controller = OverlayController()
        def publish_with_overlay(view):
            controller.update(view.markers)
            publish(view)                      # o publish original da UI
        session = ScreenSession(executor, publish_with_overlay)

    update() devolve True se o overlay está na tela, para a UI cair no desenho próprio
    (ou só na lista de texto) quando o compositor não oferece layer-shell.
    """

    def __init__(self, timeout=120):
        self.timeout = timeout
        self.overlay = None
        self.signature = None
        self.unsupported = False
        self.lock = threading.Lock()
        atexit.register(self.close)

    def update(self, markers):
        with self.lock:
            if self.overlay is not None and self.overlay.unsupported:
                self.unsupported = True
            if not markers or self.unsupported:
                self._close()
                return False
            signature = tuple((m.number, m.frame) for m in markers)
            if signature == self.signature and self.overlay is not None and self.overlay.alive():
                return True  # Mesma lista republicada (retry/help): sem piscar.
            self._close()
            self.overlay = NumberOverlay(markers, self.timeout)
            self.signature = signature
            return True

    def close(self):
        with self.lock:
            self._close()

    def _close(self):
        if self.overlay is not None:
            self.overlay.close()
        self.overlay = None
        self.signature = None


def run_child(timeout):
    payload = json.load(sys.stdin)

    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GLib, Gtk
    from gi.repository import GtkLayerShell as Shell
    import cairo

    if not Shell.is_supported():
        sys.exit(3)

    screen = Gtk.Window().get_screen()
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def make_badge(number, x, y):
        # Uma superfície minúscula por selo, posicionada por margem. Não há superfície
        # de tela cheia: fora dos selos os cliques chegam direto ao aplicativo.
        window = Gtk.Window()
        window.set_name("jarvis-overlay")
        window.set_app_paintable(True)
        if screen.get_rgba_visual():
            window.set_visual(screen.get_rgba_visual())  # Fundo transparente.
        label = Gtk.Label(label=str(number))
        label.get_style_context().add_class("badge")
        window.add(label)

        Shell.init_for_window(window)
        Shell.set_namespace(window, "jarvis-overlay")
        Shell.set_layer(window, Shell.Layer.OVERLAY)
        Shell.set_exclusive_zone(window, -1)  # Ignora painéis ao calcular a posição.
        Shell.set_anchor(window, Shell.Edge.TOP, True)
        Shell.set_anchor(window, Shell.Edge.LEFT, True)
        Shell.set_margin(window, Shell.Edge.TOP, int(y))
        Shell.set_margin(window, Shell.Edge.LEFT, int(x))
        # Nunca pede foco de teclado (o nome da chamada mudou entre versões da biblioteca).
        if hasattr(Shell, "set_keyboard_mode"):
            Shell.set_keyboard_mode(window, Shell.KeyboardMode.NONE)
        else:
            Shell.set_keyboard_interactivity(window, False)
        # Reforço: se o compositor respeitar, o próprio selo também deixa o clique passar.
        window.connect("realize", lambda w: w.get_window().input_shape_combine_region(cairo.Region(), 0, 0))
        window.show_all()
        return window

    badges = [make_badge(item["n"], item["x"], item["y"]) for item in payload[:150]]

    def on_term():
        Gtk.main_quit()
        return GLib.SOURCE_REMOVE

    # Como o overlay bloqueia o mouse, ele nunca pode sobreviver ao orquestrador:
    # se o processo pai morrer, o filho encerra sozinho.
    parent = os.getppid()

    def watch_parent():
        if os.getppid() != parent:
            Gtk.main_quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, on_term)
    GLib.timeout_add(500, watch_parent)
    GLib.timeout_add_seconds(timeout, Gtk.main_quit)
    Gtk.main()
    for badge in badges:
        badge.destroy()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        run_child(int(sys.argv[2]) if len(sys.argv) > 2 else 60)
    else:
        from collections import namedtuple
        Fake = namedtuple("Fake", "number frame")
        demo = [Fake(i + 1, (80 + (i % 5) * 180, 100 + (i // 5) * 90, 100, 40)) for i in range(15)]
        print("Overlay de teste por 10 s: o foco da sua janela não deve mudar.")
        with NumberOverlay(demo):
            time.sleep(10)