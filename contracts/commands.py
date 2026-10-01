"""O modelo propõe dados; somente o host pode autorizar e executar uma ação."""
import re
import unicodedata
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

App = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.:-]+$")]
Browser = App
CAPABILITIES = ["open_app", "open_url", "search_web", "close_app"]
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


class AppDescriptor(StrictModel):
    id: App
    name: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=8)
    source: Literal["desktop", "menu", "system", "running"] = "menu"
    browser: bool = False
    can_force: bool = False

    @field_validator("aliases")
    @classmethod
    def bounded_aliases(cls, values):
        if any(not value.strip() or len(value) > 100 for value in values):
            raise ValueError("Alias inválido")
        return values


class OpenApp(StrictModel):
    kind: Literal["open_app"]
    app: App


class CloseApp(StrictModel):
    kind: Literal["close_app"]
    target_id: App


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


Action = Annotated[OpenApp | OpenUrl | SearchWeb | CloseApp, Field(discriminator="kind")]


class CommandContext(StrictModel):
    language: Literal["pt-BR"] = "pt-BR"
    available_apps: list[AppDescriptor] = Field(default_factory=list, max_length=20)
    running_apps: list[AppDescriptor] = Field(default_factory=list, max_length=20)
    default_browser: App | None = None
    capabilities: list[Literal["open_app", "open_url", "search_web", "close_app"]] = Field(default_factory=lambda: CAPABILITIES.copy())
    original_text: str | None = Field(default=None, max_length=2000)
    clarification_question: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def unique_ids(self):
        items = self.available_apps + self.running_apps
        if len(items) > 20 or len({a.id for a in items}) != len(items):
            raise ValueError("Use no máximo 20 candidatos com IDs distintos")
        if self.default_browser and not any(a.id == self.default_browser and a.browser for a in self.available_apps):
            raise ValueError("Navegador padrão ausente dos candidatos")
        return self


class InterpretRequest(StrictModel):
    protocol_version: Literal[2]
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
    protocol_version: Literal[2]
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
    """IDs de navegadores explicitamente selecionados, nunca caminhos."""
    if request.context.clarification_question and any(word in normalize(request.context.clarification_question)
                                                      for word in ("aplicativo", "navegador")):
        exact = [a.id for a in request.context.available_apps if a.browser and
                 normalize(request.text) in map(normalize, [a.name, *a.aliases])]
        if len(exact) == 1:
            return exact
    found = {}
    for text in (request.context.original_text, request.text):
        if not text:
            continue
        source = normalize(text)
        prefix = (r"(?:^(?:por favor )?(?:eu )?(?:(?:quero|gostaria|preciso)(?: de)? )?"
                  r"(?:abra|abre|abrir|inicie|iniciar) (?:o )?(?:navegador )?|"
                  r"\b(?:usando|utilizando|atraves do|pelo|com o navegador|no navegador) (?:o )?(?:navegador )?)")
        for app in request.context.available_apps:
            if app.browser:
                for alias in [app.name, *app.aliases]:
                    if re.search(prefix + re.escape(normalize(alias)) + r"\b", source):
                        found[app.id] = max(found.get(app.id, 0), len(normalize(alias)))
    return sorted(key for key, size in found.items() if size == max(found.values())) if found else []


