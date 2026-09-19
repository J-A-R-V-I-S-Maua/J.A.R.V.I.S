"""Máquina de estados da interação. A autorização pertence ao host, nunca ao LLM."""
import logging
import time
import uuid

import numpy as np

from contracts.commands import CommandContext, Decision, InterpretRequest, normalize, validate_proposal
from wakeword.detect_microphone_service import VoiceService, DETECTION_THRESHOLD
from wakeword.events import Cancelled, State, check_cancelled
from .client import CommandClient
from .executor import WindowsExecutor, confirmation
from .interrupt import InterruptDetector
from .speech import Microphone, SpeechInput
from .tts import WindowsSpeech

ACTIVE = {State.LISTENING, State.PROCESSING, State.INTERPRETING, State.SPEAKING,
          State.CONFIRMING, State.EXECUTING}
YES = {"sim", "confirmo", "pode executar"}
NO = {"nao", "nao execute", "cancelar", "parar", "pare", "cancelar comando", "parar agora"}


class AssistantService(VoiceService):
    def __init__(self, on_event=lambda event: None, *, command_client=None, executor=None,
                 speaker=None, speech_input=None, microphone_factory=Microphone,
                 detector_factory=InterruptDetector, speaker_factory=WindowsSpeech, **kwargs):
        super().__init__(on_event, **kwargs)
        self.command_client = command_client or CommandClient()
        self.executor = executor
        self.speaker = speaker
        self.speech_input = speech_input
        self.microphone_factory = microphone_factory
        self.detector_factory = detector_factory
        self.speaker_factory = speaker_factory
        self.microphone = None
        self.action_error = None
        self.session_id = uuid.uuid4().hex
        self._executed = set()
        self._capture_generation = 0

    def cancel(self):
        with self._lock:
            if self.snapshot.state in ACTIVE:
                self._interaction_id += 1
                self.cancel_requested.set()
                self.trigger_requested.clear()
                self._publish(State.CANCELLING)

    def toggle(self):
        if self.snapshot.state in ACTIVE:
            self.cancel()
        else:
            super().toggle()

    def _cancelled(self):
        if self.microphone:
            self.microphone.check()
        return self.stop_requested.is_set() or self.cancel_requested.is_set()

    def _say(self, text, interaction_id):
        check_cancelled(self._cancelled)
        self._publish(State.SPEAKING, text, interaction_id=interaction_id)
        self.speaker.speak(text, self._cancelled)
        check_cancelled(self._cancelled)
        # Descarta a cauda acústica antes de abrir a captura de confirmação.
        end = time.monotonic() + 0.3
        while time.monotonic() < end:
            check_cancelled(self._cancelled)
            time.sleep(0.02)

    def _listen(self, interaction_id, *, confirmation_mode=False):
        self._capture_generation += 1
        generation = self._capture_generation
        listening_state = State.CONFIRMING if confirmation_mode else State.LISTENING
        finishing = False

        def partial(text, sequence):
            with self._lock:
                if generation != self._capture_generation or self.cancel_requested.is_set():
                    return
                self._publish(State.PROCESSING if finishing else listening_state, text,
                              interaction_id=interaction_id, sequence=sequence, is_final=False)

        def processing():
            nonlocal finishing
            finishing = True
            self._publish(State.PROCESSING, "Finalizando resposta…" if confirmation_mode else "Finalizando transcrição…",
                          interaction_id=interaction_id)

        try:
            return self.speech_input.capture(self._cancelled, partial,
                lambda: self._publish(listening_state, interaction_id=interaction_id), processing,
                timeout=30 if confirmation_mode else None)
        finally:
            self._capture_generation += 1

    def handle_command(self, text, interaction_id):
        """Chamado somente com texto final. Injetável em testes sem dispositivos."""
        if not text.strip():
            self._publish(State.RESULT, "Nenhuma fala reconhecida. Tente novamente.", interaction_id=interaction_id)
            return
        if normalize(text) in NO:
            raise Cancelled()
        if self.action_error or (self.microphone and self.microphone.safety_error):
            self._publish(State.ERROR, f"{text}\n{self.action_error or self.microphone.safety_error}", interaction_id=interaction_id)
            return
        context = CommandContext(available_apps=list(self.executor.apps))
        request = InterpretRequest(interaction_id=f"{self.session_id}:{interaction_id}", text=text, context=context)
        for attempt in range(2):
            check_cancelled(self._cancelled)
            self._publish(State.INTERPRETING, interaction_id=interaction_id)
            decision = self.command_client.interpret(request, self._cancelled)
            check_cancelled(self._cancelled)
            # Não confiar nem mesmo em um cliente/adaptador que devolva objetos já construídos.
            decision = Decision.model_validate(decision.model_dump(include={"status", "action", "message"}))
            validate_proposal(decision, request)
            if decision.status != "clarification":
                break
            if attempt:
                self._say("Não consegui definir a ação. Faça um novo pedido com mais detalhes.", interaction_id)
                self._publish(State.RESULT, "Pedido encerrado sem executar.", interaction_id=interaction_id)
                return
            self.speech_input.prepare(self._cancelled)
            self._say(decision.message, interaction_id)
            answer = self._listen(interaction_id, confirmation_mode=True)
            if not answer or normalize(answer) in NO:
                raise Cancelled()
            request = InterpretRequest(interaction_id=request.interaction_id, text=answer,
                context=CommandContext(available_apps=list(self.executor.apps), original_text=text,
                                       clarification_question=decision.message))
        if decision.status == "unsupported":
            self._say(decision.message, interaction_id)
            self._publish(State.RESULT, decision.message, interaction_id=interaction_id)
            return
        action = decision.action
        for attempt in range(2):
            self.speech_input.prepare(self._cancelled)
            prompt = confirmation(action)
            if attempt:
                prompt = "Não reconheci sua confirmação. " + prompt
            self._say(prompt, interaction_id)
            answer = normalize(self._listen(interaction_id, confirmation_mode=True))
            check_cancelled(self._cancelled)
            if answer in NO:
                raise Cancelled()
            if answer in YES:
                self._dispatch(action, request, interaction_id)
                return
        self._publish(State.RESULT, "Sem confirmação. Nenhuma ação executada.", interaction_id=interaction_id)

    def _dispatch(self, action, request, interaction_id):
        with self._lock:
            check_cancelled(self._cancelled)
            if interaction_id != self._interaction_id or request.interaction_id in self._executed:
                return
            if self.action_error or (self.microphone and self.microphone.safety_error):
                raise RuntimeError("Ações indisponíveis sem confirmação falada e detector de interrupção.")
            validate_proposal(Decision(status="action", action=action), request)
            self._executed.add(request.interaction_id)
            # Uma interação por vez: só é necessário lembrar o último despacho.
            self._executed.intersection_update({request.interaction_id})
            self._publish(State.EXECUTING, interaction_id=interaction_id)
            result = self.executor.execute(action)
        self._publish(State.RESULT, result, interaction_id=interaction_id)

    def run(self):
        model = None
        try:
            from wakeword.model_loader import create_vad
            self._publish(State.PREPARING)
            model = self.model_factory(self.stop_requested.is_set)
            vad = create_vad(self.stop_requested.is_set)
            self.executor = self.executor or WindowsExecutor()
            detector = None
            try:
                detector = self.detector_factory(self.stop_requested.is_set)
                self.speaker = self.speaker or self.speaker_factory()
            except Cancelled:
                raise
            except Exception as exc:
                logging.exception("Ações desabilitadas")
                self.action_error = f"Ações desabilitadas: {exc} Reinicie após corrigir a preparação."
            while not self.stop_requested.is_set():
                try:
                    self.microphone = self.microphone_factory(detector=detector, on_interrupt=self.cancel)
                    self.microphone.start()
                    while not self.microphone.ready.wait(0.025):
                        check_cancelled(self.stop_requested.is_set)
                    self.microphone.check()
                    self.speech_input = SpeechInput(self.microphone, vad)
                    self._publish(State.IDLE, self.action_error or State.IDLE.value)
                    while not self.stop_requested.is_set():
                        self.microphone.arm(False)
                        model.reset()
                        while not self.trigger_requested.is_set():
                            pcm = self.microphone.read(self.stop_requested.is_set)
                            if model.predict(np.frombuffer(pcm, dtype="<i2"), timing=False).get("hey_jarvis", 0) >= DETECTION_THRESHOLD:
                                break
                        with self._lock:
                            check_cancelled(self.stop_requested.is_set)
                            self.trigger_requested.clear()
                            self.cancel_requested.clear()
                            self._interaction_id += 1
                            interaction_id = self._interaction_id
                        self.microphone.arm(True)
                        try:
                            self._publish(State.PROCESSING, "Preparando captura…", interaction_id=interaction_id)
                            text = self._listen(interaction_id)
                            check_cancelled(self._cancelled)
                            self.handle_command(text, interaction_id)
                        except Cancelled:
                            if self.stop_requested.is_set():
                                raise
                            self._publish(State.RESULT, "Interação cancelada.")
                        except Exception as exc:
                            logging.exception("Interação não concluída")
                            self._publish(State.ERROR, f"{exc} Diga hey jarvis ou clique para tentar novamente.", interaction_id=interaction_id)
                        finally:
                            self.speech_input.close()
                            self.microphone.end_capture()
                        self.microphone.check()
                except Cancelled:
                    break
                except Exception:
                    logging.exception("Falha no microfone")
                    self._publish(State.ERROR, "Microfone indisponível. Verifique o dispositivo e clique para tentar novamente.")
                    self._wait_for_retry()
                finally:
                    if self.microphone:
                        self.microphone.close()
                        self.microphone = None
        except (Cancelled, KeyboardInterrupt):
            self.stop()
        except Exception as exc:
            logging.exception("Falha ao preparar assistente")
            self._publish(State.ERROR, str(exc))
        finally:
            if self.microphone:
                self.microphone.close()
            if self.speaker:
                self.speaker.close()
            self._publish(State.STOPPED)
