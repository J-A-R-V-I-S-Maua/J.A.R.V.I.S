"""Contrato da tela: dados imutáveis para a UI, referências nativas no backend."""
from dataclasses import dataclass
import os
import sys
import time
import uuid


class ScreenError(Exception):
    """Falha recuperável ao ler ou acionar um controle."""


@dataclass(frozen=True)
class Window:
    identity: str
    pid: int
    title: str
    frame: tuple | None


@dataclass(frozen=True)
class Element:
    number: int
    name: str
    role: str
    frame: tuple | None
    action: str


@dataclass(frozen=True)
class Node:
    depth: int
    role: str
    name: str
    frame: tuple | None


@dataclass(frozen=True)
class Snapshot:
    token: str
    window: Window
    elements: tuple
    nodes: tuple = ()
    truncated: bool = False


def intersection(a, b):
    if a is None or b is None:
        return None
    x, y = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[0]+a[2], b[0]+b[2]), min(a[1]+a[3], b[1]+b[3])
    return (x, y, right-x, bottom-y) if right > x and bottom > y else None


class Backend:
    def __init__(self):
        self.references = {}
        self.snapshot = None
        self.require_foreground = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.references.clear()
        self.snapshot = None

    def scan(self, window, max_depth=32, max_elements=3000, include_hidden=False, cancelled=lambda: False):
        if max_depth < 0 or max_elements < 1:
            raise ScreenError("Limites de leitura inválidos.")
        if window is None or window.pid == os.getpid():
            raise ScreenError("Selecione uma janela de aplicativo antes do comando.")
        if not self.window_exists(window):
            raise ScreenError("A janela mudou. Solicite uma nova leitura.")
        self.references.clear()
        self.snapshot = None
        elements, nodes, seen = [], [], set()
        truncated = False
        self.walk_incomplete = False
        deadline = time.monotonic() + 5
        for depth, native, clip in self.walk(window, max_depth):
            if cancelled():
                raise ScreenError("Leitura cancelada.")
            if len(nodes) >= max_elements or time.monotonic() > deadline:
                truncated = True
                break
            try:
                name, role, frame, action, visible, identity = self.describe(native, clip)
            except Exception:
                continue  # Um elemento pode desaparecer enquanto a árvore é percorrida.
            nodes.append(Node(depth, role, name, frame))
            if identity in seen or not action or (not include_hidden and not visible):
                continue
            if len(elements) >= 200:
                truncated = True
                continue
            seen.add(identity)
            number = len(elements) + 1
            elements.append(Element(number, name, role, frame, action))
            self.references[number] = (native, identity, clip)
        self.snapshot = Snapshot(uuid.uuid4().hex, window, tuple(elements), tuple(nodes),
                                 truncated or self.walk_incomplete)
        return self.snapshot

    def validate(self, snapshot, number):
        if self.snapshot is None or snapshot.token != self.snapshot.token or number not in self.references:
            raise ScreenError("A lista expirou. Diga mostrar números novamente.")
        current = getattr(self, "tracked_foreground", self.foreground)()
        if self.require_foreground and (current is None or (
                current.pid != os.getpid() and current.identity != snapshot.window.identity)):
            raise ScreenError("A janela mudou. Solicite uma nova leitura.")
        if not self.window_exists(snapshot.window):
            raise ScreenError("A janela mudou ou foi fechada.")
        native, identity, clip = self.references[number]
        old = snapshot.elements[number-1]
        try:
            name, role, frame, action, visible, new_identity = self.describe(native, clip)
        except Exception as exc:
            raise ScreenError("O elemento desapareceu. Solicite uma nova leitura.") from exc
        if (identity != new_identity or name != old.name or role != old.role or frame != old.frame
                or action != old.action or not visible):
            raise ScreenError("O elemento mudou. Diga mostrar números para atualizar a lista.")
        return native, action

    def activate(self, snapshot, number, cancelled=lambda: False):
        native, action = self.validate(snapshot, number)
        if cancelled():
            raise ScreenError("Ação cancelada antes da execução.")
        self.perform(native, action)

    def prepare_dictation(self, window):
        raise ScreenError("Digitação no campo ativo indisponível neste sistema.")

    def open_item(self, snapshot, number, cancelled=lambda: False):
        raise ScreenError("Abertura por duplo clique indisponível neste sistema.")

    def context_menu(self, snapshot, number, cancelled=lambda: False):
        raise ScreenError("Menu de contexto indisponível neste sistema.")

    def dictate(self, snapshot, text, cancelled=lambda: False):
        raise ScreenError("Digitação no campo ativo indisponível neste sistema.")

    def press_enter(self, snapshot, number=1, cancelled=lambda: False):
        raise ScreenError("Enter no campo ativo indisponível neste sistema.")

    def clear_text(self, snapshot, number=1, cancelled=lambda: False):
        raise ScreenError("Limpeza do campo ativo indisponível neste sistema.")


def get_backend():
    if sys.platform == "win32":
        from .windows import WindowsBackend
        return WindowsBackend()
    if sys.platform == "darwin":
        from .macos_backend import MacOSBackend
        return MacOSBackend()
    raise ScreenError("Acesso à tela disponível somente no Windows e macOS.")
