# SPDX-License-Identifier: Apache-2.0
"""Shared native command shortcuts with window-owned Qt slot receivers."""
from pathlib import Path
import sys

from pft_workbench_i18n import tr


_MESSAGES = {
    "PFT_CHAT_SHORTCUT_UNAVAILABLE": "当前插件缺少此快捷操作；请核对安装是否完整。",
    "PFT_CHAT_SHORTCUT_SOURCE_MISMATCH": "快捷操作来源与当前插件不一致；请正常退出 FreeCAD 后重新启动。",
    "PFT_CHAT_SHORTCUT_LOAD_FAILED": "无法加载快捷操作组件；请核对当前插件安装。",
    "PFT_CHAT_SHORTCUT_ACTION_FAILED": "快捷操作失败；请使用工作台同名工具核对当前目标。",
    "PFT_CHAT_SHORTCUT_WORKBENCH_UNAVAILABLE": "Phoenix 工作台尚未注册，无法切换。",
    "PFT_CHAT_SHORTCUT_WORKBENCH_ACTIVATION_FAILED": "无法切换到 Phoenix 工作台；请使用工作台列表重试。",
    "PFT_CHAT_SHORTCUT_NO_DOCUMENT": "当前没有打开的文档。",
    "PFT_CHAT_SHORTCUT_NO_3D_VIEW": "没有可确定的活动三维视图，请先激活目标文档的三维视图。",
    "PFT_CHAT_SHORTCUT_TARGETS_REQUIRED": "请明确勾选最多100个不重复的完整对象。",
}
_COMMANDS = {"document": "PhoenixTools_DocumentTools",
             "axonometric_fit": "PhoenixTools_AxonometricFit", "appearance": "PhoenixTools_Appearance"}


class _PftQuickToolbarError(RuntimeError):
    def __init__(self, code):
        super().__init__(_MESSAGES[code])
        self.code = code


def _shortcut_facade():
    """Resolve only the already registered command host from this installation."""
    host = sys.modules.get("pft_workbench_gui")
    if host is None:
        raise _PftQuickToolbarError("PFT_CHAT_SHORTCUT_UNAVAILABLE")
    origin = getattr(host, "__file__", None)
    if not isinstance(origin, str) or Path(origin).resolve() != Path(__file__).resolve().with_name("pft_workbench_gui.py"):
        raise _PftQuickToolbarError("PFT_CHAT_SHORTCUT_SOURCE_MISMATCH")
    return host


def compact_icon_button(parent, title, object_name, icon):
    from PySide import QtCore, QtGui, QtWidgets
    button = QtWidgets.QToolButton(parent)
    button.setObjectName(object_name)
    button.setText(tr(title))
    button.setToolTip(tr(title))
    button.setAccessibleName(tr(title))
    button.setIcon(QtGui.QIcon(str(Path(__file__).resolve().parent / "Resources" / "icons" / icon)))
    button.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
    button.setAutoRaise(True)
    return button


