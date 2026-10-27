"""Atalhos de abertura: correspondência exata e destinos tipados, sem shell."""
from dataclasses import dataclass
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices


from screen_access.matching import STOPWORDS, choose, tokenize
from .commands import normalize, SETTINGS_COMMANDS, RESERVED
WINDOWS_RESOURCES = {"explorer": "Explorador de Arquivos", "settings": "Configurações do Windows",
                     "network": "Configurações de Rede", "sound": "Configurações de Som"}
MACOS_RESOURCES = {"finder": "Finder", "settings": "Ajustes do Sistema",
                   "network": "Ajustes de Rede", "sound": "Ajustes de Som"}
MACOS_TARGETS = {"finder": ("-b", "com.apple.finder"), "settings": ("-b", "com.apple.systempreferences"),
                 "network": ("x-apple.systempreferences:com.apple.Network-Settings.extension",),
                 "sound": ("x-apple.systempreferences:com.apple.Sound-Settings.extension",)}
SYSTEM_RESOURCES = {"windows": WINDOWS_RESOURCES, "macos": MACOS_RESOURCES}
SYSTEM_KIND = "macos" if sys.platform == "darwin" else "windows"
APPLICATION_SUFFIXES = (".exe", ".lnk", ".app", ".dmg")
# O Finder fica fora das pastas de aplicativos.
MACOS_APPLICATION_FOLDERS = ("/Applications", "/Applications/Utilities", "/System/Applications",
                             "/System/Applications/Utilities", "~/Applications",
                             "/System/Library/CoreServices/Finder.app")


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
    elif kind in SYSTEM_RESOURCES:
        if destination not in SYSTEM_RESOURCES[kind]:
            raise ValueError("Selecione um recurso do Windows." if kind == "windows" else "Selecione um recurso do macOS.")
    elif kind in ("application", "folder"):
        path = Path(destination)
        if not path.is_absolute():
            raise ValueError("Selecione um caminho absoluto.")
        suffix = path.suffix.lower()
        if kind == "application" and suffix not in APPLICATION_SUFFIXES:
            raise ValueError("Selecione um aplicativo .app ou imagem .dmg (macOS), .exe ou atalho .lnk (Windows).")
        directory = kind == "folder" or suffix == ".app"
        if check_destination and not (path.is_dir() if directory else path.is_file()):
            raise ValueError("O destino não existe. Selecione novamente.")
    else:
        raise ValueError("Tipo de ação inválido.")
    return item


def is_disk_image(item):
    return item["kind"] == "application" and Path(item["destination"]).suffix.lower() == ".dmg"

