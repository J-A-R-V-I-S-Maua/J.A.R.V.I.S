import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication
from speakbar.preferences import PreferencesService
from speakbar.shortcuts import (ShortcutService, ShortcutConversation, disk_image_application, find_application,
                                installed_applications, validate_shortcut, validate_shortcuts, normalize)
from speakbar.controller import VoiceController, DemoController
from speakbar.settings_window import SettingsWindow
from wakeword.detect_microphone_service import VoiceService
from wakeword.events import State

APP = QApplication.instance() or QApplication([])


def shortcut(phrase="abrir YouTube", destination="youtube.com", kind="site"):
    return dict(id="one", name="YouTube", phrase=phrase, kind=kind, destination=destination, enabled=True)


def mac_shortcut(phrase, destination, kind, key):
    return dict(id=key, name=key, phrase=phrase, kind=kind, destination=destination, enabled=True)


class SettingsDomainTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings.json"
        self.store = PreferencesService(self.path)

    def test_atomic_persistence_overrides_and_restore_keep_shortcuts(self):
        self.store.save({"text_size": 24}, [shortcut()])
        with patch.dict(os.environ, {"SCREEN_MAX_ELEMENTS": "4200"}):
            loaded = PreferencesService(self.path)
            self.assertEqual(loaded.effective["text_size"], 24)
            self.assertEqual(loaded.effective["max_elements"], 4200)
        self.store.restore()
        self.assertEqual(self.store.overrides, {})
        self.assertEqual(self.store.shortcuts[0]["destination"], "https://youtube.com")
        self.assertEqual(list(self.path.parent.glob(".settings-*.tmp")), [])

    def test_invalid_file_preserved_when_loading_and_when_saving(self):
        self.path.write_text("broken JSON", encoding="utf-8")
        store = PreferencesService(self.path)
        self.assertIsNotNone(store.error)
        self.assertEqual(self.path.read_text(), "broken JSON")
        store.save({}, [])
        backup = next(self.path.parent.glob("settings.invalid-*.json"))
        self.assertEqual(backup.read_text(), "broken JSON")

    def test_write_failure_does_not_replace_memory_or_file(self):
        self.store.save({}, [])
        previous = self.path.read_text()
        with patch("speakbar.preferences.os.replace", side_effect=OSError("blocked")):
            with self.assertRaises(OSError):
                self.store.save({"text_size": 25}, [shortcut()])
        self.assertEqual(self.path.read_text(), previous)
        self.assertEqual(self.store.overrides, {})

    def test_duplicate_reserved_and_invalid_destinations(self):
        other = dict(shortcut("ABRIR, YOUTÚBE!"), id="two")
        with self.assertRaises(ValueError):
            validate_shortcuts([shortcut(), other])
        for phrase in ("confirmar", "cancelar", "abra as configurações"):
            with self.assertRaises(ValueError):
                validate_shortcuts([shortcut(phrase)])
        for destination in ("file:///secret", "ftp://example.com", "https://"):
            with self.assertRaises(ValueError):
                validate_shortcuts([shortcut(destination=destination)])
        self.assertNotEqual(normalize("ação"), normalize("asao"))

    def test_exact_only_disabled_missing_and_demo(self):
        launch = Mock()
        service = ShortcutService(validate_shortcuts([shortcut()]), launch)
        service.execute(service.resolve("Abrir, Youtúbe!"))
        launch.assert_called_once()
        with self.assertRaises(ValueError):
            service.resolve("abri youtube")
        service.items[0]["enabled"] = False
        with self.assertRaises(ValueError):
            service.resolve("abrir youtube")
        service.demo = True
        service.execute(shortcut())
        launch.assert_called_once()

    def test_four_destination_types_and_no_shell_command(self):
        app = Path(self.temp.name) / "app.exe"
        app.touch()
        items = [shortcut(), dict(shortcut("abrir app", str(app), "application"), id="two"),
                 dict(shortcut("abrir pasta", self.temp.name, "folder"), id="three"),
                 dict(shortcut("abrir Windows", "settings", "windows"), id="four")]
        validated = validate_shortcuts(items)
        with patch("speakbar.shortcuts.QDesktopServices.openUrl", return_value=True) as web, \
                patch("speakbar.shortcuts.os.startfile", create=True) as local, patch("speakbar.shortcuts.sys.platform", "win32"):
            service = ShortcutService(validated)
            for item in validated:
                service.execute(item)
            self.assertEqual(web.call_count, 1)
            self.assertEqual(local.call_count, 3)
            local.assert_called_with("ms-settings:")


class MacOSShortcutTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name) / "Notas.app"

    def test_app_bundle_is_a_directory_and_resources_are_typed(self):
        with self.assertRaises(ValueError):
            validate_shortcut(mac_shortcut("abrir notas", str(self.app), "application", "one"))
        self.app.mkdir()
        validate_shortcut(mac_shortcut("abrir notas", str(self.app), "application", "one"))
        validate_shortcut(mac_shortcut("abrir som", "sound", "macos", "two"))
        for item in (mac_shortcut("abrir som", "explorer", "macos", "three"),
                     mac_shortcut("abrir notas", str(Path(self.temp.name) / "Notas.zip"), "application", "four")):
            with self.assertRaises(ValueError):
                validate_shortcut(item, check_destination=False)

    def test_open_receives_arguments_without_shell(self):
        self.app.mkdir()
        items = validate_shortcuts([mac_shortcut("abrir notas", str(self.app), "application", "one"),
                                    mac_shortcut("abrir pasta", self.temp.name, "folder", "two"),
                                    mac_shortcut("abrir finder", "finder", "macos", "three"),
                                    mac_shortcut("abrir rede", "network", "macos", "four")])
        done = subprocess.CompletedProcess([], 0, "", "")
        with patch("speakbar.shortcuts.sys.platform", "darwin"), \
                patch("speakbar.shortcuts.subprocess.run", return_value=done) as run:
            service = ShortcutService(items)
            for item in items:
                service.execute(item)
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ["/usr/bin/open", str(self.app)], ["/usr/bin/open", self.temp.name],
            ["/usr/bin/open", "-b", "com.apple.finder"],
            ["/usr/bin/open", "x-apple.systempreferences:com.apple.Network-Settings.extension"]])
        self.assertTrue(all("shell" not in call.kwargs for call in run.call_args_list))

    def test_failures_and_other_system_resources_are_reported(self):
        failed = subprocess.CompletedProcess([], 1, "", "Unable to find application")
        with patch("speakbar.shortcuts.sys.platform", "darwin"), \
                patch("speakbar.shortcuts.subprocess.run", return_value=failed) as run:
            service = ShortcutService()
            with self.assertRaisesRegex(ValueError, "Unable to find application"):
                service.execute(mac_shortcut("abrir som", "sound", "macos", "one"))
            with self.assertRaisesRegex(ValueError, "somente no Windows"):
                service.execute(mac_shortcut("abrir windows", "settings", "windows", "two"))
        self.assertEqual(run.call_count, 1)
        with patch("speakbar.shortcuts.sys.platform", "win32"):
            with self.assertRaisesRegex(ValueError, "somente no macOS"):
                ShortcutService().execute(mac_shortcut("abrir som", "sound", "macos", "three"))

    def mount(self, *apps):
        volume = Path(tempfile.mkdtemp(dir=self.temp.name))
        for app in apps:
            (volume / app).mkdir()
        output = plistlib.dumps({"system-entities": [{"dev-entry": "/dev/disk4"}, {"mount-point": str(volume)}]})
        return volume, lambda args, **kwargs: subprocess.CompletedProcess(args, 0, output if "hdiutil" in args[0] else "", "")

    def test_disk_image_mounts_read_only_and_opens_the_app_inside(self):
        image = Path(self.temp.name) / "Notas.dmg"
        image.touch()
        item = validate_shortcut(mac_shortcut("abrir notas", str(image), "application", "Notas"))
        volume, run = self.mount("Notas.app")
        messages, finished = [], threading.Event()
        service = ShortcutService(notify=lambda text: (messages.append(text), finished.set()))
        with patch("speakbar.shortcuts.sys.platform", "darwin"), \
                patch("speakbar.shortcuts.subprocess.run", side_effect=run) as calls:
            self.assertEqual(service.execute(item), "Montando a imagem de Notas…")
            self.assertTrue(finished.wait(5))
        hdiutil, opened = [call.args[0] for call in calls.call_args_list]
        self.assertEqual(hdiutil[:2], ["/usr/bin/hdiutil", "attach"])
        self.assertIn("-readonly", hdiutil)
        self.assertEqual(hdiutil[-1], str(image))
        self.assertEqual(opened, ["/usr/bin/open", str(volume / "Notas.app")])
        self.assertEqual(messages, ["Abertura de Notas solicitada ao sistema."])

    def test_disk_image_without_a_single_app_is_reported(self):
        for apps, message in (((), "não contém um aplicativo"), (("A.app", "B.app"), "mais de um aplicativo")):
            with self.subTest(apps=apps):
                _, run = self.mount(*apps)
                with patch("speakbar.shortcuts.subprocess.run", side_effect=run):
                    with self.assertRaisesRegex(OSError, message):
                        disk_image_application("/tmp/Notas.dmg")
        image = Path(self.temp.name) / "Notas.dmg"
        image.touch()
        failed = subprocess.CompletedProcess([], 1, b"", b"hdiutil: attach failed - image not recognized")
        messages, finished = [], threading.Event()
        service = ShortcutService(notify=lambda text: (messages.append(text), finished.set()))
        with patch("speakbar.shortcuts.sys.platform", "darwin"), \
                patch("speakbar.shortcuts.subprocess.run", return_value=failed):
            service.execute(mac_shortcut("abrir notas", str(image), "application", "Notas"))
            self.assertTrue(finished.wait(5))
        self.assertEqual(messages, ["Falha ao abrir Notas. hdiutil: attach failed - image not recognized"])



class ApplicationCatalogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for folder, apps in (("Applications", ("Safari.app", "Calendar.app", "Captura de Tela.app", "ChatGPT.app")),
                             ("System", ("Safari.app", "Captura de Imagem.app")), ("Library", ("Finder.app",))):
            for app in apps:
                (root / folder / app).mkdir(parents=True)
        (root / "Applications" / "Leia-me.txt").touch()
        names = {"Calendar": "Calendário"}
        self.apps = installed_applications(
            (root / "Applications", root / "System", root / "Library" / "Finder.app", root / "Ausente"),
            name=lambda path: names.get(path.stem, path.stem))

    def found(self, phrase):
        status, apps = find_application(phrase, self.apps)
        return status, [app.name for app in apps]

    def test_lists_each_app_once_with_its_display_name(self):
        self.assertEqual([app.name for app in self.apps],
                         ["Calendário", "Captura de Imagem", "Captura de Tela", "ChatGPT", "Finder", "Safari"])
        self.assertEqual(next(app for app in self.apps if app.name == "Safari").path.parent.name, "Applications")
        self.assertEqual(installed_applications(()), [])

    def test_spoken_name_finds_the_app(self):
        self.assertEqual(self.found("abrir o Safari"), ("match", ["Safari"]))
        self.assertEqual(self.found("abrir calendário"), ("match", ["Calendário"]))
        self.assertEqual(self.found("abrir calendar"), ("match", ["Calendário"]))
        self.assertEqual(self.found("abril o finder"), ("match", ["Finder"]))
        self.assertEqual(self.found("abrir chat gpt"), ("match", ["ChatGPT"]))

    def test_similar_names_and_numbers_are_not_guessed(self):
        self.assertEqual(self.found("abrir a captura"), ("ambiguous", ["Captura de Imagem", "Captura de Tela"]))
        self.assertEqual(self.found("abrir 2"), ("none", []))
        self.assertEqual(self.found("abrir planilhas"), ("none", []))


