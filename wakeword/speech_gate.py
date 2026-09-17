"""Tempos de fala em amostras, independentes da velocidade de rede/inferência."""
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class SpeechConfig:
    silence_seconds: float = 2.0
    start_timeout: float = 10.0
    max_seconds: float = 30.0

    def __post_init__(self):
        if not (0 < self.silence_seconds <= 30 and 0 < self.start_timeout <= 30
                and 0 < self.max_seconds <= 30):
            raise ValueError("Os tempos de streaming devem estar entre 0 e 30 segundos.")

    @classmethod
    def from_environment(cls):
        return cls(float(os.getenv("STREAM_SILENCE_SECONDS", "2")),
                   float(os.getenv("STREAM_START_TIMEOUT_SECONDS", "10")),
                   float(os.getenv("STREAM_MAX_SECONDS", "30")))


class SpeechGate:
    def __init__(self, config):
        self.config = config
        self.samples = 0
        self.voiced_samples = 0
        self.last_voice = 0
        self.has_speech = False

    def feed(self, probability, samples=1280):
        self.samples += samples
        if probability >= 0.5:
            self.voiced_samples += samples
            self.last_voice = self.samples
            if self.voiced_samples >= 2560:  # 160 ms de voz para evitar impulsos isolados
                self.has_speech = True
        else:
            self.voiced_samples = 0
        seconds = self.samples / 16000
        if not self.has_speech and seconds >= min(self.config.start_timeout, self.config.max_seconds):
            return "no_speech"
        if seconds >= self.config.max_seconds:
            return "finish"
        if self.has_speech and (self.samples - self.last_voice) / 16000 >= self.config.silence_seconds:
            return "finish"
        return None
