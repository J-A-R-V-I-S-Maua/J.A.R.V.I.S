"""Testa a escrita via AT-SPI no campo que estiver COM FOCO.

    python3 -X faulthandler -m tests.linux_insert_diag

Durante a espera (10 s; mude com WAIT=15), vá até um campo de texto de uma página
e clique nele. Sugestões: a caixa de busca do Google ou 'Find in Settings' em
about:preferences. A barra de endereço do Firefox é recusada (já sabemos que ignora
escrita). O texto original do campo é restaurado ao final de cada tentativa.
"""
import os
import time

from gi.repository import Atspi

from screen_access.linux import LinuxBackend

wait = int(os.environ.get("WAIT", "10"))
print(f"Clique no campo de texto agora ({wait} s)...")
time.sleep(wait)


def read(acc):
    time.sleep(0.4)
    return Atspi.Text.get_text(acc, 0, -1)


with LinuxBackend() as backend:
    window = backend.tracked_foreground()
    print("Janela:", window.title if window else None)
    target = backend.focused_in(window) if window else None
    if target is None:
        raise SystemExit("Nenhum nó com foco encontrado.")
    print("campo em foco:", target.get_role_name(), repr(target.get_name()),
          "| editable:", backend.editable(target))
    if "Search with Google or enter address" in target.get_name():
        raise SystemExit("Esse é a barra de endereço. Clique em um campo DENTRO da página e repita.")

    original = read(target)
    caret = Atspi.Text.get_caret_offset(target)
    print(f"original={original!r} caret={caret}")

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

    print("restaurado:", read(target) == original)