# SPDX-License-Identifier: Apache-2.0
"""Native Qt chat client; all account/network actions require a user click."""
import importlib.util
from pathlib import Path
import sys

from pft_workbench_i18n import tr
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
}


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
    from pft_chat_profile import detect_codex_executable, prepare_chat_profile, validate_executable, PftChatProfileError
    from pft_chat_settings import (load_chat_options, save_chat_options, build_chat_mcp_config, PftChatSettingsError,
                                   load_chat_preferences, save_chat_preferences, load_chat_desk_configuration)
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
    for button in (connect, login): connection_row.addWidget(button)
    layout.addWidget(connection_actions)
    status = label(tr("尚未连接")); status.setObjectName("PhoenixTools_ChatStatus")
    layout.addWidget(status)

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

    transcript = shrink_horizontal(QtWidgets.QPlainTextEdit()); transcript.setReadOnly(True)
    transcript.setObjectName("PhoenixTools_ChatTranscript")
    transcript.setPlaceholderText(tr("点击“连接 Codex”，登录后即可发送消息。"))
    layout.addWidget(transcript, 1)
    entry = shrink_horizontal(QtWidgets.QPlainTextEdit()); entry.setMaximumHeight(110)
    entry.setObjectName("PhoenixTools_ChatInput")
    entry.setPlaceholderText(tr("输入消息，点击发送。文档内容不会自动附加。"))
    layout.addWidget(entry)
    action_row = QtWidgets.QHBoxLayout()
    send = QtWidgets.QPushButton(tr("发送")); send.setObjectName("PhoenixTools_ChatSend")
    stop = QtWidgets.QPushButton(tr("停止回复")); stop.setObjectName("PhoenixTools_ChatStop")
    for button in (send, stop): action_row.addWidget(button)
    layout.addLayout(action_row)

    rendered_models = [None]

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

    def changed(snapshot):
        if dialog._closed:
            return
        phase = snapshot["phase"]
        render_models(snapshot)
        message = tr(_PHASES.get(phase, "聊天连接失败"))
        if snapshot.get("error"):
            message += "\n" + tr(snapshot["error"]["message"])
        if snapshot.get("mcp_enabled"):
            message += " · " + tr("FreeCAD 工具模式")
        if snapshot.get("desk_enabled"):
            message += " · " + tr("Desk 数据工具")
        status.setText(message)
        configurable = phase in ("disconnected", "error")
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
        connection_actions.setVisible(phase in ("disconnected", "error", "signed_out"))
        program.setEnabled(phase in ("disconnected", "error"))
        browse.setEnabled(program.isEnabled())
        login.setEnabled(phase == "signed_out")
        disconnect.setEnabled(phase not in ("disconnected",))
        send.setEnabled(bool(snapshot.get("can_send")))
        stop.setEnabled(phase == "sending")
        entry.setEnabled(phase in ("ready", "signed_out", "disconnected", "error"))
        text = "\n\n".join((tr("你") if item["role"] == "user" else "AI") + "\n" + item["text"]
                            for item in snapshot["transcript"])
        if text != transcript.toPlainText():
            bar = transcript.verticalScrollBar()
            follow = bar.value() >= bar.maximum() - 4
            old = bar.value()
            transcript.setPlainText(text)
            bar.setValue(bar.maximum() if follow else min(old, bar.maximum()))

    def open_url(url):
        return QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    process = PftChatProcess(QtCore, dialog)
    controller = PftChatController(process, changed, open_url)
    dialog._controller = controller
    dialog._process = process

    def connect_client():
        try:
            executable = validate_executable(program.text())
            selected = {"enabled": enable_tools.isChecked(), "desk_enabled": enable_desk.isChecked(),
                        "node_executable": node.text(), "cli_entry": cli.text()}
            desk = load_chat_desk_configuration(app) if selected["desk_enabled"] else None
            mcp = (build_chat_mcp_config(selected, get_status(), desk_configuration=desk)
                   if selected["enabled"] else None)
            profile = prepare_chat_profile(app.getUserAppDataDir())
            controller.connect(executable, profile["home"], profile["cwd"], mcp=mcp, model_id=model_choice.currentData() or None)
        except (PftChatProfileError, PftChatSettingsError) as error:
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
            save_chat_preferences(app, model_choice.currentData() or "", font_choice.currentData())
            apply_chat_font()
            status.setText(tr("聊天设置已保存；字号立即生效，模型在开始聊天前或新连接使用。"))
        except PftChatSettingsError as error:
            status.setText(tr(str(error)))

    def choose_model():
        if controller.snapshot.get("can_select_model"):
            controller.select_model(model_choice.currentData() or None)

    save_tools.clicked.connect(save_tool_settings)
    save_preferences.clicked.connect(save_chat_settings)
    model_choice.currentIndexChanged.connect(lambda index: choose_model())
    refresh_models.clicked.connect(controller.refresh_models)
    font_choice.currentIndexChanged.connect(lambda index: apply_chat_font())
    apply_chat_font()

    def choose_program():
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(dialog, tr("选择 Codex 执行文件"), program.text())
        if filename:
            program.setText(filename)

    def send_message():
        text = entry.toPlainText()
        try:
            controller.send(text)
            if controller.snapshot["phase"] in ("sending", "interrupting"):
                entry.clear()
        except Exception:
            status.setText(tr("消息未发送，请检查连接和输入。"))

    browse.clicked.connect(choose_program)
    connect.clicked.connect(connect_client)
    login.clicked.connect(controller.login)
    disconnect.clicked.connect(controller.disconnect)
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
