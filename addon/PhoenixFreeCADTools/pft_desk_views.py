# SPDX-License-Identifier: Apache-2.0
"""View: a non-modal Desk connection panel without CAD or service ownership."""

from pft_workbench_i18n import tr

_pft_dialog = None


def _qt():
    from PySide import QtCore
    try:
        from PySide import QtWidgets
    except ImportError:
        from PySide import QtGui as QtWidgets
    return QtCore, QtWidgets


def _enum(core, group, name):
    value = getattr(core.Qt, name, None)
    return value if value is not None else getattr(getattr(core.Qt, group, None), name, None)


def _shrink_horizontal(widget):
    widget.setMinimumWidth(0)
    policy = widget.sizePolicy()
    ignored = getattr(type(policy), "Ignored", None)
    if ignored is None:
        ignored = type(policy).Policy.Ignored
    policy.setHorizontalPolicy(ignored)
    widget.setSizePolicy(policy)
    return widget


def _open_url(core, url):
    from PySide import QtGui
    return bool(QtGui.QDesktopServices.openUrl(core.QUrl(url)))


def _existing_dialog(window, widgets):
    candidates = []
    try:
        candidates.append(window.findChild(widgets.QDialog, "PhoenixTools_DeskDialog"))
    except (AttributeError, RuntimeError, ReferenceError):
        pass
    candidates.append(_pft_dialog)
    for dialog in candidates:
        if dialog is None:
            continue
        try:
            if dialog.objectName() == "PhoenixTools_DeskDialog" and dialog.parent() is window:
                return dialog
        except (RuntimeError, ReferenceError):
            pass
    return None


