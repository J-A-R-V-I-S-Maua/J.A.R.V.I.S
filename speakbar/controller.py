"""Máquina de estados demonstrativa, sem áudio ou chamadas de rede."""

from enum import Enum

from PySide6.QtCore import QObject, QTimer, Signal


class State(Enum):
    IDLE = "Olá, Como posso ajudar?"
    LISTENING = "Demonstração: ouvindo…"
    PROCESSING = "Demonstração: processando…"
    RESULT = "Demonstração concluída"


class DemoController(QObject):
    state_changed = Signal(object)
    STEPS = {
        State.LISTENING: (3000, State.PROCESSING),
        State.PROCESSING: (2000, State.RESULT),
        State.RESULT: (3000, State.IDLE),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = State.IDLE
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._advance)

    def toggle(self):
        self._set_state(State.LISTENING if self.state is State.IDLE else State.IDLE)

    def reset(self):
        self._set_state(State.IDLE)

    def _set_state(self, state):
        self.timer.stop()
        self.state = state
        if state in self.STEPS:
            self.timer.start(self.STEPS[state][0])
        self.state_changed.emit(state)

    def _advance(self):
        if self.state in self.STEPS:
            self._set_state(self.STEPS[self.state][1])
