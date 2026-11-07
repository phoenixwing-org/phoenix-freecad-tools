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

import pft_bridge
import pft_workbench_tools
import pft_workbench_settings as settings
import pft_global_menu
from pft_workbench_i18n import get_language, tr
from pft_workbench_model import PftToolError

if os.path.realpath(pft_global_menu.__file__) != os.path.realpath(os.path.join(_addon_dir, "pft_global_menu.py")):
    raise RuntimeError("Global menu module belongs to another addon.")


def _feedback(message, error=False):
    printer = App.Console.PrintError if error else App.Console.PrintMessage
    message = tr(message)
    printer("[Phoenix Tools] " + message + "\n")
    Gui.getMainWindow().statusBar().showMessage(message, 12000)


class _PftCommand:
    def __init__(self, title, tooltip, callback):
        self.title = title
        self.tooltip = tooltip
        self.callback = callback

    def GetResources(self):
        # FreeCAD's Draft snap commands use the same resource flag. These
        # callbacks open panels or change UI/camera state; panels own CAD edits.
        return {"MenuText": tr(self.title), "ToolTip": tr(self.tooltip), "CmdType": "NoTransaction"}

    def IsActive(self):
        return True

    def Activated(self):
        try:
            self.callback()
        except pft_bridge.PftBridgeError as error:
            _feedback(error.message, error=True)
        except Exception as error:
            _feedback(tr("操作失败（%s）。") % type(error).__name__, error=True)


class _PftMCPActionCommand:
    """Update only one command's actions and associated toolbar buttons."""
    def __init__(self):
        self._last_state = None

    def GetResources(self):
        return {**self._presentation()[1], "CmdType": "NoTransaction"}

    def IsActive(self):
        return True

    def _enabled(self, state):
        return True

    def _toolbutton_style(self):
        return "ToolButtonIconOnly"

    def _style_own_toolbuttons(self, action, resources):
        """Never change a global toolbar preference or another command's button."""
        from PySide import QtCore
        try:
            from PySide import QtWidgets
        except ImportError:
            from PySide import QtGui as QtWidgets
        style_name = self._toolbutton_style()
        style = getattr(QtCore.Qt, style_name, None)
        if style is None:
            style = getattr(QtCore.Qt.ToolButtonStyle, style_name)
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
                    if callable(getattr(button, "setAccessibleName", None)):
                        button.setAccessibleName(resources["MenuText"])
                    if callable(getattr(button, "setAccessibleDescription", None)):
                        button.setAccessibleDescription(resources["ToolTip"])
            if matched:
                break

    def refresh_actions(self, force=False, status=None):
        """Read the real bridge state; update only this command and its buttons.

        Gui.Command.get(...).getAction() is used by FreeCAD's bundled Draft
        observer. associatedWidgets is Qt 5; associatedObjects covers Qt 6.
        """
        try:
            state, resources = self._presentation(status)
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
                    action.setEnabled(self._enabled(state))
                self._last_state = state
            # FreeCAD may create buttons later or restore a toolbar style.
            for action in actions:
                self._style_own_toolbuttons(action, resources)
        except (AttributeError, RuntimeError, KeyError):
            return


class _PftMCPStatusCommand(_PftMCPActionCommand):
    IDENTIFIER = "PhoenixTools_MCPStatus"

    def _presentation(self, status=None):
        status = pft_bridge.get_status() if status is None else status
        running, stopping = bool(status.get("running")), bool(status.get("stopping"))
        title = tr("FreeCAD 桥接：正在停止") if stopping else tr("FreeCAD 桥接：运行中") if running else tr("FreeCAD 桥接：已停止")
        icon = "mcp-status-running.svg" if running and not stopping else "mcp-status-stopped.svg"
        return (running, stopping, get_language()), {
            "MenuText": title,
            "ToolTip": title + "\n" + tr("点击查看实际桥接状态；聊天客户端连接需另行验证。"),
            "Pixmap": os.path.join(_addon_dir, "Resources", "icons", icon),
        }

    def Activated(self):
        try:
            snapshot = dict(pft_bridge.get_status())
            return pft_workbench_tools.show_mcp_status_dialog(snapshot)
        except Exception as error:
            _feedback(tr("状态读取失败（%s）。") % type(error).__name__, error=True)


