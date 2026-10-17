"""Vocabulário compartilhado de comandos; nunca interpreta texto ditado."""
import re
import unicodedata
from decimal import Decimal, InvalidOperation


def normalize(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    return " ".join(re.findall(r"[a-z0-9]+", "".join(c for c in text if not unicodedata.combining(c))))


SETTINGS_COMMANDS = {normalize(text) for text in (
    "abra as configurações", "abrir configurações", "abra configurações", "abrir as configurações",
    "abra as configurações do Jarvis", "abrir configurações do Jarvis", "abrir as configurações do Jarvis",
)}
HELP_COMMANDS = {"ajuda", "o que posso dizer", "quais comandos"}
HELP = {
    "general": "Diga mostrar números, clique em [nome], digitar [texto], role para baixo, role para cima, Enter, limpar campo, executar atalho ou abrir configurações.",
    "choosing": "Diga um dos números exibidos, confirmar após escolher, ou cancelar.",
    "confirming": "Diga confirmar ou sim para executar; cancelar ou não para abandonar.",
    "shortcut": "Diga a frase cadastrada nas configurações ou cancelar.",
    "scroll_waiting": "Diga mais, para cima, para baixo, role um pouco para baixo ou role uma página para cima. Parar, cancelar ou silêncio encerra a rolagem.",
    "scroll_choosing": "Diga o número de uma das áreas roláveis exibidas ou cancelar. A rolagem será executada diretamente nessa área.",
    "settings": "Diga ir para atalhos, ir para voz e microfone, ir para aparência e acessibilidade, ir para ajuda e diagnóstico, salvar configurações ou fechar configurações. Ajustes: tamanho do texto 22, tamanho dos números 18, ativar alto contraste, ativar lembrar posição, tempo de silêncio 3 segundos, tempo para começar a falar 15 segundos, duração máxima 30 segundos, limiar de ativação 0,3. Use desativar para desligar as opções.",
}
RESERVED = SETTINGS_COMMANDS | HELP_COMMANDS | {"cancelar", "cancele", "nao", "parar", "pare", "sim", "confirmar", "confirme", "executar atalho"}


def help_text(context="general"):
    return HELP.get(context, HELP["general"])


def all_help():
    return "\n\n".join(HELP.values()) + "\n\nParar por voz depende da transcrição. O botão cancela imediatamente a interação local; ações já enviadas não podem ser desfeitas."


def portuguese_number(text):
    """Números de ajustes (0–100), incluindo vírgula e casas decimais por extenso."""
    raw = text.strip().casefold()
    if re.fullmatch(r"\d+(?:[.,]\d+)?", raw):
        return float(Decimal(raw.replace(",", ".")))
    from screen_access.commands import NUMBERS
    words = normalize(raw).split()
    numbers = dict(NUMBERS, zero=0, uma=1, duas=2)
    separators = [i for i, word in enumerate(words) if word in ("virgula", "ponto")]
    if not separators:
        value = numbers.get(" ".join(words))
        if value is None:
            raise ValueError("Informe um número válido.")
        return float(value)
    if len(separators) != 1:
        raise ValueError("Informe um número válido.")
    index = separators[0]
    whole = numbers.get(" ".join(words[:index]))
    tail = words[index + 1:]
    if whole is None or not tail:
        raise ValueError("Informe um número válido.")
    if all(word in numbers and numbers[word] < 10 for word in tail):
        digits = "".join(str(numbers[word]) for word in tail)
    else:
        fraction = numbers.get(" ".join(tail))
        if fraction is None:
            raise ValueError("Informe um número válido.")
        digits = str(fraction)
    try:
        return float(Decimal(f"{whole}.{digits}"))
    except InvalidOperation as exc:
        raise ValueError("Informe um número válido.") from exc


def settings_command(text):
    """Retorna uma intenção restrita ao rascunho, sem referências a widgets."""
    phrase = normalize(text)
    sections = {"atalhos": 0, "voz e microfone": 1, "aparencia e acessibilidade": 2,
                "aparencia": 2, "ajuda e diagnostico": 3}
    if phrase.startswith("ir para ") and phrase[8:] in sections:
        return "section", sections[phrase[8:]]
    if phrase == "salvar configuracoes":
        return "save", None
    if phrase in ("cancelar configuracoes", "fechar configuracoes"):
        return "close", None
    for prefix, enabled in (("ativar ", True), ("desativar ", False)):
        for label, key in (("alto contraste", "high_contrast"), ("lembrar posicao", "remember_position")):
            if phrase == prefix + label:
                return key, enabled
    prefixes = {"tamanho do texto": "text_size", "tamanho dos numeros": "number_size",
                "tempo de silencio": "silence_seconds", "tempo para comecar a falar": "start_timeout",
                "duracao maxima": "max_seconds", "limiar de ativacao": "detection_threshold"}
    # Trabalhar sobre o texto sem acentos, preservando a pontuação decimal.
    raw = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)).strip()
    raw = " ".join(raw.split())
    for prefix, key in prefixes.items():
        if raw.startswith(prefix + " "):
            value = re.sub(r"\s+segundos?\s*[.!?]?$", "", raw[len(prefix):].strip()).rstrip(".!?")
            return key, portuguese_number(value)
    return None
