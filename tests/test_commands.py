import asyncio
import json
import threading

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from contracts.commands import (CommandContext, Decision, InterpretRequest,
                               OpenApp, OpenUrl, SearchWeb, AppDescriptor, validate_proposal)
from host_agent.client import CommandClient, CommandError
from host_agent.executor import NativeExecutor
from host_agent.catalog import Catalog, Inventory, Entry
from host_agent.service import AssistantService
from wakeword.events import Cancelled, State



NAMES = {"browser": "navegador", "notepad": "Bloco de Notas", "explorer": "Explorador de Arquivos",
         "chrome": "Google Chrome", "edge": "Microsoft Edge"}


def descriptor(identifier):
    return AppDescriptor(id=identifier, name=NAMES.get(identifier, identifier),
                         aliases=[identifier], browser=identifier not in {"notepad", "explorer"})


def context(ids):
    return CommandContext(available_apps=[descriptor(i) for i in ids],
                          default_browser="browser" if "browser" in ids else None)


def fake_executor(apps, launches):
    entries = {i: Entry(i, NAMES.get(i, i), "exe", path, path, aliases=(i,),
                      browser=i not in {"notepad", "explorer"}, default=i == "browser") for i, path in apps.items()}
    catalog = Catalog(scanner=lambda: Inventory(entries.copy()))
    catalog.refresh()
    executor = NativeExecutor(catalog, launcher=lambda entry, url: launches.append([entry.target, *([url] if url else [])]))
    executor.entries = entries
    return executor


class EmptyRunning:
    error = ""
    def inventory(self, entries):
        return []


def request(text="abra o bloco de notas", apps=None):
    return InterpretRequest(protocol_version=2, interaction_id="test:1", text=text,
        context=context(apps or ["browser", "edge", "chrome", "explorer", "notepad"]))


@pytest.mark.parametrize("url", ["file:///C:/x", "javascript:alert(1)", "https://user:pass@example.com",
                                  "https://example.com:bad", "https://example.com/\n--flag", "--app=bad"])
def test_unsafe_destinations_rejected(url):
    with pytest.raises(ValueError):
        OpenUrl(kind="open_url", url=url)


def test_model_cannot_invent_url_or_executable():
    with pytest.raises(ValueError):
        validate_proposal(Decision(status="action", action=OpenUrl(kind="open_url", url="https://other.example")), request("abra meu banco"))
    with pytest.raises(ValidationError):
        OpenApp(kind="open_app", app="powershell", path="evil.exe")
    with pytest.raises(ValueError):
        validate_proposal(Decision(status="action", action=OpenApp(kind="open_app", app="chrome")), request(apps=["notepad"]))


@pytest.mark.parametrize("text,url", [("abra example.com", "https://example.com"),
                                      ("abra o YouTube", "https://www.youtube.com"),
                                      ("abra https://example.com/a?q=x", "https://example.com/a?q=x")])
def test_explicit_destinations(text, url):
    validate_proposal(Decision(status="action", action=OpenUrl(kind="open_url", url=url)), request(text))


def test_executor_uses_fixed_argv_and_encodes_query():
    launches = []
    executor = fake_executor({"browser": "C:/edge.exe", "notepad": "C:/notepad.exe"}, launches)
    executor.execute(SearchWeb(kind="search_web", query='a & b --flag "x"', provider="youtube"), context(["browser", "notepad"]))
    assert launches == [["C:/edge.exe", "https://www.youtube.com/results?search_query=a+%26+b+--flag+%22x%22"]]
    executor.execute(OpenApp(kind="open_app", app="notepad"), context(["browser", "notepad"]))
    assert launches[1] == ["C:/notepad.exe"]


class FakeSpeaker:
    def __init__(self):
        self.messages = []
        self.on_speak = lambda: None

    def speak(self, text, cancelled):
        self.messages.append(text)
        self.on_speak()
        if cancelled():
            raise Cancelled()


class FakeSpeech:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.before_answer = lambda: None

    def prepare(self, cancelled):
        if cancelled():
            raise Cancelled()

    def capture(self, cancelled, partial, listening, processing, timeout=None):
        listening()
        partial("sim", 1)  # Uma parcial afirmativa NÃO deve autorizar.
        self.before_answer()
        if cancelled():
            raise Cancelled()
        processing()
        return next(self.answers)


class FakeClient:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.requests = []
        self.on_interpret = lambda: None

    def interpret(self, req, cancelled):
        self.requests.append(req)
        self.on_interpret()
        return next(self.decisions)


