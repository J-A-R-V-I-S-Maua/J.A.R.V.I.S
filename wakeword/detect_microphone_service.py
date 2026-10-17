"""Serviço de voz do host. Um único loop possui o modelo e o microfone."""
from contextlib import ExitStack
import logging
import math
import os
from pathlib import Path
import threading
import time
import wave

from .events import Cancelled, State, VoiceEvent, Reason, check_cancelled
from .microphone import select_input_device
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
                 audio_factory=_create_audio, client_factory=TranscriptionClient,
                 input_device="environment", detection_threshold=DETECTION_THRESHOLD, on_level=lambda level: None,
                 on_devices=lambda devices: None):
        self.on_event = on_event
        self.model_factory = model_factory
        self.audio_factory = audio_factory
        self.client_factory = client_factory
        self.input_device = input_device
        self.detection_threshold = detection_threshold
        self.on_level = on_level
        self.on_devices = on_devices
        self.devices_requested = threading.Event()
        self.paused = threading.Event()
        self.context_listening = False
        self.diagnostic_capture = threading.Event()
        self.audio_test_until = 0
        self.current_diagnostic = False
        self.stop_requested = threading.Event()
        self.cancel_requested = threading.Event()
        self.trigger_requested = threading.Event()
        self._lock = threading.RLock()
        self._interaction_id = 0
        self.capture_ready = False
        self._next_start_timeout = None
        self.current_start_timeout = None
        self._published_at = time.monotonic()
        self._snapshot = VoiceEvent(State.PREPARING, State.PREPARING.value)

    @property
    def snapshot(self):
        with self._lock:
            return self._snapshot

    def _publish(self, state, text=None, *, interaction_id=None, task_id=None, sequence=0, is_final=False, reason=None):
        with self._lock:
            if interaction_id is not None and interaction_id != self._interaction_id:
                return
            if self.stop_requested.is_set() and state not in (State.STOPPING, State.STOPPED):
                return
            event = VoiceEvent(state, text or state.value, self._interaction_id, task_id,
                               sequence, is_final or state is State.RESULT, reason, self.capture_ready, self.current_diagnostic)
            now = time.monotonic()
            logging.info("voice transition=%s->%s interaction=%s reason=%s elapsed=%.3f",
                         self._snapshot.state.name, state.name, self._interaction_id,
                         reason.value if reason else None, now - self._published_at)
            self._published_at = now
            self._snapshot = event
            self.on_event(event)

    def set_paused(self, paused):
        with self._lock:
            self.cancel_interaction()
            self.diagnostic_capture.clear()
            self.audio_test_until = 0
            if paused:
                self.paused.set()
            else:
                self.paused.clear()

    def request_audio_test(self):
        self.audio_test_until = time.monotonic() + 5
        # Permite recuperar um serviço em ERROR sem executar comandos.
        if self.snapshot.state is State.ERROR:
            self.trigger_requested.set()

    def request_device_list(self):
        """A UI solicita dados; somente a thread de áudio consulta o PortAudio."""
        self.devices_requested.set()
        if self.snapshot.state is State.ERROR:
            self.trigger_requested.set()

    def _publish_devices(self, audio):
        from .microphone import input_devices
        self.devices_requested.clear()
        try:
            result = dict(devices=input_devices(audio), error=None)
        except Exception as exc:
            result = dict(devices=[], error=f"Não foi possível listar os microfones: {exc}")
        self.on_devices(result)

    def _enumerate_devices(self):
        # Chamado no loop antes de abrir uma captura, inclusive sem conexão ao backend.
        self.devices_requested.clear()
        audio = None
        try:
            audio = self.audio_factory()
            self._publish_devices(audio)
        except Exception as exc:
            self.on_devices(dict(devices=[], error=f"Não foi possível listar os microfones: {exc}"))
        finally:
            if audio is not None:
                audio.terminate()

    def request_capture(self, diagnostic=False, start_timeout=None):
        """Solicita uma captura sem alternar ou cancelar uma operação em andamento."""
        with self._lock:
            if start_timeout is not None and (isinstance(start_timeout, bool) or not isinstance(start_timeout, (int, float)) or not 0 < start_timeout <= 30):
                raise ValueError("O tempo de espera da captura deve estar entre 0 e 30 segundos.")
            if self.paused.is_set() and not self.context_listening and not diagnostic:
                return False
            if self.stop_requested.is_set() or self._snapshot.state in (
                    State.LISTENING, State.PROCESSING, State.CANCELLING, State.STOPPING, State.STOPPED):
                return False
            self._next_start_timeout = start_timeout
            self.trigger_requested.set()
            if diagnostic:
                self.diagnostic_capture.set()
            return True

    def cancel_interaction(self):
        with self._lock:
            self.trigger_requested.clear()
            self._next_start_timeout = None
            if self._snapshot.state in (State.LISTENING, State.PROCESSING):
                self._interaction_id += 1
                self.cancel_requested.set()
                self._publish(State.CANCELLING)

    def toggle(self):
        """Chamável por outra thread; nunca acessa o dispositivo ou o modelo."""
        with self._lock:
            if self.paused.is_set():
                return
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

    def _recover(self, reason, attempt=0):
        delay = (1, 2, 5, 10, 15)[min(attempt, 4)] if reason is Reason.CONNECTION else 5
        self.capture_ready = False
        message = {Reason.CONNECTION: "Reconectando…",
                   Reason.MICROPHONE: "Microfone indisponível. Tentando reconectar o dispositivo selecionado…",
                   Reason.MODEL: "Reconhecimento indisponível. Tentando preparar os modelos…"}[reason]
        self._publish(State.ERROR, message, reason=reason)
        deadline = time.monotonic() + delay
        while not self.stop_requested.is_set() and time.monotonic() < deadline:
            if self.trigger_requested.wait(min(0.1, max(0, deadline - time.monotonic()))):
                self.trigger_requested.clear()
                break
        return not self.stop_requested.is_set()

    def _check_ready(self):
        pass

    def _meter_only(self):
        """Teste sem conexão com o Whisper, sempre na thread dona do áudio."""
        import numpy as np
        import pyaudio
        with ExitStack() as resources:
            audio = self.audio_factory()
            resources.callback(audio.terminate)
            self._publish_devices(audio)
            stream = audio.open(format=pyaudio.paInt16, channels=CHANNELS, rate=RATE, input=True,
                                frames_per_buffer=CHUNK, input_device_index=select_input_device(audio, self.input_device))
            resources.callback(stream.close)
            resources.callback(stream.stop_stream)
            while self.paused.is_set() and time.monotonic() < self.audio_test_until:
                check_cancelled(self.stop_requested.is_set)
                chunk = _read_chunk(stream, self.stop_requested.is_set)
                samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
                self.on_level(min(100, int(float(np.sqrt(np.mean(samples * samples))) / 32768 * 500)))

    def _capture(self, model, capture=None):
        import numpy as np
        import pyaudio
        with ExitStack() as resources:
            check_cancelled(self.stop_requested.is_set)
            audio = self.audio_factory()
            resources.callback(audio.terminate)
            self._publish_devices(audio)
            stream = audio.open(format=pyaudio.paInt16, channels=CHANNELS, rate=RATE,
                                input=True, frames_per_buffer=CHUNK,
                                input_device_index=select_input_device(audio, self.input_device))
            resources.callback(stream.close)
            resources.callback(stream.stop_stream)
            model.reset()
            self.capture_ready = True
            self._publish(State.IDLE)
            while True:
                check_cancelled(self.stop_requested.is_set)
                if self.devices_requested.is_set():
                    self._publish_devices(audio)
                self._check_ready()
                manual = self.trigger_requested.is_set() or self.diagnostic_capture.is_set()
                if self.paused.is_set() and (not self.context_listening or time.monotonic() < self.audio_test_until) and not (manual and self.diagnostic_capture.is_set()):
                    chunk = _read_chunk(stream, self.stop_requested.is_set)
                    if time.monotonic() < self.audio_test_until:
                        samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
                        level = min(100, int(float(np.sqrt(np.mean(samples * samples))) / 32768 * 500))
                        self.on_level(level)
                    continue
                if not manual:
                    chunk = _read_chunk(stream, self.stop_requested.is_set)
                    prediction = model.predict(np.frombuffer(chunk, dtype=np.int16), timing=False)
                    if prediction.get("hey_jarvis", 0) < self.detection_threshold:
                        continue
                with self._lock:
                    check_cancelled(self.stop_requested.is_set)
                    if self.paused.is_set() and not self.context_listening and not self.diagnostic_capture.is_set():
                        continue
                    self.trigger_requested.clear()
                    self.current_start_timeout = self._next_start_timeout if manual else None
                    self._next_start_timeout = None
                    self.current_diagnostic = self.diagnostic_capture.is_set()
                    self.diagnostic_capture.clear()
                    self.cancel_requested.clear()
                    self._interaction_id += 1
                    interaction_id = self._interaction_id
                    self.capture_ready = False
                    self._publish(State.LISTENING)
                path = capture(stream, audio, interaction_id) if capture else record_audio(
                    min(RECORD_SECONDS, self.current_start_timeout) if self.current_start_timeout else RECORD_SECONDS, mic_stream=stream, audio_interface=audio,
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
                if self.devices_requested.is_set():
                    self._enumerate_devices()
                if self.paused.is_set() and time.monotonic() < self.audio_test_until:
                    try:
                        self._meter_only()
                    except Cancelled:
                        break
                    except Exception as exc:
                        self.audio_test_until = 0
                        self._publish(State.ERROR, f"Microfone indisponível: {exc}", reason=Reason.MICROPHONE)
                        self._recover(Reason.MICROPHONE)
                    continue
                if model is None:
                    self._publish(State.PREPARING)
                    try:
                        model = self.model_factory(self.stop_requested.is_set)
                        check_cancelled(self.stop_requested.is_set)
                    except Cancelled:
                        break
                    except Exception:
                        logging.exception("Falha ao preparar reconhecimento")
                        self._publish(State.ERROR, "Falha ao preparar reconhecimento. Tentando novamente…", reason=Reason.MODEL)
                        self._recover(Reason.MODEL)
                        continue
                try:
                    self.cancel_requested.clear()
                    path, interaction_id = self._capture(model)
                except Cancelled:
                    if self.stop_requested.is_set():
                        break
                    self._publish(State.IDLE)
                    continue
                except Exception as exc:
                    logging.exception("Falha na captura de áudio")
                    self._publish(State.ERROR, str(exc) if isinstance(exc, ValueError) else
                                  "Microfone indisponível. Tentando reconectar…", reason=Reason.MICROPHONE)
                    self._recover(Reason.MICROPHONE)
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
                                  interaction_id=interaction_id, task_id=transcript.task_id,
                                  reason=None if transcript.text else Reason.EMPTY_TRANSCRIPT)
                except Cancelled:
                    self._publish(State.IDLE)
                except (TranscriptionError, OSError) as exc:
                    logging.warning("Falha de transcrição: %s", exc)
                    message = str(exc) if isinstance(exc, TranscriptionError) else "Não foi possível ler o áudio. Tente novamente."
                    self._publish(State.ERROR, message, interaction_id=interaction_id, reason=Reason.CONNECTION)
                    self._recover(Reason.CONNECTION)
                finally:
                    if self.current_diagnostic:
                        try:
                            Path(path).unlink(missing_ok=True)
                        except OSError:
                            logging.exception("Não foi possível remover o áudio temporário de diagnóstico")
        except KeyboardInterrupt:
            self.stop()
        finally:
            if client is not None:
                client.close()
            self._publish(State.STOPPED)


def create_service(on_event=lambda event: None, **kwargs):
    mode = os.getenv("TRANSCRIPTION_MODE", "stream").lower()
    if mode == "batch":
        return VoiceService(on_event, **kwargs)
    if mode == "stream":
        from .streaming_service import StreamingVoiceService
        return StreamingVoiceService(on_event, **kwargs)
    raise ValueError("TRANSCRIPTION_MODE deve ser stream ou batch")


def start():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    def report(event):
        print(event.text, flush=True)
        if event.task_id:
            print(f"task_id={event.task_id}", flush=True)
    create_service(report).run()
