"""Captura/VAD no host e transcrição incremental no backend."""
from collections import deque
import logging
import time

import numpy as np

from .detect_microphone_service import VoiceService, _read_chunk
from .events import Cancelled, State, check_cancelled
from .speech_gate import SpeechConfig, SpeechGate
from .streaming_client import StreamingClient
from .transcription_client import TranscriptionError


def _create_vad(cancelled):
    from .model_loader import create_vad
    return create_vad(cancelled)


class NoSpeech(TranscriptionError):
    """Uma interação sem voz não impede a próxima ativação."""


class StreamingVoiceService(VoiceService):
    def __init__(self, on_event=lambda event: None, *, stream_factory=StreamingClient,
                 vad_factory=_create_vad, speech_config=None, **kwargs):
        super().__init__(on_event, **kwargs)
        self.stream_factory = stream_factory
        self.vad_factory = vad_factory
        self.speech_config = speech_config
        self.connection = None
        self.vad = None
        self._partial_text = ""
        self._finishing = False

    def _check_ready(self):
        if self.connection:
            self.connection.check()

    def _prepare_connection(self):
        # A inferência cancelada pode ainda estar liberando a capacidade do backend.
        deadline = time.monotonic() + 15
        while True:
            check_cancelled(self.stop_requested.is_set)
            self.connection = self.stream_factory()
            try:
                self.connection.prepare(self.stop_requested.is_set)
                return
            except TranscriptionError:
                self.connection.close()
                self.connection = None
                if time.monotonic() >= deadline:
                    raise
                self.stop_requested.wait(0.25)

    def _partial(self, text, sequence, interaction_id):
        with self._lock:
            if interaction_id != self._interaction_id or self.cancel_requested.is_set():
                return
            self._partial_text = text
            state = State.PROCESSING if self._finishing else State.LISTENING
            self._publish(state, text, interaction_id=interaction_id,
                          sequence=sequence, is_final=False)

    def _stream_audio(self, stream, audio, interaction_id):
        self._partial_text = ""
        self._finishing = False
        gate = SpeechGate(self.speech_config)
        self.vad.reset_states()
        self.connection.begin(interaction_id, lambda text, seq: self._partial(text, seq, interaction_id))
        prefix = deque(maxlen=3)
        sent_speech = False
        first_send = None
        while True:
            check_cancelled(self.cancel_requested.is_set)
            self.connection.check()
            chunk = _read_chunk(stream, self.cancel_requested.is_set)
            probability = float(self.vad.predict(np.frombuffer(chunk, dtype="<i2"), frame_size=640))
            outcome = gate.feed(probability, len(chunk) // 2)
            if not sent_speech:
                prefix.append(chunk)
                if gate.has_speech:
                    sent_speech = True
                    first_send = time.monotonic()
                    for buffered in prefix:
                        self.connection.send_audio(buffered)
                    prefix.clear()
            else:
                self.connection.send_audio(chunk)
            if first_send is not None:
                last = max(first_send, self.connection.last_response)
                if time.monotonic() - last > 15:
                    raise TranscriptionError("O reconhecimento está demorando demais. Tente novamente.")
            if outcome == "no_speech":
                raise NoSpeech("Nenhuma fala reconhecida. Diga hey jarvis ou clique para tentar novamente.")
            if outcome == "finish":
                with self._lock:
                    self._finishing = True
                    self._publish(State.PROCESSING, self._partial_text or "Finalizando transcrição…",
                                  interaction_id=interaction_id)
                return None

    def run(self):
        model = None
        try:
            while not self.stop_requested.is_set():
                try:
                    if model is None or self.vad is None:
                        self._publish(State.PREPARING)
                        self.speech_config = self.speech_config or SpeechConfig.from_environment()
                        model = self.model_factory(self.stop_requested.is_set)
                        self.vad = self.vad_factory(self.stop_requested.is_set)
                    self._prepare_connection()
                    check_cancelled(self.stop_requested.is_set)
                    self.cancel_requested.clear()
                    _, interaction_id = self._capture(model, capture=self._stream_audio)
                    result = self.connection.finish(self.cancel_requested.is_set)
                    self._publish(State.RESULT if result.text else State.ERROR,
                                  result.text or "Nenhuma fala reconhecida. Tente novamente.",
                                  interaction_id=interaction_id, sequence=self.connection.sequence,
                                  is_final=bool(result.text))
                except Cancelled:
                    if self.stop_requested.is_set():
                        break
                    # _capture publica IDLE somente após preparar a próxima conexão
                    # e reabrir o microfone. Até lá, preservar CANCELLING.
                except NoSpeech as exc:
                    self._publish(State.ERROR, str(exc))
                except Exception as exc:
                    if self.stop_requested.is_set():
                        break
                    logging.exception("Falha no streaming")
                    message = str(exc) if isinstance(exc, TranscriptionError) else (
                        "Falha no microfone ou reconhecimento. Verifique o dispositivo e tente novamente.")
                    self._publish(State.ERROR, message)
                    if self.connection:
                        self.connection.close()
                        self.connection = None
                    self._wait_for_retry()
                    self._publish(State.PREPARING)
                finally:
                    if self.connection:
                        self.connection.close()
                        self.connection = None
        except KeyboardInterrupt:
            self.stop()
        finally:
            self._publish(State.STOPPED)
