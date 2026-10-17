"""UIA real em uma janela descartável; não rola outros aplicativos."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading


def fixture():
    # Controles Win32 padrão expõem ScrollPattern; QScrollArea não o oferece nesta versão do Qt.
    import ctypes as c
    from ctypes import wintypes as w
    user32, kernel32 = c.windll.user32, c.windll.kernel32
    callback = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
    class WindowClass(c.Structure):
        _fields_ = [("style", w.UINT), ("proc", callback), ("class_extra", c.c_int), ("window_extra", c.c_int),
                    ("instance", w.HINSTANCE), ("icon", w.HICON), ("cursor", w.HANDLE),
                    ("background", w.HBRUSH), ("menu", w.LPCWSTR), ("name", w.LPCWSTR)]
    kernel32.GetModuleHandleW.argtypes, kernel32.GetModuleHandleW.restype = [w.LPCWSTR], w.HMODULE
    user32.DefWindowProcW.argtypes, user32.DefWindowProcW.restype = [w.HWND, w.UINT, w.WPARAM, w.LPARAM], c.c_ssize_t
    user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, c.c_int, c.c_int,
                                      c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
    user32.CreateWindowExW.restype = w.HWND
    user32.SendMessageW.argtypes, user32.SendMessageW.restype = [w.HWND, w.UINT, w.WPARAM, w.LPARAM], c.c_ssize_t
    user32.RegisterClassW.argtypes, user32.RegisterClassW.restype = [c.POINTER(WindowClass)], w.ATOM
    user32.DestroyWindow.argtypes = [w.HWND]
    user32.SetFocus.argtypes = [w.HWND]
    user32.SetTimer.argtypes = [w.HWND, c.c_size_t, w.UINT, c.c_void_p]
    @callback
    def procedure(handle, message, wp, lp):
        if message == 0x0113:  # WM_TIMER
            user32.DestroyWindow(handle)
        if message == 2:  # WM_DESTROY
            user32.PostQuitMessage(0)
        return user32.DefWindowProcW(handle, message, wp, lp)
    instance = kernel32.GetModuleHandleW(None)
    name = "JarvisScrollFixture"
    cls = WindowClass(0, procedure, 0, 0, instance, None, None, 6, None, name)
    assert user32.RegisterClassW(c.byref(cls))
    root = user32.CreateWindowExW(0, name, "JARVIS Scroll Test", 0x10CF0000, 100, 100, 700, 420, None, None, instance, None)
    assert root
    for index, title in enumerate(("Left panel", "Right panel")):
        area = user32.CreateWindowExW(0, "LISTBOX", title, 0x50200101, 10 + index * 335, 10, 320, 350, root, None, instance, None)
        assert area
        for row in range(120):
            label = c.c_wchar_p(f"{title} item {row+1}")
            user32.SendMessageW(area, 0x0180, 0, c.cast(label, c.c_void_p).value)  # LB_ADDSTRING
    user32.SetFocus(root)
    user32.SetTimer(root, 1, 25000, None)
    print(json.dumps(dict(handle=int(root), pid=os.getpid())), flush=True)
    message = w.MSG()
    while user32.GetMessageW(c.byref(message), None, 0, 0) > 0:
        user32.TranslateMessage(c.byref(message))
        user32.DispatchMessageW(c.byref(message))


def main():
    if "--fixture" in sys.argv:
        fixture()
        return
    if sys.platform != "win32":
        raise SystemExit("Esta verificação requer Windows.")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from screen_access.backend import Window
    from screen_access.windows import WindowsBackend
    from screen_access.scroll import ScrollRequest, ScrollOutcome
    child = subprocess.Popen([sys.executable, __file__, "--fixture"], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    messages = queue.Queue()
    def read():
        for line in child.stdout:
            messages.put(json.loads(line))
    threading.Thread(target=read, daemon=True).start()
    try:
        ready = messages.get(timeout=8)
        with WindowsBackend() as backend:
            backend.require_foreground = False  # Apenas a fixture identificada, sem tomar foco de outro app.
            native = backend.api.ControlFromHandle(ready["handle"])
            window = Window(str(ready["handle"]), ready["pid"], native.Name, backend.frame(native))
            targets = backend.prepare_scroll(window)
            print(json.dumps(dict(areas=[dict(name=e.name, role=e.role, frame=e.frame) for e in targets.snapshot.elements]), ensure_ascii=True), flush=True)
            assert len(targets.snapshot.elements) >= 2, "As duas areas da fixture precisam expor ScrollPattern."
            number = 1
            for intensity in ("small", "normal", "page"):
                result = backend.scroll(targets.snapshot, number, ScrollRequest("down", intensity))
                assert result is ScrollOutcome.MOVED, result
            assert backend.scroll(targets.snapshot, number, ScrollRequest("up", "small")) is ScrollOutcome.MOVED
            pattern = backend.scroll_references[number][0].GetPattern(10004)
            pattern.SetScrollPercent(-1, 100, waitTime=.1)
            assert backend.scroll(targets.snapshot, number, ScrollRequest("down")) is ScrollOutcome.END
            pattern.SetScrollPercent(-1, 0, waitTime=.1)
            assert backend.scroll(targets.snapshot, number, ScrollRequest("up")) is ScrollOutcome.START
            before = pattern.VerticalScrollPercent
            try:
                backend.scroll(targets.snapshot, number, ScrollRequest("down"), cancelled=lambda: True)
                raise AssertionError("Cancelamento nao foi respeitado.")
            except Exception as exc:
                from screen_access.backend import ScreenError
                assert isinstance(exc, ScreenError), exc
            assert pattern.VerticalScrollPercent == before
            print("Windows nativo: areas, tres intensidades, direcoes, limites e cancelamento: OK", flush=True)
    finally:
        child.terminate()
        child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()


if __name__ == "__main__":
    main()
