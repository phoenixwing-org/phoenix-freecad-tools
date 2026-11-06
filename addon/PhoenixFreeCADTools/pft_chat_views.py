# SPDX-License-Identifier: Apache-2.0
"""Native Qt chat client; all account/network actions require explicit user action."""
import importlib.util
from pathlib import Path
from datetime import datetime
import sys

from pft_workbench_i18n import tr
from pft_chat_transcript import render_chat_transcript
from pft_panel_host import mount_panel_dock, present_panel

PFT_CHAT_PROMPT = (
    "请通过 phoenix-freecad MCP 检查连接并列出已打开文档，读取目标文档和对象状态。"
    "先确认活动文档、Body、Tip、修改/编辑状态及读取来源，再进行设计。"
    "先用 inspect_design 读取缓存的约束、自由度和特征状态；不要自动求解或重算。"
    "改名或属性修改先展示完整预览，确认后操作；不要自动保存文件。"
)

_PHASES = {
    "disconnected": "尚未连接", "connecting": "正在连接 Codex…", "signed_out": "请使用 ChatGPT 登录",
    "ready": "可以聊天", "logging_in": "请在浏览器中完成登录…", "sending": "正在回复…",
    "interrupting": "正在停止回复…", "error": "聊天连接失败",
    "restoring": "正在恢复聊天…", "history_read_only": "保存的聊天为只读",
}


def _qt_enum(namespace, group, name):
    value = getattr(namespace, name, None)
    return value if value is not None else getattr(getattr(namespace, group), name)


