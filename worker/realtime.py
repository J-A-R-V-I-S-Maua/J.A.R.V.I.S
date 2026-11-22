"""Serviço WebSocket de transcrição após a fala, separado das tarefas Celery."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import time

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from faster_whisper import WhisperModel
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import LocalEntryNotFoundError

from streaming_engine import StreamingSession

MODEL_NAME = "small"
# Vocabulário dos comandos: sem ele, palavras soltas como "deletar" saem "Delistar" ou "Militário".
# Medido com as vozes do macOS: "deletar" foi de 1 para 4 acertos em 4 e as formas do colar de 13
# para 20 em 20, sem mudar o ditado. O cabeçalho fica: sem ele, um "ok" virou "Colar aqui."
# Um cabeçalho só: um segundo ("Comandos em português: …") vazou como texto num "ok".
# WHISPER_INITIAL_PROMPT troca este texto por outro.
DEFAULT_PROMPT = ("Comandos do Jarvis: deletar, apagar tudo, limpar campo, copiar, colar, colar texto, "
                  "cole aqui, recortar, selecionar tudo, mostrar números, mostrar janelas, digitar, "
                  "confirmar, cancelar, sim, não.")
INITIAL_PROMPT = os.getenv("WHISPER_INITIAL_PROMPT", "").strip() or DEFAULT_PROMPT


def without_prompt_echo(text):
    """Às vezes, numa fala curta ("ok"), o Whisper devolve o começo do próprio prompt. Isso não é fala."""
    plain = text.strip(" .!?,").casefold()
    return "" if plain and INITIAL_PROMPT.casefold().startswith(plain) else text


def transcription_options():
    # Sem max_new_tokens: o limite de 12 cortava o ditado ("…comprar arroz" sem o resto da frase).
    return dict(
        language="pt",
        beam_size=5,
        temperature=0.0,
        condition_on_previous_text=False,
        without_timestamps=True,
        no_speech_threshold=0.6,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 200},
        # As abas entram aqui, e não no vocabulário: lá elas apagavam o ditado. Medido com gravações
        # reais (alto-falante para o microfone, voz Luciana): 35 de 36 comandos virou 36 de 36,
        # e o ditado ficou idêntico palavra por palavra ("nova aba" saía "Não é variável").
        hotwords="sim, não, confirmar, cancelar, nova aba, fechar aba, próxima aba",
        initial_prompt=INITIAL_PROMPT,
    )


def load_model():
    # Revisão oficial fixa; reutilizar cache sem consultar a listagem remota a cada boot.
    repo = "Systran/faster-whisper-small"
    revision = "536b0662742c02347bc0e980a01041f333bce120"
    directory = None
    for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        try:
            path = hf_hub_download(repo, filename, revision=revision, local_files_only=True)
        except LocalEntryNotFoundError:
            path = hf_hub_download(repo, filename, revision=revision)
        directory = str(Path(path).parent)
    model = WhisperModel(directory, device="cpu", compute_type="float32", cpu_threads=4, num_workers=2)
    # Aquecimento antes de aceitar conexões.
    list(model.transcribe(np.zeros(16000, dtype=np.float32), **transcription_options())[0])
    return model


@asynccontextmanager
async def lifespan(app):
    app.state.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
    loop = asyncio.get_running_loop()
    app.state.model = await loop.run_in_executor(app.state.executor, load_model)
    app.state.connection_lock = asyncio.Lock()
    yield
    app.state.executor.shutdown(wait=True)


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"ready": True, "model": MODEL_NAME, "compute_type": "float32",
            "beam_size": 5, "temperature": 0.0, "prompt_enabled": INITIAL_PROMPT is not None}


@app.websocket("/transcribe/stream")
async def transcribe_stream(socket: WebSocket):
    await socket.accept()
    lock = app.state.connection_lock
    if lock.locked():
        await socket.send_json({"type": "error", "message": "Reconhecimento ocupado. Tente novamente."})
        await socket.close(code=1013)
        return
    async with lock:
        async def decode(pcm):
            def infer():
                began = time.monotonic()
                samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
                segments, _ = app.state.model.transcribe(samples, **transcription_options())
                kept = [
                    segment.text for segment in segments
                    if getattr(segment, "no_speech_prob", 0.0) <= 0.6
                    and getattr(segment, "avg_logprob", -1.0) >= -1.0
                ]
                text = without_prompt_echo(" ".join(" ".join(kept).split()))
                logging.getLogger("uvicorn.error").info(
                    "stream inference audio=%.2fs elapsed=%.3fs", len(samples) / 16000,
                    time.monotonic() - began)
                return text
            return await asyncio.get_running_loop().run_in_executor(app.state.executor, infer)

        session = StreamingSession(decode, socket.send_json)
        try:
            await socket.send_json({"type": "ready", "sample_rate": 16000, "channels": 1,
                                    "format": "pcm_s16le", "max_seconds": 30})
            while True:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    session.feed(message["bytes"])
                    continue
                command = json.loads(message.get("text") or "{}")
                if not isinstance(command, dict):
                    raise ValueError("Mensagem inválida.")
                kind = command.get("type")
                if kind == "start":
                    if command.get("sample_rate") != 16000 or command.get("channels") != 1 or command.get("format") != "pcm_s16le":
                        raise ValueError("Formato de áudio inválido.")
                    session.start(command.get("interaction_id"))
                elif kind in ("finish", "cancel"):
                    if command.get("interaction_id") != session.interaction_id:
                        raise ValueError("Identificador de interação incorreto.")
                    if kind == "cancel":
                        session.cancel()
                        break
                    session.finish()
                else:
                    raise ValueError("Mensagem de controle desconhecida.")
        except (ValueError, json.JSONDecodeError) as exc:
            await socket.send_json({"type": "error", "interaction_id": session.interaction_id,
                                    "message": str(exc)})
        except WebSocketDisconnect:
            pass
        finally:
            await session.close()
            try:
                await socket.close()
            except RuntimeError:
                pass
