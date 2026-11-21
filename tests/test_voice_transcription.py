import asyncio
import unittest
from unittest.mock import Mock, patch

from worker.streaming_engine import StreamingSession
from wakeword.speech_gate import SpeechConfig, SpeechGate


class TranscriptionSessionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.decoded = []
        self.messages = []

        async def decode(pcm):
            self.decoded.append(pcm)
            return "Abra o navegador"

        async def send(message):
            self.messages.append(message)

        self.session = StreamingSession(decode, send)
        self.session.start("command-1")

    async def asyncTearDown(self):
        await self.session.close()

    async def test_waits_for_finish_then_decodes_complete_audio_once(self):
        chunks = [bytes([index % 256, 0]) * 1280 for index in range(250)]
        for chunk in chunks:  # 20 seconds: no partial responses are required.
            self.session.feed(chunk)
            await asyncio.sleep(0)
        self.assertEqual(self.decoded, [])
        self.assertEqual(self.messages, [])

        self.session.finish()
        await self.session.task
        self.assertEqual(self.decoded, [b"".join(chunks)])
        self.assertEqual(self.messages, [{
            "type": "final", "interaction_id": "command-1",
            "sequence": 1, "text": "Abra o navegador",
        }])

    async def test_cancel_during_capture_does_not_decode(self):
        self.session.feed(b"\x01\x00" * 1280)
        self.session.cancel()
        await self.session.task
        self.assertEqual(self.decoded, [])
        self.assertEqual(self.messages, [])

    async def test_cancel_during_inference_discards_result(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def decode(pcm):
            started.set()
            await release.wait()
            return "Feche o bloco de notas"

        self.session.decode = decode
        self.session.feed(b"\x01\x00" * 1280)
        self.session.finish()
        await asyncio.wait_for(started.wait(), timeout=1)
        self.session.cancel()
        release.set()
        await self.session.task
        self.assertEqual(self.messages, [])

    async def test_audio_cannot_be_added_after_finish(self):
        self.session.feed(b"\x01\x00" * 1280)
        self.session.finish()
        with self.assertRaises(ValueError):
            self.session.feed(b"\x01\x00" * 1280)
        await self.session.task


class SpeechEndTest(unittest.TestCase):
    def test_short_pause_keeps_capture_open_and_two_seconds_end_it(self):
        gate = SpeechGate(SpeechConfig())
        for _ in range(3):
            self.assertIsNone(gate.feed(0.9))
        for _ in range(10):
            self.assertIsNone(gate.feed(0.0))
        self.assertIsNone(gate.feed(0.9))
        for _ in range(24):
            self.assertIsNone(gate.feed(0.0))
        self.assertEqual(gate.feed(0.0), "finish")

    def test_no_speech_ends_without_transcription(self):
        gate = SpeechGate(SpeechConfig())
        for _ in range(124):
            self.assertIsNone(gate.feed(0.0))
        self.assertEqual(gate.feed(0.0), "no_speech")


class VoiceCaptureTest(unittest.TestCase):
    def test_preserves_quiet_onset_before_voice_detection(self):
        from wakeword.streaming_service import StreamingVoiceService

        service = StreamingVoiceService(speech_config=SpeechConfig())
        service.connection = Mock()
        service.vad = Mock()
        service._interaction_id = 1
        # 560 ms abaixo do limiar + 160 ms para confirmar voz = 720 ms.
        service.vad.predict.side_effect = [0.1] * 7 + [0.9] * 2 + [0.0] * 25
        chunks = [bytes([index, 0]) * 1280 for index in range(34)]
        with patch("wakeword.streaming_service._read_chunk", side_effect=chunks):
            service._stream_audio(None, None, 1)
        sent = [call.args[0] for call in service.connection.send_audio.call_args_list]
        self.assertEqual(sent, chunks)

    def test_avisa_uma_vez_quando_a_fala_comeca(self):
        # O modo contínuo apaga a contagem nesse momento: a frase pode durar mais que a espera.
        from wakeword.streaming_service import StreamingVoiceService

        service = StreamingVoiceService(speech_config=SpeechConfig())
        service.connection = Mock()
        service.vad = Mock()
        service.on_speech_start = Mock()
        service.vad.predict.side_effect = [0.1] * 5 + [0.9] * 6 + [0.0] * 25
        chunks = [bytes([index, 0]) * 1280 for index in range(36)]
        with patch("wakeword.streaming_service._read_chunk", side_effect=chunks):
            service._stream_audio(None, None, 1)
        service.on_speech_start.assert_called_once_with()

    def test_bar_waits_until_end_of_speech_and_audio_is_preserved(self):
        from wakeword.events import State
        from wakeword.streaming_service import StreamingVoiceService

        events = []
        service = StreamingVoiceService(events.append, speech_config=SpeechConfig())
        service.connection = Mock()
        service.vad = Mock()
        service._interaction_id = 1
        service._publish(State.LISTENING, interaction_id=1)
        # 20 seconds of speech followed by the two-second silence threshold.
        probabilities = [0.9] * 250 + [0.0] * 25
        service.vad.predict.side_effect = probabilities
        chunks = [bytes([index % 256, 0]) * 1280 for index in range(275)]

        def read_chunk(stream, cancelled):
            self.assertEqual(events[-1].state, State.LISTENING)
            return chunks[read_chunk.index]

        read_chunk.index = 0

        def next_chunk(stream, cancelled):
            chunk = read_chunk(stream, cancelled)
            read_chunk.index += 1
            return chunk

        with patch("wakeword.streaming_service._read_chunk", side_effect=next_chunk):
            service._stream_audio(None, None, 1)

        service.connection.begin.assert_called_once_with(1)
        sent = [call.args[0] for call in service.connection.send_audio.call_args_list]
        self.assertEqual(sent, chunks)
        self.assertEqual([event.state for event in events], [State.LISTENING, State.PROCESSING])
        self.assertEqual([event.text for event in events], ["Ouvindo…", "Transcrevendo…"])


class MicrophoneSelectionTest(unittest.TestCase):
    def setUp(self):
        self.audio = Mock()
        self.audio.get_device_count.return_value = 3
        self.audio.get_default_input_device_info.return_value = {
            "index": 1, "name": "Default microphone", "maxInputChannels": 1,
        }
        self.audio.get_device_info_by_index.return_value = {
            "index": 2, "name": "Selected microphone", "maxInputChannels": 1,
        }

    def test_default_and_explicit_input(self):
        from wakeword.microphone import select_input_device
        with patch.dict("os.environ", {"MICROPHONE_DEVICE_INDEX": ""}):
            self.assertEqual(select_input_device(self.audio), 1)
        with patch.dict("os.environ", {"MICROPHONE_DEVICE_INDEX": "2"}):
            self.assertEqual(select_input_device(self.audio), 2)
            self.audio.get_device_info_by_index.assert_called_once_with(2)

    def test_rejects_invalid_index_and_output_device(self):
        from wakeword.microphone import select_input_device
        for configured in ("-1", "3", "invalid"):
            with self.subTest(configured=configured):
                with patch.dict("os.environ", {"MICROPHONE_DEVICE_INDEX": configured}):
                    with self.assertRaises(ValueError):
                        select_input_device(self.audio)
        self.audio.get_device_info_by_index.return_value["maxInputChannels"] = 0
        with patch.dict("os.environ", {"MICROPHONE_DEVICE_INDEX": "2"}):
            with self.assertRaises(ValueError):
                select_input_device(self.audio)


class WhisperPromptTest(unittest.TestCase):
    """O serviço realtime roda no Docker; aqui as bibliotecas dele viram dublês."""

    def load(self, environment):
        import importlib
        import os
        import sys
        from types import ModuleType

        fakes = {name: ModuleType(name) for name in ("fastapi", "faster_whisper", "huggingface_hub",
                                                     "huggingface_hub.errors", "streaming_engine")}
        fastapi = fakes["fastapi"]
        fastapi.FastAPI = Mock()
        fastapi.WebSocket, fastapi.WebSocketDisconnect = object, Exception
        fakes["faster_whisper"].WhisperModel = Mock()
        fakes["huggingface_hub"].hf_hub_download = Mock()
        fakes["huggingface_hub.errors"].LocalEntryNotFoundError = Exception
        fakes["streaming_engine"].StreamingSession = Mock()
        sys.modules.pop("worker.realtime", None)
        self.addCleanup(sys.modules.pop, "worker.realtime", None)
        with patch.dict(sys.modules, fakes), patch.dict(os.environ, environment, clear=False):
            if "WHISPER_INITIAL_PROMPT" not in environment:
                os.environ.pop("WHISPER_INITIAL_PROMPT", None)
            return importlib.import_module("worker.realtime")

    def test_vocabulario_de_comandos_vem_por_padrao(self):
        realtime = self.load({})
        prompt = realtime.transcription_options()["initial_prompt"]
        for command in ("deletar", "apagar tudo", "limpar campo", "copiar", "colar texto", "cole aqui",
                        "mostrar janelas"):
            self.assertIn(command, prompt)
        # Sem o cabeçalho, o Whisper chegou a transcrever um "ok" como "Colar aqui.".
        self.assertTrue(prompt.startswith("Comandos do Jarvis:"))

    def test_ditado_longo_nao_e_cortado_e_o_prompt_e_um_so(self):
        options = self.load({}).transcription_options()
        # max_new_tokens=12 cortava "digitar … comprar arroz, feijão e café" depois de "arroz".
        self.assertNotIn("max_new_tokens", options)
        self.assertEqual(options["initial_prompt"].count("Comandos"), 1)
        for word in ("sim", "não", "digitar", "confirmar", "cancelar"):
            self.assertIn(word, options["initial_prompt"])

    def test_eco_do_prompt_vira_silencio(self):
        realtime = self.load({})
        for echo in ("Comandos do Jarvis", "Comandos do Jarvis.", "comandos"):
            self.assertEqual(realtime.without_prompt_echo(echo), "")
        for speech in ("Deletar.", "Ok.", "Digitar bom dia a todos."):
            self.assertEqual(realtime.without_prompt_echo(speech), speech)

    def test_palavras_chave_incluem_as_abas_sem_mexer_no_vocabulario(self):
        realtime = self.load({})
        options = realtime.transcription_options()
        for word in ("nova aba", "fechar aba", "próxima aba", "confirmar", "cancelar"):
            self.assertIn(word, options["hotwords"])
        self.assertNotIn("aba", realtime.DEFAULT_PROMPT)  # no vocabulário elas apagavam o ditado

    def test_variavel_vazia_mantem_o_padrao_e_preenchida_substitui(self):
        self.assertEqual(self.load({"WHISPER_INITIAL_PROMPT": "  "}).INITIAL_PROMPT,
                         self.load({}).DEFAULT_PROMPT)
        self.assertEqual(self.load({"WHISPER_INITIAL_PROMPT": "Comandos: abra, feche."})
                         .transcription_options()["initial_prompt"], "Comandos: abra, feche.")


class WakeWordModelTest(unittest.TestCase):
    def test_supressor_de_ruido_so_liga_com_o_pacote_instalado(self):
        # Ligado sem o speexdsp-ns (Mac e Windows), o openWakeWord falhava e a voz não iniciava.
        from wakeword import model_loader

        paths = {
            "hey_jarvis": "hj.onnx",
            "melspectrogram": "mel.onnx",
            "embedding_model": "emb.onnx",
        }
        for spec, expected in ((None, False), (object(), True)):
            with self.subTest(instalado=expected), patch.object(
                model_loader, "ensure_models", return_value=paths
            ), patch.object(model_loader, "find_spec", return_value=spec), patch.object(
                model_loader, "Model"
            ) as model:
                model_loader.create_model()
                self.assertIs(
                    model.call_args.kwargs["enable_speex_noise_suppression"], expected
                )

if __name__ == "__main__":
    unittest.main()
