"""Catálogo dos aplicativos instalados no macOS, para abri-los pelo nome.

Duas fontes: as pastas padrão (rápidas e previsíveis) e o Spotlight (acha apps em qualquer pasta,
como a mesa e Downloads). A lista é montada em segundo plano; quem pede nunca espera por ela.
"""
from dataclasses import dataclass
from difflib import SequenceMatcher
import logging
from pathlib import Path
import subprocess
import sys
import threading
import time

from screen_access.commands import action_command
from screen_access.matching import STOPWORDS, choose, sound_key, tokenize

from .commands import normalize

STANDARD_FOLDERS = (
    "/Applications", "/Applications/Utilities", "/System/Applications",
    "/System/Applications/Utilities", "~/Applications",
)
# O Finder mora fora das pastas de aplicativos.
EXTRA_APPLICATIONS = ("/System/Library/CoreServices/Finder.app",)
# Fora de qualquer lugar onde um usuário instalaria algo: internos do sistema, lixeira, backups e
# imagens de disco montadas (instaladores).
EXCLUDED_PREFIXES = ("/System/Library/", "/Library/", "/private/", "/usr/", "/Volumes/", "/opt/homebrew/Caskroom/",
                     "/usr/local/Caskroom/")
EXCLUDED_FRAGMENTS = ("/.Trash/", "/.Trashes/", "/Backups.backupdb/")

# Apelidos falados (tradução para o português e abreviações), por nome do arquivo, que não muda
# com o idioma do sistema.
NICKNAMES = {
    "visual studio code": ("vscode", "vs code", "visual studio"),
    "calculator": ("calculadora",), "notes": ("notas", "bloco de notas"),
    "calendar": ("calendário", "agenda"), "photos": ("fotos",),
    "system settings": ("configurações do sistema", "ajustes do sistema", "configurações", "ajustes"),
    "system preferences": ("preferências do sistema", "configurações do sistema", "ajustes do sistema"),
    "preview": ("pré-visualização", "visualização"), "mail": ("e-mail", "email", "correio"),
    "messages": ("mensagens",), "maps": ("mapas",), "music": ("música",), "contacts": ("contatos",),
    "reminders": ("lembretes",), "clock": ("relógio",), "weather": ("clima", "previsão do tempo"),
    "books": ("livros",), "stocks": ("bolsa", "ações"), "home": ("casa",), "textedit": ("editor de texto",),
    "activity monitor": ("monitor de atividade",), "disk utility": ("utilitário de disco",),
    "screenshot": ("captura de tela",), "image capture": ("captura de imagem",),
    "shortcuts": ("atalhos",), "font book": ("catálogo de fontes",), "dictionary": ("dicionário",),
    "voice memos": ("gravador de voz", "memos de voz"), "keychain access": ("acesso às chaves", "chaveiro"),
    "app store": ("loja de aplicativos",), "find my": ("buscar",), "chess": ("xadrez",),
    "migration assistant": ("assistente de migração",), "stickies": ("notas adesivas",),
    "launchpad": ("launchpad", "painel de aplicativos"), "mission control": ("controle de missão",),
}


@dataclass(frozen=True)
class Application:
    name: str  # o nome que o Finder mostra, no idioma do sistema
    path: Path

    @property
    def spoken_names(self):
        """Tudo o que a pessoa pode dizer para pedir este app, sem repetições."""
        stem = self.path.stem
        # O apelido vale pelo nome do arquivo (estável) ou pelo nome do Finder ("VoiceMemos" x "Voice Memos").
        nicknames = NICKNAMES.get(normalize(stem)) or NICKNAMES.get(normalize(self.name)) or ()
        names = [self.name, stem, *nicknames]
        unique = {}
        for name in names:
            unique.setdefault(normalize(name), name)
        return tuple(unique.values())


def is_user_application(path):
    """Verdadeiro para um aplicativo que a pessoa abriria: não é interno de outro app nem do sistema."""
    text = str(path)
    if not text.endswith(".app") or ".app/" in text:
        return False
    if text in EXTRA_APPLICATIONS:
        return True
    if text.startswith(EXCLUDED_PREFIXES) or text.startswith(str(Path.home() / "Library") + "/"):
        return False
    return not any(fragment in text for fragment in EXCLUDED_FRAGMENTS)


def display_name(path):
    if sys.platform == "darwin":
        try:
            from AppKit import NSFileManager

            name = NSFileManager.defaultManager().displayNameAtPath_(str(path))
            if name:
                return str(name).removesuffix(".app")
        except Exception:
            logging.debug("Sem nome de exibição para %s", path, exc_info=True)
    return Path(path).stem


def spotlight_paths(runner=subprocess.run):
    """Todos os .app que o Spotlight conhece. Falha silenciosa: as pastas padrão continuam valendo."""
    try:
        result = runner(["/usr/bin/mdfind", "kMDItemContentType == 'com.apple.application-bundle'"],
                        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.SubprocessError):
        logging.debug("O Spotlight não respondeu", exc_info=True)
        return []
    return [Path(line) for line in result.stdout.splitlines() if line.strip()] if result.returncode == 0 else []


