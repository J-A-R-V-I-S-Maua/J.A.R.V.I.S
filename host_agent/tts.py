"""SAPI em uma única thread COM, com purge cooperativo e sem Qt."""
from concurrent.futures import Future
import queue
import threading
import os
import shutil
import subprocess
import sys

from wakeword.events import check_cancelled


class WindowsSpeech:
    def __init__(self):
        self.jobs = queue.Queue(maxsize=1)
        self.closing = threading.Event()
        self.ready = Future()
        self.thread = threading.Thread(target=self._run, name="jarvis-tts")
        self.thread.start()
        try:
            self.ready.result(timeout=10)
        except Exception:
            self.close()
            raise

    def _run(self):
        initialized = False
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            initialized = True
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            voices = voice.GetVoices("Language=416")
            if voices.Count == 0:
                raise RuntimeError("Instale uma voz PT-BR nas configurações de fala do Windows.")
            voice.Voice = voices.Item(0)
            self.ready.set_result(True)
            while not self.closing.is_set():
                try:
                    text, cancelled, done = self.jobs.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    check_cancelled(cancelled)
                    voice.Speak(text, 1 | 16)  # SVSFlagsAsync | SVSFIsNotXML
                    while not voice.WaitUntilDone(20):
                        check_cancelled(lambda: self.closing.is_set() or cancelled())
                    check_cancelled(cancelled)
                    done.set_result(None)
                except Exception as exc:
                    voice.Speak("", 1 | 2)  # Purge antes de aceitar outra fala.
                    done.set_exception(exc)
            voice.Speak("", 1 | 2)
        except Exception as exc:
            if not self.ready.done():
                self.ready.set_exception(exc)
        finally:
            if initialized:
                pythoncom.CoUninitialize()

    def speak(self, text, cancelled):
        check_cancelled(cancelled)
        if not self.thread.is_alive():
            raise RuntimeError("Síntese de fala indisponível")
        done = Future()
        self.jobs.put((text, cancelled, done), timeout=1)
        while not done.done():
            if not self.thread.is_alive():
                raise RuntimeError("Síntese de fala encerrada")
            threading.Event().wait(0.02)
        done.result()

    def close(self):
        self.closing.set()
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=10)


class ProcessSpeech:
    """Texto via stdin e processo exclusivo cancelável, independente de Qt."""
    def __init__(self, platform):
        self.closing = threading.Event()
        self.process = None
        self.lock = threading.Lock()
        if platform == "darwin":
            command = "/usr/bin/say"
            result = subprocess.run([command, "-v", "?"], capture_output=True, text=True, timeout=5, check=True)
            voices = [line.split("pt_BR", 1)[0].strip() for line in result.stdout.splitlines() if "pt_BR" in line]
            if not voices:
                raise RuntimeError("Instale uma voz PT-BR nas configurações de fala do macOS.")
            voice = os.getenv("TTS_VOICE", voices[0])
            if voice not in voices:
                raise RuntimeError("TTS_VOICE não corresponde a uma voz PT-BR instalada.")
            self.argv = [command, "-v", voice]
        else:
            command = shutil.which("espeak-ng")
            if not command:
                raise RuntimeError("Instale espeak-ng para habilitar confirmação falada no Linux.")
            self.argv = [command, "-v", "pt-br", "--stdin"]
            subprocess.run([command, "-v", "pt-br", "-q", "teste"], timeout=5, check=True, capture_output=True)

    def speak(self, text, cancelled):
        check_cancelled(lambda: self.closing.is_set() or cancelled())
        # say interpreta [[...]] como controle de fala; neutralizar metadados arbitrários.
        plain = text.replace("[[", " ").replace("]]", " ")
        with self.lock:
            self.process = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, shell=False)
            process = self.process
        try:
            process.stdin.write(plain.encode("utf-8"))
            process.stdin.close()
            while process.poll() is None:
                check_cancelled(lambda: self.closing.is_set() or cancelled())
                self.closing.wait(0.02)
            check_cancelled(cancelled)
            if process.returncode:
                raise RuntimeError("A síntese de voz falhou; ação cancelada.")
        finally:
            self._terminate(process)
            with self.lock:
                self.process = None

    @staticmethod
    def _terminate(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)

    def close(self):
        self.closing.set()
        with self.lock:
            if self.process:
                self._terminate(self.process)


def create_speech():
    return WindowsSpeech() if sys.platform == "win32" else ProcessSpeech(sys.platform)