def create_quick_toolbar(parent, gui, *, object_prefix, on_error, on_success=None,
                         show_workbench=True, settings_title="设置", settings_name=None,
                         settings_checkable=False):
    """Create shared shortcuts; hosts own settings display and error presentation."""
    from PySide import QtCore, QtWidgets

    class PftQuickToolbar(QtWidgets.QWidget):
        def __init__(self):
            super().__init__(parent)
            self.buttons, self._receivers, self._bindings = {}, [], []
            self._closed, self._invoking = False, False
            self._on_error, self._on_success = on_error, on_success
            self._settings_callback, self._settings_pass_checked = None, False

        def _alive(self):
            if self._closed:
                return False
            current = self
            try:
                while current is not None:
                    if getattr(current, "_closed", False):
                        return False
                    current = current.parent()
            except RuntimeError:
                return False
            return True

        def invalidate(self):
            self._closed = True
            self._settings_callback = self._on_error = self._on_success = None
            for receiver in self._receivers:
                receiver.invalidate()
            self._bindings.clear()
            self._receivers.clear()

        def bind_settings(self, callback, pass_checked=False):
            if callback is not None and not callable(callback):
                raise TypeError("settings callback must be callable")
            self._settings_callback = callback
            self._settings_pass_checked = bool(pass_checked)

        def _dispatch(self, action, checked):
            if not self._alive() or self._invoking:
                return
            self._invoking = True
            host, stage = None, "load"
            try:
                if action == "settings":
                    stage = "action"
                    callback = self._settings_callback
                    if callback is not None:
                        callback(checked) if self._settings_pass_checked else callback()
                    return
                if action == "workbench":
                    stage = "action"
                    try:
                        registered = gui.listWorkbenches()
                        if type(registered) is not dict or "PftWorkbench" not in registered:
                            raise ValueError()
                    except Exception:
                        raise _PftQuickToolbarError("PFT_CHAT_SHORTCUT_WORKBENCH_UNAVAILABLE") from None
                    try:
                        gui.activateWorkbench("PftWorkbench")
                    except Exception:
                        raise _PftQuickToolbarError("PFT_CHAT_SHORTCUT_WORKBENCH_ACTIVATION_FAILED") from None
                else:
                    host = _shortcut_facade()
                    callback = getattr(host, "run_native_shortcut", None)
                    if not callable(callback):
                        raise _PftQuickToolbarError("PFT_CHAT_SHORTCUT_UNAVAILABLE")
                    stage = "action"
                    callback(gui, _COMMANDS[action])
                if self._alive() and callable(self._on_success):
                    self._on_success()
            except Exception as error:
                code = "PFT_CHAT_SHORTCUT_LOAD_FAILED" if stage == "load" else "PFT_CHAT_SHORTCUT_ACTION_FAILED"
                if isinstance(error, _PftQuickToolbarError):
                    code = error.code
                else:
                    command_error = getattr(getattr(host, "pft_global_menu", None), "PftGlobalMenuError", None)
                    tool_error = getattr(host, "PftToolError", None)
                    if isinstance(command_error, type) and isinstance(error, command_error):
                        candidate = getattr(error, "code", None)
                        if type(candidate) is str and candidate in _MESSAGES:
                            code = candidate
                    elif isinstance(tool_error, type) and isinstance(error, tool_error):
                        # Recognize only the host's three fixed target errors.
                        # Never stringify, retain or return arbitrary exceptions.
                        known = {_MESSAGES[key]: key for key in (
                            "PFT_CHAT_SHORTCUT_NO_DOCUMENT", "PFT_CHAT_SHORTCUT_NO_3D_VIEW",
                            "PFT_CHAT_SHORTCUT_TARGETS_REQUIRED")}
                        if len(error.args) == 1 and type(error.args[0]) is str:
                            code = known.get(error.args[0], code)
                if self._alive() and callable(self._on_error):
                    self._on_error({"code": code, "message": _MESSAGES[code]})
            finally:
                self._invoking = False

    toolbar = PftQuickToolbar()
    toolbar.setObjectName(object_prefix + "QuickToolbar")
    layout = QtWidgets.QHBoxLayout(toolbar)
    layout.setContentsMargins(0, 0, 0, 0)

    class _PftQuickReceiver(QtCore.QObject):
        def __init__(self, action):
            super().__init__(toolbar)
            self._toolbar, self._action = toolbar, action

        @QtCore.Slot(bool)
        def trigger(self, checked=False):
            current = self._toolbar
            if current is not None:
                current._dispatch(self._action, checked)

        @QtCore.Slot()
        def owner_destroyed(self):
            current = self._toolbar
            if current is not None:
                current.invalidate()

        def invalidate(self):
            self._toolbar = self._action = None

    for action, title, suffix, icon in (
        ("document", "当前文档工具", "DocumentToolsAction", "document-tools.svg"),
        ("axonometric_fit", "轴测并适合窗口", "AxonometricFitAction", "axonometric-fit.svg"),
        ("appearance", "对象外观", "AppearanceAction", "appearance.svg"),
        ("workbench", "切换到 Phoenix 工作台", "WorkbenchAction", "phoenix-wing-mark.svg"),
        ("settings", settings_title, "SettingsAction", "gear.svg"),
    ):
        if action == "workbench":
            layout.addStretch(1)
        name = settings_name if action == "settings" and settings_name is not None else object_prefix + suffix
        button = compact_icon_button(toolbar, title, name, icon)
        toolbar.buttons[action] = button
        if action == "settings":
            button.setCheckable(settings_checkable)
        receiver = _PftQuickReceiver(action)
        slot = receiver.trigger
        signal = button.toggled if action == "settings" and settings_checkable else button.clicked
        signal.connect(slot)
        toolbar._receivers.append(receiver)
        toolbar._bindings.append((button, slot))
        layout.addWidget(button)
        if action == "workbench" and not show_workbench:
            button.hide()

    lifetime = _PftQuickReceiver(None)
    toolbar._receivers.append(lifetime)
    destroyed_slot = lifetime.owner_destroyed
    toolbar.destroyed.connect(destroyed_slot)
    parent.destroyed.connect(destroyed_slot)
    toolbar._bindings.extend(((toolbar, destroyed_slot), (parent, destroyed_slot)))
    return toolbar
