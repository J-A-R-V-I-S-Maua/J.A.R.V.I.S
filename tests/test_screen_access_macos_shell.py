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
        macos.can_press = lambda e: "AXPress" in self.actions.get(e, ())
        macos.is_visible = Mock(return_value=True)
        macos.TEXT_INPUT_ROLES = {"AXTextField"}
        macos.ACTION_ROLES = {"AXButton"}
        macos.press = Mock(return_value=0)
        macos.perform_action = Mock(return_value=0)
        macos.describe_error = Mock(return_value="recusado")
        self.macos = macos
        permissions = ModuleType("screen_access.permissions")
        permissions.has_accessibility_permission = Mock(return_value=True)
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
            patch.dict(sys.modules, {"screen_access.macos": macos, "screen_access.permissions": permissions,
                                     "ApplicationServices": services}),
            patch.object(screen_access, "macos", macos, create=True),
            patch.object(screen_access, "permissions", permissions, create=True),
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
        self.assertTrue(env.backend.mission_control(True))
        self.assertEqual(calls, [["/usr/bin/open", "-b", "com.apple.exposelauncher"]])
        self.assertTrue(env.backend.foreground().identity.startswith("missioncontrol:"))

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
