"""Adaptação dos eventos do serviço de voz para a thread gráfica."""
import logging

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from wakeword.events import State, VoiceEvent


class VoiceThread(QThread):
    event_received = Signal(object)

    def __init__(self, parent=None, service_factory=None):
        super().__init__(parent)
        if service_factory is None:
            from wakeword.detect_microphone_service import create_service
            service_factory = create_service
        self.service = service_factory(self.event_received.emit)

    def run(self):
        try:
            self.service.run()
        except Exception:
            logging.exception("Serviço de voz encerrado inesperadamente")


class VoiceController(QObject):
    state_changed = Signal(object)
    stopped = Signal()

    def __init__(self, parent=None, service_factory=None):
        super().__init__(parent)
        self.current_event = VoiceEvent(State.PREPARING, State.PREPARING.value)
        self.thread = None
        self.service_factory = service_factory
        self.closing = False

    @property
    def state(self):
        return self.current_event.state

    @property
    def text(self):
        return self.current_event.text

    def start(self):
        if self.closing or (self.thread is not None and self.thread.isRunning()):
            return
        if self.thread is not None:
            self.thread.deleteLater()
        self.thread = VoiceThread(self, self.service_factory)
        self.thread.event_received.connect(self._receive)
        self.thread.finished.connect(self._finished)
        self.current_event = VoiceEvent(State.PREPARING, State.PREPARING.value)
        self.state_changed.emit(self.current_event)
        self.thread.start()

    def _receive(self, event):
        # Uma resposta na fila do Qt pode pertencer à interação já cancelada.
        if event is not self.thread.service.snapshot or event.state is State.STOPPED:
            return
        if self.closing and event.state is not State.STOPPING:
            return
        self.current_event = event
        self.state_changed.emit(event)

    def toggle(self):
        if self.closing:
            return
        if self.thread is None or not self.thread.isRunning():
            self.start()
        else:
            self.thread.service.toggle()

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        self.current_event = VoiceEvent(State.STOPPING, State.STOPPING.value)
        self.state_changed.emit(self.current_event)
        if self.thread is not None and self.thread.isRunning():
            self.thread.service.stop()
        else:
            self.stopped.emit()

    def _finished(self):
        if self.closing:
            self.stopped.emit()
        else:
            self.current_event = VoiceEvent(State.ERROR, "Serviço encerrado. Clique para tentar novamente.")
            self.state_changed.emit(self.current_event)


class DemoController(QObject):
    state_changed = Signal(object)
    stopped = Signal()
    STEPS = {
        State.LISTENING: (3000, State.PROCESSING),
        State.PROCESSING: (2000, State.RESULT),
        State.RESULT: (3000, State.IDLE),
    }
    MESSAGES = {
        State.IDLE: "Olá, Como posso ajudar?",
        State.LISTENING: "Demonstração: ouvindo…",
        State.PROCESSING: "Demonstração: processando…",
        State.RESULT: "Demonstração concluída",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_event = VoiceEvent(State.IDLE, self.MESSAGES[State.IDLE])
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._advance)

    @property
    def state(self):
        return self.current_event.state

    @property
    def text(self):
        return self.current_event.text

    def start(self):
        pass

    def toggle(self):
        self._set_state(State.LISTENING if self.state is State.IDLE else State.IDLE)

    def _set_state(self, state):
        self.timer.stop()
        self.current_event = VoiceEvent(state, self.MESSAGES[state])
        if state in self.STEPS:
            self.timer.start(self.STEPS[state][0])
        self.state_changed.emit(self.current_event)

    def _advance(self):
        if self.state in self.STEPS:
            self._set_state(self.STEPS[self.state][1])

    def shutdown(self):
        self.timer.stop()
        self.stopped.emit()
