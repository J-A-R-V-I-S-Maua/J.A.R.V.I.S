import unittest
from unittest.mock import Mock
from screen_access.backend import Element, Snapshot, Window
from screen_access.commands import Reply, action_command, classify_reply, number_name, parse_number, select_command
from screen_access.session import ActionState, ScreenSession


def snapshot():
    window = Window("window-1", 99999, "Teste", (0, 0, 800, 600))
    elements = (
        Element(1, "Pesquisar", "ButtonControl", (10, 10, 100, 30), "invoke"),
        Element(2, "Mais ações", "ButtonControl", (10, 50, 100, 30), "invoke"),
        Element(3, "Mais ações", "ButtonControl", (10, 90, 100, 30), "invoke"),
        Element(4, "", "EditControl", (10, 130, 100, 30), "focus"),
    )
    return Snapshot("snapshot-1", window, elements)


class NumbersTest(unittest.TestCase):
    def test_reply_classification_is_safe_and_asymmetric(self):
        self.assertEqual(classify_reply("confirmar"), Reply.CONFIRM)
        self.assertEqual(classify_reply("sim por favor"), Reply.CONFIRM)
        self.assertEqual(classify_reply("certo"), Reply.CONFIRM)
        self.assertEqual(classify_reply("não"), Reply.CANCEL)
        self.assertEqual(classify_reply("cancelar"), Reply.CANCEL)
        self.assertEqual(classify_reply("sim, mas cancelar"), Reply.CANCEL)
        self.assertIsNone(classify_reply("talvez"))
        self.assertIsNone(classify_reply("confirmar talvez"))
        self.assertIsNone(classify_reply("confirmar agora por favor mesmo"))
        self.assertIsNone(classify_reply("uma frase longa sobre confirmar uma ação"))

    def test_special_click_commands_and_numbers(self):
        for phrase in ("clique duas vezes na Lixeira", "duplo clique na Lixeira", "clique duplo em Lixeira"):
            self.assertEqual(action_command(phrase), ("open_item", "lixeira"))
        for phrase in ("abrir menu de contexto da Lixeira", "abra o menu de contexto de Lixeira",
                       "clique com o botão direito na Lixeira", "clique direito em Lixeira"):
            self.assertEqual(action_command(phrase), ("context_menu", "lixeira"))
        self.assertEqual(parse_number("duplo clique no número sete"), 7)
        self.assertEqual(parse_number("clique com o botão direito no número sete"), 7)

    def test_all_numbers_round_trip(self):
        for number in range(1, 201):
            with self.subTest(number=number):
                self.assertEqual(parse_number(number_name(number)), number)
                self.assertEqual(parse_number(f"clique no número {number_name(number)}"), number)

    def test_invalid_and_named_targets(self):
        for text in ("zero", "201", "duzentos e um", "sete oito", "vinte quatro", "pesquisar", "clique em pesquisar"):
            self.assertIsNone(parse_number(text), text)
        self.assertEqual(parse_number("número sete"), 7)
        self.assertEqual(parse_number("duas"), 2)


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.executor = Mock()
        self.views = []
        self.session = ScreenSession(self.executor, self.views.append)
        self.snapshot = snapshot()
        self.session.receive(0, "foreground", self.snapshot.window)
        self.session.begin_capture()

    def read(self, phrase):
        self.assertTrue(self.session.handle(phrase))
        self.session.receive(self.session.token, "scan", self.snapshot)

    def test_name_requires_confirmation_then_single_action(self):
        self.read("clique em pesquisar")
        self.assertEqual(self.session.state, ActionState.CONFIRMING)
        self.assertTrue(self.views[-1].capture)
        self.assertEqual(self.views[-1].markers, (self.snapshot.elements[0],))
        self.session.handle("confirmar")
        self.assertEqual(self.session.state, ActionState.EXECUTING)
        self.assertEqual(self.executor.submit.call_args.args[1:], ("activate", self.snapshot, 1))
        self.session.handle("confirmar")
        self.assertEqual(self.executor.submit.call_count, 2)  # scan e uma única ação.

    def test_ambiguity_preserves_numbers_and_requires_confirmation(self):
        self.read("clique em mais ações")
        self.assertEqual(self.session.candidates, (2, 3))
        self.assertEqual([item.number for item in self.views[-1].markers], [2, 3])
        self.session.handle("três")
        self.assertEqual(self.session.selected, 3)
        self.assertEqual(self.session.state, ActionState.CONFIRMING)

    def test_show_numbers_and_unnamed_element(self):
        self.read("mostrar números")
        self.session.handle("número quatro")
        self.assertEqual(self.session.selected, 4)
        self.assertIn("sem nome", self.views[-1].text)

    def test_invalid_reply_cancels_without_action(self):
        self.read("clique em pesquisar")
        self.session.handle("talvez")
        self.assertTrue(self.session.active)
        self.assertTrue(self.views[-1].capture)
        self.session.handle("talvez")
        self.assertFalse(self.session.active)
        self.assertEqual(self.executor.submit.call_count, 1)
        self.assertEqual(self.views[-1].markers, ())

    def test_cancel_hide_and_priority(self):
        for phrase in ("cancelar", "não", "parar", "esconder números"):
            self.session.begin_capture()
            self.read("clique em pesquisar")
            self.session.handle(phrase)
            self.assertFalse(self.session.active)

    def test_mixed_confirmation_and_fuzzy_cancel_are_not_confirmed(self):
        self.read("clique em pesquisar")
        self.session.handle("sim cancelar")
        self.assertFalse(self.session.active)
        self.assertEqual(self.executor.submit.call_count, 1)

        self.session.begin_capture()
        self.read("clique em pesquisar")
        self.session.handle("confermar")
        self.assertEqual(self.session.state, ActionState.EXECUTING)

    def test_late_reply_after_cancel_is_ignored(self):
        self.session.handle("mostrar números")
        old_token = self.session.token
        self.session.cancel()
        count = len(self.views)
        self.session.receive(old_token, "scan", self.snapshot)
        self.assertEqual(len(self.views), count)
        self.assertIsNone(self.session.snapshot)

    def test_window_change_invalidates_session(self):
        self.read("clique em pesquisar")
        self.session.receive(0, "foreground", Window("other", 99998, "Outro", (0, 0, 800, 600)))
        self.assertFalse(self.session.active)
        self.assertIsNone(self.session.target)

    def test_title_change_in_same_window_does_not_cancel(self):
        self.read("clique em pesquisar")
        window = self.snapshot.window
        self.session.receive(0, "foreground", Window(window.identity, window.pid, "Novo título", window.frame))
        self.assertEqual(self.session.state, ActionState.CONFIRMING)

    def test_non_commands_do_not_invoke_screen_access(self):
        for phrase in ("abrir navegador", "feche o bloco de notas", "Pesquisar", "sim"):
            self.assertFalse(self.session.handle(phrase))
        self.executor.submit.assert_not_called()

    def test_special_actions_confirm_dispatch_once_and_cancel(self):
        for phrase, operation, label in (("duplo clique no número quatro", "open_item", "duplo clique"),
                                         ("abrir menu de contexto de pesquisar", "context_menu", "menu de contexto")):
            self.session.begin_capture()
            self.read(phrase)
            self.assertIn(label, self.views[-1].text)
            selected = self.session.selected
            self.session.handle("sim")
            self.assertEqual(self.executor.submit.call_args.args[1:], (operation, self.snapshot, selected))
            count = self.executor.submit.call_count
            self.session.handle("sim")
            self.assertEqual(self.executor.submit.call_count, count)
            self.session.receive(self.session.token, operation, None)
            self.assertFalse(self.session.active)
        self.session.begin_capture()
        self.read("clique com o botão direito em pesquisar")
        count = self.executor.submit.call_count
        self.session.handle("cancelar")
        self.assertEqual(self.executor.submit.call_count, count)

    def test_special_action_preserved_through_ambiguity(self):
        self.read("abrir menu de contexto de mais ações")
        self.assertEqual(self.session.state, ActionState.CHOOSING)
        self.session.handle("três")
        self.session.handle("confirmar")
        self.assertEqual(self.executor.submit.call_args.args[1:], ("context_menu", self.snapshot, 3))

    def test_special_action_after_show_numbers(self):
        self.read("mostrar números")
        self.session.handle("duplo clique no número quatro")
        self.session.handle("confirmar")
        self.assertEqual(self.executor.submit.call_args.args[1:], ("open_item", self.snapshot, 4))

    def test_unavailable_backend_keeps_non_commands_usable(self):
        self.session.receive(0, "unavailable", "Sem permissão")
        self.assertTrue(self.session.handle("mostrar números"))
        self.assertEqual(self.views[-1].text, "Sem permissão")
        self.assertFalse(self.session.handle("texto livre"))

    def test_inline_dictation_preserves_text_and_executes_once(self):
        self.session.handle("digitar Olá, São Paulo! {Enter}")
        self.assertEqual(self.executor.submit.call_args.args[1:], ("prepare_dictation", self.snapshot.window))
        self.session.receive(self.session.token, "prepare_dictation", self.snapshot)
        self.assertEqual(self.session.state, ActionState.CONFIRMING)
        self.session.handle("sim")
        self.assertEqual(self.executor.submit.call_args.args[1:],
                         ("dictate", self.snapshot, "Olá, São Paulo! {Enter}"))
        self.session.handle("sim")
        self.assertEqual(self.executor.submit.call_count, 2)

    def test_dictation_captures_text_then_confirmation_and_cancel(self):
        self.session.handle("Digitar.")
        self.session.receive(self.session.token, "prepare_dictation", self.snapshot)
        self.assertEqual(self.session.state, ActionState.DICTATING)
        self.assertTrue(self.views[-1].capture)
        self.session.handle("Feche o navegador, João.")
        self.assertEqual(self.session.dictation_text, "Feche o navegador, João.")
        self.assertEqual(self.session.state, ActionState.CONFIRMING)
        self.assertTrue(self.views[-1].capture)
        self.session.handle("cancelar")
        self.assertFalse(self.session.active)
        self.assertIsNone(self.session.dictation_text)
        self.assertEqual(self.executor.submit.call_count, 1)

    def test_dictation_late_result_and_field_error_do_not_type(self):
        self.session.handle("digitar teste")
        old_token = self.session.token
        self.session.cancel()
        self.session.receive(old_token, "prepare_dictation", self.snapshot)
        self.assertFalse(self.session.active)
        self.session.begin_capture()
        self.session.handle("digitar teste")
        self.session.receive(self.session.token, "error", "Nenhum campo ativo")
        self.assertFalse(self.session.active)
        self.assertEqual(self.views[-1].text, "Nenhum campo ativo")

    def test_enter_and_delete_resolve_active_field_then_confirm_once(self):
        for phrase, action in (("Enter", "press_enter"), ("Deletar", "clear_text")):
            self.session.begin_capture()
            self.assertTrue(self.session.handle(phrase))
            self.assertEqual(self.executor.submit.call_args.args[1:], ("prepare_dictation", self.snapshot.window))
            self.session.receive(self.session.token, "prepare_dictation", self.snapshot)
            self.assertEqual(self.session.state, ActionState.CONFIRMING)
            self.assertTrue(self.views[-1].capture)
            self.session.handle("confirmar")
            self.assertEqual(self.executor.submit.call_args.args[1:], (action, self.snapshot, 1))
            count = self.executor.submit.call_count
            self.session.handle("confirmar")
            self.assertEqual(self.executor.submit.call_count, count)
            self.session.receive(self.session.token, action, None)
            self.assertFalse(self.session.active)

    def test_delete_accepts_the_natural_ways_of_saying_it(self):
        # Formas que o Whisper entrega com o prompt de comandos, com pontuação e maiúsculas.
        for phrase in ("Apaga tudo!", "Apagar tudo.", "Limpa o campo.", "Limpar campo", "Deleta", "Deletar tudo"):
            with self.subTest(phrase=phrase):
                self.session.cancel()
                self.session.begin_capture()
                self.assertTrue(self.session.handle(phrase))
                self.assertEqual(self.executor.submit.call_args.args[1:], ("prepare_dictation", self.snapshot.window))
                self.assertEqual(self.session.pending_action, "clear_text")

    def test_delete_cancel_invalid_confirmation_and_late_reply(self):
        for response in ("cancelar", "talvez"):
            self.session.begin_capture()
            self.session.handle("deletar")
            self.session.receive(self.session.token, "prepare_dictation", self.snapshot)
            count = self.executor.submit.call_count
            token = self.session.token
            self.session.handle(response)
            if response == "talvez":
                self.assertTrue(self.session.active)
                self.session.handle(response)
            self.assertFalse(self.session.active)
            self.assertEqual(self.executor.submit.call_count, count)
            self.session.receive(token, "prepare_dictation", self.snapshot)
            self.assertFalse(self.session.active)

    def test_enter_and_delete_are_literal_during_dictation(self):
        self.session.handle("digitar")
        self.session.receive(self.session.token, "prepare_dictation", self.snapshot)
        self.session.handle("Enter e deletar")
        self.session.handle("sim")
        self.assertEqual(self.executor.submit.call_args.args[1:], ("dictate", self.snapshot, "Enter e deletar"))
        self.session.cancel()
        self.assertFalse(self.session.handle("deletar arquivo"))

    def test_cancel_words_remain_literal_text_during_dictation(self):
        self.session.handle("digitar")
        self.session.receive(self.session.token, "prepare_dictation", self.snapshot)
        self.session.handle("não obrigado")
        self.session.handle("confirmar")
        self.assertEqual(self.executor.submit.call_args.args[1:], ("dictate", self.snapshot, "não obrigado"))

    def test_ambiguous_invalid_number_and_no_match(self):
        self.read("clique em mais ações")
        self.session.handle("um")
        self.assertTrue(self.session.active)
        self.session.handle("um")
        self.assertFalse(self.session.active)
        self.session.begin_capture()
        self.read("clique em futebol")
        self.assertFalse(self.session.active)


