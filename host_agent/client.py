"""HTTP cancelável: a sessão de voz nunca espera uma chamada bloqueante de 30s."""
import asyncio
import os

import httpx

from contracts.commands import InterpretResponse, validate_proposal
from wakeword.events import check_cancelled


class CommandError(Exception):
    pass


class CommandClient:
    def __init__(self, url=None):
        self.url = (url or os.getenv("API_URL", "http://localhost:8000")).rstrip("/")

    def interpret(self, request, cancelled):
        return asyncio.run(self._interpret(request, cancelled))

    async def _interpret(self, request, cancelled):
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=3)) as client:
            task = asyncio.create_task(client.post(f"{self.url}/commands/interpret", json=request.model_dump()))
            try:
                async with asyncio.timeout(31):
                    while not task.done():
                        check_cancelled(cancelled)
                        await asyncio.wait({task}, timeout=0.025)
                    check_cancelled(cancelled)
                    response = task.result()
                    if response.is_error:
                        try:
                            detail = response.json().get("detail")
                        except (ValueError, AttributeError):
                            detail = None
                        raise CommandError(detail if isinstance(detail, str) else "IA local indisponível. Tente novamente.")
                    payload = response.json()
                    if not isinstance(payload, dict) or payload.get("protocol_version") != 2:
                        raise CommandError("Contrato incompatível. Atualize a API e o host para a versão 2.")
                    result = InterpretResponse.model_validate_json(response.content)
                    if result.interaction_id != request.interaction_id:
                        raise ValueError("Resposta de outra interação")
                    validate_proposal(result, request)
                    return result
            except (httpx.HTTPError, TimeoutError) as exc:
                raise CommandError("IA local indisponível ou demorando demais. Verifique o Ollama.") from exc
            except ValueError as exc:
                raise CommandError("A IA retornou uma proposta inválida. Nenhuma ação foi autorizada.") from exc
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
