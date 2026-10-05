"""Execução das intenções no host. MockExecutor descreve; BrowserExecutor age."""

import logging
import os
import webbrowser
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

HOMEPAGE = "https://www.google.com"
SEARCH_URL = "https://www.google.com/search?q={}"

SITES = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "maps": "https://www.google.com/maps",
    "whatsapp": "https://web.whatsapp.com",
}


def site_url(site):
    """Apelido conhecido, domínio digitado ou busca pelo nome."""
    known = SITES.get(site)
    if known:
        return known
    if "." in site and " " not in site:
        return f"https://{site}"
    return SEARCH_URL.format(quote_plus(site))


class MockExecutor:
    """Dry-run: descreve a ação sem executar nada."""

    def execute(self, intent):
        # intent.name vem sempre do CATALOG, nunca de texto livre do usuário.
        action = getattr(self, intent.name, None)
        if action is None:
            return f"O comando {intent.name} ainda não tem execução."
        feedback = action(intent.slots)
        logger.info("%s %s -> %s", type(self).__name__, intent.name, feedback)
        return feedback

    def parar(self, slots):
        return "Operação interrompida."

    def abrir_navegador(self, slots):
        return "Abriria o navegador. (mock)"

    def abrir_site(self, slots):
        return f"Abriria {site_url(slots['site'])} (mock)"

    def pesquisar(self, slots):
        return f"Pesquisaria por “{slots['query']}” (mock)"

    def rolar(self, slots):
        return f"Rolaria a página para {slots['direction']}. (mock)"

    def nova_aba(self, slots):
        return "Abriria uma nova aba. (mock)"

    def fechar_aba(self, slots):
        return "Fecharia a aba atual. (mock)"

    def voltar(self, slots):
        return "Voltaria para a página anterior. (mock)"

    def fechar_navegador(self, slots):
        return "Fecharia o navegador. (mock)"


class BrowserExecutor(MockExecutor):
    """Age de verdade no que dá por URL; herda o resto como mock."""

    def abrir_navegador(self, slots):
        webbrowser.open_new(HOMEPAGE)
        return "Abrindo o navegador."

    def abrir_site(self, slots):
        url = site_url(slots["site"])
        webbrowser.open_new_tab(url)
        return f"Abrindo {url}"

    def pesquisar(self, slots):
        query = slots["query"]
        webbrowser.open_new_tab(SEARCH_URL.format(quote_plus(query)))
        return f"Pesquisando por “{query}”"


def create_executor():
    mode = os.getenv("COMMAND_MODE", "browser").lower()
    if mode == "browser":
        return BrowserExecutor()
    if mode == "mock":
        return MockExecutor()
    raise ValueError("COMMAND_MODE deve ser browser ou mock")
