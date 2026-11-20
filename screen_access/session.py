"""Sessão de escolha e confirmação, independente de Qt e do sistema operacional."""
from dataclasses import dataclass
from enum import Enum
import re
import time
from .commands import (
    BROWSER_ACTIONS,
    CANCEL,
    CONFIRM,
    FIELD_COMMANDS,
    SPOKEN_COMMANDS,
    action_command,
    browser_command,
    field_command,
    normalized,
    parse_number,
    select_command,
    Reply,
    classify_reply
)
from .matching import choose
from .scroll import parse_scroll, ScrollOutcome

# Mission Control no macOS: True abre, False fecha.
WINDOW_COMMANDS = {normalized(text): opened for text, opened in (
    ("mostrar janelas", True), ("mostre as janelas", True), ("mostrar as janelas", True),
    ("mostrar todas as janelas", True), ("ver janelas", True), ("ver as janelas", True),
    ("fechar janelas", False), ("feche as janelas", False), ("fechar as janelas", False),
    ("esconder janelas", False), ("esconda as janelas", False), ("esconder as janelas", False),
)}
# Durante o ditado é texto; durante uma execução ou leitura, espera o resultado.
WINDOW_COMMAND_STATES = frozenset({"idle", "choosing", "confirming", "scroll_choosing", "scroll_waiting"})


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
    done: bool = False  # um comando acabou de dar certo: o modo contínuo pode ouvir o próximo


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
        self.selection = None  # ("all"|"last_word"|"text", trecho) enquanto o campo é procurado
        # Janelas do Mission Control: o número escolhido vai direto, sem pedir confirmação.
        self.choosing_window = False
        self.choosing_menu = False  # as opções de um menu de contexto aberto estão numeradas
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

    def view(self, text, elements=(), capture=False, done=False):
        if self.state is not self._step:
            self._step = self.state
            self.retry_count = 0
            self.deadline = time.monotonic() + (90 if self.state in (ActionState.EXECUTING, ActionState.SCROLLING) else 30) if self.active else None
        self.publish(ActionView(text, tuple(elements), capture, self.state, done))

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

    def finish(self, text):
        """Fim de um comando que deu certo (diferente de cancelar ou de um erro)."""
        self.cancel(text, done=True)

    def cancel(self, text="Ação cancelada.", done=False):
        menu_open = self.choosing_menu
        self.token += 1
        self.executor.cancel()
        if menu_open:
            # Sem escolher nada, o menu não fica aberto na tela (depois de uma escolha ele já fechou).
            self.executor.submit(self.token, "close_menu")
        self.state = ActionState.IDLE
        self.snapshot = None
        self.candidates = ()
        self.selected = None
        self.target = None
        self.dictation_text = None
        self.is_dictation = False
        self.pending_action = "activate"
        self.selection = None
        self.choosing_window = False
        self.choosing_menu = False
        self._reset_scroll()
        self.view(text, done=done)

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

    def _mission_control(self, opened):
        if self.unavailable:
            self.view(self.unavailable)
            return
        self.cancel("")  # uma proposta pendente fica para trás: as janelas vão mudar de lugar
        self.state = ActionState.EXECUTING
        self.token += 1
        self.view("Mostrando as janelas…" if opened else "Fechando as janelas…")
        self.executor.submit(self.token, "mission_control", opened)

    def _browser(self, action, number):
        if self.unavailable:
            self.view(self.unavailable)
            return
        if self.target is None:
            self.view("Deixe o navegador na frente e repita o comando.")
            return
        self.state = ActionState.EXECUTING
        self.token += 1
        self.view("Enviando ao navegador…")
        self.executor.submit(self.token, "browser", self.target, action, number)

    def _scroll_prompt(self):
        return self.scroll_message + " Diga mais, para cima, para baixo ou parar."

    def receive(self, token, kind, data):
        if kind == "foreground":
            self.last_window = data
            if self.target and (data.identity, data.pid, data.frame) != (
                    self.target.identity, self.target.pid, self.target.frame):
                if not self.active:
                    # Nada foi perguntado ainda (ex.: o app que acabou de abrir veio para a frente
                    # enquanto o Jarvis ouvia): o comando vale para a janela que está na frente.
                    self.target = data
                    return
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
                ask = ("Diga o número da janela ou cancelar." if self.choosing_window else
                       "Diga o número da opção do menu ou cancelar." if self.choosing_menu else
                       "Diga o número do controle ou cancelar.")
                self.view(ask + limit, data.elements, True)
            else:
                self.select(self.pending_phrase)
        elif kind == "prepare_dictation":
            self.snapshot = data
            if self.pending_action in ("select_text", "copy_text"):
                # Selecionar e copiar não alteram o texto: vão direto, sem pedir confirmação.
                select = self.pending_action == "select_text"
                self.state = ActionState.EXECUTING
                self.token += 1
                self.view("Selecionando…" if select else "Copiando…")
                self.executor.submit(self.token, self.pending_action, data, self.selection if select else 1)
            elif self.pending_action in ("press_enter", "clear_text", "cut_text"):
                self.selected = 1
                self.state = ActionState.CONFIRMING
                name = data.elements[0].name or "campo ativo"
                label = {"press_enter": "Pressionar Enter em", "clear_text": "Apagar todo o conteúdo de",
                         "cut_text": "Recortar o texto selecionado de"}[self.pending_action]
                self.view(f"{label} {name}? Diga confirmar ou cancelar.", capture=True)
            elif self.dictation_text:
                self.confirm_dictation()
            else:
                self.state = ActionState.DICTATING
                self.view("Diga o texto para o campo ativo ou cancelar.", capture=True)
        elif kind == "prepare_paste":
            # O conteúdo vai na pergunta: a pessoa não vê a área de transferência.
            self.snapshot, preview = data
            self.selected = 1
            self.state = ActionState.CONFIRMING
            name = self.snapshot.elements[0].name or "campo ativo"
            self.view(f"Colar “{preview}” em {name}? Diga confirmar ou cancelar.", capture=True)
        elif kind == "dictate":
            self.finish("Texto inserido no campo.")
        elif kind == "clear_text":
            self.finish("Conteúdo do campo apagado.")
        elif kind == "press_enter":
            self.finish("Enter enviado ao campo.")
        elif kind in ("select_text", "copy_text", "cut_text", "paste_text", "browser"):
            self.finish(data)
        elif kind == "context_menu" and data:
            # macOS: o menu abriu e voltou como janela; as opções já aparecem numeradas.
            self.target = data
            self.choosing_menu = True
            self.pending_action = "activate"
            self.pending_phrase = None
            self.state = ActionState.READING
            self.token += 1
            self.view("Lendo as opções do menu…")
            self.executor.submit(self.token, "scan", data, self.max_depth, self.max_elements, False)
        elif kind in ("activate", "open_item", "context_menu"):
            self.finish("Janela escolhida." if self.choosing_window else "Pedido aceito pelo aplicativo.")
        elif kind == "mission_control":
            if not data:
                self.finish("Janelas fechadas.")
                return
            # Os números já aparecem e o Jarvis fica ouvindo: basta dizer o número, sem "Hey Jarvis".
            self.target = data
            self.choosing_window = True
            self.pending_phrase = None
            self.state = ActionState.READING
            self.token += 1
            self.view("Numerando as janelas…")
            self.executor.submit(self.token, "scan", data, self.max_depth, self.max_elements, False)

    def confirm_dictation(self):
        self.state = ActionState.CONFIRMING
        name = self.snapshot.elements[0].name or "campo ativo"
        self.view(f'Digitar “{self.dictation_text}” em {name}? Diga confirmar ou cancelar.', capture=True)

    def confirm_selection(self, number):
        self.selected = number
        if self.choosing_window and self.pending_action == "activate":
            # Ir para uma janela só muda o que está na frente; não vale a pena perguntar.
            element = self.snapshot.elements[number-1]
            self.state = ActionState.EXECUTING
            self.token += 1
            self.view(f"Abrindo [{number}] {element.name or '(sem nome)'}…")
            self.executor.submit(self.token, "activate", self.snapshot, number)
            return
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
        reply = classify_reply(text)
        dictation_cancel = self.state is ActionState.DICTATING and phrase in CANCEL
        if (dictation_cancel or (self.state is not ActionState.DICTATING and reply is Reply.CANCEL)
                or phrase in {"esconder numeros", "ocultar numeros"}):
            if self.scroll_request is not None and "numeros" not in phrase:
                self.end_scroll()
            else:
                self.cancel("Números ocultados." if "numeros" in phrase else "Ação cancelada.")
            return True
        if phrase in WINDOW_COMMANDS and self.state.value in WINDOW_COMMAND_STATES:
            self._mission_control(WINDOW_COMMANDS[phrase])
            return True
        request = parse_scroll(text, self.scroll_request)
        if self.state is ActionState.CONFIRMING:
            if request:
                self.help("Diga confirmar ou cancelar antes de rolar a tela.")
                return True
            if reply is Reply.CONFIRM:
                self.state = ActionState.EXECUTING
                self.token += 1
                if self.is_dictation:
                    self.view("Inserindo texto…")
                    self.executor.submit(self.token, "dictate", self.snapshot, self.dictation_text)
                else:
                    self.view({"context_menu": "Abrindo menu de contexto…", "press_enter": "Enviando Enter…",
                               "clear_text": "Apagando conteúdo do campo…", "cut_text": "Recortando…",
                               "paste_text": "Colando…"}.get(self.pending_action, "Acionando controle…"))
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
            if (action_command(text) or phrase in FIELD_COMMANDS or browser_command(text)
                    or phrase in {"mostrar numeros", "mostre numeros", "mostrar os numeros", "mostre os numeros"}
                    or re.match(r"^\s*digitar\b", text, re.IGNORECASE)):
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
        selection = select_command(text)
        if selection is not None:
            # "Selecionar tudo" também casa com o clique ("selecionar" + controle "tudo"); o texto vence.
            command, field_action = None, "select_text"
        target = command[1] if command else None
        # O texto ditado preserva acentos, caixa e pontuação da transcrição final.
        dictation = re.match(r"^\s*digitar\b[\s,:;.!?]*(.*)$", text, re.IGNORECASE)
        show = phrase in {"mostrar numeros", "mostre numeros", "mostrar os numeros", "mostre os numeros"}
        undecided = not show and command is None and dictation is None and field_action is None
        browser = browser_command(text) if undecided else None
        if undecided and browser is None:
            # Último recurso: uma frase que soa como um comando ("Polar texto" por "colar texto").
            guess = field_command(text, SPOKEN_COMMANDS)
            if guess in BROWSER_ACTIONS:
                browser = guess, None
            else:
                field_action = guess
        if browser is not None:
            self._browser(*browser)
            return True
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
        self.selection = selection
        self.token += 1
        if self.is_dictation or field_action:
            self.view("Identificando campo de texto ativo…")
            self.executor.submit(self.token, "prepare_paste" if field_action == "paste_text" else "prepare_dictation",
                                 self.target)
        else:
            self.view("Lendo controles da janela…")
            self.executor.submit(self.token, "scan", self.target, self.max_depth, self.max_elements, False)
        return True