def request_problem(request):
    source = normalize(request_source(request))
    app_request = re.match(r"(?:(?:por favor|por gentileza) )?(?:eu )?(?:(?:quero|gostaria|pode|poderia)(?: de| que voce)? )?(?:abra|abrir|inicie|iniciar|feche|fechar|encerre|encerrar)\b", source)
    if app_request and re.search(r"\b(?:e|depois|em seguida|tambem|para) (?:por favor )?(?:escreva|escrever|digite|digitar|clique|clicar|role|rolar|salve|salvar|apague|apagar)\b", source):
        return Decision(status="unsupported", message="Esse pedido inclui operações ainda não suportadas. Nenhuma parte será executada.")
    if re.search(r"\b(?:e|depois|em seguida|tambem) (?:por favor )?(?:abra|abre|abrir|inicie|iniciar|feche|fechar|encerre)\b", source):
        return Decision(status="unsupported", message="Faça um pedido de aplicativo por vez.")
    if len(requested_browsers(request)) > 1:
        return Decision(status="clarification", message="Qual navegador deseja usar?")
    if EMPTY_SEARCH.fullmatch(source):
        return Decision(status="clarification", message="O que deseja pesquisar?")
    # Preferências conhecidas são aliases; não determinam o catálogo.
    for browser, aliases in BROWSER_ALIASES.items():
        if re.search(r"\b(?:usando|utilizando|pelo) (?:o )?(?:navegador )?(?:" + "|".join(aliases) + r")\b", source):
            if not any(a.browser and any(normalize(alias) in [normalize(a.name), *map(normalize, a.aliases)]
                                        for alias in aliases) for a in request.context.available_apps):
                return Decision(status="unsupported", message="O navegador solicitado não está disponível neste computador.")
    return None


def named_action(request):
    """Resolve nomes exatos e ambiguidades; pedidos compostos seguem para a IA."""
    original = normalize(request.context.original_text or request.text)
    match = re.fullmatch(r"(?:por favor )?(?:eu )?(?:(?:quero|gostaria)(?: de)? )?"
        r"(abra|abre|abrir|inicie|iniciar|feche|fecha|fechar|encerre|encerrar) (?:o |a )?(.+?)(?: por favor)?", original)
    if not match:
        return None
    closing = match[1] in {"feche", "fecha", "fechar", "encerre", "encerrar"}
    query = normalize(request.text) if request.context.original_text else match[2]
    apps = request.context.running_apps if closing else request.context.available_apps
    if query in {"navegador", "browser"}:
        matches = [a for a in apps if a.browser and (closing or a.id == request.context.default_browser)]
    else:
        matches = [a for a in apps if query in map(normalize, [a.name, *a.aliases])]
    if len(matches) > 1:
        names = "; ".join(a.name for a in matches[:3])
        return Decision(status="clarification", message=(f"Qual aplicativo deseja: {names}? Diga o nome completo com a variante.")[:300])
    if len(matches) == 1:
        action = CloseApp(kind="close_app", target_id=matches[0].id) if closing else OpenApp(kind="open_app", app=matches[0].id)
        return Decision(status="action", action=action)
    if closing and not apps:
        return Decision(status="unsupported", message="Não encontrei um aplicativo disponível para fechamento.")
    return None


def validate_proposal(decision: Decision, request: InterpretRequest) -> Decision:
    action = decision.action
    if action is None:
        return decision
    problem = request_problem(request)
    if problem:
        raise ValueError(problem.message)
    if action.kind not in request.context.capabilities:
        raise ValueError("Capacidade indisponível")
    named = named_action(request)
    if named and (named.status != "action" or named.action != action):
        raise ValueError(named.message or "A proposta não corresponde ao aplicativo solicitado")
    if isinstance(action, CloseApp):
        if action.target_id not in {a.id for a in request.context.running_apps}:
            raise ValueError("Aplicativo em execução indisponível")
        return decision
    available = {a.id: a for a in request.context.available_apps}
    preferred = requested_browsers(request)
    selected = action.app if isinstance(action, OpenApp) else action.browser
    if preferred and selected != preferred[0]:
        raise ValueError("A proposta não respeitou o navegador solicitado")
    if isinstance(action, OpenApp):
        if action.app not in available:
            raise ValueError("Aplicativo indisponível")
    else:
        browser = request.context.default_browser if action.browser == "default" else action.browser
        if browser not in available or not available[browser].browser:
            raise ValueError("Navegador indisponível")
    if isinstance(action, OpenUrl) and action.url.rstrip("/") not in source_urls(request_source(request)):
        raise ValueError("O endereço não foi informado pelo usuário")
    return decision
