import json
import threading
import time

import pytest

from host_agent.interrupt import InterruptDetector
from host_agent.speech import Microphone, SpeechInput
from wakeword.events import Cancelled


def test_stop_only_final_isolated_phrase():
    class Recognizer:
        complete = False
        text = "parar"
        def AcceptWaveform(self, pcm): return self.complete
        def Result(self): return json.dumps({"text": self.text})
    detector = InterruptDetector.__new__(InterruptDetector)
    detector.recognizer = Recognizer()
    assert not detector.feed(b"")
    detector.recognizer.complete = True
    assert detector.feed(b"")
    for text in ("pesquise como parar de fumar", "vou pesquisar cancelar no google posso executar", "nao cancelar"):
        detector.recognizer.text = text
        assert not detector.feed(b"")


def test_audio_queue_discards_idle_but_fails_on_capture_loss():
    microphone = Microphone()
    for _ in range(100):
        microphone._offer(b"idle")
    assert microphone.frames.qsize() == 32
    microphone.begin_capture()
    assert microphone.frames.empty()
    for _ in range(32):
        microphone._offer(b"speech")
    with pytest.raises(OSError):
        microphone._offer(b"lost")
    microphone.end_capture()
    assert microphone.frames.empty()


def test_single_audio_owner_and_interrupt_while_coordinator_busy():
    owners = []
    interrupted = threading.Event()
    class Stream:
        def get_read_available(self): return 1280
        def read(self, *args, **kwargs):
            owners.append(threading.get_ident())
            time.sleep(0.005)
            return b"\x00" * 2560
        def stop_stream(self): owners.append(threading.get_ident())
        def close(self): owners.append(threading.get_ident())
    class Audio:
        def open(self, **kwargs):
            owners.append(threading.get_ident())
            return Stream()
        def terminate(self): owners.append(threading.get_ident())
    class Detector:
        def reset(self): pass
        def feed(self, pcm): return True
    mic = Microphone(Detector(), interrupted.set, Audio)
    mic.arm(True)
    mic.start()
    assert interrupted.wait(2)
    mic.close()
    assert not mic.thread.is_alive()
    assert len(set(owners)) == 1 and owners[0] != threading.get_ident()


def test_final_timeout_does_not_promote_partial():
    class Mic:
        safety_error = None
        def check(self): pass
        def end_capture(self): pass
    speech = SpeechInput(Mic(), None)
    def unfinished(cancelled, partial, *args):
        partial("sim", 1)
        raise Cancelled()
    speech._stream = unfinished
    assert speech.capture(lambda: False, lambda *_: None, lambda: None, lambda: None, timeout=0) == ""


def test_old_portuguese_model_layout_reuses_cache(tmp_path, monkeypatch):
    import host_agent.interrupt as module
    cached = tmp_path / ".cache" / "jarvis" / module.MODEL_NAME
    cached.mkdir(parents=True)
    (cached / "final.mdl").write_bytes(b"model")
    monkeypatch.delenv("INTERRUPT_MODEL_DIR", raising=False)
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    assert module.ensure_model() == cached


def test_confirmation_connection_prepared_once_and_closed_on_cancel():
    calls = []
    class Stream:
        def prepare(self, cancelled): calls.append("prepare")
        def close(self): calls.append("close")
    speech = SpeechInput(None, None, mode="stream", stream_factory=Stream)
    speech.prepare(lambda: False)
    speech.prepare(lambda: False)
    speech.close()
    assert calls == ["prepare", "close"]


def test_batch_preserves_wav_and_does_not_use_streaming(tmp_path, monkeypatch):
    import wave
    from wakeword.transcription_client import Transcript
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path))
    monkeypatch.setenv("RECORD_SECONDS", "1")
    events = []
    class Mic:
        def check(self): pass
        def begin_capture(self): events.append("begin")
        def end_capture(self): events.append("end")
        def read(self, cancelled): return bytes(2560)
    class Batch:
        def transcribe(self, path, cancel):
            with wave.open(path) as audio:
                assert audio.getnframes() == 16000 and audio.getsampwidth() == 2
            return Transcript("sim", "batch-task")
        def close(self): events.append("closed")
    def no_stream(): raise AssertionError("Streaming não deveria ser usado")
    speech = SpeechInput(Mic(), None, mode="batch", stream_factory=no_stream, batch_factory=Batch)
    assert speech.capture(lambda: False, lambda *_: None, lambda: None, lambda: None) == "sim"
    assert len(list(tmp_path.glob("*.wav"))) == 1
    assert "closed" in events
