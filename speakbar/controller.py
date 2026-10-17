"""Adaptação dos eventos do serviço de voz para a thread gráfica."""
import logging
from dataclasses import replace

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from wakeword.events import State, VoiceEvent, Reason
from .interaction import InteractionCoordinator, Presentation


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
    settings_command_requested = Signal(str)
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
        self._last_received_event = None
        self._ignore_result = False
        self._screen_started = False
        self.coordinator = InteractionCoordinator()
        self.presentation = Presentation(State.PREPARING, State.PREPARING.value)
        self._capture_ticket = 0
        self._processing_timer = QTimer(self)
        self._processing_timer.setSingleShot(True)
        self._processing_timer.setInterval(5000)
        self._processing_timer.timeout.connect(self._slow_processing)
        from .preferences import PreferencesService
        from .shortcuts import ShortcutService, ShortcutConversation
        self.preferences = preferences or PreferencesService()
        self.shortcut_service = ShortcutService(self.preferences.shortcuts)
        self.shortcuts = ShortcutConversation(self.shortcut_service, self)
        self.shortcuts.changed.connect(self._show_text)
        self.shortcuts.cancelled.connect(self._invalidate_voice)
        self.shortcuts.capture_requested.connect(self._request_shortcut_response)
        self.settings_open = False
        self._diagnostic_test = False
        self._restarting = False
        self._recovering = False
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
        self._present()

    def _present(self):
        state = self._voice_event.state
        message = self._screen_text or self._voice_event.text
        guidance = ""
        if state in (State.LISTENING, State.PROCESSING):
            guidance, message = self._screen_text or "", state.value
        elif self._voice_event.capture_ready:
            if self.screen.session.active:
                waiting = self.screen.session.state.value in {"choosing", "confirming", "dictating", "scroll_waiting", "scroll_choosing"}
                guidance = "Responda sem repetir Hey Jarvis." if waiting else "Aguarde o resultado da operação."
            elif self.shortcuts.active:
                guidance = "Responda sem repetir Hey Jarvis."
            else:
                guidance = "Diga Hey Jarvis ou use o botão para começar."
        self.presentation = Presentation(state, message, guidance, self._voice_event.capture_ready)
        self.state_changed.emit(self.current_event)

    def _slow_processing(self):
        if not self.closing and self._voice_event.state is State.PROCESSING:
            self.presentation = replace(self.presentation, message="Transcrevendo… Ainda processando sua fala…")
            self.state_changed.emit(self.current_event)

    def _context(self):
        return dict(settings=self.settings_open, screen_state=self.screen.session.state.value,
                    shortcut=self.shortcuts.active)

    def show_help(self):
        text = self.coordinator.help(**self._context())
        if self.screen.session.active:
            self.screen.session.help(text)
        else:
            self._show_text(text)
            if self.shortcuts.active:
                self._queue_capture(lambda: self.shortcuts.active)

    def _queue_capture(self, valid=lambda: True, *, start_timeout=None):
        self._capture_ticket += 1
        ticket, generation, thread = self._capture_ticket, self.coordinator.generation, self.thread
        def request():
            if (self.closing or ticket != self._capture_ticket or generation != self.coordinator.generation
                    or thread is not self.thread or not thread or not valid()):
                return
            accepted = False
            if thread.service.capture_ready:
                accepted = thread.service.request_capture() if start_timeout is None else thread.service.request_capture(start_timeout=start_timeout)
            if not accepted:
                QTimer.singleShot(50, request)
        request()

    def _recognition_failed(self, message):
        if self.screen.session.active:
            from screen_access.session import ActionState
            if self.screen.session.state in (ActionState.CHOOSING, ActionState.CONFIRMING, ActionState.DICTATING, ActionState.SCROLL_CHOOSING, ActionState.SCROLL_WAITING):
                self.screen.session.retry(message)
            else:
                self._show_text("Operação em andamento. Aguarde o resultado.")
            return
        if self.shortcuts.active:
            self.shortcuts.retry(message)
            return
        text, retry = self.coordinator.retry(message)
        self._show_text(text)
        if retry:
            self._queue_capture()

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
        service.context_listening = self.settings_open

    def set_settings_open(self, opened):
        self.coordinator.invalidate()
        self._capture_ticket += 1
        self.settings_open = opened
        self._diagnostic_test = False
        self.shortcuts.cancel()
        self.screen.cancel()
        if self.thread:
            self.thread.service.set_paused(opened)
            self.thread.service.context_listening = opened
        self._show_text("Configurações abertas. Diga Hey Jarvis e depois ajuda, salvar configurações ou fechar configurações." if opened else "Diga hey jarvis ou use o botão para começar.")

    def test_audio(self, transcription=False):
        if not self.settings_open or not self.thread or not self.thread.isRunning():
            self.diagnostic_result.emit("O serviço de voz não está disponível. Salve os ajustes e tente novamente.")
            return
        if transcription:
            self._diagnostic_test = self.thread.service.request_capture(diagnostic=True)
            self.diagnostic_result.emit("Diga uma frase. Nenhum comando será executado." if self._diagnostic_test else "Aguarde o serviço ficar disponível e tente novamente.")
        else:
            self._diagnostic_test = True
            self.thread.service.cancel_interaction()
            self.thread.service.request_audio_test()
            generation = self.coordinator.generation
            QTimer.singleShot(5100, lambda: self._finish_meter(generation))
            self.diagnostic_result.emit("Teste de captação por 5 segundos. Observe o nível de áudio.")

    def _finish_meter(self, generation):
        if generation == self.coordinator.generation:
            self._diagnostic_test = False

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
            generation = self.shortcuts.generation
            self._queue_capture(lambda: self.shortcuts.active and self.shortcuts.generation == generation)

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
        self.coordinator.invalidate()
        self._configure_service()
        self._last_result_id = -1
        source = self.thread
        self.thread.event_received.connect(lambda event: self._receive(event) if self.thread is source else None)
        self.thread.finished.connect(lambda: self._finished() if self.thread is source else None)
        self.current_event = VoiceEvent(State.PREPARING, State.PREPARING.value)
        self._voice_event = self.current_event
        self._screen_text = None
        self._present()
        self.thread.start()
        if not self._screen_started:
            self._screen_started = True
            self.screen.start()
            values = self.preferences.effective
            self.screen.session.max_depth = values["max_depth"]
            self.screen.session.max_elements = values["max_elements"]

    def _receive(self, event):
        # Uma resposta na fila do Qt pode pertencer à interação já cancelada.
        if (event is self._last_received_event or event.interaction_id < self.thread.service.snapshot.interaction_id
                or event.state is State.STOPPED):
            return
        if self.closing and event.state is not State.STOPPING:
            return
        self._last_received_event = event
        self._voice_event = event
        scroll_silence = (event.state is State.ERROR and event.reason in (Reason.NO_SPEECH, Reason.EMPTY_TRANSCRIPT)
                          and self.screen.session.scroll_continuing is True)
        if scroll_silence:
            self._voice_event = replace(event, state=State.IDLE, reason=None, is_final=False)
        if event.state is State.ERROR and event.reason in (Reason.CONNECTION, Reason.MICROPHONE, Reason.MODEL):
            self._recovering = True
        if event.state is State.IDLE and self._recovering:
            self._screen_text = "Reconhecimento disponível novamente."
            self._recovering = False
        if event.state is State.PROCESSING:
            if not self._processing_timer.isActive():
                self._processing_timer.start()
        else:
            self._processing_timer.stop()
        if event.state is State.ERROR and not scroll_silence:
            self.last_error = event.text
        if self.settings_open and (self._diagnostic_test or event.diagnostic):
            if event.state is State.ERROR or (self._diagnostic_test and event.state is State.RESULT):
                self._diagnostic_test = False
                self.diagnostic_result.emit(event.text)
            self._show_text("Teste de áudio: nenhum comando será executado.")
            return
        if event.state is State.LISTENING:
            self._capture_ticket += 1
            self.coordinator.listening()
            self._ignore_result = False
            if not self.shortcuts.active and not self.settings_open:
                self.screen.session.begin_capture()
            if not self.screen.session.active and not self.shortcuts.active and not self.settings_open and not self.coordinator.retries:
                self._screen_text = None
        if event.state is State.ERROR and event.reason in (Reason.NO_SPEECH, Reason.EMPTY_TRANSCRIPT):
            if self.screen.session.scroll_continuing is True:
                self.screen.session.end_scroll()
                return
            self._recognition_failed("Não detectei sua fala. Vou ouvir mais uma vez." if event.reason is Reason.NO_SPEECH else
                                     "Não consegui entender sua fala. Tente novamente.")
            return
        if event.state is State.ERROR:
            self._screen_text = None
            self.coordinator.invalidate()
            self._capture_ticket += 1
        if event.state is State.ERROR and self.screen.session.active:
            self.screen.cancel("Não foi recebida uma resposta válida. Repita o comando.")
        if event.state is State.ERROR and self.shortcuts.active:
            self.shortcuts.cancel("Nenhuma resposta válida para o atalho. Repita executar atalho.")
        if event.state is State.ERROR:
            self._screen_text = event.text
        if event.state is State.RESULT and event.is_final:
            if event.interaction_id <= self._last_result_id:
                return
            self._last_result_id = event.interaction_id
            if self._ignore_result:
                return
            route = self.coordinator.route(event.text, **self._context())
            if route == "help":
                self.show_help()
                return
            if route == "settings":
                self.settings_command_requested.emit(event.text)
                return
            if route == "open_settings":
                self.settings_requested.emit()
                return
            if route == "shortcut":
                self.shortcuts.handle(event.text)
                return
            if route == "command":
                if self.shortcuts.handle(event.text):
                    self.screen.session.target = None
                    return
            if self.screen.session.handle(event.text):
                return
            self._recognition_failed("Não reconheci esse comando. Tente ‘mostrar números’, ‘digitar’ ou ‘ajuda’. Ouvindo novamente…")
            return
        self.current_event = replace(event, text=self._screen_text) if self._screen_text else event
        self._present()

    def _screen_changed(self, view):
        if self.closing:
            return
        self._screen_text = view.text
        self.markers_changed.emit(view.markers)
        self.current_event = replace(self._voice_event, text=view.text)
        self._present()
        if view.capture:
            self._request_response()

    def _request_response(self):
        if not self.closing and self.screen.session.active and self.thread:
            token, state = self.screen.session.token, self.screen.session.state
            timeout = self.screen.session.capture_timeout
            self._queue_capture(lambda: self.screen.session.active and self.screen.session.token == token
                                and self.screen.session.state == state, start_timeout=5 if timeout == 5 else None)

    def _invalidate_voice(self):
        self.coordinator.invalidate()
        self._capture_ticket += 1
        self._ignore_result = True
        if self.thread:
            self.thread.service.cancel_interaction()

    def toggle(self):
        if self.closing or self.settings_open:
            return
        self.coordinator.invalidate()
        self._capture_ticket += 1
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
        self.coordinator.invalidate()
        self._capture_ticket += 1
        self._processing_timer.stop()
        self.shortcuts.cancel()
        self.markers_changed.emit(())
        self.screen.shutdown()
        self.current_event = VoiceEvent(State.STOPPING, State.STOPPING.value)
        self._voice_event = self.current_event
        self._screen_text = None
        self._present()
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
            self._voice_event = self.current_event
            self._screen_text = None
            self._present()


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
