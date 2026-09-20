"""Solicitações nativas de fechar. Não usa atalhos globais nem nomes para matar processos."""
import json
import os
from pathlib import Path
import socket
import sys

import psutil

from .running import Window, eligible


def process_identity(ref):
    # Intérpretes podem hospedar aplicativos diferentes. Nunca agrupar apenas por exe.
    from .catalog import stable_id
    stem = Path(ref.executable).stem.lower()
    if stem.startswith(("python", "java", "dotnet", "electron", "rundll32")):
        try:
            command = psutil.Process(ref.pid).cmdline()
            return ref.executable + ":" + stable_id("\0".join(command))[4:]
        except psutil.Error:
            return ref.executable + f":pid:{ref.pid}:{ref.created}"
    return ref.executable


def force_checked(ref):
    # psutil.kill verifica reutilização de PID a partir da identidade deste objeto.
    process = psutil.Process(ref.pid)
    if (process.create_time() == ref.created and process.username() == ref.user and
            os.path.normcase(process.exe()) == ref.executable):
        process.kill()


PROTECTED = {"gnome-shell", "kwin_wayland", "kwin_x11", "plasmashell", "xfwm4", "mutter",
             "dwm.exe", "sihost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
             "textinputhost.exe", "searchhost.exe", "searchapp.exe", "runtimebroker.exe",
             "loginwindow", "windowserver", "dock", "systemuiserver"}
FILE_MANAGERS = {"explorer.exe", "finder", "nautilus", "nemo", "caja", "dolphin", "thunar"}


def allowed(pid):
    ref = eligible(pid)
    if not ref or Path(ref.executable).name.casefold() in PROTECTED:
        return None
    return ref


def can_force(ref):
    return Path(ref.executable).name.casefold() not in FILE_MANAGERS


class WindowsBackend:
    def windows(self):
        import win32gui
        import win32process
        import win32ts
        current_session = win32ts.ProcessIdToSessionId(os.getpid())
        result = []
        def visit(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd) or win32gui.GetWindow(hwnd, 4):
                    return
                cls = win32gui.GetClassName(hwnd)
                if cls in {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}:
                    return
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if win32ts.ProcessIdToSessionId(pid) != current_session:
                    return
                ref = allowed(pid)
                if not ref or Path(ref.executable).name.casefold() == "applicationframehost.exe":
                    return  # Host UWP compartilhado não comprova a identidade do aplicativo.
                result.append(Window(str(hwnd), ref, process_identity(ref), Path(ref.executable).stem, can_force(ref)))
            except Exception:
                return
        win32gui.EnumWindows(visit, None)
        return result

    def close(self, window):
        import win32con
        import win32gui
        import win32process
        hwnd = int(window.handle)
        if not window.process.valid() or not win32gui.IsWindow(hwnd) or win32process.GetWindowThreadProcessId(hwnd)[1] != window.process.pid:
            raise ValueError("A janela mudou")
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)

    def force(self, process):
        force_checked(process)


class MacBackend:
    def windows(self):
        from AppKit import NSWorkspace
        from Foundation import NSRunLoop, NSDate
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.001))
        result = []
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            if app.activationPolicy() != 0 or not app.bundleURL():
                continue
            ref = allowed(app.processIdentifier())
            # Finder compartilha o desktop. terminate() não é fechamento de janelas.
            # Sem um adaptador de janelas verificável, não anunciar esse alvo.
            if ref and Path(ref.executable).name.casefold() != "finder":
                identity = "bundle:" + str(Path(str(app.bundleURL().path())).resolve())
                result.append(Window(str(ref.pid), ref, identity, str(app.localizedName()), can_force(ref)))
        return result

    def close(self, window):
        from AppKit import NSRunningApplication
        if Path(window.process.executable).name.casefold() == "finder":
            raise ValueError("Fechamento das janelas do Finder ainda indisponível.")
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(window.process.pid)
        if app and window.process.valid():
            app.terminate()

    def force(self, process):
        from AppKit import NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(process.pid)
        if app and process.valid() and app.launchDate() and abs(app.launchDate().timeIntervalSince1970() - process.created) < 1:
            app.forceTerminate()


class X11Backend:
    def windows(self):
        from Xlib import display
        connection = display.Display()
        result = []
        try:
            root = connection.screen().root
            clients = root.get_full_property(connection.intern_atom("_NET_CLIENT_LIST"), 0)
            if not clients:
                return result
            for handle in clients.value:
                try:
                    window = connection.create_resource_object("window", int(handle))
                    prop = window.get_full_property(connection.intern_atom("_NET_WM_PID"), 0)
                    machine = window.get_wm_client_machine()
                    if isinstance(machine, bytes):
                        machine = machine.decode("utf-8", "replace")
                    if machine not in {socket.gethostname(), socket.getfqdn(), "localhost"} or not prop:
                        continue  # Nenhuma inferência de PID local para clientes remotos/desconhecidos.
                    ref = allowed(int(prop.value[0]))
                    if ref:
                        result.append(Window(str(int(handle)), ref, process_identity(ref),
                                             Path(ref.executable).stem, can_force(ref)))
                except Exception:
                    continue
        finally:
            connection.close()
        return result

    def close(self, window):
        from Xlib import X, display, protocol
        connection = display.Display()
        try:
            native = connection.create_resource_object("window", int(window.handle))
            pid = native.get_full_property(connection.intern_atom("_NET_WM_PID"), 0)
            if not window.process.valid() or not pid or int(pid.value[0]) != window.process.pid:
                raise ValueError("A janela mudou")
            event = protocol.event.ClientMessage(window=int(window.handle),
                client_type=connection.intern_atom("_NET_CLOSE_WINDOW"), data=(32, [X.CurrentTime, 2, 0, 0, 0]))
            connection.screen().root.send_event(event, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            connection.flush()
        finally:
            connection.close()

    def force(self, process):
        force_checked(process)


class GnomeBackend:
    def call(self, method, arguments=None):
        from .platform_apps import gio
        Gio, GLib = gio()
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return connection.call_sync("org.gnome.Shell", "/org/gnome/Shell/Extensions/Jarvis",
            "org.jarvis.WindowControl", method, arguments, None, Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]

    def windows(self):
        result = []
        for item in json.loads(self.call("List")):
            ref = allowed(int(item["pid"]))
            if ref:
                identity = "desktop:" + item["app_id"] if item["app_id"] else ref.executable
                result.append(Window(str(item["id"]), ref, identity, item["name"], can_force(ref)))
        return result

    def close(self, window):
        from .platform_apps import gio
        _, GLib = gio()
        if not window.process.valid() or not self.call("Close", GLib.Variant("(su)", (window.handle, window.process.pid))):
            raise ValueError("A janela não está mais disponível")

    def force(self, process):
        force_checked(process)


class UnavailableBackend:
    def windows(self):
        raise RuntimeError("Este ambiente gráfico não oferece integração de fechamento. No Wayland, use GNOME e ative a extensão JARVIS.")


def create_backend():
    if sys.platform == "win32":
        return WindowsBackend()
    if sys.platform == "darwin":
        return MacBackend()
    if sys.platform == "linux":
        if os.getenv("XDG_SESSION_TYPE") == "wayland" or os.getenv("WAYLAND_DISPLAY"):
            return GnomeBackend() if "gnome" in os.getenv("XDG_CURRENT_DESKTOP", "").lower() else UnavailableBackend()
        if os.getenv("DISPLAY"):
            return X11Backend()
    return UnavailableBackend()
