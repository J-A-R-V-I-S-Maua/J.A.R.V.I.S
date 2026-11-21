"""Mesa, Dock e Mission Control no backend macOS, com uma árvore de acessibilidade simulada.

A árvore espelha o que a sonda encontrou num Mac: a mesa é uma rolagem pendurada direto no
Finder, o ícone de um PDF traz botões de página dentro, e o Dock tem separadores sem nome.
Com o Mission Control aberto, o Dock ganha o grupo "mc", com as janelas e a barra do Spaces.
"""
import importlib
import sys
import unittest
from types import ModuleType
from unittest.mock import Mock, patch

FINDER, DOCK, OTHER = 635, 631, 99
SCREEN = (0, 0, 1470, 956)


def build_tree():
    roles, names, frames, actions, children, attrs = {}, {}, {}, {}, {}, {}

    def add(element, role, name="", frame=None, acts=(), kids=()):
        roles[element], names[element], frames[element] = role, name, frame
        actions[element], children[element] = tuple(acts), tuple(kids)

    add("finder", "AXApplication", kids=("menubar", "window", "desk"))
    add("menubar", "AXMenuBar", frame=(0, 0, 1470, 33))
    add("window", "AXWindow", "Documentos", (100, 100, 600, 400), kids=("windowarea",))
    add("windowarea", "AXScrollArea", "", (100, 130, 600, 370))
    add("desk", "AXScrollArea", "desktop", SCREEN, kids=("group",))
    add("group", "AXGroup", "desktop", SCREEN, ("AXShowMenu",), kids=("pdf", "app", "nameless"))
    add("pdf", "AXImage", "Guia.pdf", (1389, 377, 64, 64), ("AXOpen", "AXShowMenu"), kids=("prev", "next"))
    add("prev", "AXButton", "Previous Page", (1393, 414, 25, 25), ("AXPress",))
    add("next", "AXButton", "Next Page", (1424, 414, 25, 25), ("AXPress",))
    add("app", "AXImage", "bigjpg", (1389, 265, 64, 64), ("AXOpen", "AXShowMenu"))
    add("nameless", "AXImage", "", (1389, 153, 64, 64), ("AXOpen",))
    add("dock", "AXApplication", "Dock", kids=("dlist",))
    add("dlist", "AXList", "", (147, 877, 1176, 69), kids=("chrome", "sep", "terminal", "trash"))
    add("chrome", "AXDockItem", "Google Chrome", (897, 877, 53, 69), ("AXPress", "AXShowMenu", "AXShowExpose"))
    add("sep", "AXDockItem", "", (1162, 877, 24, 69), ("AXPress", "AXShowMenu"))
    add("terminal", "AXDockItem", "Terminal", (314, 877, 53, 69), ("AXPress", "AXShowMenu"))
    add("trash", "AXDockItem", "Trash", (1262, 877, 53, 69), ("AXPress", "AXShowMenu"))
    attrs[("window", "AXWindowNumber")] = 7
    # Mission Control, como lido num Mac real; só fica pendurado no Dock enquanto está aberto.
    add("mc", "AXGroup", "Mission Control", SCREEN, kids=("display",))
    add("display", "AXGroup", "", SCREEN, kids=("mcwindows", "spaces"))
    add("mcwindows", "AXGroup", "", (20, 204, 1430, 654), kids=("readme", "linkedin", "mcnote"))
    add("readme", "AXButton", "README.md — J.A.R.V.I.S", (18, 212, 470, 295), ("AXPress",))
    add("linkedin", "AXButton", "Feed | LinkedIn", (500, 541, 470, 295), ("AXPress",))
    add("mcnote", "AXStaticText", "texto solto", (10, 10, 50, 20))
    add("spaces", "AXGroup", "Barra do Spaces", (0, 0, 1470, 164), kids=("spacelist", "addspace"))
    add("spacelist", "AXList", "", (0, 0, 1470, 164), kids=("desk1", "fullscreen"))
    add("desk1", "AXButton", "Mesa", (581, 46, 138, 90), ("AXPress",))
    add("fullscreen", "AXButton", "Claude", (751, 46, 138, 90), ("AXPress", "AXRemoveDesktop"))
    add("addspace", "AXButton", "", (1398, -6, 170, 170), ("AXPress",))
    for element, identifier in (("mc", "mc"), ("display", "mc.display"), ("mcwindows", "mc.windows"),
                                ("spaces", "mc.spaces"), ("spacelist", "mc.spaces.list"), ("addspace", "mc.spaces.add")):
        attrs[(element, "AXIdentifier")] = identifier
    return roles, names, frames, actions, children, attrs


class Environment:
    """Instala um `macos` falso e importa o backend por cima dele."""

    def __init__(self, case, frontmost=FINDER, front="desk", dock_running=True, mission_control=False):
        self.roles, self.names, self.frames, self.actions, self.children, self.attrs = build_tree()
        self.frontmost, self.front, self.dock_running = frontmost, front, dock_running
        self.set_mission_control(mission_control)
        macos = ModuleType("screen_access.macos")
        macos.read_attribute = lambda e, name: (self.children.get(e, ()) or None) if name == "AXChildren" \
            else self.attrs.get((e, name), True if name == "AXEnabled" and e == "x" else None)
        macos.element_role = lambda e: self.roles.get(e, "")
        macos.element_name = lambda e: self.names.get(e, "")
        macos.element_frame = lambda e: self.frames.get(e)
        macos.action_names = lambda e: self.actions.get(e, ())
        macos.application_element = lambda pid: {FINDER: "finder", DOCK: "dock"}[pid]
        macos.pid_for_bundle = lambda bundle: {"com.apple.finder": FINDER,
                                               "com.apple.dock": DOCK if self.dock_running else None}.get(bundle)
        macos.frontmost_application = lambda: (self.frontmost, "App")
        macos.front_window = lambda pid: self.front  # no Finder, depois de clicar na mesa, é a própria mesa
        macos.window_title = lambda w: self.names.get(w, "")
        macos.enable_web_accessibility = Mock()
        macos.wait_for_web_content = Mock(return_value=True)
        macos.text_inside = Mock(return_value="")
        macos.parent = Mock(return_value=None)
        macos.action_names = getattr(macos, "action_names", None) or (lambda e: ("AXPress",))
        macos.can_press = lambda e: "AXPress" in self.actions.get(e, ())
        macos.is_visible = Mock(return_value=True)
        macos.TEXT_INPUT_ROLES = {"AXTextField"}
        macos.ACTION_ROLES = {"AXButton"}
        macos.press = Mock(return_value=0)
        macos.perform_action = Mock(return_value=0)
        macos.describe_error = Mock(return_value="recusado")
        macos.has_accessibility_permission = Mock(return_value=True)
        self.macos = macos
        services = ModuleType("ApplicationServices")
        services.AXUIElementIsAttributeSettable = Mock(return_value=(0, True))
        services.AXUIElementSetAttributeValue = Mock(return_value=0)

        import screen_access

        def forget():
            # Outro teste pode ter deixado no pacote uma referência a um módulo ligado ao dublê dele.
            for name in ("macos_shell", "macos_backend"):
                if hasattr(screen_access, name):
                    delattr(screen_access, name)

        forget()
        case.addCleanup(forget)
        for patcher in (
            patch.dict(sys.modules, {"screen_access.macos": macos,
                                     "ApplicationServices": services}),
            patch.object(screen_access, "macos", macos, create=True),
        ):
            patcher.start()
            case.addCleanup(patcher.stop)
        for name in ("macos_shell", "macos_backend"):
            sys.modules.pop(f"screen_access.{name}", None)
        self.module = importlib.import_module("screen_access.macos_backend")
        self.backend = self.module.MacOSBackend()

    def set_mission_control(self, opened):
        self.children["dock"] = ("dlist", "mc") if opened else ("dlist",)


