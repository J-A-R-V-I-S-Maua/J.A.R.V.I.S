"""Catálogo nativo fechado. Nenhum caminho ou argumento de shell vem do LLM."""
import subprocess
import sys
from urllib.parse import urlencode

from contracts.commands import OpenApp, OpenUrl, SearchWeb
from .catalog import APP_NAMES, discover_apps


def confirmation(action):
    if isinstance(action, OpenApp):
        return f"Vou abrir o {APP_NAMES[action.app]}. Posso executar?"
    browser = "navegador" if action.browser == "default" else APP_NAMES[action.browser]
    if isinstance(action, OpenUrl):
        return f"Vou abrir {action.url} no {browser}. Posso executar?"
    provider = "Google" if action.provider == "google" else "YouTube"
    return f"Vou pesquisar {action.query} no {provider}, usando o {browser}. Posso executar?"


class NativeExecutor:
    def __init__(self, apps=None, launch=None, *, platform=None):
        self.platform = sys.platform if platform is None else platform
        self.apps = discover_apps(self.platform) if apps is None else apps
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
        target = self.apps.get(app)
        if not target:
            raise ValueError("Aplicativo indisponível")
        # Tuplas vêm do catálogo local, nunca do contrato recebido da IA.
        argv = [target] if isinstance(target, str) else list(target)
        self.launch([*argv, *args])
        system = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}.get(self.platform, "sistema")
        return f"Solicitação enviada ao {system}."


class WindowsExecutor(NativeExecutor):
    """Compatibilidade com integrações existentes do host Windows."""
    def __init__(self, apps=None, launch=None):
        super().__init__(apps, launch, platform="win32")
