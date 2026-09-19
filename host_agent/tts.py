"""SAPI em uma única thread COM, com purge cooperativo e sem Qt."""
from concurrent.futures import Future
import queue
import threading

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
