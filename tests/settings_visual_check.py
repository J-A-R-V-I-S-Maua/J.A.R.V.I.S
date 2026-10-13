"""Renderização explícita sem áudio/aberturas: python tests/settings_visual_check.py."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    if len(sys.argv) == 1:
        for factor in ("1", "1.25", "1.5"):
            subprocess.run([sys.executable, __file__, factor], env=dict(os.environ, QT_SCALE_FACTOR=factor), check=True)
        return
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QCoreApplication, QEvent
    from speakbar.controller import DemoController
    from speakbar.preferences import PreferencesService
    from speakbar.settings_window import SettingsWindow
    app = QApplication([])
    output = Path(tempfile.gettempdir()) / "jarvis-settings-preview"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        controller = DemoController()
        controller.preferences = PreferencesService(Path(directory) / "settings.json")
        window = SettingsWindow(controller, demo=True)
        window.ensurePolished()
        for index in range(4):
            window.navigation.setCurrentRow(index)
            window.layout().activate()
            app.processEvents()
            path = output / f"settings-{sys.argv[1]}-{index}.png"
            assert window.grab().save(str(path))
        window.resize(650, 430)
        window.navigation.setCurrentRow(0)
        app.processEvents()
        path = output / f"settings-{sys.argv[1]}-small.png"
        assert window.grab().save(str(path))
        print(json.dumps({"scale": sys.argv[1], "pages": 4, "output": str(output)}))
        window.close()
        window.deleteLater()
        controller.timer.stop()
        controller.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


if __name__ == "__main__":
    main()
