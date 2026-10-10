"""Executor único; consultas, referências e ações pertencem à mesma thread."""
import logging
import os
import queue
import threading
from .backend import ScreenError, get_backend


class ScreenExecutor:
    def __init__(self, callback, backend_factory=get_backend, require_foreground=True):
        self.callback = callback
        self.backend_factory = backend_factory
        self.require_foreground = require_foreground
        self.commands = queue.Queue()
        self.stopping = threading.Event()
        self.cancelled = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="screen-access", daemon=True)
        self.thread.start()

    def submit(self, token, kind, *args):
        self.cancelled.set()
        self.cancelled = threading.Event()
        self.commands.put((token, kind, args, self.cancelled))

    def cancel(self):
        self.cancelled.set()

    def stop(self):
        self.stopping.set()
        self.cancel()

    def emit(self, token, kind, data):
        if not self.stopping.is_set():
            try:
                self.callback(token, kind, data)
            except RuntimeError:
                logging.debug("Interface encerrada antes da resposta da tela", exc_info=True)

    def _run(self):
        try:
            with self.backend_factory() as backend:
                backend.require_foreground = self.require_foreground
                last = None
                while not self.stopping.is_set():
                    try:
                        foreground = getattr(backend, "tracked_foreground", backend.foreground)()
                        if foreground and hasattr(backend, "remember_focus"):
                            backend.remember_focus(foreground)
                        if foreground and foreground.pid != os.getpid() and foreground != last:
                            last = foreground
                            self.emit(0, "foreground", foreground)
                    except Exception:
                        logging.debug("Janela temporariamente indisponível", exc_info=True)
                    try:
                        token, kind, args, cancel = self.commands.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if cancel.is_set() or self.stopping.is_set():
                        continue
                    try:
                        if kind == "scan":
                            window, depth, limit, hidden = args
                            result = backend.scan(window or last, depth, limit, hidden, cancel.is_set)
                        elif kind in ("activate", "open_item", "context_menu", "press_enter", "clear_text"):
                            getattr(backend, kind)(*args, cancelled=lambda: cancel.is_set() or self.stopping.is_set())
                            result = None
                        elif kind == "prepare_dictation":
                            result = backend.prepare_dictation(*args)
                        elif kind == "dictate":
                            backend.dictate(*args, cancelled=lambda: cancel.is_set() or self.stopping.is_set())
                            result = None
                        elif kind == "validate":
                            snapshot, numbers = args
                            for number in numbers:
                                if cancel.is_set() or self.stopping.is_set():
                                    break
                                backend.validate(snapshot, number)
                            result = None
                        else:
                            raise ScreenError("Operação de tela desconhecida.")
                        if not cancel.is_set() and not self.stopping.is_set():
                            self.emit(token, kind, result)
                    except Exception as exc:
                        logging.debug("Falha de acesso à tela", exc_info=True)
                        if not cancel.is_set() and not self.stopping.is_set():
                            message = str(exc) if isinstance(exc, ScreenError) else (
                                "Controle indisponível. Verifique a janela e suas permissões.")
                            self.emit(token, "error", message)
        except Exception as exc:
            message = str(exc) if isinstance(exc, ScreenError) else "Acesso à tela indisponível. Instale screen_access/requirements.txt."
            self.emit(0, "unavailable", message)
        finally:
            self.emit(0, "stopped", None)
