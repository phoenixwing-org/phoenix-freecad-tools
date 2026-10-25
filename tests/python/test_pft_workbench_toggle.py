# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

INIT_GUI = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools" / "pft_workbench_gui.py"


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
        self.accessible_name = ""
        self.accessible_description = ""

    def defaultAction(self): return self.action
    def toolButtonStyle(self): return self.style
    def setToolButtonStyle(self, style):
        self.style = style
        self.style_writes.append(style)
    def setAccessibleName(self, value): self.accessible_name = value
    def setAccessibleDescription(self, value): self.accessible_description = value


class Container:
    def __init__(self, buttons): self.buttons = buttons
    def findChildren(self, kind): return [button for button in self.buttons if isinstance(button, kind)]


class ToolBar:
    def __init__(self, name, actions):
        self.name, self.items, self.mutations = name, list(actions), []
    def objectName(self): return self.name
    def windowTitle(self): return self.name
    def actions(self): return list(self.items)
    def removeAction(self, action):
        self.mutations.append(("remove", action))
        self.items.remove(action)
    def insertAction(self, anchor, action):
        self.mutations.append(("insert", action))
        self.items.insert(self.items.index(anchor) if anchor is not None else len(self.items), action)


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
    def __init__(self): self.values, self.bools = {}, {}
    def GetString(self, key, default=""): return self.values.get(key, default)
    def SetString(self, key, value): self.values[key] = value
    def GetBool(self, key, default=False): return self.bools.get(key, default)
    def SetBool(self, key, value): self.bools[key] = value
    def GetStrings(self): return list(self.values)
    def GetBools(self): return list(self.bools)
    def RemString(self, key): self.values.pop(key, None)
    def RemBool(self, key): self.bools.pop(key, None)


