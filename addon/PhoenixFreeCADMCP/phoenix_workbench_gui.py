# SPDX-License-Identifier: Apache-2.0
"""FreeCAD commands and MCP state presentation in a normal, isolated module namespace."""

import os
import sys

import FreeCAD as App
import FreeCADGui as Gui

_addon_dir = os.path.dirname(os.path.abspath(__file__))
_workbench_base = getattr(Gui, "Workbench", None) or globals().get("_bootstrap_workbench_base")
if _workbench_base is None:
    raise RuntimeError("FreeCAD workbench base class is unavailable.")

import phoenix_freecad_bridge
import phoenix_workbench_tools


def _feedback(message, error=False):
    printer = App.Console.PrintError if error else App.Console.PrintMessage
    printer("[Phoenix Tools] " + message + "\n")
    Gui.getMainWindow().statusBar().showMessage(message, 12000)


class _Command:
    def __init__(self, title, tooltip, callback):
        self.title = title
        self.tooltip = tooltip
        self.callback = callback

    def GetResources(self):
        return {"MenuText": self.title, "ToolTip": self.tooltip}

    def IsActive(self):
        return True

    def Activated(self):
        try:
            self.callback()
        except phoenix_freecad_bridge.BridgeError as error:
            _feedback(error.message, error=True)
        except Exception as error:
            _feedback("操作失败（%s）。" % type(error).__name__, error=True)


class _ToggleMCPCommand:
    IDENTIFIER = "PhoenixTools_ToggleMCP"
    TIMER_NAME = "PhoenixTools_MCPStateTimer"

    def __init__(self):
        self._timer = None
        self._last_state = None

    def _presentation(self):
        status = phoenix_freecad_bridge.get_status()
        stopping = bool(status.get("stopping"))
        running = bool(status.get("running"))
        if stopping:
            title = "MCP 正在停止"
            tooltip = "当前 MCP 正在后台停止；完成后可再次启动。"
        elif running:
            title = "停止 MCP · 运行中"
            tooltip = "当前 MCP 运行中；点击停止本机 Phoenix MCP 服务。"
        else:
            title = "启动 MCP · 已停止"
            tooltip = "当前 MCP 已停止；点击启动本机服务（127.0.0.1:9876）。"
        icon = "mcp-stop.svg" if running or stopping else "mcp-start.svg"
        return (running, stopping), {
            "MenuText": title,
            "ToolTip": tooltip,
            "Pixmap": os.path.join(_addon_dir, "Resources", "icons", icon),
        }

    def GetResources(self):
        return self._presentation()[1]

    def IsActive(self):
        return not bool(phoenix_freecad_bridge.get_status().get("stopping"))

    def _style_own_toolbuttons(self, action):
        """Keep text visible only on buttons whose default action is this command."""
        from PySide import QtCore
        try:
            from PySide import QtWidgets
        except ImportError:
            from PySide import QtGui as QtWidgets
        style = getattr(QtCore.Qt, "ToolButtonTextBesideIcon", None)
        if style is None:
            style = QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        containers = []
        for name in ("associatedWidgets", "associatedObjects"):
            associated = getattr(action, name, None)
            if callable(associated):
                containers.extend(associated())
        # QToolBar may be associated with the QAction while its generated
        # QToolButton is only a child. The fallback still requires the exact
        # defaultAction and never changes toolbar/global presentation settings.
        seen = set()
        matched = False
        for candidates_from in (containers, [Gui.getMainWindow()]):
            for container in candidates_from:
                candidates = [container] if isinstance(container, QtWidgets.QToolButton) else []
                if callable(getattr(container, "findChildren", None)):
                    candidates.extend(container.findChildren(QtWidgets.QToolButton))
                for button in candidates:
                    if id(button) in seen:
                        continue
                    seen.add(id(button))
                    if button.defaultAction() is not action and button.defaultAction() != action:
                        continue
                    matched = True
                    if button.toolButtonStyle() != style:
                        button.setToolButtonStyle(style)
            if matched:
                break

    def refresh_actions(self, force=False):
        """Read the real bridge state; update only this command and its buttons.

        Gui.Command.get(...).getAction() is used by FreeCAD's bundled Draft
        observer. associatedWidgets is Qt 5; associatedObjects covers Qt 6.
        """
        try:
            state, resources = self._presentation()
            from PySide import QtGui
            actions = Gui.Command.get(self.IDENTIFIER).getAction()
            if hasattr(actions, "setText"):
                actions = [actions]
            if not actions:
                return
            if state != self._last_state or force:
                icon = QtGui.QIcon(resources["Pixmap"])
                for action in actions:
                    action.setText(resources["MenuText"])
                    if callable(getattr(action, "setIconText", None)):
                        action.setIconText(resources["MenuText"])
                    action.setToolTip(resources["ToolTip"])
                    action.setStatusTip(resources["ToolTip"])
                    action.setIcon(icon)
                    action.setEnabled(not state[1])
                self._last_state = state
            # Buttons can be created later or inherit an icon-only toolbar
            # preference. Enforce this command's visible text on every tick.
            for action in actions:
                self._style_own_toolbuttons(action)
        except (AttributeError, RuntimeError, KeyError):
            return

    def start_polling(self):
        from PySide import QtCore
        window = Gui.getMainWindow()
        previous = window.findChild(QtCore.QTimer, self.TIMER_NAME)
        if previous is not None:
            previous.stop()
            previous.deleteLater()
        self._timer = QtCore.QTimer(window)
        self._timer.setObjectName(self.TIMER_NAME)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh_actions)
        self._timer.start()
        self.refresh_actions(force=True)

    def stop_polling(self):
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None

    def Activated(self):
        try:
            status = phoenix_freecad_bridge.get_status()
            if status.get("stopping"):
                return
            # stop_server schedules shutdown on its own thread and returns
            # immediately. The timer follows its real completion state.
            if status.get("running"):
                result = phoenix_freecad_bridge.stop_server()
            else:
                result = phoenix_freecad_bridge.start_server()
            if result.get("stopping"):
                _feedback("MCP 服务正在停止。")
            elif result.get("running"):
                _feedback("MCP 服务已启动：127.0.0.1:%s。" % result["port"])
            else:
                _feedback("MCP 服务已停止。")
        except phoenix_freecad_bridge.BridgeError as error:
            _feedback(error.message, error=True)
        except Exception as error:
            _feedback("MCP 操作失败（%s）。" % type(error).__name__, error=True)
        finally:
            self.refresh_actions(force=True)


