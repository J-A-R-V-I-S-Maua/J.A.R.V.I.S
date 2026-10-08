"""Leitura da tela do aplicativo em foco (macOS): lista os itens visíveis e acionáveis."""

from dataclasses import dataclass

from . import macos
from .flow import NO_NAME


@dataclass(frozen=True)
class Screen:
    app: str
    title: str
    items: list  # [(elemento, papel, nome)] na ordem em que os números são mostrados


def collect_items(window, max_depth, max_elements, include_hidden=False):
    """Itens interativos e visíveis da janela, sem repetições."""
    window_frame = macos.element_frame(window)
    if include_hidden or window_frame is None:
        nodes = ((element, None) for _, element in macos.walk(window, max_depth))
    else:
        nodes = (
            (element, clip)
            for _, element, clip in macos.walk_with_clip(window, max_depth, window_frame)
        )
    items = []
    seen = set()
    for visited, (element, clip) in enumerate(nodes):
        if visited >= max_elements:
            break
        role = macos.element_role(element)
        if element in seen or not macos.is_interactive(element, role):
            continue
        if clip is not None and not macos.is_visible(element, clip):
            continue
        seen.add(element)
        items.append((element, role, macos.element_name(element) or NO_NAME))
    return items


def read_frontmost(max_depth=30, max_elements=3000):
    """Lê o aplicativo que está em primeiro plano agora."""
    pid, app = macos.frontmost_application()
    macos.enable_web_accessibility(pid)
    window = macos.front_window(pid)
    if window is None:
        raise RuntimeError(f"{app} não tem uma janela acessível agora")
    return Screen(app, macos.window_title(window), collect_items(window, max_depth, max_elements))
