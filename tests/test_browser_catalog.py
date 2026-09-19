"""Descoberta por SO simulada; estes testes nunca abrem navegadores."""
import asyncio
import json
from pathlib import Path
import plistlib
import subprocess
import sys
from types import SimpleNamespace
from typing import get_args

import httpx
import pytest

from contracts.commands import (App, Browser, CommandContext, Decision, InterpretRequest,
                               OpenApp, OpenUrl, SearchWeb, validate_proposal)
from host_agent import catalog
from host_agent.executor import NativeExecutor, confirmation


@pytest.mark.parametrize("browser", ["brave", "firefox", "chromium", "opera", "vivaldi", "safari"])
def test_added_browser_flows_and_missing_browser(browser):
    request = InterpretRequest(interaction_id="browser:1", text=f"abra example.com no {browser}",
                               context=CommandContext(available_apps=[browser]))
    action = OpenUrl(kind="open_url", url="https://example.com", browser=browser)
    validate_proposal(Decision(status="action", action=action), request)
    assert catalog.APP_NAMES[browser] in confirmation(action)
    launches = []
    executor = NativeExecutor(apps={browser: ("/installed/browser",)}, launch=launches.append, platform="linux")
    assert executor.execute(action) == "Solicitação enviada ao Linux."
    executor.execute(OpenApp(kind="open_app", app=browser))
    assert launches == [["/installed/browser", "https://example.com"], ["/installed/browser"]]
    request = request.model_copy(update={"context": CommandContext(available_apps=["browser"])})
    with pytest.raises(ValueError, match="Navegador indisponível"):
        validate_proposal(Decision(status="action", action=action), request)
    with pytest.raises(ValueError, match="Aplicativo indisponível"):
        NativeExecutor(apps={"browser": ("other",)}, launch=launches.append).execute(action)


def test_catalog_and_contracts_agree():
    assert set(catalog.APP_NAMES) == set(get_args(App))
    assert set(catalog.MACOS) == set(get_args(Browser)) - {"default"}


@pytest.mark.parametrize("platform,default,expected", [
    ("win32", "brave", "brave"), ("linux", "chrome", "chrome"),
    ("darwin", "firefox", "firefox"), ("win32", "missing", "edge"),
    ("linux", None, "firefox"), ("darwin", None, "safari"),
])
def test_default_then_platform_fallback(monkeypatch, platform, default, expected):
    apps = {name: (name,) for name in catalog.MACOS}
    discover = {"win32": "_windows", "linux": "_linux", "darwin": "_macos"}[platform]
    monkeypatch.setattr(catalog, discover, lambda: (apps.copy(), default))
    assert catalog.discover_apps(platform)["browser"] == (expected,)


def test_no_browser_and_unsupported_platform(monkeypatch):
    monkeypatch.setattr(catalog, "_linux", lambda: ({}, None))
    assert catalog.discover_apps("linux") == {}
    assert catalog.discover_apps("unsupported") == {}


def test_windows_registry_and_standard_fallback(monkeypatch, tmp_path):
    registered = tmp_path / "Firefox/firefox.exe"
    registered.parent.mkdir()
    registered.touch()
    brave = tmp_path / "BraveSoftware/Brave-Browser/Application/brave.exe"
    brave.parent.mkdir(parents=True)
    brave.touch()

    class Key:
        def __init__(self, value):
            self.value = value
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def open_key(root, key, *args):
        if key.endswith("UserChoice"):
            return Key("FirefoxURL-308046B0AF4A39CB")
        if key.endswith("App Paths\\firefox.exe"):
            return Key(f'"{registered}"')
        if key.endswith("App Paths\\chrome.exe"):
            return Key(str(tmp_path / "missing.exe"))
        raise FileNotFoundError()

    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace(
        HKEY_CURRENT_USER=1, HKEY_LOCAL_MACHINE=2, KEY_WOW64_64KEY=4,
        KEY_WOW64_32KEY=8, KEY_READ=16, OpenKey=open_key,
        QueryValue=lambda key, _: key.value, QueryValueEx=lambda key, _: (key.value, 1)))
    for env in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)", "SystemRoot"):
        monkeypatch.setenv(env, str(tmp_path))
    apps = catalog.discover_apps("win32")
    assert apps["firefox"] == (str(registered),)
    assert apps["brave"] == (str(brave),)
    assert apps["browser"] == apps["firefox"]
    assert "chrome" not in apps and "explorer" not in apps


