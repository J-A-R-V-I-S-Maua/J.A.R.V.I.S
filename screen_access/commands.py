"""Comandos explícitos e números PT-BR, sem interpretar fala livre como ação."""
from .matching import tokenize
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
CANCEL = {"cancelar", "cancele", "nao", "parar", "pare"}
CONFIRM = {"confirmar", "confirme", "sim"}
FIELD_COMMANDS = {
    "enter": "press_enter", "pressionar enter": "press_enter", "pressione enter": "press_enter",
    "apertar enter": "press_enter", "deletar": "clear_text", "delete": "clear_text",
    "apagar tudo": "clear_text", "limpar campo": "clear_text",
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