class WorkbenchToggleTests(unittest.TestCase):
    def setUp(self):
        self.status = {"running": False, "stopping": False, "port": 9876}
        self.calls = []
        self.commands = {}
        self.actions = {}
        self.command_queries = []
        self.registered = []
        self.status_dialogs, self.confirmations, self.single_shots = [], [], []
        self.visual_calls = []
        self.confirm_stop = lambda: True
        self.window = types.SimpleNamespace(timers=[], buttons=[])
        self.window_properties = {}
        self.window.property = self.window_properties.get
        self.window.setProperty = lambda key, value: self.window_properties.__setitem__(key, value)
        self.window.findChildren = lambda kind: [button for button in self.window.buttons if isinstance(button, kind)]
        self.window.statusBar = lambda: types.SimpleNamespace(showMessage=lambda *args: None)
        self.window.findChild = lambda kind, name: next((timer for timer in self.window.timers if timer.name == name and not timer.deleted), None)
        self.preferences = Preferences()
        self.saved_parameter_scopes = []
        def save_parameters(scope):
            if scope != "User parameter":
                raise ValueError("Unexpected preference scope")
            self.saved_parameter_scopes.append(scope)
            return None
        self.app = types.SimpleNamespace(Console=types.SimpleNamespace(PrintMessage=lambda text: None, PrintError=lambda text: None), ParamGet=lambda group: self.preferences, ActiveDocument=types.SimpleNamespace(Name="Example"), saveParameter=save_parameters)

        class PftBridgeError(Exception):
            def __init__(self, message): self.message = message
        self.bridge = types.SimpleNamespace(
            get_status=lambda: dict(self.status),
            start_server=self.start,
            stop_server=self.stop,
            PftBridgeError=PftBridgeError,
        )
        self.gui = types.SimpleNamespace(
            addWorkbench=self.registered.append,
            addCommand=self.add_command,
            getMainWindow=lambda: self.window,
            Command=types.SimpleNamespace(get=self.get_command),
        )
        self.qt = types.SimpleNamespace(ToolButtonIconOnly="icons-only", ToolButtonTextBesideIcon="text-beside-icon", ToolButtonStyle=types.SimpleNamespace(ToolButtonIconOnly="icons-only", ToolButtonTextBesideIcon="text-beside-icon"))
        Timer.singleShot = staticmethod(lambda delay, callback: self.single_shots.append((delay, callback)))
        def confirm():
            self.confirmations.append(True)
            return self.confirm_stop()
        modules = {
            "FreeCAD": self.app, "FreeCADGui": self.gui,
            "pft_bridge": self.bridge,
            "pft_workbench_tools": types.SimpleNamespace(show_rename_dialog=lambda: None, show_properties_dialog=lambda: None, show_link_labels_dialog=lambda: None, show_desk_dialog=lambda: None, show_document_tools_dialog=lambda: None,
            show_chat_connection_dialog=lambda: None,
            run_axonometric_fit=lambda: self.visual_calls.append("axonometric_fit"),
            show_appearance_dialog=lambda: self.visual_calls.append("appearance"),
                show_mcp_status_dialog=lambda snapshot: self.status_dialogs.append(snapshot), confirm_mcp_stop=confirm),
            "PySide": types.SimpleNamespace(QtCore=types.SimpleNamespace(QTimer=Timer, Qt=self.qt), QtGui=types.SimpleNamespace(QIcon=lambda path: path), QtWidgets=types.SimpleNamespace(QToolButton=ToolButton)),
        }
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        initial_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), initial_path))
        sys.path.insert(0, str(INIT_GUI.parent))
        from pft_workbench_i18n import get_language, set_language
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

    def test_toolbar_orders_status_toggle_settings_before_unchanged_business_tools(self):
        workbench, command, action = self.initialize()
        toolbar = ["PhoenixTools_MCPStatus", "PhoenixTools_ToggleMCP", "PhoenixTools_Settings", "PhoenixTools_DocumentTools", "PhoenixTools_AxonometricFit", "PhoenixTools_Appearance", "PhoenixTools_NormalizeNames", "PhoenixTools_SyncLinkLabels", "PhoenixTools_EditPartProperties", "PhoenixTools_DeskTools", "PhoenixTools_ChatConnection"]
        expected = toolbar
        self.assertEqual(workbench.menu_commands, expected)
        self.assertEqual(workbench.toolbar_commands, toolbar)
        self.assertEqual(list(self.commands), expected)
        self.assertEqual(self.calls, [])
        self.assertEqual(action.text, "启动 MCP · 已停止")
        self.assertTrue(action.icon.endswith("mcp-start.svg"))
        self.assertTrue(action.enabled)
        self.assertTrue(command.IsActive())
        self.assertEqual(command._timer.interval, 500)
        self.assertEqual(len(self.window.timers), 1)
        self.assertEqual(self.single_shots, [])
        self.assertTrue(self.actions["PhoenixTools_MCPStatus"].icon.endswith("mcp-status-stopped.svg"))
        self.assertTrue(self.actions["PhoenixTools_Settings"].icon.endswith("gear.svg"))

    def test_all_commands_leave_native_transaction_ownership_to_their_panels(self):
        _, command, _ = self.initialize()
        for running in (False, True):
            self.status["running"] = running
            command.refresh_actions(force=True)
            for identifier, registered in self.commands.items():
                with self.subTest(identifier=identifier, running=running):
                    resources = registered.GetResources()
                    self.assertEqual(resources["CmdType"], "NoTransaction")
                    self.assertEqual(resources["MenuText"], self.actions[identifier].text)
                    self.assertEqual(resources["ToolTip"], self.actions[identifier].tooltip)
                    self.assertEqual(resources["Pixmap"], self.actions[identifier].icon)
        plain = self.namespace["_PftCommand"]("当前文档工具", "当前文档工具", lambda: None)
        self.assertEqual(plain.GetResources()["CmdType"], "NoTransaction")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.visual_calls, [])

    def install_toolbar_fixture(self, toolbars):
        widgets = sys.modules["PySide"].QtWidgets
        widgets.QToolBar = ToolBar
        self.window.findChildren = lambda kind: list(toolbars) if kind is ToolBar else [button for button in self.window.buttons if isinstance(button, kind)]

    def test_legacy_toolbar_order_is_fixed_without_changing_other_relative_order_or_menu(self):
        workbench, command, _ = self.initialize()
        controls = [self.actions[name] for name in command.TOOLBAR_COMMANDS[:3]]
        business = [self.actions[name] for name in command.TOOLBAR_ANCHOR_COMMANDS[3:]]
        first_custom, second_custom = Action(), Action()
        remaining = [business[2], first_custom, business[0], second_custom, business[1]]
        toolbar = ToolBar("Phoenix Tools", [business[2], first_custom, business[0], controls[1], controls[0], second_custom, controls[2], business[1]])
        menu_before = list(workbench.menu_commands)
        self.install_toolbar_fixture([toolbar])
        command._timer.callback()
        self.assertEqual(toolbar.items, controls + remaining)
        self.assertEqual(len(toolbar.mutations), 6)
        self.assertEqual(workbench.menu_commands, menu_before)
        self.assertEqual(self.calls, [])
        self.assertEqual(len([timer for timer in self.window.timers if not timer.deleted]), 1)
        for _ in range(3): command._timer.callback()
        self.assertEqual(len(toolbar.mutations), 6)

    def test_toolbar_matching_uses_real_action_identity_and_leaves_unrelated_bars_alone(self):
        _, command, _ = self.initialize()
        own = [self.actions[name] for name in command.TOOLBAR_COMMANDS]
        genuine = ToolBar("Phoenix Tools", own[3:] + own[:3])
        same_name = ToolBar("Phoenix Tools", [Action() for _ in own])
        other_name = ToolBar("Custom toolbar", own[3:] + own[:3])
        original_other = list(other_name.items)
        self.install_toolbar_fixture([same_name, other_name, genuine])
        # Commands can expose more than one QAction; the toolbar's real one
        # need not be the first wrapper returned by Gui.Command.
        self.gui.Command.get = lambda name: types.SimpleNamespace(getAction=lambda: [Action(), self.actions[name]])
        command._timer.callback()
        self.assertEqual(genuine.items, own)
        self.assertEqual(same_name.mutations, [])
        self.assertEqual(other_name.mutations, [])
        self.assertEqual(other_name.items, original_other)
        self.assertEqual(self.calls, [])

    def test_two_full_matching_toolbars_are_ambiguous_and_not_reordered(self):
        _, command, _ = self.initialize()
        own = [self.actions[name] for name in command.TOOLBAR_COMMANDS]
        toolbars = [ToolBar("Phoenix Tools", own[3:] + own[:3]) for _ in range(2)]
        self.install_toolbar_fixture(toolbars)
        command._timer.callback()
        self.assertTrue(all(not toolbar.mutations for toolbar in toolbars))

    def test_real_start_stop_and_asynchronous_completion_update_actions(self):
        _, command, action = self.initialize()
        command.Activated()
        self.assertEqual(self.calls, ["start"])
        self.assertEqual(action.text, "停止 MCP · 运行中")
        self.assertTrue(action.icon.endswith("mcp-stop.svg"))
        self.assertTrue(action.enabled)
        self.assertTrue(self.actions["PhoenixTools_MCPStatus"].icon.endswith("mcp-status-running.svg"))
        command.Activated()
        self.assertEqual(self.calls, ["start", "stop"])
        self.assertEqual(action.text, "MCP 正在停止")
        self.assertFalse(action.enabled)
        self.assertFalse(command.IsActive())
        self.assertTrue(self.actions["PhoenixTools_MCPStatus"].icon.endswith("mcp-status-stopped.svg"))
        self.assertIn("正在停止", self.actions["PhoenixTools_MCPStatus"].text)
        self.assertTrue(self.commands["PhoenixTools_MCPStatus"].IsActive())
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
        self.assertEqual(set(self.command_queries), set(self.commands))
        resources = command.GetResources()
        self.assertEqual(resources["MenuText"], "停止 MCP · 运行中")
        self.assertTrue(resources["Pixmap"].endswith("mcp-stop.svg"))

    def test_failed_start_keeps_stopped_state(self):
        _, command, action = self.initialize()
        self.bridge.start_server = lambda: (_ for _ in ()).throw(self.bridge.PftBridgeError("Unavailable"))
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

    def test_toolbar_defaults_business_icons_and_text_and_three_compact_controls(self):
        workbench, command, action = self.initialize()
        controls = {"PhoenixTools_MCPStatus", "PhoenixTools_ToggleMCP", "PhoenixTools_Settings"}
        for name, own_action in self.actions.items():
            button = next(button for button in self.window.buttons if button.action is own_action)
            self.assertEqual(button.style, "icons-only" if name in controls else "text-beside-icon")
            self.assertEqual(button.accessible_name, own_action.text)
            self.assertEqual(button.accessible_description, own_action.tooltip)
            self.assertTrue(Path(own_action.icon).is_file())
            self.assertTrue(own_action.icon.endswith(".svg"))
        self.assertEqual(action.icon_text, "启动 MCP · 已停止")
        self.assertIn("点击启动", action.tooltip)
        self.assertEqual(len(self.window.timers), 1)

    def test_toolbar_style_preference_changes_only_own_business_buttons(self):
        workbench, command, _ = self.initialize()
        unrelated = ToolButton(Action())
        unrelated.style = "text-only"
        self.window.buttons.append(unrelated)
        self.preferences.values["ToolbarButtonStyle"] = "icon"
        workbench.refresh_language()
        self.assertTrue(all(button.style == "icons-only" for button in self.window.buttons if button is not unrelated))
        self.assertEqual(unrelated.style, "text-only")
        self.assertEqual(unrelated.style_writes, [])
        self.preferences.values["ToolbarButtonStyle"] = "icon_text"
        command._timer.callback()
        for name, own_action in self.actions.items():
            button = next(button for button in self.window.buttons if button.action is own_action)
            self.assertEqual(button.style, "icons-only" if name in command.TOOLBAR_COMMANDS[:3] else "text-beside-icon")
        self.assertEqual(unrelated.style_writes, [])
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.window.timers), 1)

    def test_local_visual_commands_run_without_starting_mcp_and_follow_active_document(self):
        _, command, _ = self.initialize()
        for identifier in ("PhoenixTools_AxonometricFit", "PhoenixTools_Appearance"):
            self.assertTrue(self.commands[identifier].IsActive())
            self.commands[identifier].Activated()
        self.assertEqual(self.visual_calls, ["axonometric_fit", "appearance"])
        self.assertEqual(self.calls, [])
        self.app.ActiveDocument = None
        command._timer.callback()
        for identifier in ("PhoenixTools_AxonometricFit", "PhoenixTools_Appearance"):
            self.assertFalse(self.commands[identifier].IsActive())
            self.assertFalse(self.actions[identifier].enabled)
        self.assertTrue(self.actions["PhoenixTools_DocumentTools"].enabled)

    def test_qt5_associated_toolbar_is_scoped_by_exact_default_action(self):
        _, command, action = self.initialize()
        own = next(button for button in self.window.buttons if button.action is action)
        unrelated = next(button for button in self.window.buttons if button.action is not action)
        action.widgets = [Container([own, unrelated])]
        own.style = "text-beside-icon"
        self.window.findChildren = lambda kind: (_ for _ in ()).throw(AssertionError("must prefer associated widgets"))
        self.namespace["_PftMCPActionCommand"].refresh_actions(command, force=True)
        self.assertEqual(own.style, "icons-only")
        self.assertEqual(unrelated.style, "icons-only")

    def test_qt6_associated_objects_and_scoped_enum_hide_only_own_text(self):
        _, command, action = self.initialize()
        own = next(button for button in self.window.buttons if button.action is action)
        action.associatedWidgets = None
        action.objects = [own]
        del self.qt.ToolButtonIconOnly
        own.style = "text-beside-icon"
        command.refresh_actions(force=True)
        self.assertEqual(own.style, "icons-only")

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
        self.assertEqual(self.actions["PhoenixTools_MCPStatus"].text, "FreeCAD bridge: Running")
        self.assertEqual(self.actions["PhoenixTools_NormalizeNames"].text, "Normalize labels")
        self.assertEqual(self.actions["PhoenixTools_EditPartProperties"].text, "Part properties")
        self.assertEqual(self.actions["PhoenixTools_Settings"].text, "Settings")
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
        self.assertEqual(self.commands["PhoenixTools_Settings"].GetResources()["ToolTip"], "Choose the Phoenix Tools interface language and bridge startup options.")
        self.assertEqual(self.calls, [])

    def test_action_icons_are_neutral_gray_and_status_icons_are_colored_circles(self):
        import xml.etree.ElementTree as ET
        icon = ET.parse(INIT_GUI.parent / "Resources/icons/mcp-stop.svg").getroot()
        rect = icon.find("{http://www.w3.org/2000/svg}rect")
        self.assertIsNotNone(rect)
        self.assertEqual(rect.get("width"), rect.get("height"))
        self.assertEqual(rect.get("fill"), "#737373")
        self.assertIsNone(icon.find("{http://www.w3.org/2000/svg}circle"))
        start = ET.parse(INIT_GUI.parent / "Resources/icons/mcp-start.svg").getroot()
        self.assertEqual(start.find("{http://www.w3.org/2000/svg}path").get("fill"), "#737373")
        for filename, color in (("mcp-status-running.svg", "#16a34a"), ("mcp-status-stopped.svg", "#dc2626")):
            root = ET.parse(INIT_GUI.parent / "Resources/icons" / filename).getroot()
            self.assertEqual(root.find("{http://www.w3.org/2000/svg}circle").get("fill"), color)

    def test_status_click_displays_actual_snapshot_without_start_stop_or_confirmation(self):
        workbench, command, action = self.initialize()
        self.status.update(running=True, stopping=True, addon_version="fixture", protocol_version=1, host="127.0.0.1")
        self.commands["PhoenixTools_MCPStatus"].Activated()
        self.assertEqual(self.status_dialogs, [self.status])
        self.assertIsNot(self.status_dialogs[0], self.status)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.confirmations, [])

    def test_cancelled_stop_does_not_stop_and_start_never_asks(self):
        _, command, _ = self.initialize()
        self.confirm_stop = lambda: False
        command.Activated()
        self.assertEqual(self.calls, ["start"])
        self.assertEqual(self.confirmations, [])
        command.Activated()
        self.assertEqual(self.calls, ["start"])
        self.assertEqual(len(self.confirmations), 1)

    def test_confirmation_modal_race_never_restarts_stopped_or_stopping_bridge(self):
        _, command, _ = self.initialize()
        for stopping in (False, True):
            self.status.update(running=True, stopping=False)
            def change_status():
                self.status.update(running=False, stopping=stopping)
                return True
            self.confirm_stop = change_status
            command.Activated()
        self.assertEqual(self.calls, [])

    def test_legacy_false_preference_cannot_bypass_mandatory_stop_confirmation(self):
        _, command, _ = self.initialize()
        self.preferences.bools["ConfirmStopMCP"] = False
        self.status.update(running=True)
        self.confirm_stop = lambda: False
        command.Activated()
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.confirmations), 1)
        self.assertTrue(self.status["running"])
        self.confirm_stop = lambda: True
        command.Activated()
        self.assertEqual(self.calls, ["stop"])
        self.assertEqual(len(self.confirmations), 2)
        self.assertFalse(self.preferences.bools["ConfirmStopMCP"])

    def reload_gui(self):
        exec(compile(INIT_GUI.read_text(), str(INIT_GUI), "exec"), self.namespace)

    def test_auto_start_is_opt_in_delayed_and_not_repeated_on_reload_or_activation(self):
        self.preferences.bools["AutoStartMCP"] = True
        self.window_properties.clear()  # Simulate the next FreeCAD process/window.
        self.reload_gui()
        self.assertEqual(len(self.single_shots), 1)
        self.assertEqual(self.calls, [])
        self.reload_gui()
        self.assertEqual(len(self.single_shots), 1)
        self.single_shots[0][1]()
        self.assertEqual(self.calls, ["start"])
        workbench, command, _ = self.initialize()
        workbench.Activated()
        workbench.refresh_language()
        self.assertEqual(self.calls, ["start"])
        self.assertEqual(len(self.single_shots), 1)
        command.Activated()
        self.assertEqual(self.calls, ["start", "stop"])
        self.status.update(running=False, stopping=False)
        command._timer.callback()
        self.assertEqual(self.calls, ["start", "stop"])

    def test_enabling_auto_start_now_waits_until_next_freecad_startup(self):
        self.preferences.bools["AutoStartMCP"] = True
        self.reload_gui()
        self.assertEqual(self.single_shots, [])
        self.assertEqual(self.calls, [])

    def test_auto_start_failure_does_not_block_registration_or_retry(self):
        self.preferences.bools["AutoStartMCP"] = True
        self.window_properties.clear()
        self.bridge.start_server = lambda: (_ for _ in ()).throw(self.bridge.PftBridgeError("fixture"))
        self.reload_gui()
        self.assertEqual(len(self.single_shots), 1)
        self.single_shots[-1][1]()
        self.assertFalse(self.status["running"])
        self.reload_gui()
        self.assertEqual(len(self.single_shots), 1)
        self.assertGreaterEqual(len(self.registered), 3)

    def test_already_running_or_stopping_startup_does_not_schedule_start(self):
        self.preferences.bools["AutoStartMCP"] = True
        for running, stopping in ((True, False), (False, True), (True, True)):
            self.window_properties.clear()
            self.status.update(running=running, stopping=stopping)
            self.reload_gui()
        self.assertEqual(self.single_shots, [])
        self.assertEqual(self.calls, [])

    def test_stopping_wins_over_running_for_red_status_and_disabled_toggle(self):
        _, command, action = self.initialize()
        self.status.update(running=True, stopping=True)
        command._timer.callback()
        self.assertFalse(action.enabled)
        self.assertTrue(self.actions["PhoenixTools_MCPStatus"].icon.endswith("mcp-status-stopped.svg"))
        self.assertIn("正在停止", self.actions["PhoenixTools_MCPStatus"].text)

    def test_delayed_auto_start_rechecks_preference_state_and_manual_stop(self):
        self.preferences.bools["AutoStartMCP"] = True
        self.window_properties.clear()
        self.reload_gui()
        callback = self.single_shots[-1][1]
        self.preferences.bools["AutoStartMCP"] = False
        callback()
        self.assertEqual(self.calls, [])
        self.preferences.bools["AutoStartMCP"] = True
        self.status.update(running=True)
        callback()
        self.status.update(running=False, stopping=True)
        callback()
        self.assertEqual(self.calls, [])
        self.status.update(running=True, stopping=False)
        _, command, _ = self.initialize()
        command.Activated()
        self.status.update(running=False, stopping=False)
        callback()
        self.assertEqual(self.calls, ["stop"])

    def test_confirmed_stop_race_also_suppresses_pending_auto_start_callback(self):
        self.preferences.bools["AutoStartMCP"] = True
        self.window_properties.clear()
        self.reload_gui()
        callback = self.single_shots[-1][1]
        _, command, _ = self.initialize()
        command.Activated()  # Manual start before the delayed startup callback.
        def external_stop_during_confirmation():
            self.status.update(running=False, stopping=False)
            return True
        self.confirm_stop = external_stop_during_confirmation
        command.Activated()
        callback()
        self.assertEqual(self.calls, ["start"])
        self.assertTrue(self.window_properties["PhoenixTools_AutoStartSuppressed"])


if __name__ == "__main__":
    unittest.main()