class ForegroundTest(unittest.TestCase):
    def test_finder_que_entrega_a_propria_mesa_como_janela_em_foco(self):
        # Foi isto que o Mac real fez: a "janela em foco" do Finder era a rolagem da mesa.
        env = Environment(self, front="desk")
        window = env.backend.foreground()
        self.assertEqual((window.identity, window.pid, window.title, window.frame),
                         (f"desktop:{FINDER}", FINDER, "Área de trabalho", SCREEN))

    def test_finder_sem_nenhuma_janela_tambem_e_a_mesa(self):
        env = Environment(self, front=None)
        self.assertEqual(env.backend.foreground().identity, f"desktop:{FINDER}")

    def test_finder_com_janela_de_pasta_nao_e_a_mesa(self):
        env = Environment(self, front="window")
        window = env.backend.foreground()
        self.assertEqual((window.identity, window.title), (f"{FINDER}:7", "Documentos"))

    def test_outro_aplicativo_nao_vira_a_mesa(self):
        env = Environment(self, frontmost=OTHER, front=None)
        self.assertIsNone(env.backend.foreground())

    def test_janelas_do_finder_nao_sao_confundidas_com_a_mesa(self):
        Environment(self)
        roots = importlib.import_module("screen_access.macos_shell").desktop_roots(FINDER)
        self.assertEqual(roots, ["desk"])  # "window" é AXWindow e "windowarea" fica dentro dela

    def test_sem_mesa_no_finder_nao_ha_alvo(self):
        env = Environment(self)
        env.children["finder"] = ("menubar",)  # a mesa sumiu (ex.: ícones da mesa desligados)
        env.front = None  # e o Finder deixa de entregá-la como janela em foco
        self.assertIsNone(env.backend.foreground())


class ShellReadingTest(unittest.TestCase):
    def setUp(self):
        self.env = Environment(self)
        self.shell = importlib.import_module("screen_access.macos_shell")

    def test_so_os_icones_sao_lidos_sem_descer_neles(self):
        icons = list(self.shell.desktop_icons("desk"))
        self.assertEqual(icons, ["pdf", "app", "nameless"])  # sem "prev" e "next", que ficam dentro do PDF

    def test_dock_lista_so_itens_do_dock_incluindo_os_sem_nome(self):
        self.assertEqual(list(self.shell.dock_items(DOCK)), ["chrome", "sep", "terminal", "trash"])

    def test_so_o_finder_e_reconhecido_como_finder(self):
        self.assertTrue(self.shell.is_finder(FINDER))
        self.assertFalse(self.shell.is_finder(OTHER))
        self.assertFalse(self.shell.is_finder(None))


class ScanTest(unittest.TestCase):
    def scan(self, **options):
        env = Environment(self, **options)
        snapshot = env.backend.scan(env.backend.foreground())
        return env, snapshot

    def test_mesa_e_dock_na_mesma_numeracao(self):
        _, snapshot = self.scan()
        self.assertEqual([(e.number, e.name, e.action) for e in snapshot.elements], [
            (1, "Guia.pdf", "open"), (2, "bigjpg", "open"),
            (3, "Google Chrome", "press"), (4, "Terminal", "press"), (5, "Trash", "press")])

    def test_botoes_de_pagina_do_pdf_ficam_de_fora(self):
        _, snapshot = self.scan()
        names = [e.name for e in snapshot.elements]
        self.assertNotIn("Previous Page", names)
        self.assertNotIn("Next Page", names)

    def test_itens_sem_nome_ficam_de_fora(self):
        _, snapshot = self.scan()
        self.assertTrue(all(e.name for e in snapshot.elements))  # ícone sem nome e separador do Dock

    def test_dock_fora_do_ar_deixa_so_a_mesa(self):
        _, snapshot = self.scan(dock_running=False)
        self.assertEqual([e.name for e in snapshot.elements], ["Guia.pdf", "bigjpg"])

    def test_leitura_comum_de_janela_nao_usa_a_classificacao_da_mesa(self):
        env = Environment(self, front="window")
        env.roles["img"], env.names["img"] = "AXImage", "Foto na página"
        env.actions["img"] = ("AXOpen",)
        action = env.backend.describe("img", None)[3]
        self.assertEqual(action, "")  # AXImage fora da mesa não vira acionável


