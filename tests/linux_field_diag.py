"""Descobre qual método de escrita o Firefox (e o seu sistema) aceitam.

    python3 -X faulthandler -m tests.linux_insert_diag

Deixe o Firefox em primeiro plano durante a espera. O texto original da barra
de endereço é restaurado ao final de cada tentativa.
"""
import os
import shutil
import time

from gi.repository import Atspi

from screen_access.linux import LinuxBackend

print("Sessão:", os.environ.get("XDG_SESSION_TYPE"), "| desktop:", os.environ.get("XDG_CURRENT_DESKTOP"))
for tool in ("ydotool", "ydotoold", "wl-copy", "wl-paste", "wtype", "xdotool"):
    print(f"  {tool:<9}", shutil.which(tool) or "-")
print("  /dev/uinput gravável:", os.access("/dev/uinput", os.W_OK))

print("\nDeixe o Firefox em primeiro plano...")
time.sleep(5)


def read(acc):
    time.sleep(0.4)
    return Atspi.Text.get_text(acc, 0, -1)


with LinuxBackend() as backend:
    window = backend.tracked_foreground()
    snapshot = backend.scan(window)
    field = next(e for e in snapshot.elements if e.action == "focus")
    target = backend.references[field.number][0]
    Atspi.Component.grab_focus(target)
    time.sleep(0.4)

    original = read(target)
    caret = Atspi.Text.get_caret_offset(target)
    print(f"\noriginal={original!r} caret={caret}")

    def attempt(label, action):
        result = action()
        print(f"{label:<34} -> retorno={result!r} | texto={read(target)!r}")
        Atspi.EditableText.set_text_contents(target, original)  # restaura
        time.sleep(0.3)

    attempt("insert_text ASCII 'abc'",
            lambda: Atspi.EditableText.insert_text(target, caret, "abc", 3))
    attempt("insert_text acentos 'olá, açaí'",
            lambda: Atspi.EditableText.insert_text(target, caret, "olá, açaí", len("olá, açaí".encode())))
    attempt("set_text_contents (texto novo)",
            lambda: Atspi.EditableText.set_text_contents(target, "teste set_text"))
    attempt("insert_text com length=-1",
            lambda: Atspi.EditableText.insert_text(target, caret, "xyz", -1))

    print("\nrestaurado:", read(target) == original)