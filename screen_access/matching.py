"""Escolha do item da tela a partir de uma frase. Sem dependência do sistema operacional.

A frase vem do reconhecimento de voz, que erra ("abrir" vira "abril", "Kant" vira "cante"),
então a comparação é por semelhança sonora e só escolhe quando há folga clara.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata

# Palavras de comando e artigos: não ajudam a identificar qual item o usuário quer.
STOPWORDS = frozenset({
    "clique", "clica", "clicar", "abra", "abre", "abrir", "acesse", "acessar", "selecione",
    "selecionar", "escolha", "escolher", "va", "ir", "ative", "ativar", "aperte", "apertar",
    "toque", "para", "pra", "no", "na", "nos", "nas", "o", "a", "os", "as", "um", "uma",
    "de", "do", "da", "dos", "das", "em", "por", "favor", "video",
})
# Verbos que o reconhecimento costuma trocar por palavras parecidas ("abriu", "abril", "abrem").
COMMAND_VERBS = ("abrir", "clicar", "acessar", "selecionar", "escolher", "ativar", "apertar")
NUMBER_WORDS = frozenset({"numero", "item", "opcao"})

MIN_PREFIX = 4  # palavra a partir de 4 letras vale como início de outra ("noticia" ~ "noticias")
MIN_FUZZY_LENGTH = 3
MIN_WORD_SIMILARITY = 0.75  # semelhança mínima para duas palavras contarem como a mesma
MIN_VERB_SIMILARITY = 0.6  # só vale para a primeira palavra da frase
MIN_SCORE = 0.75  # nota média mínima de um item
MIN_SINGLE_WORD_SCORE = 0.9  # frase de uma palavra não tem outra para confirmar: só igual ou início
MIN_MARGIN = 0.1  # folga exigida do 1º colocado sobre o 2º
MAX_CANDIDATES = 5


@dataclass(frozen=True)
class Choice:
    status: str  # "match", "ambiguous" ou "none"
    index: int | None = None  # posição (a partir de 0) do item escolhido, quando "match"
    candidates: tuple = ()  # posições dos itens possíveis, quando "ambiguous"


def tokenize(text):
    """Minúsculas, sem acentos nem pontuação, separado em palavras."""
    decomposed = unicodedata.normalize("NFKD", text.lower().replace("ç", "s"))
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.findall(r"[a-z0-9]+", plain)


def sound_key(word):
    """Aproxima a pronúncia em português, para "cant" e "kant" virarem a mesma chave."""
    word = re.sub(r"ch|sh", "x", word)
    word = re.sub(r"ck|qu", "k", word)
    word = re.sub(r"c(?=[ei])", "s", word)
    word = word.replace("c", "k").replace("ph", "f").replace("w", "v")
    word = word.replace("y", "i").replace("z", "s")
    word = re.sub(r"([a-z])\1+", r"\1", word)
    return word.lstrip("h") or word


def _similarity(word, other):
    """Nota de 0 a 1 para duas palavras já convertidas em chave sonora."""
    if word == other:
        return 1.0
    shorter, longer = sorted((word, other), key=len)
    if len(shorter) >= MIN_PREFIX and longer.startswith(shorter):
        return 0.9
    if word.isalpha() and other.isalpha() and min(len(word), len(other)) >= MIN_FUZZY_LENGTH:
        ratio = SequenceMatcher(None, word, other).ratio()
        if ratio >= MIN_WORD_SIMILARITY:
            return ratio
    return 0.0


def _looks_like_command(token):
    if token in STOPWORDS:
        return True
    key = sound_key(token)
    return any(
        abs(len(key) - len(verb)) <= 2
        and SequenceMatcher(None, key, sound_key(verb)).ratio() >= MIN_VERB_SIMILARITY
        for verb in COMMAND_VERBS
    )


def _explicit_number(meaningful):
    """Reconhece "33", "número 33" e "item 33"; devolve o número ou None."""
    if len(meaningful) == 1 and meaningful[0].isdigit():
        return int(meaningful[0])
    if len(meaningful) == 2 and meaningful[0] in NUMBER_WORDS and meaningful[1].isdigit():
        return int(meaningful[1])
    return None


def _score(wanted, name_keys):
    """Média, entre as palavras pedidas, da melhor semelhança com alguma palavra do nome."""
    if not name_keys:
        return 0.0
    return sum(max(_similarity(word, key) for key in name_keys) for word in wanted) / len(wanted)


def choose(phrase, names):
    """Decide qual dos itens (identificados por nome) a frase pede.

    Só devolve "match" quando um item se destaca com folga. Quando há empate ou dúvida,
    devolve "ambiguous" e as opções; sem nenhum candidato, "none". Nunca adivinha.
    """
    tokens = tokenize(phrase)
    meaningful = [token for token in tokens if token not in STOPWORDS]

    number = _explicit_number(meaningful)
    if number is not None:
        if 1 <= number <= len(names):
            return Choice("match", number - 1)
        return Choice("none")

    # O verbo vem primeiro e pode chegar distorcido; quem identifica o item são as outras palavras.
    if tokens and _looks_like_command(tokens[0]):
        tokens = tokens[1:]
    wanted = [sound_key(token) for token in tokens if token not in STOPWORDS]
    if not wanted:
        return Choice("none")

    minimum = MIN_SCORE if len(wanted) > 1 else MIN_SINGLE_WORD_SCORE
    scored = []
    for index, name in enumerate(names):
        score = _score(wanted, [sound_key(token) for token in tokenize(name)])
        if score >= minimum:
            scored.append((score, index))
    if not scored:
        return Choice("none")

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    best = scored[0][0]
    close = [index for score, index in scored if score >= best - MIN_MARGIN]
    if len(close) == 1:
        return Choice("match", close[0])
    return Choice("ambiguous", candidates=tuple(close[:MAX_CANDIDATES]))
