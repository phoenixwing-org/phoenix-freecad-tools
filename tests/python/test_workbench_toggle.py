# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

INIT_GUI = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADMCP" / "phoenix_workbench_gui.py"


class Action:
    def __init__(self):
        self.text = ""
        self.icon = ""
        self.enabled = True
        self.icon_text = ""
        self.widgets = []
        self.objects = []

    def setText(self, value): self.text = value
    def setIconText(self, value): self.icon_text = value
    def associatedWidgets(self): return self.widgets
    def associatedObjects(self): return self.objects
    def setToolTip(self, value): self.tooltip = value
    def setStatusTip(self, value): self.status_tip = value
    def setIcon(self, value): self.icon = value
    def setEnabled(self, value): self.enabled = value


class ToolButton:
    def __init__(self, action):
        self.action = action
        self.style = "icons-only"
        self.style_writes = []

    def defaultAction(self): return self.action
    def toolButtonStyle(self): return self.style
    def setToolButtonStyle(self, style):
        self.style = style
        self.style_writes.append(style)


class Container:
    def __init__(self, buttons): self.buttons = buttons
    def findChildren(self, kind): return [button for button in self.buttons if isinstance(button, kind)]


class Timer:
    def __init__(self, parent):
        self.parent = parent
        self.timeout = types.SimpleNamespace(connect=lambda callback: setattr(self, "callback", callback))
        self.stopped = False
        self.deleted = False
        parent.timers.append(self)

    def setObjectName(self, name): self.name = name
    def setInterval(self, value): self.interval = value
    def start(self): self.stopped = False
    def stop(self): self.stopped = True
    def deleteLater(self): self.deleted = True


class Preferences:
    def __init__(self): self.values = {}
    def GetString(self, key, default=""): return self.values.get(key, default)
    def SetString(self, key, value): self.values[key] = value