def scan_applications(folders=STANDARD_FOLDERS, extra=EXTRA_APPLICATIONS, spotlight=spotlight_paths,
                      name=display_name):
    """Lista os aplicativos, sem repetir o mesmo (por caminho real) e em ordem alfabética."""
    candidates = []
    for folder in folders:
        folder = Path(folder).expanduser()
        if folder.is_dir():
            candidates.extend(sorted(folder.glob("*.app")))
    candidates.extend(Path(item) for item in extra if Path(item).is_dir())
    if spotlight is not None:
        candidates.extend(path for path in spotlight() if is_user_application(path) and path.is_dir())
    apps, seen = [], set()
    for path in candidates:
        real = path.resolve()
        if real not in seen:
            seen.add(real)
            apps.append(Application(name(path), path))
    return sorted(apps, key=lambda app: normalize(app.name))


OPEN_VERBS = frozenset({"abrir", "abra", "abre"})
OPEN_VERB_SIMILARITY = 0.6  # a transcrição entrega "abril", "abriu" e "abrem" no lugar de "abrir"


def is_open_verb(token):
    if token in OPEN_VERBS:
        return True
    key, target = sound_key(token), sound_key("abrir")
    return abs(len(key) - len(target)) <= 2 and SequenceMatcher(None, key, target).ratio() >= OPEN_VERB_SIMILARITY


def is_open_request(text):
    """"Abrir Safari" abre um aplicativo; "abrir menu de contexto" continua sendo um comando de tela."""
    tokens = tokenize(text)
    return bool(tokens) and is_open_verb(tokens[0]) and not action_command(text)


def request_words(phrase):
    """As palavras do nome pedido, sem o verbo "abrir" e sem artigos ("abrir o Safari" -> ["safari"])."""
    tokens = tokenize(phrase)
    if tokens and is_open_verb(tokens[0]):
        tokens = tokens[1:]
    return [token for token in tokens if token not in STOPWORDS]


def collapse_same_name(apps):
    """Dois apps com o mesmo nome viram um só (fica o primeiro, que vem das pastas padrão)."""
    seen, result = set(), []
    for app in apps:
        key = normalize(app.name)
        if key not in seen:
            seen.add(key)
            result.append(app)
    return result


def find_application(phrase, apps):
    """Devolve ("match", [app]), ("ambiguous", [apps]) ou ("none", []). Nunca adivinha."""
    apps = collapse_same_name(apps)
    entries = [(number, name) for number, app in enumerate(apps) for name in app.spoken_names]
    names = [name for _, name in entries]
    words = request_words(phrase)
    if not words:
        return "none", []
    # O "abrir" na frente é só para o comparador descartar o verbo e não confundi-lo com o nome.
    choice = choose("abrir " + " ".join(words), names, numbers=False)
    if choice.status == "none" and len(words) > 1:
        # A transcrição separa nomes como "ChatGPT" em "chat gpt".
        choice = choose("abrir " + "".join(words), names, numbers=False)
    if choice.status == "match":
        indexes = (choice.index,)
    elif choice.status == "ambiguous":
        indexes = choice.candidates
    else:
        return "none", []
    # Vários nomes do mesmo app (inglês e português) contam como um app só.
    distinct = list(dict.fromkeys(entries[index][0] for index in indexes))
    found = [apps[number] for number in distinct]
    if len(found) > 1:
        # Se a frase é exatamente o nome de um único app ("notas" x "notas adesivas"), ele ganha.
        spoken = tokenize(" ".join(words))
        exact = [app for app in found if any(tokenize(name) == spoken for name in app.spoken_names)]
        if len(exact) == 1:
            found = exact
    return ("match" if len(found) == 1 else "ambiguous"), found


def open_application(path, runner=subprocess.run):
    """Pede ao macOS para abrir o aplicativo. Sem shell: o caminho vai como argumento separado."""
    path = Path(path)
    if path.suffix != ".app":
        raise ValueError("Só aplicativos (.app) podem ser abertos por aqui.")
    result = runner(["/usr/bin/open", str(path)], capture_output=True, text=True,
                    stdin=subprocess.DEVNULL, timeout=10)
    if result.returncode:
        raise OSError(result.stderr.strip() or "O macOS recusou a abertura.")


class ApplicationCatalog:
    """Lista de aplicativos que se atualiza sozinha em segundo plano e nunca faz quem pede esperar."""

    def __init__(self, scan=scan_applications, ttl=60.0, clock=time.monotonic):
        self._scan, self._ttl, self._clock = scan, ttl, clock
        self._lock = threading.Lock()
        self._applications = []
        self._loaded_at = None
        self._refreshing = False
        self._ready = threading.Event()

    def applications(self):
        """A lista atual, na hora. Se estiver velha (ou ainda não existir), atualiza em segundo plano."""
        with self._lock:
            stale = self._loaded_at is None or self._clock() - self._loaded_at >= self._ttl
            start = stale and not self._refreshing
            if start:
                self._refreshing = True
            current = self._applications
        if start:
            threading.Thread(target=self._refresh, name="catalogo-de-aplicativos", daemon=True).start()
        return current

    def invalidate(self):
        """Marca a lista como velha, para a próxima consulta atualizar (ex.: nada combinou com o nome)."""
        with self._lock:
            self._loaded_at = None

    def wait(self, timeout=None):
        """Espera a primeira leitura terminar. Devolve False se estourou o tempo."""
        return self._ready.wait(timeout)

    def _refresh(self):
        try:
            found = self._scan()
        except Exception:
            logging.exception("Falha ao ler os aplicativos instalados")
            found = None
        with self._lock:
            if found is not None:
                self._applications = found
            self._loaded_at = self._clock()  # em caso de falha, só tenta de novo depois do prazo
            self._refreshing = False
        self._ready.set()
