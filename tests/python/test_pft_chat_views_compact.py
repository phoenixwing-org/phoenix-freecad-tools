# SPDX-License-Identifier: Apache-2.0
"""Chat presentation, command shortcuts and explicit actions without Qt or CAD."""
from copy import deepcopy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
_old_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_chat_views as views
    import pft_workbench_i18n as i18n
finally:
    sys.path[:] = _old_path


class Signal:
    def __init__(self): self.slots = []
    def connect(self, callback): self.slots.append(callback)
    def emit(self, *args):
        for callback in list(self.slots): callback(*args)


class ChatQt:
    def __init__(self):
        owner = self
        self.objects, self.clipboard = [], []

        class Policy:
            Ignored = 13
            def __init__(self): self.horizontal = 5
            def setHorizontalPolicy(self, value): self.horizontal = value

        class Font:
            def __init__(self, previous=None): self.point_size = previous.point_size if previous else 12
            def setPointSize(self, value): self.point_size = value

        class Widget:
            def __init__(self, parent=None):
                self._parent, self._name = None, ""
                self.visible, self.enabled, self.value = True, True, ""
                self.policy, self._font = Policy(), Font()
                owner.objects.append(self)
                self.setParent(parent)
            def setParent(self, parent): self._parent = parent
            def parent(self): return self._parent
            def setObjectName(self, value): self._name = value
            def objectName(self): return self._name
            def findChild(self, kind, name):
                return next((item for item in owner.objects if isinstance(item, kind)
                             and item._name == name and owner.inside(item, self)), None)
            def setVisible(self, value): self.visible = bool(value)
            def hide(self): self.visible = False
            def show(self): self.visible = True
            def setEnabled(self, value): self.enabled = value
            def isEnabled(self): return self.enabled
            def setMinimumWidth(self, value): self.minimum_width = value
            def setMaximumHeight(self, value): self.maximum_height = value
            def sizePolicy(self): return self.policy
            def setSizePolicy(self, value): self.policy = value
            def setWordWrap(self, value): self.word_wrap = value
            def setTextFormat(self, value): self.text_format = value
            def setText(self, value): self.value = value
            def text(self): return self.value
            def setToolTip(self, value): self.tooltip = value
            def setAccessibleName(self, value): self.accessible_name = value
            def setIcon(self, value): self.icon = value
            def setToolButtonStyle(self, value): self.style = value
            def setAutoRaise(self, value): self.auto_raise = value
            def setWindowTitle(self, value): self.title = value
            def setAttribute(self, *args): pass
            def resize(self, *args): pass
            def font(self): return self._font
            def setFont(self, value): self._font = value
            def closeEvent(self, event): self.visible = False; event.accept()
            def close(self):
                event = types.SimpleNamespace(accepted=False)
                event.accept = lambda: setattr(event, "accepted", True)
                event.ignore = lambda: setattr(event, "accepted", False)
                self.closeEvent(event)
                return event.accepted

        class Dock(Widget):
            def setWidget(self, widget): self.content = widget; widget.setParent(self)

        class Layout:
            def __init__(self, parent=None):
                self.parent, self.children = parent, []
                if parent is not None: parent.layout = self
            def attach(self, parent):
                self.parent = parent
                for child in self.children:
                    if isinstance(child, Layout): child.attach(parent)
                    elif isinstance(child, Widget): child.setParent(parent)
            def addWidget(self, widget, *args):
                self.children.append(widget)
                if self.parent is not None: widget.setParent(self.parent)
            def addLayout(self, layout): self.children.append(layout); layout.attach(self.parent)
            def addStretch(self, value=0): self.children.append(("stretch", value))
            def setContentsMargins(self, *args): pass

        class Text(Widget):
            def __init__(self, value="", parent=None): super().__init__(parent); self.value = value

        class Button(Text):
            def __init__(self, value="", parent=None):
                super().__init__(value, parent)
                self.clicked, self.toggled = Signal(), Signal()
                self.checked, self.checkable = False, False
            def setCheckable(self, value): self.checkable = value
            def setChecked(self, value):
                changed = value != self.checked
                self.checked = value
                if changed: self.toggled.emit(value)
            def isChecked(self): return self.checked
            def click(self):
                if self.checkable: self.setChecked(not self.checked)
                self.clicked.emit()

        class Combo(Widget):
            AdjustToMinimumContentsLengthWithIcon = 3
            def __init__(self):
                super().__init__()
                self.items, self.index, self.blocked, self.currentIndexChanged = [], -1, False, Signal()
            def addItem(self, title, value):
                self.items.append((title, value))
                if self.index < 0: self.index = 0
            def clear(self): self.items, self.index = [], -1
            def findData(self, value): return next((i for i, (_, data) in enumerate(self.items) if data == value), -1)
            def currentData(self): return self.items[self.index][1] if 0 <= self.index < len(self.items) else None
            def setCurrentIndex(self, value):
                changed = value != self.index
                self.index = value
                if changed and not self.blocked: self.currentIndexChanged.emit(value)
            def blockSignals(self, value): self.blocked = value
            def setMinimumContentsLength(self, value): self.minimum_contents_length = value
            def setSizeAdjustPolicy(self, value): self.size_adjust_policy = value

        class Editor(Text):
            def __init__(self):
                super().__init__()
                self.bar = types.SimpleNamespace(position=0, value=lambda: self.bar.position, maximum=lambda: 100,
                    setValue=lambda value: setattr(self.bar, "position", value))
            def setReadOnly(self, value): self.read_only = value
            def setPlaceholderText(self, value): self.placeholder = value
            def setPlainText(self, value): self.value = value
            def toPlainText(self): return self.value
            def clear(self): self.value = ""
            def verticalScrollBar(self): return self.bar

        class Scroll(Widget):
            def setWidgetResizable(self, value): self.widget_resizable = value
            def setFrameShape(self, value): self.frame = value
            def setHorizontalScrollBarPolicy(self, value): self.horizontal_scroll_policy = value
            def setWidget(self, value): self.content = value; value.setParent(self)

        self.core = types.SimpleNamespace(Qt=types.SimpleNamespace(
            WA_DeleteOnClose=1, PlainText=2, ToolButtonIconOnly=3, ScrollBarAlwaysOff=4), QUrl=lambda value: value)
        self.gui = types.SimpleNamespace(QFont=Font, QIcon=lambda value: value,
            QDesktopServices=types.SimpleNamespace(openUrl=Mock(return_value=True)))
        self.widgets = types.SimpleNamespace(QDockWidget=Dock, QWidget=Widget, QVBoxLayout=Layout, QHBoxLayout=Layout,
            QLabel=Text, QLineEdit=Text, QToolButton=Button, QPushButton=Button, QCheckBox=Button, QComboBox=Combo,
            QPlainTextEdit=Editor, QScrollArea=Scroll, QSizePolicy=Policy, QFrame=types.SimpleNamespace(NoFrame=0),
            QFileDialog=types.SimpleNamespace(getOpenFileName=Mock(return_value=("", ""))),
            QApplication=types.SimpleNamespace(clipboard=lambda: types.SimpleNamespace(setText=self.clipboard.append)),
            QMessageBox=types.SimpleNamespace(Yes=1, Cancel=2, question=Mock(return_value=2)))

    @staticmethod
    def inside(widget, parent):
        current = widget.parent()
        while current is not None:
            if current is parent: return True
            current = current.parent()
        return False

    def named(self, name): return next(item for item in self.objects if item.objectName() == name)


