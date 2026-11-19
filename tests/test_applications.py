from pathlib import Path
import tempfile
import threading
import unittest

from speakbar.applications import (
    Application, ApplicationCatalog, NICKNAMES, find_application, is_open_request, is_user_application,
    open_application, scan_applications, spotlight_paths,
)

HOME = Path.home()


class IsUserApplicationTest(unittest.TestCase):
    def test_apps_comuns_valem(self):
        for path in ("/Applications/Safari.app", "/Applications/Utilities/Terminal.app",
                     "/System/Applications/Calculator.app", f"{HOME}/Applications/Foo.app",
                     f"{HOME}/Downloads/Instalado.app", f"{HOME}/Desktop/bigjpg.app"):
            with self.subTest(path=path):
                self.assertTrue(is_user_application(Path(path)))

    def test_o_finder_e_uma_excecao_dentro_do_sistema(self):
        self.assertTrue(is_user_application(Path("/System/Library/CoreServices/Finder.app")))
        self.assertFalse(is_user_application(Path("/System/Library/CoreServices/Dock.app")))

    def test_internos_de_outro_app_ficam_de_fora(self):
        self.assertFalse(is_user_application(Path("/Applications/Docker.app/Contents/MacOS/Docker Desktop.app")))
        self.assertFalse(is_user_application(Path("/Applications/Xcode.app/Contents/Developer/Applications/Simulator.app")))

    def test_sistema_biblioteca_lixeira_backup_e_instaladores_ficam_de_fora(self):
        for path in ("/Library/Application Support/Foo/Helper.app", f"{HOME}/Library/Application Support/X/Y.app",
                     f"{HOME}/.Trash/Velho.app", "/Volumes/Backup/Backups.backupdb/Mac/App.app",
                     "/Volumes/Instalador/Instalador.app", "/opt/homebrew/Caskroom/foo/1.0/Foo.app",
                     "/private/var/folders/x/Foo.app"):
            with self.subTest(path=path):
                self.assertFalse(is_user_application(Path(path)))

    def test_so_pacotes_app(self):
        self.assertFalse(is_user_application(Path("/Applications/Leia-me.pdf")))
        self.assertFalse(is_user_application(Path("/Applications/Pasta")))


class SpokenNamesTest(unittest.TestCase):
    def test_nome_do_finder_nome_do_arquivo_e_apelidos_em_portugues(self):
        app = Application("Calculator", Path("/System/Applications/Calculator.app"))
        self.assertEqual(app.spoken_names, ("Calculator", "calculadora"))

    def test_sistema_em_portugues_o_nome_traduzido_e_o_arquivo_valem(self):
        app = Application("Calculadora", Path("/System/Applications/Calculator.app"))
        self.assertEqual(app.spoken_names, ("Calculadora", "Calculator"))  # "calculadora" repetido some

    def test_app_sem_apelido_so_tem_os_dois_nomes(self):
        app = Application("Google Chrome", Path("/Applications/Google Chrome.app"))
        self.assertEqual(app.spoken_names, ("Google Chrome",))

    def test_nome_traduzido_diferente_do_arquivo(self):
        app = Application("Ajustes do Sistema", Path("/System/Applications/System Settings.app"))
        self.assertIn("Ajustes do Sistema", app.spoken_names)
        self.assertIn("System Settings", app.spoken_names)
        self.assertIn("configurações", app.spoken_names)

    def test_apelido_vale_pelo_nome_do_finder_quando_o_arquivo_nao_tem_espaco(self):
        # No Mac real: VoiceMemos.app aparece no Finder como "Voice Memos"; FindMy.app como "Find My".
        memos = Application("Voice Memos", Path("/System/Applications/VoiceMemos.app"))
        self.assertIn("gravador de voz", memos.spoken_names)
        find = Application("Find My", Path("/System/Applications/FindMy.app"))
        self.assertIn("buscar", find.spoken_names)

    def test_chaves_da_tabela_estao_normalizadas(self):
        from speakbar.commands import normalize
        for key in NICKNAMES:
            self.assertEqual(key, normalize(key))


def app(name, stem=None):
    return Application(name, Path(f"/Applications/{stem or name}.app"))


