"""Transporte simultâneo de áudio e hipóteses, sem bloquear a captura."""
import asyncio
import json
import os
import queue
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import connect

from .events import check_cancelled
from .transcription_client import Transcript, TranscriptionError

FINAL_TIMEOUT_SECONDS = 20


class StreamingClient:
    def __init__(self, url=None):
        api = urlsplit(os.getenv("API_URL", "http://localhost:8000"))
        self.url = url or urlunsplit(("wss" if api.scheme == "https" else "ws",
                                     api.netloc, api.path.rstrip("/") + "/transcribe/stream", "", ""))
        self.outgoing = queue.Queue(maxsize=32)  # no máximo ~2,5 segundos de áudio
        self.ready = threading.Event()
        self.completed = threading.Event()
        self.closing = threading.Event()
        self.thread = None
        self.error = None
        self.result = None
        self.interaction_id = None
        self.sequence = 0
        self.on_partial = lambda text, sequence: None
        self.last_response = time.monotonic()

    def prepare(self, cancelled):
        self.thread = threading.Thread(target=self._run, name="speech-websocket")
        self.thread.start()
        deadline = time.monotonic() + 35
        while not self.ready.wait(0.05):
            check_cancelled(cancelled)
            self.check()
            if time.monotonic() >= deadline:
                raise TranscriptionError("O reconhecimento ainda não está pronto. Aguarde e tente novamente.")
        check_cancelled(cancelled)
        self.check()

    def _run(self):
        try:
            asyncio.run(self._connection())
        except Exception as exc:
            if not self.closing.is_set():
                self.error = exc if isinstance(exc, TranscriptionError) else TranscriptionError(
                    "Conexão de transcrição interrompida. Verifique o Docker e tente novamente.")
        finally:
            self.completed.set()

    async def _connection(self):
        tasks = []
        try:
            async with connect(self.url, open_timeout=3, close_timeout=1,
                               max_size=65536, max_queue=8, ping_interval=10, ping_timeout=10) as socket:
                deadline = time.monotonic() + 30
                while True:
                    if self.closing.is_set():
                        return
                    try:
                        first = json.loads(await asyncio.wait_for(socket.recv(), timeout=0.1))
                        break
                    except TimeoutError:
                        if time.monotonic() >= deadline:
                            raise TranscriptionError("O serviço demorou para ficar pronto.")
                if first.get("type") != "ready":
                    raise TranscriptionError(first.get("message", "Serviço não está pronto."))
                self.ready.set()

                async def sender():
                    while True:
                        try:
                            value = self.outgoing.get_nowait()
                        except queue.Empty:
                            await asyncio.sleep(0.01)
                            continue
                        await asyncio.wait_for(socket.send(value), timeout=2)

                async def receiver():
                    async for raw in socket:
                        message = json.loads(raw)
                        if not isinstance(message, dict):
                            raise ValueError("Mensagem inválida")
                        if message.get("type") == "error":
                            raise TranscriptionError(message.get("message", "Falha no reconhecimento."))
                        if message.get("interaction_id") != self.interaction_id:
                            continue
                        kind = message.get("type")
                        sequence = message.get("sequence")
                        text = message.get("text")
                        if kind not in ("partial", "final") or not isinstance(sequence, int) or not isinstance(text, str):
                            raise ValueError("Resposta de streaming inválida")
                        if sequence <= self.sequence or self.closing.is_set():
                            continue
                        self.sequence = sequence
                        self.last_response = time.monotonic()
                        if kind == "partial":
                            self.on_partial(text, sequence)
                        else:
                            self.result = Transcript(text, self.interaction_id)
                            self.completed.set()
                            return
                    if self.result is None and not self.closing.is_set():
                        raise TranscriptionError("Conexão encerrada antes da transcrição final.")

                async def shutdown():
                    while not self.closing.is_set():
                        await asyncio.sleep(0.02)
                    if self.interaction_id:
                        try:
                            await asyncio.wait_for(socket.send(json.dumps({
                                "type": "cancel", "interaction_id": self.interaction_id})), timeout=0.2)
                        except Exception:
                            pass

                tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver()),
                         asyncio.create_task(shutdown())]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def check(self):
        if self.error:
            raise self.error
        if self.completed.is_set() and self.result is None and not self.closing.is_set():
            raise TranscriptionError("A conexão de transcrição foi encerrada.")

    def _enqueue(self, value):
        self.check()
        try:
            self.outgoing.put_nowait(value)
        except queue.Full as exc:
            raise TranscriptionError("O envio do áudio atrasou. Tente novamente.") from exc

    def begin(self, interaction_id, on_partial):
        self.interaction_id = str(interaction_id)
        self.on_partial = on_partial
        self._enqueue(json.dumps({"type": "start", "interaction_id": self.interaction_id,
                                  "sample_rate": 16000, "channels": 1, "format": "pcm_s16le"}))

    def send_audio(self, pcm):
        self._enqueue(pcm)

    def finish(self, cancelled):
        self._enqueue(json.dumps({"type": "finish", "interaction_id": self.interaction_id}))
        deadline = time.monotonic() + FINAL_TIMEOUT_SECONDS
        while not self.completed.wait(0.05):
            check_cancelled(cancelled)
            self.check()
            if time.monotonic() >= deadline:
                raise TranscriptionError("A finalização demorou demais. Tente novamente.")
        check_cancelled(cancelled)
        self.check()
        if self.result is None:
            raise TranscriptionError("Não foi recebida uma transcrição final.")
        return self.result

    def close(self):
        self.closing.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join()  # Conexão/envio/recepção têm timeouts e fechamento cooperativo.
