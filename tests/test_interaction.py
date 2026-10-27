"""Regressões de diálogo e recuperação sem Whisper nem ações externas."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, Signal

from screen_access.backend import Element, Snapshot, Window
from screen_access.session import ActionState, ScreenSession
from screen_access.scroll import ScrollRequest, ScrollTargets, ScrollOutcome
from speakbar.commands import settings_command, portuguese_number, HELP_COMMANDS, RESERVED
from speakbar.controller import VoiceController
from speakbar.interaction import InteractionCoordinator
from speakbar.preferences import PreferencesService
from speakbar.settings_window import SettingsWindow
from speakbar.shortcuts import Application, ShortcutConversation, ShortcutService
from wakeword.detect_microphone_service import VoiceService
from wakeword.events import State, Reason
from wakeword.streaming_service import StreamingVoiceService, NoSpeech
from wakeword.transcription_client import Transcript, TranscriptionError

APP = QApplication.instance() or QApplication([])


class FakeScreen(QObject):
    changed = Signal(object)
    invalidated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.executor = Mock()
        self.session = ScreenSession(self.executor, self.changed.emit)
        self.start = Mock()
        self.shutdown = Mock()

    def cancel(self, text="Ação cancelada."):
        self.session.cancel(text)
        self.invalidated.emit()


class ControllerFixture:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.controller = VoiceController(screen_factory=FakeScreen,
                                          preferences=PreferencesService(Path(self.directory.name) / "settings.json"))
        self.service = VoiceService()
        self.service.capture_ready = True
        self.controller.thread = Mock()
        self.controller.thread.service = self.service

    def tearDown(self):
        self.controller.shutdown()
        APP.processEvents()
        self.directory.cleanup()

    def event(self, state, text=None, reason=None):
        if state is State.LISTENING:
            self.service._interaction_id += 1
            self.service.trigger_requested.clear()
        self.service._publish(state, text, reason=reason)
        self.controller._receive(self.service.snapshot)

class ControllerTest(ControllerFixture, unittest.TestCase):
    def begin_scroll(self):
        session = self.controller.screen.session
        window = Window("100", 12345, "Teste", (0, 0, 800, 600))
        session.last_window = window
        self.event(State.LISTENING)
        self.event(State.RESULT, "role para baixo")
        snapshot = Snapshot("scroll", window, (Element(1, "Página", "PaneControl", (0, 0, 800, 600), "scroll"),))
        session.receive(session.token, "prepare_scroll", ScrollTargets(snapshot, 1))
        return session

    def test_scroll_continuation_requests_five_seconds_and_silence_ends_without_retry(self):
        session = self.begin_scroll()
        with patch.object(self.service, "request_capture", return_value=True) as capture:
            session.receive(session.token, "scroll", ScrollOutcome.MOVED)
            capture.assert_called_once_with(start_timeout=5)
            self.assertIn("sem repetir Hey Jarvis", self.controller.presentation.guidance)
        self.event(State.LISTENING)
        self.event(State.ERROR, reason=Reason.NO_SPEECH)
        self.assertFalse(session.active)
        self.assertIn("Rolagem encerrada", self.controller.text)
        self.assertIs(self.controller.state, State.IDLE)
        self.assertEqual(self.controller.last_error, "")
        self.assertEqual(self.controller.coordinator.retries, 0)
        self.assertFalse(self.service.trigger_requested.is_set())

    def test_scroll_empty_transcript_ends_and_settings_block_scroll(self):
        session = self.begin_scroll()
        session.receive(session.token, "scroll", ScrollOutcome.END)
        self.event(State.LISTENING)
        self.event(State.ERROR, reason=Reason.EMPTY_TRANSCRIPT)
        self.assertFalse(session.active)
        self.assertIn("Rolagem encerrada", self.controller.text)
        self.controller.set_settings_open(True)
        commands = Mock()
        self.controller.settings_command_requested.connect(commands)
        count = self.controller.screen.executor.submit.call_count
        self.event(State.LISTENING)
        self.event(State.RESULT, "role para baixo")
        commands.assert_called_once_with("role para baixo")
        self.assertEqual(self.controller.screen.executor.submit.call_count, count)

    def test_unknown_command_retries_once_then_waits_for_activation(self):
        self.event(State.LISTENING)
        self.event(State.RESULT, "abacaxi")
        self.assertIn("Não reconheci", self.controller.text)
        self.assertTrue(self.service.trigger_requested.is_set())
        self.event(State.LISTENING)
        self.event(State.RESULT, "abacaxi")
        self.assertIn("Não consegui concluir", self.controller.text)
        self.assertFalse(self.service.trigger_requested.is_set())
        self.event(State.LISTENING)
        self.event(State.RESULT, "abacaxi")
        self.assertTrue(self.service.trigger_requested.is_set())
        self.controller.screen.executor.submit.assert_not_called()

    def test_typed_silence_and_empty_transcript_share_retry_budget(self):
        self.event(State.LISTENING)
        self.event(State.ERROR, reason=Reason.NO_SPEECH)
        self.assertIn("Não detectei", self.controller.text)
        self.controller._receive(self.service.snapshot)
        self.assertEqual(self.controller.coordinator.retries, 1)
        self.event(State.LISTENING)
        self.event(State.ERROR, reason=Reason.EMPTY_TRANSCRIPT)
        self.assertIn("Não consegui concluir", self.controller.text)
        self.assertFalse(self.service.trigger_requested.is_set())

    def test_state_and_prompt_are_separate_and_slow_processing_is_explicit(self):
        self.controller._show_text("Diga confirmar ou cancelar.")
        self.controller.screen.session.state = ActionState.CONFIRMING
        self.event(State.LISTENING)
        self.assertEqual(self.controller.presentation.message, "Ouvindo…")
        self.assertIn("confirmar", self.controller.presentation.guidance)
        self.event(State.PROCESSING)
        self.controller._slow_processing()
        self.assertIn("Ainda processando", self.controller.presentation.message)
        self.event(State.RESULT, "cancelar")
        self.assertFalse(self.controller._processing_timer.isActive())

    def test_final_is_not_lost_when_ready_event_overtakes_gui_delivery(self):
        self.event(State.LISTENING)
        self.service._publish(State.RESULT, "ajuda")
        final = self.service.snapshot
        self.service.capture_ready = True
        self.service._publish(State.IDLE)
        self.controller._receive(final)
        self.controller._receive(self.service.snapshot)
        self.assertIn("mostrar números", self.controller.presentation.message)
        self.assertTrue(self.controller.presentation.capture_available)
        self.assertIn("Hey Jarvis", self.controller.presentation.guidance)

    def test_automatic_capture_waits_until_microphone_is_ready(self):
        self.service.capture_ready = False
        with patch.object(self.service, "request_capture", return_value=True) as request, patch("speakbar.controller.QTimer.singleShot") as timer:
            self.controller._queue_capture()
            request.assert_not_called()
            self.service.capture_ready = True
            timer.call_args.args[1]()
            request.assert_called_once()

    def test_cancel_invalidates_deferred_capture(self):
        self.service._publish(State.PROCESSING)
        with patch.object(self.service, "request_capture", return_value=False) as request, patch("speakbar.controller.QTimer.singleShot") as timer:
            self.controller._queue_capture()
            self.controller._invalidate_voice()
            timer.call_args.args[1]()
            self.assertEqual(request.call_count, 1)

    def test_pending_capture_cannot_cross_service_restart(self):
        self.service.capture_ready = False
        with patch("speakbar.controller.QTimer.singleShot") as timer:
            self.controller._queue_capture()
            replacement = VoiceService()
            replacement.capture_ready = True
            self.controller.thread = Mock()
            self.controller.thread.service = replacement
            timer.call_args.args[1]()
            self.assertFalse(replacement.trigger_requested.is_set())
            self.assertFalse(self.service.trigger_requested.is_set())

    def test_retry_rechecks_acceptance_without_toggling(self):
        with patch.object(self.service, "request_capture", side_effect=[False, True]) as request, patch("speakbar.controller.QTimer.singleShot") as timer:
            self.controller._queue_capture()
            timer.call_args.args[1]()
            self.assertEqual(request.call_count, 2)

    def test_technical_error_does_not_keep_previous_prompt(self):
        self.controller._show_text("Texto antigo")
        self.event(State.ERROR, "Reconectando…", Reason.CONNECTION)
        self.assertEqual(self.controller.presentation.message, "Reconectando…")
        self.event(State.PREPARING)
        self.service.capture_ready = True
        self.event(State.IDLE)
        self.assertIn("disponível novamente", self.controller.presentation.message)

    def test_settings_context_never_routes_external_actions(self):
        self.controller.set_settings_open(True)
        commands = Mock()
        self.controller.settings_command_requested.connect(commands)
        self.event(State.LISTENING)
        self.event(State.RESULT, "deletar")
        commands.assert_called_once_with("deletar")
        self.controller.screen.executor.submit.assert_not_called()
        self.assertTrue(self.service.context_listening)

    def test_diagnostic_cannot_save_settings_or_execute_commands(self):
        self.controller.set_settings_open(True)
        self.service._publish(State.IDLE)
        commands = Mock()
        self.controller.settings_command_requested.connect(commands)
        self.controller.test_audio(transcription=True)
        self.event(State.LISTENING)
        self.event(State.RESULT, "salvar configurações")
        commands.assert_not_called()
        self.assertFalse(self.controller._diagnostic_test)

    def test_diagnostic_provenance_survives_connection_error(self):
        self.controller.set_settings_open(True)
        commands = Mock()
        self.controller.settings_command_requested.connect(commands)
        self.controller._diagnostic_test = True
        self.event(State.ERROR, "Reconectando…", Reason.CONNECTION)
        self.assertFalse(self.controller._diagnostic_test)
        # A captura de diagnóstico aceita antes da falha começa após a recuperação.
        self.service.current_diagnostic = True
        self.event(State.LISTENING)
        self.event(State.RESULT, "salvar configurações")
        commands.assert_not_called()
        self.controller.screen.executor.submit.assert_not_called()


class SettingsVoiceTest(ControllerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.controller.set_settings_open(True)
        self.dialog = SettingsWindow(self.controller)
        self.dialog.show()
        APP.processEvents()

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        APP.processEvents()
        super().tearDown()

    def test_voice_updates_draft_and_saves_through_existing_path(self):
        self.dialog.handle_voice("ir para aparência e acessibilidade")
        self.assertEqual(self.dialog.navigation.currentRow(), 2)
        self.dialog.handle_voice("tamanho do texto vinte e dois")
        self.dialog.handle_voice("ativar alto contraste")
        self.dialog.handle_voice("limiar de ativação zero vírgula três")
        self.assertEqual(self.dialog.text_size.value(), 22)
        self.assertTrue(self.dialog.contrast.isChecked())
        self.assertFalse(self.controller.preferences.path.exists())
        self.dialog.handle_voice("salvar configurações")
        self.assertEqual(self.controller.preferences.effective["text_size"], 22)
        self.assertTrue(self.controller.preferences.effective["high_contrast"])
        self.assertFalse(self.dialog.isVisible())

    def test_invalid_value_does_not_silently_clamp(self):
        for phrase in ("tamanho do texto 100", "tamanho do texto 22,5", "limiar de ativação zero"):
            self.dialog.handle_voice(phrase)
            self.assertIn("Informe um valor", self.dialog.status.text())
        self.assertEqual(self.dialog.text_size.value(), 18)

    def test_close_discards_draft(self):
        self.dialog.handle_voice("tamanho dos números dezoito")
        self.dialog.handle_voice("fechar configurações")
        self.assertFalse(self.dialog.isVisible())
        self.assertFalse(self.controller.preferences.path.exists())

    def test_all_sections_and_timing_controls_accept_spoken_values(self):
        for phrase, section in (("ir para atalhos", 0), ("ir para voz e microfone", 1),
                                ("ir para aparência e acessibilidade", 2), ("ir para ajuda e diagnóstico", 3)):
            self.dialog.handle_voice(phrase)
            self.assertEqual(self.dialog.navigation.currentRow(), section)
        for phrase, key, value in (("tempo de silêncio três segundos", "silence_seconds", 3),
                                   ("tempo para começar a falar quinze segundos", "start_timeout", 15),
                                   ("duração máxima trinta segundos", "max_seconds", 30),
                                   ("limiar de ativação zero vírgula zero cinco", "detection_threshold", .05)):
            self.dialog.handle_voice(phrase)
            self.assertEqual(self.dialog.voice_controls[key].value(), value)
        self.dialog.handle_voice("desativar lembrar posição")
        self.assertFalse(self.dialog.remember.isChecked())
        self.assertFalse(self.controller.preferences.path.exists())

    def install_apps(self, *names):
        apps = []
        for name in names:
            path = Path(self.directory.name) / f"{name}.app"
            path.mkdir()
            apps.append(Application(name, path))
        self.controller.shortcut_service.catalog = lambda: apps
        return apps

    def test_spoken_shortcut_command_keeps_what_was_said(self):
        self.assertEqual(settings_command("Criar atalho para o Safari."), ("create_shortcut", "o Safari"))
        self.assertEqual(settings_command("novo atalho para Captura de Tela"), ("create_shortcut", "Captura de Tela"))
        self.assertEqual(settings_command("Frase do atalho: abrir o navegador!"), ("shortcut_phrase", "abrir o navegador"))

    def test_create_app_shortcut_by_voice_changes_phrase_and_saves(self):
        safari, = self.install_apps("Safari")
        self.dialog.handle_voice("ir para voz e microfone")
        self.dialog.handle_voice("criar atalho para o Safari")
        self.assertEqual(self.dialog.navigation.currentRow(), 0)
        self.assertEqual([(item["name"], item["phrase"], item["destination"]) for item in self.dialog.draft],
                         [("Safari", "abrir Safari", str(safari.path))])
        self.assertIn("abrir Safari", self.controller.presentation.message)
        self.dialog.handle_voice("frase do atalho abrir meu navegador")
        self.assertEqual([item["phrase"] for item in self.dialog.draft], ["abrir meu navegador"])
        self.assertFalse(self.controller.preferences.path.exists())
        self.dialog.handle_voice("salvar configurações")
        self.assertEqual([item["phrase"] for item in self.controller.preferences.shortcuts], ["abrir meu navegador"])

    def test_similar_apps_are_chosen_by_number_with_one_retry(self):
        self.install_apps("Captura de Imagem", "Captura de Tela")
        self.dialog.handle_voice("criar atalho para captura")
        self.assertIn("1 Captura de Imagem, 2 Captura de Tela", self.dialog.status.text())
        self.assertTrue(self.service.trigger_requested.is_set())
        self.dialog.handle_voice("dois")
        self.assertEqual([item["name"] for item in self.dialog.draft], ["Captura de Tela"])
        self.dialog.handle_voice("criar atalho para captura")
        self.dialog.handle_voice("banana")
        self.assertIsNotNone(self.dialog.pending_apps)
        self.dialog.handle_voice("abacaxi")
        self.assertIsNone(self.dialog.pending_apps)
        self.dialog.handle_voice("criar atalho para captura")
        self.dialog.handle_voice("cancelar")
        self.assertIn("Cadastro cancelado", self.dialog.status.text())
        self.assertEqual(len(self.dialog.draft), 1)

    def test_invalid_spoken_shortcuts_are_explained(self):
        self.install_apps("Safari")
        self.dialog.handle_voice("frase do atalho abrir navegador")
        self.assertIn("Primeiro diga criar atalho", self.dialog.status.text())
        self.dialog.handle_voice("criar atalho para planilhas")
        self.assertIn("Não encontrei o aplicativo “planilhas”", self.dialog.status.text())
        self.dialog.handle_voice("criar atalho para Safari")
        self.dialog.handle_voice("frase do atalho abrir configurações")
        self.assertIn("reservada", self.dialog.status.text())
        self.dialog.handle_voice("criar atalho para Safari")
        self.assertIn("Já existe um atalho", self.dialog.status.text())
        self.assertEqual([item["phrase"] for item in self.dialog.draft], ["abrir Safari"])


class DialogueTest(unittest.TestCase):
    def test_settings_numbers_and_sections(self):
        for text, number in (("0,3", .3), ("zero vírgula três", .3), ("zero vírgula zero cinco", .05),
                             ("vinte e dois", 22), ("um ponto vinte e cinco", 1.25)):
            self.assertEqual(portuguese_number(text), number)
        self.assertEqual(settings_command("Tempo de silêncio três segundos."), ("silence_seconds", 3))
        self.assertEqual(settings_command("Limiar de ativação 0,3."), ("detection_threshold", .3))
        self.assertIsNone(settings_command("clique em salvar"))
        for text in ("zero vírgula", "talvez", "um dois", "zero vírgula ponto cinco"):
            with self.assertRaises(ValueError):
                portuguese_number(text)

    def test_help_does_not_interpret_literal_dictation(self):
        coordinator = InteractionCoordinator()
        for phrase in HELP_COMMANDS:
            self.assertEqual(coordinator.route(phrase, screen_state="dictating"), "screen")
            self.assertEqual(coordinator.route(phrase, screen_state="confirming"), "help")
            self.assertIn(phrase, RESERVED)

    def test_screen_retry_and_help_preserve_absolute_deadline(self):
        views, executor = [], Mock()
        session = ScreenSession(executor, views.append)
        window = Window("test", 123, "Teste", (0, 0, 800, 600))
        snapshot = Snapshot("test", window, (Element(1, "Pesquisar", "ButtonControl", (0, 0, 10, 10), "invoke"),))
        session.last_window = window
        session.begin_capture()
        with patch("screen_access.session.time.monotonic", return_value=100):
            session.handle("mostrar números")
            session.receive(session.token, "scan", snapshot)
        deadline = session.deadline
        with patch("screen_access.session.time.monotonic", return_value=110):
            session.handle("99")
            session.help("Diga um número ou cancelar.")
        self.assertEqual(session.deadline, deadline)
        self.assertEqual(session.retry_count, 1)
        with patch("screen_access.session.time.monotonic", return_value=120):
            session.handle("um")
            self.assertEqual(session.retry_count, 0)
            session.handle("talvez")
            session.handle("confirmar")
        self.assertEqual(executor.submit.call_count, 2)
        session.handle("confirmar")
        self.assertEqual(executor.submit.call_count, 2)

    def test_expired_step_cannot_be_extended_by_retry(self):
        session = ScreenSession(Mock(), lambda view: None)
        session.state = ActionState.CONFIRMING
        session.deadline = 10
        with patch("screen_access.session.time.monotonic", return_value=11):
            session.retry("Tente novamente")
        self.assertFalse(session.active)

    def test_invalid_direct_number_enters_choice_and_requires_confirmation(self):
        views, executor = [], Mock()
        session = ScreenSession(executor, views.append)
        window = Window("test", 123, "Teste", (0, 0, 800, 600))
        snapshot = Snapshot("test", window, (Element(1, "Pesquisar", "ButtonControl", (0, 0, 10, 10), "invoke"),))
        session.last_window = window
        session.begin_capture()
        session.handle("clique no número nove")
        session.receive(session.token, "scan", snapshot)
        self.assertEqual(session.state, ActionState.CHOOSING)
        self.assertEqual(session.retry_count, 1)
        self.assertTrue(views[-1].capture)
        session.handle("um")
        self.assertEqual(session.state, ActionState.CONFIRMING)
        self.assertEqual(executor.submit.call_count, 1)
        session.handle("confirmar")
        self.assertEqual(executor.submit.call_count, 2)

    def test_shortcut_retries_resolution_without_relaunching_action(self):
        launch = Mock()
        item = dict(id="1", name="Teste", phrase="abrir teste", kind="site", destination="https://example.com", enabled=True)
        conversation = ShortcutConversation(ShortcutService([item], launcher=launch))
        conversation.handle("executar atalho inexistente")
        APP.processEvents()
        deadline = conversation.deadline
        self.assertTrue(conversation.active)
        conversation.handle("abrir teste")
        APP.processEvents()
        launch.assert_called_once()
        self.assertFalse(conversation.active)
        self.assertEqual(conversation.deadline, deadline)


class RecoveryTest(unittest.TestCase):
    def test_backoff_delays_and_stop_are_cooperative(self):
        for reason, attempt, delay in ((Reason.CONNECTION, 0, 1), (Reason.CONNECTION, 1, 2),
                                       (Reason.CONNECTION, 2, 5), (Reason.CONNECTION, 3, 10),
                                       (Reason.CONNECTION, 4, 15), (Reason.CONNECTION, 9, 15),
                                       (Reason.MICROPHONE, 0, 5)):
            service = VoiceService()
            # O relógio passa diretamente do início para além do prazo.
            with patch("wakeword.detect_microphone_service.time.monotonic", side_effect=[100, 100, 100 + delay + .1]):
                service._recover(reason, attempt)
            self.assertEqual(service.snapshot.reason, reason)
            service.stop()
            self.assertFalse(service._recover(reason, attempt))

    def test_streaming_failure_uses_backoff_and_keeps_selected_microphone(self):
        service = StreamingVoiceService(model_factory=lambda cancelled: Mock(), vad_factory=lambda cancelled: Mock(),
                                        input_device={"name": "Meu microfone", "api": "WASAPI"})
        service._prepare_connection = Mock(side_effect=TranscriptionError("offline"))
        attempts = []
        def recover(reason, attempt):
            attempts.append((reason, attempt))
            if len(attempts) == 3:
                service.stop()
        service._recover = recover
        service.run()
        self.assertEqual(attempts, [(Reason.CONNECTION, 0), (Reason.CONNECTION, 1), (Reason.CONNECTION, 2)])
        self.assertEqual(service.input_device["name"], "Meu microfone")

    def test_streaming_no_speech_and_empty_result_are_typed(self):
        events = []
        service = StreamingVoiceService(events.append, model_factory=lambda cancelled: Mock(), vad_factory=lambda cancelled: Mock())
        service._prepare_connection = Mock()
        service.connection = Mock()
        service.connection.finish.return_value = Transcript("", "1")
        calls = []
        def capture(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise NoSpeech("silêncio")
            if len(calls) == 2:
                service.connection = Mock()
                service.connection.sequence = 1
                service.connection.finish.return_value = Transcript("", "1")
                return None, 0
            service.stop()
            from wakeword.events import Cancelled
            raise Cancelled()
        service._capture = capture
        service.run()
        self.assertEqual([event.reason for event in events if event.state is State.ERROR],
                         [Reason.NO_SPEECH, Reason.EMPTY_TRANSCRIPT])


class LegacyEndpointTest(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_is_passed_as_callable_to_threadpool(self):
        # Isolar a rota permite verificá-la no host sem instalar dependências dos containers.
        import ast
        from types import SimpleNamespace
        source = Path(__file__).resolve().parents[1] / "api" / "controller.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        route = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "post_transcribe")
        route.decorator_list = []
        send = Mock(return_value=SimpleNamespace(id="test", status="PENDING"))
        async def threadpool(function, *args, **kwargs):
            send.assert_not_called()
            self.assertIs(function, send)
            return function(*args, **kwargs)
        namespace = dict(TranscriptionRequest=object, uuid=lambda: "test", celery_app=SimpleNamespace(send_task=send),
                         run_in_threadpool=threadpool)
        exec(compile(ast.Module(body=[route], type_ignores=[]), str(source), "exec"), namespace)
        result = await namespace["post_transcribe"](SimpleNamespace(url="https://example.com/audio.wav", language="pt"))
        send.assert_called_once_with("task.transcribe", args=["https://example.com/audio.wav", "pt"], task_id="test")
        self.assertEqual(result, dict(task_id="test", status="PENDING"))


if __name__ == "__main__":
    unittest.main()
