"""Cola entre o texto reconhecido e a execução da ação."""

from dataclasses import dataclass
import logging

from .executor import create_executor
from .intents import Intent, resolve

logger = logging.getLogger(__name__)

UNKNOWN_FEEDBACK = (
    "Não entendi “{}”. Tente “abre o youtube” ou “pesquisa por gatos”."
)
FAILURE_FEEDBACK = "Não foi possível executar o comando. Tente novamente."


@dataclass(frozen=True)
class CommandResult:
    recognized: bool
    feedback: str
    intent: Intent | None = None


class CommandDispatcher:
    def __init__(self, executor=None):
        self.executor = executor or create_executor()

    def handle(self, text):
        """Nunca levanta exceção: o chamador é a thread gráfica."""
        intent = resolve(text or "")
        if intent is None:
            return CommandResult(False, UNKNOWN_FEEDBACK.format(text or ""))
        try:
            feedback = self.executor.execute(intent)
        except Exception:
            logger.exception("Falha ao executar %s", intent.name)
            return CommandResult(True, FAILURE_FEEDBACK, intent)
        return CommandResult(True, feedback, intent)
