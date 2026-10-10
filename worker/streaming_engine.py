"""Acumula a fala e transcreve uma única vez após a finalização."""
import asyncio

RATE = 16000
BYTES_PER_SECOND = RATE * 2
MAX_SECONDS = 30


class StreamingSession:
    def __init__(self, decode, send):
        self.decode = decode
        self.send = send
        self.audio = bytearray()
        self.interaction_id = None
        self.sequence = 0
        self.finishing = False
        self.cancelled = False
        self.done = False
        self.work = asyncio.Event()
        self.task = None

    def start(self, interaction_id):
        if self.interaction_id is not None:
            raise ValueError("Uma conexão aceita apenas uma interação.")
        if not isinstance(interaction_id, str) or not 1 <= len(interaction_id) <= 64:
            raise ValueError("Identificador de interação inválido.")
        self.interaction_id = interaction_id
        self.task = asyncio.create_task(self._run())

    def feed(self, data):
        if self.interaction_id is None or self.finishing or self.cancelled or self.done:
            raise ValueError("Áudio recebido fora de uma interação ativa.")
        if not data or len(data) % 2 or len(data) > 2560:
            raise ValueError("Use PCM16 mono em blocos de até 80 ms.")
        if len(self.audio) + len(data) > MAX_SECONDS * BYTES_PER_SECOND:
            raise ValueError("Limite de áudio excedido.")
        self.audio.extend(data)

    def finish(self):
        if self.interaction_id is None or self.cancelled or self.done or self.finishing:
            raise ValueError("Não há interação ativa para finalizar.")
        self.finishing = True
        self.work.set()

    def cancel(self):
        self.cancelled = True
        self.work.set()

    async def close(self):
        self.cancel()
        if self.task:
            # Não liberar a capacidade enquanto a inferência em CPU ainda executa.
            await self.task
        self.audio.clear()

    async def _emit(self, kind, text):
        self.sequence += 1
        await self.send({"type": kind, "interaction_id": self.interaction_id,
                         "sequence": self.sequence, "text": text})

    async def _run(self):
        try:
            await self.work.wait()
            if self.cancelled:
                return
            snapshot = bytes(self.audio)
            text = await self.decode(snapshot) if snapshot else ""
            if not self.cancelled:
                await self._emit("final", text)
        except Exception:
            if not self.cancelled:
                try:
                    await self.send({"type": "error", "interaction_id": self.interaction_id,
                                     "sequence": self.sequence + 1,
                                     "message": "Falha no reconhecimento de fala."})
                except Exception:
                    pass
        finally:
            self.done = True