class _PftSettingsCommand(_PftCommand, _PftMCPActionCommand):
    IDENTIFIER = "PhoenixTools_Settings"

    def __init__(self, callback):
        _PftCommand.__init__(self, "设置", "配置 Phoenix Tools 的界面语言与桥接启动选项。", callback)
        _PftMCPActionCommand.__init__(self)

    def _presentation(self, status=None):
        return (get_language(),), {"MenuText": tr(self.title), "ToolTip": tr(self.tooltip),
            "Pixmap": os.path.join(_addon_dir, "Resources", "icons", "gear.svg")}

    def GetResources(self):
        return {**self._presentation()[1], "CmdType": "NoTransaction"}


class _PftToolbarCommand(_PftCommand, _PftMCPActionCommand):
    """Business actions with their own icons and the chosen toolbar text style."""
    def __init__(self, identifier, title, tooltip, callback, icon, requires_document=False):
        _PftCommand.__init__(self, title, tooltip, callback)
        _PftMCPActionCommand.__init__(self)
        self.IDENTIFIER, self.icon = identifier, icon
        self.requires_document = requires_document
        self._invocation_serial = 0
        self._last_invocation = None

    def _toolbutton_style(self):
        return ("ToolButtonIconOnly" if settings.load_toolbar_button_style(App) == "icon"
                else "ToolButtonTextBesideIcon")

    def _presentation(self, status=None):
        active = self.IsActive()
        return (get_language(), active, self._toolbutton_style()), {
            "MenuText": tr(self.title), "ToolTip": tr(self.tooltip),
            "Pixmap": os.path.join(_addon_dir, "Resources", "icons", self.icon),
        }

    def GetResources(self):
        return {**self._presentation()[1], "CmdType": "NoTransaction"}

    def IsActive(self):
        return not self.requires_document or getattr(App, "ActiveDocument", None) is not None

    def _enabled(self, state):
        return state[1]

    def Activated(self):
        self._invocation_serial += 1
        outcome = {"serial": self._invocation_serial, "ok": False,
                   "code": "PFT_CHAT_SHORTCUT_ACTION_FAILED",
                   "message": "快捷操作失败；请使用工作台同名工具核对当前目标。"}
        self._last_invocation = None
        try:
            result = self.callback()
            outcome.update(ok=True, code=None, message=None)
            return result
        except PftToolError as error:
            known = {"当前没有打开的文档。": "PFT_CHAT_SHORTCUT_NO_DOCUMENT",
                     "没有可确定的活动三维视图，请先激活目标文档的三维视图。": "PFT_CHAT_SHORTCUT_NO_3D_VIEW",
                     "请明确勾选最多100个不重复的完整对象。": "PFT_CHAT_SHORTCUT_TARGETS_REQUIRED"}
            if len(error.args) == 1 and type(error.args[0]) is str and error.args[0] in known:
                outcome.update(code=known[error.args[0]], message=error.args[0])
            _feedback(str(error), error=True)
        except Exception as error:
            _feedback(tr("操作失败（%s）。") % type(error).__name__, error=True)
        finally:
            # Native Gui.runCommand discards return values and can catch Python
            # exceptions. Keep only a bounded, fixed diagnostic for this call.
            self._last_invocation = outcome


