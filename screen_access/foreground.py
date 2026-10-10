"""Histórico de foco Win32; callbacks contêm somente HWNDs, nunca objetos UIA."""
import ctypes
from ctypes import wintypes
from .backend import ScreenError


class ForegroundTracker:
    def __init__(self):
        self.handle = None
        self.user32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                          wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)

        def changed(hook, event, window, object_id, child_id, thread, timestamp):
            if window:
                self.handle = window

        self.callback = callback_type(changed)
        self.user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
                                               callback_type, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        self.user32.SetWinEventHook.restype = wintypes.HANDLE
        self.user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        # EVENT_SYSTEM_FOREGROUND, OUTOFCONTEXT | SKIPOWNPROCESS.
        self.hook = self.user32.SetWinEventHook(3, 3, None, self.callback, 0, 0, 2)
        if not self.hook:
            raise ScreenError("Não foi possível acompanhar a janela em foco.")

    def pump(self):
        message = wintypes.MSG()
        while self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            self.user32.TranslateMessage(ctypes.byref(message))
            self.user32.DispatchMessageW(ctypes.byref(message))

    def close(self):
        if self.hook:
            self.user32.UnhookWinEvent(self.hook)
            self.hook = None
