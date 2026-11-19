import unittest

from screen_access.matching import choose, find_in_text, last_word

# Nomes reais de itens da página inicial do YouTube; "" é um item sem nome.
NAMES = [
    "Tudo",
    "Notícias",
    "Pesquisar",
    "Mais ações",
    "A ECONOMIA DE JAIR BOLSONARO | O que foi feito em 4 anos de governo 25 minutos",
    "Breno Perrucho - Jovens de Negócios",
    "Acessar o canal Breno Perrucho - Jovens de Negócios",
    "Mais ações",
    "",
    "ENTENDA a FILOSOFIA de IMMANUEL KANT 20 minutos",
]


class ChooseTest(unittest.TestCase):
    def test_numero_explicito(self):
        for phrase in ("3", "número 3", "clique no item 3"):
            with self.subTest(phrase=phrase):
                self.assertEqual(choose(phrase, NAMES).index, 2)

    def test_numero_fora_da_lista(self):
        self.assertEqual(choose("número 99", NAMES).status, "none")
        self.assertEqual(choose("0", NAMES).status, "none")

    def test_sem_numeros_a_frase_com_numero_nao_escolhe_nada(self):
        # Listas que a pessoa não está vendo (ex.: aplicativos instalados): "abrir 3" não vale.
        self.assertEqual(choose("3", NAMES, numbers=False).status, "none")
        self.assertEqual(choose("abrir número 3", NAMES, numbers=False).status, "none")
        self.assertEqual(choose("notícias", NAMES, numbers=False).index, 1)  # o nome continua valendo

    def test_nome_unico_ignora_acento_e_caixa(self):
        self.assertEqual(choose("NOTICIAS", NAMES).index, 1)
        self.assertEqual(choose("clique em notícias", NAMES).index, 1)

    def test_frase_com_palavras_de_comando(self):
        choice = choose("abrir o vídeo da economia de bolsonaro", NAMES)
        self.assertEqual((choice.status, choice.index), ("match", 4))

    def test_aceita_palavra_parcial(self):
        self.assertEqual(choose("filosofia kant", NAMES).index, 9)
        self.assertEqual(choose("noticia", NAMES).index, 1)

    def test_nomes_repetidos_pedem_esclarecimento(self):
        choice = choose("mais ações", NAMES)
        self.assertEqual(choice.status, "ambiguous")
        self.assertEqual(choice.candidates, (3, 7))

    def test_canal_e_video_do_mesmo_nome_pedem_esclarecimento(self):
        choice = choose("breno perrucho", NAMES)
        self.assertEqual(choice.status, "ambiguous")
        self.assertEqual(choice.candidates, (5, 6))

    def test_palavra_a_mais_desfaz_a_ambiguidade(self):
        self.assertEqual(choose("canal breno perrucho", NAMES).index, 6)

    def test_sem_correspondencia(self):
        self.assertEqual(choose("futebol", NAMES).status, "none")

    def test_so_palavras_de_comando(self):
        self.assertEqual(choose("clique no", NAMES).status, "none")
        self.assertEqual(choose("", NAMES).status, "none")

    def test_item_sem_nome_nunca_casa_por_nome(self):
        self.assertEqual(choose("sem nome", NAMES).status, "none")

    def test_verbo_distorcido_pelo_reconhecimento_e_ignorado(self):
        # O Whisper já entregou "abriu", "abril" e "abrem" no lugar de "abrir".
        for verb in ("abrir", "abriu", "abril", "abrem", "abre"):
            with self.subTest(verb=verb):
                self.assertEqual(choose(f"{verb} filosofia kant", NAMES).index, 9)

    def test_nome_proprio_mal_transcrito(self):
        # O Whisper já entregou "can", "cant", "cante" e "cantem" no lugar de "Kant".
        for heard in ("cant", "cante", "cantem.", "can"):
            with self.subTest(heard=heard):
                self.assertEqual(choose(f"abrir filosofia {heard}", NAMES).index, 9)

    def test_fala_cortada_nao_escolhe_nada_por_engano(self):
        self.assertEqual(choose("Abriu. Fio.", NAMES).status, "none")

    def test_palavra_errada_nao_e_perdoada(self):
        self.assertEqual(choose("abrir filosofia futebol", NAMES).status, "none")

    def test_som_de_c_e_k(self):
        from screen_access.matching import sound_key

        self.assertEqual(sound_key("cant"), sound_key("kant"))
        self.assertEqual(sound_key("quero"), sound_key("kero"))
        self.assertNotEqual(sound_key("cena"), sound_key("kena"))



class FindInTextTest(unittest.TestCase):
    TEXT = "Olá Maria, olá de novo. Bom dia!"

    def span(self, phrase, cursor=None):
        found = find_in_text(self.TEXT, phrase, cursor)
        return (self.TEXT[found[0]:found[1]], found[2]) if found else None

    def test_ignora_acento_e_caixa_e_conta_as_ocorrencias(self):
        self.assertEqual(self.span("ola"), ("Olá", 2))
        self.assertEqual(self.span("BOM DIA"), ("Bom dia", 1))

    def test_fica_com_a_ocorrencia_mais_perto_antes_do_cursor(self):
        self.assertEqual(find_in_text(self.TEXT, "ola", cursor=20)[:2], (11, 14))
        self.assertEqual(find_in_text(self.TEXT, "ola", cursor=0)[:2], (0, 3))  # nenhuma antes: a primeira

    def test_palavra_que_a_transcricao_separou_ou_trocou(self):
        self.assertEqual(self.span("o la"), ("Olá", 2))
        self.assertEqual(self.span("mariah"), ("Maria", 1))
        self.assertIsNone(self.span("xyz"))

    def test_ultima_palavra(self):
        self.assertEqual(last_word(self.TEXT), (28, 31))
        self.assertEqual(last_word(self.TEXT, cursor=9), (4, 9))
        self.assertIsNone(last_word("...", cursor=3))


if __name__ == "__main__":
    unittest.main()