class _PftToggleMCPCommand(_PftMCPActionCommand):
    IDENTIFIER = "PhoenixTools_ToggleMCP"
    TIMER_NAME = "PhoenixTools_MCPStateTimer"
    TOOLBAR_ANCHOR_COMMANDS = (
        "PhoenixTools_MCPStatus", "PhoenixTools_ToggleMCP", "PhoenixTools_Settings",
        "PhoenixTools_NormalizeNames", "PhoenixTools_SyncLinkLabels", "PhoenixTools_EditPartProperties",
    )
    TOOLBAR_COMMANDS = TOOLBAR_ANCHOR_COMMANDS[:3] + ("PhoenixTools_DocumentTools", "PhoenixTools_AxonometricFit", "PhoenixTools_Appearance") + TOOLBAR_ANCHOR_COMMANDS[3:] + ("PhoenixTools_DeskTools", "PhoenixTools_ChatConnection", "PhoenixTools_Help")

    def __init__(self, status_command=None, settings_command=None, toolbar_commands=()):
        super().__init__()
        self._timer = None
        self._status_command = status_command
        self._settings_command = settings_command
        self._toolbar_commands = tuple(toolbar_commands)

    def _presentation(self, status=None):
        status = pft_bridge.get_status() if status is None else status
        stopping, running = bool(status.get("stopping")), bool(status.get("running"))
        port = status.get("port", getattr(pft_bridge, "DEFAULT_PORT", 9876))
        if stopping:
            title = tr("MCP 正在停止")
            tooltip = tr("当前 MCP 正在后台停止；完成后可再次启动。")
        elif running:
            title = tr("停止 MCP · 运行中")
            tooltip = tr("当前 MCP 运行中；点击停止本机 Phoenix MCP 服务。")
        else:
            title = tr("启动 MCP · 已停止")
            tooltip = tr("当前 MCP 已停止；点击启动本机桥接，采用连接设置或独立开发端口。")
        return (running, stopping, get_language(), port), {"MenuText": title, "ToolTip": tooltip,
            "Pixmap": os.path.join(_addon_dir, "Resources", "icons", "mcp-stop.svg" if running or stopping else "mcp-start.svg")}

    def IsActive(self):
        return not bool(pft_bridge.get_status().get("stopping"))

    def _enabled(self, state):
        return not state[1]

    def _order_own_toolbar(self):
        """Correct a retained toolbar using real command actions, without firing them."""
        try:
            try:
                from PySide import QtWidgets
            except ImportError:
                from PySide import QtGui as QtWidgets
            toolbar_type = getattr(QtWidgets, "QToolBar", None)
            if toolbar_type is None:
                return
            toolbars = [bar for bar in Gui.getMainWindow().findChildren(toolbar_type)
                        if bar.objectName() == "Phoenix Tools" or bar.windowTitle() == "Phoenix Tools"]
            if not toolbars:
                return
            command_actions = []
            for identifier in self.TOOLBAR_COMMANDS:
                actions = Gui.Command.get(identifier).getAction()
                command_actions.append([actions] if hasattr(actions, "setText") else list(actions or []))
            candidates = []
            for toolbar in toolbars:
                current = list(toolbar.actions())
                matched = [[action for action in current if any(action is own or action == own for own in actions)]
                           for actions in command_actions]
                # A matching title alone is insufficient, and multiple matching
                # actions for one command are ambiguous. Never guess a toolbar.
                required = {self.TOOLBAR_COMMANDS.index(name) for name in self.TOOLBAR_ANCHOR_COMMANDS}
                if (all(len(matched[index]) == 1 for index in required)
                        and all(len(actions) <= 1 for index, actions in enumerate(matched) if index not in required)):
                    candidates.append((toolbar, current, [actions[0] for actions in matched[:3]]))
            if len(candidates) != 1:
                return
            toolbar, current, controls = candidates[0]
            same = lambda left, right: left is right or left == right
            if len(current) >= 3 and all(same(action, wanted) for action, wanted in zip(current[:3], controls)):
                return
            remaining = [action for action in current if not any(same(action, control) for control in controls)]
            anchor = remaining[0] if remaining else None
            for action in controls:
                toolbar.removeAction(action)
            for action in controls:
                toolbar.insertAction(anchor, action)
        except (AttributeError, RuntimeError, KeyError):
            # Hidden/restoring workbenches may not have their real actions yet.
            return

    def refresh_actions(self, force=False):
        # Qt may rebuild QToolButtons when actions move. Apply icon-only styling
        # afterwards, and keep the existing single read-only polling timer.
        self._order_own_toolbar()
        snapshot = pft_bridge.get_status()
        super().refresh_actions(force, snapshot)
        if self._status_command is not None:
            self._status_command.refresh_actions(force, snapshot)
        if self._settings_command is not None:
            self._settings_command.refresh_actions(force, snapshot)
        for command in self._toolbar_commands:
            command.refresh_actions(force)

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
            status = pft_bridge.get_status()
            if status.get("stopping"):
                return
            # stop_server schedules shutdown on its own thread and returns
            # immediately. The timer follows its real completion state.
            if status.get("running"):
                if not pft_workbench_tools.confirm_mcp_stop():
                    return
                # A confirmed stop intent also suppresses a startup callback
                # still pending when another caller has already stopped it.
                window = Gui.getMainWindow()
                window.setProperty("PhoenixTools_AutoStartSuppressed", True)
                # The modal confirmation can process events. Never turn a stop
                # request into a start if the bridge changed while it was open.
                status = pft_bridge.get_status()
                if status.get("stopping") or not status.get("running"):
                    return
                result = pft_bridge.stop_server()
            else:
                result = pft_bridge.start_server()
            if result.get("stopping"):
                _feedback(tr("MCP 服务正在停止。"))
            elif result.get("running"):
                _feedback(tr("MCP 服务已启动：127.0.0.1:%s。") % result["port"])
            else:
                _feedback(tr("MCP 服务已停止。"))
        except pft_bridge.PftBridgeError as error:
            _feedback(error.message, error=True)
        except Exception as error:
            _feedback(tr("MCP 操作失败（%s）。") % type(error).__name__, error=True)
        finally:
            self.refresh_actions(force=True)


