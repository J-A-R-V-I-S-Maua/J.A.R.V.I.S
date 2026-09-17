"""Carregamento ONNX comum a Windows, Linux e macOS, sem acessar o microfone."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import openwakeword
from openwakeword.model import Model
import requests

from .events import check_cancelled


def model_directory():
    configured = os.getenv("WAKEWORD_MODELS_DIR")
    return Path(configured).expanduser() if configured else (
        Path.home() / ".cache" / "jarvis" / "openwakeword-0.6.0"
    )


def _download_model(url, destination, cancelled=lambda: False):
    """Publica somente downloads completos; uma falha pode ser tentada novamente."""
    temporary = None
    try:
        print(f"Baixando modelo {destination.name}...")
        check_cancelled(cancelled)
        with requests.get(url, stream=True, timeout=(3, 5)) as response:
            response.raise_for_status()
            with NamedTemporaryFile(dir=destination.parent, suffix=".part", delete=False) as output:
                temporary = Path(output.name)
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    check_cancelled(cancelled)
                    if chunk:
                        output.write(chunk)
            if temporary.stat().st_size == 0:
                raise OSError("O servidor retornou um modelo vazio")
            check_cancelled(cancelled)
            temporary.replace(destination)
    except (requests.RequestException, OSError) as exc:
        raise RuntimeError(
            f"Não foi possível baixar {destination.name}. Verifique a conexão e a "
            f"permissão de escrita em {destination.parent} e execute novamente."
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def ensure_models(cancelled=lambda: False):
    directory = model_directory()
    directory.mkdir(parents=True, exist_ok=True)
    # URLs oficiais da versão fixada no requirements; somente os três ONNX usados.
    sources = {
        "hey_jarvis": openwakeword.MODELS["hey_jarvis"]["download_url"],
        "melspectrogram": openwakeword.FEATURE_MODELS["melspectrogram"]["download_url"],
        "embedding_model": openwakeword.FEATURE_MODELS["embedding"]["download_url"],
    }
    paths = {}
    for name, source in sources.items():
        check_cancelled(cancelled)
        destination = directory / f"{name}.onnx"
        if not destination.is_file() or destination.stat().st_size == 0:
            _download_model(source.replace(".tflite", ".onnx"), destination, cancelled)
        paths[name] = str(destination)
    return paths


def create_model(cancelled=lambda: False):
    paths = ensure_models(cancelled)
    check_cancelled(cancelled)
    return Model(
        wakeword_models=[paths["hey_jarvis"]],
        inference_framework="onnx",
        enable_speex_noise_suppression=False,
        melspec_model_path=paths["melspectrogram"],
        embedding_model_path=paths["embedding_model"],
    )


def create_vad(cancelled=lambda: False):
    directory = model_directory()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "silero_vad.onnx"
    check_cancelled(cancelled)
    if not destination.is_file() or destination.stat().st_size == 0:
        _download_model(openwakeword.VAD_MODELS["silero_vad"]["download_url"], destination, cancelled)
    check_cancelled(cancelled)
    return openwakeword.VAD(model_path=str(destination), n_threads=1)


if __name__ == "__main__":
    create_model()
    create_vad()
    print("Modelos hey_jarvis e Silero VAD carregados com ONNX. Nenhum microfone foi aberto.")