def test_linux_native_and_flatpak_default(monkeypatch):
    tools = {"firefox": "/usr/bin/firefox", "brave-browser": "/usr/bin/brave-browser",
             "flatpak": "/usr/bin/flatpak", "xdg-settings": "/usr/bin/xdg-settings"}
    monkeypatch.setattr(catalog, "_which", tools.get)
    monkeypatch.setattr(catalog, "_file", lambda _: False)
    def query(argv):
        if "list" in argv:
            return "com.brave.Browser\nunknown.Browser"
        return "com.brave.Browser.desktop"
    monkeypatch.setattr(catalog, "_query", query)
    apps = catalog.discover_apps("linux")
    assert apps["firefox"] == ("/usr/bin/firefox",)
    assert apps["brave"] == ("/usr/bin/flatpak", "run", "com.brave.Browser")
    assert apps["browser"] == apps["brave"]
    assert set(apps) == {"firefox", "brave", "browser"}
    launched = []
    NativeExecutor(apps=apps, launch=launched.append, platform="linux").execute(
        SearchWeb(kind="search_web", query="a & b", browser="brave"))
    assert launched == [["/usr/bin/flatpak", "run", "com.brave.Browser", "https://www.google.com/search?q=a+%26+b"]]


def test_snap_wrapper_preserved_and_mime_fallback(monkeypatch):
    snap = str(Path("/snap/bin/firefox"))
    monkeypatch.setattr(catalog, "_which", {"xdg-mime": "/usr/bin/xdg-mime"}.get)
    monkeypatch.setattr(catalog, "_file", lambda path: str(path) == snap)
    monkeypatch.setattr(catalog.os, "access", lambda *args: True)
    queries = []
    def query(argv):
        queries.append(argv)
        return "firefox_firefox.desktop"
    monkeypatch.setattr(catalog, "_query", query)
    assert catalog.discover_apps("linux")["browser"] == (snap,)
    assert queries == [["/usr/bin/xdg-mime", "query", "default", "x-scheme-handler/https"]]


def test_linux_unknown_desktop_is_not_executed(monkeypatch):
    monkeypatch.setattr(catalog, "_which", {"xdg-settings": "/usr/bin/xdg-settings",
                                            "firefox": "/usr/bin/firefox"}.get)
    monkeypatch.setattr(catalog, "_file", lambda _: False)
    monkeypatch.setattr(catalog, "_query", lambda _: "sh -c malicious.desktop")
    assert catalog.discover_apps("linux")["browser"] == ("/usr/bin/firefox",)


def test_macos_launch_services_and_bundle_validation(monkeypatch, tmp_path):
    validate_bundle = catalog._bundle
    monkeypatch.setattr(catalog, "_bundle", lambda path, identifier:
                        Path(path).is_relative_to(tmp_path) and validate_bundle(path, identifier))
    firefox = tmp_path / "Custom Location/Firefox.app"
    (firefox / "Contents").mkdir(parents=True)
    with (firefox / "Contents/Info.plist").open("wb") as file:
        plistlib.dump({"CFBundleIdentifier": "org.mozilla.firefox"}, file)
    # A mesma pasta não pode ser anunciada falsamente como Chrome.
    monkeypatch.setattr(catalog, "_query", lambda _: json.dumps({
        "apps": {"firefox": str(firefox), "chrome": str(firefox)}, "default": "org.mozilla.firefox"}))
    apps = catalog.discover_apps("darwin")
    assert apps["browser"] == ("/usr/bin/open", "-a", str(firefox))
    assert "chrome" not in apps
    launches = []
    NativeExecutor(apps=apps, launch=launches.append, platform="darwin").execute(
        OpenUrl(kind="open_url", url="https://example.com"))
    assert launches == [["/usr/bin/open", "-a", str(firefox), "https://example.com"]]


