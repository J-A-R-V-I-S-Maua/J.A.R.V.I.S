import unittest

from screen_access.matching import choose

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


if __name__ == "__main__":
    unittest.main()