class AppTreeTest(unittest.TestCase):
    """Itens que a varredura deixava de fora no Finder e no Spotify (medido num Mac real)."""

    def setUp(self):
        self.env = Environment(self, front="window")
        # Os mesmos papéis acionáveis do módulo de verdade.
        self.env.macos.ACTION_ROLES = {"AXLink", "AXButton", "AXRadioButton", "AXCheckBox", "AXPopUpButton",
                                       "AXMenuItem", "AXMenuButton", "AXDisclosureTriangle", "AXRow"}
        self.env.macos.walk_with_clip = lambda root, depth, clip: iter([(0, root, clip)])

    def describe(self, element, name, role, actions, **attrs):
        env = self.env
        env.roles[element], env.names[element], env.actions[element] = role, name, actions
        env.frames[element] = (10, 10, 100, 20)
        for key, value in attrs.items():
            env.attrs[(element, key)] = value
        return env.backend.describe(element, None)

    def test_nome_de_arquivo_no_finder_abre_o_item(self):
        # O campo do nome não aceita foco (seria renomear); ele declara AXOpen, que abre o item.
        with patch.object(self.env.module, "AXUIElementIsAttributeSettable", Mock(return_value=(0, False))):
            name, role, frame, action, visible, _ = self.describe(
                "tf", "Downloads", "AXTextField", ("AXOpen", "AXShowMenu"), AXEnabled=True)
        self.assertEqual((name, action), ("Downloads", "open"))

    def test_barra_lateral_e_caminho_abrem(self):
        self.assertEqual(self.describe("cell", "Aplicativos", "AXCell", ("AXOpen",))[3], "open")
        self.assertEqual(self.describe("path", "Macintosh HD", "AXStaticText", ("AXOpen",))[3], "open")

    def test_item_sem_nome_nao_entra(self):
        self.assertEqual(self.describe("cell2", "", "AXCell", ("AXOpen",))[3], "")

    def test_imagem_so_abre_dentro_do_finder(self):
        self.env.backend.finder_walk = True  # visualização por ícones
        self.assertEqual(self.describe("icon", "Relatório.pdf", "AXImage", ("AXOpen",))[3], "open")
        self.env.backend.finder_walk = False
        self.assertEqual(self.describe("icon", "Relatório.pdf", "AXImage", ("AXOpen",))[3], "")

    def test_linha_sem_titulo_ganha_o_nome_dos_textos_de_dentro(self):
        # Uma música do Spotify: AXRow clicável e sem título; os textos ficam nas células.
        self.env.macos.text_inside = Mock(return_value="Ela Partiu – Tim Maia")
        name, role, frame, action, visible, _ = self.describe("row", "", "AXRow", ("AXPress", "AXShowMenu"),
                                                              AXEnabled=True)
        self.assertEqual((name, action), ("Ela Partiu – Tim Maia", "press"))

    def test_linha_sem_acao_continua_de_fora(self):
        # Linhas do Finder não têm AXPress; quem entra é o nome do arquivo dentro delas.
        self.assertEqual(self.describe("frow", "", "AXRow", ("AXShowDefaultUI",), AXEnabled=True)[3], "")

    def test_triangulo_de_expandir_leva_o_nome_da_pasta(self):
        self.env.macos.parent = Mock(return_value="cellx")
        self.env.macos.text_inside = Mock(return_value="Downloads")
        name = self.describe("tri", "", "AXDisclosureTriangle", ("AXPress",), AXEnabled=True, AXValue=0)[0]
        self.assertEqual(name, "Expandir Downloads")
        self.env.macos.text_inside.assert_called_with("cellx", skip="tri", limit=1)
        self.env.attrs[("tri", "AXValue")] = 1
        self.assertEqual(self.env.backend.describe("tri", None)[0], "Recolher Downloads")

    def test_botoes_de_menu_do_finder(self):
        self.assertEqual(self.describe("group", "Agrupar", "AXMenuButton", ("AXPress",), AXEnabled=True)[3], "press")

    def test_leitura_espera_a_pagina_do_chromium(self):
        self.env.backend.scan(self.env.backend.foreground())
        self.env.macos.wait_for_web_content.assert_called_once_with("window")


