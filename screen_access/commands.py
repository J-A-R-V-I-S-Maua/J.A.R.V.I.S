"""Comandos explícitos e números PT-BR, sem interpretar fala livre como ação."""
from .matching import MIN_MARGIN, tokenize, word_similarity
from difflib import SequenceMatcher
from enum import Enum
import re

UNITS = ("zero", "um", "dois", "tres", "quatro", "cinco", "seis", "sete", "oito", "nove")
TEENS = ("dez", "onze", "doze", "treze", "quatorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove")
TENS = ("", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa")


def number_name(number):
    if number < 10:
        return UNITS[number]
    if number < 20:
        return TEENS[number-10]
    if number < 100:
        tens, units = divmod(number, 10)
        return TENS[tens] + (" e " + UNITS[units] if units else "")
    if number == 100:
        return "cem"
    if number < 200:
        return "cento e " + number_name(number-100)
    return "duzentos"


NUMBERS = {number_name(n): n for n in range(1, 201)}
NUMBERS.update({"uma": 1, "duas": 2, "catorze": 14, "dezasseis": 16, "dezassete": 17, "dezanove": 19})
ACTION_VERBS = {"clique", "clicar", "clica", "acione", "acionar", "ative", "ativar", "selecione", "selecionar"}
CANCEL = {
    "cancelar", "cancele", "cancela", "cancelo", "nao", "parar", "pare", "para",
    "esquece", "esqueca", "deixa", "voltar", "volta", "sair", "negativo",
}
CONFIRM = {
    "confirmar", "confirme", "confirma", "confirmo", "sim", "isso", "certo",
    "pode", "claro", "positivo",
}
FILLERS = {"por", "favor", "a", "o", "e", "mas", "ai", "entao", "ta", "ne", "tudo", "bem"}
MAX_REPLY_TOKENS = 4
_ALL_REPLY_WORDS = CONFIRM | CANCEL
_FUZZY_REPLY_WORDS = {word for word in _ALL_REPLY_WORDS if len(word) >= 6}
_NOISE = re.compile(r"[\[\(][^\]\)]*[\]\)]")


class Reply(Enum):
    CONFIRM = "confirm"
    CANCEL = "cancel"


def _canonical_reply_word(token):
    if token in _ALL_REPLY_WORDS:
        return token
    if len(token) < 7:
        return None
    best = max(_FUZZY_REPLY_WORDS, key=lambda word: SequenceMatcher(None, token, word).ratio())
    if SequenceMatcher(None, token, best).ratio() >= 0.85:
        return best
    return None


def classify_reply(text):
    """Classifica respostas curtas de forma conservadora."""
    if not isinstance(text, str):
        return None
    plain = _NOISE.sub(" ", text.lower())
    tokens = tokenize(plain)
    meaningful = [token for token in tokens if token not in FILLERS]
    if not meaningful or len(meaningful) > MAX_REPLY_TOKENS:
        return None

    confirm = cancel = unknown = 0
    for token in meaningful:
        word = _canonical_reply_word(token)
        if word in CANCEL:
            cancel += 1
        elif word in CONFIRM:
            confirm += 1
        else:
            unknown += 1
    # Só uma resposta pura vale: "para", "voltar" e "não" também aparecem em comandos e textos
    # ("role para baixo", "clique em voltar", "digitar não sei"), que não podem virar cancelamento.
    if unknown:
        return None
    if cancel:
        return Reply.CANCEL
    if confirm:
        return Reply.CONFIRM
    return None


FIELD_COMMANDS = {
    "enter": "press_enter", "pressionar enter": "press_enter", "pressione enter": "press_enter",
    "apertar enter": "press_enter", "deletar": "clear_text", "delete": "clear_text",
    "apagar tudo": "clear_text", "limpar campo": "clear_text",
    # Formas naturais da mesma ordem; "apagar tudo" é a que o reconhecimento erra menos.
    "deleta": "clear_text", "deletar tudo": "clear_text", "apaga tudo": "clear_text",
    "limpa o campo": "clear_text", "limpar o campo": "clear_text", "limpa campo": "clear_text",
    "apagar o campo": "clear_text", "apaga o campo": "clear_text",
    # Com "texto" o reconhecimento acerta mais: "colar" sozinho às vezes sai "Polar" ou "Blood".
    "copiar texto": "copy_text", "copiar": "copy_text", "copie": "copy_text", "copia": "copy_text",
    "copiar o texto": "copy_text", "copiar selecao": "copy_text", "copiar a selecao": "copy_text",
    "recortar texto": "cut_text", "recortar": "cut_text", "recorte": "cut_text", "recorta": "cut_text",
    "recortar o texto": "cut_text", "recortar selecao": "cut_text", "recortar a selecao": "cut_text",
    "colar texto": "paste_text", "colar": "paste_text", "cole": "paste_text", "cola": "paste_text",
    "colar o texto": "paste_text", "colar aqui": "paste_text", "cole aqui": "paste_text",
}


def normalized(text):
    return " ".join(tokenize(text))


def action_command(text):
    """Retorna (operação, alvo); o alvo nunca inclui a modalidade do clique."""
    phrase = normalized(text)
    prefixes = (
        ("context_menu", r"^(?:(?:abrir|abra|mostrar|mostre) (?:o )?menu de contexto|menu de contexto|(?:clique|clicar|clica) (?:com (?:o )?botao direito|com (?:o )?direito|direito)|botao direito)\b"),
        ("open_item", r"^(?:(?:clique|clicar|clica) duas vezes|(?:de |dar )?(?:um )?duplo clique|clique duplo)\b"),
    )
    for action, prefix in prefixes:
        match = re.match(prefix, phrase)
        if match:
            tokens = phrase[match.end():].split()
            break
    else:
        tokens = tokenize(text)
        if not tokens or tokens[0] not in ACTION_VERBS:
            return None
        action = "activate"
        tokens = tokens[1:]
    while tokens and tokens[0] in {"em", "no", "na", "o", "a", "de", "do", "da", "sobre"}:
        tokens = tokens[1:]
    return action, " ".join(tokens)


def target_phrase(text):
    command = action_command(text)
    return command[1] if command else None


def parse_number(text):
    value = target_phrase(text)
    tokens = tokenize(value if value is not None else text)
    if tokens and tokens[0] in {"numero", "item", "opcao"}:
        tokens = tokens[1:]
    tokens = [{"uma": "um", "duas": "dois", "catorze": "quatorze"}.get(token, token) for token in tokens]
    value = " ".join(tokens)
    if value.isdigit():
        return int(value) if 1 <= int(value) <= 200 else None
    return NUMBERS.get(value)


SELECT_VERB = r"(?:selecionar|selecione|seleciona)"


def select_command(text):
    """Seleção de texto no campo ativo: ("all"|"last_word"|"text", trecho) ou None.

    Só formas explícitas: "selecionar X" sozinho continua sendo o clique no controle X.
    """
    phrase = normalized(text)
    if re.fullmatch(SELECT_VERB + r" (?:tudo|todo o texto)", phrase):
        return "all", None
    if re.fullmatch(SELECT_VERB + r" (?:a )?ultima palavra", phrase):
        return "last_word", None
    match = re.fullmatch(SELECT_VERB + r" (?:a palavra|as palavras|o texto|o trecho) (.+)", phrase)
    return ("text", match[1]) if match else None


# Navegador: abas e páginas. Só valem com um navegador na frente (o backend confere).
BROWSER_COMMANDS = {normalized(text): action for text, action in (
    ("nova aba", "new_tab"), ("abrir nova aba", "new_tab"), ("abrir uma nova aba", "new_tab"),
    ("abra uma nova aba", "new_tab"), ("abre uma nova aba", "new_tab"), ("nova guia", "new_tab"),
    ("fechar aba", "close_tab"), ("fechar a aba", "close_tab"), ("feche a aba", "close_tab"),
    ("fecha a aba", "close_tab"), ("fechar guia", "close_tab"), ("fechar esta aba", "close_tab"),
    ("reabrir aba", "reopen_tab"), ("reabrir a aba", "reopen_tab"), ("reabra a aba", "reopen_tab"),
    ("próxima aba", "next_tab"), ("aba seguinte", "next_tab"), ("próxima guia", "next_tab"),
    ("aba anterior", "previous_tab"), ("guia anterior", "previous_tab"),
    ("última aba", "last_tab"),
    ("voltar página", "back"), ("voltar a página", "back"), ("volta a página", "back"),
    ("voltar uma página", "back"), ("página anterior", "back"),
    ("avançar página", "forward"), ("avançar a página", "forward"), ("avançar uma página", "forward"),
    ("próxima página", "forward"),
    ("recarregar", "reload"), ("recarregar página", "reload"), ("recarregar a página", "reload"),
    ("recarrega a página", "reload"), ("atualizar página", "reload"), ("atualizar a página", "reload"),
)}
BROWSER_ACTIONS = frozenset(BROWSER_COMMANDS.values()) | {"tab"}
# Tudo o que a frase pode ser quando só soa como um comando: campo e navegador disputam juntos.
SPOKEN_COMMANDS = {**FIELD_COMMANDS, **BROWSER_COMMANDS}


def browser_command(text):
    """("new_tab"|…|"tab", número da aba ou None), ou None. "Ir para a aba 3" vira ("tab", 3)."""
    phrase = normalized(text)
    if phrase in BROWSER_COMMANDS:
        return BROWSER_COMMANDS[phrase], None
    match = re.fullmatch(r"(?:(?:ir|va|vai) para (?:a )?)?aba (?:numero )?(.+)", phrase)
    number = parse_number(match[1]) if match else None
    if number is not None and 1 <= number <= 8:
        return "tab", number
    return None


def field_command(text, table=FIELD_COMMANDS):
    """Comando da frase: exato, ou o único que soa como ela ("Polar texto" é colar texto).

    A transcrição troca palavras curtas por outras parecidas. Só aceita quando todas as palavras
    soam como as de um comando e nenhuma ação diferente fica perto; nunca adivinha.
    """
    phrase = normalized(text)
    if phrase in table:
        return table[phrase]
    words = phrase.split()
    scored = {}
    for key, action in table.items():
        parts = key.split()
        if len(parts) != len(words):
            continue
        scores = [word_similarity(word, part) for word, part in zip(words, parts)]
        if min(scores, default=0) > 0:
            scored[action] = max(scored.get(action, 0), sum(scores) / len(scores))
    ranked = sorted(scored.items(), key=lambda item: -item[1])
    if not ranked or (len(ranked) > 1 and ranked[0][1] - ranked[1][1] < MIN_MARGIN):
        return None
    return ranked[0][0]