def show_desk_dialog(app, gui):
    """Show one connection panel; opening does not save, probe or start Desk."""
    global _pft_dialog
    core, widgets = _qt()
    window = gui.getMainWindow()
    existing = _existing_dialog(window, widgets)
    if existing is not None:
        existing.show()
        existing.raise_()
        existing.activateWindow()
        return existing

    import pft_desk_controller as desk
    from pft_desk_qprocess import PftDeskProcess

    state = {"controller": None, "request_config": None, "ready_config": None}

    class PftDeskDialog(widgets.QDialog):
        def refuse_busy_close(self):
            controller = state["controller"]
            if controller is not None and controller.busy:
                output.setPlainText(tr("后台操作尚未结束，请等待结果后关闭。关闭面板不会停止 Desk 后台。"))
                return True
            return False

        def closeEvent(self, event):
            if self.refuse_busy_close():
                event.ignore()
                return
            super().closeEvent(event)

        def reject(self):
            if not self.refuse_busy_close():
                super().reject()

    dialog = PftDeskDialog(window)
    dialog.setObjectName("PhoenixTools_DeskDialog")
    dialog.setWindowTitle(tr("Phoenix：Desk 连接"))
    dialog.resize(680, 480)
    delete_on_close = _enum(core, "WidgetAttribute", "WA_DeleteOnClose")
    if delete_on_close is not None:
        dialog.setAttribute(delete_on_close, True)
    layout = widgets.QVBoxLayout(dialog)
    layout.setSpacing(4)

    def explanation(text):
        label = _shrink_horizontal(widgets.QLabel(tr(text)))
        plain = _enum(core, "TextFormat", "PlainText")
        if plain is not None:
            label.setTextFormat(plain)
        label.setWordWrap(True)
        layout.addWidget(label)

    explanation("连接本地已构建的 Desk 源码候选，需要已有 Node 和构建资源。这里不会下载、编译或启动 Tauri。")
    fields, browse_buttons = {}, []
    definitions = (
        ("node_executable", "Node 执行文件", "PhoenixTools_DeskNodeExecutable"),
        ("cli_entry", "Desk CLI 文件", "PhoenixTools_DeskCliEntry"),
        ("state_dir", "私有状态目录", "PhoenixTools_DeskStateDirectory"),
    )
    form = widgets.QFormLayout()
    form.setRowWrapPolicy(widgets.QFormLayout.WrapLongRows)
    for key, title, object_name in definitions:
        row = widgets.QHBoxLayout()
        title_label = widgets.QLabel(tr(title))
        title_label.setWordWrap(True)
        field = _shrink_horizontal(widgets.QLineEdit())
        field.setObjectName(object_name)
        fields[key] = field
        row.addWidget(field, 1)
        browse = widgets.QPushButton(tr("浏览…"))
        browse.setObjectName(object_name + "Browse")
        row.addWidget(browse)
        browse_buttons.append(browse)
        form.addRow(title_label, row)
    layout.addLayout(form)

    explanation("状态目录应为当前用户私有的持久目录，可输入尚未存在的子目录。不要选择工程、源码或插件目录，也不要用新目录绕过未知服务。")
    actions = widgets.QGridLayout()
    save = widgets.QPushButton(tr("保存配置"))
    status = widgets.QPushButton(tr("检查状态"))
    ensure = widgets.QPushButton(tr("启动 / 复用后台"))
    open_web = widgets.QPushButton(tr("打开 Desk Web"))
    copy_prompt = widgets.QPushButton(tr("复制配置提示词"))
    for button, name in ((save, "SaveConfiguration"), (status, "Status"), (ensure, "Ensure"), (open_web, "OpenWeb"), (copy_prompt, "CopySetupPrompt")):
        button.setObjectName("PhoenixTools_Desk" + name)
    for button, row, column, span in ((save, 0, 0, 1), (status, 0, 1, 1), (ensure, 0, 2, 1),
                                      (open_web, 1, 0, 1), (copy_prompt, 1, 1, 2)):
        actions.addWidget(button, row, column, 1, span)
    open_web.setEnabled(False)
    layout.addLayout(actions)
    output = _shrink_horizontal(widgets.QPlainTextEdit())
    output.setObjectName("PhoenixTools_DeskStatusText")
    output.setReadOnly(True)
    output.setMinimumHeight(64)
    output.setPlainText(tr("尚未检查。打开面板不会启动 Desk。"))
    layout.addWidget(output, 1)
    explanation("保存配置不会启动服务。浏览器打开前会重新检查；关闭面板或浏览器不会停止后台。")
    close = widgets.QPushButton(tr("关闭"))
    close.setObjectName("PhoenixTools_DeskClose")
    layout.addWidget(close)

    def configuration():
        return {key: field.text() for key, field in fields.items()}

    def changed(event):
        busy = bool(event.get("busy"))
        snapshot = event.get("snapshot")
        error = event.get("error")
        for widget in list(fields.values()) + browse_buttons + [save, status, ensure, copy_prompt]:
            widget.setEnabled(not busy)
        ready = isinstance(snapshot, dict) and snapshot.get("state") == "ready" and not busy
        if ready and state["request_config"] == configuration():
            state["ready_config"] = dict(state["request_config"])
        else:
            state["ready_config"] = None
        open_web.setEnabled(ready and state["ready_config"] == configuration())
        if error:
            output.setPlainText(tr("Desk 操作失败：%s") % tr(str(error.get("message", ""))))
        elif busy:
            output.setPlainText(tr("正在检查或启动 Desk，请等待…"))
        elif isinstance(snapshot, dict):
            labels = {"ready": tr("已就绪"), "absent": tr("未运行"), "unavailable": tr("暂不可用"), "incompatible": tr("不兼容")}
            lines = [tr("Desk 状态：%s") % labels.get(snapshot.get("state"), tr("未知")), str(snapshot.get("reason", ""))]
            if snapshot.get("state") == "ready":
                lines.append(tr("Web 地址：%s") % str(snapshot.get("web_url", "")))
                if snapshot.get("reused"):
                    lines.append(tr("已复用现有后台。"))
                elif snapshot.get("created"):
                    lines.append(tr("本次已启动后台。"))
            lines.append(tr("这是本地源码候选，不是正式 Release 安装包。"))
            output.setPlainText("\n".join(lines))
        else:
            output.setPlainText(tr("配置已变化，请重新检查状态。"))

    runner = PftDeskProcess(core, dialog)
    state["controller"] = desk.PftDeskController(runner, changed, lambda url: _open_url(core, url))

    def invalidate(*_args):
        state["request_config"] = state["ready_config"] = None
        open_web.setEnabled(False)
        if not state["controller"].busy:
            state["controller"].invalidate()

    def run(action):
        if state["controller"].busy:
            return
        config = configuration()
        state["request_config"] = dict(config)
        state["ready_config"] = None
        open_web.setEnabled(False)
        state["controller"].run(config, action)

    def save_configuration():
        if state["controller"].busy:
            return
        try:
            canonical = desk.save_desk_configuration(app, configuration())
            for key, field in fields.items():
                field.setText(canonical[key])
            invalidate()
            output.setPlainText(tr("Desk 配置已保存；尚未启动或检查服务。"))
        except desk.PftDeskError as error:
            output.setPlainText(tr("Desk 配置未保存：%s") % tr(error.message))

    def copy_setup_prompt():
        if state["controller"].busy:
            return
        try:
            from pft_desk_prompt import PftDeskPromptError, build_setup_prompt
        except ImportError:
            output.setPlainText(tr("无法复制配置提示词，请检查 FreeCAD 用户目录与剪贴板。"))
            return
        try:
            prompt = build_setup_prompt(configuration(), app.getUserAppDataDir())
            clipboard = widgets.QApplication.clipboard()
            if clipboard is None:
                raise RuntimeError("clipboard unavailable")
            clipboard.setText(prompt)
            output.setPlainText(tr("配置提示词已复制，可粘贴给 AI；复制不会保存配置或启停服务。"))
        except PftDeskPromptError as error:
            output.setPlainText(tr("配置提示词未复制：%s") % tr(str(error)))
        except Exception:
            output.setPlainText(tr("无法复制配置提示词，请检查 FreeCAD 用户目录与剪贴板。"))

    def browse(key):
        if state["controller"].busy:
            return
        if key == "state_dir":
            selected = widgets.QFileDialog.getExistingDirectory(dialog, tr("选择私有状态目录"), fields[key].text())
        else:
            title = tr("选择 Node 执行文件") if key == "node_executable" else tr("选择 Desk CLI 文件")
            selected = widgets.QFileDialog.getOpenFileName(dialog, title, fields[key].text())
            if isinstance(selected, (tuple, list)):
                selected = selected[0]
        if selected:
            fields[key].setText(str(selected))

    try:
        config = desk.load_desk_configuration(app)
        for key, field in fields.items():
            field.setText(config.get(key, ""))
    except desk.PftDeskError as error:
        output.setPlainText(tr("无法读取 Desk 配置：%s") % tr(error.message))
    for key, field in fields.items():
        field.textChanged.connect(invalidate)
    for (key, _, _), button in zip(definitions, browse_buttons):
        button.clicked.connect(lambda _checked=False, key=key: browse(key))
    save.clicked.connect(lambda _checked=False: save_configuration())
    status.clicked.connect(lambda _checked=False: run("status"))
    ensure.clicked.connect(lambda _checked=False: run("ensure"))
    open_web.clicked.connect(lambda _checked=False: run("open"))
    copy_prompt.clicked.connect(lambda _checked=False: copy_setup_prompt())
    close.clicked.connect(lambda _checked=False: dialog.close())

    def forget(_object=None):
        global _pft_dialog
        if _pft_dialog is dialog:
            _pft_dialog = None
    dialog.destroyed.connect(forget)
    _pft_dialog = dialog
    dialog.show()
    return dialog
