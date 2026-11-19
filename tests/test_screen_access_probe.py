import unittest

from screen_access.probe import Row, format_row, should_list, summarize


class ShouldListTest(unittest.TestCase):
    def test_papeis_de_interesse_sao_listados(self):
        self.assertTrue(should_list("AXRow", ()))
        self.assertTrue(should_list("AXTextField", ("AXConfirm",)))

    def test_no_comum_so_entra_se_oferecer_acao_de_interesse(self):
        self.assertFalse(should_list("AXGroup", ("AXPress",)))
        self.assertTrue(should_list("AXGroup", ("AXPress", "AXShowMenu")))

    def test_ausencia_de_acoes(self):
        self.assertFalse(should_list("AXStaticText", ()))


class FormatRowTest(unittest.TestCase):
    def test_linha_completa(self):
        row = Row("AXRow", "Documentos", (180, 22), ("AXOpen", "AXShowMenu"), ("AXSelected",))
        self.assertEqual(
            format_row(3, row),
            "[3] AXRow 'Documentos'  180x22  ações: AXOpen, AXShowMenu  | graváveis: AXSelected")

    def test_sem_nome_sem_tamanho_sem_acoes(self):
        self.assertEqual(format_row(1, Row("AXGroup", "", None, (), ())), "[1] AXGroup  ações: nenhuma")

    def test_nome_longo_e_cortado(self):
        line = format_row(1, Row("AXLink", "x" * 100, None, (), ()))
        self.assertIn("x" * 60, line)
        self.assertNotIn("x" * 61, line)

    def test_notas_aparecem_no_fim(self):
        row = Row("AXScrollArea", "", (500, 400), (), (), ("barra vertical: valor 0.10, gravável",))
        self.assertTrue(format_row(2, row).endswith("| barra vertical: valor 0.10, gravável"))


class PathTest(unittest.TestCase):
    def test_caminho_aparece_no_fim_da_linha(self):
        row = Row("AXDockItem", "Safari", (60, 60), ("AXPress",), (), path="AXApplication > AXList")
        self.assertTrue(format_row(1, row).endswith("| caminho: AXApplication > AXList"))

    def test_sem_caminho_a_linha_nao_muda(self):
        self.assertNotIn("caminho", format_row(1, Row("AXRow", "a", None, (), ())))


class SummarizeTest(unittest.TestCase):
    ROWS = [
        Row("AXRow", "a", None, ("AXOpen", "AXShowMenu"), ("AXSelected",)),
        Row("AXRow", "b", None, ("AXOpen", "AXShowMenu"), ("AXSelected",)),
        Row("AXTextField", "", None, ("AXConfirm",), ("AXValue", "AXFocused")),
    ]

    def setUp(self):
        self.text = "\n".join(summarize(self.ROWS))

    def test_resume_os_papeis_encontrados(self):
        self.assertIn("Papéis encontrados: AXRow 2, AXTextField 1", self.text)

    def test_conta_quem_oferece_cada_acao(self):
        self.assertIn("AXOpen: 2  (AXRow 2)", self.text)
        self.assertIn("AXConfirm: 1  (AXTextField 1)", self.text)

    def test_conta_atributos_gravaveis_inclusive_zero(self):
        self.assertIn("AXValue: 1  (AXTextField 1)", self.text)
        self.assertIn("AXSelectedText: 0", self.text)

    def test_lista_acoes_de_interesse_ausentes(self):
        self.assertIn("nenhum controle desta tela ofereceu:", self.text)
        self.assertIn("AXIncrement", self.text.split("ofereceu:")[1])
        self.assertNotIn("AXOpen", self.text.split("ofereceu:")[1])

    def test_tela_vazia(self):
        text = "\n".join(summarize([]))
        self.assertIn("AXOpen", text.split("ofereceu:")[1])


if __name__ == "__main__":
    unittest.main()
