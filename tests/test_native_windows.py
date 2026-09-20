"""Opt-in: cria somente janelas descartáveis e nunca fecha aplicativos do usuário."""
import os
from pathlib import Path
import subprocess
import sys
import time
import psutil

import pytest

from host_agent.running import RunningApps

pytestmark = pytest.mark.skipif(sys.platform != "win32" or os.getenv("JARVIS_NATIVE_TESTS") != "1",
                               reason="Aceitação Windows opt-in com janelas descartáveis")


@pytest.mark.parametrize("mode", ["normal", "unsaved"])
def test_native_close_disposable_application(mode):
    from host_agent.window_backends import WindowsBackend
    program = Path(__file__).parent / "fixtures/native_window.py"
    executable = Path(sys.executable).with_name("pythonw.exe")
    process = subprocess.Popen([str(executable), "-B", str(program), mode], shell=False)
    manager = RunningApps(WindowsBackend(), wait_seconds=0.4)
    try:
        until = time.monotonic() + 10
        target = None
        while time.monotonic() < until:
            targets = manager.inventory({})
            owned = {process.pid, *(p.pid for p in psutil.Process(process.pid).children(recursive=True))}
            target = next((a for a in targets if a.processes and {p.pid for p in a.processes} <= owned), None)
            if target:
                break
            time.sleep(0.1)
        assert target, manager.error
        closed = manager.close_normal(target, lambda: False, lambda call: call())
        if mode == "normal":
            assert closed
        else:
            assert not closed and process.poll() is None
            manager.inventory({})
            # O diálogo é preservado até o teste autorizar explicitamente a força.
            current = manager.prepare(target.id)
            assert manager.force(current, lambda: False, lambda call: call())
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()  # Handle do subprocesso descartável criado por este teste.
            process.wait(timeout=5)


def test_native_open_discovered_shortcut(tmp_path):
    import uuid
    import pythoncom
    import win32com.client
    from host_agent.platform_apps import executable_entry, launch
    from host_agent.window_backends import WindowsBackend
    program = (Path(__file__).parent / "fixtures/native_window.py").resolve()
    executable = Path(sys.executable).with_name("pythonw.exe")
    token = "jarvis-native-" + uuid.uuid4().hex
    arguments = subprocess.list2cmdline(["-B", str(program), "normal", token])
    shortcut = tmp_path / "Editor fora do catalogo antigo.lnk"
    owned = []
    pythoncom.CoInitialize()
    try:
        link = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(shortcut))
        link.TargetPath = str(executable)
        link.Arguments = arguments
        link.Save()
        application = executable_entry(shortcut.stem, link.TargetPath, link.Arguments, source="desktop", shortcut=shortcut)
        launch(application)
        manager = RunningApps(WindowsBackend(), wait_seconds=2)
        until = time.monotonic() + 10
        target = None
        while time.monotonic() < until:
            owned = [p for p in psutil.process_iter(["cmdline"]) if p.info["cmdline"] and p.info["cmdline"][-1] == token]
            pids = {p.pid for p in owned}
            target = next((a for a in manager.inventory({application.id: application})
                           if a.processes and {p.pid for p in a.processes} <= pids), None)
            if target:
                break
            time.sleep(0.1)
        assert target
        assert manager.close_normal(target, lambda: False, lambda call: call())
    finally:
        for process in owned:
            if process.is_running():
                process.kill()
        pythoncom.CoUninitialize()
