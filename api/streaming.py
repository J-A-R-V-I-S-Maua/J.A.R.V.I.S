"""Proxy bidirecional; a API não carrega modelos nem grava o áudio do streaming."""
import asyncio
from contextlib import suppress
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

router = APIRouter()


@router.websocket("/transcribe/stream")
async def stream(socket: WebSocket):
    await socket.accept()
    jobs = []
    try:
        async with connect(
            os.getenv("REALTIME_URL", "ws://realtime:8001/transcribe/stream"),
            open_timeout=3, close_timeout=1, max_size=65536, max_queue=8,
        ) as upstream:
            async def upload():
                while True:
                    packet = await socket.receive()
                    if packet["type"] == "websocket.disconnect":
                        return
                    data = packet.get("bytes")
                    if data is None:
                        data = packet.get("text", "")
                    if len(data) > 4096:
                        raise ValueError("Bloco de áudio inválido.")
                    await asyncio.wait_for(upstream.send(data), timeout=3)

            async def download():
                async for data in upstream:
                    await asyncio.wait_for(socket.send_text(data), timeout=3)

            jobs = [asyncio.create_task(upload()), asyncio.create_task(download())]
            done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
            for job in done:
                job.result()
    except (OSError, TimeoutError, WebSocketException, WebSocketDisconnect, ValueError):
        with suppress(RuntimeError, WebSocketDisconnect):
            await socket.send_json({"type": "error", "message": "Serviço de transcrição indisponível. Tente novamente."})
    finally:
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        with suppress(RuntimeError, WebSocketDisconnect):
            await socket.close()