class MissionControlSessionTest(unittest.TestCase):
    def setUp(self):
        self.executor = Mock()
        self.views = []
        self.session = ScreenSession(self.executor, self.views.append)
        self.session.receive(0, "foreground", snapshot().window)
        self.session.begin_capture()

    MISSION_CONTROL = Window("missioncontrol:631", 631, "Mission Control", (0, 0, 1470, 956))

    def windows(self):
        return Snapshot("mc-1", self.MISSION_CONTROL, (
            Element(1, "README.md — J.A.R.V.I.S", "AXButton", (100, 100, 400, 300), "press"),
            Element(2, "Feed | LinkedIn", "AXButton", (600, 100, 400, 300), "press"),
            Element(3, "Mesa", "AXButton", (100, 20, 80, 50), "press"),
        ))

    def show_windows(self, phrase="mostrar janelas"):
        self.assertTrue(self.session.handle(phrase))
        self.assertEqual(self.executor.submit.call_args.args[1:], ("mission_control", True))
        self.assertEqual(self.views[-1].text, "Mostrando as janelas…")
        self.session.receive(0, "foreground", self.MISSION_CONTROL)
        self.session.receive(self.session.token, "mission_control", self.MISSION_CONTROL)
        self.assertEqual(self.executor.submit.call_args.args[1:],
                         ("scan", self.MISSION_CONTROL, 32, 3000, False))
        self.session.receive(self.session.token, "scan", self.windows())

    def test_mostrar_janelas_ja_mostra_os_numeros_e_fica_ouvindo(self):
        for phrase in ("mostrar janelas", "Mostre as janelas!", "ver as janelas"):
            with self.subTest(phrase=phrase):
                self.show_windows(phrase)
                self.assertEqual(self.session.state, ActionState.CHOOSING)
                view = self.views[-1]
                self.assertEqual(view.text, "Diga o número da janela ou cancelar.")
                self.assertEqual([element.number for element in view.markers], [1, 2, 3])
                self.assertTrue(view.capture)  # ouve o número sem precisar de "Hey Jarvis"
                self.session.cancel()

    def test_o_numero_leva_direto_a_janela_sem_confirmar(self):
        self.show_windows()
        self.session.handle("dois")
        self.assertEqual(self.session.state, ActionState.EXECUTING)
        self.assertEqual(self.views[-1].text, "Abrindo [2] Feed | LinkedIn…")
        self.assertEqual(self.executor.submit.call_args.args[1:], ("activate", self.windows(), 2))
        self.session.receive(self.session.token, "activate", None)
        self.assertFalse(self.session.active)
        self.assertEqual(self.views[-1].text, "Janela escolhida.")
        self.assertFalse(self.session.choosing_window)

    def test_numero_fora_da_lista_pede_de_novo(self):
        self.show_windows()
        self.session.handle("nove")
        self.assertEqual(self.session.state, ActionState.CHOOSING)
        self.assertTrue(self.views[-1].capture)

    def test_cancelar_esconde_os_numeros_das_janelas(self):
        self.show_windows()
        self.session.handle("cancelar")
        self.assertFalse(self.session.active)
        self.assertFalse(self.session.choosing_window)

    def test_controles_comuns_continuam_pedindo_confirmacao(self):
        self.show_windows()
        self.session.cancel()
        self.session.receive(0, "foreground", snapshot().window)
        self.session.begin_capture()
        self.session.handle("mostrar números")
        self.session.receive(self.session.token, "scan", snapshot())
        self.session.handle("um")
        self.assertEqual(self.session.state, ActionState.CONFIRMING)

    def test_fechar_janelas(self):
        self.session.handle("fechar janelas")
        self.assertEqual(self.executor.submit.call_args.args[1:], ("mission_control", False))
        self.session.receive(self.session.token, "mission_control", False)
        self.assertEqual(self.views[-1].text, "Janelas fechadas.")

    def test_numeros_pendentes_dao_lugar_as_janelas(self):
        self.session.handle("mostrar números")
        self.session.receive(self.session.token, "scan", snapshot())
        self.assertEqual(self.session.state, ActionState.CHOOSING)
        self.session.handle("mostrar janelas")
        self.assertEqual(self.session.state, ActionState.EXECUTING)
        self.assertEqual(self.executor.submit.call_args.args[1:], ("mission_control", True))

    def test_abrir_as_janelas_nao_e_tomado_por_troca_de_janela(self):
        self.show_windows()
        self.session.receive(0, "foreground", self.MISSION_CONTROL)
        self.assertEqual(self.session.state, ActionState.CHOOSING)
        self.assertEqual(self.session.target, self.MISSION_CONTROL)

    def test_durante_o_ditado_e_texto_e_durante_execucao_espera(self):
        self.session.state = ActionState.DICTATING
        self.session.snapshot = snapshot()
        self.session.handle("mostrar janelas")
        self.assertEqual(self.session.dictation_text, "mostrar janelas")
        self.session.cancel()
        self.session.state = ActionState.EXECUTING
        self.executor.submit.reset_mock()
        self.session.handle("mostrar janelas")
        self.executor.submit.assert_not_called()

    def test_erro_do_sistema_aparece_na_barra(self):
        self.session.handle("mostrar janelas")
        self.session.receive(self.session.token, "error", "O Mission Control não abriu. Tente de novo.")
        self.assertFalse(self.session.active)
        self.assertEqual(self.views[-1].text, "O Mission Control não abriu. Tente de novo.")