class FindApplicationTest(unittest.TestCase):
    APPS = [app("Safari"), app("Google Chrome"), app("Until AM for Chrome"), app("Notes"), app("Stickies"),
            app("Calculator"), app("Image Capture"), app("Screenshot"), app("ChatGPT"),
            app("Visual Studio Code"), app("Photo Booth")]

    def find(self, phrase, apps=None):
        status, found = find_application(phrase, apps or self.APPS)
        return status, [a.name for a in found]

    def test_nome_unico(self):
        self.assertEqual(self.find("abrir Safari"), ("match", ["Safari"]))
        self.assertEqual(self.find("abrir o safari"), ("match", ["Safari"]))

    def test_verbo_mal_transcrito(self):
        for verb in ("abril", "abriu", "abrem", "abre", "abra"):
            with self.subTest(verb=verb):
                self.assertEqual(self.find(f"{verb} safari"), ("match", ["Safari"]))

    def test_apelido_em_portugues_acha_o_app_com_nome_em_ingles(self):
        self.assertEqual(self.find("abrir calculadora"), ("match", ["Calculator"]))

    def test_abreviacao(self):
        self.assertEqual(self.find("abrir vscode"), ("match", ["Visual Studio Code"]))
        self.assertEqual(self.find("abrir vs code"), ("match", ["Visual Studio Code"]))

    def test_nome_que_a_transcricao_separa(self):
        self.assertEqual(self.find("abrir chat gpt"), ("match", ["ChatGPT"]))

    def test_varios_aplicativos_parecidos_perguntam(self):
        status, names = self.find("abrir chrome")
        self.assertEqual((status, set(names)), ("ambiguous", {"Google Chrome", "Until AM for Chrome"}))
        status, names = self.find("abrir captura")
        self.assertEqual((status, set(names)), ("ambiguous", {"Image Capture", "Screenshot"}))

    def test_frase_igual_ao_nome_de_um_so_app_vence(self):
        # "notas" é o apelido de Notes e também uma palavra de "notas adesivas" (Stickies).
        self.assertEqual(self.find("abrir notas"), ("match", ["Notes"]))
        self.assertEqual(self.find("abrir notas adesivas"), ("match", ["Stickies"]))

    def test_dois_nomes_do_mesmo_app_nao_sao_ambiguidade(self):
        # "Calculator" e "calculadora" são o mesmo app.
        self.assertEqual(self.find("abrir calculator"), ("match", ["Calculator"]))

    def test_mesmo_nome_em_duas_pastas_vira_um(self):
        apps = [Application("Epic Games Launcher", Path("/Applications/Epic.app")),
                Application("Epic Games Launcher", Path("/Users/Shared/Staging/Epic.app"))]
        status, found = find_application("abrir epic games launcher", apps)
        self.assertEqual((status, [a.path for a in found]), ("match", [Path("/Applications/Epic.app")]))

    def test_sem_correspondencia_e_sem_nome(self):
        for phrase in ("abrir futebol", "abrir fio", "abrir", "abrir o", "abrir 3"):
            with self.subTest(phrase=phrase):
                self.assertEqual(find_application(phrase, self.APPS)[0], "none")

    def test_lista_vazia(self):
        self.assertEqual(find_application("abrir safari", []), ("none", []))


class OpenRequestTest(unittest.TestCase):
    def test_pedidos_de_abrir_aplicativo(self):
        for phrase in ("abrir safari", "abra o chrome", "abre calculadora", "abril notas", "abriu spotify"):
            with self.subTest(phrase=phrase):
                self.assertTrue(is_open_request(phrase))

    def test_comandos_de_tela_continuam_sendo_de_tela(self):
        for phrase in ("abrir menu de contexto", "abrir o menu de contexto do número sete",
                       "clique em safari", "mostrar números", "digitar abrir", "rolar para baixo", ""):
            with self.subTest(phrase=phrase):
                self.assertFalse(is_open_request(phrase))

    def test_palavras_parecidas_demais_nao_contam(self):
        self.assertFalse(is_open_request("abraço"))
        self.assertFalse(is_open_request("acessar safari"))