class PhoenixFreeCADMCPWorkbench(_workbench_base):
    Icon = os.path.join(_addon_dir, "Resources", "icons", "phoenix-wing-mark.svg")
    MenuText = "Phoenix Tools"
    ToolTip = "Phoenix 的 FreeCAD 工具入口；通过 MCP 提供聊天控制能力。"

    def Initialize(self):
        definitions = [
            ("PhoenixTools_NormalizeNames", "命名修正", "预览所选对象的 Label 命名修正，确认后可撤销。", phoenix_workbench_tools.show_rename_dialog),
            ("PhoenixTools_SyncLinkLabels", "同步链接名称", "预览所选 App::Link 与直接来源的名称差异，确认后仅修改链接实例 Label。", phoenix_workbench_tools.show_link_labels_dialog),
            ("PhoenixTools_EditPartProperties", "填写零件属性", "编辑所选对象的五个 BOM 字符串字段，确认后写入。", phoenix_workbench_tools.show_properties_dialog),
        ]
        commands = []
        for identifier, title, tooltip, callback in definitions:
            Gui.addCommand(identifier, _Command(title, tooltip, callback))
            commands.append(identifier)
        self._mcp_command = _ToggleMCPCommand()
        Gui.addCommand(self._mcp_command.IDENTIFIER, self._mcp_command)
        commands.append(self._mcp_command.IDENTIFIER)
        self.appendMenu("Phoenix Tools", commands)
        self.appendToolbar("Phoenix Tools", commands)
        self._mcp_command.start_polling()

    def Activated(self):
        if hasattr(self, "_mcp_command"):
            self._mcp_command.refresh_actions(force=True)

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(PhoenixFreeCADMCPWorkbench())
