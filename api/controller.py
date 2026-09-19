import os
from fastapi import FastAPI
from pydantic import BaseModel
from celery import Celery, uuid
from celery.result import AsyncResult
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from fastapi import FastAPI, UploadFile, File, Form

app = FastAPI()
from streaming import router as streaming_router
app.include_router(streaming_router)
from commands import router as commands_router
app.include_router(commands_router)

celery_app = Celery(
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_BACKEND_URL", "redis://localhost:6379/0"),
)

AUDIO_UPLOAD_DIR = os.getenv("AUDIO_UPLOAD_DIR", "/app/audio_uploads")
os.makedirs(AUDIO_UPLOAD_DIR, exist_ok=True)

class TranscriptionRequest(BaseModel):
    url: str
    language: str

@app.post("/transcribe")
async def post_transcribe(request: TranscriptionRequest):
    task_id = uuid()

    async_task = await run_in_threadpool(
            celery_app.send_task(
            "task.transcribe",
            args=[request.url, request.language],
            task_id=task_id,
        )
    )

    return {
        "task_id": async_task.id,
        "status": async_task.status
    }

@app.post("/transcribe/upload")
async def post_transcribe_upload(
    file: UploadFile = File(...),
    language: str = Form(...),
):
    task_id = uuid()
    extension = os.path.splitext(file.filename or "")[1] or ".wav"
    saved_path = os.path.join(AUDIO_UPLOAD_DIR, f"{task_id}{extension}")

    contents = await file.read()
    with open(saved_path, "wb") as f:
        f.write(contents)

    async_task = await run_in_threadpool(
        celery_app.send_task,
        "task.transcribe_file",
        args=[saved_path, language],
        task_id=task_id
    )
    return{
        "task_id": async_task.id,
        "status": async_task.status,
    }
    
@app.get("/transcribe/{task_id}/status")
async def get_transcribe_status(task_id: str):
    async_result = AsyncResult(task_id, app = celery_app)
    status = async_result.status
    if status in ("FAILURE", "REVOKED"):
        return {
            "status": status,
            "result": None,
            "error": str(async_result.result or "A tarefa foi cancelada."),
            "task_id": async_result.id,
        }
    return {
        "status": status,
        "result": async_result.result if async_result.ready() else None,
        "task_id": async_result.id,
    }
