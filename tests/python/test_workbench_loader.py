# SPDX-License-Identifier: Apache-2.0
"""Exercise FreeCAD's separate loader globals/locals and missing-__file__ execution."""
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADMCP"
BOOTSTRAP = ADDON / "InitGui.py"


class WorkbenchBootstrapTests(unittest.TestCase):
    def test_loader_registers_in_regular_module_for_all_exec_namespace_variants(self):
        for separate in (False, True):
            for with_file in (False, True):
                with self.subTest(separate=separate, with_file=with_file):
                    registered = []
                    base = type("WorkbenchBase", (), {})
                    app = types.SimpleNamespace()
                    gui = types.SimpleNamespace(addWorkbench=registered.append)
                    bridge = types.SimpleNamespace(get_status=lambda: {"running": False, "stopping": False})
                    initial_path = list(sys.path)
                    global_namespace = {"__name__": "FreeCADLoaderTest", "sentinel": object()}
                    local_namespace = {} if separate else global_namespace
                    local_namespace["Workbench"] = base
                    if with_file:
                        local_namespace["__file__"] = str(BOOTSTRAP)
                    try:
                        with patch.dict(sys.modules, {"FreeCAD": app, "FreeCADGui": gui, "phoenix_freecad_bridge": bridge, "phoenix_workbench_tools": types.SimpleNamespace()}):
                            exec(compile(BOOTSTRAP.read_text(), str(BOOTSTRAP), "exec"), global_namespace, local_namespace)
                            module = sys.modules["phoenix_workbench_gui"]
                            self.assertEqual(module._addon_dir, str(ADDON))
                            self.assertEqual(module.PhoenixFreeCADMCPWorkbench.__module__, "phoenix_workbench_gui")
                            self.assertIs(module.App, app)
                            self.assertIs(module.Gui, gui)
                            self.assertIs(module._workbench_base, base)
                            self.assertEqual(len(registered), 1)
                            self.assertTrue(registered[0].Icon.endswith("Resources/icons/phoenix-wing-mark.svg"))
                            self.assertEqual(module._ToggleMCPCommand().GetResources()["MenuText"], "启动 MCP · 已停止")
                            self.assertEqual(registered[0].GetClassName(), "Gui::PythonWorkbench")
                            self.assertNotIn("_addon_dir", global_namespace)
                            self.assertNotIn("App", global_namespace)
                    finally:
                        sys.path[:] = initial_path

    def test_gui_workbench_base_takes_precedence_when_available(self):
        preferred = type("GuiWorkbenchBase", (), {})
        fallback = type("FallbackWorkbenchBase", (), {})
        registered = []
        gui = types.SimpleNamespace(Workbench=preferred, addWorkbench=registered.append)
        initial_path = list(sys.path)
        try:
            with patch.dict(sys.modules, {"FreeCAD": types.SimpleNamespace(), "FreeCADGui": gui, "phoenix_freecad_bridge": types.SimpleNamespace(), "phoenix_workbench_tools": types.SimpleNamespace()}):
                exec(compile(BOOTSTRAP.read_text(), str(BOOTSTRAP), "exec"), {"__name__": "Loader"}, {"Workbench": fallback})
                self.assertIsInstance(registered[0], preferred)
                self.assertNotIsInstance(registered[0], fallback)
        finally:
            sys.path[:] = initial_path


if __name__ == "__main__":
    unittest.main()
