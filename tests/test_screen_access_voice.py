from types import SimpleNamespace
import unittest

from screen_access import flow
from screen_access.voice import VoiceNavigator
from wakeword.events import State, VoiceEvent

ITEMS = [
    ("e1", "AXRadioButton", "Notícias"),
    ("e2", "AXLink", "ENTENDA a FILOSOFIA de IMMANUEL KANT 20 minutos"),
    ("e3", "AXButton", flow.NO_NAME),
]


class Harness:
    """Monta um navegador com tela, clique e teclado falsos."""

    def __init__(self, typed=(), items=ITEMS, read_error=None):
        self.said = []
        self.pressed = []
        self.reads = 0
        typed = iter(typed)
        self._items = items
        self._read_error = read_error
        self.navigator = VoiceNavigator(
            self._read, self._press, lambda code: f"erro {code}",
            ask=lambda prompt="": next(typed), say=self.said.append)

    def _read(self):
        self.reads += 1
        if self._read_error:
            raise self._read_error
        return SimpleNamespace(app="Chrome", title="YouTube", items=self._items)

    def _press(self, element):
        self.pressed.append(element)
        return 0

    def event(self, state, text="", interaction=1, final=False):
        self.navigator.on_event(VoiceEvent(state, text, interaction, is_final=final))

    def speak(self, phrase, interaction=1):
        """Sequência de eventos de uma fala: várias parciais e um resultado final."""
        for partial in ("abriu", "abriu fil", phrase):
            self.event(State.LISTENING, partial, interaction)
        self.event(State.PROCESSING, phrase, interaction)
        self.event(State.RESULT, phrase, interaction, final=True)

    def process_next(self):
        phrase, reading = self.navigator.requests.get(timeout=5)
        return self.navigator.handle(phrase, reading)


class VoiceNavigatorTest(unittest.TestCase):
    def test_le_a_tela_uma_vez_por_interacao_enquanto_a_pessoa_fala(self):
        harness = Harness(typed=["s"])
        harness.speak("abrir filosofia kant")
        self.assertEqual(harness.process_next(), 0)
        self.assertEqual(harness.reads, 1)

    def test_fala_com_erro_de_reconhecimento_aciona_o_item_certo(self):
        harness = Harness(typed=["s"])
        harness.speak("Abriu. filosofia cante.")
        harness.process_next()
        self.assertEqual(harness.pressed, ["e2"])

    def test_cancelar_a_confirmacao_nao_aciona(self):
        harness = Harness(typed=["n"])
        harness.speak("abrir notícias")
        harness.process_next()
        self.assertEqual(harness.pressed, [])

    def test_frase_sem_correspondencia_nao_pergunta_nem_aciona(self):
        harness = Harness(typed=[])
        harness.speak("abrir futebol")
        self.assertEqual(harness.process_next(), 0)
        self.assertEqual(harness.pressed, [])

    def test_resultado_final_repetido_gera_uma_unica_acao(self):
        harness = Harness(typed=["s"])
        harness.speak("abrir notícias")
        harness.event(State.RESULT, "abrir notícias", final=True)
        harness.process_next()
        self.assertTrue(harness.navigator.requests.empty())

    def test_resultado_nao_final_e_ignorado(self):
        harness = Harness()
        harness.event(State.RESULT, "abrir notícias", final=False)
        self.assertTrue(harness.navigator.requests.empty())

    def test_interacoes_diferentes_leem_a_tela_de_novo(self):
        harness = Harness(typed=["s", "s"])
        harness.speak("abrir notícias", interaction=1)
        harness.process_next()
        harness.speak("abrir notícias", interaction=2)
        harness.process_next()
        self.assertEqual(harness.reads, 2)

    def test_falha_ao_ler_a_tela_avisa_e_nao_aciona(self):
        harness = Harness(read_error=RuntimeError("sem janela"))
        harness.speak("abrir notícias")
        self.assertEqual(harness.process_next(), 1)
        self.assertEqual(harness.pressed, [])
        self.assertTrue(any("sem janela" in message for message in harness.said))

    def test_tela_sem_itens(self):
        harness = Harness(items=[])
        harness.speak("abrir notícias")
        self.assertEqual(harness.process_next(), 1)

    def test_erro_do_servico_de_voz_e_mostrado(self):
        harness = Harness()
        harness.event(State.ERROR, "Nenhuma fala reconhecida.")
        self.assertIn("Nenhuma fala reconhecida.", harness.said)


if __name__ == "__main__":
    unittest.main()
