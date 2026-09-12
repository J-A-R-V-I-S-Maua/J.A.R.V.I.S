from posixpath import basename
from socket import timeout

import pyaudio
from openwakeword.model import Model
import numpy as np
import os
import requests
import time
import wave 


FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNCK = 1280

RECORD_SECONDS = int(os.getenv("RECORD_SECONDS", 5))
DETECTION_THRESHOLD = float(os.getenv("DETECTION_THRESHOLD", 0.3))
API_URL = os.getenv("API_URL", "http://localhost:8000")
LANGUAGE = os.getenv("TRANSCRIBE_LANGUAGE", "pt")
RECORDINGS_DIR = os.getenv("RECORDINGS_DIR", "./recordings")

os.makedirs(RECORDINGS_DIR, exist_ok=True)

audio_interface = pyaudio.PyAudio()
mic_stream = audio_interface.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNCK)

owwModel = Model(enable_speex_noise_suppression=True)

def record_audio(seconds: int) -> str:
    """Grava 'seconds' segundos de áudio do microfone e salva localmente em WAV."""
    frames = []
    num_chuncks = int(RATE/CHUNCK * seconds)
    for _ in range(num_chuncks):
       frames.append(mic_stream.read(CHUNCK, exception_on_overflow=False))

    filename = os.path.join(RECORDINGS_DIR, f"{int(time.time())}.wav")
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(audio_interface.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b"".join(frames))
    print(f"Áudio salvo em {filename}")
    return filename

def send_for_transcription(file_path: str):
    """Envia o áudio gravado para a API de transcrição"""
    try:
        with open(file_path, "rb") as f:
            response = requests.post(
                f"{API_URL}/transcribe/upload",
                files={"file": (os.path.basename(file_path), f, "audio/wav")},
                data={"language": LANGUAGE},
                timeout=10
            )
        response.raise_for_status()
        data = response.json()
        print(f"Enviado para transcrição. task_id={data['task_id']}")
        return data["task_id"]
    except requests.RequestException as exc:
        print(f"Falha ao enviar áudio para a API: {exc}")
        return None
    
def start():
    """Inicia o serviço para ouvir a wake word 'hey jarvis'."""
    print("Escutando...")
    try:
        while True:
            audio_chunk = np.frombuffer(
                mic_stream.read(CHUNCK, exception_on_overflow=False), dtype=np.int16
            )
            prediction = owwModel.predict(audio_chunk, timing=False)

            if isinstance(prediction, dict) and prediction.get("hey_jarvis", 0) >= DETECTION_THRESHOLD:
                print("Hey Jarvis detectado! Ouvindo comando...")
                if hasattr(owwModel, "reset"):
                    owwModel.reset()  # evita re-disparo no mesmo trecho de buffer, se suportado nessa versão
                file_path = record_audio(RECORD_SECONDS)
                send_for_transcription(file_path)
                print("Escutando...")
    except KeyboardInterrupt:
        print("Parando...")
    finally:
        mic_stream.stop_stream()
        mic_stream.close()
        audio_interface.terminate()
        
        