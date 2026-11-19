"""Testa scan, foco e ditado de ponta a ponta.

    python3 -m tests.linux_scan_test

Rode a partir do terminal: durante a espera, deixe o Firefox em primeiro plano.
Depois o próprio script precisa trazer o Firefox de volta (é esse o teste no Wayland).
"""
import time

from screen_access.backend import ScreenError
from screen_access.linux import LinuxBackend

print("Deixe o Firefox em primeiro plano...")
time.sleep(5)

with LinuxBackend() as backend:
    window = backend.tracked_foreground()
    print("Janela:", window)

    start = time.monotonic()
    snapshot = backend.scan(window)
    print(f"scan: {time.monotonic() - start:.2f}s | nós={len(snapshot.nodes)} "
          f"elementos={len(snapshot.elements)} truncado={snapshot.truncated}")
    for element in snapshot.elements[:25]:
        print(f"  {element.number:>3} {element.role:<18} {element.name[:40]!r:<44} {element.action}")

    field = next((e for e in snapshot.elements if e.action == "focus"), None)
    if field is None:
        raise SystemExit("Nenhum campo com ação 'focus' encontrado.")
    print("\nCampo escolhido:", field)

    try:
        backend.activate(snapshot, field.number)  # ação nativa (grab_focus)
        time.sleep(0.3)
        dictation = backend.prepare_dictation(backend.tracked_foreground())
        backend.dictate(dictation, "olá, açaí")
        print("Ditado enviado: veja se 'olá, açaí' apareceu no campo.")
    except ScreenError as error:
        print("ScreenError:", error)