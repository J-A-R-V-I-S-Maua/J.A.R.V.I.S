"""Decisões de diálogo sem Qt, microfone ou widgets."""
from dataclasses import dataclass
import logging
from wakeword.events import State
from .commands import HELP_COMMANDS, SETTINGS_COMMANDS, normalize, help_text


@dataclass(frozen=True)
class Presentation:
    state: State
    message: str
    guidance: str = ""
    capture_available: bool = False


class InteractionCoordinator:
    def __init__(self):
        self.generation = 0
        self.retries = 0
        self.awaiting_retry = False

    def invalidate(self):
        self.generation += 1
        self.retries = 0
        self.awaiting_retry = False

    def listening(self):
        if not self.awaiting_retry:
            self.retries = 0
        self.awaiting_retry = False

    def retry(self, message):
        logging.info("dialog retry generation=%s previous_attempts=%s", self.generation, self.retries)
        if self.retries:
            self.awaiting_retry = False
            return "Não consegui concluir. Diga ‘Hey Jarvis’ para tentar novamente.", False
        self.retries += 1
        self.awaiting_retry = True
        return message, True

    def route(self, text, *, settings=False, screen_state="idle", shortcut=False):
        value = normalize(text)
        if settings:
            return "help" if value in HELP_COMMANDS else "settings"
        if screen_state == "dictating":
            return "screen"
        if value in HELP_COMMANDS:
            return "help"
        if shortcut:
            return "shortcut"
        if screen_state != "idle":
            return "screen"
        if value in SETTINGS_COMMANDS:
            return "open_settings"
        return "command"

    def help(self, *, settings=False, screen_state="idle", shortcut=False):
        return help_text("settings" if settings else "shortcut" if shortcut else screen_state)
