"""Contrato de eventos do host, independente da interface gráfica."""
from dataclasses import dataclass
from enum import Enum


class State(Enum):
    PREPARING = "Preparando reconhecimento…"
    IDLE = "Aguardando comando"
    LISTENING = "Ouvindo…"
    PROCESSING = "Transcrevendo…"
    RESULT = "Transcrição concluída"
    ERROR = "Não foi possível concluir"
    CANCELLING = "Cancelando…"
    STOPPING = "Encerrando…"
    STOPPED = "Encerrado"


class Reason(Enum):
    NO_SPEECH = "no_speech"
    EMPTY_TRANSCRIPT = "empty_transcript"
    CONNECTION = "connection_unavailable"
    MICROPHONE = "microphone_unavailable"
    MODEL = "model_unavailable"


@dataclass(frozen=True)
class VoiceEvent:
    state: State
    text: str
    interaction_id: int = 0
    task_id: str | None = None
    sequence: int = 0
    is_final: bool = False
    reason: Reason | None = None
    capture_ready: bool = False
    diagnostic: bool = False


class Cancelled(Exception):
    """Interrupção solicitada pelo usuário, sem representar falha."""


def check_cancelled(cancelled):
    if cancelled():
        raise Cancelled()
