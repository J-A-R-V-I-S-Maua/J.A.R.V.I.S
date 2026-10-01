import io
import subprocess
import threading
from types import SimpleNamespace

import pytest

from host_agent.tts import ProcessSpeech
from wakeword.events import Cancelled


class Input(io.BytesIO):
    def close(self):
        self.written = self.getvalue()
        super().close()


class Process:
    def __init__(self):
        self.stdin = Input()
        self.returncode = None
        self.terminated = False
    def poll(self):
        return self.returncode
    def terminate(self):
        self.terminated = True
        self.returncode = -15
    def wait(self, timeout):
        return self.returncode


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_portable_tts_plain_text_stdin_and_interrupt(monkeypatch, platform):
    import host_agent.tts as module
    monkeypatch.setattr(module.shutil, "which", lambda _: "/usr/bin/espeak-ng")
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="Luciana pt_BR # voz\n"))
    monkeypatch.delenv("TTS_VOICE", raising=False)
    process = Process()
    launches = []
    def launch(argv, **kwargs):
        assert kwargs["shell"] is False
        launches.append(argv)
        return process
    monkeypatch.setattr(module.subprocess, "Popen", launch)
    speaker = ProcessSpeech(platform)
    checks = iter([False, True])
    with pytest.raises(Cancelled):
        speaker.speak("Texto; $(comando) [[rate 999]]", lambda: next(checks, True))
    assert process.terminated
    assert b"$(comando)" in process.stdin.written and b"[[" not in process.stdin.written
    assert all("Texto" not in arg for arg in launches[0])
    speaker.close()


def test_missing_linux_tts_is_explicit(monkeypatch):
    import host_agent.tts as module
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="espeak-ng"):
        ProcessSpeech("linux")


def test_missing_portuguese_macos_voice_is_explicit(monkeypatch):
    import host_agent.tts as module
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="Alex en_US\n"))
    with pytest.raises(RuntimeError, match="PT-BR"):
        ProcessSpeech("darwin")
