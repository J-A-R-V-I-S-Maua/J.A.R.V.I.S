"""Catálogo descoberto no sistema. Lançadores nunca são enviados à IA."""
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import threading

from contracts.commands import AppDescriptor, CommandContext, normalize


def stable_id(value):
    return "app:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def fingerprint(path):
    path = Path(path)
    if path.is_symlink():
        stat = path.lstat()
        return (stat.st_dev, stat.st_ino, stat.st_mtime_ns, os.readlink(path))
    stat = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and stat.st_size < 1024 * 1024 else ""
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, digest)


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    kind: str
    target: str
    identity: str
    executable: str = ""
    arguments: str = ""
    cwd: str = ""
    aliases: tuple = ()
    sources: tuple = ()
    checks: tuple = ()
    browser: bool = False
    default: bool = False

    def public(self):
        return AppDescriptor(id=self.id, name=self.name[:100], aliases=list(self.aliases[:8]),
                             source="desktop" if "desktop" in self.sources else "menu", browser=self.browser)

    def verify(self):
        for path, expected in self.checks:
            try:
                if fingerprint(path) != expected:
                    raise ValueError("O aplicativo ou atalho mudou. Faça um novo pedido.")
            except OSError as exc:
                raise ValueError("O aplicativo ou atalho não está mais disponível.") from exc


@dataclass
class Inventory:
    entries: dict = field(default_factory=dict)
    excluded: list = field(default_factory=list)

    def exclude(self, source, reason):
        self.excluded.append({"source": str(source), "reason": str(reason)})

    def add(self, entry):
        previous = self.entries.get(entry.id)
        if previous:
            preferred = entry if "desktop" in entry.sources and "desktop" not in previous.sources else previous
            aliases = tuple(dict.fromkeys((*previous.aliases, previous.name, *entry.aliases, entry.name)))
            self.entries[entry.id] = replace(preferred, aliases=aliases[:8],
                sources=tuple(sorted(set(previous.sources + entry.sources))),
                browser=previous.browser or entry.browser, default=previous.default or entry.default)
        else:
            self.entries[entry.id] = entry

    def distinguish(self):
        groups = {}
        for entry in self.entries.values():
            groups.setdefault(normalize(entry.name), []).append(entry)
        for group in groups.values():
            if len(group) > 1:
                for index, entry in enumerate(sorted(group, key=lambda e: ("desktop" not in e.sources, e.id)), 1):
                    self.entries[entry.id] = replace(entry, name=f"{entry.name[:75]} (variante {index})",
                        aliases=tuple(dict.fromkeys((entry.name, *entry.aliases)))[:8])
        return self


def discover_apps(platform=None):
    from .platform_apps import scan
    return scan(platform or sys.platform)


def relevance(text, descriptor):
    source = normalize(text)
    score = 0.0
    for alias in (descriptor.name, *descriptor.aliases):
        name = normalize(alias)
        if not name:
            continue
        if re.search(r"\b" + re.escape(name) + r"\b", source):
            score = max(score, 100 + len(name.split()))
        else:
            words = set(name.split()) - {"microsoft", "google", "application", "app"}
            if words and words <= set(source.split()):
                score = max(score, 90)
            score = max(score, 50 * SequenceMatcher(None, source, name).ratio())
    return score


class Catalog:
    def __init__(self, scanner=discover_apps):
        self.scanner = scanner
        self.inventory = Inventory()
        self.lock = threading.Lock()
        self.refresh_lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None

    def refresh(self):
        with self.refresh_lock:
            new = self.scanner()
            with self.lock:
                self.inventory = new
        return new

    def start(self):
        self.refresh()
        self.thread = threading.Thread(target=self._run, name="jarvis-catalog", daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stopping.wait(60):
            try:
                self.refresh()
            except Exception:
                logging.exception("Não foi possível atualizar o catálogo")

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=5)

    def snapshot(self):
        with self.lock:
            return dict(self.inventory.entries)

    def select(self, text, running=(), *, refresh_missing=True):
        entries = self.snapshot()
        if refresh_missing and not any(relevance(text, e.public()) >= 90 for e in entries.values()):
            entries = dict(self.refresh().entries)
        close = bool(re.match(r"(?:(?:por favor|por gentileza) )?(?:eu )?(?:(?:quero|gostaria|pode|poderia)(?: de| que voce)? )?(?:feche|fecha|fechar|encerre|encerrar)\b", normalize(text)))
        if close:
            ranked = sorted(running, key=lambda a: (-relevance(text, a), a.name, a.id))[:20]
            return CommandContext(running_apps=ranked), entries
        ranked = sorted(entries.values(), key=lambda e: (
            -relevance(text, e.public()), "desktop" not in e.sources, e.name, e.id))
        browsers = [e for e in ranked if e.browser]
        default = next((e for e in browsers if e.default), browsers[0] if browsers else None)
        chosen = ranked[:20]
        if default and default not in chosen:
            chosen = chosen[:19] + [default]
        return CommandContext(available_apps=[e.public() for e in chosen],
                              default_browser=default.id if default else None), entries


if __name__ == "__main__":
    from .running import RunningApps
    result = discover_apps()
    running = RunningApps()
    targets = running.inventory(result.entries)
    print(json.dumps({"platform": sys.platform,
        "apps": [{**entry.public().model_dump(), "sources": entry.sources,
                  "target": entry.target, "identity": entry.identity,
                  "variant": entry.arguments, "default": entry.default}
                 for entry in result.entries.values()], "excluded": result.excluded,
        "running": [app.public().model_dump() for app in targets],
        "closing_error": running.error,
        "limitations": ["Finder: fechamento de janelas indisponível; o desktop compartilhado não será encerrado."] if sys.platform == "darwin" else []
        }, ensure_ascii=False, indent=2))
