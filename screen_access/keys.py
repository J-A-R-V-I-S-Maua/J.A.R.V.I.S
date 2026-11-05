"""Atalhos de teclado no Linux: xdotool (X11) ou ydotool (Wayland).

Só é usado onde o AT-SPI não oferece ação nativa (Enter, Shift+F10).
Texto livre NÃO passa por aqui: vai por EditableText.insert_text.
"""
import os
import shutil
import subprocess
import time

from .backend import ScreenError

# Códigos evdev para o ydotool (ele fala com /dev/uinput, não com keysyms).
EVDEV = {"ctrl": 29, "shift": 42, "alt": 56, "Return": 28, "BackSpace": 14,
         "a": 30, "v": 47, "F10": 68, "Menu": 127}


def session():
    kind = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if kind in ("wayland", "x11"):
        return kind
    return "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11"


def _run(command):
    try:
        subprocess.run(command, check=True, timeout=2, capture_output=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise ScreenError("Não foi possível enviar a tecla. Verifique xdotool/ydotool.") from error


def press(*combo):
    """press("Return") ou press("shift", "F10")."""
    if session() == "x11":
        if not shutil.which("xdotool"):
            raise ScreenError("Instale o xdotool para enviar teclas no X11.")
        _run(["xdotool", "key", "--clearmodifiers", "+".join(combo)])
        return
    if shutil.which("wtype"):
        *modifiers, key = combo  # ex.: ("shift", "F10") -> wtype -M shift -k F10 -m shift
        args = [arg for mod in modifiers for arg in ("-M", mod)] + ["-k", key]
        args += [arg for mod in reversed(modifiers) for arg in ("-m", mod)]
        _run(["wtype", *args])
        return
    if not shutil.which("ydotool"):
        raise ScreenError("Instale o wtype (ou o ydotool, com ydotoold ativo) para enviar teclas no Wayland.")
    codes = [EVDEV[key] for key in combo]
    sequence = [f"{code}:1" for code in codes] + [f"{code}:0" for code in reversed(codes)]
    _run(["ydotool", "key", *sequence])


def _clipboard_read():
    """Texto atual da área de transferência, ou None se estiver vazia/não for texto."""
    try:
        result = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _clipboard_write(data):
    # wl-copy deixa um processo filho segurando a seleção: sem DEVNULL o run() trava.
    command = ["wl-copy"] if data is not None else ["wl-copy", "--clear"]
    try:
        subprocess.run(command, input=data, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=2, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise ScreenError("Não foi possível usar a área de transferência (wl-clipboard).") from error


def type_text(text):
    """Escreve texto livre (com acentos) no campo que está com foco.

    X11: xdotool digita Unicode direto.
    Wayland: wtype digita Unicode direto. Sem ele, cola pelo clipboard (Ctrl+V) e
    restaura o conteúdo anterior (o ydotool digita pelo layout US e perde acentos).
    """
    if session() == "x11":
        if not shutil.which("xdotool"):
            raise ScreenError("Instale o xdotool para digitar no X11.")
        _run(["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", text])
        return
    if shutil.which("wtype"):
        # Unicode direto pelo teclado virtual do Wayland: sem layout, sem clipboard.
        _run(["wtype", "--", text])
        return
    if not shutil.which("wl-copy"):
        raise ScreenError("Instale o wtype (ou o wl-clipboard) para digitar no Wayland.")
    previous = _clipboard_read()
    _clipboard_write(text.encode("utf-8"))
    try:
        press("ctrl", "v")
        time.sleep(0.25)  # O aplicativo lê o clipboard de forma assíncrona.
    finally:
        _clipboard_write(previous)