def service(answers=("sim",), decisions=None):
    launched = []
    speaker = FakeSpeaker()
    client = FakeClient(decisions or [Decision(status="action", action=OpenApp(kind="open_app", app="notepad"))])
    result = AssistantService(command_client=client, speaker=speaker, speech_input=FakeSpeech(answers),
        executor=fake_executor({"notepad": "C:/notepad.exe", "browser": "C:/edge.exe"}, launched), running=EmptyRunning())
    result._interaction_id = 1
    # A espera acústica real é testada na integração; evitar retardar casos de lógica.
    def say(text, interaction_id):
        result._publish(State.SPEAKING, text, interaction_id=interaction_id)
        speaker.speak(text, result._cancelled)
    result._say = say
    return result, launched


@pytest.mark.parametrize("answer", ["sim", "Confirmo.", "Pode executar!"])
def test_only_explicit_final_confirmation_authorizes(answer):
    agent, launched = service([answer])
    agent.handle_command("abra o bloco de notas", 1)
    assert launched == [["C:/notepad.exe"]]
    assert "Bloco de Notas" in agent.speaker.messages[0]


@pytest.mark.parametrize("answers", [("", ""), ("talvez", "sim mas nao"), ("não",), ("cancelar",)])
def test_no_confirmation_no_execution_even_with_yes_partial(answers):
    agent, launched = service(answers)
    try:
        agent.handle_command("abra o bloco de notas", 1)
    except Cancelled:
        pass
    assert not launched
    assert len(agent.speaker.messages) <= 2


@pytest.mark.parametrize("stage", ["interpret", "speak", "confirm", "dispatch"])
def test_cancel_at_every_boundary(stage):
    agent, launched = service()
    if stage == "interpret":
        agent.command_client.on_interpret = agent.cancel
    elif stage == "speak":
        agent.speaker.on_speak = agent.cancel
    elif stage == "confirm":
        agent.speech_input.before_answer = agent.cancel
    else:
        original = agent._dispatch
        def dispatch(*args):
            agent.cancel()
            original(*args)
        agent._dispatch = dispatch
    with pytest.raises(Cancelled):
        agent.handle_command("abra o bloco de notas", 1)
    assert not launched


def test_same_interaction_cannot_launch_twice():
    decision = Decision(status="action", action=OpenApp(kind="open_app", app="notepad"))
    agent, launched = service(["sim", "sim"], [decision, decision])
    agent.handle_command("abra o bloco de notas", 1)
    agent.handle_command("abra o bloco de notas", 1)
    assert len(launched) == 1


def test_one_clarification_then_confirmation():
    agent, launched = service(["acessibilidade", "sim"], [
        Decision(status="clarification", message="O que deseja pesquisar?"),
        Decision(status="action", action=SearchWeb(kind="search_web", query="acessibilidade"))])
    agent.handle_command("pesquise no google", 1)
    assert agent.command_client.requests[1].context.original_text == "pesquise no google"
    assert agent.command_client.requests[1].text == "acessibilidade"
    assert len(launched) == 1


def test_second_clarification_or_unsupported_cannot_execute():
    ask = Decision(status="clarification", message="Qual endereço?")
    agent, launched = service(["aquele"], [ask, ask])
    agent.handle_command("abra o site", 1)
    assert not launched
    agent, launched = service([], [Decision(status="unsupported", message="Escrita ainda não disponível.")])
    agent.handle_command("abra e escreva", 1)
    assert not launched


def test_degraded_safety_keeps_text_without_actions():
    agent, launched = service()
    agent.action_error = "Detector indisponível"
    agent.handle_command("abra o bloco de notas", 1)
    assert agent.snapshot.state is State.ERROR
    assert "abra o bloco de notas" in agent.snapshot.text
    assert not launched and not agent.command_client.requests


def test_http_client_rejects_late_interaction(monkeypatch):
    import host_agent.client as module
    actual = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={
        "protocol_version": 2, "interaction_id": "old", "status": "action", "action": {"kind": "open_app", "app": "notepad"}, "message": ""}))
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: actual(transport=transport, **kwargs))
    with pytest.raises(CommandError):
        CommandClient().interpret(request(), lambda: False)


def test_http_client_cancels_inflight_request(monkeypatch):
    import host_agent.client as module
    actual = httpx.AsyncClient
    cancelled = threading.Event()
    transport_cancelled = []
    async def slow(_):
        cancelled.set()
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            transport_cancelled.append(True)
            raise
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: actual(transport=httpx.MockTransport(slow), **kwargs))
    with pytest.raises(Cancelled):
        CommandClient().interpret(request(), cancelled.is_set)
    assert transport_cancelled


