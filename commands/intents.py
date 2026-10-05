from dataclasses import dataclass, field
import re
import unicodedata


@dataclass(frozen=True)
class Intent:
    name: str
    slots: dict[str, str] = field(default_factory=dict)
    phrase: str = ""


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern
    defaults: dict = field(default_factory=dict)


_EDGE_PUNCTUATION = " .,!?;:\"'"


def normalize(text):
    decomposed = unicodedata.normalize("NFD", text.lower())
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(without_accents.split()).strip(_EDGE_PUNCTUATION)


CATALOG = [
    Rule("parar", re.compile(r"parar|pare|para|cancelar|cancela")),
    Rule(
        "abrir_navegador",
        re.compile(r"(?:abre|abrir|abra)\s+(?:o\s+)?(?:navegador|chrome|edge|browser)"),
    ),
    Rule(
        "fechar_navegador",
        re.compile(
            r"(?:fecha|fechar|feche)\s+(?:o\s+)?(?:navegador|chrome|edge|browser)"
        ),
    ),
    Rule("nova_aba", re.compile(r"nova aba|(?:abre|abrir)(?:\s+uma)?\s+aba")),
    Rule("fechar_aba", re.compile(r"(?:fecha|fechar|feche)\s+(?:a\s+)?aba")),
    Rule(
        "rolar", re.compile(r"(?:rola|rolar|role)\s+para\s+(?P<direction>baixo|cima)")
    ),
    Rule("rolar", re.compile(r"desce|descer|para baixo"), {"direction": "baixo"}),
    Rule("rolar", re.compile(r"sobe|subir|para cima"), {"direction": "cima"}),
    Rule("voltar", re.compile(r"voltar|volta|pagina anterior")),
    Rule(
        "pesquisar",
        re.compile(
            r"(?:pesquisa|pesquisar|busca|buscar|procura|procurar)\s+(?:por\s+)?(?P<query>.+)"
        ),
    ),
    Rule(
        "abrir_site",
        re.compile(
            r"(?:abre|abrir|abra|vai para|ir para)\s+(?:o\s+|a\s+)?(?P<site>.+)"
        ),
    ),
]


def resolve(text):
    phrase = normalize(text)
    for rule in CATALOG:
        match = rule.pattern.fullmatch(phrase)
        if match is None:
            continue
        slots = {**rule.defaults, **{k: v for k, v in match.groupdict().items() if v}}
        return Intent(rule.name, slots, phrase)
    return None