@unittest.skipUnless(sys.platform == "darwin", "usa o módulo macos de verdade")
class ChromiumWaitTest(unittest.TestCase):
    def setUp(self):
        for name in ("screen_access.macos",):
            sys.modules.pop(name, None)
        self.macos = importlib.import_module("screen_access.macos")

    def test_janela_que_nao_e_do_chromium_passa_direto(self):
        with patch.object(self.macos, "is_chromium_window", return_value=False), \
                patch.object(self.macos, "web_area_size") as size:
            self.assertTrue(self.macos.wait_for_web_content("janela"))
        size.assert_not_called()

    def wait(self, sizes, elapsed_per_nap=0.15, **kwargs):
        sizes, now = iter(sizes), [0.0]
        with patch.object(self.macos, "is_chromium_window", return_value=True), \
                patch.object(self.macos, "web_area_size", side_effect=lambda w: next(sizes)), \
                patch.object(self.macos.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(self.macos.time, "sleep", side_effect=lambda s: now.__setitem__(0, now[0] + elapsed_per_nap)) as nap:
            return self.macos.wait_for_web_content("janela", **kwargs), nap.call_count

    def test_pagina_ja_montada_segue_na_hora(self):
        self.assertEqual(self.wait([1766]), (True, 0))  # o Chrome do dia a dia não fica mais lento

    def test_arvore_desmontada_espera_ficar_grande(self):
        # Medido no Spotify: o Chromium remonta a árvore em degraus; parar no 132 seria ler pela metade.
        ready, naps = self.wait([19, 132, 388, 1766])
        self.assertEqual((ready, naps), (True, 3))

    def test_esqueleto_parado_no_comeco_nao_engana(self):
        # Como no YouTube: alguns décimos parado só com a busca, e depois a página inteira.
        ready, naps = self.wait([5, 5, 5, 5, 5, 600])
        self.assertEqual((ready, naps), (True, 5))

    def test_pagina_pequena_que_parou_de_crescer_esta_pronta_depois_do_tempo_minimo(self):
        ready, naps = self.wait([12] + [20] * 20)
        self.assertTrue(ready)
        self.assertEqual(naps, 8)  # 1,2 s: o tempo de uma remontagem

    def test_desiste_no_limite(self):
        now = [0.0]
        counter = iter(range(1000))
        with patch.object(self.macos, "is_chromium_window", return_value=True), \
                patch.object(self.macos, "web_area_size", side_effect=lambda w: next(counter)), \
                patch.object(self.macos.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(self.macos.time, "sleep", side_effect=lambda s: now.__setitem__(0, now[0] + s)):
            self.assertFalse(self.macos.wait_for_web_content("janela", timeout=1.0))
        self.assertLessEqual(now[0], 1.2)

    def test_textos_de_dentro_sem_numeros_soltos(self):
        tree = [(0, "row"), (1, "n"), (1, "title"), (1, "artist"), (1, "dup"), (1, "time"), (1, "album")]
        roles = {"row": "AXRow", "n": "AXStaticText", "title": "AXStaticText", "artist": "AXStaticText",
                 "dup": "AXStaticText", "time": "AXStaticText", "album": "AXStaticText"}
        values = {"n": "1", "title": "Ela Partiu", "artist": "Tim Maia", "dup": "Tim Maia", "time": "3:21",
                  "album": "Racional"}
        with patch.object(self.macos, "walk", return_value=iter(tree)), \
                patch.object(self.macos, "element_role", side_effect=lambda e: roles[e]), \
                patch.object(self.macos, "read_attribute", side_effect=lambda e, a: values.get(e)), \
                patch.object(self.macos, "same_element", side_effect=lambda a, b: a == b):
            self.assertEqual(self.macos.text_inside("row"), "Ela Partiu – Tim Maia – Racional")


class ContextMenuTest(unittest.TestCase):
    """Menu de contexto como lido no Finder real: pendurado na lista, alternativas no mesmo lugar."""

    def setUp(self):
        self.env = env = Environment(self, frontmost=FINDER, front="window")
        env.macos.ACTION_ROLES = {"AXButton", "AXMenuItem"}
        env.macos.KEY_ESCAPE = 53
        env.macos.press_key = Mock()
        env.macos.walk_with_clip = lambda root, depth, clip: iter([(0, root, clip), (1, "file", clip)])
        env.roles["file"], env.names["file"], env.frames["file"] = "AXButton", "Downloads", (110, 140, 200, 20)
        env.actions["file"] = ("AXPress", "AXShowMenu")
        env.attrs[("file", "AXEnabled")] = True
        env.children["window"] = ("windowarea", "file")
        parents = {"file": "window", "ctx": "window"}
        env.macos.parent = lambda element: parents.get(element)
        env.macos.perform_action = Mock(side_effect=self.open_menu)
        items = (("open", "Abrir em Nova Aba", 387), ("alt", "Abrir em Nova Janela", 387), ("sep", "", 411),
                 ("info", "Obter Informações", 422), ("inspector", "Mostrar Inspetor", 422),
                 ("off", "Desativada", 446))
        for element, name, y in items:
            env.roles[element], env.names[element], env.frames[element] = "AXMenuItem", name, (594, y, 233, 24)
            env.actions[element] = ("AXPress",)
            env.attrs[(element, "AXEnabled")] = element not in ("sep", "off")
        env.roles["ctx"], env.frames["ctx"] = "AXMenu", (594, 382, 233, 378)
        env.children["ctx"] = tuple(element for element, _, _ in items)
        self.threads = patch.object(env.module.threading, "Thread", side_effect=self.run_now)
        self.threads.start()
        self.addCleanup(self.threads.stop)

    def run_now(self, target, args, **kwargs):
        target(*args)
        return Mock()

    def open_menu(self, element, action):
        self.env.children["window"] = ("windowarea", "file", "ctx")  # o macOS pendura o menu na janela
        return -25204  # o AXShowMenu "falha" por tempo, com o menu aberto

    def open(self):
        backend = self.env.backend
        snapshot = backend.scan(backend.foreground())
        return backend, backend.context_menu(snapshot, 1)

    def test_abre_e_o_menu_vira_a_janela_da_frente(self):
        backend, menu = self.open()
        self.env.macos.perform_action.assert_called_once_with("file", "AXShowMenu")
        self.assertEqual((menu.identity, menu.title), (f"menu:{FINDER}", "Menu de contexto"))
        self.assertEqual(backend.foreground(), menu)

    def test_numera_so_as_opcoes_visiveis(self):
        backend, menu = self.open()
        names = [e.name for e in backend.scan(menu).elements]
        self.assertEqual(names, ["Abrir em Nova Aba", "Obter Informações"])

    def test_escolher_uma_opcao(self):
        backend, menu = self.open()
        options = backend.scan(menu)
        backend.activate(options, 2)
        self.env.macos.press.assert_called_once_with("info")

    def test_menu_fechado_tem_tamanho_zero(self):
        backend, menu = self.open()
        self.env.frames["ctx"] = (0, 956, 0, 0)
        self.assertIsNone(backend.menu_window())
        self.assertEqual(backend.foreground().title, "Documentos")

    def test_cancelar_fecha_com_esc_so_se_estiver_aberto(self):
        backend, menu = self.open()
        backend.close_menu()
        self.env.macos.press_key.assert_called_once_with(53)
        self.env.macos.press_key.reset_mock()
        backend.close_menu()
        self.env.macos.press_key.assert_not_called()

    def test_sem_menu_avisa(self):
        self.env.macos.perform_action = Mock(return_value=-25204)  # nada abriu
        with patch.object(self.env.module, "MENU_WAIT", 0.1):
            with self.assertRaisesRegex(self.env.module.ScreenError, "não abriu"):
                self.open()


class ActionTest(unittest.TestCase):
    def setUp(self):
        self.env = Environment(self)
        self.window = self.env.backend.foreground()
        self.snapshot = self.env.backend.scan(self.window)

    def test_clique_em_icone_da_mesa_abre_o_item(self):
        self.env.backend.activate(self.snapshot, 1)
        self.env.macos.perform_action.assert_called_once_with("pdf", "AXOpen")
        self.env.macos.press.assert_not_called()

    def test_clique_em_item_do_dock_usa_axpress(self):
        self.env.backend.activate(self.snapshot, 3)
        self.env.macos.press.assert_called_once_with("chrome")
        self.env.macos.perform_action.assert_not_called()

    def test_duplo_clique_abre_icone_da_mesa(self):
        self.env.backend.open_item(self.snapshot, 2)
        self.env.macos.perform_action.assert_called_once_with("app", "AXOpen")

    def test_duplo_clique_em_item_do_dock_e_recusado(self):
        with self.assertRaises(self.env.module.ScreenError):
            self.env.backend.open_item(self.snapshot, 3)
        self.env.macos.press.assert_not_called()

    def test_recusa_do_aplicativo_vira_mensagem(self):
        self.env.macos.perform_action.return_value = -25206
        with self.assertRaises(self.env.module.ScreenError):
            self.env.backend.activate(self.snapshot, 1)

    def test_a_mensagem_de_erro_diz_qual_acao_e_qual_codigo(self):
        self.env.macos.perform_action.return_value = -25204
        with self.assertRaises(self.env.module.ScreenError) as caught:
            self.env.backend.activate(self.snapshot, 1)
        self.assertIn("ação open", str(caught.exception))
        self.assertIn("código -25204", str(caught.exception))

    def test_finder_que_abre_e_responde_25205_nao_e_erro(self):
        # Comportamento observado no Mac real: o PDF abriu e a chamada devolveu -25205.
        self.env.macos.perform_action.return_value = -25205
        self.env.backend.activate(self.snapshot, 1)
        self.env.backend.open_item(self.snapshot, 2)
        self.assertEqual(self.env.macos.perform_action.call_count, 2)

    def test_a_tolerancia_vale_so_para_abrir_nao_para_apertar(self):
        self.env.macos.press.return_value = -25205
        with self.assertRaises(self.env.module.ScreenError):
            self.env.backend.activate(self.snapshot, 3)

    def test_outros_codigos_ao_abrir_continuam_sendo_erro(self):
        for code in (-25200, -25202, -25204, -25206, -25208):
            with self.subTest(code=code):
                self.env.macos.perform_action.return_value = code
                with self.assertRaises(self.env.module.ScreenError):
                    self.env.backend.activate(self.snapshot, 1)

    def test_erro_ao_apertar_item_do_dock_cita_a_acao_press(self):
        self.env.macos.press.return_value = -25204
        with self.assertRaises(self.env.module.ScreenError) as caught:
            self.env.backend.activate(self.snapshot, 3)
        self.assertIn("ação press", str(caught.exception))

    def test_nao_aciona_se_o_icone_mudou_de_lugar(self):
        self.env.frames["pdf"] = (10, 10, 64, 64)
        with self.assertRaises(self.env.module.ScreenError):
            self.env.backend.activate(self.snapshot, 1)
        self.env.macos.perform_action.assert_not_called()

    def test_nao_aciona_se_o_foco_saiu_da_mesa(self):
        self.env.frontmost = OTHER
        with self.assertRaises(self.env.module.ScreenError):
            self.env.backend.activate(self.snapshot, 1)
        self.env.macos.perform_action.assert_not_called()

    def test_cancelamento_antes_da_execucao(self):
        with self.assertRaises(self.env.module.ScreenError):
            self.env.backend.activate(self.snapshot, 3, cancelled=lambda: True)
        self.env.macos.press.assert_not_called()


class WindowExistsTest(unittest.TestCase):
    def test_mesa_existe_enquanto_o_retangulo_nao_muda(self):
        env = Environment(self)
        window = env.backend.foreground()
        self.assertTrue(env.backend.window_exists(window))
        env.frames["desk"] = (0, 0, 1000, 700)
        self.assertFalse(env.backend.window_exists(window))

    def test_mesa_some_se_o_finder_perde_a_rolagem(self):
        env = Environment(self)
        window = env.backend.foreground()
        env.children["finder"] = ("menubar",)
        self.assertFalse(env.backend.window_exists(window))


if __name__ == "__main__":
    unittest.main()


class MissionControlTest(unittest.TestCase):
    def test_fechado_a_janela_da_frente_continua_sendo_a_do_aplicativo(self):
        env = Environment(self, front="window")
        self.assertFalse(env.backend.foreground().identity.startswith("missioncontrol:"))

    def test_aberto_ele_e_a_janela_da_frente(self):
        env = Environment(self, frontmost=OTHER, front=None, mission_control=True)
        window = env.backend.foreground()
        self.assertEqual((window.identity, window.pid, window.title, window.frame),
                         (f"missioncontrol:{DOCK}", DOCK, "Mission Control", SCREEN))

    def test_numera_janelas_e_mesas_sem_o_botao_de_adicionar(self):
        env = Environment(self, mission_control=True)
        snapshot = env.backend.scan(env.backend.foreground())
        self.assertEqual([(item.name, item.action) for item in snapshot.elements],
                         [("README.md — J.A.R.V.I.S", "press"), ("Feed | LinkedIn", "press"),
                          ("Mesa", "press"), ("Claude", "press")])

    def test_escolher_um_numero_leva_ate_a_janela(self):
        env = Environment(self, mission_control=True)
        snapshot = env.backend.scan(env.backend.foreground())
        env.backend.activate(snapshot, 2)
        env.macos.press.assert_called_once_with("linkedin")

    def test_fechado_no_meio_do_caminho_a_lista_expira(self):
        env = Environment(self, mission_control=True)
        snapshot = env.backend.scan(env.backend.foreground())
        env.set_mission_control(False)
        with self.assertRaises(env.module.ScreenError):
            env.backend.activate(snapshot, 1)
        env.macos.press.assert_not_called()

    def test_campo_de_texto_lembrado_nao_e_apagado_pelo_mission_control(self):
        env = Environment(self, mission_control=True)
        env.backend.last_field = ("app", "campo")
        env.backend.remember_focus(env.backend.foreground())
        self.assertEqual(env.backend.last_field, ("app", "campo"))


class MissionControlTahoeTest(unittest.TestCase):
    """No Tahoe quem desenha o Mission Control é o WindowManager, com uma tela por monitor.

    A árvore espelha a lida num Mac real com dois monitores: as janelas ficam direto na
    "mc.display" (sem "mc.windows") e o botão de adicionar mesa fica fora da lista.
    """
    MANAGER = 1064
    MAIN, EXTERNAL = (0, 0, 1470, 956), (-1920, -124, 1920, 1080)

    def setUp(self):
        self.env = env = Environment(self, frontmost=OTHER, front="window")
        tree = {
            "disp1": ("AXGroup", "", self.MAIN, ("appstore", "chrome1", "spaces1"), "mc.display"),
            "appstore": ("AXButton", "App Store", (830, 472, 622, 382), (), "com.apple.AppStore.space.1"),
            "chrome1": ("AXButton", "Live – Chrome", (18, 213, 775, 487), (), "com.google.Chrome.space.1"),
            "spaces1": ("AXGroup", "Barra do Spaces", (0, 0, 1470, 164), ("list1", "add1"), "mc.spaces"),
            "list1": ("AXList", "", (0, 0, 1470, 164), ("mesa1",), "mc.spaces.list"),
            "mesa1": ("AXButton", "Mesa 1", (735, 96, 168, 129), (), None),
            "add1": ("AXButton", "Adicionar Mesa", (1539, 96, 138, 90), (), "mc.spaces.add"),
            "disp2": ("AXGroup", "", self.EXTERNAL, ("docker", "spaces2"), "mc.display"),
            "docker": ("AXButton", "Containers - Docker Desktop", (-1097, 70, 537, 300), (), "com.electron.dockerdesktop.space.10"),
            "spaces2": ("AXGroup", "Barra do Spaces", (-1920, -124, 1920, 146), ("list2",), "mc.spaces"),
            "list2": ("AXList", "", (-1920, -124, 1920, 146), ("mesa2",), "mc.spaces.list"),
            "mesa2": ("AXButton", "Mesa 2", (-1056, -47, 190, 129), (), None),
        }
        for element, (role, name, frame, kids, identifier) in tree.items():
            env.roles[element], env.names[element], env.frames[element] = role, name, frame
            env.children[element] = kids
            env.actions[element] = ("AXPress",) if role == "AXButton" else ()
            if identifier:
                env.attrs[(element, "AXIdentifier")] = identifier
        self.opened(False)
        bundles = {"com.apple.finder": FINDER, "com.apple.dock": DOCK, "com.apple.WindowManager": self.MANAGER}
        env.macos.pid_for_bundle = lambda bundle: bundles.get(bundle)
        env.macos.application_element = lambda pid: {FINDER: "finder", DOCK: "dock", self.MANAGER: "wm"}[pid]

    def opened(self, value):
        self.env.children["wm"] = ("disp1", "disp2") if value else ()

    def test_aberto_cobre_os_dois_monitores(self):
        self.opened(True)
        window = self.env.backend.foreground()
        self.assertEqual((window.identity, window.pid), (f"missioncontrol:{self.MANAGER}", self.MANAGER))
        self.assertEqual(window.frame, (-1920, -124, 3390, 1080))

    def test_numera_janelas_e_mesas_dos_dois_monitores(self):
        self.opened(True)
        snapshot = self.env.backend.scan(self.env.backend.foreground())
        self.assertEqual([e.name for e in snapshot.elements],
                         ["App Store", "Live – Chrome", "Mesa 1", "Containers - Docker Desktop", "Mesa 2"])
        self.env.backend.activate(snapshot, 4)
        self.env.macos.press.assert_called_once_with("docker")

    def test_fechado_nao_e_a_janela_da_frente(self):
        self.assertFalse(self.env.backend.foreground().identity.startswith("missioncontrol:"))

    def test_mostrar_janelas_abre_e_espera_o_window_manager(self):
        shell = self.env.module.macos_shell
        with patch.object(shell.subprocess, "run", side_effect=lambda *a, **k: self.opened(True)) as run, \
                patch.object(shell, "MISSION_CONTROL_WAIT", 0.05):
            window = self.env.backend.mission_control(True)
        run.assert_called_once()
        self.assertEqual(window.pid, self.MANAGER)
        with patch.object(shell.subprocess, "run", side_effect=lambda *a, **k: self.opened(False)), \
                patch.object(shell, "MISSION_CONTROL_WAIT", 0.05):
            self.assertIsNone(self.env.backend.mission_control(False))


class MissionControlToggleTest(unittest.TestCase):
    def environment(self, opened=False, appears=True):
        env = Environment(self, mission_control=opened)
        shell = env.module.macos_shell
        calls = []

        def run(arguments, **kwargs):
            calls.append(arguments)
            if appears:
                env.set_mission_control(not opened)

        for patcher in (patch.object(shell.subprocess, "run", side_effect=run),
                        patch.object(shell, "MISSION_CONTROL_WAIT", 0.05)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return env, calls

    def test_mostrar_janelas_abre_pelo_app_do_sistema(self):
        env, calls = self.environment()
        window = env.backend.mission_control(True)
        self.assertEqual(calls, [["/usr/bin/open", "-b", "com.apple.exposelauncher"]])
        # Devolve a própria "janela" do Mission Control, pronta para ser numerada.
        self.assertEqual(window, env.backend.foreground())
        self.assertTrue(window.identity.startswith("missioncontrol:"))
        self.assertEqual(len(env.backend.scan(window).elements), 4)

    def test_espera_as_miniaturas_pararem_antes_de_numerar(self):
        env, _ = self.environment(opened=True)
        shell = env.module.macos_shell
        sizes = iter([(10, 10, 40, 30), (10, 10, 120, 90), (10, 10, 200, 150)])
        moving = env.frames["readme"]

        def frame(element):
            if element == "readme":
                nonlocal moving
                moving = next(sizes, moving)  # cresce até o lugar e para
                return moving
            return env.frames.get(element)

        env.macos.element_frame = frame
        naps = []
        shell.wait_mission_control_settled(DOCK, sleep=naps.append)
        self.assertEqual(len(naps), 3)
        self.assertEqual(moving, (10, 10, 200, 150))

    def test_espera_tem_limite_mesmo_se_algo_nao_parar(self):
        env, _ = self.environment(opened=True)
        shell = env.module.macos_shell
        counter = iter(range(1000))
        env.macos.element_frame = lambda element: (next(counter), 0, 10, 10)
        now = [0.0]

        def nap(seconds):
            now[0] += seconds

        shell.wait_mission_control_settled(DOCK, clock=lambda: now[0], sleep=nap)
        self.assertLessEqual(now[0], shell.MISSION_CONTROL_SETTLE + 0.1)

    def test_fechado_no_meio_da_espera_desiste(self):
        env, _ = self.environment(opened=True)
        env.set_mission_control(False)
        naps = []
        env.module.macos_shell.wait_mission_control_settled(DOCK, sleep=naps.append)
        self.assertEqual(naps, [])

    def test_ja_aberto_nao_alterna_de_novo(self):
        env, calls = self.environment(opened=True)
        env.backend.mission_control(True)
        self.assertEqual(calls, [])

    def test_fechar_janelas(self):
        env, calls = self.environment(opened=True)
        self.assertFalse(env.backend.mission_control(False))
        self.assertEqual(len(calls), 1)
        self.assertFalse(env.backend.foreground().identity.startswith("missioncontrol:"))

    def test_quando_nao_abre_explica(self):
        env, _ = self.environment(appears=False)
        with self.assertRaisesRegex(env.module.ScreenError, "não abriu"):
            env.backend.mission_control(True)

    def test_sem_dock_nao_tenta(self):
        env = Environment(self, dock_running=False)
        with self.assertRaisesRegex(env.module.ScreenError, "Dock"):
            env.backend.mission_control(True)


class BrowserTest(unittest.TestCase):
    def environment(self, bundle="com.google.Chrome", keyboard=None):
        env = Environment(self, frontmost=OTHER, front="window")
        macos = env.macos
        macos.bundle_id = lambda pid: bundle
        macos.keyboard_pid = lambda: OTHER if keyboard is None else keyboard
        macos.press_key = Mock()
        macos.KEY_T, macos.KEY_W, macos.KEY_R, macos.KEY_TAB = 17, 13, 15, 48
        macos.KEY_LEFT, macos.KEY_RIGHT = 123, 124
        macos.KEY_DIGITS = {n: 17 + n for n in range(1, 10)}
        macos.COMMAND, macos.SHIFT, macos.CONTROL = 1, 2, 4
        macos.application_element = lambda pid: "chromeapp"  # sem barra de menus até menu() montar uma
        env.window = env.backend.foreground()
        return env

    def menu(self, env, char="[", enabled=True):
        env.roles["mbar"], env.children["mbar"] = "AXMenuBar", ("historico",)
        env.children["historico"] = ("menu",)
        env.children["menu"] = ("voltar",)
        env.attrs[("voltar", "AXMenuItemCmdChar")] = char
        env.attrs[("voltar", "AXMenuItemCmdModifiers")] = 0
        env.attrs[("voltar", "AXEnabled")] = enabled
        env.attrs[("chromeapp", "AXMenuBar")] = "mbar"

    def test_atalhos_de_aba(self):
        env = self.environment()
        for action, keys in (("new_tab", (17, 1)), ("close_tab", (13, 1)), ("reopen_tab", (17, 3)),
                             ("reload", (15, 1)), ("next_tab", (48, 4)), ("previous_tab", (48, 6)),
                             ("last_tab", (26, 1))):
            env.macos.press_key.reset_mock()
            env.backend.browser(env.window, action)
            env.macos.press_key.assert_called_once_with(*keys)
        env.macos.press_key.reset_mock()
        self.assertEqual(env.backend.browser(env.window, "tab", 3), "Aba 3.")
        env.macos.press_key.assert_called_once_with(20, 1)

    def test_voltar_usa_o_menu_do_navegador(self):
        # Pelo atalho do item, não pelo nome: vale em qualquer idioma e com o teclado ABNT2.
        env = self.environment()
        self.menu(env)
        self.assertEqual(env.backend.browser(env.window, "back"), "Página anterior.")
        env.macos.press.assert_called_once_with("voltar")
        env.macos.press_key.assert_not_called()

    def test_item_desativado_avisa_e_nao_aperta_nada(self):
        env = self.environment()
        self.menu(env, enabled=False)  # aba sem histórico
        with self.assertRaisesRegex(env.module.ScreenError, "Não há página anterior"):
            env.backend.browser(env.window, "back")
        env.macos.press.assert_not_called()
        env.macos.press_key.assert_not_called()

    def test_outros_comandos_tambem_passam_pelo_menu(self):
        env = self.environment()
        self.menu(env, char="T")
        self.assertEqual(env.backend.browser(env.window, "new_tab"), "Nova aba aberta.")
        env.macos.press.assert_called_once_with("voltar")  # o item do menu montado no teste
        env.macos.press_key.assert_not_called()

    def test_sem_o_item_no_menu_usa_cmd_seta(self):
        env = self.environment()
        env.backend.browser(env.window, "forward")
        env.macos.press_key.assert_called_once_with(124, 1)

    def test_fora_de_um_navegador_nao_aperta_nada(self):
        env = self.environment(bundle="com.apple.TextEdit")  # Cmd+W fecharia o documento
        with self.assertRaisesRegex(env.module.ScreenError, "navegador na frente"):
            env.backend.browser(env.window, "close_tab")
        env.macos.press_key.assert_not_called()

    def test_navegador_sem_o_teclado_nao_aperta_nada(self):
        env = self.environment(keyboard=12345)
        with self.assertRaisesRegex(env.module.ScreenError, "saiu da frente"):
            env.backend.browser(env.window, "new_tab")
        env.macos.press_key.assert_not_called()

    def test_aba_fora_do_intervalo(self):
        env = self.environment()
        with self.assertRaises(env.module.ScreenError):
            env.backend.browser(env.window, "tab", 9)


class ScrollTest(unittest.TestCase):
    """Janela de pasta do Finder com uma lista três vezes mais alta que a área visível."""

    def setUp(self):
        self.env = env = Environment(self, front="window")
        env.roles["bar"], env.roles["list"] = "AXScrollBar", "AXOutline"
        env.frames["list"] = (100, 130, 600, 1110)
        env.children["windowarea"] = ("list",)
        env.attrs[("windowarea", "AXContents")] = ("list",)
        self.settable = set()
        self.wheel = []
        self.owner = FINDER

        def walk_with_clip(element, max_depth, clip, depth=0):
            yield depth, element, clip
            for child in env.children.get(element, ()):
                yield from walk_with_clip(child, max_depth, clip, depth + 1)

        def set_attribute(element, name, value):
            env.attrs[(element, name)] = value
            return 0

        macos = env.macos
        macos.walk_with_clip = walk_with_clip
        macos.is_settable = lambda element, name: (element, name) in self.settable
        macos.set_attribute = set_attribute
        macos.focused_element = lambda pid: None
        macos.parent = lambda element: next((key for key, kids in env.children.items() if element in kids), None)
        macos.same_element = lambda a, b: a is not None and a == b
        macos.scroll_wheel = lambda point, pixels: self.wheel.append((point, pixels))
        macos.window_owner_at = lambda point, ignore=(): self.owner
        self.window = env.backend.foreground()

    def with_bar(self):
        self.env.attrs[("windowarea", "AXVerticalScrollBar")] = "bar"
        self.env.attrs[("bar", "AXValue")] = 0.0
        self.settable.add(("bar", "AXValue"))

    def test_app_nativo_rola_pela_barra_e_confirma_o_movimento(self):
        from screen_access.scroll import ScrollOutcome, ScrollRequest

        self.with_bar()
        targets = self.env.backend.prepare_scroll(self.window)
        self.assertEqual([(item.role, item.action) for item in targets.snapshot.elements], [("AXScrollArea", "scroll")])
        self.assertEqual(targets.preferred, 1)
        outcome = self.env.backend.scroll(targets.snapshot, 1, ScrollRequest("down", "small"))
        self.assertEqual(outcome, ScrollOutcome.MOVED)
        # Visível: um terço da lista. "Um pouco" anda 15% da altura visível.
        self.assertAlmostEqual(self.env.attrs[("bar", "AXValue")], 0.075)
        self.assertEqual(self.wheel, [])

    def test_area_sem_barra_recebe_rolagem_do_trackpad_no_centro(self):
        from screen_access.scroll import ScrollOutcome, ScrollRequest

        targets = self.env.backend.prepare_scroll(self.window)
        self.assertEqual(self.env.backend.scroll_references[1][5], "wheel")
        with patch.object(self.env.module.time, "sleep"):
            outcome = self.env.backend.scroll(targets.snapshot, 1, ScrollRequest("down", "normal"))
        self.assertEqual(outcome, ScrollOutcome.REQUESTED)  # a página não diz onde está
        self.assertEqual({point for point, _ in self.wheel}, {(400.0, 315.0)})
        self.assertEqual(sum(pixels for _, pixels in self.wheel), -185)  # metade da altura, para baixo
        self.assertGreater(len(self.wheel), 5)  # em vários passos, não num salto só

    def test_sem_area_exposta_rola_a_janela_inteira(self):
        from screen_access.scroll import ScrollRequest

        self.env.children["window"] = ()  # como o Cursor e outros apps Electron
        targets = self.env.backend.prepare_scroll(self.window)
        self.assertEqual([item.role for item in targets.snapshot.elements], ["AXWindow"])
        with patch.object(self.env.module.time, "sleep"):
            self.env.backend.scroll(targets.snapshot, 1, ScrollRequest("up", "page"))
        self.assertEqual({point for point, _ in self.wheel}, {(400.0, 300.0)})
        self.assertEqual(sum(pixels for _, pixels in self.wheel), 360)  # 90% da janela, para cima

    def test_outra_janela_por_cima_cancela_sem_rolar(self):
        from screen_access.scroll import ScrollRequest

        targets = self.env.backend.prepare_scroll(self.window)
        self.owner = OTHER
        with self.assertRaisesRegex(self.env.module.ScreenError, "por cima"):
            self.env.backend.scroll(targets.snapshot, 1, ScrollRequest("down", "normal"))
        self.assertEqual(self.wheel, [])

    def test_mesa_e_mission_control_nao_viram_area_rolavel(self):
        env = Environment(self, front="desk")
        env.macos.window_owner_at = lambda point, ignore=(): FINDER
        env.macos.focused_element = lambda pid: None
        targets = env.backend.prepare_scroll(env.backend.foreground())
        self.assertEqual(targets.snapshot.elements, ())


class TextFieldFixture:
    """Campo de texto dentro da janela de pasta, com a seleção guardada em unidades UTF-16."""

    def setUp(self):
        self.env = env = Environment(self, front="window")
        env.roles["field"] = "AXTextField"
        env.names["field"] = "Mensagem"
        env.frames["field"] = (120, 140, 300, 24)
        env.children["window"] = ("windowarea", "field")
        env.attrs.update({("field", "AXEnabled"): True, ("field", "AXWindow"): "window"})
        self.selection = None
        self.accepts = True
        macos = env.macos
        macos.focused_element = lambda pid: "field"
        macos.keyboard_pid = lambda: FINDER
        macos.activate_application = Mock(return_value=True)
        macos.same_element = lambda a, b: a is not None and a == b
        macos.parent = lambda element: next((key for key, kids in env.children.items() if element in kids), None)
        macos.set_attribute = Mock(return_value=0)
        macos.selected_range = lambda element: self.selection
        macos.select_range = Mock(side_effect=self.select)
        macos.press_key = Mock()
        macos.KEY_A, macos.COMMAND = 0, 1 << 20
        self.window = env.backend.foreground()

    def select(self, element, location, length):
        if not self.accepts:
            return -25205
        self.selection = (location, length)
        return 0

    def run_selection(self, text, selection, cursor=None):
        self.env.attrs[("field", "AXValue")] = text
        self.selection = (len(text.encode("utf-16-le")) // 2 if cursor is None else cursor, 0)
        snapshot = self.env.backend.prepare_dictation(self.window)
        return self.env.backend.select_text(snapshot, selection)

    def selected(self, text):
        start, length = self.selection
        units = text.encode("utf-16-le")
        return units[start * 2:(start + length) * 2].decode("utf-16-le")


class SelectTextTest(TextFieldFixture, unittest.TestCase):
    def test_trecho_falado_seleciona_o_mais_perto_antes_do_cursor(self):
        text = "Olá Maria, olá de novo"
        message = self.run_selection(text, ("text", "o la"))  # como o Whisper costuma entregar "olá"
        self.assertEqual(self.selection, (11, 3))
        self.assertEqual(message, "Selecionado: “olá”. Aparece 2 vezes; escolhi a mais perto antes do cursor.")

    def test_tudo_e_ultima_palavra(self):
        text = "Bom dia, Jarvis!"
        self.assertEqual(self.run_selection(text, ("all", None)), "Todo o texto do campo foi selecionado.")
        self.assertEqual(self.selected(text), text)
        self.run_selection(text, ("last_word", None))
        self.assertEqual(self.selected(text), "Jarvis")

    def test_emoji_conta_duas_posicoes_para_o_macos(self):
        text = "😀 olá"
        self.run_selection(text, ("text", "ola"))
        self.assertEqual(self.selection, (3, 3))
        self.assertEqual(self.selected(text), "olá")

    def test_trecho_ausente_ou_campo_vazio_nao_mexe_na_selecao(self):
        with self.assertRaisesRegex(self.env.module.ScreenError, "Não encontrei “xyz”"):
            self.run_selection("Bom dia", ("text", "xyz"))
        with self.assertRaisesRegex(self.env.module.ScreenError, "vazio"):
            self.run_selection("   ", ("all", None))
        self.env.macos.select_range.assert_not_called()

    def test_campo_que_recusa_a_selecao(self):
        self.accepts = False
        with self.assertRaisesRegex(self.env.module.ScreenError, "não deixa selecionar"):
            self.run_selection("Bom dia", ("text", "dia"))
        self.env.macos.press_key.assert_not_called()
        # "Tudo" tem o atalho do sistema como reserva, no campo já validado.
        self.run_selection("Bom dia", ("all", None))
        self.env.macos.press_key.assert_called_once_with(0, 1 << 20)


class ClipboardTest(TextFieldFixture, unittest.TestCase):
    """Mesmo campo; a área de transferência é um contador e um texto, como o NSPasteboard."""

    def setUp(self):
        super().setUp()
        self.clipboard = {"count": 1, "text": "anterior"}
        self.env.attrs[("field", "AXValue")] = "Bom dia, Jarvis!"
        self.env.attrs[("field", "AXSelectedText")] = "Jarvis"
        macos = self.env.macos
        macos.KEY_C, macos.KEY_X, macos.KEY_V = 8, 7, 9
        macos.clipboard_count = lambda: self.clipboard["count"]
        macos.clipboard_text = lambda: self.clipboard["text"]
        macos.wait_clipboard_change = lambda before, timeout=1.0: self.clipboard["count"] != before
        macos.press_key = Mock(side_effect=self.key)

    def key(self, keycode, flags=0, text=None):
        if keycode in (7, 8) and self.env.attrs[("field", "AXSelectedText")]:
            self.clipboard.update(count=self.clipboard["count"] + 1, text=self.env.attrs[("field", "AXSelectedText")])

    def snapshot(self):
        return self.env.backend.prepare_dictation(self.window)

    def test_copiar_confere_a_area_de_transferencia(self):
        self.assertEqual(self.env.backend.copy_text(self.snapshot()), "Copiado: “Jarvis”.")
        self.env.macos.press_key.assert_called_once_with(8, 1 << 20)

    def test_sem_selecao_avisa_em_vez_de_copiar(self):
        self.env.attrs[("field", "AXSelectedText")] = ""
        for action in (self.env.backend.copy_text, self.env.backend.cut_text):
            with self.assertRaisesRegex(self.env.module.ScreenError, "Nada selecionado"):
                action(self.snapshot())
        self.env.macos.press_key.assert_not_called()

    def test_aplicativo_que_ignora_o_atalho(self):
        self.env.macos.press_key = Mock()  # a tecla vai, mas nada é copiado
        with self.assertRaisesRegex(self.env.module.ScreenError, "não copiou"):
            self.env.backend.copy_text(self.snapshot())

    def test_recortar(self):
        self.assertIn("Recortado: “Jarvis”", self.env.backend.cut_text(self.snapshot()))
        self.env.macos.press_key.assert_called_once_with(7, 1 << 20)

    def test_colar_mostra_o_conteudo_e_so_cola_o_que_foi_mostrado(self):
        snapshot, preview = self.env.backend.prepare_paste(self.window)
        self.assertEqual(preview, "anterior")
        self.assertEqual(self.env.backend.paste_text(snapshot), "Texto colado no campo.")
        self.env.macos.press_key.assert_called_once_with(9, 1 << 20)
        snapshot, _ = self.env.backend.prepare_paste(self.window)
        self.clipboard["text"] = "outra coisa"  # copiado por outro app entre a pergunta e o "confirmar"
        with self.assertRaisesRegex(self.env.module.ScreenError, "mudou"):
            self.env.backend.paste_text(snapshot)
        self.assertEqual(self.env.macos.press_key.call_count, 1)

    def test_area_de_transferencia_vazia(self):
        self.clipboard["text"] = "   "
        with self.assertRaisesRegex(self.env.module.ScreenError, "não tem texto"):
            self.env.backend.prepare_paste(self.window)