class SelectTextSessionTest(unittest.TestCase):
    def setUp(self):
        self.executor = Mock()
        self.views = []
        self.session = ScreenSession(self.executor, self.views.append)
        self.snapshot = snapshot()
        self.session.receive(0, "foreground", self.snapshot.window)
        self.session.begin_capture()

    def test_formas_explicitas_e_clique_por_nome_preservado(self):
        self.assertEqual(select_command("Selecionar tudo."), ("all", None))
        self.assertEqual(select_command("Selecione a última palavra"), ("last_word", None))
        self.assertEqual(select_command("Selecionar a palavra o lá."), ("text", "o la"))
        self.assertEqual(select_command("selecionar o texto bom dia"), ("text", "bom dia"))
        self.assertIsNone(select_command("selecionar pesquisar"))
        self.assertEqual(action_command("selecionar pesquisar"), ("activate", "pesquisar"))

    def test_seleciona_sem_pedir_confirmacao(self):
        self.assertTrue(self.session.handle("Selecionar a palavra olá"))
        self.assertEqual(self.executor.submit.call_args.args[1:], ("prepare_dictation", self.snapshot.window))
        self.session.receive(self.session.token, "prepare_dictation", self.snapshot)
        self.assertEqual(self.executor.submit.call_args.args[1:], ("select_text", self.snapshot, ("text", "ola")))
        self.assertEqual(self.session.state, ActionState.EXECUTING)
        self.session.receive(self.session.token, "select_text", "Selecionado: “olá”.")
        self.assertFalse(self.session.active)
        self.assertEqual(self.views[-1].text, "Selecionado: “olá”.")

    def test_selecionar_tudo_nao_vira_clique_no_controle_tudo(self):
        self.session.handle("selecionar tudo")
        self.assertEqual(self.executor.submit.call_args.args[1], "prepare_dictation")
        self.assertEqual(self.session.pending_action, "select_text")

    def test_erro_aparece_e_encerra(self):
        self.session.handle("selecionar o texto xyz")
        self.session.receive(self.session.token, "prepare_dictation", self.snapshot)
        self.session.receive(self.session.token, "error", "Não encontrei “xyz” no campo.")
        self.assertFalse(self.session.active)
        self.assertIsNone(self.session.selection)
        self.assertEqual(self.views[-1].text, "Não encontrei “xyz” no campo.")