class SettingsConversationTest(unittest.TestCase):
    def setUp(self):
        self.launch = Mock()
        self.service = ShortcutService(validate_shortcuts([shortcut()]), self.launch)
        self.conversation = ShortcutConversation(self.service)
        self.messages = []
        self.conversation.changed.connect(self.messages.append)
        self.addCleanup(self.conversation.cancel)

    def test_two_turn_and_single_turn_execute_once(self):
        capture = Mock()
        self.conversation.capture_requested.connect(capture)
        self.conversation.handle("executar atalho")
        capture.assert_called_once()
        self.conversation.handle("abrir youtube")
        APP.processEvents()
        self.launch.assert_called_once()
        self.conversation.handle("executar atalho abrir youtube")
        APP.processEvents()
        self.assertEqual(self.launch.call_count, 2)

    def test_cancel_pending_launch_and_expiration(self):
        self.conversation.handle("executar atalho abrir youtube")
        self.conversation.cancel()
        APP.processEvents()
        self.launch.assert_not_called()
        self.conversation.handle("executar atalho")
        self.conversation.timer.timeout.emit()
        self.assertFalse(self.conversation.active)

    def test_voice_settings_aliases_and_literal_dictation(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = VoiceController(preferences=PreferencesService(Path(directory) / "settings.json"))
            controller.thread = Mock()
            service = VoiceService()
            controller.thread.service = service
            opening = Mock()
            controller.settings_requested.connect(opening)
            for phrase in ("Abra as configurações", "abrir configurações", "ABRA AS CONFIGURAÇÕES DO JARVIS!"):
                service._interaction_id += 1
                service._publish(State.RESULT, phrase)
                controller._receive(service.snapshot)
                controller._receive(service.snapshot)
            self.assertEqual(opening.call_count, 3)
            from screen_access.session import ActionState
            controller.screen.session.state = ActionState.DICTATING
            controller.screen.session.snapshot = Mock()
            controller.screen.session.snapshot.elements = [Mock(name="Campo")]
            service._interaction_id += 1
            service._publish(State.RESULT, "abra as configurações")
            controller._receive(service.snapshot)
            self.assertEqual(opening.call_count, 3)
            self.assertEqual(controller.screen.session.dictation_text, "abra as configurações")
            controller.screen.session.cancel()
            controller.screen.shutdown()

    def test_diagnostic_transcription_never_routes_action_and_pause_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = VoiceController(preferences=PreferencesService(Path(directory) / "settings.json"))
            controller.thread = Mock()
            service = VoiceService()
            controller.thread.service = service
            service._publish(State.IDLE)
            controller.set_settings_open(True)
            self.assertTrue(service.paused.is_set())
            controller.test_audio(transcription=True)
            service._interaction_id += 1
            service._publish(State.RESULT, "deletar")
            controller._receive(service.snapshot)
            self.assertFalse(controller.screen.session.active)
            controller.set_settings_open(False)
            self.assertFalse(service.paused.is_set())
            controller.screen.shutdown()

    def test_expired_shortcut_invalidates_pending_voice_result(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = VoiceController(preferences=PreferencesService(Path(directory) / "settings.json"))
            controller.thread = Mock()
            service = VoiceService()
            controller.thread.service = service
            controller.shortcuts.handle("executar atalho")
            service._publish(State.PROCESSING)
            previous_id = service._interaction_id
            controller.shortcuts.timer.timeout.emit()
            self.assertGreater(service._interaction_id, previous_id)
            self.assertFalse(controller.shortcuts.active)
            service._publish(State.RESULT, "executar atalho abrir youtube", interaction_id=previous_id)
            self.assertNotEqual(service.snapshot.state, State.RESULT)
            controller.screen.shutdown()


class SettingsUITest(unittest.TestCase):
    def test_new_shortcut_clears_previous_selection_and_refresh_keeps_editing(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = DemoController()
            controller.preferences = PreferencesService(Path(directory) / "settings.json")
            controller.preferences.save({}, [shortcut()])
            dialog = SettingsWindow(controller, demo=True)
            self.assertFalse(dialog.delete_button.isEnabled())
            dialog.items.setCurrentRow(0)
            key = dialog.editing
            self.assertTrue(dialog.delete_button.isEnabled())
            dialog._toggle_shortcut()
            self.assertEqual(dialog.editing, key)
            self.assertEqual(dialog.items.currentRow(), 0)
            self.assertFalse(dialog.draft[0]["enabled"])
            dialog._new_shortcut()
            self.assertIsNone(dialog._selected())
            self.assertFalse(dialog.delete_button.isEnabled())
            self.assertFalse(dialog.toggle_button.isEnabled())
            dialog.search.setText("não existe")
            self.assertEqual(dialog.items.count(), 0)
            self.assertFalse(dialog.empty_list.isHidden())
            dialog.reject()
            self.assertTrue(controller.preferences.shortcuts[0]["enabled"])
            dialog.deleteLater()

    def test_form_save_restore_and_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = DemoController()
            controller.preferences = PreferencesService(Path(directory) / "settings.json")
            dialog = SettingsWindow(controller, demo=True)
            self.assertEqual(dialog.pages.count(), 4)
            dialog._template("YouTube")
            self.assertEqual(controller.preferences.shortcuts, [])
            dialog._save()
            self.assertEqual(controller.preferences.shortcuts[0]["phrase"], "abrir YouTube")
            second = SettingsWindow(controller, demo=True)
            second._delete_shortcut()
            second.reject()
            self.assertEqual(len(controller.preferences.shortcuts), 1)
            third = SettingsWindow(controller, demo=True)
            third._restore()
            third._save()
            self.assertEqual(controller.preferences.overrides, {})
            self.assertEqual(len(controller.preferences.shortcuts), 1)
            for widget in (dialog, second, third):
                widget.deleteLater()
            APP.processEvents()


class AudioPreferencesTest(unittest.TestCase):
    def test_settings_never_initializes_portaudio_on_ui_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = VoiceController(preferences=PreferencesService(Path(directory) / "settings.json"))
            controller.thread = Mock()
            controller.thread.service = VoiceService()
            with patch("wakeword.microphone.input_devices", side_effect=AssertionError("Audio called on UI")):
                dialog = SettingsWindow(controller, demo=False)
            self.assertTrue(controller.thread.service.devices_requested.is_set())
            controller.devices_changed.emit(dict(devices=[dict(index=0, name="Teste", api="API")], error=None))
            self.assertEqual(dialog.microphone.count(), 2)
            self.assertEqual(controller.audio_devices[0]["name"], "Teste")
            dialog.deleteLater()
            APP.processEvents()
            controller.screen.shutdown()

    def test_device_refresh_reuses_active_audio_interface_on_owner_thread(self):
        import threading
        import time
        from wakeword.events import Cancelled
        audio = Mock()
        owners, updates, levels = [], [], []
        audio.get_device_count.side_effect = lambda: (owners.append(threading.get_ident()) or 1)
        audio.get_device_info_by_index.return_value = dict(index=0, name="Teste", maxInputChannels=1, hostApi=0)
        audio.get_default_input_device_info.return_value = dict(index=0, name="Teste", maxInputChannels=1)
        audio.get_host_api_info_by_index.return_value = dict(name="API")
        factory = Mock(return_value=audio)
        service = VoiceService(audio_factory=factory, on_devices=updates.append)
        service.set_paused(True)
        service.audio_test_until = time.monotonic() + 5

        def level(value):
            levels.append(value)
            if len(levels) == 1:
                service.request_device_list()
            else:
                service.stop()

        service.on_level = level

        def worker():
            try:
                service._capture(Mock())
            except Cancelled:
                pass

        with patch("wakeword.detect_microphone_service._read_chunk", return_value=b"\x10\x10" * 1280):
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        factory.assert_called_once()
        self.assertEqual(len(updates), 2)
        self.assertTrue(all(owner == thread.ident for owner in owners))
        self.assertNotEqual(thread.ident, threading.get_ident())
        audio.terminate.assert_called_once()

    def test_paused_meter_uses_one_stream_without_predicting_wakeword(self):
        import time
        from wakeword.events import Cancelled
        audio = Mock()
        audio.get_default_input_device_info.return_value = {"index": 0, "name": "Teste", "maxInputChannels": 1}
        audio.get_device_info_by_index.return_value = {"index": 0, "name": "Teste", "maxInputChannels": 1}
        service = VoiceService(audio_factory=lambda: audio)
        model = Mock()
        levels = []
        service.set_paused(True)
        service.audio_test_until = time.monotonic() + 5

        def level(value):
            levels.append(value)
            service.stop()

        service.on_level = level
        with patch("wakeword.detect_microphone_service._read_chunk", return_value=b"\x10\x10" * 1280):
            with self.assertRaises(Cancelled):
                service._capture(model)
        self.assertEqual(audio.open.call_count, 1)
        audio.open.return_value.close.assert_called_once()
        audio.terminate.assert_called_once()
        model.predict.assert_not_called()
        self.assertEqual(len(levels), 1)

    def test_paused_service_only_accepts_diagnostic_capture(self):
        service = VoiceService()
        service._publish(State.IDLE)
        service.set_paused(True)
        self.assertFalse(service.request_capture())
        service.toggle()
        self.assertFalse(service.trigger_requested.is_set())
        self.assertTrue(service.request_capture(diagnostic=True))
        self.assertTrue(service.diagnostic_capture.is_set())
        service.set_paused(False)
        self.assertFalse(service.diagnostic_capture.is_set())
        self.assertFalse(service.trigger_requested.is_set())

    def test_device_identity_missing_and_ambiguous(self):
        from wakeword.microphone import select_input_device
        audio = Mock()
        audio.get_device_count.return_value = 2
        audio.get_device_info_by_index.return_value = {"name": "Microfone", "hostApi": 0, "maxInputChannels": 1}
        audio.get_host_api_info_by_index.return_value = {"name": "API"}
        selection = dict(name="Microfone", api="API")
        with self.assertRaises(ValueError):
            select_input_device(audio, selection)
        audio.get_device_count.return_value = 1
        self.assertEqual(select_input_device(audio, selection), 0)
        with self.assertRaises(ValueError):
            select_input_device(audio, dict(name="Outro", api="API"))

    def test_single_settings_window_and_resume(self):
        from speakbar.window import Speakbar
        with tempfile.TemporaryDirectory() as directory:
            controller = DemoController()
            controller.preferences = PreferencesService(Path(directory) / "settings.json")
            bar = Speakbar(demo=True, controller=controller)
            bar.open_settings()
            first = bar._settings_dialog
            bar.open_settings()
            self.assertIs(bar._settings_dialog, first)
            self.assertTrue(controller.settings_open)
            first.reject()
            APP.processEvents()
            self.assertFalse(controller.settings_open)
            bar.tray.hide()
            bar.hide()
            bar.deleteLater()
            APP.processEvents()

    def test_system_settings_template_does_not_use_reserved_phrase(self):
        for system, template, phrase in (("windows", "Configurações", "abrir configurações do Windows"),
                                         ("macos", "Ajustes do Sistema", "abrir ajustes do sistema")):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as directory, \
                    patch("speakbar.settings_window.SYSTEM_KIND", system):
                controller = DemoController()
                controller.preferences = PreferencesService(Path(directory) / "settings.json")
                dialog = SettingsWindow(controller, demo=True)
                dialog._template(template)
                dialog._save()
                self.assertEqual(controller.preferences.shortcuts[0]["phrase"], phrase)
                self.assertEqual(controller.preferences.shortcuts[0]["kind"], system)
                dialog.deleteLater()

    def test_audio_restart_waits_for_finished_and_overrides_environment_device(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = VoiceController(preferences=PreferencesService(Path(directory) / "settings.json"))
            controller.thread = Mock()
            controller.thread.service = VoiceService(input_device="environment")
            previous = controller.preferences.effective
            controller.preferences.save({"microphone": None}, [])
            with patch.object(controller.thread.service, "stop") as stop, patch.object(controller, "start") as start:
                controller.apply_preferences(previous)
                stop.assert_called_once()
                APP.processEvents()
                start.assert_not_called()
                controller._finished()
                APP.processEvents()
                start.assert_called_once()
            controller.screen.shutdown()
