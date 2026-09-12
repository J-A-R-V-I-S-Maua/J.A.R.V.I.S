import time
import os
from celery import Celery
from numpy._core.numerictypes import float32
import requests
from faster_whisper import WhisperModel
from celery.app.base import Celery
from tempfile import NamedTemporaryFile

celery_app = Celery(
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_BACKEND_URL", "redis://localhost:6379/0"),
    broker_connection_retry_on_startup=True,
)

celery_app.conf.beat_schedule = {
    "cleanup-audio-every-hour": {
           "task": "task.cleanup_old_audio",
           "schedule": 3600.0,  # a cada 1h
       },
}

def _run_transcription(path: str, language: str):
    segments, _ = model.transcribe(
        path,
        language = language,
        vad_filter = True,
        condition_on_previous_text=False,
        temperature= 0.2
    )

    return[
        {
            "start": s.start,
            "end": s.end,
            "text": s.text,
        }
        for s in segments
    ]

model = WhisperModel(
    model_size_or_path="small",
    device="cpu",
    compute_type="float32"
)

@celery_app.task(name="task.transcribe")
def transcribe (url, language):
    start_time = time.perf_counter()

    response = requests.get(url, timeout=10)
    response.raise_for_status()

    with NamedTemporaryFile(delete=True, suffix=".audio") as temp_file:
        temp_file.write(response.content)
        temp_file.flush()
        print(temp_file)
        transcript = _run_transcription(temp_file.name, language)

    elapsed_time = time.perf_counter() - start_time

    return {
        "elapsed_time": f"{elapsed_time:.2f}",
        "transcript": transcript,
        "url": url,
    }

@celery_app.task(name="task.transcribe_file")
def transcribe_file(file_path, language):
    start_time = time.perf_counter()
    transcript = _run_transcription(file_path, language)
    elapsed_time = time.perf_counter() - start_time

    return {
        "elapsed_time": f"{elapsed_time:.2f}",
        "transcript": transcript,
        "source_file": file_path,
        }

@celery_app.task(name="task.cleanup_old_audio")
def cleanup_old_audio():
    rettention_hours = float(os.getenv("AUDIO_RETENTION_HOURS", 24))
    cutoff = time.time() - rettention_hours * 3600
    audio_dir = os.getenv("AUDIO_UPLOAD_DIR", "/app/audio_uploads")

    removed = []
    if os.path.isdir(audio_dir):
        for fname in os.listdir(audio_dir):
            path = os.path.join(audio_dir, fname) 
            if os.path.isfile(path) and os.path.getatime(path) < cutoff:
                os.remove(path)
                removed.append(fname)
    return {
        "removed": removed,
        "count": len(removed)
    }