def _auto_start_mcp(window):
    """One startup attempt; never restart a bridge after a manual stop."""
    try:
        if window.property("PhoenixTools_AutoStartSuppressed"):
            return
        if not settings.load_service_options(App)["auto_start_mcp"]:
            return
        snapshot = pft_bridge.get_status()
        if snapshot.get("running") or snapshot.get("stopping"):
            return
        result = pft_bridge.start_server()
        if result.get("running"):
            _feedback(tr("MCP 服务已启动：127.0.0.1:%s。") % result["port"])
    except Exception as error:
        _feedback(tr("桥接自动启动失败（%s）；可稍后手动启动。") % type(error).__name__, error=True)


def _schedule_mcp_autostart():
    """Run at module registration, never on workbench activation or language refresh."""
    try:
        getter = getattr(Gui, "getMainWindow", None)
        window = getter() if callable(getter) else None
        if window is None or not callable(getattr(window, "property", None)):
            return False
        key = "PhoenixTools_AutoStartChecked"
        if window.property(key):
            return False
        # Mark even an opted-out startup so enabling the preference or reloading
        # this module cannot trigger auto-start in the same FreeCAD session.
        window.setProperty(key, True)
        if not settings.load_service_options(App)["auto_start_mcp"]:
            return False
        snapshot = pft_bridge.get_status()
        if snapshot.get("running") or snapshot.get("stopping"):
            return False
        from PySide import QtCore
        QtCore.QTimer.singleShot(1000, lambda: _auto_start_mcp(window))
        return True
    except Exception as error:
        _feedback(tr("桥接自动启动失败（%s）；可稍后手动启动。") % type(error).__name__, error=True)
        return False


