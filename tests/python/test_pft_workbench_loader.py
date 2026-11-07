# SPDX-License-Identifier: Apache-2.0
"""Exercise FreeCAD's separate loader globals/locals and missing-__file__ execution."""
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
BOOTSTRAP = ADDON / "InitGui.py"


def host(registered, **options):
    commands = {}
    preferences = types.SimpleNamespace(GetString=lambda key, default="": default)
    app = types.SimpleNamespace(ParamGet=lambda path: preferences)
    gui = types.SimpleNamespace(
        addWorkbench=registered.append,
        addCommand=lambda identifier, command: commands.setdefault(identifier, command),
        Command=types.SimpleNamespace(get=lambda identifier: commands.get(identifier)),
        addWorkbenchManipulator=lambda manipulator: None,
        removeWorkbenchManipulator=lambda manipulator: None,
        **options)
    tools = types.SimpleNamespace(**{name: lambda: None for name in (
        "show_document_tools_dialog", "run_axonometric_fit", "show_appearance_dialog",
        "show_rename_dialog", "show_link_labels_dialog", "show_properties_dialog",
        "show_desk_dialog", "show_chat_connection_dialog", "show_help_dialog")})
    return app, gui, tools


class WorkbenchBootstrapTests(unittest.TestCase):
    def test_loader_registers_in_regular_module_for_all_exec_namespace_variants(self):
        for separate in (False, True):
            for with_file in (False, True):
                with self.subTest(separate=separate, with_file=with_file):
                    registered = []
                    base = type("WorkbenchBase", (), {})
                    app, gui, tools = host(registered)
                    bridge = types.SimpleNamespace(get_status=lambda: {"running": False, "stopping": False})
                    initial_path = list(sys.path)
                    global_namespace = {"__name__": "FreeCADLoaderTest", "sentinel": object()}
                    local_namespace = {} if separate else global_namespace
                    local_namespace["Workbench"] = base
                    if with_file:
                        local_namespace["__file__"] = str(BOOTSTRAP)
                    try:
                        with patch.dict(sys.modules, {"FreeCAD": app, "FreeCADGui": gui, "pft_bridge": bridge, "pft_workbench_tools": tools}):
                            exec(compile(BOOTSTRAP.read_text(), str(BOOTSTRAP), "exec"), global_namespace, local_namespace)
                            module = sys.modules["pft_workbench_gui"]
                            self.assertEqual(module._addon_dir, str(ADDON))
                            self.assertEqual(module.PftWorkbench.__module__, "pft_workbench_gui")
                            self.assertIs(module.App, app)
                            self.assertIs(module.Gui, gui)
                            self.assertIs(module._workbench_base, base)
                            self.assertEqual(len(registered), 1)
                            self.assertTrue(registered[0].Icon.endswith("Resources/icons/phoenix-wing-mark.svg"))
                            self.assertEqual(module._PftToggleMCPCommand().GetResources()["MenuText"], "启动 MCP · 已停止")
                            self.assertEqual(registered[0].GetClassName(), "Gui::PythonWorkbench")
                            self.assertNotIn("_addon_dir", global_namespace)
                            self.assertNotIn("App", global_namespace)
                    finally:
                        sys.path[:] = initial_path

    def test_gui_workbench_base_takes_precedence_when_available(self):
        preferred = type("GuiWorkbenchBase", (), {})
        fallback = type("FallbackWorkbenchBase", (), {})
        registered = []
        app, gui, tools = host(registered, Workbench=preferred)
        initial_path = list(sys.path)
        try:
            with patch.dict(sys.modules, {"FreeCAD": app, "FreeCADGui": gui, "pft_bridge": types.SimpleNamespace(), "pft_workbench_tools": tools}):
                exec(compile(BOOTSTRAP.read_text(), str(BOOTSTRAP), "exec"), {"__name__": "Loader"}, {"Workbench": fallback})
                self.assertIsInstance(registered[0], preferred)
                self.assertNotIsInstance(registered[0], fallback)
        finally:
            sys.path[:] = initial_path


if __name__ == "__main__":
    unittest.main()
