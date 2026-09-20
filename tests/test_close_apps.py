import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from contracts.commands import CloseApp, Decision
from host_agent.catalog import Catalog, Inventory
from host_agent.executor import NativeExecutor
from host_agent.running import ProcessRef, Window, RunningApps, TargetChanged
from host_agent.service import AssistantService
from wakeword.events import Cancelled, State


class Backend:
    def __init__(self, ref):
        self.ref = ref
        self.items = [Window("10", ref, "/editor", "Editor de Teste")]
        self.alive = {ref.pid}
        self.requests = []
        self.normal_exits = False

    def windows(self):
        return list(self.items)

    def close(self, window):
        self.requests.append(("close", window.handle))
        if self.normal_exits:
            self.alive.clear()
            self.items.clear()

    def force(self, ref):
        self.requests.append(("force", ref.pid))
        self.alive.discard(ref.pid)
        self.items.clear()


@pytest.fixture
def runtime(monkeypatch):
    ref = ProcessRef(2147483000, 123.0, "/editor", "user")
    backend = Backend(ref)
    monkeypatch.setattr(ProcessRef, "valid", lambda p: p.pid in backend.alive)
    manager = RunningApps(backend, wait_seconds=0)
    manager.inventory({})
    return manager, backend


def test_manual_application_and_multiple_windows_are_grouped(runtime):
    manager, backend = runtime
    backend.items.append(Window("11", backend.ref, "/editor", "Editor de Teste"))
    applications = manager.inventory({})
    assert len(applications) == 1 and len(applications[0].windows) == 2
    backend.normal_exits = True
    assert manager.close_normal(applications[0], lambda: False, lambda call: call())
    assert backend.requests == [("close", "10")]


def test_changed_windows_require_new_confirmation(runtime):
    manager, backend = runtime
    target = next(iter(manager.targets.values()))
    backend.items.append(Window("12", backend.ref, "/editor", "Editor de Teste"))
    with pytest.raises(TargetChanged):
        manager.close_normal(target, lambda: False, lambda call: call())
    assert not backend.requests


def test_distinct_running_installations_have_distinguishable_names(runtime):
    manager, backend = runtime
    backend.items.append(replace(backend.items[0], handle="11", identity="/other-editor"))
    targets = manager.inventory({})
    assert len({app.name for app in targets}) == 2
    assert all("Editor de Teste" in app.aliases for app in targets)


def test_already_closed_target_does_not_close_a_replacement(runtime):
    manager, backend = runtime
    target = next(iter(manager.targets.values()))
    backend.items = []
    with pytest.raises(TargetChanged):
        manager.close_normal(target, lambda: False, lambda call: call())
    assert not backend.requests


def test_process_identity_checks_creation_time_and_user(monkeypatch):
    ref = ProcessRef(50, 100, "/app", "one")
    monkeypatch.setattr(ProcessRef, "read", classmethod(lambda cls, pid: replace(ref, created=101)))
    assert not ref.valid()
    monkeypatch.setattr(ProcessRef, "read", classmethod(lambda cls, pid: replace(ref, user="other")))
    assert not ref.valid()


def test_system_shared_process_cannot_be_forced(runtime):
    manager, backend = runtime
    backend.items = [replace(backend.items[0], can_force=False)]
    target = manager.inventory({})[0]
    with pytest.raises(ValueError, match="compartilha"):
        manager.force(target, lambda: False, lambda call: call())
    assert not backend.requests


def test_tray_process_remains_bound_to_original_identity(runtime):
    manager, backend = runtime
    target = next(iter(manager.targets.values()))
    backend.items = []
    remaining = manager.remaining(target)
    assert remaining.processes == target.processes and remaining.windows == ()
    assert manager.force(remaining, lambda: False, lambda call: call())


def test_new_instance_cannot_be_added_to_force_after_normal_close(runtime):
    manager, backend = runtime
    target = next(iter(manager.targets.values()))
    backend.items.append(Window("11", replace(backend.ref, created=124), "/editor", "Editor de Teste"))
    with pytest.raises(TargetChanged):
        manager.remaining(target)


