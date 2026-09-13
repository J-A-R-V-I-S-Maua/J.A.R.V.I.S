"""Upload e consulta da transcrição; não depende de Qt ou do microfone."""
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests

from .events import check_cancelled


class TranscriptionError(Exception):
    pass


@dataclass(frozen=True)
class Transcript:
    text: str
    task_id: str


class TranscriptionClient:
    def __init__(self, api_url=None, language=None, timeout=120, session=None):
        self.api_url = (api_url or os.getenv("API_URL", "http://localhost:8000")).rstrip("/")
        self.language = language or os.getenv("TRANSCRIBE_LANGUAGE", "pt")
        self.timeout = timeout
        self.session = session or requests.Session()

    def close(self):
        self.session.close()

    def transcribe(self, file_path, cancel, on_submitted=lambda task_id: None):
        deadline = time.monotonic() + self.timeout

        def remaining_timeout():
            check_cancelled(cancel.is_set)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TranscriptionError("A transcrição demorou demais. Tente novamente.")
            return (min(3, remaining / 2), min(5, remaining / 2))

        def decode(response):
            with response:
                response.raise_for_status()
                try:
                    data = response.json()
                except ValueError as exc:
                    raise TranscriptionError("A API retornou uma resposta inválida. Tente novamente.") from exc
            check_cancelled(cancel.is_set)
            remaining_timeout()
            if not isinstance(data, dict):
                raise ValueError("Resposta JSON inválida")
            return data

        try:
            with open(file_path, "rb") as audio:
                data = decode(self.session.post(
                    f"{self.api_url}/transcribe/upload",
                    files={"file": (Path(file_path).name, audio, "audio/wav")},
                    data={"language": self.language}, timeout=remaining_timeout(),
                ))
            task_id = data.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                raise ValueError("task_id ausente")
            on_submitted(task_id)
            while True:
                data = decode(self.session.get(
                    f"{self.api_url}/transcribe/{quote(task_id, safe='')}/status",
                    timeout=remaining_timeout(),
                ))
                if data.get("task_id") != task_id:
                    raise ValueError("Identificador de tarefa incorreto")
                status = data.get("status")
                if status == "SUCCESS":
                    result = data.get("result")
                    segments = result.get("transcript") if isinstance(result, dict) else None
                    if not isinstance(segments, list) or any(
                        not isinstance(segment, dict) or not isinstance(segment.get("text"), str)
                        for segment in segments
                    ):
                        raise ValueError("Segmentos de transcrição inválidos")
                    text = " ".join(" ".join(segment["text"] for segment in segments).split())
                    return Transcript(text, task_id)
                if status in ("FAILURE", "REVOKED"):
                    raise TranscriptionError("Não foi possível transcrever o áudio. Tente novamente.")
                if status not in ("PENDING", "STARTED", "RECEIVED", "RETRY"):
                    raise ValueError("Estado de tarefa inválido")
                cancel.wait(min(0.5, max(0, deadline - time.monotonic())))
        except requests.RequestException as exc:
            check_cancelled(cancel.is_set)
            raise TranscriptionError("Não foi possível consultar a API. Verifique os serviços de transcrição.") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise TranscriptionError("A API retornou uma resposta inválida. Tente novamente.") from exc
