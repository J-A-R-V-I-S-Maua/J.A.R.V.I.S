"""Escolha do item da tela a partir de uma frase. Sem dependência do sistema operacional."""

from dataclasses import dataclass
import re
import unicodedata

# Palavras de comando e artigos: não ajudam a identificar qual item o usuário quer.
STOPWORDS = frozenset({
    "clique", "clica", "clicar", "abra", "abre", "abrir", "acesse", "acessar", "selecione",
    "selecionar", "escolha", "escolher", "va", "ir", "ative", "ativar", "aperte", "apertar",
    "toque", "para", "pra", "no", "na", "nos", "nas", "o", "a", "os", "as", "um", "uma",
    "de", "do", "da", "dos", "das", "em", "por", "favor", "video",
})
NUMBER_WORDS = frozenset({"numero", "item", "opcao"})
MIN_PREFIX = 4
MAX_CANDIDATES = 5


@dataclass(frozen=True)
class Choice:
    status: str  # "match", "ambiguous" ou "none"
    index: int | None = None  # posição (a partir de 0) do item escolhido, quando "match"
    candidates: tuple = ()  # posições dos itens possíveis, quando "ambiguous"


def tokenize(text):
    """Minúsculas, sem acentos nem pontuação, separado em palavras."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.findall(r"[a-z0-9]+", plain)


def _explicit_number(meaningful):
    """Reconhece "33", "número 33" e "item 33"; devolve o número ou None."""
    if len(meaningful) == 1 and meaningful[0].isdigit():
        return int(meaningful[0])
    if len(meaningful) == 2 and meaningful[0] in NUMBER_WORDS and meaningful[1].isdigit():
        return int(meaningful[1])
    return None


def _word_in_name(word, name_tokens):
    """Palavra igual, ou prefixo dela (a partir de 4 letras) para tolerar plural e flexão."""
    return any(
        token == word or (len(word) >= MIN_PREFIX and token.startswith(word))
        for token in name_tokens
    )


def choose(phrase, names):
    """Decide qual dos itens (identificados por nome) a frase pede.

    Só devolve "match" quando há exatamente um item compatível. Com vários, devolve
    "ambiguous" e as opções; sem nenhum, "none". Nunca adivinha.
    """
    meaningful = [token for token in tokenize(phrase) if token not in STOPWORDS]

    number = _explicit_number(meaningful)
    if number is not None:
        if 1 <= number <= len(names):
            return Choice("match", number - 1)
        return Choice("none")

    if not meaningful:
        return Choice("none")

    candidates = []
    for index, name in enumerate(names):
        name_tokens = tokenize(name)
        if all(_word_in_name(word, name_tokens) for word in meaningful):
            candidates.append(index)

    if not candidates:
        return Choice("none")
    if len(candidates) == 1:
        return Choice("match", candidates[0])
    return Choice("ambiguous", candidates=tuple(candidates[:MAX_CANDIDATES]))
