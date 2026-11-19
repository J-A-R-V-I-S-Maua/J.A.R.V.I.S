"""Conversa de voz que abre aplicativos pelo nome ("abrir Safari") no macOS.

É uma ShortcutConversation com mais um assunto: o controlador já sabe falar com ela (estado ativo,
nova captura, nova tentativa, cancelamento), então nada mais precisa mudar para escutar as respostas.
"""
import logging
import threading
import time

from screen_access.commands import CONFIRM, parse_number

from .applications import find_application, is_open_request, open_application
from .commands import normalize
from .shortcuts import ShortcutConversation

CANCEL = {"cancelar", "cancele", "nao", "parar", "pare"}
TIMEOUT_SECONDS = 30
FIRST_LOAD_WAIT = 1.0  # só importa se a pessoa pedir um app logo ao iniciar, antes da lista existir


class LauncherConversation(ShortcutConversation):
    def __init__(self, service, catalog=None, launcher=open_application, confirm=True, parent=None):
        super().__init__(service, parent)
        self.catalog = catalog
        self.launcher = launcher
        self.confirm = confirm  # como no controle da tela: nada é aberto sem a pessoa confirmar
        # None: conversa de atalho; []: esperando o nome do aplicativo; lista: apps para escolher.
        self.choices = None
        self.pending = None  # o aplicativo que espera o "confirmar"

    def cancel(self, text=None):
        self.choices = None
        self.pending = None
        super().cancel(text)

    def _expired(self):
        if self.choices is None and self.pending is None:
            super()._expired()
        else:
            self.cancel("Tempo esgotado. Diga ‘Hey Jarvis’ e repita abrir o aplicativo.")

    def handle(self, text):
        if self.active and self.pending is not None:
            self._confirmation(text)
            return True
        if self.active and self.choices is not None:
            self._answer(text)
            return True
        if self.active or self.catalog is None:
            return super().handle(text)
        if super().handle(text):
            return True
        if is_open_request(text):
            self._open(text)
            return True
        return False

    def _applications(self):
        apps = self.catalog.applications()
        if not apps and self.catalog.wait(FIRST_LOAD_WAIT):
            apps = self.catalog.applications()
        return apps

    def _start(self):
        self.active = True
        self.retry_count = 0
        self.deadline = time.monotonic() + TIMEOUT_SECONDS
        self.timer.start(TIMEOUT_SECONDS * 1000)

    def _open(self, text):
        status, found = find_application(text, self._applications())
        self._resolve(status, found)

    def _answer(self, text):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self._expired()
            return
        if normalize(text) in CANCEL:
            self.cancel("Abertura cancelada.")
            return
        number = parse_number(text) if self.choices else None
        if number is not None and 1 <= number <= len(self.choices):
            self._chosen(self.choices[number - 1])
            return
        status, found = find_application(text, self.choices or self._applications())
        if status == "none" and self.choices:
            # Um nome que não está entre as opções ainda pode ser outro aplicativo instalado.
            status, found = find_application(text, self._applications())
        self._resolve(status, found)

    def _chosen(self, app):
        """O aplicativo está decidido; falta a confirmação (a menos que ela esteja desligada)."""
        if not self.confirm:
            self._launch(app)
            return
        if not self.active:
            self._start()
        self.choices = None
        self.pending = app
        self.retry_count = 0  # a pergunta de confirmação tem a sua própria nova tentativa
        self.changed.emit(f"Abrir {app.name}? Diga confirmar ou cancelar.")
        self.capture_requested.emit()

    def _confirmation(self, text):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self._expired()
            return
        value = normalize(text)
        if value in CONFIRM:
            self._launch(self.pending)
        elif value in CANCEL:
            self.cancel("Abertura cancelada.")
        else:
            self.retry("Não entendi. Diga confirmar ou cancelar. Ouvindo novamente…")

    def _resolve(self, status, found):
        if status == "match":
            self._chosen(found[0])
            return
        first = not self.active
        if first:
            self._start()
        if status == "none":
            self.choices = []
            self.catalog.invalidate()  # a próxima tentativa já enxerga um app recém-instalado
            self.retry("Não encontrei esse aplicativo. Diga o nome de novo ou cancelar. Ouvindo novamente…")
            return
        self.choices = found
        listing = ", ".join(f"{number} {app.name}" for number, app in enumerate(found, 1))
        message = f"Encontrei {len(found)} aplicativos: {listing}. Diga o número ou cancelar."
        if first:
            self.changed.emit(message)
            self.capture_requested.emit()
        else:
            self.retry(message + " Ouvindo novamente…")

    def _launch(self, app):
        self.active = False
        self.choices = None
        self.pending = None
        self.timer.stop()
        self.changed.emit(f"Abrindo {app.name}…")
        # O comando "open" é rápido, mas não vale esperar por ele na thread da interface.
        threading.Thread(target=self._open_in_background, args=(app,), name="abrir-aplicativo", daemon=True).start()

    def _open_in_background(self, app):
        try:
            self.launcher(app.path)
        except Exception as exc:
            logging.exception("Falha ao abrir %s", app.name)
            self.changed.emit(f"Não consegui abrir {app.name}. {exc}")
        else:
            self.changed.emit(f"Abertura de {app.name} solicitada ao sistema.")