class ClipboardSessionTest(unittest.TestCase):
    setUp = SelectTextSessionTest.setUp

    def found_field(self, phrase, prepare="prepare_dictation"):
        self.assertTrue(self.session.handle(phrase))
        self.assertEqual(self.executor.submit.call_args.args[1:], (prepare, self.snapshot.window))

    def test_frases_com_texto_e_palavras_soltas(self):
        for phrase, action in (("Copiar texto.", "copy_text"), ("copiar", "copy_text"), ("Recortar texto", "cut_text"),
                               ("Colar texto.", "paste_text"), ("cole aqui", "paste_text")):
            with self.subTest(phrase=phrase):
                self.session.cancel()
                self.session.begin_capture()
                self.session.handle(phrase)
                self.assertEqual(self.session.pending_action, action)

    def test_copiar_vai_direto(self):
        self.found_field("copiar texto")
        self.session.receive(self.session.token, "prepare_dictation", self.snapshot)
        self.assertEqual(self.executor.submit.call_args.args[1:], ("copy_text", self.snapshot, 1))
        self.session.receive(self.session.token, "copy_text", "Copiado: “olá”.")
        self.assertEqual(self.views[-1].text, "Copiado: “olá”.")
        self.assertFalse(self.session.active)

    def test_recortar_pede_confirmacao(self):
        self.found_field("recortar texto")
        self.session.receive(self.session.token, "prepare_dictation", self.snapshot)
        self.assertEqual(self.session.state, ActionState.CONFIRMING)
        self.assertIn("Recortar o texto selecionado", self.views[-1].text)
        count = self.executor.submit.call_count
        self.session.handle("cancelar")
        self.assertEqual(self.executor.submit.call_count, count)

    def test_colar_mostra_o_que_vai_colar_e_espera_confirmar(self):
        self.found_field("colar texto", prepare="prepare_paste")
        self.session.receive(self.session.token, "prepare_paste", (self.snapshot, "bom dia"))
        self.assertEqual(self.session.state, ActionState.CONFIRMING)
        self.assertTrue(self.views[-1].text.startswith("Colar “bom dia” em"))
        self.session.handle("confirmar")
        self.assertEqual(self.executor.submit.call_args.args[1:], ("paste_text", self.snapshot, 1))
        self.assertEqual(self.views[-1].text, "Colando…")
        self.session.receive(self.session.token, "paste_text", "Texto colado no campo.")
        self.assertFalse(self.session.active)



