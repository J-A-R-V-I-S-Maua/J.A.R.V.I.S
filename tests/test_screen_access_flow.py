import unittest

from screen_access import flow

ITEMS = [
    ("e1", "AXRadioButton", "Notícias"),
    ("e2", "AXButton", "Mais ações"),
    ("e3", "AXButton", "Mais ações"),
    ("e4", "AXLink", "ENTENDA a FILOSOFIA de IMMANUEL KANT 20 minutos"),
    ("e5", "AXButton", flow.NO_NAME),
]


def answers(*texts):
    """Respostas digitadas, na ordem; falha se o código perguntar mais do que o esperado."""
    pending = iter(texts)
    return lambda prompt="": next(pending)


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.said = []

    def resolve(self, phrase, *typed):
        return flow.resolve(phrase, ITEMS, answers(*typed), self.said.append)

    def test_frase_unica(self):
        self.assertEqual(self.resolve("abrir filosofia kant"), 3)

    def test_frase_ambigua_com_esclarecimento(self):
        self.assertEqual(self.resolve("mais ações", "3"), 2)
        self.assertIn("Vários itens combinam com isso:", self.said)

    def test_esclarecimento_fora_das_opcoes(self):
        self.assertIsNone(self.resolve("mais ações", "1"))

    def test_esclarecimento_cancelado(self):
        self.assertIsNone(self.resolve("mais ações", ""))

    def test_sem_correspondencia(self):
        self.assertIsNone(self.resolve("futebol"))
        self.assertEqual(self.said, ["Não encontrei nenhum item que combine com isso."])

    def test_item_sem_nome_nao_casa_por_nome(self):
        self.assertIsNone(self.resolve("sem nome"))


class ConfirmAndPressTest(unittest.TestCase):
    def setUp(self):
        self.said = []
        self.pressed = []

    def run_flow(self, typed, code=0):
        def press(element):
            self.pressed.append(element)
            return code

        return flow.confirm_and_press(
            3, ITEMS, press, lambda c: f"erro {c}", answers(typed), self.said.append)

    def test_confirmado(self):
        self.assertEqual(self.run_flow("s"), 0)
        self.assertEqual(self.pressed, ["e4"])

    def test_cancelado_nao_aciona(self):
        self.assertEqual(self.run_flow("n"), 0)
        self.assertEqual(self.pressed, [])
        self.assertEqual(self.said, ["Cancelado."])

    def test_qualquer_coisa_diferente_de_s_cancela(self):
        self.assertEqual(self.run_flow("sim, claro?"), 0)
        self.assertEqual(self.pressed, [])

    def test_recusa_do_aplicativo(self):
        self.assertEqual(self.run_flow("s", code=-25206), 1)
        self.assertIn("Recusado: erro -25206 (código -25206).", self.said)


if __name__ == "__main__":
    unittest.main()
