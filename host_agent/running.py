"""Inventário e identidade dos alvos. A IA nunca recebe PIDs ou handles."""
from dataclasses import dataclass, replace
import os
from pathlib import Path
import time

import psutil

from contracts.commands import AppDescriptor
from wakeword.events import check_cancelled
from .catalog import stable_id


class TargetChanged(ValueError):
    pass


@dataclass(frozen=True)
class ProcessRef:
    pid: int
    created: float
    executable: str
    user: str

    @classmethod
    def read(cls, pid):
        process = psutil.Process(pid)
        return cls(pid, process.create_time(), os.path.normcase(process.exe()), process.username())

    def valid(self):
        try:
            return self == self.read(self.pid) and psutil.Process(self.pid).is_running()
        except (psutil.Error, OSError):
            return False


@dataclass(frozen=True)
class Window:
    handle: str
    process: ProcessRef
    identity: str
    name: str
    can_force: bool = True


@dataclass(frozen=True)
class RunningApp:
    id: str
    identity: str
    name: str
    windows: tuple
    processes: tuple
    browser: bool = False
    aliases: tuple = ()
    can_force: bool = True

    def public(self):
        return AppDescriptor(id=self.id, name=self.name[:100], aliases=list(self.aliases[:8]),
                             source="running", browser=self.browser, can_force=self.can_force)


class RunningApps:
    def __init__(self, backend=None, *, wait_seconds=15):
        if backend is None:
            from .window_backends import create_backend
            backend = create_backend()
        self.backend = backend
        self.wait_seconds = wait_seconds
        self.targets = {}
        self.error = ""

    def inventory(self, entries):
        try:
            windows = self.backend.windows()
            grouped = {}
            for window in windows:
                grouped.setdefault(window.identity, []).append(window)
            result = {}
            for identity, group in grouped.items():
                matches = [e for e in entries.values() if e.identity == identity or
                           (identity == group[0].process.executable and e.executable and
                            os.path.normcase(e.executable) == group[0].process.executable)]
                name = matches[0].name if matches else group[0].name
                aliases = tuple(dict.fromkeys(a for e in matches for a in (e.name, *e.aliases)))[:8]
                identifier = "run:" + stable_id(identity)[4:]
                refs = tuple(sorted({w.process for w in group}, key=lambda p: p.pid))
                # Inclui apenas filhos do mesmo executável; não assume propriedade de toda a árvore.
                children = set(refs)
                for ref in refs:
                    try:
                        for child in psutil.Process(ref.pid).children(recursive=True):
                            item = ProcessRef.read(child.pid)
                            if identity == ref.executable and item.executable == ref.executable and item.user == ref.user and item.created >= ref.created:
                                children.add(item)
                    except psutil.Error:
                        pass
                result[identifier] = RunningApp(identifier, identity, name, tuple(sorted(group, key=lambda w: w.handle)),
                    tuple(sorted(children, key=lambda p: p.pid)), any(e.browser for e in matches), aliases,
                    all(w.can_force for w in group))
            names = {}
            for app in result.values():
                names.setdefault(app.name.casefold(), []).append(app)
            for group in names.values():
                if len(group) > 1:
                    for index, app in enumerate(sorted(group, key=lambda a: a.id), 1):
                        result[app.id] = replace(app, name=f"{app.name[:75]} (instalação {index})",
                            aliases=tuple(dict.fromkeys((app.name[:100], *app.aliases)))[:8])
            self.targets, self.error = result, ""
        except Exception as exc:
            self.targets, self.error = {}, f"Fechamento indisponível: {exc}"
        return list(self.targets.values())

    def prepare(self, target_id):
        target = self.targets.get(target_id)
        if target is None:
            raise TargetChanged("O aplicativo não está mais disponível para fechamento.")
        return target

    def revalidate(self, target):
        current = self.backend.windows()
        now = {(w.handle, w.process) for w in current if w.identity == target.identity}
        before = {(w.handle, w.process) for w in target.windows}
        if now != before or not all(p.valid() for p in target.processes):
            raise TargetChanged("O aplicativo ou suas janelas mudaram. Faça um novo pedido para confirmar o alvo atualizado.")

    def wait(self, target, cancelled, seconds=None):
        until = time.monotonic() + (self.wait_seconds if seconds is None else seconds)
        while True:
            check_cancelled(cancelled)
            if not any(p.valid() for p in target.processes):
                return True
            # Gerenciadores de arquivos: fechar janelas não encerra o shell compartilhado.
            if not target.can_force:
                handles = {(w.handle, w.process) for w in self.backend.windows()}
                if not any((w.handle, w.process) in handles for w in target.windows):
                    return True
            if time.monotonic() >= until:
                return False
            time.sleep(0.05)

    def close_normal(self, target, cancelled, dispatch):
        self.revalidate(target)
        for window in target.windows:
            check_cancelled(cancelled)
            if not window.process.valid():
                continue
            dispatch(lambda w=window: self.backend.close(w))
        return self.wait(target, cancelled)

    def remaining(self, target):
        """Preserva a identidade de um app que fechou a janela mas ficou na bandeja."""
        refs = tuple(p for p in target.processes if p.valid())
        if not refs:
            return None
        windows = tuple(sorted((w for w in self.backend.windows() if w.identity == target.identity), key=lambda w: w.handle))
        if any(w.process not in target.processes for w in windows):
            raise TargetChanged("O aplicativo mudou. Faça um novo pedido de fechamento.")
        return replace(target, windows=windows, processes=refs)

    def force(self, target, cancelled, dispatch):
        if not target.can_force:
            raise ValueError("Este aplicativo compartilha componentes com o sistema; encerramento forçado indisponível.")
        self.revalidate(target)
        for ref in reversed(target.processes):
            check_cancelled(cancelled)
            if not ref.valid():
                continue
            # Segunda verificação dentro da exclusão mútua do despacho.
            def terminate(process=ref):
                if process.valid():
                    self.backend.force(process)
            dispatch(terminate)
        return self.wait(target, cancelled, 5)


def eligible(pid):
    try:
        current = psutil.Process()
        if pid == os.getpid():
            return None
        ref = ProcessRef.read(pid)
        if ref.user != current.username():
            return None
        return ref
    except (psutil.Error, OSError):
        return None
