from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication

from speakbar.applications import Application
from speakbar.launcher import LauncherConversation

APP = QApplication.instance() or QApplication([])


def app(name):
    return Application(name, Path(f"/Applications/{name}.app"))


class FakeCatalog:
    def __init__(self, apps):
        self.apps = apps
        self.invalidated = 0

    def applications(self):
        return self.apps

    def wait(self, timeout=None):
        return True

    def invalidate(self):
        self.invalidated += 1


class Base(unittest.TestCase):
    APPS = [app("Safari"), app("Google Chrome"), app("Image Capture"), app("Screenshot"), app("Calculator")]
    CONFIRM = True

    def setUp(self):
        self.catalog = FakeCatalog(list(self.APPS))
        self.opened = []
        self.fail_with = None

        def launcher(path):
            if self.fail_with:
                raise self.fail_with
            self.opened.append(Path(path).stem)

        self.conversation = LauncherConversation(Mock(), self.catalog, launcher=launcher, confirm=self.CONFIRM)
        self.messages, self.captures, self.cancellations = [], [], []
        self.conversation.changed.connect(self.messages.append)
        self.conversation.capture_requested.connect(lambda: self.captures.append(1))
        self.conversation.cancelled.connect(lambda: self.cancellations.append(1))

    def tearDown(self):
        self.conversation.timer.stop()

    def wait_for_message(self, fragment):
        # A mensagem final sai de uma thread e é enfileirada para a principal; é preciso rodar os eventos.
        for _ in range(200):
            APP.processEvents()
            if any(fragment in message for message in self.messages):
                return
            time.sleep(0.01)
        self.fail(f"mensagem com {fragment!r} não apareceu: {self.messages}")

    def say(self, *phrases):
        for phrase in phrases:
            self.conversation.handle(phrase)


class ConfirmationTest(Base):
    """Padrão: como no controle da tela, nada é aberto sem a pessoa confirmar."""

    def test_um_unico_aplicativo_pergunta_antes_de_abrir(self):
        self.assertTrue(self.conversation.handle("abrir Safari"))
        self.assertEqual(self.messages, ["Abrir Safari? Diga confirmar ou cancelar."])
        self.assertTrue(self.conversation.active)
        self.assertEqual(len(self.captures), 1)  # a barra volta a ouvir a resposta
        time.sleep(0.05)
        self.assertEqual(self.opened, [])  # ainda não abriu

    def test_confirmar_abre(self):
        self.say("abrir Safari", "confirmar")
        self.wait_for_message("Abertura de Safari solicitada")
        self.assertEqual(self.opened, ["Safari"])
        self.assertFalse(self.conversation.active)

    def test_sim_tambem_confirma(self):
        self.say("abrir Safari", "sim")
        self.wait_for_message("Abertura de Safari solicitada")

    def test_cancelar_a_confirmacao_nao_abre(self):
        self.say("abrir Safari")
        self.assertTrue(self.conversation.handle("cancelar"))
        self.assertFalse(self.conversation.active)
        self.assertIn("Abertura cancelada.", self.messages)
        self.assertEqual(len(self.cancellations), 1)
        time.sleep(0.05)
        self.assertEqual(self.opened, [])

    def test_nao_tambem_cancela(self):
        self.say("abrir Safari", "não")
        self.assertIn("Abertura cancelada.", self.messages)
        self.assertEqual(self.opened, [])

    def test_resposta_que_nao_e_confirmacao_nao_abre_e_pergunta_de_novo(self):
        self.say("abrir Safari", "talvez")
        self.assertIn("Diga confirmar ou cancelar", self.messages[-1])
        self.assertTrue(self.conversation.active)
        self.assertEqual(len(self.captures), 2)
        time.sleep(0.05)
        self.assertEqual(self.opened, [])

    def test_duas_respostas_invalidas_encerram_sem_abrir(self):
        self.say("abrir Safari", "talvez", "hum")
        self.assertFalse(self.conversation.active)
        self.assertIn("Não consegui concluir", self.messages[-1])
        time.sleep(0.05)
        self.assertEqual(self.opened, [])

    def test_a_segunda_tentativa_ainda_pode_confirmar(self):
        self.say("abrir Safari", "talvez", "confirmar")
        self.wait_for_message("Abertura de Safari solicitada")

    def test_tempo_esgotado_na_confirmacao_nao_abre(self):
        self.say("abrir Safari")
        self.conversation.deadline = time.monotonic() - 1
        self.conversation.handle("confirmar")
        self.assertFalse(self.conversation.active)
        self.assertIn("Tempo esgotado", self.messages[-1])
        time.sleep(0.05)
        self.assertEqual(self.opened, [])

    def test_aviso_de_tempo_esgotado_fala_de_aplicativo(self):
        self.say("abrir Safari")
        self.conversation._expired()
        self.assertIn("abrir o aplicativo", self.messages[-1])

    def test_escolher_na_lista_tambem_pede_confirmacao(self):
        self.say("abrir captura")
        self.assertIn("Encontrei 2 aplicativos", self.messages[-1])
        self.say("dois")
        self.assertEqual(self.messages[-1], "Abrir Screenshot? Diga confirmar ou cancelar.")
        time.sleep(0.05)
        self.assertEqual(self.opened, [])
        self.say("confirmar")
        self.wait_for_message("Abertura de Screenshot solicitada")
        self.assertEqual(self.opened, ["Screenshot"])

    def test_a_confirmacao_depois_da_lista_tem_a_sua_propria_nova_tentativa(self):
        self.say("abrir futebol", "safari")  # a 1ª tentativa falhou: já gastou a nova chance da busca
        self.assertEqual(self.messages[-1], "Abrir Safari? Diga confirmar ou cancelar.")
        self.say("talvez")  # ainda dá uma nova tentativa à confirmação
        self.assertTrue(self.conversation.active)
        self.say("confirmar")
        self.wait_for_message("Abertura de Safari solicitada")

    def test_falha_do_sistema_ao_abrir_e_informada(self):
        self.fail_with = OSError("O macOS recusou a abertura.")
        self.say("abrir safari", "confirmar")
        self.wait_for_message("Não consegui abrir Safari")
        self.assertIn("recusou", self.messages[-1])


