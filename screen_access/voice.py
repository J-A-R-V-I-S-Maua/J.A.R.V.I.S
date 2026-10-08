"""Navegação por voz: a frase falada escolhe um item da tela. A confirmação ainda é digitada.

Uso: python -m screen_access.voice   (com o Docker de pé e o app alvo em primeiro plano)
"""

from concurrent.futures import ThreadPoolExecutor
import queue
import threading
import time

from wakeword.events import State

from . import flow


class VoiceNavigator:
    def __init__(self, read_screen, press, describe_error, ask=None, say=print):
        self.read_screen = read_screen
        self.press = press
        self.describe_error = describe_error
        self.ask = ask
        self.say = say
        self.requests = queue.Queue()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="screen")
        self._reading = {}  # id da interação -> leitura da tela em andamento
        self._answered = set()

    def _timed_read(self):
        began = time.monotonic()
        screen = self.read_screen()
        return screen, time.monotonic() - began

    def on_event(self, event):
        """Chamado pelo serviço de voz, que segura uma trava: só pode fazer coisas rápidas."""
        if event.state is State.LISTENING:
            if event.interaction_id not in self._reading:
                self._reading[event.interaction_id] = self._executor.submit(self._timed_read)
                self.say("Ouvindo… (lendo a tela enquanto você fala)")
        elif event.state is State.RESULT and event.is_final:
            if event.interaction_id not in self._answered:
                self._answered.add(event.interaction_id)
                reading = self._reading.pop(event.interaction_id, None)
                self.requests.put((event.text, reading))
        elif event.state is State.ERROR:
            self.say(event.text)

    def handle(self, phrase, reading):
        """Trata uma frase final: lê a tela (se preciso), escolhe o item, confirma e aciona."""
        self.say(f"\nEntendi: {phrase!r}")
        try:
            screen, seconds = (reading or self._executor.submit(self._timed_read)).result(timeout=60)
        except Exception as exc:
            self.say(f"Não consegui ler a tela: {exc}")
            return 1
        self.say(f"Tela: {screen.app} - {screen.title!r} ({len(screen.items)} itens, lida em {seconds:.1f}s)")
        if not screen.items:
            self.say("Nenhum item acionável nessa tela.")
            return 1
        index = flow.resolve(phrase, screen.items, self.ask, self.say)
        if index is None:
            return 0
        return flow.confirm_and_press(
            index, screen.items, self.press, self.describe_error, self.ask, self.say)

    def serve(self, is_running):
        """Atende as frases finais, uma por vez, até `is_running()` virar falso."""
        while is_running():
            try:
                phrase, reading = self.requests.get(timeout=0.5)
            except queue.Empty:
                continue
            self.handle(phrase, reading)
            self.say("\nAguardando comando… (diga hey jarvis; Ctrl+C para sair)")


def main() -> int:
    from . import macos, snapshot
    from .permissions import has_accessibility_permission
    from wakeword.detect_microphone_service import create_service

    if not has_accessibility_permission():
        print("Sem permissão de Acessibilidade para este programa. Veja os Ajustes do Sistema.")
        return 1
    navigator = VoiceNavigator(snapshot.read_frontmost, macos.press, macos.describe_error)
    service = create_service(navigator.on_event)
    thread = threading.Thread(target=service.run, name="voz", daemon=True)
    thread.start()
    print("Deixe o app alvo (ex.: Chrome no YouTube) em primeiro plano e diga hey jarvis.")
    try:
        navigator.serve(thread.is_alive)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
