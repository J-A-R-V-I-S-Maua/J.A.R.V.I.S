"""Reconhecedor local de interrupções; nunca autoriza ações."""
import json
import os
from pathlib import Path
import tempfile
import zipfile

from contracts.commands import normalize
from wakeword.events import check_cancelled

MODEL_NAME = "vosk-model-small-pt-0.3"
STOP_PHRASES = {"parar", "para", "cancelar", "pare", "parar agora", "cancelar comando"}


def _valid_model(path):
    # O modelo PT 0.3 usa o layout antigo (final.mdl na raiz).
    return (path / "final.mdl").is_file() or (path / "am" / "final.mdl").is_file()


def ensure_model(cancelled=lambda: False):
    configured = os.getenv("INTERRUPT_MODEL_DIR")
    if configured:
        path = Path(configured).expanduser()
        if not _valid_model(path):
            raise RuntimeError("INTERRUPT_MODEL_DIR não contém um modelo Vosk válido.")
        return path
    root = Path.home() / ".cache" / "jarvis"
    destination = root / MODEL_NAME
    if _valid_model(destination):
        return destination
    root.mkdir(parents=True, exist_ok=True)
    from wakeword.model_loader import _download_model
    # O diretório temporário pertence exclusivamente a este download.
    with tempfile.TemporaryDirectory(prefix="vosk-download-", dir=root) as work:
        archive = Path(work) / "model.zip"
        _download_model(f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip", archive, cancelled)
        with zipfile.ZipFile(archive) as zipped:
            for member in zipped.infolist():
                check_cancelled(cancelled)
                target = (Path(work) / member.filename).resolve()
                if not target.is_relative_to(Path(work).resolve()):
                    raise ValueError("Caminho inválido no modelo")
                zipped.extract(member, work)
        check_cancelled(cancelled)
        extracted = Path(work) / MODEL_NAME
        if not _valid_model(extracted):
            raise ValueError("Download não contém um modelo Vosk válido")
        try:
            extracted.rename(destination)
        except FileExistsError:
            if not _valid_model(destination):
                raise RuntimeError(f"Cache incompleto em {destination}")
    return destination


class InterruptDetector:
    def __init__(self, cancelled=lambda: False):
        from vosk import Model, KaldiRecognizer, SetLogLevel
        SetLogLevel(-1)
        self.model = Model(str(ensure_model(cancelled)))
        # Vocabulário completo evita transformar uma consulta em uma das palavras
        # de uma gramática restrita. Só uma frase FINAL isolada interrompe.
        self.recognizer = KaldiRecognizer(self.model, 16000)

    def feed(self, pcm):
        if self.recognizer.AcceptWaveform(pcm):
            return normalize(json.loads(self.recognizer.Result()).get("text", "")) in STOP_PHRASES
        return False

    def reset(self):
        self.recognizer.Reset()


if __name__ == "__main__":
    InterruptDetector()
    print("Detector de interrupção preparado. Nenhum microfone foi aberto.")
