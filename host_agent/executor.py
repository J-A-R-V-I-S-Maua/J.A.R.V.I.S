"""Catálogo nativo fechado. Nenhum caminho ou argumento de shell vem do LLM."""
import os
from pathlib import Path
import subprocess
from urllib.parse import urlencode

from contracts.commands import OpenApp, OpenUrl, SearchWeb

APP_NAMES = {"browser": "navegador", "chrome": "Google Chrome", "edge": "Microsoft Edge",
             "explorer": "Explorador de Arquivos", "notepad": "Bloco de Notas"}


def discover_apps():
    import winreg
    apps = {}
    for app, exe in (("chrome", "chrome.exe"), ("edge", "msedge.exe")):
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}",
                                        0, winreg.KEY_READ | view) as key:
                        path = Path(winreg.QueryValue(key, None).strip('"'))
                        if path.is_absolute() and path.is_file():
                            apps.setdefault(app, str(path))
                except OSError:
                    pass
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    for app, path in (("explorer", windows / "explorer.exe"), ("notepad", windows / "System32" / "notepad.exe")):
        if path.is_file():
            apps[app] = str(path)
    default = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as key:
            progid = winreg.QueryValueEx(key, "ProgId")[0].lower()
            default = "chrome" if progid.startswith("chromehtml") else "edge" if progid.startswith("msedgehtm") else None
    except OSError:
        pass
    default = next((name for name in (default, "edge", "chrome") if name in apps), None)
    if default:
        apps["browser"] = apps[default]
    return apps


def confirmation(action):
    if isinstance(action, OpenApp):
        return f"Vou abrir o {APP_NAMES[action.app]}. Posso executar?"
    browser = "navegador" if action.browser == "default" else APP_NAMES[action.browser]
    if isinstance(action, OpenUrl):
        return f"Vou abrir {action.url} no {browser}. Posso executar?"
    provider = "Google" if action.provider == "google" else "YouTube"
    return f"Vou pesquisar {action.query} no {provider}, usando o {browser}. Posso executar?"


class WindowsExecutor:
    def __init__(self, apps=None, launch=None):
        self.apps = discover_apps() if apps is None else apps
        self.launch = launch or self._launch

    @staticmethod
    def _launch(argv):
        return subprocess.Popen(argv, shell=False, close_fds=True)

    def execute(self, action):
        # A autorização e a exclusão mútua do despacho pertencem ao coordenador.
        if isinstance(action, OpenApp):
            app = action.app
            args = []
        elif isinstance(action, (OpenUrl, SearchWeb)):
            app = "browser" if action.browser == "default" else action.browser
            if isinstance(action, OpenUrl):
                url = action.url
            elif action.provider == "youtube":
                url = "https://www.youtube.com/results?" + urlencode({"search_query": action.query})
            else:
                url = "https://www.google.com/search?" + urlencode({"q": action.query})
            args = [url]
        else:
            raise ValueError("Ação desconhecida")
        executable = self.apps.get(app)
        if not executable:
            raise ValueError("Aplicativo indisponível")
        self.launch([executable, *args])
        return "Solicitação enviada ao Windows."
