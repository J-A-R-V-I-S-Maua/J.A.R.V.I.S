"""Inserção Unicode literal no cursor, sem interpretar atalhos ou usar clipboard."""


def send_literal_text(text, allowed=lambda: True):
    import ctypes
    import time
    import uiautomation as api
    from .backend import ScreenError

    if not text or any(ord(char) < 32 for char in text):
        raise ScreenError("O texto deve conter uma frase sem teclas de controle.")
    send = ctypes.windll.user32.SendInput
    send.argtypes = [ctypes.c_uint, ctypes.POINTER(api.INPUT), ctypes.c_int]
    send.restype = ctypes.c_uint
    for char in text:
        if not allowed():
            raise ScreenError("Digitação interrompida. Confira o texto já inserido antes de repetir.")
        encoded = char.encode("utf-16-le")
        events = []
        for offset in range(0, len(encoded), 2):
            unit = int.from_bytes(encoded[offset:offset+2], "little")
            events.append(api.KeyboardInput(0, unit, api.KeyboardEventFlag.KeyUnicode))
            events.append(api.KeyboardInput(0, unit, api.KeyboardEventFlag.KeyUnicode | api.KeyboardEventFlag.KeyUp))
        inputs = (api.INPUT * len(events))(*events)
        sent = send(len(events), inputs, ctypes.sizeof(api.INPUT))
        if sent != len(events):
            raise ScreenError("O Windows não inseriu todo o texto. Confira o campo antes de repetir.")
        # Alguns provedores (Qt inclusive) processam pares substitutos separadamente
        # dos caracteres BMP; dar tempo ao destino preserva a ordem da frase.
        time.sleep(0.01)
