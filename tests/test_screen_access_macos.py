"""Contrato do backend AX validado com APIs simuladas fora do macOS."""
import importlib
import sys
import unittest
from types import ModuleType
from unittest.mock import Mock, patch


class MacOSBackendTest(unittest.TestCase):
    def test_scan_focus_press_and_permission_failure(self):
        macos = ModuleType("screen_access.macos")
        services = ModuleType("ApplicationServices")
        macos.frontmost_application = Mock(return_value=(99999, "Teste"))
        macos.front_window = Mock(return_value="window")
        macos.window_title = Mock(return_value="Teste")
        macos.element_frame = Mock(side_effect=lambda element: (0, 0, 800, 600) if element == "window" else (10, 10, 100, 30))
        macos.element_role = Mock(side_effect=lambda element: "AXTextField" if element == "field" else "AXButton")
        macos.element_name = Mock(side_effect=lambda element: element)
        macos.read_attribute = Mock(side_effect=lambda element, attribute: 1 if attribute == "AXWindowNumber" else True)
        macos.enable_web_accessibility = Mock()
        macos.pid_for_bundle = Mock(return_value=None)  # o aplicativo da frente não é o Finder
        macos.walk_with_clip = Mock(return_value=iter([(0, "button", (0, 0, 800, 600)), (1, "field", (0, 0, 800, 600))]))
        macos.TEXT_INPUT_ROLES = {"AXTextField"}
        macos.ACTION_ROLES = {"AXButton"}
        macos.can_press = Mock(return_value=True)
        macos.is_visible = Mock(return_value=True)
        macos.press = Mock(return_value=0)
        macos.describe_error = Mock(return_value="recusado")
        macos.has_accessibility_permission = Mock(return_value=True)
        services.AXUIElementIsAttributeSettable = Mock(return_value=(0, True))
        services.AXUIElementSetAttributeValue = Mock(return_value=0)
        # Evitar que a importação simulada permaneça como backend real no processo.
        sys.modules.pop("screen_access.macos_backend", None)
        with patch.dict(sys.modules, {"screen_access.macos": macos,
                                     "ApplicationServices": services}):
            module = importlib.import_module("screen_access.macos_backend")
            with module.MacOSBackend() as backend:
                window = backend.foreground()
                snapshot = backend.scan(window)
                self.assertEqual([item.action for item in snapshot.elements], ["press", "focus"])
                backend.activate(snapshot, 1)
                macos.press.assert_called_once_with("button")
                backend.activate(snapshot, 2)
                services.AXUIElementSetAttributeValue.assert_called_once_with("field", "AXFocused", True)
                macos.is_visible.return_value = False
                with self.assertRaises(module.ScreenError):
                    backend.activate(snapshot, 1)
            macos.has_accessibility_permission.return_value = False
            with self.assertRaises(module.ScreenError):
                with module.MacOSBackend():
                    pass
        sys.modules.pop("screen_access.macos_backend", None)


@unittest.skipUnless(sys.platform == "darwin", "usa o AppKit de verdade")
class FrontmostApplicationTest(unittest.TestCase):
    def test_app_da_frente_vem_da_acessibilidade_e_nao_fica_preso(self):
        # O NSWorkspace só se atualiza num processo que trata eventos do macOS; num script, ou no
        # "python -m screen_access", ficaria preso no app que estava na frente ao começar.
        import screen_access

        loaded = "screen_access.macos" in sys.modules
        macos = importlib.import_module("screen_access.macos")
        if not loaded:
            # Os outros testes trocam este módulo por um dublê; o verdadeiro não pode ficar no pacote.
            self.addCleanup(sys.modules.pop, "screen_access.macos", None)
            self.addCleanup(lambda: hasattr(screen_access, "macos") and delattr(screen_access, "macos"))
        app = Mock(localizedName=Mock(return_value="Finder"))
        with patch.object(macos, "keyboard_pid", return_value=635), \
                patch.object(macos, "NSRunningApplication") as running:
            running.runningApplicationWithProcessIdentifier_.return_value = app
            self.assertEqual(macos.frontmost_application(), (635, "Finder"))
        running.runningApplicationWithProcessIdentifier_.assert_called_once_with(635)
