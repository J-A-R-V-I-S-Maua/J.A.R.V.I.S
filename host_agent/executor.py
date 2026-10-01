"""Lançamento local verificado. Fechamento pertence ao coordenador e RunningApps."""
import sys
from urllib.parse import urlencode

from contracts.commands import OpenApp, OpenUrl, SearchWeb, CloseApp
from .catalog import Catalog
from .platform_apps import launch


def confirmation(action, context):
    entries = {a.id: a for a in context.available_apps + context.running_apps}
    if isinstance(action, CloseApp):
        return f"Vou fechar o aplicativo {entries[action.target_id].name}, incluindo todas as suas janelas. Posso executar?"
    if isinstance(action, OpenApp):
        return f"Vou abrir o {entries[action.app].name}. Posso executar?"
    identifier = context.default_browser if action.browser == "default" else action.browser
    browser = entries[identifier].name
    if isinstance(action, OpenUrl):
        return f"Vou abrir {action.url} no {browser}. Posso executar?"
    provider = "Google" if action.provider == "google" else "YouTube"
    return f"Vou pesquisar {action.query} no {provider}, usando o {browser}. Posso executar?"


class NativeExecutor:
    def __init__(self, catalog=None, launcher=launch):
        self.catalog = catalog or Catalog()
        self.launcher = launcher
        self.entries = {}

    def start(self):
        self.catalog.start()

    def close(self):
        self.catalog.close()

    def context(self, text, running=(), **kwargs):
        context, self.entries = self.catalog.select(text, running, **kwargs)
        return context

    def execute(self, action, context):
        if isinstance(action, OpenApp):
            identifier, url = action.app, None
        elif isinstance(action, (OpenUrl, SearchWeb)):
            identifier = context.default_browser if action.browser == "default" else action.browser
            if isinstance(action, OpenUrl):
                url = action.url
            elif action.provider == "youtube":
                url = "https://www.youtube.com/results?" + urlencode({"search_query": action.query})
            else:
                url = "https://www.google.com/search?" + urlencode({"q": action.query})
        else:
            raise ValueError("Ação desconhecida")
        entry = self.entries.get(identifier)
        if not entry:
            raise ValueError("Aplicativo indisponível")
        entry.verify()
        current = self.catalog.snapshot().get(identifier)
        if not current or current != entry:
            raise ValueError("O catálogo mudou. Faça um novo pedido.")
        self.launcher(entry, url)
        system = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}.get(sys.platform, "sistema")
        return f"Solicitação enviada ao {system}."


WindowsExecutor = NativeExecutor
