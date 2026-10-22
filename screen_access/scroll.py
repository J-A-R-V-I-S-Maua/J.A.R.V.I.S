"""Rolagem vertical nativa e comandos explícitos; sem eventos de mouse/teclado."""
from dataclasses import dataclass
from enum import Enum
import re
import os
import time
import uuid

from .backend import Element, Snapshot, ScreenError, intersection
from .commands import normalized


@dataclass(frozen=True)
class ScrollRequest:
    direction: str
    intensity: str = "normal"


@dataclass(frozen=True)
class ScrollTargets:
    snapshot: Snapshot
    preferred: int | None = None


class ScrollOutcome(Enum):
    MOVED = "moved"
    START = "start"
    END = "end"
    REQUESTED = "requested"


# Tempo para acompanhar o movimento sem prolongar a continuação por voz.
SCROLL_DURATIONS = {"small": .45, "normal": .75, "page": 1.1}
SCROLL_FRAME_SECONDS = 1 / 30


def parse_scroll(text, previous=None):
    phrase = normalized(text)
    if previous and phrase == "mais":
        return previous
    if previous and phrase in ("para cima", "para baixo"):
        return ScrollRequest("up" if phrase == "para cima" else "down", previous.intensity)
    match = re.fullmatch(r"(?:role|rolar|rola|rolagem)(?: a tela)?(?: (um pouco|uma pagina))? para (cima|baixo)(?: (um pouco|uma pagina))?", phrase)
    if match and not (match[1] and match[3]):
        amount = match[1] or match[3]
        return ScrollRequest("up" if match[2] == "cima" else "down",
                             {"um pouco": "small", "uma pagina": "page"}.get(amount, "normal"))
    aliases = {normalized(alias): direction for alias, direction in (
        ("descer a tela", "down"), ("desça a tela", "down"), ("subir a tela", "up"), ("suba a tela", "up"))}
    if phrase in aliases:
        return ScrollRequest(aliases[phrase])
    return None


class ScrollMixin:
    def _check_scroll_window(self, window, cancelled):
        if cancelled():
            raise ScreenError("Rolagem cancelada.")
        current = self.tracked_foreground()
        if (not self.window_exists(window) or (self.require_foreground and
                (current is None or (current.identity, current.pid, current.frame) !=
                 (window.identity, window.pid, window.frame)))):
            raise ScreenError("A janela mudou. Rolagem encerrada.")

    def _animate_scroll(self, snapshot, number, pattern, before, target, intensity, cancelled):
        began = time.monotonic()
        duration = SCROLL_DURATIONS[intensity]
        next_frame = began + SCROLL_FRAME_SECONDS
        while True:
            # A espera é curta e ocorre apenas na thread do executor.
            delay = min(next_frame, began + duration) - time.monotonic()
            if delay > 0:
                time.sleep(min(delay, SCROLL_FRAME_SECONDS))
            self._validate_scroll_area(snapshot, number, cancelled)
            progress = min(1., max(0., (time.monotonic() - began) / duration))
            # Smoothstep: parte devagar, acelera no meio e desacelera ao chegar.
            eased = progress * progress * (3 - 2 * progress)
            position = target if progress >= 1 else before + (target - before) * eased
            if not pattern.SetScrollPercent(-1, position, waitTime=0):
                raise ScreenError("O aplicativo não confirmou a rolagem. Confira a tela antes de repetir.")
            if progress >= 1:
                return
            # Chamadas nativas lentas não geram uma sequência de frames atrasados.
            next_frame = time.monotonic() + SCROLL_FRAME_SECONDS

    def scroll(self, snapshot, number, request, cancelled=lambda: False):
        if request.direction not in ("up", "down") or request.intensity not in ("small", "normal", "page"):
            raise ScreenError("Comando de rolagem inválido.")
        native = self._validate_scroll_area(snapshot, number, cancelled)
        try:
            pattern = self.scroll_pattern(native)
            if not pattern or not pattern.VerticallyScrollable:
                raise ScreenError("Essa área não oferece rolagem vertical.")
            before = float(pattern.VerticalScrollPercent)
            view = float(pattern.VerticalViewSize)
        except ScreenError:
            raise
        except Exception as exc:
            raise ScreenError("A área rolável ficou indisponível. Repita o comando.") from exc
        if request.direction == "up" and before == 0:
            return ScrollOutcome.START
        if request.direction == "down" and before == 100:
            return ScrollOutcome.END
        self._check_scroll_window(snapshot.window, cancelled)
        if 0 <= before <= 100 and 0 < view < 100:
            fraction = {"small": .15, "normal": .5, "page": .9}[request.intensity]
            delta = 100 * view / (100 - view) * fraction
            target = min(100, before + delta) if request.direction == "down" else max(0, before - delta)
            self._animate_scroll(snapshot, number, pattern, before, target, request.intensity, cancelled)
            accepted = True
        else:
            # O tamanho exato do incremento é definido pelo aplicativo.
            amount = (1 if request.direction == "up" else 4) if request.intensity == "small" else (0 if request.direction == "up" else 3)
            accepted = pattern.Scroll(2, amount, waitTime=0)
        if not accepted:
            raise ScreenError("O aplicativo não confirmou a rolagem. Confira a tela antes de repetir.")
        deadline = time.monotonic() + .3
        after = before
        while time.monotonic() < deadline and not cancelled():
            try:
                after = float(pattern.VerticalScrollPercent)
            except Exception:
                break
            if after != before:
                break
            time.sleep(.03)
        if after == 0 and request.direction == "up":
            return ScrollOutcome.START
        if after == 100 and request.direction == "down":
            return ScrollOutcome.END
        if 0 <= before <= 100 and 0 <= after <= 100 and (after - before) * (1 if request.direction == "down" else -1) > 0:
            return ScrollOutcome.MOVED
        return ScrollOutcome.REQUESTED