class OpenApplicationTest(unittest.TestCase):
    def runner(self, code=0, stderr=""):
        calls = []

        class Result:
            returncode = code

        Result.stderr = stderr

        def run(command, **options):
            calls.append((command, options))
            return Result()

        return run, calls

    def test_abre_com_o_comando_open_sem_shell(self):
        run, calls = self.runner()
        open_application("/Applications/Safari.app", runner=run)
        self.assertEqual(calls[0][0], ["/usr/bin/open", "/Applications/Safari.app"])
        self.assertNotIn("shell", calls[0][1])

    def test_caminho_com_espacos_e_simbolos_vai_inteiro_como_um_argumento(self):
        run, calls = self.runner()
        open_application("/Applications/App; rm -rf ~ $(x).app", runner=run)
        self.assertEqual(calls[0][0][1], "/Applications/App; rm -rf ~ $(x).app")

    def test_recusa_o_que_nao_e_aplicativo(self):
        run, calls = self.runner()
        with self.assertRaises(ValueError):
            open_application("/Users/x/Documents/relatorio.pdf", runner=run)
        self.assertEqual(calls, [])

    def test_erro_do_macos_vira_mensagem(self):
        run, _ = self.runner(code=1, stderr="The file does not exist.")
        with self.assertRaises(OSError) as caught:
            open_application("/Applications/Fantasma.app", runner=run)
        self.assertIn("does not exist", str(caught.exception))


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def app(self, *parts):
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True)
        return path

    def test_le_as_pastas_padrao_em_ordem_alfabetica(self):
        self.app("Applications", "Zeta.app")
        self.app("Applications", "alfa.app")
        self.app("Applications", "Leia-me")
        found = scan_applications(folders=[self.root / "Applications"], extra=(), spotlight=None)
        self.assertEqual([app.name for app in found], ["alfa", "Zeta"])

    def test_pasta_inexistente_e_ignorada(self):
        self.assertEqual(scan_applications(folders=[self.root / "nao-existe"], extra=(), spotlight=None), [])

    def test_spotlight_acrescenta_apps_de_qualquer_pasta(self):
        # Um app fora de /Applications (aqui, "Downloads") só chega pelo Spotlight.
        solto = self.app("Home", "Downloads", "Solto.app")
        self.app("Applications", "Safari.app")
        found = scan_applications(folders=[self.root / "Applications"], extra=(),
                                  spotlight=lambda: [solto], name=lambda path: path.stem)
        self.assertEqual({app.name for app in found}, {"Safari", "Solto"})

    def test_sem_spotlight_o_app_fora_das_pastas_padrao_nao_aparece(self):
        self.app("Home", "Downloads", "Solto.app")
        self.app("Applications", "Safari.app")
        found = scan_applications(folders=[self.root / "Applications"], extra=(), spotlight=None,
                                  name=lambda path: path.stem)
        self.assertEqual([app.name for app in found], ["Safari"])

    def test_spotlight_ignora_o_que_nao_existe_mais_e_o_que_e_interno(self):
        real = self.app("Applications", "Real.app")
        nested = self.app("Applications", "Real.app", "Contents", "Nested.app")
        found = scan_applications(
            folders=[], extra=(),
            spotlight=lambda: [Path("/Applications/Fantasma.app"), nested, real],
            name=lambda path: path.stem)
        self.assertEqual([app.name for app in found], ["Real"])  # sem o fantasma e sem o app interno

    def test_o_mesmo_app_por_dois_caminhos_aparece_uma_vez(self):
        original = self.app("Applications", "Original.app")
        link = self.root / "Applications" / "Atalho.app"
        link.symlink_to(original, target_is_directory=True)
        found = scan_applications(folders=[self.root / "Applications"], extra=(), spotlight=None, name=lambda p: p.stem)
        self.assertEqual(len(found), 1)

    def test_nome_de_exibicao_vem_da_funcao_injetada(self):
        self.app("Applications", "Calculator.app")
        found = scan_applications(folders=[self.root / "Applications"], extra=(), spotlight=None,
                                  name=lambda path: "Calculadora")
        self.assertEqual((found[0].name, found[0].path.stem), ("Calculadora", "Calculator"))