def disk_image_application(path):
    """Monta o .dmg (ou reaproveita a montagem) e devolve o único .app que ele contém."""
    result = subprocess.run(["/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-plist", str(path)],
                            capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    if result.returncode:
        raise OSError(result.stderr.decode(errors="replace").strip() or "Não foi possível montar a imagem.")
    entities = plistlib.loads(result.stdout).get("system-entities", [])
    apps = sorted(app for entity in entities if "mount-point" in entity
                  for app in Path(entity["mount-point"]).glob("*.app"))
    if not apps:
        raise OSError("A imagem não contém um aplicativo.")
    if len(apps) > 1:
        raise OSError("A imagem contém mais de um aplicativo. Cadastre o .app desejado.")
    return apps[0]


@dataclass(frozen=True)
class Application:
    name: str
    path: Path

    @property
    def spoken_names(self):
        # O Finder mostra "Calendário", mas o arquivo continua Calendar.app; os dois valem.
        return self.name if self.path.stem == self.name else f"{self.name} {self.path.stem}"


def display_name(path):
    if sys.platform == "darwin":
        from AppKit import NSFileManager
        name = NSFileManager.defaultManager().displayNameAtPath_(str(path))
        if name:
            return str(name).removesuffix(".app")
    return path.stem


def installed_applications(folders=None, name=display_name):
    if folders is None:
        folders = MACOS_APPLICATION_FOLDERS if sys.platform == "darwin" else ()
    apps, seen = [], set()
    for folder in map(lambda folder: Path(folder).expanduser(), folders):
        for path in [folder] if folder.suffix == ".app" and folder.is_dir() else sorted(folder.glob("*.app")):
            app = Application(name(path), path)
            if normalize(app.name) not in seen:
                seen.add(normalize(app.name))
                apps.append(app)
    return sorted(apps, key=lambda app: normalize(app.name))


def find_application(phrase, apps):
    """Devolve (status, apps): "match" com um app, "ambiguous" com os parecidos ou "none"."""
    names = [app.spoken_names for app in apps]
    choice = choose(phrase, names, numbers=False)
    words = [word for word in tokenize(phrase)[1:] if word not in STOPWORDS]
    if choice.status == "none" and len(words) > 1:
        # A transcrição separa nomes como "ChatGPT" em "chat gpt".
        choice = choose(tokenize(phrase)[0] + " " + "".join(words), names, numbers=False)
    if choice.status == "match":
        return "match", [apps[choice.index]]
    return choice.status, [apps[index] for index in choice.candidates]


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
    def __init__(self, items=(), launcher=None, demo=False, notify=lambda text: None):
        self.items = list(items)
        self.launcher = launcher or self._launch
        self.demo = demo
        self.notify = notify

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
        if is_disk_image(item):
            # Montar pode demorar; a interface não espera por isso.
            threading.Thread(target=self._launch_later, args=(item,), daemon=True).start()
            return f"Montando a imagem de {item['name']}…"
        try:
            self.launcher(item)
        except Exception as exc:
            raise ValueError(f"Falha ao abrir {item['destination']}. Revise o destino. {exc}") from exc
        return f"Abertura de {item['name']} solicitada ao sistema."

    def _launch_later(self, item):
        try:
            self.launcher(item)
        except Exception as exc:
            self.notify(f"Falha ao abrir {item['name']}. {exc}")
        else:
            self.notify(f"Abertura de {item['name']} solicitada ao sistema.")

    @staticmethod
    def _launch(item):
        kind, destination = item["kind"], item["destination"]
        if kind == "site":
            if not QDesktopServices.openUrl(QUrl(destination)):
                raise OSError("O sistema recusou o endereço.")
        elif sys.platform == "darwin":
            if kind == "windows":
                raise OSError("Recursos do Windows estão disponíveis somente no Windows.")
            if kind == "macos":
                arguments = MACOS_TARGETS[destination]
            elif is_disk_image(item):
                arguments = (str(disk_image_application(destination)),)
            else:
                arguments = (destination,)
            result = subprocess.run(["/usr/bin/open", *arguments], capture_output=True, text=True, timeout=10)
            if result.returncode:
                raise OSError(result.stderr.strip() or "O macOS recusou a abertura.")
        elif sys.platform != "win32":
            raise OSError("Esse tipo de abertura está disponível somente no Windows e no macOS.")
        elif kind == "macos" or is_disk_image(item):
            raise OSError("Recursos do macOS estão disponíveis somente no macOS.")
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
        self.retry_count = 0
        self.deadline = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self.cancel("Atalho expirado. Repita executar atalho."))

    def cancel(self, text=None):
        was_active = self.active
        self.generation += 1
        self.active = False
        self.retry_count = 0
        self.deadline = None
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
            self.retry_count = 0
            self.deadline = time.monotonic() + 30
            self.timer.start(30000)
            self.changed.emit("Qual atalho? Diga a frase cadastrada ou cancelar.")
            self.capture_requested.emit()
            return True
        if value.startswith("executar atalho "):
            self._queue(value[len("executar atalho "):])
            return True
        return False

    def retry(self, message):
        if self.retry_count or (self.deadline is not None and time.monotonic() >= self.deadline):
            self.cancel("Não consegui concluir. Diga ‘Hey Jarvis’ para tentar novamente.")
            return
        self.retry_count += 1
        self.changed.emit(message)
        self.capture_requested.emit()

    def _queue(self, phrase):
        if not self.active:
            self.retry_count = 0
            self.deadline = time.monotonic() + 30
        self.timer.start(max(1, int(((self.deadline or time.monotonic() + 30) - time.monotonic()) * 1000)))
        self.active = True
        generation = self.generation

        def launch():
            if not self.active or generation != self.generation:
                return
            if self.deadline is not None and time.monotonic() >= self.deadline:
                self.cancel("Atalho expirado. Repita executar atalho.")
                return
            try:
                item = self.service.resolve(phrase)
            except ValueError as exc:
                self.retry(str(exc) + " Diga a frase cadastrada ou cancelar. Ouvindo novamente…")
                return
            self.active = False
            self.timer.stop()
            try:
                message = self.service.execute(item)
            except ValueError as exc:
                message = str(exc)
            self.changed.emit(message)

        QTimer.singleShot(0, launch)