def _shortcut_facade():
    """Load only this installation's fixed workbench facade, on a click."""
    name = "pft_workbench_tools"
    filename = Path(__file__).resolve().with_name(name + ".py")
    existing = sys.modules.get(name)
    if existing is not None:
        origin = getattr(existing, "__file__", None)
        if not isinstance(origin, str) or Path(origin).resolve() != filename:
            raise RuntimeError("Shortcut facade belongs to another installation")
        return existing
    spec = importlib.util.spec_from_file_location(name, filename)
    if spec is None or spec.loader is None:
        raise RuntimeError("Shortcut facade is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(name) is module:
            del sys.modules[name]
        raise
    return module


def show_chat_connection_dialog(app, gui, get_status):
    from PySide import QtCore, QtGui, QtWidgets
    from pft_chat_controller import PftChatController
    from pft_chat_qprocess import PftChatProcess
    from pft_chat_process_lease import (acquire as acquire_process_lease, inspect_user_registration,
                                        PftChatProcessLeaseError)
    from pft_chat_profile import detect_codex_executable, prepare_chat_profile, validate_executable, PftChatProfileError
    from pft_chat_session_store import PftChatSessionStore, PftChatSessionStoreError, list_user_sessions
    from pft_chat_settings import (load_chat_options, save_chat_options, build_chat_mcp_config, PftChatSettingsError,
                                   load_chat_preferences, load_chat_history_preference, save_chat_preferences, load_chat_desk_configuration)
    parent = gui.getMainWindow()
    existing = parent.findChild(QtWidgets.QDockWidget, "PhoenixTools_ChatConnectionDialog")
    if existing is not None:
        present_panel(existing)
        return existing

    class PftChatDock(QtWidgets.QDockWidget):
        def closeEvent(self, event):
            if self._controller.snapshot.get("is_busy"):
                answer = QtWidgets.QMessageBox.question(
                    self, tr("关闭聊天"), tr("关闭会停止本窗口的回复并断开聊天连接。是否关闭？"),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Cancel)
                if answer != QtWidgets.QMessageBox.Yes:
                    event.ignore()
                    return
            self._closed = True
            self._status_timer.stop()
            self._controller.disconnect()
            super().closeEvent(event)

        def reject(self):
            self.close()

    def shrink_horizontal(widget):
        widget.setMinimumWidth(0)
        policy = widget.sizePolicy()
        policy.setHorizontalPolicy(QtWidgets.QSizePolicy.Ignored)
        widget.setSizePolicy(policy)
        return widget

    def label(text=""):
        widget = shrink_horizontal(QtWidgets.QLabel(text))
        widget.setWordWrap(True)
        widget.setTextFormat(QtCore.Qt.PlainText)
        return widget

    def compact_button(title, object_name, icon):
        button = QtWidgets.QToolButton()
        button.setObjectName(object_name)
        button.setText(tr(title))
        button.setToolTip(tr(title))
        button.setAccessibleName(tr(title))
        button.setIcon(QtGui.QIcon(str(Path(__file__).resolve().parent / "Resources" / "icons" / icon)))
        button.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        button.setAutoRaise(True)
        return button

    dialog = PftChatDock(parent)
    dialog._closed = False
    dialog.setObjectName("PhoenixTools_ChatConnectionDialog")
    dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose)
    dialog.setWindowTitle(tr("Phoenix 聊天"))
    dialog.setToolTip(tr("Phoenix：AI 聊天"))
    dialog.resize(460, 650)
    dialog.setMinimumWidth(300)
    content = QtWidgets.QWidget(dialog)
    layout = QtWidgets.QVBoxLayout(content)
    def run_shortcut(action):
        try:
            facade = _shortcut_facade()
            callbacks = {"document": facade.show_document_tools_dialog,
                         "axonometric_fit": facade.run_axonometric_fit,
                         "appearance": facade.show_appearance_dialog}
            callbacks[action]()
        except Exception:
            message = tr("快捷操作失败；未自动保存 CAD 文档。")
            status.setText(message)
            printer = getattr(getattr(app, "Console", None), "PrintError", None)
            if callable(printer):
                try:
                    printer("[Phoenix Tools] " + message + "\n")
                except Exception:
                    pass  # The panel already displays the fixed failure.

    toolbar = QtWidgets.QHBoxLayout()
    for title, suffix, icon, action in (
        ("当前文档工具", "DocumentTools", "document-tools.svg", "document"),
        ("轴测并适合窗口", "AxonometricFit", "axonometric-fit.svg", "axonometric_fit"),
        ("对象外观", "Appearance", "appearance.svg", "appearance"),
    ):
        button = compact_button(title, "PhoenixTools_Chat" + suffix + "Action", icon)
        button.clicked.connect(lambda _checked=False, action=action: run_shortcut(action))
        toolbar.addWidget(button)
    toolbar.addStretch(1)
    settings_toggle = compact_button("聊天与工具设置", "PhoenixTools_ChatSettingsToggle", "gear.svg")
    settings_toggle.setCheckable(True)
    toolbar.addWidget(settings_toggle)
    layout.addLayout(toolbar)

    # Keep the first connection/sign-in step visible without exposing all of
    # the connection and tool configuration in the ordinary chat flow.
    connection_actions = QtWidgets.QWidget(content)
    connection_actions.setObjectName("PhoenixTools_ChatConnectionActions")
    connection_row = QtWidgets.QHBoxLayout(connection_actions)
    connection_row.setContentsMargins(0, 0, 0, 0)
    connect = QtWidgets.QPushButton(tr("连接 Codex")); connect.setObjectName("PhoenixTools_ChatConnect")
    login = QtWidgets.QPushButton(tr("使用 ChatGPT 登录")); login.setObjectName("PhoenixTools_ChatLogin")
    reopen_login = QtWidgets.QPushButton(tr("重新打开登录页面"))
    reopen_login.setObjectName("PhoenixTools_ChatReopenLogin")
    copy_login = QtWidgets.QPushButton(tr("复制登录链接"))
    copy_login.setObjectName("PhoenixTools_ChatCopyLoginLink")
    for button in (connect, login, reopen_login, copy_login): connection_row.addWidget(button)
    layout.addWidget(connection_actions)
    status = label(tr("尚未连接")); status.setObjectName("PhoenixTools_ChatStatus")
    layout.addWidget(status)
    history_status = label()
    history_status.setObjectName("PhoenixTools_ChatHistoryStatus")
    history_status.hide()
    layout.addWidget(history_status)

    options = load_chat_options(app)
    preferences = load_chat_preferences(app)
    settings_box = shrink_horizontal(QtWidgets.QWidget(content))
    settings_box.setObjectName("PhoenixTools_ChatSettings")
    settings_layout = QtWidgets.QVBoxLayout(settings_box)
    note = label(tr("在 FreeCAD 内使用 Codex 聊天，通过 ChatGPT 浏览器登录。使用本窗口的独立会话；可启用 FreeCAD 工具，改名和属性修改需在原生窗口确认。"))
    settings_layout.addWidget(note)
    settings_layout.addWidget(label(tr("Codex 执行文件")))
    path_row = QtWidgets.QHBoxLayout()
    program = shrink_horizontal(QtWidgets.QLineEdit(detect_codex_executable()))
    program.setObjectName("PhoenixTools_ChatExecutable")
    browse = QtWidgets.QPushButton(tr("浏览…"))
    browse.setObjectName("PhoenixTools_ChatBrowse")
    path_row.addWidget(program, 1); path_row.addWidget(browse)
    settings_layout.addLayout(path_row)
    disconnect = QtWidgets.QPushButton(tr("断开")); disconnect.setObjectName("PhoenixTools_ChatDisconnect")
    settings_layout.addWidget(disconnect)
    check_registration = QtWidgets.QPushButton(tr("检查聊天进程登记"))
    check_registration.setObjectName("PhoenixTools_ChatCheckProcessRegistration")
    settings_layout.addWidget(check_registration)
    registration_status = label(tr("尚未检查聊天进程登记。"))
    registration_status.setObjectName("PhoenixTools_ChatProcessRegistrationStatus")
    settings_layout.addWidget(registration_status)
    enable_tools = QtWidgets.QCheckBox(tr("启用 FreeCAD 工具"))
    enable_tools.setObjectName("PhoenixTools_ChatEnableTools")
    enable_tools.setChecked(options["enabled"])
    settings_layout.addWidget(enable_tools)
    enable_desk = QtWidgets.QCheckBox(tr("启用 Desk 数据工具"))
    enable_desk.setObjectName("PhoenixTools_ChatEnableDeskTools")
    enable_desk.setChecked(bool(options.get("desk_enabled", False) and options["enabled"]))
    settings_layout.addWidget(enable_desk)
    settings_layout.addWidget(label(tr("需要先启用 FreeCAD 工具并配置 Desk 连接。仅查询明确工作空间的已保存索引，不代表当前内存模型或物料数量；不启动 Desk、CAD 或生成任务。")))

    def update_desk_option():
        if not enable_tools.isChecked():
            enable_desk.setChecked(False)
        enable_desk.setEnabled(enable_tools.isEnabled() and enable_tools.isChecked())
    enable_tools.toggled.connect(lambda _checked: update_desk_option())
    update_desk_option()
    settings_layout.addWidget(label(tr("聊天模型")))
    model_row = QtWidgets.QHBoxLayout()
    model_choice = shrink_horizontal(QtWidgets.QComboBox())
    model_choice.setObjectName("PhoenixTools_ChatModel")
    model_choice.setMinimumContentsLength(1)
    model_choice.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
    model_choice.addItem(tr("服务默认"), "")
    if preferences["model_id"]:
        model_choice.addItem(tr("%s（待验证）") % preferences["model_id"], preferences["model_id"])
        model_choice.setCurrentIndex(1)
    refresh_models = QtWidgets.QPushButton(tr("刷新模型"))
    refresh_models.setObjectName("PhoenixTools_ChatRefreshModels")
    model_row.addWidget(model_choice, 1); model_row.addWidget(refresh_models)
    settings_layout.addLayout(model_row)
    model_status = label(tr("连接后从官方服务读取可用模型。"))
    model_status.setObjectName("PhoenixTools_ChatModelsStatus")
    settings_layout.addWidget(model_status)
    settings_layout.addWidget(label(tr("切换模型需要在开始聊天前选择或建立新连接；当前会话不会被自动重置。")))
    font_row = QtWidgets.QHBoxLayout()
    font_row.addWidget(label(tr("聊天字号")))
    font_choice = shrink_horizontal(QtWidgets.QComboBox())
    font_choice.setObjectName("PhoenixTools_ChatFontSize")
    font_choice.addItem(tr("跟随 FreeCAD"), 0)
    for point_size in range(8, 33):
        font_choice.addItem(tr("%s 磅") % point_size, point_size)
    font_choice.setCurrentIndex(font_choice.findData(preferences["font_point_size"]))
    font_row.addWidget(font_choice, 1)
    settings_layout.addLayout(font_row)
    save_preferences = QtWidgets.QPushButton(tr("保存聊天设置"))
    save_preferences.setObjectName("PhoenixTools_ChatSavePreferences")
    settings_layout.addWidget(save_preferences)
    retain_history = shrink_horizontal(QtWidgets.QCheckBox(tr("保留新聊天")))
    retain_history.setObjectName("PhoenixTools_ChatRetainHistory")
    retain_history.setChecked(load_chat_history_preference(app))
    settings_layout.addWidget(retain_history)
    settings_layout.addWidget(label(tr("默认新建临时聊天。开启保留后，官方客户端可能保存消息及 CAD 工具返回的数据；关闭选项不会删除已有会话。")))
    saved_sessions = shrink_horizontal(QtWidgets.QComboBox())
    saved_sessions.setObjectName("PhoenixTools_ChatSavedSessions")
    saved_sessions.setMinimumContentsLength(1)
    saved_sessions.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
    saved_sessions.addItem(tr("新聊天"), None)
    settings_layout.addWidget(saved_sessions)
    list_sessions = QtWidgets.QPushButton(tr("读取本插件保存的聊天"))
    list_sessions.setObjectName("PhoenixTools_ChatListSessions")
    settings_layout.addWidget(list_sessions)
    session_status = label(tr("尚未读取保存的会话。仅列出本插件登记的会话，选择后点击连接。"))
    session_status.setObjectName("PhoenixTools_ChatSavedSessionsStatus")
    settings_layout.addWidget(session_status)
    path_fields = []
    for title, key, object_name in (("Node 执行文件", "node_executable", "PhoenixTools_ChatNode"),
                                    ("MCP CLI 文件", "cli_entry", "PhoenixTools_ChatMCPCLI")):
        settings_layout.addWidget(label(tr(title)))
        row = QtWidgets.QHBoxLayout()
        field = shrink_horizontal(QtWidgets.QLineEdit(options[key])); field.setObjectName(object_name)
        chooser = QtWidgets.QPushButton(tr("浏览…")); chooser.setObjectName(object_name + "Browse")
        def choose_path(checked=False, target=field, title=title):
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(dialog, tr(title), target.text())
            if filename: target.setText(filename)
        chooser.clicked.connect(choose_path)
        row.addWidget(field, 1); row.addWidget(chooser); settings_layout.addLayout(row)
        path_fields.append((field, chooser))
    node, cli = [pair[0] for pair in path_fields]
    save_tools = QtWidgets.QPushButton(tr("保存工具设置"))
    save_tools.setObjectName("PhoenixTools_ChatSaveTools")
    settings_layout.addWidget(save_tools)
    settings_layout.addWidget(label(tr("需要 Node 22 或更新版本及已构建的 Phoenix MCP。设置在下次连接生效；不会下载、编译或启动桥接。")))
    copy = QtWidgets.QPushButton(tr("复制给现有聊天")); copy.setObjectName("PftChatCopyPrompt")
    official = QtWidgets.QPushButton(tr("官方接入说明")); official.setObjectName("PftChatOfficialDocs")
    close = QtWidgets.QPushButton(tr("关闭")); close.setObjectName("PftChatClose")
    for button in (copy, official, close): settings_layout.addWidget(button)
    bridge = label(); bridge.setObjectName("PftChatBridgeStatus")
    try:
        snapshot = get_status()
        running = tr("运行中") if snapshot.get("running") and not snapshot.get("stopping") else tr("已停止")
        bridge.setText(tr("FreeCAD 桥接：%s") % running + " · " + tr("文档读取、视图和外观工具已开放；改名和属性先预览，再在 FreeCAD 窗口确认。文档仅在请求工具时读取。"))
    except Exception:
        bridge.setText(tr("桥接状态读取失败，请查看工作台状态。"))
    settings_layout.addWidget(bridge)
    tool_status = label()
    tool_status.setObjectName("PhoenixTools_ChatToolStatus")
    tool_status.hide()
    settings_layout.addWidget(tool_status)
    settings_box.hide(); settings_toggle.toggled.connect(settings_box.setVisible)
    layout.addWidget(settings_box)

    class PftChatTranscript(QtWidgets.QTextBrowser):
        def loadResource(self, _kind, _url):
            return None

    transcript = shrink_horizontal(PftChatTranscript()); transcript.setReadOnly(True)
    transcript.setOpenExternalLinks(False)
    transcript.setOpenLinks(False)
    transcript.setTextInteractionFlags(
        _qt_enum(QtCore.Qt, "TextInteractionFlag", "TextSelectableByMouse") |
        _qt_enum(QtCore.Qt, "TextInteractionFlag", "TextSelectableByKeyboard"))
    transcript.setWordWrapMode(_qt_enum(QtGui.QTextOption, "WrapMode", "WrapAtWordBoundaryOrAnywhere"))
    transcript.setObjectName("PhoenixTools_ChatTranscript")
    transcript.setPlaceholderText(tr("点击“连接 Codex”，登录后即可发送消息。"))
    layout.addWidget(transcript, 1)
    class PftChatInput(QtWidgets.QPlainTextEdit):
        def __init__(self):
            super().__init__()
            self._ime_composing = False

        def inputMethodEvent(self, event):
            self._ime_composing = bool(event.preeditString())
            super().inputMethodEvent(event)

        def keyPressEvent(self, event):
            keys = tuple(_qt_enum(QtCore.Qt, "Key", name) for name in ("Key_Return", "Key_Enter"))
            modifiers = tuple(_qt_enum(QtCore.Qt, "KeyboardModifier", name)
                              for name in ("NoModifier", "KeypadModifier"))
            if event.key() in keys and event.modifiers() in modifiers:
                if self._ime_composing:
                    super().keyPressEvent(event)
                else:
                    if not event.isAutoRepeat():
                        send_message()
                    event.accept()
                return
            super().keyPressEvent(event)

    entry = shrink_horizontal(PftChatInput()); entry.setMaximumHeight(110)
    entry.setObjectName("PhoenixTools_ChatInput")
    entry.setPlaceholderText(tr("Enter 发送 · Shift+Enter 换行"))
    entry.setToolTip(tr("输入消息，点击发送。文档内容不会自动附加。"))
    layout.addWidget(entry)
    action_row = QtWidgets.QHBoxLayout()
    send = QtWidgets.QPushButton(tr("发送")); send.setObjectName("PhoenixTools_ChatSend")
    stop = QtWidgets.QPushButton(tr("停止回复")); stop.setObjectName("PhoenixTools_ChatStop")
    for button in (send, stop): action_row.addWidget(button)
    layout.addLayout(action_row)

    rendered_models, rendered_html = [None], [None]
    pending_submission = [None]
    registration_pending, registration_activity = [False], [0]

    def registration_context():
        """Memory-only gate; opening settings never inspects the profile."""
        if dialog._closed:
            return None
        try:
            snapshot = controller.snapshot
            generation = snapshot.get("connection_generation")
            if (snapshot.get("phase") not in ("disconnected", "error")
                    or snapshot.get("is_busy") is not False
                    or type(generation) is not int or generation < 0
                    or process.has_active_session is not False):
                return None
            return (generation, snapshot["phase"], registration_activity[0])
        except (AttributeError, RuntimeError, TypeError):
            return None

    def update_registration_availability():
        if not dialog._closed:
            available = not registration_pending[0] and registration_context() is not None
            check_registration.setEnabled(available)
            list_sessions.setEnabled(available)

    def set_registration_status(message):
        if not dialog._closed:
            registration_status.setText(tr(message))

    def check_process_registration():
        context = registration_context()
        if context is None or registration_pending[0]:
            set_registration_status("请先断开聊天，并等待本窗口的进程退出后再检查登记。")
            return
        registration_pending[0] = True
        update_registration_availability()
        try:
            inspection = inspect_user_registration(app.getUserAppDataDir())
            if registration_context() != context:
                set_registration_status("聊天连接状态已变化，未清理登记；请重新检查。")
                return
            state, pid, can_clear = inspection.status, inspection.child_pid, inspection.can_clear
            if (state not in ("absent", "present", "unknown", "exited")
                    or type(can_clear) is not bool
                    or pid is not None and (type(pid) is not int or not 1 <= pid <= 2 ** 31 - 1)
                    or can_clear is not (state == "exited")
                    or state == "exited" and pid is None
                    or state == "absent" and pid is not None):
                raise ValueError("incompatible registration inspection")
            messages = {"absent": "没有聊天进程登记；未启动连接。",
                        "present": "登记中的 PID 仍存在；未清理，不代表它仍属于原聊天进程。",
                        "unknown": "聊天进程状态无法确认；保留登记。"}
            if state != "exited":
                set_registration_status(messages[state])
                return
            answer = QtWidgets.QMessageBox.question(dialog, tr("清除已退出的聊天进程登记"),
                tr("已确认登记中的 PID %s 当前不存在。只清除这一笔进程登记，不删除登录或聊天数据，也不启动服务。是否继续？") % pid,
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Cancel)
            if registration_context() != context:
                set_registration_status("聊天连接状态已变化，未清理登记；请重新检查。")
                return
            if answer != QtWidgets.QMessageBox.Yes:
                set_registration_status("已取消，未清理登记。")
                return
            if inspection.clear_exited() is not True:
                raise ValueError("registration cleanup was not confirmed")
            set_registration_status("已清除这一笔进程登记；可手动连接。未启动服务。")
        except PftChatProcessLeaseError as error:
            set_registration_status(error.message)
        except Exception:
            set_registration_status("聊天进程登记操作未确认；请重新检查，不要重复操作。")
        finally:
            registration_pending[0] = False
            update_registration_availability()

    def list_saved_sessions():
        context = registration_context()
        if context is None:
            session_status.setText(tr("请先断开当前聊天，再选择保存的会话。"))
            return
        try:
            rows = list_user_sessions(app.getUserAppDataDir())
            if registration_context() != context:
                session_status.setText(tr("聊天连接已变化；未切换会话。"))
                return
            if type(rows) is not list or len(rows) > 100:
                raise ValueError("invalid session registry projection")
            labels = []
            for row in rows:
                if (type(row) is not dict or type(row.get("model")) is not str
                        or type(row.get("created_at")) is not int):
                    raise ValueError("invalid session registry metadata")
                stamp = datetime.fromtimestamp(row["created_at"]).strftime("%Y-%m-%d %H:%M")
                labels.append((stamp + " · " + row["model"], dict(row)))
            saved_sessions.clear()
            saved_sessions.addItem(tr("新聊天"), None)
            for text, record in labels:
                saved_sessions.addItem(text, record)
            saved_sessions.setCurrentIndex(0)
            session_status.setText(tr("已读取 %s 个登记会话；选择后点击连接，不会自动发送消息。") % len(rows))
        except PftChatSessionStoreError as error:
            session_status.setText(tr(error.message))
        except Exception:
            session_status.setText(tr("无法读取本插件会话登记；未连接或切换会话。"))

    def render_models(snapshot):
        phase = snapshot["phase"]
        desired = ((model_choice.currentData() or "") if phase in ("disconnected", "error")
                   else (snapshot.get("selected_model") or ""))
        available = snapshot.get("models", [])
        model_state = snapshot.get("models_status", "idle")
        signature = (tuple((row["model"], row["display_name"], row["is_default"]) for row in available),
                     model_state, desired)
        if signature != rendered_models[0]:
            model_choice.blockSignals(True)
            try:
                model_choice.clear(); model_choice.addItem(tr("服务默认"), "")
                for row in available:
                    model_choice.addItem("%s · %s" % (row["display_name"], row["model"]), row["model"])
                if desired and model_choice.findData(desired) < 0:
                    label = "%s（不可用，请重新选择）" if model_state == "ready" else "%s（待验证）"
                    model_choice.addItem(tr(label) % desired, desired)
                model_choice.setCurrentIndex(max(0, model_choice.findData(desired)))
            finally:
                model_choice.blockSignals(False)
            rendered_models[0] = signature
        text = tr({"idle": "连接后从官方服务读取可用模型。", "loading": "正在读取可用模型…",
                   "ready": "模型清单已读取。", "error": "模型清单读取失败；服务默认仍可使用。"}.get(model_state, "连接后从官方服务读取可用模型。"))
        if snapshot.get("models_error"):
            if snapshot["models_error"].get("code") == "CHAT_MODELS_CANCELLED":
                text = tr("模型清单读取已停止。")
            text += "\n" + tr(snapshot["models_error"]["message"])
        if snapshot.get("model"):
            text += "\n" + tr("当前模型：%s") % snapshot["model"]
        model_status.setText(text)
        model_choice.setEnabled(phase in ("disconnected", "error") or bool(snapshot.get("can_select_model")))
        refresh_models.setEnabled(bool(snapshot.get("can_refresh_models")))

    def apply_chat_font():
        point_size = font_choice.currentData()
        for widget in (transcript, entry):
            font = QtGui.QFont(widget.font()) if point_size else QtGui.QFont()
            if point_size:
                font.setPointSize(point_size)
            widget.setFont(font)

    def refresh_status(snapshot):
        if dialog._closed:
            return
        phase = snapshot["phase"]
        history = snapshot.get("history_status", "inactive")
        notice = snapshot.get("history_notice")
        history_status.setText(tr(notice) if isinstance(notice, str) and notice else
                               tr("正在读取保存的聊天…") if history == "loading" else "")
        history_status.setVisible(bool(history_status.text()))
        error = snapshot.get("error")
        recovering = bool(error and error.get("code") == "CHAT_SERVICE_RECONNECTING")
        message = tr(_PHASES.get(phase, "聊天连接失败"))
        if phase == "logging_in":
            message = tr({"requesting_link": "正在获取登录链接…",
                          "browser_open_failed": "登录页面尚未打开"}.get(
                              snapshot.get("login_stage"), "请在浏览器中完成登录…"))
        if (phase == "sending" and not recovering
                and any(call["status"] == "in_progress" for call in snapshot.get("tool_calls", []))):
            message = tr("正在执行工具…")
        if error:
            message += "\n" + tr(error["message"])
        timing = snapshot.get("turn_timing")
        tooltip = ""
        if isinstance(timing, dict):
            elapsed = "%g" % round(timing["elapsed_seconds"], 1)
            first = timing.get("first_response_seconds")
            pattern = ("耗时 %s 秒" if timing.get("finished") else
                       "已等待 %s 秒" if first is None else "已用 %s 秒")
            message += " · " + tr(pattern) % elapsed
            if first is not None:
                tooltip = tr("首条回复耗时：%s 秒") % ("%g" % round(first, 1))
        if snapshot.get("mcp_enabled"):
            message += " · " + tr("FreeCAD 工具模式")
        if snapshot.get("desk_enabled"):
            message += " · " + tr("Desk 数据工具")
        status.setText(message)
        status.setToolTip(tooltip)

    def changed(snapshot):
        if dialog._closed:
            return
        phase = snapshot["phase"]
        submission = pending_submission[0]
        if submission is not None:
            submitted_text, previous_count = submission
            if any(row["role"] == "user" and row["text"] == submitted_text
                   for row in snapshot["transcript"][previous_count:]):
                if entry.toPlainText() == submitted_text:
                    entry.clear()
                pending_submission[0] = None
            elif phase in ("error", "disconnected", "signed_out"):
                pending_submission[0] = None
        render_models(snapshot)
        refresh_status(snapshot)
        if snapshot.get("is_busy") or getattr(process, "has_active_session", False) is True:
            if not status_timer.isActive():
                status_timer.start()
        else:
            status_timer.stop()
        update_registration_availability()
        configurable = phase in ("disconnected", "error")
        retain_history.setEnabled(configurable)
        saved_sessions.setEnabled(configurable)
        list_sessions.setEnabled(registration_context() is not None)
        enable_tools.setEnabled(configurable)
        update_desk_option()
        save_tools.setEnabled(configurable)
        for field, chooser in path_fields:
            field.setEnabled(configurable); chooser.setEnabled(configurable)
        labels = {"in_progress": "执行中", "completed": "已返回", "failed": "失败"}
        calls = snapshot.get("tool_calls", [])[-5:]
        tool_status.setText("\n".join("%s · %s" % (call["name"], tr(labels.get(call["status"], "失败"))) for call in calls))
        tool_status.setVisible(bool(calls))
        connect.setEnabled(phase in ("disconnected", "error"))
        connect.setVisible(phase in ("disconnected", "error"))
        login.setVisible(phase == "signed_out")
        for button in (reopen_login, copy_login):
            button.setVisible(phase == "logging_in")
            button.setEnabled(phase == "logging_in" and bool(snapshot.get("can_use_login_link")))
        connection_actions.setVisible(phase in ("disconnected", "error", "signed_out", "logging_in"))
        program.setEnabled(phase in ("disconnected", "error"))
        browse.setEnabled(program.isEnabled())
        login.setEnabled(phase == "signed_out")
        disconnect.setEnabled(phase not in ("disconnected",))
        send.setEnabled(bool(snapshot.get("can_send")))
        stop.setEnabled(phase == "sending")
        entry.setEnabled(phase in ("ready", "signed_out", "disconnected", "error"))
        try:
            base = transcript.palette().color(_qt_enum(QtGui.QPalette, "ColorRole", "Base"))
            dark = base.lightness() < 128
        except (AttributeError, RuntimeError, TypeError):
            dark = False
        html = render_chat_transcript(snapshot["transcript"], tr("你"),
                                      snapshot.get("message_times", ()), dark=dark,
                                      interrupted_label=tr("已停止"), error_label=tr("未完成"))
        if html != rendered_html[0]:
            bar = transcript.verticalScrollBar()
            follow = bar.value() >= bar.maximum() - 4
            old = bar.value()
            transcript.setHtml(html)
            rendered_html[0] = html
            bar.setValue(bar.maximum() if follow else min(old, bar.maximum()))

    def open_url(url):
        return QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    process = PftChatProcess(QtCore, dialog, lease_factory=acquire_process_lease)
    controller = PftChatController(process, changed, open_url)
    dialog._controller = controller
    dialog._process = process
    status_timer = QtCore.QTimer(dialog)
    status_timer.setInterval(1000)
    def refresh_memory_status():
        if dialog._closed:
            return
        refresh_status(controller.snapshot)
        update_registration_availability()
        if not controller.snapshot.get("is_busy") and getattr(process, "has_active_session", False) is not True:
            status_timer.stop()
    status_timer.timeout.connect(refresh_memory_status)
    dialog._status_timer = status_timer

    def connect_client():
        registration_activity[0] += 1
        try:
            executable = validate_executable(program.text())
            selected = {"enabled": enable_tools.isChecked(), "desk_enabled": enable_desk.isChecked(),
                        "node_executable": node.text(), "cli_entry": cli.text()}
            desk = load_chat_desk_configuration(app) if selected["desk_enabled"] else None
            mcp = (build_chat_mcp_config(selected, get_status(), desk_configuration=desk)
                   if selected["enabled"] else None)
            profile = prepare_chat_profile(app.getUserAppDataDir())
            history_options = {}
            selected_session = saved_sessions.currentData()
            if selected_session is not None or retain_history.isChecked():
                if selected_session is not None and type(selected_session) is not dict:
                    raise ValueError("invalid selected registered session")
                store = PftChatSessionStore(profile["home"], profile["cwd"])
                history_options = {"session_store": store, "retain_history": True,
                                   "resume_session": selected_session}
            controller.connect(executable, profile["home"], profile["cwd"], mcp=mcp,
                               model_id=model_choice.currentData() or None, **history_options)
        except (PftChatProfileError, PftChatSettingsError, PftChatSessionStoreError) as error:
            status.setText(tr(str(error)))
        except Exception:
            status.setText(tr("无法连接聊天，请核对 Codex 与专属目录。"))

    def save_tool_settings():
        try:
            if enable_desk.isChecked():
                load_chat_desk_configuration(app)
            saved = save_chat_options(app, enable_tools.isChecked(), node.text(), cli.text(),
                                      desk_enabled=enable_desk.isChecked())
            node.setText(saved["node_executable"]); cli.setText(saved["cli_entry"])
            status.setText(tr("工具设置已保存，下次连接生效。"))
        except PftChatSettingsError as error:
            status.setText(tr(str(error)))

    def save_chat_settings():
        try:
            save_chat_preferences(app, model_choice.currentData() or "", font_choice.currentData(),
                                  retain_history=retain_history.isChecked())
            apply_chat_font()
            status.setText(tr("聊天设置已保存；字号立即生效，模型在开始聊天前或新连接使用。"))
        except PftChatSettingsError as error:
            status.setText(tr(str(error)))

    def choose_model():
        if controller.snapshot.get("can_select_model"):
            controller.select_model(model_choice.currentData() or None)

    save_tools.clicked.connect(save_tool_settings)
    save_preferences.clicked.connect(save_chat_settings)
    list_sessions.clicked.connect(list_saved_sessions)
    model_choice.currentIndexChanged.connect(lambda index: choose_model())
    refresh_models.clicked.connect(controller.refresh_models)
    font_choice.currentIndexChanged.connect(lambda index: apply_chat_font())
    apply_chat_font()

    def choose_program():
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(dialog, tr("选择 Codex 执行文件"), program.text())
        if filename:
            program.setText(filename)

    def send_message():
        snapshot = controller.snapshot
        if (not entry.isEnabled() or not send.isEnabled() or snapshot["phase"] != "ready"
                or not snapshot.get("can_send")):
            return
        text = entry.toPlainText()
        pending_submission[0] = (text, len(controller.snapshot["transcript"]))
        try:
            controller.send(text)
            if controller.snapshot["phase"] not in ("sending", "interrupting"):
                pending_submission[0] = None
        except Exception:
            pending_submission[0] = None
            status.setText(tr("消息未发送，请检查连接和输入。"))

    def copy_pending_login_link():
        if controller.copy_login_link(lambda url: QtWidgets.QApplication.clipboard().setText(url)):
            status.setText(tr("登录链接已复制，请在浏览器中打开。"))

    browse.clicked.connect(choose_program)
    connect.clicked.connect(connect_client)
    login.clicked.connect(controller.login)
    reopen_login.clicked.connect(controller.reopen_login)
    copy_login.clicked.connect(copy_pending_login_link)
    disconnect.clicked.connect(controller.disconnect)
    check_registration.clicked.connect(check_process_registration)
    settings_toggle.toggled.connect(lambda _checked: update_registration_availability())
    send.clicked.connect(send_message)
    stop.clicked.connect(controller.interrupt)
    copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(tr(PFT_CHAT_PROMPT)))
    official.clicked.connect(lambda: open_url("https://learn.chatgpt.com/docs/app-server"))
    close.clicked.connect(dialog.close)
    changed(controller.snapshot)
    scroll = shrink_horizontal(QtWidgets.QScrollArea(dialog))
    scroll.setObjectName("PhoenixTools_ChatScrollArea")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    mount_panel_dock(dialog, parent, scroll)
    present_panel(dialog)
    return dialog
