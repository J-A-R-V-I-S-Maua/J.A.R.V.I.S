"""Descoberta local de apps permitidos, sem executar comandos de arquivos desktop."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from xml.parsers.expat import ExpatError

APP_NAMES = {
    "browser": "navegador", "chrome": "Google Chrome", "edge": "Microsoft Edge",
    "brave": "Brave", "firefox": "Mozilla Firefox", "chromium": "Chromium",
    "opera": "Opera", "vivaldi": "Vivaldi", "safari": "Safari",
    "explorer": "Explorador de Arquivos", "notepad": "Bloco de Notas",
}
# Apenas nomes, caminhos e argumentos definidos pelo host entram no catálogo.
WINDOWS = {
    "chrome": ("chrome.exe", ("Google/Chrome/Application/chrome.exe",), ("chromehtml",)),
    "edge": ("msedge.exe", ("Microsoft/Edge/Application/msedge.exe",), ("msedgehtm",)),
    "brave": ("brave.exe", ("BraveSoftware/Brave-Browser/Application/brave.exe",), ("bravehtml",)),
    "firefox": ("firefox.exe", ("Mozilla Firefox/firefox.exe",), ("firefoxurl", "firefoxhtml")),
    "chromium": ("chromium.exe", ("Chromium/Application/chrome.exe",), ("chromiumhtm",)),
    "opera": ("opera.exe", ("Programs/Opera/launcher.exe", "Opera/launcher.exe"), ("operastable",)),
    "vivaldi": ("vivaldi.exe", ("Vivaldi/Application/vivaldi.exe",), ("vivaldihtm",)),
}
LINUX = {
    "chrome": ("google-chrome", "google-chrome-stable"),
    "edge": ("microsoft-edge", "microsoft-edge-stable"),
    "brave": ("brave-browser", "brave"),
    "firefox": ("firefox", "firefox-esr"),
    "chromium": ("chromium", "chromium-browser"),
    "opera": ("opera",), "vivaldi": ("vivaldi", "vivaldi-stable"),
}
FLATPAK = {
    "chrome": "com.google.Chrome", "edge": "com.microsoft.Edge",
    "brave": "com.brave.Browser", "firefox": "org.mozilla.firefox",
    "chromium": "org.chromium.Chromium", "opera": "com.opera.Opera",
    "vivaldi": "com.vivaldi.Vivaldi",
}
MACOS = {
    "chrome": ("com.google.Chrome", "Google Chrome.app"),
    "edge": ("com.microsoft.edgemac", "Microsoft Edge.app"),
    "brave": ("com.brave.Browser", "Brave Browser.app"),
    "firefox": ("org.mozilla.firefox", "Firefox.app"),
    "chromium": ("org.chromium.Chromium", "Chromium.app"),
    "opera": ("com.operasoftware.Opera", "Opera.app"),
    "vivaldi": ("com.vivaldi.Vivaldi", "Vivaldi.app"),
    "safari": ("com.apple.Safari", "Safari.app"),
}
FALLBACK = {
    "win32": ("edge", "chrome", "brave", "firefox", "chromium", "opera", "vivaldi"),
    "linux": ("firefox", "chrome", "chromium", "brave", "edge", "opera", "vivaldi"),
    "darwin": ("safari", "chrome", "firefox", "brave", "edge", "chromium", "opera", "vivaldi"),
}


def _query(argv):
    """Consultas locais limitadas; falta de um utilitário não impede descoberta."""
    try:
        result = subprocess.run(argv, shell=False, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=3,
                                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _file(path):
    path = Path(path)
    return path.is_absolute() and path.is_file()


def _which(name):
    found = shutil.which(name)
    return found if found and _file(found) else None


def _windows():
    import winreg
    apps = {}
    for app, (exe, relatives, _) in WINDOWS.items():
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}",
                                        0, winreg.KEY_READ | view) as key:
                        path = os.path.expandvars(winreg.QueryValue(key, None).strip().strip('"'))
                        if _file(path):
                            apps.setdefault(app, (path,))
                except OSError:
                    pass
        for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles"),
                     os.environ.get("ProgramFiles(x86)")):
            if base:
                for relative in relatives:
                    path = Path(base) / relative
                    if _file(path):
                        apps.setdefault(app, (str(path),))
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    for app, path in (("explorer", windows / "explorer.exe"), ("notepad", windows / "System32/notepad.exe")):
        if _file(path):
            apps[app] = (str(path),)
    default = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as key:
            progid = winreg.QueryValueEx(key, "ProgId")[0].casefold()
            default = next((app for app, (_, _, prefixes) in WINDOWS.items()
                            if progid.startswith(prefixes)), None)
    except OSError:
        pass
    return apps, default


def _linux():
    apps = {}
    for app, names in LINUX.items():
        for name in names:
            path = _which(name)
            if not path:
                snap = Path("/snap/bin") / name
                if _file(snap) and os.access(snap, os.X_OK):
                    path = str(snap)
            if path:
                # Não resolver symlinks: wrappers Snap dependem de argv[0].
                apps[app] = (path,)
                break
    flatpak = _which("flatpak")
    installed = set(_query([flatpak, "list", "--app", "--columns=application"]).splitlines()) if flatpak else set()
    for app, identifier in FLATPAK.items():
        if identifier in installed:
            apps.setdefault(app, (flatpak, "run", identifier))
    settings = _which("xdg-settings")
    desktop = _query([settings, "get", "default-web-browser"]) if settings else ""
    if not desktop:
        mime = _which("xdg-mime")
        desktop = _query([mime, "query", "default", "x-scheme-handler/https"]) if mime else ""
    default = None
    for app, names in LINUX.items():
        ids = {f"{name}.desktop" for name in names} | {f"{FLATPAK[app]}.desktop"}
        ids |= {f"{name}_{name}.desktop" for name in names}  # IDs exportados pelo Snap.
        if desktop in ids:
            default = app
            # Se o padrão é Flatpak, preservar essa instalação, mesmo havendo outra nativa.
            if desktop == f"{FLATPAK[app]}.desktop" and FLATPAK[app] in installed:
                apps[app] = (flatpak, "run", FLATPAK[app])
            elif desktop.endswith(tuple(f"{name}_{name}.desktop" for name in names)):
                for name in names:
                    snap = Path("/snap/bin") / name
                    if _file(snap) and os.access(snap, os.X_OK):
                        apps[app] = (str(snap),)
                        break
            break
    return apps, default


def _bundle(path, identifier):
    try:
        if not Path(path).is_absolute():
            return False
        with (Path(path) / "Contents/Info.plist").open("rb") as file:
            data = plistlib.load(file)
            return isinstance(data, dict) and data.get("CFBundleIdentifier") == identifier
    except (OSError, ValueError, plistlib.InvalidFileException, ExpatError):
        return False


def _macos():
    # Script fixo: nenhum texto do usuário ou do modelo é interpolado.
    identifiers = json.dumps({app: spec[0] for app, spec in MACOS.items()})
    script = """ObjC.import('AppKit');
