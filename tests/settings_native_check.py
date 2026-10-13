"""Regressão Windows: menu por voz com captura real, sem Whisper ou gravações."""
import sys
from pathlib import Path
import tempfile
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speakbar.controller import VoiceController
from speakbar.preferences import PreferencesService
from speakbar.window import Speakbar
from wakeword.detect_microphone_service import VoiceService
from wakeword.events import State


class IdleModel:
    def reset(self):
        pass

    def predict(self, samples, timing=False):
        return {"hey_jarvis": 0}


class NoNetworkClient:
    def close(self):
        pass


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    outcome = {"success": False, "started": False, "cycles": 0}
    with tempfile.TemporaryDirectory() as directory:
        controller = VoiceController(service_factory=lambda callback: VoiceService(
            callback, model_factory=lambda cancelled: IdleModel(), client_factory=NoNetworkClient),
            preferences=PreferencesService(Path(directory) / "settings.json"))
        bar = Speakbar(controller=controller)

        def fail(message):
            print(message, flush=True)
            bar.quit()

        def command():
            service = controller.thread.service
            service._interaction_id += 1
            service._publish(State.LISTENING)
            service._publish(State.RESULT, "Abra as configurações")
            QTimer.singleShot(250, inspect)

        def inspect():
            dialog = bar._settings_dialog
            if dialog is None or not dialog.isVisible() or not controller.settings_open:
                fail("Falha: o comando não abriu as configurações.")
                return
            if dialog.microphone.count() < 2:
                fail("Falha: dispositivos não chegaram à janela.")
                return
            controller.settings_requested.emit()
            if bar._settings_dialog is not dialog:
                fail("Falha: janela duplicada.")
                return
            print(f"Captura ativa: {controller.thread.isRunning()}, dispositivos: {len(controller.audio_devices)}, abertura: OK", flush=True)
            dialog.reject()
            outcome["cycles"] += 1
            if outcome["cycles"] < 3:
                QTimer.singleShot(200, command)
            else:
                outcome["success"] = not controller.settings_open
                bar.quit()

        def ready(event):
            if event.state is State.IDLE and not outcome["started"]:
                outcome["started"] = True
                QTimer.singleShot(200, command)
            elif event.state is State.ERROR and not outcome["started"]:
                fail(event.text)

        controller.state_changed.connect(ready)
        bar.show()
        QTimer.singleShot(0, controller.start)
        QTimer.singleShot(12000, lambda: fail("Tempo limite na validação.") if not controller.closing else None)
        app.exec()
        if controller.thread and controller.thread.isRunning():
            controller.thread.service.stop()
            controller.thread.wait(5000)
        if controller.screen.executor.thread:
            controller.screen.executor.thread.join(2)
        # Liberar widgets antes do QApplication, evitando callbacks durante teardown.
        from PySide6.QtCore import QCoreApplication, QEvent
        bar.deleteLater()
        controller.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        return 0 if outcome["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
