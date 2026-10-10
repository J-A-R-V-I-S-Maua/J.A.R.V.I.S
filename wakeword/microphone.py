"""Seleção e listagem de entradas de áudio sem iniciar uma gravação."""
import logging
import os


def select_input_device(audio):
    configured = os.getenv("MICROPHONE_DEVICE_INDEX", "").strip()
    if configured:
        try:
            index = int(configured)
        except ValueError as exc:
            raise ValueError("MICROPHONE_DEVICE_INDEX deve ser um índice inteiro.") from exc
        if not 0 <= index < audio.get_device_count():
            raise ValueError("Índice de microfone inexistente. Use --list-microphones.")
        device = audio.get_device_info_by_index(index)
    else:
        device = audio.get_default_input_device_info()
        index = int(device["index"])
    if device["maxInputChannels"] < 1:
        raise ValueError("O dispositivo selecionado não possui entrada de áudio.")
    logging.getLogger(__name__).info("Microfone selecionado: %s (índice %s)", device["name"], index)
    return index


def list_microphones():
    import pyaudio
    audio = pyaudio.PyAudio()
    try:
        try:
            default = int(audio.get_default_input_device_info()["index"])
        except OSError:
            default = None
        for index in range(audio.get_device_count()):
            device = audio.get_device_info_by_index(index)
            if device["maxInputChannels"] > 0:
                api = audio.get_host_api_info_by_index(device["hostApi"])["name"]
                marker = " (padrão do sistema)" if index == default else ""
                print(f"{index}: {device['name']} [{api}]{marker}")
    finally:
        audio.terminate()
