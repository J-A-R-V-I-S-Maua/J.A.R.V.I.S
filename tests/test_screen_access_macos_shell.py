"""Mesa e Dock no backend macOS, com uma árvore de acessibilidade simulada.

A árvore espelha o que a sonda encontrou num Mac: a mesa é uma rolagem pendurada direto no
Finder, o ícone de um PDF traz botões de página dentro, e o Dock tem separadores sem nome.
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
    return roles, names, frames, actions, children, attrs


class Environment:
    """Instala um `macos` falso e importa o backend por cima dele."""

    def __init__(self, case, frontmost=FINDER, front="desk", dock_running=True):
        self.roles, self.names, self.frames, self.actions, self.children, self.attrs = build_tree()
        self.frontmost, self.front, self.dock_running = frontmost, front, dock_running
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
