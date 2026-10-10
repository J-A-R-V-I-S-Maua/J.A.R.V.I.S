"""Executar explicitamente: python tests/screen_native_check.py. Não toca em outros aplicativos."""
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screen_access.backend import Window
from screen_access.executor import ScreenExecutor
from screen_access.session import ActionState, ScreenSession
from screen_access.windows import WindowsBackend


def main():
    if sys.platform != "win32":
        raise SystemExit("Esta verificação requer Windows.")
    fixture = Path(__file__).with_name("screen_native_fixture.py")
    child = subprocess.Popen([sys.executable, str(fixture)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    outputs = queue.Queue()

    def reader():
        for line in child.stdout:
            outputs.put(json.loads(line))

    threading.Thread(target=reader, daemon=True).start()
    replies = queue.Queue()
    executor = None
    try:
        ready = outputs.get(timeout=8)
        while "handle" not in ready:
            ready = outputs.get(timeout=8)
        def response(token):
            while True:
                result_token, kind, data = replies.get(timeout=8)
                if kind in ("error", "unavailable"):
                    raise AssertionError(data)
                if result_token == token:
                    return kind, data

        # O backend determina o retângulo físico na sua thread; nenhuma referência nativa é passada.
        class FixtureBackend(WindowsBackend):
            def foreground(self):
                control = self.api.ControlFromHandle(ready["handle"])
                return Window(str(ready["handle"]), ready["pid"], control.Name, self.frame(control))

        executor = ScreenExecutor(lambda *reply: replies.put(reply), FixtureBackend, require_foreground=False)
        executor.start()
        window = None
        while window is None:
            _, kind, data = replies.get(timeout=8)
            if kind == "foreground" and data.pid == ready["pid"]:
                window = data
        views = []
        session = ScreenSession(executor, views.append)
        session.receive(0, "foreground", window)

        for phrase, action in (("clique em Test action", "invoke"), ("clique em Test checkbox", "toggle"),
                               ("clique em Test input", "focus"), ("clique em Second test item", "select")):
            session.begin_capture()
            assert session.handle(phrase)
            kind, result = response(session.token)
            session.receive(session.token, kind, result)
            assert session.state is ActionState.CONFIRMING, views[-1]
            element = session.snapshot.elements[session.selected-1]
            assert element.action == action, element
            session.handle("confirmar")
            kind, result = response(session.token)
            session.receive(session.token, kind, result)
            assert not session.active
            if action != "focus":
                event = outputs.get(timeout=3)
                while event.get("event") != action:
                    event = outputs.get(timeout=3)
            print(f"Windows nativo: {action}, seleção e confirmação: OK")
            if action == "focus":
                session.begin_capture()
                session.handle("digitar Olá, São Paulo! {Enter} 😀")
                kind, result = response(session.token)
                session.receive(session.token, kind, result)
                assert session.state is ActionState.CONFIRMING, views[-1]
                session.handle("confirmar")
                kind, result = response(session.token)
                session.receive(session.token, kind, result)
                assert not session.active
                expected = "Prefixo: Olá, São Paulo! {Enter} 😀"
                event = outputs.get(timeout=3)
                while event.get("event") != "text" or event.get("text") != expected:
                    event = outputs.get(timeout=3)
                print("Windows nativo: digitação Unicode literal, acentos e emoji: OK")
                for command, expected_event in (("Enter", "enter"), ("Deletar", "text")):
                    session.begin_capture()
                    session.handle(command)
                    kind, result = response(session.token)
                    session.receive(session.token, kind, result)
                    assert session.state is ActionState.CONFIRMING, views[-1]
                    session.handle("confirmar")
                    kind, result = response(session.token)
                    session.receive(session.token, kind, result)
                    assert not session.active
                    event = outputs.get(timeout=3)
                    while event.get("event") != expected_event:
                        event = outputs.get(timeout=3)
                    assert event.get("text") == (expected if command == "Enter" else ""), event
                    print(f"Windows nativo: {command} no campo após confirmação: OK")
        session.begin_capture()
        session.handle("abrir menu de contexto de Test action")
        kind, result = response(session.token)
        session.receive(session.token, kind, result)
        assert session.state is ActionState.CONFIRMING, views[-1]
        session.handle("confirmar")
        kind, result = response(session.token)
        session.receive(session.token, kind, result)
        assert not session.active
        event = outputs.get(timeout=3)
        while event.get("event") != "context_menu":
            event = outputs.get(timeout=3)
        print("Windows nativo: menu de contexto após foco e confirmação: OK")
        return 0
    finally:
        if executor:
            executor.stop()
            if executor.thread:
                executor.thread.join(2)
        if child.poll() is None:
            child.terminate()
        child.communicate(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