class SpotlightPathsTest(unittest.TestCase):
    def test_converte_a_saida_em_caminhos(self):
        class Result:
            returncode, stdout = 0, "/Applications/A.app\n\n/Users/x/Downloads/B.app\n"
        self.assertEqual(spotlight_paths(runner=lambda *a, **k: Result()),
                         [Path("/Applications/A.app"), Path("/Users/x/Downloads/B.app")])

    def test_falha_do_spotlight_nao_quebra(self):
        def broken(*args, **kwargs):
            raise OSError("sem mdfind")
        self.assertEqual(spotlight_paths(runner=broken), [])

    def test_codigo_de_erro_devolve_vazio(self):
        class Result:
            returncode, stdout = 1, "/Applications/A.app\n"
        self.assertEqual(spotlight_paths(runner=lambda *a, **k: Result()), [])


class CatalogTest(unittest.TestCase):
    def make(self, ttl=60.0):
        self.now = 1000.0
        self.calls = 0
        self.gate = threading.Event()
        self.gate.set()
        self.results = [[Application("A", Path("/Applications/A.app"))],
                        [Application("A", Path("/Applications/A.app")), Application("B", Path("/Applications/B.app"))]]

        def scan():
            self.gate.wait(5)
            result = self.results[min(self.calls, len(self.results) - 1)]
            self.calls += 1
            if isinstance(result, Exception):
                raise result
            return result

        return ApplicationCatalog(scan=scan, ttl=ttl, clock=lambda: self.now)

    def test_a_primeira_consulta_nao_espera_a_leitura(self):
        catalog = self.make()
        self.gate.clear()  # a leitura fica "presa"
        self.assertEqual(catalog.applications(), [])  # voltou na hora, mesmo sem a lista pronta
        self.gate.set()
        self.assertTrue(catalog.wait(5))
        self.assertEqual([app.name for app in catalog.applications()], ["A"])

    def test_lista_fresca_nao_le_de_novo(self):
        catalog = self.make()
        catalog.applications()
        catalog.wait(5)
        self.now += 30
        catalog.applications()
        self.assertEqual(self.calls, 1)

    def test_lista_velha_e_devolvida_e_atualizada_em_segundo_plano(self):
        catalog = self.make(ttl=60)
        catalog.applications()
        catalog.wait(5)
        self.now += 61
        self.gate.clear()
        stale = catalog.applications()  # devolve a velha na hora e dispara a atualização
        self.assertEqual([app.name for app in stale], ["A"])
        self.gate.set()
        deadline = threading.Event()
        for _ in range(100):
            if [app.name for app in catalog.applications()] == ["A", "B"]:
                break
            deadline.wait(0.02)
        self.assertEqual([app.name for app in catalog.applications()], ["A", "B"])

    def test_varias_consultas_seguidas_disparam_uma_unica_leitura(self):
        catalog = self.make()
        self.gate.clear()
        for _ in range(5):
            catalog.applications()
        self.gate.set()
        catalog.wait(5)
        self.assertEqual(self.calls, 1)

    def test_invalidar_forca_nova_leitura(self):
        catalog = self.make()
        catalog.applications()
        catalog.wait(5)
        catalog.invalidate()
        catalog.applications()
        for _ in range(100):
            if self.calls == 2:
                break
            threading.Event().wait(0.02)
        self.assertEqual(self.calls, 2)

    def test_falha_na_leitura_mantem_a_lista_anterior(self):
        catalog = self.make(ttl=60)
        catalog.applications()
        catalog.wait(5)
        self.results[1] = RuntimeError("disco ocupado")
        self.now += 61
        catalog.applications()
        for _ in range(100):
            if self.calls == 2:
                break
            threading.Event().wait(0.02)
        self.assertEqual([app.name for app in catalog.applications()], ["A"])

    def test_falha_na_primeira_leitura_nao_trava_quem_espera(self):
        catalog = self.make()
        self.results[0] = RuntimeError("sem permissão")
        catalog.applications()
        self.assertTrue(catalog.wait(5))
        self.assertEqual(catalog.applications(), [])


if __name__ == "__main__":
    unittest.main()