class WorkbenchToggleTests(unittest.TestCase):
    def setUp(self):
        self.status = {"running": False, "stopping": False, "port": 9876}
        self.calls = []
        self.commands = {}
        self.actions = {}
        self.command_queries = []
        self.registered = []
        self.window = types.SimpleNamespace(timers=[], buttons=[])
        self.window.findChildren = lambda kind: [button for button in self.window.buttons if isinstance(button, kind)]
        self.window.statusBar = lambda: types.SimpleNamespace(showMessage=lambda *args: None)
        self.window.findChild = lambda kind, name: next((timer for timer in self.window.timers if timer.name == name and not timer.deleted), None)
        self.preferences = Preferences()
        self.app = types.SimpleNamespace(Console=types.SimpleNamespace(PrintMessage=lambda text: None, PrintError=lambda text: None), ParamGet=lambda group: self.preferences)

        class BridgeError(Exception):
            def __init__(self, message): self.message = message
        self.bridge = types.SimpleNamespace(
            get_status=lambda: dict(self.status),
            start_server=self.start,
            stop_server=self.stop,
            BridgeError=BridgeError,
        )
        self.gui = types.SimpleNamespace(
            addWorkbench=self.registered.append,
            addCommand=self.add_command,
            getMainWindow=lambda: self.window,
            Command=types.SimpleNamespace(get=self.get_command),
        )
        self.qt = types.SimpleNamespace(ToolButtonTextBesideIcon="text-beside-icon", ToolButtonStyle=types.SimpleNamespace(ToolButtonTextBesideIcon="text-beside-icon"))
        modules = {
            "FreeCAD": self.app, "FreeCADGui": self.gui,
            "phoenix_freecad_bridge": self.bridge,
            "phoenix_workbench_tools": types.SimpleNamespace(show_rename_dialog=lambda: None, show_properties_dialog=lambda: None, show_link_labels_dialog=lambda: None),
            "PySide": types.SimpleNamespace(QtCore=types.SimpleNamespace(QTimer=Timer, Qt=self.qt), QtGui=types.SimpleNamespace(QIcon=lambda path: path), QtWidgets=types.SimpleNamespace(QToolButton=ToolButton)),
        }
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        initial_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), initial_path))
        sys.path.insert(0, str(INIT_GUI.parent))
        from phoenix_workbench_i18n import get_language, set_language
        self.addCleanup(set_language, get_language())
        set_language("zh")

        class Workbench:
            def appendMenu(self, name, commands): self.menu_commands = list(commands)
            def appendToolbar(self, name, commands): self.toolbar_commands = list(commands)
        self.namespace = {"__file__": str(INIT_GUI), "__name__": "PhoenixToggleUITest", "_bootstrap_workbench_base": Workbench}
        exec(compile(INIT_GUI.read_text(), str(INIT_GUI), "exec"), self.namespace)

    def add_command(self, name, command):
        self.commands[name] = command
        self.actions[name] = Action()
        self.window.buttons.append(ToolButton(self.actions[name]))

    def get_command(self, name):
        self.command_queries.append(name)
        return types.SimpleNamespace(getAction=lambda: [self.actions[name]])

    def start(self):
        self.calls.append("start")
        self.status.update(running=True, stopping=False)
        return dict(self.status)

    def stop(self):
        self.calls.append("stop")
        self.status.update(running=False, stopping=True)
        return dict(self.status)

    def initialize(self):
        workbench = self.registered[0]
        workbench.Initialize()
        return workbench, workbench._mcp_command, self.actions["PhoenixTools_ToggleMCP"]

    def test_registration_keeps_only_one_mcp_command_without_starting_service(self):
        workbench, command, action = self.initialize()
        toolbar = ["PhoenixTools_NormalizeNames", "PhoenixTools_SyncLinkLabels", "PhoenixTools_EditPartProperties", "PhoenixTools_ToggleMCP"]
        expected = toolbar + ["PhoenixTools_Settings"]
        self.assertEqual(workbench.menu_commands, expected)
        self.assertEqual(workbench.toolbar_commands, toolbar)
        self.assertEqual(list(self.commands), expected)
        self.assertEqual(self.calls, [])
        self.assertEqual(action.text, "启动 MCP · 已停止")
        self.assertTrue(action.icon.endswith("mcp-start.svg"))
        self.assertTrue(action.enabled)
        self.assertTrue(command.IsActive())
        self.assertEqual(command._timer.interval, 500)

    def test_real_start_stop_and_asynchronous_completion_update_actions(self):
        _, command, action = self.initialize()
        command.Activated()
        self.assertEqual(self.calls, ["start"])
        self.assertEqual(action.text, "停止 MCP · 运行中")
        self.assertTrue(action.icon.endswith("mcp-stop.svg"))
        self.assertTrue(action.enabled)
        command.Activated()
        self.assertEqual(self.calls, ["start", "stop"])
        self.assertEqual(action.text, "MCP 正在停止")
        self.assertFalse(action.enabled)
        self.assertFalse(command.IsActive())
        command.Activated()
        self.assertEqual(self.calls, ["start", "stop"])
        self.status["stopping"] = False
        command._timer.callback()
        self.assertEqual(action.text, "启动 MCP · 已停止")
        self.assertTrue(action.icon.endswith("mcp-start.svg"))
        self.assertTrue(action.enabled)

    def test_external_state_change_is_reflected_without_mutating_service(self):
        _, command, action = self.initialize()
        self.status["running"] = True
        command._timer.callback()
        self.assertEqual(action.text, "停止 MCP · 运行中")
        self.assertEqual(self.calls, [])
        self.assertEqual(set(self.command_queries), {"PhoenixTools_ToggleMCP"})
        resources = command.GetResources()
        self.assertEqual(resources["MenuText"], "停止 MCP · 运行中")
        self.assertTrue(resources["Pixmap"].endswith("mcp-stop.svg"))

    def test_failed_start_keeps_stopped_state(self):
        _, command, action = self.initialize()
        self.bridge.start_server = lambda: (_ for _ in ()).throw(self.bridge.BridgeError("Unavailable"))
        command.Activated()
        self.assertEqual(action.text, "启动 MCP · 已停止")
        self.assertTrue(action.icon.endswith("mcp-start.svg"))
        self.assertFalse(self.status["running"])

    def test_reinitialization_retires_only_own_timer(self):
        workbench, command, _ = self.initialize()
        previous = command._timer
        workbench.Initialize()
        self.assertTrue(previous.stopped)
        self.assertTrue(previous.deleted)
        self.assertIsNot(workbench._mcp_command._timer, previous)
        self.assertEqual(len([timer for timer in self.window.timers if not timer.deleted]), 1)

    def test_visible_text_is_enforced_only_for_this_command_even_when_state_is_unchanged(self):
        _, command, action = self.initialize()
        own = next(button for button in self.window.buttons if button.action is action)
        others = [button for button in self.window.buttons if button.action is not action]
        self.assertEqual(own.style, "text-beside-icon")
        self.assertEqual(action.icon_text, "启动 MCP · 已停止")
        self.assertTrue(all(button.style == "icons-only" and not button.style_writes for button in others))
        own.style = "icons-only"
        command._timer.callback()
        self.assertEqual(own.style, "text-beside-icon")
        self.assertTrue(all(not button.style_writes for button in others))
        self.assertIn("已停止", action.tooltip)
        self.assertIn("点击启动", action.tooltip)

    def test_qt5_associated_toolbar_is_scoped_by_exact_default_action(self):
        _, command, action = self.initialize()
        own = next(button for button in self.window.buttons if button.action is action)
        unrelated = next(button for button in self.window.buttons if button.action is not action)
        action.widgets = [Container([own, unrelated])]
        own.style = "icons-only"
        self.window.findChildren = lambda kind: (_ for _ in ()).throw(AssertionError("must prefer associated widgets"))
        command.refresh_actions(force=True)
        self.assertEqual(own.style, "text-beside-icon")
        self.assertEqual(unrelated.style, "icons-only")

    def test_qt6_associated_objects_and_scoped_enum_keep_text_visible(self):
        _, command, action = self.initialize()
        own = next(button for button in self.window.buttons if button.action is action)
        action.associatedWidgets = None
        action.objects = [own]
        del self.qt.ToolButtonTextBesideIcon
        own.style = "icons-only"
        command.refresh_actions(force=True)
        self.assertEqual(own.style, "text-beside-icon")

    def test_language_change_refreshes_existing_actions_without_service_or_timer_changes(self):
        workbench, command, action = self.initialize()
        self.status["running"] = True
        command._timer.callback()
        before_commands = dict(self.commands)
        timer = command._timer
        self.namespace["settings"].save_language(self.app, "en")
        workbench.refresh_language()
        self.assertEqual(action.text, "Stop MCP · Running")
        self.assertEqual(action.icon_text, "Stop MCP · Running")
        self.assertEqual(self.actions["PhoenixTools_NormalizeNames"].text, "Normalize labels")
        self.assertEqual(self.actions["PhoenixTools_EditPartProperties"].text, "Edit part properties")
        self.assertEqual(self.actions["PhoenixTools_Settings"].text, "Settings / 设置")
        self.assertIn("Click to stop", action.tooltip)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.commands, before_commands)
        self.assertIs(command._timer, timer)
        self.assertEqual(len([item for item in self.window.timers if not item.deleted]), 1)
        self.assertTrue(self.status["running"])
        self.namespace["settings"].save_language(self.app, "zh")
        workbench.refresh_language()
        self.assertEqual(action.text, "停止 MCP · 运行中")
        self.assertEqual(self.actions["PhoenixTools_NormalizeNames"].text, "命名修正")
        self.assertEqual(self.commands["PhoenixTools_NormalizeNames"].title, "命名修正")
        self.assertEqual(self.calls, [])

    def test_persisted_language_is_loaded_without_starting_mcp(self):
        self.preferences.values["Language"] = "en"
        workbench, command, action = self.initialize()
        self.assertEqual(action.text, "Start MCP · Stopped")
        self.assertEqual(self.commands["PhoenixTools_NormalizeNames"].GetResources()["MenuText"], "Normalize labels")
        self.assertEqual(self.commands["PhoenixTools_Settings"].GetResources()["ToolTip"], "Choose the Phoenix Tools interface language.")
        self.assertEqual(self.calls, [])

    def test_stop_svg_is_a_red_square_instead_of_a_circle(self):
        import xml.etree.ElementTree as ET
        icon = ET.parse(INIT_GUI.parent / "Resources/icons/mcp-stop.svg").getroot()
        rect = icon.find("{http://www.w3.org/2000/svg}rect")
        self.assertIsNotNone(rect)
        self.assertEqual(rect.get("width"), rect.get("height"))
        self.assertEqual(rect.get("fill"), "#dc2626")
        self.assertIsNone(icon.find("{http://www.w3.org/2000/svg}circle"))


if __name__ == "__main__":
    unittest.main()
