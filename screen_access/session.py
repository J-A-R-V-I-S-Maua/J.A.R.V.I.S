"""Sessão de escolha e confirmação, independente de Qt e do sistema operacional."""
from dataclasses import dataclass
from enum import Enum
import re
import time
from .commands import CANCEL, CONFIRM, FIELD_COMMANDS, action_command, normalized, parse_number
from .matching import choose
from .scroll import parse_scroll, ScrollOutcome


class ActionState(Enum):
    IDLE = "idle"
    READING = "reading"
    CHOOSING = "choosing"
    CONFIRMING = "confirming"
    DICTATING = "dictating"
    EXECUTING = "executing"
    SCROLL_CHOOSING = "scroll_choosing"
    SCROLLING = "scrolling"
    SCROLL_WAITING = "scroll_waiting"


@dataclass(frozen=True)
class ActionView:
    text: str
    markers: tuple = ()
    capture: bool = False
    state: ActionState = ActionState.IDLE


class ScreenSession:
    def __init__(self, executor, publish, max_depth=32, max_elements=3000):
        self.executor = executor
        self.publish = publish
        self.max_depth = max_depth
        self.max_elements = max_elements
        self.state = ActionState.IDLE
        self.last_window = None
        self.target = None
        self.snapshot = None
        self.candidates = ()
        self.selected = None
        self.token = 0
        self.pending_phrase = None
        self.unavailable = None
        self.dictation_text = None
        self.is_dictation = False
        self.pending_action = "activate"
        self.retry_count = 0
        self.deadline = None
        self._step = self.state
        self._reset_scroll()

    def _reset_scroll(self):
        self.scroll_request = None
        self.scroll_snapshot = None
        self.scroll_selected = None
        self.scroll_restore_numbers = False
        self.scroll_refresh = False
        self.scroll_message = ""

    @property
    def scroll_continuing(self):
        return self.scroll_request is not None and self.state in (ActionState.SCROLL_WAITING, ActionState.CHOOSING)

    @property
    def capture_timeout(self):
        return 5 if self.scroll_continuing else None

    def end_scroll(self):
        self.cancel("Rolagem encerrada. Diga Hey Jarvis para outro comando.")

    @property
    def active(self):
        return self.state is not ActionState.IDLE

    def view(self, text, elements=(), capture=False):
        if self.state is not self._step:
            self._step = self.state
            self.retry_count = 0
            self.deadline = time.monotonic() + (90 if self.state in (ActionState.EXECUTING, ActionState.SCROLLING) else 30) if self.active else None
        self.publish(ActionView(text, tuple(elements), capture, self.state))

    def retry(self, text):
        if self.retry_count or (self.deadline is not None and time.monotonic() >= self.deadline):
            self.cancel("Não consegui concluir. Diga ‘Hey Jarvis’ para tentar novamente.")
            return
        self.retry_count += 1
        elements = tuple(self.snapshot.elements[n-1] for n in self.candidates) if self.snapshot and self.state is ActionState.CHOOSING else ()
        if self.state is ActionState.SCROLL_CHOOSING and self.scroll_snapshot:
            elements = self.scroll_snapshot.elements
        if self.snapshot and self.state is ActionState.CONFIRMING and not self.is_dictation:
            elements = (self.snapshot.elements[self.selected-1],)
        self.view(text, elements, capture=True)

    def help(self, text):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self.cancel("Interação expirada. Repita o comando.")
            return
        elements = tuple(self.snapshot.elements[n-1] for n in self.candidates) if self.snapshot and self.state is ActionState.CHOOSING else ()
        if self.state is ActionState.SCROLL_CHOOSING and self.scroll_snapshot:
            elements = self.scroll_snapshot.elements
        if self.snapshot and self.state is ActionState.CONFIRMING and not self.is_dictation:
            elements = (self.snapshot.elements[self.selected-1],)
        self.view(text, elements, capture=self.state in (ActionState.CHOOSING, ActionState.CONFIRMING, ActionState.SCROLL_CHOOSING, ActionState.SCROLL_WAITING))

    def begin_capture(self):
        if not self.active:
            self.target = self.last_window

    def cancel(self, text="Ação cancelada."):
        self.token += 1
        self.executor.cancel()
        self.state = ActionState.IDLE
        self.snapshot = None
        self.candidates = ()
        self.selected = None
        self.target = None
        self.dictation_text = None
        self.is_dictation = False
        self.pending_action = "activate"
        self._reset_scroll()
        self.view(text)

    def _start_scroll(self, request):
        if self.unavailable:
            self.view(self.unavailable)
            return
        if self.target is None:
            self.view("Selecione uma janela de aplicativo antes de rolar.")
            return
        self.scroll_restore_numbers = self.state is ActionState.CHOOSING
        self.scroll_request = request
        self.pending_action = "activate"
        self.token += 1
        self.state = ActionState.SCROLLING
        self.view("Identificando área rolável…")
        self.executor.submit(self.token, "prepare_scroll", self.target, self.max_depth, self.max_elements)

    def _execute_scroll(self, request):
        self.scroll_request = request
        self.state = ActionState.SCROLLING
        self.token += 1
        self.view("Rolando para cima…" if request.direction == "up" else "Rolando para baixo…")
        self.executor.submit(self.token, "scroll", self.scroll_snapshot, self.scroll_selected, request)

    def _scroll_prompt(self):
        return self.scroll_message + " Diga mais, para cima, para baixo ou parar."

    def receive(self, token, kind, data):
        if kind == "foreground":
            self.last_window = data
            if self.target and (data.identity, data.pid, data.frame) != (
                    self.target.identity, self.target.pid, self.target.frame):
                self.cancel("A janela mudou. Diga mostrar números para ler novamente.")
            return
        if kind == "unavailable":
            self.unavailable = data
            if self.active:
                self.cancel(data)
            return
        if token != self.token or not self.active:
            return
        if kind == "error":
            self.cancel(data)
        elif kind == "prepare_scroll":
            self.scroll_snapshot = data.snapshot
            if not data.snapshot.elements:
                self.cancel("Não consegui identificar uma área para rolar nesta janela.")
            elif data.preferred:
                self.scroll_selected = data.preferred
                self._execute_scroll(self.scroll_request)
            else:
                self.state = ActionState.SCROLL_CHOOSING
                self.view("Qual área você quer rolar? Diga um dos números ou cancelar." +
                          (" Lista parcial de áreas." if data.snapshot.truncated else ""), data.snapshot.elements, True)
        elif kind == "scroll":
            self.scroll_message = {ScrollOutcome.MOVED: "Tela rolada para cima." if self.scroll_request.direction == "up" else "Tela rolada para baixo.",
                                   ScrollOutcome.START: "Você chegou ao início.", ScrollOutcome.END: "Você chegou ao fim.",
                                   ScrollOutcome.REQUESTED: "Rolagem solicitada ao aplicativo."}[data]
            if self.scroll_restore_numbers:
                self.scroll_refresh = True
                self.pending_phrase = None
                self.state = ActionState.READING
                self.token += 1
                self.view(self.scroll_message + " Atualizando números…")
                self.executor.submit(self.token, "scan", self.target, self.max_depth, self.max_elements, False)
            else:
                self.state = ActionState.SCROLL_WAITING
                self.view(self._scroll_prompt(), capture=True)
        elif kind == "scan":
            self.snapshot = data
            self.target = data.window
            if self.scroll_refresh:
                self.scroll_refresh = False
                self.candidates = tuple(element.number for element in data.elements)
                self.state = ActionState.CHOOSING if data.elements else ActionState.SCROLL_WAITING
                extra = " Números atualizados. Diga um número para escolher ou continue a rolagem." if data.elements else " Nenhum controle disponível nesta janela."
                self.view(self._scroll_prompt() + extra + (" Lista parcial." if data.truncated else ""), data.elements, True)
                return
            if not data.elements:
                suffix = " Leitura parcial; aumente os limites SCREEN_MAX_DEPTH/SCREEN_MAX_ELEMENTS." if data.truncated else ""
                self.cancel("Nenhum controle disponível nesta janela." + suffix)
            elif self.pending_phrase is None:
                self.state = ActionState.CHOOSING
                self.candidates = tuple(element.number for element in data.elements)
                limit = " Lista parcial: limite atingido ou ramo inacessível." if data.truncated else ""
                self.view("Diga o número do controle ou cancelar." + limit, data.elements, True)
            else:
                self.select(self.pending_phrase)
        elif kind == "prepare_dictation":
            self.snapshot = data
            if self.pending_action in ("press_enter", "clear_text"):
                self.selected = 1
                self.state = ActionState.CONFIRMING
                name = data.elements[0].name or "campo ativo"
                label = "Pressionar Enter em" if self.pending_action == "press_enter" else "Apagar todo o conteúdo de"
                self.view(f"{label} {name}? Diga confirmar ou cancelar.", capture=True)
            elif self.dictation_text:
                self.confirm_dictation()
            else:
                self.state = ActionState.DICTATING
                self.view("Diga o texto para o campo ativo ou cancelar.", capture=True)
        elif kind == "dictate":
            self.cancel("Texto inserido no campo.")
        elif kind == "clear_text":
            self.cancel("Conteúdo do campo apagado.")
        elif kind == "press_enter":
            self.cancel("Enter enviado ao campo.")
        elif kind in ("activate", "open_item", "context_menu"):
            self.cancel("Pedido aceito pelo aplicativo.")

    def confirm_dictation(self):
        self.state = ActionState.CONFIRMING
        name = self.snapshot.elements[0].name or "campo ativo"
        self.view(f'Digitar “{self.dictation_text}” em {name}? Diga confirmar ou cancelar.', capture=True)

    def confirm_selection(self, number):
        self.selected = number
        self.state = ActionState.CONFIRMING
        element = self.snapshot.elements[number-1]
        label = {"activate": "Acionar", "open_item": "Abrir com duplo clique",
                 "context_menu": "Abrir menu de contexto de"}[self.pending_action]
        self.view(f"{label} [{number}] {element.name or '(sem nome)'}? Diga confirmar ou cancelar.", (element,), True)

    def select(self, phrase):
        number = parse_number(phrase)
        if number is not None:
            if number <= len(self.snapshot.elements):
                self.confirm_selection(number)
            else:
                if self.state is ActionState.READING:
                    self.state = ActionState.CHOOSING
                    self.candidates = tuple(element.number for element in self.snapshot.elements)
                    self.view("Escolha um dos números exibidos.", self.snapshot.elements)
                self.retry("Esse número não está na lista. Diga um dos números exibidos ou cancelar.")
            return
        choice = choose(phrase, [element.name for element in self.snapshot.elements])
        if choice.status == "match":
            self.confirm_selection(choice.index+1)
        elif choice.status == "ambiguous":
            self.state = ActionState.CHOOSING
            self.candidates = tuple(index+1 for index in choice.candidates)
            elements = tuple(self.snapshot.elements[number-1] for number in self.candidates)
            self.view("Vários controles combinam. Diga um dos números ou cancelar.", elements, True)
        else:
            self.cancel("Não encontrei esse controle. Diga mostrar números para consultar a lista.")

    def handle(self, text):
        if self.active and self.deadline is not None and time.monotonic() >= self.deadline:
            self.cancel("Interação expirada. Repita o comando.")
            return True
        phrase = normalized(text)
        if phrase in CANCEL or phrase in {"esconder numeros", "ocultar numeros"}:
            if self.scroll_request is not None and "numeros" not in phrase:
                self.end_scroll()
            else:
                self.cancel("Números ocultados." if "numeros" in phrase else "Ação cancelada.")
            return True
        request = parse_scroll(text, self.scroll_request)
        if self.state is ActionState.CONFIRMING:
            if request:
                self.help("Diga confirmar ou cancelar antes de rolar a tela.")
                return True
            if phrase in CONFIRM:
                self.state = ActionState.EXECUTING
                self.token += 1
                if self.is_dictation:
                    self.view("Inserindo texto…")
                    self.executor.submit(self.token, "dictate", self.snapshot, self.dictation_text)
                else:
                    self.view({"context_menu": "Abrindo menu de contexto…", "press_enter": "Enviando Enter…",
                               "clear_text": "Apagando conteúdo do campo…"}.get(self.pending_action, "Acionando controle…"))
                    self.executor.submit(self.token, self.pending_action, self.snapshot, self.selected)
            else:
                self.retry("Confirmação inválida. Diga confirmar ou cancelar. Ouvindo novamente…")
            return True
        if self.state is ActionState.DICTATING:
            if not text.strip() or any(ord(char) < 32 for char in text):
                self.cancel("Texto inválido. Repita digitar.")
            else:
                self.dictation_text = text.strip()
                self.confirm_dictation()
            return True
        if self.state is ActionState.SCROLL_CHOOSING:
            number = parse_number(text)
            if number is not None and 1 <= number <= len(self.scroll_snapshot.elements):
                self.scroll_selected = number
                self._execute_scroll(self.scroll_request)
            else:
                self.retry("Diga um dos números das áreas roláveis ou cancelar.")
            return True
        if request and self.state in (ActionState.IDLE, ActionState.CHOOSING, ActionState.SCROLL_WAITING):
            if self.scroll_selected is not None:
                self._execute_scroll(request)
            else:
                self._start_scroll(request)
            return True
        if self.state is ActionState.SCROLL_WAITING:
            # Comandos de tela explícitos podem substituir a continuação.
            if action_command(text) or phrase in FIELD_COMMANDS or phrase in {"mostrar numeros", "mostre numeros", "mostrar os numeros", "mostre os numeros"} or re.match(r"^\s*digitar\b", text, re.IGNORECASE):
                target = self.target
                self.cancel("")
                self.target = target
            else:
                self.retry("Diga mais, para cima, para baixo ou parar.")
                return True
        if self.state is ActionState.CHOOSING:
            number = parse_number(text)
            if number in self.candidates:
                self._reset_scroll()
                command = action_command(text)
                if command and command[0] != "activate":
                    self.pending_action = command[0]
                self.confirm_selection(number)
            else:
                self.retry("Escolha inválida. Diga um dos números exibidos ou cancelar. Ouvindo novamente…")
            return True
        if self.active:
            self.view("Operação em andamento. Aguarde o resultado.")
            return True  # Uma operação em andamento não aceita uma segunda ação.
        command = action_command(text)
        field_action = FIELD_COMMANDS.get(phrase)
        target = command[1] if command else None
        # O texto ditado preserva acentos, caixa e pontuação da transcrição final.
        dictation = re.match(r"^\s*digitar\b[\s,:;.!?]*(.*)$", text, re.IGNORECASE)
        show = phrase in {"mostrar numeros", "mostre numeros", "mostrar os numeros", "mostre os numeros"}
        if not show and target is None and dictation is None and field_action is None:
            return False
        if command and not target:
            self.cancel("Diga o nome ou número do controle após o comando.")
            return True
        if self.unavailable:
            self.view(self.unavailable)
            return True
        if self.target is None:
            self.view("Selecione uma janela de aplicativo e repita o comando.")
            return True
        self.state = ActionState.READING
        self.is_dictation = dictation is not None
        self.dictation_text = dictation.group(1).strip() or None if dictation else None
        self.pending_phrase = None if show else target
        self.pending_action = field_action or (command[0] if command else "activate")
        self.token += 1
        if self.is_dictation or field_action:
            self.view("Identificando campo de texto ativo…")
            self.executor.submit(self.token, "prepare_dictation", self.target)
        else:
            self.view("Lendo controles da janela…")
            self.executor.submit(self.token, "scan", self.target, self.max_depth, self.max_elements, False)
        return True
