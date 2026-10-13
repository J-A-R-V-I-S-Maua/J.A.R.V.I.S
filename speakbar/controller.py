"""Adaptação dos eventos do serviço de voz para a thread gráfica."""
import logging
from dataclasses import replace

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
    markers_changed = Signal(object)
    settings_requested = Signal()
    audio_level = Signal(int)
    diagnostic_result = Signal(str)
    devices_changed = Signal(object)

    def __init__(self, parent=None, service_factory=None, screen_factory=None, preferences=None):
        super().__init__(parent)
        self.current_event = VoiceEvent(State.PREPARING, State.PREPARING.value)
        self.thread = None
        self.service_factory = service_factory
        self.closing = False
        from .screen_controller import ScreenController
        self.screen = (screen_factory or ScreenController)(self)
        self.screen.changed.connect(self._screen_changed)
        self.screen.invalidated.connect(self._invalidate_voice)
        self._screen_text = None
        self._voice_event = self.current_event
        self._last_result_id = -1
        self._ignore_result = False
        self._screen_started = False
        from .preferences import PreferencesService
        from .shortcuts import ShortcutService, ShortcutConversation
        self.preferences = preferences or PreferencesService()
        self.shortcut_service = ShortcutService(self.preferences.shortcuts)
        self.shortcuts = ShortcutConversation(self.shortcut_service, self)
        self.shortcuts.changed.connect(self._show_text)
        self.shortcuts.cancelled.connect(self._invalidate_voice)
        self.shortcuts.capture_requested.connect(lambda: QTimer.singleShot(0, self._request_shortcut_response))
        self.settings_open = False
        self._diagnostic_test = False
        self._restarting = False
        self.last_error = self.preferences.error or ""
        self.audio_devices = []
        self.device_error = None
        self.devices_changed.connect(self._devices_updated)

    def _devices_updated(self, result):
        self.audio_devices = result["devices"]
        self.device_error = result["error"]

    def request_device_list(self):
        if self.thread and self.thread.isRunning():
            self.thread.service.request_device_list()

    def _show_text(self, text):
        self._screen_text = text
        self.current_event = replace(self._voice_event, text=text)
        self.state_changed.emit(self.current_event)

    def _configure_service(self):
        from wakeword.speech_gate import SpeechConfig
        values = self.preferences.effective
        service = self.thread.service
        service.input_device = values["microphone"] if "microphone" in self.preferences.overrides else "environment"
        service.detection_threshold = values["detection_threshold"]
        if hasattr(service, "speech_config"):
            service.speech_config = SpeechConfig(values["silence_seconds"], values["start_timeout"],
                                                values["max_seconds"], SpeechConfig.from_environment().pre_roll_seconds)
        service.on_level = self.audio_level.emit
        service.on_devices = self.devices_changed.emit
        service.set_paused(self.settings_open)

    def set_settings_open(self, opened):
        self.settings_open = opened
        self._diagnostic_test = False
        self.shortcuts.cancel()
        self.screen.cancel()
        if self.thread:
            self.thread.service.set_paused(opened)
        self._show_text("Reconhecimento pausado enquanto as configurações estão abertas." if opened else "Diga hey jarvis ou use o botão para começar.")

    def test_audio(self, transcription=False):
        if not self.settings_open or not self.thread or not self.thread.isRunning():
            self.diagnostic_result.emit("O serviço de voz não está disponível. Salve os ajustes e tente novamente.")
            return
        if transcription:
            self._diagnostic_test = self.thread.service.request_capture(diagnostic=True)
            self.diagnostic_result.emit("Diga uma frase. Nenhum comando será executado." if self._diagnostic_test else "Aguarde o serviço ficar disponível e tente novamente.")
        else:
            self.thread.service.request_audio_test()
            self.diagnostic_result.emit("Teste de captação por 5 segundos. Observe o nível de áudio.")

    def apply_preferences(self, previous):
        values = self.preferences.effective
        self.shortcut_service.items = self.preferences.shortcuts
        self.screen.session.max_depth = values["max_depth"]
        self.screen.session.max_elements = values["max_elements"]
        keys = ("microphone", "silence_seconds", "start_timeout", "max_seconds", "detection_threshold")
        selected = values["microphone"] if "microphone" in self.preferences.overrides else "environment"
        microphone_changed = self.thread is not None and self.thread.service.input_device != selected
        if (microphone_changed or any(previous[key] != values[key] for key in keys)) and self.thread and self.thread.isRunning():
            self._restarting = True
            self._diagnostic_test = False
            self.thread.service.stop()
        elif not self.thread or not self.thread.isRunning():
            self.start()

    def _request_shortcut_response(self):
        if self.shortcuts.active and not self.settings_open and not self.closing and self.thread:
            self.thread.service.request_capture()

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
        self._configure_service()
        self._last_result_id = -1
        self.thread.event_received.connect(self._receive)
        self.thread.finished.connect(self._finished)
        self.current_event = VoiceEvent(State.PREPARING, State.PREPARING.value)
        self.state_changed.emit(self.current_event)
        self.thread.start()
        if not self._screen_started:
            self._screen_started = True
            self.screen.start()
            values = self.preferences.effective
            self.screen.session.max_depth = values["max_depth"]
            self.screen.session.max_elements = values["max_elements"]

    def _receive(self, event):
        # Uma resposta na fila do Qt pode pertencer à interação já cancelada.
        if event is not self.thread.service.snapshot or event.state is State.STOPPED:
            return
        if self.closing and event.state is not State.STOPPING:
            return
        self._voice_event = event
        if event.state is State.ERROR:
            self.last_error = event.text
        if self.settings_open:
            if event.state is State.ERROR or (self._diagnostic_test and event.state is State.RESULT):
                self._diagnostic_test = False
                self.diagnostic_result.emit(event.text)
            self._show_text("Reconhecimento pausado enquanto as configurações estão abertas.")
            return
        if event.state is State.LISTENING:
            self._ignore_result = False
            if not self.shortcuts.active:
                self.screen.session.begin_capture()
            if not self.screen.session.active:
                self._screen_text = None
        if event.state is State.ERROR and self.screen.session.active:
            self.screen.cancel("Não foi recebida uma resposta válida. Repita o comando.")
        if event.state is State.ERROR and self.shortcuts.active:
            self.shortcuts.cancel("Nenhuma resposta válida para o atalho. Repita executar atalho.")
        if event.state is State.RESULT and event.is_final:
            if event.interaction_id <= self._last_result_id:
                return
            self._last_result_id = event.interaction_id
            if self._ignore_result:
                return
            if self.shortcuts.active:
                self.shortcuts.handle(event.text)
                return
            if not self.screen.session.active:
                from .shortcuts import normalize, SETTINGS_COMMANDS
                if normalize(event.text) in SETTINGS_COMMANDS:
                    self.settings_requested.emit()
                    return
                if self.shortcuts.handle(event.text):
                    self.screen.session.target = None
                    return
            if self.screen.session.handle(event.text):
                return
        self.current_event = replace(event, text=self._screen_text) if self._screen_text else event
        self.state_changed.emit(self.current_event)

    def _screen_changed(self, view):
        if self.closing:
            return
        self._screen_text = view.text
        self.markers_changed.emit(view.markers)
        self.current_event = replace(self._voice_event, text=view.text)
        self.state_changed.emit(self.current_event)
        if view.capture:
            QTimer.singleShot(0, self._request_response)

    def _request_response(self):
        if not self.closing and self.screen.session.active and self.thread:
            self.thread.service.request_capture()

    def _invalidate_voice(self):
        self._ignore_result = True
        if self.thread:
            self.thread.service.cancel_interaction()

    def toggle(self):
        if self.closing or self.settings_open:
            return
        if self.shortcuts.active:
            self.shortcuts.cancel("Atalho cancelado.")
            self._invalidate_voice()
            return
        if self.screen.session.active:
            self.screen.cancel()
            return
        if self.thread is None or not self.thread.isRunning():
            self.start()
        else:
            self.thread.service.toggle()

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        self.shortcuts.cancel()
        self.markers_changed.emit(())
        self.screen.shutdown()
        self.current_event = VoiceEvent(State.STOPPING, State.STOPPING.value)
        self.state_changed.emit(self.current_event)
        if self.thread is not None and self.thread.isRunning():
            self.thread.service.stop()
        else:
            self.stopped.emit()

    def _finished(self):
        if self.closing:
            self.stopped.emit()
        elif self._restarting:
            self._restarting = False
            QTimer.singleShot(0, self.start)
        else:
            self.screen.cancel("Reconhecimento encerrado; ação pendente cancelada.")
            self.current_event = VoiceEvent(State.ERROR, "Serviço encerrado. Clique para tentar novamente.")
            self.state_changed.emit(self.current_event)


class DemoController(QObject):
    state_changed = Signal(object)
    stopped = Signal()
    settings_requested = Signal()
    audio_level = Signal(int)
    diagnostic_result = Signal(str)
    devices_changed = Signal(object)
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
        from .preferences import PreferencesService
        from .shortcuts import ShortcutService
        self.preferences = PreferencesService()
        self.shortcut_service = ShortcutService(self.preferences.shortcuts, demo=True)
        self.settings_open = False
        self.last_error = self.preferences.error or ""
        self.audio_devices = []
        self.device_error = None

    def request_device_list(self):
        pass

    def set_settings_open(self, opened):
        self.settings_open = opened
        self.timer.stop()
        self._set_state(State.IDLE)

    def test_audio(self, transcription=False):
        self.diagnostic_result.emit("Demonstração: microfone e transcrição não são iniciados.")

    def apply_preferences(self, previous):
        self.shortcut_service.items = self.preferences.shortcuts

    @property
    def state(self):
        return self.current_event.state

    @property
    def text(self):
        return self.current_event.text

    def start(self):
        pass

    def toggle(self):
        if self.settings_open:
            return
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
