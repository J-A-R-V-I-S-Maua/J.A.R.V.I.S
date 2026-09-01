from unittest.case import expectedFailure

import pyaudio
from openwakeword.model import Model
from scipy.integrate._ivp.dop853_coefficients import np



FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNCK = 1280
audio = pyaudio.PyAudio()
mic_stream = audio.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNCK)

owwModel = Model(enable_speex_noise_suppression=True)

def start():
    """ Incia o servico para ouvir a chamada "hey jarvis" """

    print("Escutando...")

    try:
        while True: 
    
            audio = np.frombuffer(mic_stream.read(CHUNCK), dtype=np.int16)  

            prediction = owwModel.predict(audio, timing=False)
    
            if isinstance(prediction, dict):
    
                score = prediction['hey_jarvis']
    
                if score >= 0.3:
                    print("Hey Jarvis detected")
    except KeyboardInterrupt:
        print("Parando...")
        
        