const ws = $.NSWorkspace.sharedWorkspace;
const ids = IDS;
const result = {apps: {}, default: ''};
for (const name in ids) {
    const url = ws.URLForApplicationWithBundleIdentifier(ids[name]);
    if (url && !url.isNil()) result.apps[name] = ObjC.unwrap(url.path);
}
const url = ws.URLForApplicationToOpenURL($.NSURL.URLWithString('https://example.com'));
if (url && !url.isNil()) {
    const bundle = $.NSBundle.bundleWithURL(url);
    if (bundle && !bundle.isNil()) result.default = ObjC.unwrap(bundle.bundleIdentifier);
}
JSON.stringify(result);""".replace("IDS", identifiers)
    try:
        data = json.loads(_query(["/usr/bin/osascript", "-l", "JavaScript", "-e", script]))
        if not isinstance(data, dict) or not isinstance(data.get("apps"), dict):
            data = {}
    except ValueError:
        data = {}
    apps = {}
    for app, (identifier, filename) in MACOS.items():
        candidates = [data.get("apps", {}).get(app)] + [str(root / filename) for root in (
            Path.home() / "Applications", Path("/Applications"), Path("/System/Applications"))]
        for path in candidates:
            if isinstance(path, str) and _bundle(path, identifier):
                apps[app] = ("/usr/bin/open", "-a", path)
                break
    default = next((app for app, (identifier, _) in MACOS.items() if identifier == data.get("default")), None)
    return apps, default


def discover_apps(platform=None):
    """Retorna argv fixo por app; 'browser' referencia um navegador encontrado."""
    platform = sys.platform if platform is None else platform
    if platform.startswith("linux"):
        platform = "linux"
    discover = {"win32": _windows, "linux": _linux, "darwin": _macos}.get(platform)
    if not discover:
        return {}
    apps, default = discover()
    selected = next((app for app in (default, *FALLBACK[platform]) if app in apps), None)
    if selected:
        apps["browser"] = apps[selected]
    return apps


if __name__ == "__main__":
    # Diagnóstico sem microfone, Ollama ou abertura de aplicativos.
    print(json.dumps({"platform": sys.platform, "apps": discover_apps()}, ensure_ascii=False, indent=2))
