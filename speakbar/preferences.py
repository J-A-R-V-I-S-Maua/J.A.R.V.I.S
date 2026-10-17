"""Preferências locais versionadas; o headless continua usando o ambiente."""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
from wakeword.speech_gate import SpeechConfig


def defaults():
    speech = SpeechConfig.from_environment()
    return dict(microphone=None, silence_seconds=speech.silence_seconds,
                start_timeout=speech.start_timeout, max_seconds=speech.max_seconds,
                detection_threshold=float(os.getenv("DETECTION_THRESHOLD", "0.3")),
                text_size=18, number_size=12, high_contrast=False, remember_position=True,
                position=None, max_depth=int(os.getenv("SCREEN_MAX_DEPTH", "32")),
                max_elements=int(os.getenv("SCREEN_MAX_ELEMENTS", "3000")))


def validate_preferences(values):
    result = defaults()
    if not isinstance(values, dict) or set(values) - set(result):
        raise ValueError("Preferências inválidas.")
    result.update(values)
    for key in ("silence_seconds", "start_timeout", "max_seconds", "detection_threshold"):
        if isinstance(result[key], bool) or not isinstance(result[key], (int, float)) or not math.isfinite(result[key]):
            raise ValueError("Tempos e sensibilidade devem ser números válidos.")
    SpeechConfig(result["silence_seconds"], result["start_timeout"], result["max_seconds"])
    if not 0.01 <= result["detection_threshold"] <= 1:
        raise ValueError("Sensibilidade deve estar entre 0,01 e 1.")
    for key, minimum, maximum in (("text_size", 14, 28), ("number_size", 10, 24),
                                   ("max_depth", 0, 2147483647), ("max_elements", 1, 10000)):
        if type(result[key]) is not int or not minimum <= result[key] <= maximum:
            raise ValueError(f"Valor inválido: {key}.")
    for key in ("remember_position", "high_contrast"):
        if type(result[key]) is not bool:
            raise ValueError("Preferência de aparência inválida.")
    microphone = result["microphone"]
    if microphone is not None and (not isinstance(microphone, dict) or set(microphone) != {"name", "api"}
                                  or not all(isinstance(v, str) and v.strip() for v in microphone.values())):
        raise ValueError("Identificação do microfone inválida.")
    position = result["position"]
    if position is not None and (not isinstance(position, list) or len(position) != 2
                                 or any(type(v) is not int for v in position)):
        raise ValueError("Posição da barra inválida.")
    return result


class PreferencesService:
    def __init__(self, path=None):
        if path is None:
            from PySide6.QtCore import QStandardPaths
            path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)) / "settings.json"
        self.path = Path(path)
        self.overrides = {}
        self.shortcuts = []
        self.error = None
        self.load()

    @property
    def effective(self):
        return validate_preferences(self.overrides)

    def load(self):
        if not self.path.exists():
            return
        try:
            from .shortcuts import validate_shortcuts
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1:
                raise ValueError("Versão de configurações não suportada.")
            validate_preferences(data["preferences"])
            shortcuts = validate_shortcuts(data["shortcuts"], check_destination=False)
            self.overrides = data["preferences"]
            self.shortcuts = shortcuts
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.error = f"Não foi possível ler as configurações; arquivo preservado: {exc}"

    def save(self, preferences, shortcuts):
        from .shortcuts import validate_shortcuts
        validate_preferences(preferences)
        shortcuts = validate_shortcuts(shortcuts, check_destination=False)
        data = dict(version=1, preferences=preferences, shortcuts=shortcuts)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            # Preservar também o arquivo inválido se o usuário salvar novas preferências.
            if self.error and self.path.exists():
                import shutil
                import uuid
                shutil.copy2(self.path, self.path.with_name(f"settings.invalid-{uuid.uuid4().hex}.json"))
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".settings-", suffix=".tmp", delete=False) as output:
                temporary = output.name
                json.dump(data, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and Path(temporary).exists():
                Path(temporary).unlink()
        self.overrides = copy.deepcopy(preferences)
        self.shortcuts = copy.deepcopy(shortcuts)
        self.error = None

    def restore(self):
        self.save({}, self.shortcuts)