class SearchFlowTest(Base):
    def test_verbo_mal_transcrito_tambem_pergunta(self):
        self.say("abril calculadora")
        self.assertEqual(self.messages[-1], "Abrir Calculator? Diga confirmar ou cancelar.")

    def test_varios_parecidos_listam_e_escutam_a_resposta(self):
        self.assertTrue(self.conversation.handle("abrir captura"))
        self.assertTrue(self.conversation.active)
        self.assertEqual(len(self.captures), 1)
        listing = self.messages[-1]
        self.assertIn("Encontrei 2 aplicativos", listing)
        self.assertIn("1 Image Capture", listing)
        self.assertIn("2 Screenshot", listing)
        self.assertEqual(self.opened, [])

    def test_resposta_por_parte_do_nome(self):
        self.say("abrir captura", "screenshot")
        self.assertEqual(self.messages[-1], "Abrir Screenshot? Diga confirmar ou cancelar.")

    def test_resposta_com_outro_aplicativo_fora_das_opcoes(self):
        self.say("abrir captura", "safari")
        self.assertEqual(self.messages[-1], "Abrir Safari? Diga confirmar ou cancelar.")

    def test_cancelar_a_escolha(self):
        self.say("abrir captura")
        self.assertTrue(self.conversation.handle("cancelar"))
        self.assertFalse(self.conversation.active)
        self.assertIn("Abertura cancelada.", self.messages)
        self.assertEqual(len(self.cancellations), 1)
        self.assertEqual(self.opened, [])

    def test_numero_fora_das_opcoes_nao_abre_nada(self):
        self.say("abrir captura", "nove")
        self.assertEqual(self.opened, [])
        self.assertTrue(self.conversation.active)  # ainda dá uma nova chance

    def test_sem_correspondencia_tenta_de_novo_e_atualiza_a_lista(self):
        self.assertTrue(self.conversation.handle("abrir futebol"))
        self.assertIn("Não encontrei esse aplicativo", self.messages[-1])
        self.assertTrue(self.conversation.active)
        self.assertEqual(self.catalog.invalidated, 1)
        self.assertEqual(len(self.captures), 1)

    def test_segunda_falha_encerra(self):
        self.say("abrir futebol", "basquete")
        self.assertFalse(self.conversation.active)
        self.assertIn("Não consegui concluir", self.messages[-1])
        self.assertEqual(self.opened, [])

    def test_tempo_esgotado_durante_a_escolha(self):
        self.say("abrir captura")
        self.conversation.deadline = time.monotonic() - 1
        self.conversation.handle("um")
        self.assertFalse(self.conversation.active)
        self.assertIn("Tempo esgotado", self.messages[-1])
        self.assertEqual(self.opened, [])


class NotInterferingTest(Base):
    def test_comandos_de_tela_nao_sao_atropelados(self):
        for phrase in ("mostrar números", "abrir menu de contexto", "clique em safari", "digitar abrir safari"):
            with self.subTest(phrase=phrase):
                self.assertFalse(self.conversation.handle(phrase))
                self.assertFalse(self.conversation.active)

    def test_sem_catalogo_fora_do_macos_nao_interfere(self):
        other = LauncherConversation(Mock(), None)
        self.assertFalse(other.handle("abrir safari"))

    def test_a_conversa_de_atalho_continua_funcionando(self):
        self.assertTrue(self.conversation.handle("executar atalho"))
        self.assertTrue(self.conversation.active)
        self.assertIsNone(self.conversation.choices)
        self.assertIsNone(self.conversation.pending)
        self.assertIn("Qual atalho?", self.messages[-1])
        self.assertTrue(self.conversation.handle("cancelar"))
        self.assertIn("Atalho cancelado.", self.messages)

    def test_cancelar_a_abertura_nao_deixa_resto_para_o_proximo_atalho(self):
        self.say("abrir Safari", "cancelar")
        self.assertIsNone(self.conversation.pending)
        self.say("executar atalho")
        self.assertIsNone(self.conversation.pending)
        self.conversation.handle("meu site")  # tem que ir para o atalho, não virar "resposta de confirmação"
        for _ in range(50):
            APP.processEvents()
            if self.conversation.service.resolve.called:
                break
            time.sleep(0.01)
        self.conversation.service.resolve.assert_called_once_with("meu site")
        self.assertEqual(self.opened, [])

    def test_tempo_esgotado_de_atalho_mantem_a_mensagem_de_atalho(self):
        self.conversation.handle("executar atalho")
        self.conversation._expired()
        self.assertIn("Atalho expirado", self.messages[-1])


class WithoutConfirmationTest(Base):
    """A confirmação pode ser desligada (futuro: uma opção nas configurações)."""

    CONFIRM = False

    def test_abre_direto_quando_a_confirmacao_esta_desligada(self):
        self.assertTrue(self.conversation.handle("abrir Safari"))
        self.wait_for_message("Abertura de Safari solicitada")
        self.assertEqual(self.opened, ["Safari"])
        self.assertEqual(self.captures, [])
        self.assertFalse(self.conversation.active)

    def test_lista_continua_perguntando_qual(self):
        self.say("abrir captura", "dois")
        self.wait_for_message("Abertura de Screenshot solicitada")
        self.assertEqual(self.opened, ["Screenshot"])


if __name__ == "__main__":
    unittest.main()
