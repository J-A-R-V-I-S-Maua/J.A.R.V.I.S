"""Atalhos de abertura: correspondência exata e destinos tipados, sem shell."""
import os
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import urlsplit
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices


def normalize(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    return " ".join(re.findall(r"[a-z0-9]+", "".join(c for c in text if not unicodedata.combining(c))))


SETTINGS_COMMANDS = {normalize(text) for text in (
    "abra as configurações", "abrir configurações", "abra configurações", "abrir as configurações",
    "abra as configurações do Jarvis", "abrir configurações do Jarvis", "abrir as configurações do Jarvis",
)}
RESERVED = SETTINGS_COMMANDS | {"cancelar", "cancele", "nao", "parar", "pare", "sim", "confirmar", "confirme", "executar atalho"}
WINDOWS_RESOURCES = {"explorer": "Explorador de Arquivos", "settings": "Configurações do Windows",
                     "network": "Configurações de Rede", "sound": "Configurações de Som"}


def validate_shortcut(item, check_destination=True):
    if not isinstance(item, dict) or set(item) != {"id", "name", "phrase", "kind", "destination", "enabled"}:
        raise ValueError("Cadastro de atalho inválido.")
    item = dict(item)
    for key in ("id", "name", "phrase", "kind", "destination"):
        if not isinstance(item[key], str) or not item[key].strip():
            raise ValueError("Preencha nome, frase, ação e destino.")
        item[key] = item[key].strip()
    if type(item["enabled"]) is not bool or not normalize(item["phrase"]) or normalize(item["phrase"]) in RESERVED:
        raise ValueError("Essa frase é reservada ou inválida. Escolha outra frase de voz.")
    kind, destination = item["kind"], item["destination"]
    if kind == "site":
        if "://" not in destination:
            destination = "https://" + destination
        url = urlsplit(destination)
        url.port  # Também valida portas e colchetes malformados.
        if url.scheme not in ("http", "https") or not url.hostname or any(c.isspace() for c in destination) or url.username or url.password:
            raise ValueError("Informe um endereço HTTP ou HTTPS válido.")
        item["destination"] = destination
    elif kind == "windows":
        if destination not in WINDOWS_RESOURCES:
            raise ValueError("Selecione um recurso do Windows.")
    elif kind in ("application", "folder"):
        path = Path(destination)
        if not path.is_absolute():
            raise ValueError("Selecione um caminho absoluto.")
        if kind == "application" and path.suffix.lower() not in (".exe", ".lnk"):
            raise ValueError("Selecione um aplicativo .exe ou atalho .lnk.")
        if check_destination and not (path.is_dir() if kind == "folder" else path.is_file()):
            raise ValueError("O destino não existe. Selecione novamente.")
    else:
        raise ValueError("Tipo de ação inválido.")
    return item


def validate_shortcuts(items, check_destination=True):
    if not isinstance(items, list):
        raise ValueError("Lista de atalhos inválida.")
    result, ids, phrases = [], set(), set()
    for item in items:
        item = validate_shortcut(item, check_destination)
        phrase = normalize(item["phrase"])
        if phrase in phrases or item["id"] in ids:
            raise ValueError("Já existe um atalho com essa frase de voz.")
        phrases.add(phrase)
        ids.add(item["id"])
        result.append(item)
    return result


class ShortcutService:
    def __init__(self, items=(), launcher=None, demo=False):
        self.items = list(items)
        self.launcher = launcher or self._launch
        self.demo = demo

    def resolve(self, phrase):
        key = normalize(phrase)
        item = next((item for item in self.items if normalize(item["phrase"]) == key), None)
        if item is None:
            raise ValueError("Atalho não encontrado. Confira a frase nas configurações.")
        if not item["enabled"]:
            raise ValueError("Esse atalho está desativado.")
        return item

    def execute(self, item):
        item = validate_shortcut(item)
        if self.demo:
            return "Demonstração: nenhuma abertura foi executada."
        try:
            self.launcher(item)
        except Exception as exc:
            raise ValueError(f"Falha ao abrir {item['destination']}. Revise o destino. {exc}") from exc
        return f"Abertura de {item['name']} solicitada ao sistema."

    @staticmethod
    def _launch(item):
        kind, destination = item["kind"], item["destination"]
        if kind == "site":
            if not QDesktopServices.openUrl(QUrl(destination)):
                raise OSError("O sistema recusou o endereço.")
        elif sys.platform != "win32":
            raise OSError("Esse tipo de abertura está disponível somente no Windows.")
        elif kind == "windows":
            if destination == "explorer":
                os.startfile(str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "explorer.exe"))
            else:
                os.startfile({"settings": "ms-settings:", "network": "ms-settings:network-status", "sound": "ms-settings:sound"}[destination])
        else:
            os.startfile(destination)


class ShortcutConversation(QObject):
    changed = Signal(str)
    capture_requested = Signal()
    cancelled = Signal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.active = False
        self.generation = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self.cancel("Atalho expirado. Repita executar atalho."))

    def cancel(self, text=None):
        was_active = self.active
        self.generation += 1
        self.active = False
        self.timer.stop()
        if was_active:
            self.cancelled.emit()
        if text:
            self.changed.emit(text)

    def handle(self, text):
        value = normalize(text)
        if self.active:
            if value in {"cancelar", "cancele", "nao", "parar", "pare"}:
                self.cancel("Atalho cancelado.")
            else:
                self._queue(value)
            return True
        if value == "executar atalho":
            self.active = True
            self.timer.start(30000)
            self.changed.emit("Qual atalho? Diga a frase cadastrada ou cancelar.")
            self.capture_requested.emit()
            return True
        if value.startswith("executar atalho "):
            self._queue(value[len("executar atalho "):])
            return True
        return False

    def _queue(self, phrase):
        self.timer.stop()
        self.active = True
        generation = self.generation

        def launch():
            if not self.active or generation != self.generation:
                return
            self.active = False
            try:
                message = self.service.execute(self.service.resolve(phrase))
            except ValueError as exc:
                message = str(exc)
            self.changed.emit(message)

        QTimer.singleShot(0, launch)