def test_cancel_stops_next_window_dispatch(runtime):
    manager, backend = runtime
    backend.items.append(Window("11", backend.ref, "/editor", "Editor de Teste"))
    target = manager.inventory({})[0]
    cancelled = threading.Event()
    def dispatch(call):
        call()
        cancelled.set()
    with pytest.raises(Cancelled):
        manager.close_normal(target, cancelled.is_set, dispatch)
    assert backend.requests == [("close", "10")]


def agent_for(manager, answers):
    from test_commands import FakeSpeech, FakeSpeaker, FakeClient
    catalog = Catalog(lambda: Inventory())
    catalog.refresh()
    executor = NativeExecutor(catalog, launcher=lambda *_: pytest.fail("Não deveria abrir app"))
    identifier = next(iter(manager.targets))
    client = FakeClient([Decision(status="action", action=CloseApp(kind="close_app", target_id=identifier))])
    agent = AssistantService(executor=executor, running=manager, speaker=FakeSpeaker(),
                             speech_input=FakeSpeech(answers), command_client=client)
    agent._interaction_id = 1
    def say(text, interaction):
        agent._publish(State.SPEAKING, text, interaction_id=interaction)
        agent.speaker.speak(text, agent._cancelled)
    agent._say = say
    return agent


@pytest.mark.parametrize("answer", ["sim", "", "talvez", "não", "pode executar"])
def test_force_requires_its_own_exact_phrase(runtime, answer):
    manager, backend = runtime
    agent = agent_for(manager, ["sim", answer])
    agent.handle_command("feche o Editor de Teste", 1)
    assert backend.requests == [("close", "10")]
    assert "perder seu trabalho" in agent.speaker.messages[-1]
    assert "não autorizado" in agent.snapshot.text


def test_force_phrase_after_normal_confirmation_terminates_once(runtime):
    manager, backend = runtime
    agent = agent_for(manager, ["sim", "forçar fechamento"])
    agent.handle_command("feche o Editor de Teste", 1)
    assert backend.requests == [("close", "10"), ("force", backend.ref.pid)]
    assert "foi encerrado" in agent.snapshot.text


def test_force_phrase_cannot_skip_normal_confirmation(runtime):
    manager, backend = runtime
    agent = agent_for(manager, ["forçar fechamento", "forçar fechamento"])
    agent.handle_command("feche o Editor de Teste", 1)
    assert not backend.requests


def test_cancel_during_second_question_prevents_force(runtime):
    manager, backend = runtime
    agent = agent_for(manager, ["sim"])
    def on_speak():
        if len(agent.speaker.messages) == 2:
            agent.cancel()
    agent.speaker.on_speak = on_speak
    with pytest.raises(Cancelled):
        agent.handle_command("feche o Editor de Teste", 1)
    assert backend.requests == [("close", "10")]


def test_wait_does_not_hold_interface_lock(runtime):
    manager, backend = runtime
    agent = agent_for(manager, ["sim"])
    entered = threading.Event()
    original = manager.wait
    def wait(target, cancelled, seconds=None):
        entered.set()
        while not cancelled():
            entered.wait(0.01)
        raise Cancelled()
    manager.wait = wait
    def handle():
        try:
            agent.handle_command("feche o Editor de Teste", 1)
        except Cancelled:
            pass
    thread = threading.Thread(target=handle)
    thread.start()
    assert entered.wait(2)
    agent.cancel()
    thread.join(2)
    assert not thread.is_alive()


def test_unavailable_window_backend_reports_without_process_fallback():
    backend = SimpleNamespace(windows=lambda: (_ for _ in ()).throw(RuntimeError("GNOME extension missing")))
    manager = RunningApps(backend)
    assert manager.inventory({}) == []
    assert "GNOME extension missing" in manager.error


def test_unknown_wayland_desktop_does_not_use_x11(monkeypatch):
    from host_agent.window_backends import create_backend, UnavailableBackend, GnomeBackend
    import sys
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert isinstance(create_backend(), UnavailableBackend)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    assert isinstance(create_backend(), GnomeBackend)


def test_finder_cannot_be_terminated_by_normal_close(monkeypatch):
    import sys
    from host_agent.window_backends import MacBackend
    calls = []
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(NSRunningApplication=SimpleNamespace(
        runningApplicationWithProcessIdentifier_=lambda _: calls.append("lookup"))))
    window = SimpleNamespace(process=SimpleNamespace(executable="/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder"))
    with pytest.raises(ValueError, match="Finder"):
        MacBackend().close(window)
    assert not calls