def test_macos_query_failure_still_finds_user_bundle(monkeypatch, tmp_path):
    safari = tmp_path / "Applications/Safari.app/Contents"
    safari.mkdir(parents=True)
    with (safari / "Info.plist").open("wb") as file:
        plistlib.dump({"CFBundleIdentifier": "com.apple.Safari"}, file)
    monkeypatch.setattr(catalog.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(catalog, "_query", lambda _: "invalid")
    assert catalog.discover_apps("darwin")["browser"] == ("/usr/bin/open", "-a", str(safari.parent))


@pytest.mark.parametrize("contents", [b"invalid", b"<?xml version='1.0'?><plist><", plistlib.dumps([])])
def test_malformed_bundle_is_ignored(tmp_path, contents):
    (tmp_path / "Contents").mkdir()
    (tmp_path / "Contents/Info.plist").write_bytes(contents)
    assert catalog._bundle(tmp_path, "com.apple.Safari") is False


def test_system_query_timeout_is_bounded_and_has_no_shell(monkeypatch):
    def run(argv, **kwargs):
        assert kwargs["timeout"] == 3 and kwargs["shell"] is False
        raise subprocess.TimeoutExpired(argv, 3)
    monkeypatch.setattr(catalog.subprocess, "run", run)
    assert catalog._query(["xdg-settings", "get", "default-web-browser"]) == ""


@pytest.mark.parametrize("browser", ["brave", "firefox", "safari"])
@pytest.mark.parametrize("explicit", [False, True])
def test_api_schema_contains_only_discovered_browsers(monkeypatch, browser, explicit):
    import api.commands as api
    actual = httpx.AsyncClient
    def handler(req):
        schema = json.loads(req.content)["format"]["properties"]
        assert schema["browser"]["enum"] == ([browser] if explicit else ["default", browser])
        assert schema["app"]["enum"] == (["", browser] if explicit else ["", "browser", browser])
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps({
            "kind": "search_web", "query": "receitas", "provider": "google", "browser": browser})}})
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda **kwargs: actual(transport=httpx.MockTransport(handler), **kwargs))
    request = InterpretRequest(interaction_id="test", text=f"pesquise receitas usando {browser}" if explicit else "pesquise receitas",
                               context=CommandContext(available_apps=["browser", browser]))
    assert asyncio.run(api.infer(request)).action.browser == browser


@pytest.mark.parametrize("browser", ["brave", "firefox", "safari"])
def test_explicit_preference_rechecked_on_host(browser):
    from contracts.commands import requested_browsers, request_problem
    request = InterpretRequest(interaction_id="test", text=f"pesquise receitas usando o {browser}",
                               context=CommandContext(available_apps=["browser", browser]))
    with pytest.raises(ValueError, match="não respeitou"):
        validate_proposal(Decision(status="action", action=SearchWeb(kind="search_web", query="receitas")), request)
    absent = request.model_copy(update={"context": CommandContext(available_apps=["browser"])})
    assert request_problem(absent).status == "unsupported"
    topic = request.model_copy(update={"text": f"pesquise sobre {browser}"})
    assert requested_browsers(topic) == []
    topic = request.model_copy(update={"text": f"pesquise como abrir o {browser}"})
    assert requested_browsers(topic) == []


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_other_platforms_keep_transcription_until_tts_ported(monkeypatch, platform):
    from wakeword.detect_microphone_service import VoiceService, create_service
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("COMMANDS_ENABLED", "1")
    monkeypatch.setenv("TRANSCRIPTION_MODE", "batch")
    assert type(create_service()) is VoiceService
