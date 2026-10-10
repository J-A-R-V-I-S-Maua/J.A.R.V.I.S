"""Ponte Qt da sessão de tela. Não transporta referências COM/AX."""
import os
from PySide6.QtCore import QObject, QTimer, Signal
from screen_access.executor import ScreenExecutor
from screen_access.session import ScreenSession


class ScreenController(QObject):
    reply = Signal(int, str, object)
    changed = Signal(object)
    invalidated = Signal()

    def __init__(self, parent=None, executor_factory=ScreenExecutor):
        super().__init__(parent)
        self.executor = executor_factory(self.reply.emit)
        self.session = ScreenSession(self.executor, self._publish)
        self.configuration_error = None
        try:
            self.session.max_depth = int(os.getenv("SCREEN_MAX_DEPTH", "32"))
            self.session.max_elements = int(os.getenv("SCREEN_MAX_ELEMENTS", "3000"))
            if self.session.max_depth < 0 or not 1 <= self.session.max_elements <= 10000:
                raise ValueError()
        except ValueError:
            self.configuration_error = "SCREEN_MAX_DEPTH deve ser >= 0 e SCREEN_MAX_ELEMENTS entre 1 e 10000."
        self.reply.connect(self._reply)
        self.expiration = QTimer(self)
        self.expiration.setSingleShot(True)
        self.expiration.timeout.connect(lambda: self.cancel("Interação expirada. Repita o comando."))
        self.validation = QTimer(self)
        self.validation.setInterval(1000)
        self.validation.timeout.connect(self._validate_markers)
        self._validating = False
        self.closing = False

    def start(self):
        self.executor.start()
        if self.configuration_error:
            self.session.unavailable = self.configuration_error

    def _publish(self, view):
        if view.markers:
            self.validation.start()
        else:
            self.validation.stop()
        if self.session.active:
            self.expiration.start(90000 if view.state.value == "executing" else 30000)
        else:
            self.expiration.stop()
        self.changed.emit(view)

    def _reply(self, token, kind, data):
        if self.closing:
            return
        if kind in ("scan", "activate", "open_item", "context_menu", "press_enter", "clear_text", "validate", "error"):
            self._validating = False
        was_active = self.session.active
        had_target = self.session.target is not None
        self.session.receive(token, kind, data)
        if kind == "foreground" and had_target and self.session.target is None:
            self.invalidated.emit()
        elif was_active and not self.session.active and kind in ("error", "unavailable"):
            self.invalidated.emit()

    def _validate_markers(self):
        from screen_access.session import ActionState
        session = self.session
        if not self._validating and session.snapshot and session.state in (
                ActionState.CHOOSING, ActionState.CONFIRMING):
            numbers = (session.selected,) if session.selected else session.candidates
            self._validating = True
            self.executor.submit(session.token, "validate", session.snapshot, numbers)

    def cancel(self, text="Ação cancelada."):
        self._validating = False
        self.session.cancel(text)
        self.invalidated.emit()

    def shutdown(self):
        self.closing = True
        self.expiration.stop()
        self.validation.stop()
        self.session.cancel()
        self.executor.stop()