class PftWorkbench(_workbench_base):
    Icon = os.path.join(_addon_dir, "Resources", "icons", "phoenix-wing-mark.svg")
    MenuText = "Phoenix Tools"
    ToolTip = "Phoenix 的 FreeCAD 工具入口；通过 MCP 提供聊天控制能力。"

    def __init__(self):
        self._commands = {}
        self._registered_commands = {}
        self._global_menu_runtime = None
        self._initialized = False

    def _register_command(self, identifier, command):
        if identifier in self._registered_commands:
            return
        if Gui.Command.get(identifier) is not None:
            raise pft_global_menu.PftGlobalMenuError("Phoenix command registration conflicts.")
        Gui.addCommand(identifier, command)
        self._registered_commands[identifier] = command

    def _ensure_commands(self, identifiers=None):
        """Register reusable entry commands without opening panels or a workbench."""
        try:
            settings.load_language(App)
        except settings.PftSettingsError as error:
            _feedback(str(error), error=True)
        definitions = [
            ("PhoenixTools_DocumentTools", "当前文档工具", "读取当前内存文档的信息与缓存尺寸，复制并预览导出 Markdown。", pft_workbench_tools.show_document_tools_dialog),
            ("PhoenixTools_AxonometricFit", "轴测并适合视图", "将当前三维视图切换为轴测，并缩放以显示全部对象。", pft_workbench_tools.run_axonometric_fit),
            ("PhoenixTools_Appearance", "对象外观", "实时预览随机配色与透明度；确定保留，取消或 Esc 恢复。", pft_workbench_tools.show_appearance_dialog),
            ("PhoenixTools_NormalizeNames", "命名修正", "预览所选对象的 Label 命名修正，确认后可撤销。", pft_workbench_tools.show_rename_dialog),
            ("PhoenixTools_SyncLinkLabels", "链接同步", "预览所选 App::Link 与直接来源的名称差异，确认后仅修改链接实例 Label。", pft_workbench_tools.show_link_labels_dialog),
            ("PhoenixTools_EditPartProperties", "零件属性", "编辑所选对象的五个 BOM 字符串字段，确认后写入。", pft_workbench_tools.show_properties_dialog),
            ("PhoenixTools_DeskTools", "Desk Tools", "配置、检查或启动 Desk 后台，并独立打开已验证的 Web 页面。", pft_workbench_tools.show_desk_dialog),
            ("PhoenixTools_ChatConnection", "AI 聊天", "使用原生面板连接 Codex，登录后发送消息与停止回复。", pft_workbench_tools.show_chat_connection_dialog),
            ("PhoenixTools_Help", "帮助", "查看 Phoenix Tools 与 MCP 的本地能力和操作说明。", pft_workbench_tools.show_help_dialog),
        ]
        icons = {
            "PhoenixTools_DocumentTools": "document-tools.svg", "PhoenixTools_AxonometricFit": "axonometric-fit.svg",
            "PhoenixTools_Appearance": "appearance.svg", "PhoenixTools_NormalizeNames": "normalize-names.svg",
            "PhoenixTools_SyncLinkLabels": "link-labels.svg", "PhoenixTools_EditPartProperties": "part-properties.svg",
            "PhoenixTools_DeskTools": "desk-tools.svg", "PhoenixTools_ChatConnection": "chat-connection.svg",
            "PhoenixTools_Help": "help.svg",
        }
        for identifier, title, tooltip, callback in definitions:
            if identifiers is not None and identifier not in identifiers:
                continue
            if identifier not in self._commands:
                self._commands[identifier] = _PftToolbarCommand(
                    identifier, title, tooltip, callback, icons[identifier],
                    requires_document=identifier in {"PhoenixTools_AxonometricFit", "PhoenixTools_Appearance"})
            self._register_command(identifier, self._commands[identifier])
        if identifiers is None or _PftSettingsCommand.IDENTIFIER in identifiers:
            if not hasattr(self, "_settings_command"):
                self._settings_command = _PftSettingsCommand(self._show_settings)
            self._register_command(self._settings_command.IDENTIFIER, self._settings_command)
        return [definition[0] for definition in definitions]

    def Initialize(self):
        if self._initialized:
            return
        business_commands = self._ensure_commands()
        self._mcp_status_command = _PftMCPStatusCommand()
        self._mcp_command = _PftToggleMCPCommand(
            self._mcp_status_command, self._settings_command,
            self._commands.values())
        for command in (self._mcp_status_command, self._mcp_command):
            self._register_command(command.IDENTIFIER, command)
        commands = [self._mcp_status_command.IDENTIFIER, self._mcp_command.IDENTIFIER,
                    self._settings_command.IDENTIFIER] + business_commands
        self.appendMenu("Phoenix", commands)
        self.appendToolbar("Phoenix Tools", commands)
        self._mcp_command.start_polling()
        self._initialized = True

    def _show_settings(self):
        return pft_workbench_tools.show_settings_dialog(self.refresh_language)

    def run_native_shortcut(self, identifier):
        command = self._registered_commands.get(identifier)
        if identifier not in pft_global_menu.QUICK_COMMANDS or command is None or Gui.Command.get(identifier) is None:
            raise pft_global_menu.PftGlobalMenuError("当前插件缺少此快捷操作；请核对安装是否完整。")
        if not command.IsActive():
            raise PftToolError("当前没有打开的文档。")
        before = command._invocation_serial
        Gui.runCommand(identifier, 0)
        outcome = command._last_invocation
        if (command._invocation_serial != before + 1 or type(outcome) is not dict
                or outcome.get("serial") != before + 1):
            raise pft_global_menu.PftGlobalMenuError("快捷操作失败；请使用工作台同名工具核对当前目标。",
                "PFT_CHAT_SHORTCUT_ACTION_FAILED")
        if outcome.get("ok") is not True:
            raise pft_global_menu.PftGlobalMenuError(outcome["message"], outcome["code"])

    def refresh_language(self):
        """Refresh existing actions only; keep timers, documents and MCP state."""
        self.ToolTip = tr("Phoenix 的 FreeCAD 工具入口；通过 MCP 提供聊天控制能力。")
        owned_actions = []
        for identifier, command in self._commands.items():
            try:
                actions = Gui.Command.get(identifier).getAction()
                if hasattr(actions, "setText"):
                    actions = [actions]
                resources = command.GetResources()
                owned_actions.extend(actions or [])
                for action in actions or []:
                    action.setText(resources["MenuText"])
                    if callable(getattr(action, "setIconText", None)):
                        action.setIconText(resources["MenuText"])
                    action.setToolTip(resources["ToolTip"])
                    action.setStatusTip(resources["ToolTip"])
            except (AttributeError, RuntimeError, KeyError):
                # A hidden workbench may not have created actions yet.
                # GetResources will supply the current language when it does.
                continue
        if self._global_menu_runtime is not None:
            self._global_menu_runtime.refresh_primary_menu(owned_actions, settings.load_primary_menu_visible(App))
        if hasattr(self, "_mcp_command"):
            self._mcp_command.refresh_actions(force=True)
        elif hasattr(self, "_settings_command"):
            self._settings_command.refresh_actions(force=True)
        if self._global_menu_runtime is not None:
            self._global_menu_runtime.refresh_language()

    def Activated(self):
        if hasattr(self, "_mcp_command"):
            self.refresh_language()

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