class WindowsScrollMixin(ScrollMixin):
    @staticmethod
    def scroll_pattern(native):
        return native.GetPattern(10004)  # UIA_ScrollPatternId

    def prepare_scroll(self, window, max_depth=32, max_elements=3000, cancelled=lambda: False):
        if max_depth < 0 or not 1 <= max_elements <= 10000:
            raise ScreenError("Limites de leitura inválidos.")
        if window is None or window.pid == os.getpid():
            raise ScreenError("Selecione uma janela de aplicativo antes de rolar.")
        self._check_scroll_window(window, cancelled)
        self.scroll_references = {}
        self.scroll_snapshot = None
        elements, seen, truncated = [], set(), False
        began = time.monotonic()
        self.walk_incomplete = False
        self._node_windows.clear()
        for visited, (_, native, clip) in enumerate(self.walk(window, max_depth)):
            if cancelled():
                raise ScreenError("Rolagem cancelada.")
            if visited >= max_elements or time.monotonic() - began >= 5:
                truncated = True
                break
            try:
                frame = self.frame(native)
                if (native.IsOffscreen or not native.IsEnabled or not intersection(frame, clip)
                        or frame[2] < 4 or frame[3] < 4):
                    continue
                identity = tuple(native.GetRuntimeId())
                pattern = native.GetPattern(10004)  # UIA_ScrollPatternId
                if not pattern or not pattern.VerticallyScrollable or identity in seen:
                    continue
                if len(elements) >= 200:
                    truncated = True
                    break
                seen.add(identity)
                number = len(elements) + 1
                name, role = native.Name.strip(), native.ControlTypeName
                elements.append(Element(number, name or f"Área rolável {number}", role, frame, "scroll"))
                self.scroll_references[number] = (native, identity, frame, clip, name, role)
            except Exception:
                truncated = True
                continue
        truncated = truncated or self.walk_incomplete
        self._node_windows.clear()
        self.scroll_snapshot = Snapshot(uuid.uuid4().hex, window, tuple(elements), truncated=truncated)
        preferred = None
        try:
            focused = self.api.GetFocusedControl()
            if focused is None or focused.ProcessId == os.getpid():
                remembered = getattr(self, "last_scroll_focus", None)
                focused = remembered[1] if remembered and remembered[0] == window.identity else None
            if focused is not None and self.belongs_to(focused, window):
                by_identity = {reference[1]: number for number, reference in self.scroll_references.items()}
                for _ in range(64):
                    if focused is None:
                        break
                    preferred = by_identity.get(tuple(focused.GetRuntimeId()))
                    if preferred:
                        break
                    focused = focused.GetParentControl()
        except Exception:
            preferred = None
        if preferred is None and len(elements) == 1 and not truncated:
            preferred = 1
        if truncated:
            preferred = None
        self._check_scroll_window(window, cancelled)
        return ScrollTargets(self.scroll_snapshot, preferred)

    def _validate_scroll_area(self, snapshot, number, cancelled):
        self._check_scroll_window(snapshot.window, cancelled)
        if self.scroll_snapshot is None or snapshot.token != self.scroll_snapshot.token or number not in self.scroll_references:
            raise ScreenError("A área rolável expirou. Repita o comando.")
        native, identity, frame, clip, name, role = self.scroll_references[number]
        try:
            if (tuple(native.GetRuntimeId()) != identity or self.frame(native) != frame or native.IsOffscreen
                    or native.Name.strip() != name or native.ControlTypeName != role or not native.IsEnabled
                    or not intersection(frame, clip) or not self.belongs_to(native, snapshot.window)):
                raise ScreenError("A área rolável mudou. Repita o comando.")
            return native
        except ScreenError:
            raise
        except Exception as exc:
            raise ScreenError("A área rolável ficou indisponível. Repita o comando.") from exc
