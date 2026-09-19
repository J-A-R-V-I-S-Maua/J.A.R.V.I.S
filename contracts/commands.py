"""O modelo propõe dados; somente o host pode autorizar e executar uma ação."""
import re
import unicodedata
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Browser = Literal["default", "chrome", "edge", "brave", "firefox", "chromium", "opera", "vivaldi", "safari"]
App = Literal["browser", "chrome", "edge", "brave", "firefox", "chromium", "opera", "vivaldi", "safari", "explorer", "notepad"]
CAPABILITIES = ["open_app", "open_url", "search_web"]
SITE_ALIASES = {"google": "https://www.google.com", "youtube": "https://www.youtube.com"}
BROWSER_ALIASES = {
    "chrome": ("google chrome", "chrome"), "edge": ("microsoft edge", "edge"),
    "brave": ("brave",), "firefox": ("mozilla firefox", "firefox"),
    "chromium": ("chromium",), "opera": ("opera",),
    "vivaldi": ("vivaldi",), "safari": ("safari",),
}
EMPTY_SEARCH = re.compile(
    r"(?:por favor )?(?:eu )?"
    r"(?:(?:gostaria|quero|queria|preciso|pode|poderia)(?: de| que voce)? )?"
    r"(?:pesquise|pesquisei|pesquisar|busque|buscar|procure|procurar|faca uma pesquisa|fazer uma pesquisa)"
    r"(?: no| na| em| pelo| pela)? (?:google|youtube|internet|web)(?: por favor)?"
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    return " ".join(re.sub(r"[^\w\s]", " ", "".join(c for c in text if not unicodedata.combining(c))).split())


def safe_url(value: str) -> str:
    value = value.strip()
    if any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
        raise ValueError("Endereço inválido")
    if "://" not in value:
        value = "https://" + value
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Somente endereços HTTP/HTTPS sem credenciais são permitidos")
    # Também força a validação da porta antes de despachar ao navegador.
    _ = parsed.port
    if not re.fullmatch(r"[\w.\-:]+", parsed.hostname) or "." not in parsed.hostname:
        raise ValueError("Informe um domínio completo")
    return urlunsplit(parsed)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class OpenApp(StrictModel):
    kind: Literal["open_app"]
    app: App


class OpenUrl(StrictModel):
    kind: Literal["open_url"]
    url: str = Field(min_length=1, max_length=2048)
    browser: Browser = "default"

    _url = field_validator("url")(safe_url)


class SearchWeb(StrictModel):
    kind: Literal["search_web"]
    query: str = Field(min_length=1, max_length=1000)
    provider: Literal["google", "youtube"] = "google"
    browser: Browser = "default"

    @field_validator("query")
    @classmethod
    def nonempty(cls, value):
        if not any(c.isalnum() for c in value) or any(ord(c) < 32 for c in value):
            raise ValueError("Pesquisa vazia ou inválida")
        return value.strip()


Action = Annotated[OpenApp | OpenUrl | SearchWeb, Field(discriminator="kind")]


class CommandContext(StrictModel):
    language: Literal["pt-BR"] = "pt-BR"
    available_apps: list[App]
    capabilities: list[Literal["open_app", "open_url", "search_web"]] = Field(default_factory=lambda: CAPABILITIES.copy())
    original_text: str | None = Field(default=None, max_length=2000)
    clarification_question: str | None = Field(default=None, max_length=300)


class InterpretRequest(StrictModel):
    interaction_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2000)
    context: CommandContext


class Decision(StrictModel):
    status: Literal["action", "clarification", "unsupported"]
    action: Action | None = None
    message: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def consistent(self):
        if (self.status == "action") != (self.action is not None):
            raise ValueError("Estado incompatível com a ação")
        if self.status != "action" and not self.message.strip():
            raise ValueError("Resposta sem explicação")
        return self


class InterpretResponse(Decision):
    interaction_id: str


def request_source(request):
    return " ".join(filter(None, (request.context.original_text, request.text)))


def source_urls(source):
    """Destinos literais para o schema dinâmico; o modelo não completa domínios."""
    candidates = re.findall(r"(?:https?://)?(?:[\w-]+\.)+[\w-]+(?::\d+)?(?:[/\?#][^\s]*)?", source, re.I)
    allowed = set()
    for candidate in candidates:
        try:
            allowed.add(safe_url(candidate.rstrip(".,;!")).rstrip("/"))
        except ValueError:
            pass
    for alias, url in SITE_ALIASES.items():
        if alias in normalize(source).split():
            allowed.add(url)
    return sorted(allowed)


def requested_browsers(request):
    """Preferências explícitas, sem confundir 'pesquise sobre Firefox' com seleção."""
    found = set()
    for text in (request.context.original_text, request.text):
        if not text:
            continue
        source = normalize(text)
        prefix = (
            r"(?:^(?:por favor )?(?:eu )?(?:(?:quero|gostaria|preciso)(?: de)? )?"
            r"(?:abra|abre|abrir|inicie|iniciar) (?:o )?(?:navegador )?|"
            r"\b(?:usando|utilizando|atraves do|pelo|com o navegador|no navegador) "
            r"(?:o )?(?:navegador )?)"
        )
        for app, aliases in BROWSER_ALIASES.items():
            if re.search(prefix + "(?:" + "|".join(map(re.escape, aliases)) + r")\b", source):
                found.add(app)
    return sorted(found)


def request_problem(request):
    """Guardas conservadoras para ambiguidades observadas nos testes do modelo."""
    source = normalize(request_source(request))
    if re.search(r"\b(?:e|depois|em seguida|tambem) (?:por favor )?(?:abra|abre|abrir|inicie|iniciar)\b", source):
        return Decision(status="unsupported", message="Faça um pedido de abertura por vez.")
    browsers = requested_browsers(request)
    if len(browsers) > 1:
        return Decision(status="unsupported", message="Escolha um navegador por pedido.")
    if browsers and browsers[0] not in request.context.available_apps:
        return Decision(status="unsupported", message="O navegador solicitado não está disponível neste computador.")
    if EMPTY_SEARCH.fullmatch(source):
        return Decision(status="clarification", message="O que deseja pesquisar?")
    return None


def validate_proposal(decision: Decision, request: InterpretRequest) -> Decision:
    """Aplica a mesma política no servidor e no host, inclusive origem do destino."""
    action = decision.action
    if action is None:
        return decision
    problem = request_problem(request)
    if problem:
        raise ValueError(problem.message)
    available = request.context.available_apps
    if action.kind not in request.context.capabilities:
        raise ValueError("Capacidade indisponível")
    browsers = requested_browsers(request)
    selected = action.app if isinstance(action, OpenApp) else action.browser
    if browsers and selected != browsers[0]:
        raise ValueError("A proposta não respeitou o navegador solicitado")
    if isinstance(action, OpenApp):
        if action.app not in available:
            raise ValueError("Aplicativo indisponível")
    else:
        browser = "browser" if action.browser == "default" else action.browser
        if browser not in available:
            raise ValueError("Navegador indisponível")
    if isinstance(action, OpenUrl):
        if action.url.rstrip("/") not in source_urls(request_source(request)):
            raise ValueError("O endereço não foi informado pelo usuário")
    return decision