def test_api_structured_output_and_invalid_json(monkeypatch):
    import api.commands as module
    actual = httpx.AsyncClient
    responses = iter(['{"kind":"open_app","app":"notepad","provider":"google","browser":"default"}', '{bad'])
    def handler(req):
        body = json.loads(req.content)
        assert body["options"]["num_ctx"] == 4096
        assert body["format"]["additionalProperties"] is False
        return httpx.Response(200, json={"done": True, "message": {"content": next(responses)}})
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: actual(transport=httpx.MockTransport(handler), **kwargs))
    app = FastAPI()
    app.include_router(module.router)
    with TestClient(app) as client:
        assert client.post("/commands/interpret", json=request("Gostaria que o Bloco de Notas fosse iniciado").model_dump()).json()["action"]["app"] == "notepad"
        assert client.post("/commands/interpret", json=request("Gostaria que o Bloco de Notas fosse iniciado").model_dump()).status_code == 502


def test_command_factory_opt_out(monkeypatch):
    from wakeword.detect_microphone_service import create_service, VoiceService
    monkeypatch.setenv("COMMANDS_ENABLED", "0")
    monkeypatch.setenv("TRANSCRIPTION_MODE", "batch")
    assert type(create_service()) is VoiceService


def test_model_requires_browser_provider_and_preserves_explicit_choice():
    from api.commands import ModelProposal
    with pytest.raises(ValidationError):
        ModelProposal(kind="search_web", query="receitas")
    decision = ModelProposal(kind="open_url", url="https://www.youtube.com",
                             provider="google", browser="chrome").decision()
    assert decision.action.browser == "chrome"
    with pytest.raises(ValueError):
        SearchWeb(kind="search_web", query=".")


def test_late_partial_cannot_replace_confirmation_result():
    agent, _ = service(["sim"])
    callbacks = []
    original = agent.speech_input.capture
    def capture(cancelled, partial, *args, **kwargs):
        callbacks.append(partial)
        return original(cancelled, partial, *args, **kwargs)
    agent.speech_input.capture = capture
    agent.handle_command("abra o bloco de notas", 1)
    result = agent.snapshot
    callbacks[0]("outra resposta", 999)
    assert agent.snapshot is result


def test_safety_failure_immediately_before_dispatch_blocks_action():
    agent, launches = service()
    def safety_fails():
        agent.action_error = "Detector falhou"
    agent.speech_input.before_answer = safety_fails
    with pytest.raises(RuntimeError):
        agent.handle_command("abra o bloco de notas", 1)
    assert not launches


def test_api_unavailable_model_does_not_disable_routes(monkeypatch):
    import api.commands as module
    actual = httpx.AsyncClient
    def unavailable(req):
        raise httpx.ConnectError("offline", request=req)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: actual(transport=httpx.MockTransport(unavailable), **kwargs))
    app = FastAPI()
    app.include_router(module.router)
    with TestClient(app) as client:
        assert client.get("/commands/health").json()["ready"] is False
        assert client.post("/commands/interpret", json=request("Gostaria que o Bloco de Notas fosse iniciado").model_dump()).status_code == 503


@pytest.mark.parametrize("text,status", [("Abra o Chrome e abra o Explorer", "unsupported"),
                                       ("Pesquise no YouTube", "clarification"),
                                       ("Eu gostaria de pesquisar no YouTube, por favor.", "clarification"),
                                       ("Quero fazer uma pesquisa no Google", "clarification"),
                                       ("Pode pesquisar na internet?", "clarification")])
def test_observed_ambiguous_requests_are_guarded_on_both_sides(text, status):
    from contracts.commands import request_problem
    req = request(text)
    assert request_problem(req).status == status
    with pytest.raises(ValueError):
        validate_proposal(Decision(status="action", action=OpenApp(kind="open_app", app="chrome")), req)


def test_browser_plus_search_remains_one_action():
    from contracts.commands import request_problem
    assert request_problem(request("Abra o navegador e pesquise carros no YouTube")) is None


def test_clarification_context_separates_original_request_from_answer():
    from api.commands import model_messages
    from contracts.commands import request_problem
    req = InterpretRequest(protocol_version=2, interaction_id="clarification", text="receitas de pão",
        context=CommandContext(available_apps=[descriptor("browser")], default_browser="browser", original_text="Pesquise no YouTube",
                               clarification_question="O que deseja pesquisar?"))
    messages = model_messages(req, ["https://www.youtube.com"])
    assert request_problem(req) is None
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert json.loads(messages[1]["content"])["text"] == "Pesquise no YouTube"
    assert messages[2]["content"] == "O que deseja pesquisar?"
    assert json.loads(messages[3]["content"])["text"] == "receitas de pão"
