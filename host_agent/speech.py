"""Uma thread possui o microfone; rede e coordenação consomem cópias limitadas."""
from collections import deque
from contextlib import ExitStack
import logging
import math
import os
from pathlib import Path
import queue
import threading
import time
import wave

import numpy as np

from wakeword.detect_microphone_service import CHUNK, RATE, _create_audio, _read_chunk
from wakeword.events import Cancelled, check_cancelled
from wakeword.speech_gate import SpeechConfig, SpeechGate
from wakeword.streaming_client import StreamingClient
from wakeword.transcription_client import TranscriptionClient, TranscriptionError


class Microphone:
    def __init__(self, detector=None, on_interrupt=lambda: None, audio_factory=_create_audio):
        self.detector = detector
        self.on_interrupt = on_interrupt
        self.audio_factory = audio_factory
        self.frames = queue.Queue(maxsize=32)
        self.stop_requested = threading.Event()
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.armed = False
        self.reset_detector = True
        self.capturing = False
        self.error = None
        self.safety_error = None
        self.thread = threading.Thread(target=self._run, name="jarvis-microphone")

    def start(self):
        self.thread.start()

    def arm(self, enabled):
        with self.lock:
            self.armed = enabled
            self.reset_detector = True

    def begin_capture(self):
        with self.lock:
            self._drain()
            self.capturing = True

    def end_capture(self):
        with self.lock:
            self.capturing = False
            self._drain()

    def _drain(self):
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                return

    def _offer(self, pcm):
        with self.lock:
            if self.frames.full():
                if self.capturing:
                    raise OSError("A captura atrasou. Tente novamente.")
                self.frames.get_nowait()
            self.frames.put_nowait(pcm)

    def _run(self):
        try:
            import pyaudio
            with ExitStack() as resources:
                audio = self.audio_factory()
                resources.callback(audio.terminate)
                stream = audio.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                                    input=True, frames_per_buffer=CHUNK)
                resources.callback(stream.close)
                resources.callback(stream.stop_stream)
                self.ready.set()
                while not self.stop_requested.is_set():
                    pcm = _read_chunk(stream, self.stop_requested.is_set)
                    with self.lock:
                        reset, armed = self.reset_detector, self.armed
                        self.reset_detector = False
                    if self.detector is not None:
                        try:
                            if reset:
                                self.detector.reset()
                            if armed and self.detector.feed(pcm):
                                self.on_interrupt()
                        except Exception:
                            logging.exception("Detector de interrupção indisponível")
                            self.safety_error = "Detector de interrupção indisponível. Ações desabilitadas."
                            self.detector = None
                            self.on_interrupt()
                    self._offer(pcm)
        except Cancelled:
            pass
        except Exception as exc:
            self.error = exc
            self.on_interrupt()
        finally:
            self.ready.set()

    def check(self):
        if self.error:
            raise OSError(str(self.error))
        if self.ready.is_set() and not self.thread.is_alive() and not self.stop_requested.is_set():
            raise OSError("Microfone encerrado")

    def read(self, cancelled):
        while True:
            check_cancelled(cancelled)
            self.check()
            try:
                return self.frames.get(timeout=0.025)
            except queue.Empty:
                pass

    def close(self):
        self.stop_requested.set()
        if self.thread.ident is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=6)


class _CancelView:
    def __init__(self, cancelled):
        self.cancelled = cancelled

    def is_set(self):
        return self.cancelled()

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while not self.is_set() and time.monotonic() < end:
            time.sleep(min(0.025, max(0, end - time.monotonic())))
        return self.is_set()


class SpeechInput:
    def __init__(self, microphone, vad, mode=None, stream_factory=StreamingClient, batch_factory=TranscriptionClient):
        self.microphone = microphone
        self.vad = vad
        self.mode = mode or os.getenv("TRANSCRIPTION_MODE", "stream").lower()
        if self.mode not in ("stream", "batch"):
            raise ValueError("TRANSCRIPTION_MODE deve ser stream ou batch")
        self.config = SpeechConfig.from_environment()
        self.stream_factory = stream_factory
        self.batch_factory = batch_factory
        self.capture_id = 0
        self.prepared = None

    def prepare(self, cancelled):
        """Prepara a próxima escuta antes da pergunta, evitando perder a resposta."""
        if self.mode == "stream" and self.prepared is None:
            client = self.stream_factory()
            try:
                client.prepare(cancelled)
                self.prepared = client
            except BaseException:
                client.close()
                raise

    def close(self):
        if self.prepared is not None:
            self.prepared.close()
            self.prepared = None

    def capture(self, cancelled, on_partial, on_listening, on_processing, timeout=None):
        deadline = time.monotonic() + timeout if timeout is not None else float("inf")

        def bounded_cancel():
            self.microphone.check()
            return cancelled() or time.monotonic() >= deadline

        self.capture_id += 1
        try:
            if self.mode == "batch":
                return self._batch(bounded_cancel, on_listening, on_processing)
            config = (SpeechConfig(self.config.silence_seconds, 30, 30) if timeout is not None else self.config)
            return self._stream(bounded_cancel, on_partial, on_listening, on_processing, config)
        except Cancelled:
            check_cancelled(cancelled)
            return ""  # Expiração da confirmação, nunca autorização.
        finally:
            self.microphone.end_capture()

    def _stream(self, cancelled, on_partial, on_listening, on_processing, config=None):
        client = self.prepared or self.stream_factory()
        was_prepared = self.prepared is not None
        self.prepared = None
        try:
            if not was_prepared:
                client.prepare(cancelled)
            check_cancelled(cancelled)
            client.begin(self.capture_id, on_partial)
            self.vad.reset_states()
            gate = SpeechGate(config or self.config)
            prefix = deque(maxlen=3)
            sent = False
            first_send = None
            self.microphone.begin_capture()
            on_listening()
            while True:
                pcm = self.microphone.read(cancelled)
                client.check()
                probability = float(self.vad.predict(np.frombuffer(pcm, dtype="<i2"), frame_size=640))
                outcome = gate.feed(probability, len(pcm) // 2)
                if not sent:
                    prefix.append(pcm)
                    if gate.has_speech:
                        first_send = time.monotonic()
                        sent = True
                        for block in prefix:
                            client.send_audio(block)
                        prefix.clear()
                else:
                    client.send_audio(pcm)
                if first_send and time.monotonic() - max(first_send, client.last_response) > 15:
                    raise TranscriptionError("O reconhecimento está demorando demais. Tente novamente.")
                if outcome == "no_speech":
                    return ""
                if outcome == "finish":
                    self.microphone.end_capture()
                    on_processing()
                    result = client.finish(cancelled)
                    check_cancelled(cancelled)
                    return result.text
        finally:
            client.close()

    def _batch(self, cancelled, on_listening, on_processing):
        seconds = int(os.getenv("RECORD_SECONDS", "5"))
        if not 0 < seconds <= 30:
            raise ValueError("RECORD_SECONDS deve estar entre 1 e 30")
        self.microphone.begin_capture()
        on_listening()
        frames = [self.microphone.read(cancelled) for _ in range(math.ceil(RATE * seconds / CHUNK))]
        self.microphone.end_capture()
        check_cancelled(cancelled)
        directory = Path(os.getenv("RECORDINGS_DIR", "./recordings"))
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{time.time_ns()}.wav"
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(RATE)
            output.writeframes(b"".join(frames)[:RATE * seconds * 2])
        on_processing()
        client = self.batch_factory()
        try:
            return client.transcribe(str(path), _CancelView(cancelled)).text
        finally:
            client.close()