class CompactChatViewTests(unittest.TestCase):
    def setUp(self):
        previous = i18n.get_language(); i18n.set_language("zh")
        self.addCleanup(i18n.set_language, previous)
        self.qt = ChatQt()
        self.window = self.qt.widgets.QWidget()
        self.app = types.SimpleNamespace(getUserAppDataDir=Mock(return_value="/fixture/user-data"))
        self.gui = types.SimpleNamespace(getMainWindow=lambda: self.window, runCommand=Mock())
        self.app.Console = types.SimpleNamespace(PrintError=Mock())
        self.facade = types.SimpleNamespace(show_document_tools_dialog=Mock(),
            run_axonometric_fit=Mock(), show_appearance_dialog=Mock())
        self.real_facade_loader = views._shortcut_facade
        self.controllers, self.processes = [], []
        owner = self
        class Process:
            def __init__(self, core, parent): owner.processes.append(self)
        class Controller:
            def __init__(self, process, changed, open_url):
                self.changed, self.open_url = changed, open_url
                self.snapshot = {"phase": "disconnected", "transcript": [], "can_send": False, "is_busy": False}
                self.connect, self.login, self.disconnect = Mock(), Mock(), Mock()
                self.interrupt, self.refresh_models, self.select_model = Mock(), Mock(), Mock()
                self.send = Mock(side_effect=lambda text: self.push(phase="sending", is_busy=True))
                owner.controllers.append(self)
            def push(self, **updates): self.snapshot.update(deepcopy(updates)); self.changed(deepcopy(self.snapshot))
        class ProfileError(ValueError): pass
        class SettingsError(ValueError): pass
        self.profile = types.SimpleNamespace(detect_codex_executable=Mock(return_value="/fixture/codex"),
            validate_executable=Mock(side_effect=lambda value: value),
            prepare_chat_profile=Mock(return_value={"home": "/fixture/home", "cwd": "/fixture/work"}),
            PftChatProfileError=ProfileError)
        self.settings = types.SimpleNamespace(load_chat_options=Mock(return_value={
            "enabled": False, "desk_enabled": False, "node_executable": "/fixture/node", "cli_entry": "/fixture/mcp/dist/cli.js"}),
            load_chat_preferences=Mock(return_value={"model_id": "", "font_point_size": 0}),
            save_chat_options=Mock(side_effect=lambda app, enabled, node, cli, desk_enabled=False:
                {"enabled": enabled, "desk_enabled": desk_enabled, "node_executable": node, "cli_entry": cli}),
            load_chat_desk_configuration=Mock(return_value={"node_executable": "/fixture/desk-node",
                "cli_entry": "/fixture/desk/scripts/desk-web-service.mjs", "state_dir": "/fixture/desk-state"}),
            save_chat_preferences=Mock(), build_chat_mcp_config=Mock(), PftChatSettingsError=SettingsError)
        modules = {"PySide": types.SimpleNamespace(QtCore=self.qt.core, QtGui=self.qt.gui, QtWidgets=self.qt.widgets),
            "pft_chat_controller": types.SimpleNamespace(PftChatController=Controller),
            "pft_chat_qprocess": types.SimpleNamespace(PftChatProcess=Process),
            "pft_chat_profile": self.profile, "pft_chat_settings": self.settings}
        patches = [patch.dict(sys.modules, modules),
            patch.object(views, "_shortcut_facade", return_value=self.facade),
            patch.object(views, "mount_panel_dock", side_effect=lambda dock, parent, content: dock.setWidget(content)),
            patch.object(views, "present_panel", side_effect=lambda dock: dock.show())]
        for handle in patches: handle.start(); self.addCleanup(handle.stop)
        self.get_status = Mock(return_value={"running": True})

    def show(self): return views.show_chat_connection_dialog(self.app, self.gui, self.get_status)
    def named(self, suffix): return self.qt.named("PhoenixTools_Chat" + suffix)

    def test_primary_toolbar_and_chat_remain_compact_while_settings_own_configuration(self):
        dock = self.show()
        self.assertEqual(dock.minimum_width, 300)
        scroll = self.named("ScrollArea")
        self.assertTrue(scroll.widget_resizable)
        self.assertEqual(scroll.horizontal_scroll_policy, self.qt.core.Qt.ScrollBarAlwaysOff)
        settings = self.named("Settings")
        self.assertFalse(settings.visible)
        toolbar = scroll.content.layout.children[0]
        self.assertEqual([child.objectName() if hasattr(child, "objectName") else child for child in toolbar.children], [
            "PhoenixTools_ChatDocumentToolsAction", "PhoenixTools_ChatAxonometricFitAction",
            "PhoenixTools_ChatAppearanceAction", ("stretch", 1), "PhoenixTools_ChatSettingsToggle"])
        for suffix in ("DocumentToolsAction", "AxonometricFitAction", "AppearanceAction", "SettingsToggle"):
            button = self.named(suffix)
            self.assertEqual(button.style, self.qt.core.Qt.ToolButtonIconOnly)
            self.assertTrue(button.auto_raise)
            self.assertTrue(button.tooltip)
            self.assertTrue(Path(button.icon).is_file())
        for suffix in ("Executable", "Browse", "EnableTools", "EnableDeskTools", "Disconnect", "Model", "RefreshModels", "ModelsStatus",
                       "FontSize", "SavePreferences", "Node", "NodeBrowse", "MCPCLI", "MCPCLIBrowse", "SaveTools", "ToolStatus"):
            self.assertTrue(self.qt.inside(self.named(suffix), settings), suffix)
        for name in ("PftChatCopyPrompt", "PftChatOfficialDocs", "PftChatClose", "PftChatBridgeStatus"):
            self.assertTrue(self.qt.inside(self.qt.named(name), settings), name)
        for suffix in ("Status", "Transcript", "Input", "Send", "Stop"):
            self.assertFalse(self.qt.inside(self.named(suffix), settings), suffix)
        self.assertFalse(self.named("ToolStatus").visible)
        self.controllers[0].connect.assert_not_called()
        self.controllers[0].login.assert_not_called()
        self.profile.prepare_chat_profile.assert_not_called()
        self.settings.save_chat_options.assert_not_called()
        self.settings.save_chat_preferences.assert_not_called()
        self.gui.runCommand.assert_not_called()

    def test_shortcuts_dispatch_fixed_facade_callbacks_and_reopening_reuses_the_chat(self):
        dock = self.show()
        views._shortcut_facade.assert_not_called()
        for suffix, callback in (("DocumentToolsAction", self.facade.show_document_tools_dialog),
                                 ("AxonometricFitAction", self.facade.run_axonometric_fit),
                                 ("AppearanceAction", self.facade.show_appearance_dialog)):
            self.named(suffix).click()
            callback.assert_called_once_with()
        self.assertEqual(views._shortcut_facade.call_count, 3)
        self.gui.runCommand.assert_not_called()
        self.assertIs(self.show(), dock)
        self.assertEqual((len(self.controllers), len(self.processes)), (1, 1))
        self.controllers[0].connect.assert_not_called()
        self.controllers[0].disconnect.assert_not_called()
        self.assertEqual(self.get_status.call_count, 1)

    def test_shortcut_failures_are_visible_without_native_exception_details(self):
        self.show()
        message = i18n.tr("快捷操作失败；未自动保存 CAD 文档。")
        for suffix, callback in (("DocumentToolsAction", self.facade.show_document_tools_dialog),
                                 ("AxonometricFitAction", self.facade.run_axonometric_fit),
                                 ("AppearanceAction", self.facade.show_appearance_dialog)):
            with self.subTest(action=suffix):
                callback.side_effect = RuntimeError("SYNTHETIC_NATIVE_DETAIL")
                self.named(suffix).click()
                self.assertEqual(self.named("Status").value, message)
                self.assertNotIn("SYNTHETIC_NATIVE_DETAIL", self.named("Status").value)
                callback.side_effect = None
        self.assertEqual(self.app.Console.PrintError.call_count, 3)
        self.assertTrue(all(call.args == ("[Phoenix Tools] " + message + "\n",)
                            for call in self.app.Console.PrintError.call_args_list))
        self.gui.runCommand.assert_not_called()
        self.controllers[0].connect.assert_not_called()

    def test_shortcut_loader_failure_is_visible_before_any_callback(self):
        self.show()
        views._shortcut_facade.side_effect = RuntimeError("SYNTHETIC_LOADER_DETAIL")
        self.named("DocumentToolsAction").click()
        self.assertEqual(self.named("Status").value, i18n.tr("快捷操作失败；未自动保存 CAD 文档。"))
        self.facade.show_document_tools_dialog.assert_not_called()
        self.app.Console.PrintError.assert_called_once()

    def test_shortcut_loader_rejects_foreign_facade_before_callback(self):
        foreign = types.SimpleNamespace(__file__="/fixture/other-addon/pft_workbench_tools.py",
            show_document_tools_dialog=Mock())
        with patch.dict(sys.modules, {"pft_workbench_tools": foreign}):
            with self.assertRaises(RuntimeError):
                self.real_facade_loader()
        foreign.show_document_tools_dialog.assert_not_called()
        local = types.SimpleNamespace(__file__=str(ADDON / "pft_workbench_tools.py"))
        with patch.dict(sys.modules, {"pft_workbench_tools": local}):
            self.assertIs(self.real_facade_loader(), local)

    def test_connection_conveniences_follow_phase_and_empty_tool_diagnostics_stay_hidden(self):
        self.show()
        controller = self.controllers[0]
        for phase in ("disconnected", "connecting", "signed_out", "logging_in", "ready", "sending", "interrupting", "error"):
            with self.subTest(phase=phase):
                controller.push(phase=phase)
                self.assertEqual(self.named("Connect").visible, phase in ("disconnected", "error"))
                self.assertEqual(self.named("Login").visible, phase == "signed_out")
                self.assertEqual(self.named("ConnectionActions").visible, phase in ("disconnected", "error", "signed_out"))
                self.assertEqual(self.named("Executable").enabled, phase in ("disconnected", "error"))
        controller.push(tool_calls=[{"name": "get_document_info", "status": "completed"}])
        self.assertTrue(self.named("ToolStatus").visible)
        self.assertIn("get_document_info", self.named("ToolStatus").text())
        self.assertTrue(self.qt.inside(self.named("ToolStatus"), self.named("Settings")))
        controller.push(tool_calls=[])
        self.assertFalse(self.named("ToolStatus").visible)

    def test_gear_and_font_changes_keep_the_live_connection_and_do_not_save_implicitly(self):
        dock = self.show()
        controller = self.controllers[0]
        controller.push(phase="ready", can_send=True, transcript=[{"role": "assistant", "text": "Public fixture answer"}])
        before = deepcopy(controller.snapshot)
        gear = self.named("SettingsToggle")
        gear.click(); self.assertTrue(self.named("Settings").visible)
        font = self.named("FontSize"); font.setCurrentIndex(font.findData(18))
        self.assertEqual(self.named("Input").font().point_size, 18)
        gear.click(); self.assertFalse(self.named("Settings").visible)
        self.assertEqual(controller.snapshot, before)
        self.assertIn("Public fixture answer", self.named("Transcript").toPlainText())
        self.assertIs(self.show(), dock)
        controller.connect.assert_not_called(); controller.disconnect.assert_not_called()
        self.settings.save_chat_preferences.assert_not_called()
        self.settings.save_chat_options.assert_not_called()

    def test_relocated_controls_keep_explicit_connect_login_send_stop_and_save_wiring(self):
        self.show()
        controller = self.controllers[0]
        self.named("Connect").click()
        controller.connect.assert_called_once_with("/fixture/codex", "/fixture/home", "/fixture/work", mcp=None, model_id=None)
        controller.push(phase="signed_out")
        self.named("Login").click(); controller.login.assert_called_once()
        controller.push(phase="ready", can_send=True)
        self.named("Input").setPlainText("Public fixture request")
        self.named("Send").click()
        controller.send.assert_called_once_with("Public fixture request")
        self.assertEqual(self.named("Input").toPlainText(), "")
        self.named("Stop").click(); controller.interrupt.assert_called_once()
        self.named("SavePreferences").click()
        self.settings.save_chat_preferences.assert_called_once_with(self.app, "", 0)
        self.named("Disconnect").click(); controller.disconnect.assert_called_once()
        self.named("SaveTools").click()
        self.settings.save_chat_options.assert_called_once_with(self.app, False, "/fixture/node", "/fixture/mcp/dist/cli.js", desk_enabled=False)
        self.qt.named("PftChatCopyPrompt").click(); self.assertEqual(len(self.qt.clipboard), 1)
        self.qt.named("PftChatOfficialDocs").click()
        self.qt.gui.QDesktopServices.openUrl.assert_called_once_with("https://learn.chatgpt.com/docs/app-server")

    def test_long_configuration_and_models_do_not_set_a_wide_minimum(self):
        self.settings.load_chat_preferences.return_value = {"model_id": "fixture-model-" * 40, "font_point_size": 0}
        self.show()
        for suffix in ("Executable", "Node", "MCPCLI", "Model", "FontSize", "ModelsStatus", "Status", "Transcript", "Input"):
            widget = self.named(suffix)
            self.assertEqual(widget.minimum_width, 0, suffix)
            self.assertEqual(widget.policy.horizontal, self.qt.widgets.QSizePolicy.Ignored, suffix)
        self.assertEqual(self.named("Model").minimum_contents_length, 1)
        self.assertIn("fixture-model-" * 40, self.named("Model").currentData())

    def test_busy_close_keeps_cancel_default_and_disconnects_only_after_explicit_confirmation(self):
        dock = self.show()
        controller = self.controllers[0]
        controller.push(phase="sending", is_busy=True)
        self.assertFalse(dock.close())
        self.assertFalse(dock._closed)
        controller.disconnect.assert_not_called()
        question = self.qt.widgets.QMessageBox.question
        self.assertEqual(question.call_args.args[-1], self.qt.widgets.QMessageBox.Cancel)
        question.return_value = self.qt.widgets.QMessageBox.Yes
        self.assertTrue(dock.close())
        self.assertTrue(dock._closed)
        controller.disconnect.assert_called_once()



    def test_desk_is_off_by_default_and_only_explicit_save_or_connect_reads_configuration(self):
        self.show()
        desk = self.named("EnableDeskTools")
        self.assertFalse(desk.isChecked()); self.assertFalse(desk.isEnabled())
        self.settings.load_chat_desk_configuration.assert_not_called()
        self.named("EnableTools").setChecked(True)
        self.assertTrue(desk.isEnabled()); desk.setChecked(True)
        self.settings.load_chat_desk_configuration.assert_not_called()
        self.named("SaveTools").click()
        self.settings.load_chat_desk_configuration.assert_called_once_with(self.app)
        self.settings.load_chat_desk_configuration.reset_mock()
        self.named("Connect").click()
        self.settings.load_chat_desk_configuration.assert_called_once_with(self.app)
        options = self.settings.build_chat_mcp_config.call_args.args[0]
        self.assertTrue(options['enabled']); self.assertTrue(options['desk_enabled'])
        self.assertEqual(self.settings.build_chat_mcp_config.call_args.kwargs,
                         {'desk_configuration': self.settings.load_chat_desk_configuration.return_value})
        self.controllers[0].push(phase='ready')
        self.assertFalse(desk.isEnabled())
        self.controllers[0].connect.assert_called_once()
        self.controllers[0].push(phase='disconnected')
        self.named("EnableTools").setChecked(False)
        self.assertFalse(desk.isChecked()); self.assertFalse(desk.isEnabled())

    def test_missing_desk_configuration_prevents_save_and_child_connection(self):
        self.show(); self.named("EnableTools").setChecked(True); self.named("EnableDeskTools").setChecked(True)
        self.settings.load_chat_desk_configuration.side_effect = self.settings.PftChatSettingsError("Neutral configuration unavailable")
        self.named("SaveTools").click()
        self.settings.save_chat_options.assert_not_called()
        self.named("Connect").click()
        self.controllers[0].connect.assert_not_called()
        self.assertEqual(self.named("Status").text(), 'Neutral configuration unavailable')
        self.profile.prepare_chat_profile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
