# SPDX-License-Identifier: Apache-2.0
"""Chat presentation, command shortcuts and explicit actions without Qt or CAD."""
from copy import deepcopy
import json
from html.parser import HTMLParser
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
    import pft_chat_controller as chat_controller
    import pft_chat_network as chat_network
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
            def palette(self):
                return types.SimpleNamespace(color=lambda _role: types.SimpleNamespace(
                    lightness=lambda: getattr(self, 'base_lightness', 255)))
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
            def count(self): return len(self.items)
            def currentText(self): return self.items[self.index][0] if 0 <= self.index < len(self.items) else ""
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
            def keyPressEvent(self, event):
                self.forwarded_key = event.key()
                if not getattr(self, 'preedit', '') and event.key() in (0x1000004, 0x1000005):
                    self.value += '\n'
            def inputMethodEvent(self, event):
                self.preedit = event.preeditString()
                self.value += event.commitString()

        class Browser(Editor):
            def __init__(self):
                super().__init__()
                self.html, self.html_updates = '', 0
            def setOpenExternalLinks(self, value): self.open_external_links = value
            def setOpenLinks(self, value): self.open_links = value
            def setTextInteractionFlags(self, value): self.interaction_flags = value
            def setWordWrapMode(self, value): self.word_wrap_mode = value
            def setHtml(self, value):
                self.html, self.html_updates = value, self.html_updates + 1
                class TextParser(HTMLParser):
                    def __init__(self): super().__init__(); self.parts = []
                    def handle_data(self, data): self.parts.append(data)
                parser = TextParser(); parser.feed(value)
                self.value = ''.join(parser.parts)

        class Timer:
            def __init__(self, parent): self.parent, self.active, self.timeout = parent, False, Signal()
            def setInterval(self, value): self.interval = value
            def isActive(self): return self.active
            def start(self): self.active = True
            def stop(self): self.active = False
            def fire(self):
                if self.active: self.timeout.emit()

        class Scroll(Widget):
            def setWidgetResizable(self, value): self.widget_resizable = value
            def setFrameShape(self, value): self.frame = value
            def setHorizontalScrollBarPolicy(self, value): self.horizontal_scroll_policy = value
            def setWidget(self, value): self.content = value; value.setParent(self)

        self.core = types.SimpleNamespace(Qt=types.SimpleNamespace(
            WA_DeleteOnClose=1, PlainText=2, ToolButtonIconOnly=3, ScrollBarAlwaysOff=4,
            Key_Return=0x1000004, Key_Enter=0x1000005, NoModifier=0, ShiftModifier=0x2000000,
            KeypadModifier=0x20000000, TextSelectableByMouse=1, TextSelectableByKeyboard=2),
            QUrl=lambda value: value, QTimer=Timer)
        self.gui = types.SimpleNamespace(QFont=Font, QIcon=lambda value: value,
            QTextOption=types.SimpleNamespace(WrapAtWordBoundaryOrAnywhere=4), QPalette=types.SimpleNamespace(Base=9),
            QDesktopServices=types.SimpleNamespace(openUrl=Mock(return_value=True)))
        self.widgets = types.SimpleNamespace(QDockWidget=Dock, QWidget=Widget, QVBoxLayout=Layout, QHBoxLayout=Layout,
            QLabel=Text, QLineEdit=Text, QToolButton=Button, QPushButton=Button, QCheckBox=Button, QComboBox=Combo,
            QPlainTextEdit=Editor, QTextBrowser=Browser, QScrollArea=Scroll, QSizePolicy=Policy,
            QFrame=types.SimpleNamespace(NoFrame=0),
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


class ChatKeyEvent:
    def __init__(self, key=0x1000004, modifiers=0, repeat=False):
        self.code, self.flags, self.repeat, self.accepted = key, modifiers, repeat, False
    def key(self): return self.code
    def modifiers(self): return self.flags
    def isAutoRepeat(self): return self.repeat
    def accept(self): self.accepted = True


class ChatRpcTransport:
    """Connect the real controller to the view without starting any process."""
    def __init__(self):
        self.messages, self.stops, self.starts = [], 0, []
        self.has_active_session = False
    def start(self, _program, _args, _env, _cwd, on_data, _on_exit, _on_error):
        self.data = on_data
        self.starts.append({key: value for key, value in _env.items() if key in chat_network.PFT_PROXY_ENV_KEYS})
    def write(self, data): self.messages.append(json.loads(data))
    def arm_timeout(self, _milliseconds, on_timeout=None): pass
    def clear_timeout(self): pass
    def stop(self): self.stops += 1
    def result(self, method, result):
        request = next(row for row in reversed(self.messages) if row.get('method') == method)
        self.data({'id': request['id'], 'result': result})


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
            def __init__(self, core, parent, lease_factory=None):
                self.lease_factory=lease_factory
                self.has_active_session = False
                owner.processes.append(self)
        class Controller:
            def __init__(self, process, changed, open_url):
                self.changed, self.open_url = changed, open_url
                self.snapshot = {"phase": "disconnected", "transcript": [], "can_send": False,
                                 "is_busy": False, "connection_generation": 0}
                self.connect, self.login, self.disconnect = Mock(), Mock(), Mock()
                self.reopen_login, self.copy_login_link = Mock(), Mock(return_value=False)
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
            load_chat_history_preference=Mock(return_value=False),
            save_chat_options=Mock(side_effect=lambda app, enabled, node, cli, desk_enabled=False:
                {"enabled": enabled, "desk_enabled": desk_enabled, "node_executable": node, "cli_entry": cli}),
            load_chat_desk_configuration=Mock(return_value={"node_executable": "/fixture/desk-node",
                "cli_entry": "/fixture/desk/scripts/desk-web-service.mjs", "state_dir": "/fixture/desk-state"}),
            save_chat_preferences=Mock(), build_chat_mcp_config=Mock(), PftChatSettingsError=SettingsError)
        self.lease_factory = Mock()
        class LeaseError(RuntimeError):
            def __init__(self, message):
                self.message = message
                super().__init__(message)
        self.lease_error = LeaseError
        self.registration_inspector = Mock(return_value=types.SimpleNamespace(
            status="absent", child_pid=None, can_clear=False, clear_exited=Mock(return_value=True)))
        self.session_store = types.SimpleNamespace(home="/fixture/home", cwd="/fixture/work")
        self.session_store_factory = Mock(return_value=self.session_store)
        self.session_reader = Mock(return_value=[])
        class SessionStoreError(RuntimeError):
            def __init__(self, message): self.message = message; super().__init__(message)
        self.session_error = SessionStoreError
        modules = {"pft_chat_session_store": types.SimpleNamespace(PftChatSessionStore=self.session_store_factory,
                    PftChatSessionStoreError=SessionStoreError, list_user_sessions=self.session_reader),
            "pft_chat_process_lease": types.SimpleNamespace(acquire=self.lease_factory,
                    inspect_user_registration=self.registration_inspector, PftChatProcessLeaseError=LeaseError),
            "PySide": types.SimpleNamespace(QtCore=self.qt.core, QtGui=self.qt.gui, QtWidgets=self.qt.widgets),
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

    def connected_real_chat(self, load_models=True, account="chatgpt"):
        transport = ChatRpcTransport()
        modules = {
            'pft_chat_controller': types.SimpleNamespace(PftChatController=lambda process, changed, open_url:
                chat_controller.PftChatController(process, changed, open_url, network_environment=lambda: {})),
            'pft_chat_qprocess': types.SimpleNamespace(PftChatProcess=lambda _core, _parent, lease_factory=None: transport)}
        with patch.dict(sys.modules, modules):
            dock = self.show()
            self.named('Connect').click()
        transport.result('initialize', {'userAgent': 'codex_cli_rs/0.160.0 (neutral fixture)'})
        transport.result('account/read', {'account': {'type': account} if account else None})
        if load_models and account:
            transport.result('model/list', {'data': []})
        return dock._controller, transport

    def pass_chat_config(self, transport):
        config = {'approval_policy': 'never', 'sandbox_mode': 'read-only', 'model_provider': 'openai',
            'forced_login_method': 'chatgpt', 'web_search': 'disabled', 'cli_auth_credentials_store': 'file',
            'features': {name: False for name in chat_controller._PFT_DISABLE}, 'tools': {'view_image': False}}
        transport.result('config/read', {'config': config})

    def finish_preparation(self, transport, new_thread=True):
        self.pass_chat_config(transport)
        transport.result('mcpServerStatus/list', {'data': []})
        if new_thread:
            transport.result('thread/start', {'thread': {
                'id': 'neutral-thread', 'cliVersion': '0.160.0', 'ephemeral': True},
                'model': 'neutral-model', 'modelProvider': 'openai', 'cwd': '/fixture/work',
                'approvalPolicy': 'never', 'sandbox': {'type': 'readOnly', 'networkAccess': False}})
        transport.result('mcpServerStatus/list', {'data': []})

    def proxy_real_view(self, reader):
        transport = ChatRpcTransport()
        resolve = lambda: chat_network.build_chat_proxy_environment({"NO_PROXY": "example.invalid"}, reader)
        modules = {
            "pft_chat_controller": types.SimpleNamespace(PftChatController=lambda process, changed, open_url:
                chat_controller.PftChatController(process, changed, open_url, network_environment=resolve)),
            "pft_chat_qprocess": types.SimpleNamespace(PftChatProcess=lambda _core, _parent, lease_factory=None: transport)}
        with patch.dict(sys.modules, modules):
            dock = self.show()
        return dock._controller, transport

    @staticmethod
    def retained_session():
        return {"thread_id": "neutral-owned-session", "created_at": 1700000000, "model": "fixture-model",
                "scope_fingerprint": "a" * 64, "history_mode": "paginated", "cli_version": "0.160.0", "provider": "openai"}

    def test_history_controls_default_to_new_temporary_chat_without_reading_registry(self):
        self.show()
        self.named("SettingsToggle").click()
        self.assertFalse(self.named("RetainHistory").isChecked())
        self.assertIsNone(self.named("SavedSessions").currentData())
        self.session_reader.assert_not_called()
        self.session_store_factory.assert_not_called()
        self.profile.prepare_chat_profile.assert_not_called()

    def test_explicit_registry_list_keeps_draft_and_only_projects_timestamp_and_model(self):
        self.session_reader.return_value = [self.retained_session()]
        self.show()
        self.named("Input").setPlainText("Neutral unsent message")
        self.named("ListSessions").click()
        self.session_reader.assert_called_once_with("/fixture/user-data")
        self.assertEqual(self.named("SavedSessions").count(), 2)
        self.named("SavedSessions").setCurrentIndex(1)
        self.assertNotIn("neutral-owned-session", self.named("SavedSessions").currentText())
        self.assertEqual(self.named("Input").toPlainText(), "Neutral unsent message")
        self.controllers[0].connect.assert_not_called()
        self.profile.prepare_chat_profile.assert_not_called()
        self.session_store_factory.assert_not_called()

    def test_selected_own_session_connects_with_fresh_store_and_explicit_resume_metadata(self):
        record = self.retained_session()
        self.session_reader.return_value = [record]
        self.show()
        self.named("ListSessions").click()
        self.named("SavedSessions").setCurrentIndex(1)
        self.named("Connect").click()
        self.session_store_factory.assert_called_once_with("/fixture/home", "/fixture/work")
        self.controllers[0].connect.assert_called_once_with("/fixture/codex", "/fixture/home", "/fixture/work",
            mcp=None, model_id=None, session_store=self.session_store, retain_history=True, resume_session=record)

    def test_opt_in_and_save_do_not_create_session_until_explicit_connection(self):
        self.show()
        self.named("RetainHistory").setChecked(True)
        self.named("SavePreferences").click()
        self.settings.save_chat_preferences.assert_called_once_with(self.app, "", 0, retain_history=True)
        self.session_store_factory.assert_not_called()
        self.controllers[0].connect.assert_not_called()
        self.named("Connect").click()
        self.controllers[0].connect.assert_called_once_with("/fixture/codex", "/fixture/home", "/fixture/work",
            mcp=None, model_id=None, session_store=self.session_store, retain_history=True, resume_session=None)

    def test_registry_list_failure_is_fixed_and_never_falls_back_to_other_history(self):
        self.session_reader.side_effect = RuntimeError("synthetic-private-history-path")
        self.show()
        self.named("ListSessions").click()
        self.assertNotIn("synthetic-private", self.named("SavedSessionsStatus").text())
        self.controllers[0].connect.assert_not_called()
        self.session_store_factory.assert_not_called()

    def test_loading_or_read_only_history_disables_send_and_configuration(self):
        self.show()
        controller = self.controllers[0]
        for phase, busy in (("restoring", True), ("history_read_only", False)):
            with self.subTest(phase=phase):
                controller.push(phase=phase, is_busy=busy, can_send=False, history_status="loading" if busy else "read_only",
                                history_notice="Synthetic fixed history notice")
                self.assertFalse(self.named("Send").isEnabled())
                self.assertFalse(self.named("RetainHistory").isEnabled())
                self.assertFalse(self.named("SavedSessions").isEnabled())
                self.assertFalse(self.named("ListSessions").isEnabled())
                self.assertIn("Synthetic fixed history notice", self.named("HistoryStatus").text())
        self.session_reader.assert_not_called()

    def test_native_view_supplies_lease_factory_without_acquiring_on_open(self):
        self.show()
        self.assertIs(self.processes[0].lease_factory, self.lease_factory)
        self.lease_factory.assert_not_called()
        self.profile.prepare_chat_profile.assert_not_called()


    def registration(self, status='exited', pid=31234, can_clear=True):
        inspection = types.SimpleNamespace(status=status, child_pid=pid, can_clear=can_clear,
                                           clear_exited=Mock(return_value=True))
        self.registration_inspector.return_value = inspection
        return inspection

    def assert_registration_did_not_drive_chat(self):
        controller = self.controllers[0]
        for method in ('connect', 'login', 'send', 'disconnect', 'interrupt'):
            getattr(controller, method).assert_not_called()
        self.profile.prepare_chat_profile.assert_not_called()
        self.lease_factory.assert_not_called()

    def test_registration_check_is_settings_only_and_opening_panel_or_gear_does_not_inspect(self):
        self.show()
        settings = self.named('Settings')
        self.assertTrue(self.qt.inside(self.named('CheckProcessRegistration'), settings))
        self.assertTrue(self.qt.inside(self.named('ProcessRegistrationStatus'), settings))
        self.named('SettingsToggle').click()
        self.registration_inspector.assert_not_called()
        self.named('CheckProcessRegistration').click()
        self.registration_inspector.assert_called_once_with('/fixture/user-data')
        self.assertIn('没有聊天进程登记', self.named('ProcessRegistrationStatus').text())
        self.qt.widgets.QMessageBox.question.assert_not_called()
        self.assert_registration_did_not_drive_chat()

    def test_present_unknown_and_absent_registration_never_offer_cleanup(self):
        for state, pid in (('present', 31234), ('unknown', None), ('absent', None)):
            with self.subTest(state=state):
                self.setUp()
                self.show()
                inspection = self.registration(state, pid, False)
                self.named('CheckProcessRegistration').click()
                inspection.clear_exited.assert_not_called()
                self.qt.widgets.QMessageBox.question.assert_not_called()
                self.assert_registration_did_not_drive_chat()
                self.assertNotIn('已清除', self.named('ProcessRegistrationStatus').text())
                if state == 'present':
                    self.assertIn('不代表它仍属于原聊天进程', self.named('ProcessRegistrationStatus').text())

    def test_exited_registration_requires_native_default_cancel_and_preserves_draft_history_and_model(self):
        self.show()
        controller = self.controllers[0]
        controller.push(transcript=[{'role': 'assistant', 'text': 'Neutral prior answer', 'status': 'complete'}],
                        message_times=['10:20'])
        self.named('Input').setPlainText('Neutral unsent draft')
        self.named('Model').addItem('Neutral model', 'neutral-model')
        self.named('Model').setCurrentIndex(1)
        snapshot = deepcopy(controller.snapshot)
        inspection = self.registration()
        self.named('CheckProcessRegistration').click()
        question = self.qt.widgets.QMessageBox.question
        self.assertEqual(question.call_args.args[1], '清除已退出的聊天进程登记')
        self.assertIn('31234', question.call_args.args[2])
        self.assertIn('只清除这一笔进程登记', question.call_args.args[2])
        self.assertIn('不删除登录或聊天数据，也不启动服务', question.call_args.args[2])
        self.assertEqual(question.call_args.args[-1], self.qt.widgets.QMessageBox.Cancel)
        inspection.clear_exited.assert_not_called()
        self.assertIn('已取消', self.named('ProcessRegistrationStatus').text())
        self.assertEqual(controller.snapshot, snapshot)
        self.assertEqual(self.named('Input').toPlainText(), 'Neutral unsent draft')
        self.assertEqual(self.named('Model').currentData(), 'neutral-model')
        self.assert_registration_did_not_drive_chat()

    def test_registration_cleanup_runs_once_only_after_yes_and_does_not_connect(self):
        self.show()
        inspection = self.registration()
        self.qt.widgets.QMessageBox.question.return_value = self.qt.widgets.QMessageBox.Yes
        self.named('CheckProcessRegistration').click()
        inspection.clear_exited.assert_called_once_with()
        self.assertIn('可手动连接', self.named('ProcessRegistrationStatus').text())
        self.assertIn('未启动服务', self.named('ProcessRegistrationStatus').text())
        self.assert_registration_did_not_drive_chat()

    def test_checking_requires_explicit_inactive_phase_and_valid_generation_and_no_transport_session(self):
        self.show()
        controller, process = self.controllers[0], self.processes[0]
        blocked = [
            {'phase': phase, 'is_busy': False, 'connection_generation': 0}
            for phase in ('ready', 'signed_out', 'connecting', 'logging_in', 'sending', 'interrupting', 'unknown')]
        blocked += [{'phase': 'disconnected', 'is_busy': True, 'connection_generation': 0}]
        for updates in blocked:
            controller.push(**updates)
            self.assertFalse(self.named('CheckProcessRegistration').isEnabled())
            self.named('CheckProcessRegistration').click()
        for generation in (None, True, '0', -1):
            controller.push(phase='disconnected', is_busy=False, connection_generation=generation)
            self.named('CheckProcessRegistration').click()
        controller.snapshot.pop('connection_generation', None)
        self.named('CheckProcessRegistration').click()
        controller.push(phase='disconnected', is_busy=False, connection_generation=1)
        for active in (True, None, 'false'):
            process.has_active_session = active
            controller.push()
            self.assertFalse(self.named('CheckProcessRegistration').isEnabled())
            self.named('CheckProcessRegistration').click()
        del process.has_active_session
        self.named('CheckProcessRegistration').click()
        self.registration_inspector.assert_not_called()
        self.qt.widgets.QMessageBox.question.assert_not_called()
        process.has_active_session = False
        controller.push(phase='error', is_busy=False, connection_generation=2)
        self.assertTrue(self.named('CheckProcessRegistration').isEnabled())
        self.named('CheckProcessRegistration').click()
        self.registration_inspector.assert_called_once_with('/fixture/user-data')
        self.assert_registration_did_not_drive_chat()

    def test_confirmation_rechecks_generation_phase_and_transport_before_any_cleanup(self):
        for change in ('generation', 'phase', 'process', 'connect_attempt'):
            with self.subTest(change=change):
                self.setUp()
                self.show()
                controller, process = self.controllers[0], self.processes[0]
                inspection = self.registration()
                def confirmation(*_args):
                    if change == 'generation':
                        controller.push(connection_generation=1)
                    elif change == 'phase':
                        controller.push(phase='signed_out')
                    elif change == 'process':
                        process.has_active_session = True
                    else:
                        self.named('Connect').click()
                    return self.qt.widgets.QMessageBox.Yes
                self.qt.widgets.QMessageBox.question.side_effect = confirmation
                self.named('CheckProcessRegistration').click()
                inspection.clear_exited.assert_not_called()
                self.assertIn('状态已变化', self.named('ProcessRegistrationStatus').text())
                if change != 'connect_attempt':
                    self.assert_registration_did_not_drive_chat()

    def test_closing_panel_during_confirmation_cancels_pending_cleanup(self):
        dock = self.show()
        inspection = self.registration()
        def close_panel(*_args):
            self.assertTrue(dock.close())
            return self.qt.widgets.QMessageBox.Yes
        self.qt.widgets.QMessageBox.question.side_effect = close_panel
        self.named('CheckProcessRegistration').click()
        self.assertTrue(dock._closed)
        inspection.clear_exited.assert_not_called()
        self.controllers[0].disconnect.assert_called_once_with()
        self.registration_inspector.assert_called_once_with('/fixture/user-data')

    def test_controller_change_during_inspection_does_not_open_confirmation(self):
        self.show()
        controller = self.controllers[0]
        inspection = self.registration()
        def inspect(_directory):
            controller.push(connection_generation=1)
            return inspection
        self.registration_inspector.side_effect = inspect
        self.named('CheckProcessRegistration').click()
        inspection.clear_exited.assert_not_called()
        self.qt.widgets.QMessageBox.question.assert_not_called()
        self.assertIn('状态已变化', self.named('ProcessRegistrationStatus').text())
        self.assert_registration_did_not_drive_chat()

    def test_helper_errors_are_sanitized_and_cleanup_uncertainty_never_claims_record_is_preserved(self):
        self.show()
        self.registration_inspector.side_effect = RuntimeError('synthetic-private-registration-error')
        self.named('CheckProcessRegistration').click()
        self.assertIn('操作未确认', self.named('ProcessRegistrationStatus').text())
        self.assertNotIn('synthetic-private', self.named('ProcessRegistrationStatus').text())
        self.registration_inspector.side_effect = None
        inspection = self.registration()
        self.qt.widgets.QMessageBox.question.return_value = self.qt.widgets.QMessageBox.Yes
        inspection.clear_exited.side_effect = self.lease_error('登记释放无法确认；请重新检查。')
        self.named('CheckProcessRegistration').click()
        self.assertEqual(self.named('ProcessRegistrationStatus').text(), '登记释放无法确认；请重新检查。')
        self.assertNotIn('保留', self.named('ProcessRegistrationStatus').text())
        self.assertNotIn('已清除', self.named('ProcessRegistrationStatus').text())
        self.assert_registration_did_not_drive_chat()

    def test_invalid_inspection_and_unconfirmed_cleanup_never_report_success(self):
        self.show()
        inspection = self.registration('exited', True, True)
        self.named('CheckProcessRegistration').click()
        inspection.clear_exited.assert_not_called()
        self.qt.widgets.QMessageBox.question.assert_not_called()
        inspection = self.registration()
        inspection.clear_exited.return_value = None
        self.qt.widgets.QMessageBox.question.return_value = self.qt.widgets.QMessageBox.Yes
        self.named('CheckProcessRegistration').click()
        inspection.clear_exited.assert_called_once_with()
        self.assertIn('操作未确认', self.named('ProcessRegistrationStatus').text())
        self.assertNotIn('已清除', self.named('ProcessRegistrationStatus').text())
        self.assert_registration_did_not_drive_chat()

    def test_pending_confirmation_rejects_a_reentrant_check_signal_without_second_inspection(self):
        self.show()
        inspection = self.registration()
        def reentrant_check(*_args):
            self.named('CheckProcessRegistration').clicked.emit()
            return self.qt.widgets.QMessageBox.Yes
        self.qt.widgets.QMessageBox.question.side_effect = reentrant_check
        self.named('CheckProcessRegistration').click()
        self.registration_inspector.assert_called_once_with('/fixture/user-data')
        self.qt.widgets.QMessageBox.question.assert_called_once()
        inspection.clear_exited.assert_called_once_with()
        self.assert_registration_did_not_drive_chat()

    def test_transport_finishing_after_disconnect_reenables_check_using_only_memory_timer(self):
        dock = self.show()
        process, controller = self.processes[0], self.controllers[0]
        process.has_active_session = True
        controller.push(phase='disconnected', is_busy=False)
        self.assertFalse(self.named('CheckProcessRegistration').isEnabled())
        self.assertTrue(dock._status_timer.isActive())
        process.has_active_session = False
        dock._status_timer.fire()
        self.assertTrue(self.named('CheckProcessRegistration').isEnabled())
        self.assertFalse(dock._status_timer.isActive())
        self.registration_inspector.assert_not_called()
        self.profile.prepare_chat_profile.assert_not_called()

    def test_explicit_connect_passes_system_proxy_without_login_or_sending_the_draft(self):
        reader = Mock(return_value={"proxies": {"https": "http://proxy.example.invalid:8080"},
            "bypass": ["198.51.100.0/24"], "exclude_simple": False, "pac_enabled": False, "auto_detect": False})
        controller, transport = self.proxy_real_view(reader)
        reader.assert_not_called()
        self.named("Input").setPlainText("Neutral unsent draft")
        self.named("Connect").click()
        reader.assert_called_once_with()
        self.assertEqual(transport.starts[0]["HTTPS_PROXY"], "http://proxy.example.invalid:8080")
        self.assertEqual(transport.starts[0]["NO_PROXY"], "example.invalid,198.51.100.0/24,localhost,127.0.0.1,::1")
        self.assertEqual(controller.snapshot["phase"], "connecting")
        self.assertEqual([row["method"] for row in transport.messages], ["initialize"])
        self.assertEqual(self.named("Input").toPlainText(), "Neutral unsent draft")
        self.qt.gui.QDesktopServices.openUrl.assert_not_called()
        self.assertNotIn("proxy.example.invalid", self.named("Status").text())

    def test_proxy_policy_error_keeps_connect_available_and_preserves_draft_for_manual_retry(self):
        reader = Mock(return_value={"proxies": {}, "bypass": [], "exclude_simple": False,
                                   "pac_enabled": True, "auto_detect": False})
        controller, transport = self.proxy_real_view(reader)
        self.named("Input").setPlainText("Neutral unsent draft")
        self.named("Connect").click()
        self.assertEqual(controller.snapshot["error"]["code"], "CHAT_PROXY_UNSUPPORTED")
        self.assertEqual(controller.snapshot["phase"], "disconnected")
        self.assertTrue(self.named("Connect").isEnabled())
        self.assertTrue(self.named("Connect").visible)
        self.assertEqual(transport.starts, [])
        self.assertEqual(transport.messages, [])
        self.assertEqual(self.named("Input").toPlainText(), "Neutral unsent draft")
        self.assertIn("系统代理规则", self.named("Status").text())
        reader.return_value["pac_enabled"] = False
        self.named("Connect").click()
        self.assertEqual(reader.call_count, 2)
        self.assertEqual(controller.snapshot["phase"], "connecting")
        self.assertEqual(len(transport.starts), 1)
        self.assertEqual(self.named("Input").toPlainText(), "Neutral unsent draft")
        self.qt.gui.QDesktopServices.openUrl.assert_not_called()

    def test_preflight_failure_preserves_unsent_draft(self):
        controller, transport = self.connected_real_chat()
        draft = 'Neutral request to retain after preparation fails'
        self.named('Input').setPlainText(draft)
        self.named('Send').click()
        request = transport.messages[-1]
        self.assertEqual(request['method'], 'config/read')
        self.assertEqual(self.named('Input').toPlainText(), draft)
        transport.data({'id': request['id'], 'error': {'code': -32000, 'message': 'Neutral failure'}})
        self.assertEqual(controller.snapshot['phase'], 'error')
        self.assertEqual(controller.snapshot['transcript'], [])
        self.assertEqual(self.named('Input').toPlainText(), draft)
        self.assertNotIn('turn/start', [row.get('method') for row in transport.messages])

    def test_preparation_stop_preserves_unsent_draft(self):
        controller, transport = self.connected_real_chat()
        draft = 'Neutral request stopped during preparation'
        self.named('Input').setPlainText(draft)
        self.named('Send').click()
        self.named('Stop').click()
        self.assertEqual(controller.snapshot['error']['code'], 'CHAT_PREPARATION_CANCELLED')
        self.assertEqual(controller.snapshot['transcript'], [])
        self.assertEqual(self.named('Input').toPlainText(), draft)
        self.assertEqual(transport.stops, 1)
        self.assertNotIn('turn/start', [row.get('method') for row in transport.messages])

    def test_submission_clears_only_its_recorded_draft_and_preserves_later_edits(self):
        controller, transport = self.connected_real_chat(load_models=False)
        draft = 'Neutral repeated request'
        self.named('Input').setPlainText(draft)
        self.named('Send').click()
        self.assertEqual(self.named('Input').toPlainText(), draft)
        self.finish_preparation(transport)
        self.assertEqual(transport.messages[-1]['method'], 'turn/start')
        self.assertEqual(self.named('Input').toPlainText(), '')
        self.assertEqual(controller.snapshot['transcript'][-1]['text'], draft)
        transport.result('turn/start', {'turn': {'id': 'neutral-turn', 'items': [], 'status': 'inProgress'}})
        transport.data({'method': 'turn/completed', 'params': {'threadId': 'neutral-thread',
            'turn': {'id': 'neutral-turn', 'items': [], 'status': 'interrupted'}}})
        self.assertEqual(self.named('Input').toPlainText(), '')
        self.assertIsNone(controller.snapshot['error'])
        self.named('Input').setPlainText(draft)
        self.named('Send').click()
        self.assertEqual(self.named('Input').toPlainText(), draft)
        newer_draft = 'Neutral later edit while waiting'
        self.named('Input').setPlainText(newer_draft)
        self.finish_preparation(transport, new_thread=False)
        self.assertEqual(self.named('Input').toPlainText(), newer_draft)
        self.assertEqual([row['text'] for row in controller.snapshot['transcript']], [draft, draft])


    def test_failed_terminal_disables_send_until_reconnect_and_retains_new_unsent_draft(self):
        controller, transport = self.connected_real_chat()
        self.named('Input').setPlainText('Neutral failed terminal request')
        self.named('Send').click()
        self.finish_preparation(transport)
        transport.result('turn/start', {'turn': {'id': 'neutral-turn', 'items': [], 'status': 'inProgress'}})
        transport.data({'method': 'turn/completed', 'params': {'threadId': 'neutral-thread', 'turn': {
            'id': 'neutral-turn', 'items': [], 'status': 'failed',
            'error': {'codexErrorInfo': 'unauthorized', 'message': 'synthetic-private-auth-text'}}}})
        self.assertEqual(controller.snapshot['phase'], 'error')
        self.assertEqual(controller.snapshot['error']['code'], 'CHAT_AUTH_FAILED')
        self.assertFalse(self.named('Send').isEnabled())
        self.assertFalse(self.named('Stop').isEnabled())
        self.assertTrue(self.named('Connect').visible)
        self.assertEqual(self.named('Input').toPlainText(), '')
        before = len(transport.messages)
        self.named('Input').setPlainText('Neutral unsent request after terminal failure')
        self.named('Send').click()
        self.assertEqual(len(transport.messages), before)
        self.assertEqual(self.named('Input').toPlainText(), 'Neutral unsent request after terminal failure')
        self.assertNotIn('synthetic-private-auth-text', self.named('Status').text())

    def test_preflight_failure_and_late_reply_do_not_overwrite_newer_draft(self):
        controller, transport = self.connected_real_chat()
        self.named('Input').setPlainText('Neutral first draft')
        self.named('Send').click()
        request = transport.messages[-1]
        newer_draft = 'Neutral replacement while waiting'
        self.named('Input').setPlainText(newer_draft)
        transport.data({'id': request['id'], 'error': {'code': -32000, 'message': 'Neutral failure'}})
        count = len(transport.messages)
        transport.data({'id': request['id'], 'result': {}})
        self.assertEqual(self.named('Input').toPlainText(), newer_draft)
        self.assertEqual(controller.snapshot['transcript'], [])
        self.assertEqual(len(transport.messages), count)
        self.assertEqual(transport.stops, 1)

    def test_tool_inventory_rejection_preserves_unsent_draft(self):
        controller, transport = self.connected_real_chat()
        draft = 'Neutral request rejected before tools are verified'
        self.named('Input').setPlainText(draft)
        self.named('Send').click()
        self.pass_chat_config(transport)
        transport.result('mcpServerStatus/list', {'data': [{'name': 'neutral-unexpected-server'}]})
        self.assertEqual(controller.snapshot['error']['code'], 'CHAT_MCP_NOT_EMPTY')
        self.assertEqual(controller.snapshot['transcript'], [])
        self.assertEqual(self.named('Input').toPlainText(), draft)
        self.assertNotIn('turn/start', [row.get('method') for row in transport.messages])

    def test_enter_sends_once_and_shift_enter_preserves_multiline_editing(self):
        self.show()
        controller = self.controllers[0]
        controller.push(phase='ready', can_send=True)
        entry = self.named('Input')
        entry.setPlainText('Neutral first line')
        entry.keyPressEvent(ChatKeyEvent(modifiers=self.qt.core.Qt.ShiftModifier))
        self.assertEqual(entry.toPlainText(), 'Neutral first line\n')
        controller.send.assert_not_called()
        enter = ChatKeyEvent()
        entry.keyPressEvent(enter)
        controller.send.assert_called_once_with('Neutral first line\n')
        self.assertTrue(enter.accepted)
        entry.keyPressEvent(ChatKeyEvent(repeat=True))
        controller.send.assert_called_once()
        self.assertEqual(entry.placeholder, 'Enter 发送 · Shift+Enter 换行')

    def test_enter_cannot_send_repeated_disabled_busy_or_unavailable_input(self):
        self.show()
        controller = self.controllers[0]
        controller.push(phase='ready', can_send=True)
        entry = self.named('Input')
        entry.setPlainText('Neutral draft')
        entry.keyPressEvent(ChatKeyEvent(repeat=True))
        entry.setEnabled(False)
        entry.keyPressEvent(ChatKeyEvent())
        entry.setEnabled(True)
        self.named('Send').setEnabled(False)
        entry.keyPressEvent(ChatKeyEvent())
        controller.push(phase='sending', can_send=False)
        entry.keyPressEvent(ChatKeyEvent())
        controller.push(phase='ready', can_send=False)
        entry.keyPressEvent(ChatKeyEvent())
        controller.send.assert_not_called()
        self.assertEqual(entry.toPlainText(), 'Neutral draft')

    def test_ime_candidate_confirmation_reaches_editor_without_starting_a_turn(self):
        controller, transport = self.connected_real_chat()
        entry = self.named('Input')
        entry.inputMethodEvent(types.SimpleNamespace(preeditString=lambda: 'nihao', commitString=lambda: ''))
        entry.keyPressEvent(ChatKeyEvent())
        self.assertEqual(entry.forwarded_key, 0x1000004)
        self.assertNotIn('config/read', [row.get('method') for row in transport.messages])
        entry.inputMethodEvent(types.SimpleNamespace(preeditString=lambda: '', commitString=lambda: '你好'))
        self.assertEqual(entry.toPlainText(), '你好')
        self.assertTrue(controller.snapshot['can_send'])
        entry.keyPressEvent(ChatKeyEvent())
        self.assertEqual(transport.messages[-1]['method'], 'config/read')
        self.assertEqual(entry.toPlainText(), '你好')
        request = transport.messages[-1]
        transport.data({'id': request['id'], 'error': {'code': -32000, 'message': 'Neutral failure'}})
        self.assertEqual(entry.toPlainText(), '你好')

    def test_keypad_enter_supports_scoped_qt_enums(self):
        qt = self.qt.core.Qt
        qt.Key = types.SimpleNamespace(Key_Return=qt.Key_Return, Key_Enter=qt.Key_Enter)
        qt.KeyboardModifier = types.SimpleNamespace(NoModifier=qt.NoModifier, ShiftModifier=qt.ShiftModifier,
            KeypadModifier=qt.KeypadModifier)
        for name in ('Key_Return', 'Key_Enter', 'NoModifier', 'ShiftModifier', 'KeypadModifier'):
            delattr(qt, name)
        self.show()
        controller = self.controllers[0]
        controller.push(phase='ready', can_send=True)
        entry = self.named('Input')
        entry.setPlainText('Neutral keypad request')
        entry.keyPressEvent(ChatKeyEvent(qt.Key.Key_Enter, qt.KeyboardModifier.KeypadModifier))
        controller.send.assert_called_once_with('Neutral keypad request')

    def test_transcript_uses_local_noninteractive_html_and_known_message_times(self):
        self.show()
        browser = self.named('Transcript')
        self.assertFalse(browser.open_external_links)
        self.assertFalse(browser.open_links)
        self.assertEqual(browser.interaction_flags,
            self.qt.core.Qt.TextSelectableByMouse | self.qt.core.Qt.TextSelectableByKeyboard)
        self.assertEqual(browser.word_wrap_mode, self.qt.gui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.assertIsNone(browser.loadResource(2, 'https://example.invalid/neutral-image'))
        self.controllers[0].push(transcript=[{'role': 'assistant', 'text': '<img src="neutral"> & reply'}],
            message_times=['12:34'])
        self.assertIn('&lt;img src=', browser.html)
        self.assertNotIn('<img src=', browser.html)
        self.assertIn('12:34', browser.toPlainText())
        self.assertIn('<img src="neutral"> & reply', browser.toPlainText())

    def test_transcript_html_cache_keeps_scroll_and_font_when_only_status_changes(self):
        self.show()
        controller, browser = self.controllers[0], self.named('Transcript')
        browser.bar.position = 100
        rows = [{'role': 'assistant', 'text': 'Neutral answer'}]
        controller.push(transcript=rows, message_times=['12:34'])
        self.assertEqual(browser.bar.position, 100)
        updates = browser.html_updates
        controller.push(turn_timing={'elapsed_seconds': 2, 'first_response_seconds': 1,
            'running': True, 'finished': False})
        self.assertEqual(browser.html_updates, updates)
        font = self.named('FontSize'); font.setCurrentIndex(font.findData(18))
        self.assertEqual(browser.font().point_size, 18)
        browser.bar.position = 23
        controller.push(transcript=[{'role': 'assistant', 'text': 'Neutral answer continues'}])
        self.assertEqual(browser.bar.position, 23)
        self.assertEqual(browser.font().point_size, 18)
        browser.base_lightness = 10
        controller.push()
        self.assertIn('#e5e9ef', browser.html)

    def test_refresh_with_new_renderer_preserves_session_error_draft_and_scroll(self):
        self.show()
        controller, browser = self.controllers[0], self.named("Transcript")
        with patch.object(views, "render_chat_transcript", return_value="<html><body>Earlier layout</body></html>"):
            controller.push(phase="ready", can_send=True,
                error={"code": "CHAT_FIXTURE_NOTICE", "message": "Neutral existing notice"},
                transcript=[{"role": "user", "text": "Neutral short request"},
                            {"role": "assistant", "text": "Neutral plain reply"}],
                message_times=["10:20", "10:21"])
        self.named("Input").setPlainText("Neutral unsent draft")
        browser.bar.position = 23
        before = deepcopy(controller.snapshot)
        updates = browser.html_updates
        controller.changed(controller.snapshot)
        self.assertEqual(controller.snapshot, before)
        self.assertEqual(self.named("Input").toPlainText(), "Neutral unsent draft")
        self.assertEqual(browser.bar.position, 23)
        self.assertEqual(browser.html_updates, updates + 1)
        self.assertIn('<table align="right"', browser.html)
        self.assertIn("Neutral plain reply", browser.toPlainText())
        self.assertIn("Neutral existing notice", self.named("Status").text())
        controller.changed(controller.snapshot)
        self.assertEqual(browser.html_updates, updates + 1)
        controller.connect.assert_not_called()
        controller.disconnect.assert_not_called()
        controller.login.assert_not_called()
        controller.send.assert_not_called()

    def test_status_timer_updates_elapsed_only_and_stops_on_completion_or_close(self):
        dock = self.show()
        controller, browser, timer = self.controllers[0], self.named('Transcript'), dock._status_timer
        self.assertIs(timer.parent, dock)
        self.assertEqual(timer.interval, 1000)
        self.assertFalse(timer.isActive())
        controller.push(phase='sending', is_busy=True, turn_timing={'elapsed_seconds': 1,
            'first_response_seconds': None, 'running': True, 'finished': False})
        self.assertTrue(timer.isActive())
        self.assertIn('已等待 1 秒', self.named('Status').text())
        updates = browser.html_updates
        controller.snapshot['turn_timing']['elapsed_seconds'] = 3
        timer.fire()
        self.assertIn('已等待 3 秒', self.named('Status').text())
        self.assertEqual(browser.html_updates, updates)
        controller.push(tool_calls=[{'server': 'phoenix_freecad', 'name': 'get_document_info', 'status': 'in_progress'}],
            turn_timing={'elapsed_seconds': 4, 'first_response_seconds': 2,
                'running': True, 'finished': False})
        self.assertIn('正在执行工具…', self.named('Status').text())
        self.assertIn('已用 4 秒', self.named('Status').text())
        self.assertEqual(self.named('Status').tooltip, '首条回复耗时：2 秒')
        controller.push(error={'code': 'CHAT_SERVICE_RECONNECTING', 'message': '模型服务正在恢复连接，请稍候…'})
        self.assertIn('模型服务正在恢复连接', self.named('Status').text())
        self.assertNotIn('正在执行工具', self.named('Status').text())
        controller.push(phase='ready', is_busy=False, error=None, turn_timing={'elapsed_seconds': 5,
            'first_response_seconds': 2, 'running': False, 'finished': True})
        self.assertFalse(timer.isActive())
        self.assertIn('耗时 5 秒', self.named('Status').text())
        controller.push(phase='sending', is_busy=True)
        self.qt.widgets.QMessageBox.question.return_value = self.qt.widgets.QMessageBox.Yes
        self.assertTrue(dock.close())
        self.assertFalse(timer.isActive())
        before = self.named('Status').text()
        controller.snapshot['turn_timing']['elapsed_seconds'] = 99
        timer.timeout.emit()
        self.assertEqual(self.named('Status').text(), before)
        self.assertEqual(browser.html_updates, updates)

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
        for suffix in ("Executable", "Browse", "EnableTools", "EnableDeskTools", "Disconnect", "CheckProcessRegistration", "ProcessRegistrationStatus", "Model", "RefreshModels", "ModelsStatus",
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
                self.assertEqual(self.named("ConnectionActions").visible, phase in ("disconnected", "error", "signed_out", "logging_in"))
                for suffix in ("ReopenLogin", "CopyLoginLink"):
                    self.assertEqual(self.named(suffix).visible, phase == "logging_in")
                    self.assertFalse(self.named(suffix).enabled)
                self.assertEqual(self.named("Executable").enabled, phase in ("disconnected", "error"))
        controller.push(tool_calls=[{"name": "get_document_info", "status": "completed"}])
        self.assertTrue(self.named("ToolStatus").visible)
        self.assertIn("get_document_info", self.named("ToolStatus").text())
        self.assertTrue(self.qt.inside(self.named("ToolStatus"), self.named("Settings")))
        controller.push(tool_calls=[])
        self.assertFalse(self.named("ToolStatus").visible)

    def test_real_login_pending_ui_recovers_one_request_without_exposing_the_link(self):
        controller, transport = self.connected_real_chat(account=None)
        self.qt.gui.QDesktopServices.openUrl.side_effect = [RuntimeError("Neutral browser diagnostic"), True]
        self.named("Login").click()
        self.assertEqual(self.named("Status").text(), "正在获取登录链接…")
        for suffix in ("ReopenLogin", "CopyLoginLink"):
            self.assertTrue(self.named(suffix).visible)
            self.assertFalse(self.named(suffix).enabled)
        self.assertEqual(self.qt.gui.QDesktopServices.openUrl.call_count, 0)
        url = "https://auth.openai.com/authorize?fixture=neutral-view-login"
        transport.result("account/login/start", {"type": "chatgpt", "loginId": "neutral-login", "authUrl": url})
        self.assertEqual(controller.snapshot["error"]["code"], "CHAT_BROWSER_OPEN_FAILED")
        self.assertIn("登录页面尚未打开", self.named("Status").text())
        self.assertTrue(self.named("ConnectionActions").visible)
        for suffix in ("ReopenLogin", "CopyLoginLink"):
            self.assertTrue(self.named(suffix).enabled)
        self.named("CopyLoginLink").click()
        self.assertEqual(self.qt.clipboard, [url])
        self.assertEqual(self.named("Status").text(), "登录链接已复制，请在浏览器中打开。")
        self.named("ReopenLogin").click()
        self.assertEqual(self.named("Status").text(), "请在浏览器中完成登录…")
        self.assertEqual(self.qt.gui.QDesktopServices.openUrl.call_args_list, [unittest.mock.call(url)] * 2)
        self.assertEqual(sum(row.get("method") == "account/login/start" for row in transport.messages), 1)
        self.assertNotIn(url, json.dumps(controller.snapshot))
        self.assertNotIn(url, self.named("Status").text())
        transport.data({"method": "account/login/completed", "params": {"loginId": "neutral-login", "success": True}})
        self.assertEqual(controller.snapshot["phase"], "connecting")
        for suffix in ("ReopenLogin", "CopyLoginLink"):
            self.assertFalse(self.named(suffix).visible)
            self.assertFalse(self.named(suffix).enabled)
        self.named("CopyLoginLink").click()
        self.named("ReopenLogin").click()
        self.assertEqual(self.qt.clipboard, [url])
        self.assertEqual(self.qt.gui.QDesktopServices.openUrl.call_count, 2)
        self.assertEqual(transport.stops, 0)

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
        self.assertEqual(self.named("Input").toPlainText(), "Public fixture request")
        controller.push(transcript=[{'role': 'user', 'text': 'Public fixture request', 'status': 'complete'}])
        self.assertEqual(self.named("Input").toPlainText(), "")
        self.named("Stop").click(); controller.interrupt.assert_called_once()
        self.named("SavePreferences").click()
        self.settings.save_chat_preferences.assert_called_once_with(self.app, "", 0, retain_history=False)
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
