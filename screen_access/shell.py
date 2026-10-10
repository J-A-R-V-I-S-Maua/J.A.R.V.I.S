"""Localiza hosts do desktop e barras de tarefas sem percorrer outros aplicativos."""
import ctypes
from ctypes import wintypes

DESKTOP_CLASSES = ("Progman", "WorkerW", "#32769")
TASKBAR_CLASSES = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")


def shell_handles():
    user32 = ctypes.windll.user32
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
    desktops, taskbars = [], []

    def class_name(hwnd):
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    def collect(hwnd, data):
        name = class_name(hwnd)
        if name in TASKBAR_CLASSES:
            taskbars.append(hwnd)
        elif name in DESKTOP_CLASSES[:2]:
            # Windows pode aninhar SHELLDLL_DefView sob um WorkerW intermediário.
            found = []

            def child(child_hwnd, unused):
                if class_name(child_hwnd) == "SHELLDLL_DefView":
                    found.append(child_hwnd)
                return True

            child_callback = callback_type(child)
            user32.EnumChildWindows(hwnd, child_callback, 0)
            if found:
                desktops.append(hwnd)
        return True

    callback = callback_type(collect)
    user32.EnumWindows(callback, 0)
    return tuple(desktops), tuple(taskbars)