class SoundAlikeFieldCommandTest(unittest.TestCase):
    setUp = SelectTextSessionTest.setUp

    def test_frase_que_soa_como_comando_de_campo(self):
        # Trocas reais do Whisper com as vozes do macOS.
        from screen_access.commands import field_command

        for phrase, action in (("Polar texto.", "paste_text"), ("Calar aqui.", "paste_text"),
                               ("Deletor.", "clear_text"), ("Impar campo.", "clear_text")):
            self.assertEqual(field_command(phrase), action, phrase)
        for phrase in ("Blood.", "Comandos.", "Ode.", "previsão do tempo", "clicar", "Conte-se."):
            self.assertIsNone(field_command(phrase), phrase)

    def test_vira_o_comando_mas_ainda_pede_confirmacao(self):
        self.assertTrue(self.session.handle("Polar texto."))
        self.assertEqual(self.executor.submit.call_args.args[1:], ("prepare_paste", self.snapshot.window))
        self.session.receive(self.session.token, "prepare_paste", (self.snapshot, "bom dia"))
        self.assertEqual(self.session.state, ActionState.CONFIRMING)

    def test_so_como_ultimo_recurso(self):
        self.assertFalse(self.session.handle("Blood."))
        self.session.handle("clique em polar")  # o clique por nome continua sendo clique
        self.assertEqual(self.executor.submit.call_args.args[1], "scan")
        self.session.cancel()
        self.session.begin_capture()
        self.session.handle("digitar polar texto")  # e o ditado continua literal
        self.assertEqual(self.session.dictation_text, "polar texto")

if __name__ == "__main__":
    unittest.main()
