"""Serviço de voz do host. Um único loop possui o modelo e o microfone."""
from contextlib import ExitStack
import logging
import math
import os
from pathlib import Path
import threading
import time
import wave

from .events import Cancelled, State, VoiceEvent, check_cancelled
from .transcription_client import TranscriptionClient, TranscriptionError

RATE = 16000
CHANNELS = 1
CHUNK = 1280
RECORD_SECONDS = int(os.getenv("RECORD_SECONDS", "5"))
DETECTION_THRESHOLD = float(os.getenv("DETECTION_THRESHOLD", "0.3"))
RECORDINGS_DIR = os.getenv("RECORDINGS_DIR", "./recordings")


def _create_model(cancelled):
    from .model_loader import create_model
    return create_model(cancelled=cancelled)


def _create_audio():
    import pyaudio
    return pyaudio.PyAudio()


def _read_chunk(stream, cancelled):
    deadline = time.monotonic() + 5
    while stream.get_read_available() < CHUNK:
        check_cancelled(cancelled)
        if time.monotonic() >= deadline:
            raise OSError("O microfone deixou de fornecer áudio")
        time.sleep(0.01)
    check_cancelled(cancelled)
    return stream.read(CHUNK, exception_on_overflow=False)


def record_audio(seconds, *, mic_stream, audio_interface, cancelled=lambda: False):
    import pyaudio
    if seconds <= 0:
        raise ValueError("RECORD_SECONDS deve ser positivo")
    frames = [_read_chunk(mic_stream, cancelled) for _ in range(math.ceil(RATE * seconds / CHUNK))]
    check_cancelled(cancelled)
    directory = Path(RECORDINGS_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    filename = directory / f"{time.time_ns()}.wav"
    with wave.open(str(filename), "wb") as output:
        output.setnchannels(CHANNELS)
        sample_size = audio_interface.get_sample_size(pyaudio.paInt16)
        output.setsampwidth(sample_size)
        output.setframerate(RATE)
        output.writeframes(b"".join(frames)[:int(RATE * seconds) * sample_size])
    return str(filename)


class VoiceService:
    def __init__(self, on_event=lambda event: None, *, model_factory=_create_model,
                 audio_factory=_create_audio, client_factory=TranscriptionClient):
        self.on_event = on_event
        self.model_factory = model_factory
        self.audio_factory = audio_factory
        self.client_factory = client_factory
        self.stop_requested = threading.Event()
        self.cancel_requested = threading.Event()
        self.trigger_requested = threading.Event()
        self._lock = threading.RLock()
        self._interaction_id = 0
        self._snapshot = VoiceEvent(State.PREPARING, State.PREPARING.value)

    @property
    def snapshot(self):
        with self._lock:
            return self._snapshot

    def _publish(self, state, text=None, *, interaction_id=None, task_id=None):
        with self._lock:
            if interaction_id is not None and interaction_id != self._interaction_id:
                return
            if self.stop_requested.is_set() and state not in (State.STOPPING, State.STOPPED):
                return
            event = VoiceEvent(state, text or state.value, self._interaction_id, task_id)
            self._snapshot = event
            self.on_event(event)

    def toggle(self):
        """Chamável por outra thread; nunca acessa o dispositivo ou o modelo."""
        with self._lock:
            state = self._snapshot.state
            if state in (State.LISTENING, State.PROCESSING):
                self._interaction_id += 1
                self.cancel_requested.set()
                self.trigger_requested.clear()
                self._publish(State.CANCELLING)
            elif state in (State.IDLE, State.RESULT, State.ERROR):
                self.trigger_requested.set()

    def stop(self):
        with self._lock:
            if self.stop_requested.is_set():
                return
            self._interaction_id += 1
            self.stop_requested.set()
            self.cancel_requested.set()
            self.trigger_requested.set()
            self._publish(State.STOPPING)

    def _wait_for_retry(self):
        while not self.stop_requested.is_set():
            if self.trigger_requested.wait(0.1):
                self.trigger_requested.clear()
                return

    def _capture(self, model):
        import numpy as np
        import pyaudio
        with ExitStack() as resources:
            check_cancelled(self.stop_requested.is_set)
            audio = self.audio_factory()
            resources.callback(audio.terminate)
            stream = audio.open(format=pyaudio.paInt16, channels=CHANNELS, rate=RATE,
                                input=True, frames_per_buffer=CHUNK)
            resources.callback(stream.close)
            resources.callback(stream.stop_stream)
            model.reset()
            if self.snapshot.state in (State.PREPARING, State.CANCELLING):
                self._publish(State.IDLE)
            while True:
                check_cancelled(self.stop_requested.is_set)
                manual = self.trigger_requested.is_set()
                if not manual:
                    chunk = _read_chunk(stream, self.stop_requested.is_set)
                    prediction = model.predict(np.frombuffer(chunk, dtype=np.int16), timing=False)
                    if prediction.get("hey_jarvis", 0) < DETECTION_THRESHOLD:
                        continue
                with self._lock:
                    check_cancelled(self.stop_requested.is_set)
                    self.trigger_requested.clear()
                    self.cancel_requested.clear()
                    self._interaction_id += 1
                    interaction_id = self._interaction_id
                    self._publish(State.LISTENING)
                path = record_audio(RECORD_SECONDS, mic_stream=stream, audio_interface=audio,
                                    cancelled=self.cancel_requested.is_set)
                check_cancelled(self.cancel_requested.is_set)
                return path, interaction_id
        # ExitStack releases the device before the upload and polling begin.

    def run(self):
        model = None
        client = None
        try:
            client = self.client_factory()
            while not self.stop_requested.is_set():
                if model is None:
                    self._publish(State.PREPARING)
                    try:
                        model = self.model_factory(self.stop_requested.is_set)
                        check_cancelled(self.stop_requested.is_set)
                    except Cancelled:
                        break
                    except Exception:
                        logging.exception("Falha ao preparar reconhecimento")
                        self._publish(State.ERROR, "Falha ao preparar reconhecimento. Clique para tentar novamente.")
                        self._wait_for_retry()
                        continue
                try:
                    self.cancel_requested.clear()
                    path, interaction_id = self._capture(model)
                except Cancelled:
                    if self.stop_requested.is_set():
                        break
                    self._publish(State.IDLE)
                    continue
                except Exception:
                    logging.exception("Falha na captura de áudio")
                    self._publish(State.ERROR, "Microfone indisponível. Verifique o dispositivo e clique para tentar novamente.")
                    self._wait_for_retry()
                    self._publish(State.PREPARING)
                    continue
                try:
                    check_cancelled(self.cancel_requested.is_set)
                    self._publish(State.PROCESSING, interaction_id=interaction_id)
                    transcript = client.transcribe(
                        path, self.cancel_requested,
                        on_submitted=lambda task_id: self._publish(
                            State.PROCESSING, interaction_id=interaction_id, task_id=task_id),
                    )
                    check_cancelled(self.cancel_requested.is_set)
                    self._publish(State.RESULT if transcript.text else State.ERROR,
                                  transcript.text or "Nenhuma fala reconhecida. Tente novamente.",
                                  interaction_id=interaction_id, task_id=transcript.task_id)
                except Cancelled:
                    self._publish(State.IDLE)
                except (TranscriptionError, OSError) as exc:
                    logging.warning("Falha de transcrição: %s", exc)
                    message = str(exc) if isinstance(exc, TranscriptionError) else "Não foi possível ler o áudio. Tente novamente."
                    self._publish(State.ERROR, message, interaction_id=interaction_id)
        except KeyboardInterrupt:
            self.stop()
        finally:
            if client is not None:
                client.close()
            self._publish(State.STOPPED)


def start():
    def report(event):
        print(event.text, flush=True)
        if event.task_id:
            print(f"task_id={event.task_id}", flush=True)
    VoiceService(report).run()
