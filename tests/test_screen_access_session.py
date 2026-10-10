import unittest
from unittest.mock import Mock
from screen_access.backend import Element, Snapshot, Window
from screen_access.commands import action_command, number_name, parse_number
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
        self.assertFalse(self.session.active)
        self.assertEqual(self.executor.submit.call_count, 1)
        self.assertEqual(self.views[-1].markers, ())

    def test_cancel_hide_and_priority(self):
        for phrase in ("cancelar", "não", "parar", "esconder números"):
            self.session.begin_capture()
            self.read("clique em pesquisar")
            self.session.handle(phrase)
            self.assertFalse(self.session.active)

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

    def test_delete_cancel_invalid_confirmation_and_late_reply(self):
        for response in ("cancelar", "talvez"):
            self.session.begin_capture()
            self.session.handle("deletar")
            self.session.receive(self.session.token, "prepare_dictation", self.snapshot)
            count = self.executor.submit.call_count
            token = self.session.token
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

    def test_ambiguous_invalid_number_and_no_match(self):
        self.read("clique em mais ações")
        self.session.handle("um")
        self.assertFalse(self.session.active)
        self.session.begin_capture()
        self.read("clique em futebol")
        self.assertFalse(self.session.active)