_global_menu_runtime = pft_global_menu.get_session_runtime(Gui, _addon_dir)
if _global_menu_runtime is None:
    _workbench = PftWorkbench()
    _global_menu_runtime = pft_global_menu.PftGlobalMenuRuntime(
        Gui, _workbench, _addon_dir, PftWorkbench.Icon)
    # Keep the exact owner even if registration fails partway; a retry can only
    # reuse its own command objects, never replace existing native command IDs.
    pft_global_menu.retain_session_runtime(Gui, _global_menu_runtime)
else:
    _workbench = _global_menu_runtime.workbench
_workbench._global_menu_runtime = _global_menu_runtime
_workbench._ensure_commands(pft_global_menu.BOOTSTRAP_COMMANDS)
_global_menu_runtime.install()
if not _global_menu_runtime.workbench_registered:
    Gui.addWorkbench(_workbench)
    _global_menu_runtime.workbench_registered = True
_schedule_mcp_autostart()


def run_native_shortcut(gui, identifier):
    """Use the same registered native command as the workbench menu/toolbar."""
    if gui is not Gui:
        raise pft_global_menu.PftGlobalMenuError("快捷操作来源与当前插件不一致；请正常退出 FreeCAD 后重新启动。",
            "PFT_CHAT_SHORTCUT_SOURCE_MISMATCH")
    return _workbench.run_native_shortcut(identifier)
