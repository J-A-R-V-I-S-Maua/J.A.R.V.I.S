"""Prévia explícita da barra sem áudio: python tests/interaction_visual_check.py."""
from pathlib import Path
import os
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
    from speakbar.window import Speakbar
    from speakbar.controller import DemoController
    from speakbar.interaction import Presentation
    from speakbar.preferences import PreferencesService
    from wakeword.events import State, VoiceEvent
    app = QApplication([])
    output = Path(tempfile.gettempdir()) / "jarvis-interaction-preview"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        controller = DemoController()
        controller.preferences = PreferencesService(Path(directory) / "settings.json")
        bar = Speakbar(demo=True, controller=controller)
        for size in (18, 28):
            bar.apply_appearance(dict(controller.preferences.effective, text_size=size))
            for name, presentation in (
                ("listening", Presentation(State.LISTENING, "Ouvindo…", "Acionar [7] Pesquisar? Diga confirmar ou cancelar.")),
                ("processing", Presentation(State.PROCESSING, "Transcrevendo… Ainda processando sua fala…", "Diga confirmar ou cancelar.")),
                ("unknown", Presentation(State.RESULT, "Não reconheci esse comando. Tente ‘mostrar números’, ‘digitar’ ou ‘ajuda’. Ouvindo novamente…")),
                ("ready", Presentation(State.IDLE, "Texto inserido no campo.", "Diga Hey Jarvis ou use o botão para começar.", True)),
            ):
                controller.presentation = presentation
                controller.current_event = VoiceEvent(presentation.state, presentation.message)
                controller.state_changed.emit(controller.current_event)
                bar.ensurePolished()
                bar.layout().activate()
                app.processEvents()
                assert bar.screen().availableGeometry().contains(bar.geometry())
                assert bar.message.toPlainText() == presentation.message
                assert bar.grab().save(str(output / f"bar-{sys.argv[1]}-{size}-{name}.png"))
        bar.quit()
        app.processEvents()
        print(f"scale={sys.argv[1]} output={output}")


if __name__ == "__main__":
    main()
