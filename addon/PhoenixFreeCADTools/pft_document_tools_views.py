# SPDX-License-Identifier: Apache-2.0
"""Native non-modal document information, cached dimensions and Markdown panel."""
from pathlib import Path
import re
import unicodedata

import pft_document_tools as tools
from pft_workbench_i18n import tr

TRANSLATABLE_STRINGS = (
    "Phoenix：当前文档工具", "当前文档工具", "文档", "（跟随活动文档）", "刷新当前内存", "处理", "名称", "显示名称", "类型", "说明",
    "内部对象（仅手动）", "顶层候选", "手动候选", "信息", "尺寸", "Markdown", "缩略图", "复制信息", "复制尺寸", "复制 Markdown", "导出 Markdown", "关闭",
    "选择仅作用于本面板，不改变树选择或活动文档。尺寸来自缓存，不重算、不验证。", "Markdown 默认省略文件路径，分享前请检查内容。",
    "读取失败，请刷新后重试。", "复制失败。", "Markdown 文件 (*.md *.markdown)", "Markdown 导出预览",
    "目标：%s", "目标已存在，确认将覆盖该 Markdown 文件。", "确认导出 Markdown；不会保存 CAD 文档。", "确认覆盖并导出", "确认导出", "取消",
    "Markdown 已导出；CAD 文档未保存。", "无法打开当前文档工具。", "对象范围（已选 %s / %s）",
    "无法激活此文档，请先在 FreeCAD 中切换。",
    "属性字段", "读取属性设置", "每行一个内部字段名，也可用逗号分隔；最多20项。留空则不读取属性。",
    "仅作用于本面板，不创建或修改CAD属性，也不保存配置。", "恢复常用字段",
)
_pft_dock = None


def _qt():
    from PySide import QtCore
    try:
        from PySide import QtWidgets
    except ImportError:
        from PySide import QtGui as QtWidgets
    return QtCore, QtWidgets


def _enum(owner, group, name):
    value = getattr(owner, name, None)
    return value if value is not None else getattr(getattr(owner, group, None), name, None)


def _existing(window):
    candidates = [_pft_dock]
    try:
        candidates.append(window.findChild(type(_pft_dock), "PhoenixTools_DocumentToolsDock") if _pft_dock is not None else None)
    except Exception:
        pass
    try:
        candidates.extend(window.findChildren(_qt()[1].QDockWidget, "PhoenixTools_DocumentToolsDock"))
    except Exception:
        pass
    for dialog in candidates:
        try:
            if dialog is not None and dialog.objectName() == "PhoenixTools_DocumentToolsDock" and dialog.parent() is window:
                return dialog
        except (RuntimeError, ReferenceError):
            pass
    return None


def _panel_host():
    import pft_panel_host
    return pft_panel_host


def _thumbnail_views():
    import pft_thumbnail_views
    return pft_thumbnail_views


def _shrink_horizontal(widget):
    """Keep data length out of a dock's minimum width, preserving Qt styling."""
    widget.setMinimumWidth(0)
    policy = widget.sizePolicy()
    ignored = _enum(type(policy), "Policy", "Ignored")
    if ignored is not None:
        policy.setHorizontalPolicy(ignored)
        widget.setSizePolicy(policy)
    return widget


def _wrapping_button_row(widgets, first, second):
    row = widgets.QFormLayout()
    wrap = _enum(widgets.QFormLayout, "RowWrapPolicy", "WrapLongRows")
    if wrap is not None:
        row.setRowWrapPolicy(wrap)
    grow = _enum(widgets.QFormLayout, "FieldGrowthPolicy", "AllNonFixedFieldsGrow")
    if grow is not None:
        row.setFieldGrowthPolicy(grow)
    row.addRow(first, second)
    return row


def _readonly(widgets, parent, name):
    editor = widgets.QPlainTextEdit(parent)
    editor.setObjectName(name)
    editor.setReadOnly(True)
    return _shrink_horizontal(editor)


def _plain(label, core):
    value = _enum(core.Qt, "TextFormat", "PlainText")
    if value is not None:
        label.setTextFormat(value)
    _shrink_horizontal(label)
    label.setWordWrap(True)
    return label


def _export_filename(document, suffix):
    """Suggest beside the saved CAD, or a safe basename for an unsaved document."""
    filename = document.get("FileName")
    if isinstance(filename, str) and filename and Path(filename).is_absolute():
        return str(Path(filename).with_suffix(suffix))
    label = document.get("Label") or document.get("Name") or "Document"
    unsafe = '<>:"/\\|?*'
    basename = "".join("_" if character in unsafe or unicodedata.category(character).startswith("C")
                       else character for character in str(label)).strip(" .")[:120]
    if not basename or basename.upper().split(".")[0] in {
            "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}:
        basename = "Document"
    return basename + suffix


def _preview_export(core, widgets, parent, plan):
    preview = widgets.QDialog(parent)
    preview.setObjectName("PhoenixTools_MarkdownExportPreview")
    preview.setWindowTitle(tr("Markdown 导出预览"))
    preview.resize(780, 560)
    layout = widgets.QVBoxLayout(preview)
    layout.addWidget(_plain(widgets.QLabel(tr("目标：%s") % plan["path"]), core))
    message = "目标已存在，确认将覆盖该 Markdown 文件。" if plan["exists"] else "确认导出 Markdown；不会保存 CAD 文档。"
    layout.addWidget(_plain(widgets.QLabel(tr(message)), core))
    editor = _readonly(widgets, preview, "PhoenixTools_MarkdownExportText")
    editor.setPlainText(plan["text"])
    layout.addWidget(editor, 1)
    ok = _enum(widgets.QDialogButtonBox, "StandardButton", "Ok")
    cancel = _enum(widgets.QDialogButtonBox, "StandardButton", "Cancel")
    buttons = widgets.QDialogButtonBox(ok | cancel)
    confirm = buttons.button(ok)
    confirm.setText(tr("确认覆盖并导出") if plan["exists"] else tr("确认导出"))
    confirm.setDefault(False)
    buttons.button(cancel).setText(tr("取消"))
    buttons.button(cancel).setDefault(True)
    buttons.accepted.connect(preview.accept)
    buttons.rejected.connect(preview.reject)
    layout.addWidget(buttons)
    execute = getattr(preview, "exec", None) or preview.exec_
    return execute() == _enum(widgets.QDialog, "DialogCode", "Accepted")



def _settings_icon():
    from PySide import QtGui
    return QtGui.QIcon(str(Path(__file__).parent / "Resources" / "icons" / "gear.svg"))


def _choose_property_names(core, widgets, parent, current):
    """Choose an explicit read scope; validation has no native or file access."""
    dialog = widgets.QDialog(parent)
    dialog.setObjectName("PhoenixTools_DocumentPropertySettingsDialog")
    dialog.setWindowTitle(tr("读取属性设置"))
    dialog.resize(400, 280)
    layout = widgets.QVBoxLayout(dialog)
    layout.addWidget(_plain(widgets.QLabel(tr("每行一个内部字段名，也可用逗号分隔；最多20项。留空则不读取属性。")), core))
    editor = widgets.QPlainTextEdit(dialog)
    editor.setObjectName("PhoenixTools_DocumentPropertyNames")
    editor.setMinimumWidth(0)
    editor.setMaximumHeight(160)
    editor.setPlainText("\n".join(tools.validate_property_names(current)))
    layout.addWidget(editor)
    defaults = widgets.QPushButton(tr("恢复常用字段"), dialog)
    defaults.setObjectName("PhoenixTools_DocumentPropertiesDefault")
    defaults.clicked.connect(lambda _checked=False: editor.setPlainText("\n".join(tools.validate_property_names(None))))
    layout.addWidget(defaults)
    status = _plain(widgets.QLabel(tr("仅作用于本面板，不创建或修改CAD属性，也不保存配置。")), core)
    status.setObjectName("PhoenixTools_DocumentPropertySettingsStatus")
    layout.addWidget(status)
    ok = _enum(widgets.QDialogButtonBox, "StandardButton", "Ok")
    cancel = _enum(widgets.QDialogButtonBox, "StandardButton", "Cancel")
    box = widgets.QDialogButtonBox(ok | cancel, dialog)
    box.setObjectName("PhoenixTools_DocumentPropertySettingsButtons")
    box.button(ok).setDefault(False)
    box.button(cancel).setDefault(True)
    choice = {"accepted": False, "names": current}

    def confirm():
        raw = editor.toPlainText()
        try:
            if len(raw) > 4096:
                raise tools.PftDocumentToolsError("INVALID_PROPERTY_NAMES", "属性字段必须是至多20个唯一的FreeCAD内部字段名。")
            names = tools.validate_property_names([name.strip() for name in re.split(r"[,\r\n]+", raw.replace("，", ",")) if name.strip()])
        except tools.PftDocumentToolsError as error:
            status.setText(tr(error.message))
            return
        choice["names"] = None if names == tools.validate_property_names(None) else names
        choice["accepted"] = True
        dialog.accept()

    box.accepted.connect(confirm)
    box.rejected.connect(dialog.reject)
    layout.addWidget(box)
    execute = getattr(dialog, "exec", None) or dialog.exec_
    accepted = execute() == _enum(widgets.QDialog, "DialogCode", "Accepted") and choice["accepted"]
    return (bool(accepted), choice["names"] if accepted else current)


def show_document_tools_dialog(app, gui):
    """Open/reuse one panel. Initial display reads memory, never clipboard/files."""
    global _pft_dock
    core, widgets = _qt()
    window = gui.getMainWindow()
    host = _panel_host()
    existing = _existing(window)
    if existing is not None:
        return host.present_panel(existing)
    dock = widgets.QDockWidget(tr("Phoenix 工具"), window)
    dock.setObjectName("PhoenixTools_DocumentToolsDock")
    dock.setWindowTitle(tr("Phoenix 工具"))
    dock.setToolTip(tr("Phoenix：当前文档工具"))
    dock.setMinimumWidth(300)
    attribute = _enum(core.Qt, "WidgetAttribute", "WA_DeleteOnClose")
    if attribute is not None:
        dock.setAttribute(attribute, True)
    content = widgets.QWidget(dock)
    layout = widgets.QVBoxLayout(content)
    row = widgets.QHBoxLayout()
    row.addWidget(widgets.QLabel(tr("文档")))
    documents = widgets.QComboBox(content)
    documents.setObjectName("PhoenixTools_DocumentSelector")
    _shrink_horizontal(documents)
    documents.setMinimumContentsLength(1)
    adjust = _enum(widgets.QComboBox, "SizeAdjustPolicy", "AdjustToMinimumContentsLengthWithIcon")
    if adjust is not None:
        documents.setSizeAdjustPolicy(adjust)
    row.addWidget(documents, 1)
    property_settings = widgets.QToolButton(content)
    property_settings.setObjectName("PhoenixTools_DocumentPropertySettings")
    property_settings.setText(tr("属性字段"))
    property_settings.setToolTip(tr("读取属性设置"))
    property_settings.setIcon(_settings_icon())
    icon_only = _enum(core.Qt, "ToolButtonStyle", "ToolButtonIconOnly")
    if icon_only is not None:
        property_settings.setToolButtonStyle(icon_only)
    row.addWidget(property_settings)
    refresh = widgets.QPushButton(tr("刷新当前内存"))
    refresh.setObjectName("PhoenixTools_DocumentRefresh")
    layout.addLayout(row)
    layout.addWidget(refresh)
    target_toggle = widgets.QToolButton(content)
    target_toggle.setObjectName("PhoenixTools_DocumentTargetToggle")
    target_toggle.setCheckable(True)
    _shrink_horizontal(target_toggle)
    button_style = _enum(core.Qt, "ToolButtonStyle", "ToolButtonTextBesideIcon")
    if button_style is not None:
        target_toggle.setToolButtonStyle(button_style)
    policy = getattr(widgets, "QSizePolicy", None)
    if policy is not None:
        horizontal = _enum(policy, "Policy", "Expanding")
        vertical = _enum(policy, "Policy", "Fixed")
        if horizontal is not None and vertical is not None:
            target_toggle.setSizePolicy(horizontal, vertical)
    layout.addWidget(target_toggle)
    target_section = widgets.QWidget(content)
    target_section.setObjectName("PhoenixTools_DocumentTargetSection")
    target_layout = widgets.QVBoxLayout(target_section)
    target_layout.setContentsMargins(0, 0, 0, 0)
    target_layout.addWidget(_plain(widgets.QLabel(tr("选择仅作用于本面板，不改变树选择或活动文档。尺寸来自缓存，不重算、不验证。")), core))
    table = widgets.QTableWidget(0, 5, target_section)
    table.setObjectName("PhoenixTools_DocumentTargets")
    _shrink_horizontal(table)
    adjust = _enum(widgets.QAbstractScrollArea, "SizeAdjustPolicy", "AdjustIgnored")
    if adjust is not None:
        table.setSizeAdjustPolicy(adjust)
    table.setHorizontalHeaderLabels([tr(key) for key in ("处理", "名称", "显示名称", "类型", "说明")])
    table.setMinimumHeight(96)
    table.setMaximumHeight(240)
    target_layout.addWidget(table)
    layout.addWidget(target_section)
    tabs = widgets.QTabWidget(content)
    tabs.setObjectName("PhoenixTools_DocumentTabs")
    _shrink_horizontal(tabs)
    tabs.setUsesScrollButtons(True)
    elide = _enum(core.Qt, "TextElideMode", "ElideRight")
    if elide is not None:
        tabs.setElideMode(elide)
    editors = {}
    for key, title in (("information", "信息"), ("dimensions", "尺寸"), ("markdown", "Markdown")):
        editor = _readonly(widgets, content, "PhoenixTools_Document" + key.title())
        editors[key] = editor
        tabs.addTab(editor, tr(title))
    thumbnail = _thumbnail_views().create_thumbnail_widget(
        app, gui, tabs, lambda: documents.currentData(), on_activate=lambda name: activate_document(name),
        get_export_filename=lambda name: thumbnail_export_filename(name))
    tabs.addTab(thumbnail, tr("缩略图"))
    layout.addWidget(tabs, 2)
    layout.addWidget(_plain(widgets.QLabel(tr("Markdown 默认省略文件路径，分享前请检查内容。")), core))
    buttons = {}
    for key, title in (("information", "复制信息"), ("dimensions", "复制尺寸"), ("markdown", "复制 Markdown"), ("export", "导出 Markdown")):
        button = widgets.QPushButton(tr(title))
        button.setObjectName("PhoenixTools_Document" + key.title() + "Action")
        buttons[key] = button
    layout.addLayout(_wrapping_button_row(widgets, buttons["information"], buttons["dimensions"]))
    layout.addLayout(_wrapping_button_row(widgets, buttons["markdown"], buttons["export"]))
    state = {"loading": False, "checks": {}, "snapshot": None, "snapshot_instance": None,
             "section_initialized": False, "property_names": None}

    def set_target_expanded(expanded):
        # Presentation only: never refresh CAD, change checks or run an export.
        target_section.setVisible(bool(expanded))
        arrow = _enum(core.Qt, "ArrowType", "DownArrow" if expanded else "RightArrow")
        if arrow is not None:
            target_toggle.setArrowType(arrow)

    target_toggle.toggled.connect(set_target_expanded)
    set_target_expanded(target_toggle.isChecked())

    def report(error):
        message = tr(error.message) if isinstance(error, tools.PftDocumentToolsError) else tr("读取失败，请刷新后重试。")
        state["snapshot"], state["snapshot_instance"] = None, None
        thumbnail.set_document(None, active=False)
        for editor in editors.values():
            editor.setPlainText(message)
        for button in buttons.values():
            button.setEnabled(False)

    def selected():
        return [name for name, check in state["checks"].items() if check.isChecked()]

    def update_target_summary():
        chosen = len(selected())
        target_toggle.setText(tr("对象范围（已选 %s / %s）") % (chosen, len(state["checks"])))
        if not state["section_initialized"]:
            state["section_initialized"] = True
            target_toggle.setChecked(chosen == 0)
            set_target_expanded(target_toggle.isChecked())

    def document_is_active(snapshot):
        document = snapshot.get("document")
        try:
            active = app.ActiveDocument
            gui_document = gui.activeDocument()
            return (document is not None and active is not None and active.Name == document["Name"]
                    and app.getDocument(document["Name"]) is active
                    and gui_document is not None and gui_document.Document is active)
        except Exception:
            return False

    def update_thumbnail_scope(snapshot):
        document = snapshot.get("document")
        thumbnail.set_document(document["Name"] if document else None, active=document_is_active(snapshot))

    def display(snapshot):
        document = snapshot.get("document")
        state["snapshot_instance"] = app.getDocument(document["Name"]) if document else None
        state["snapshot"] = snapshot
        editors["information"].setPlainText(tools.format_information(snapshot, tr=tr))
        editors["dimensions"].setPlainText(tools.format_dimensions(snapshot, tr=tr))
        editors["markdown"].setPlainText(tools.format_markdown(snapshot, tr=tr))
        for button in buttons.values():
            button.setEnabled(snapshot.get("document") is not None)
        update_thumbnail_scope(snapshot)

    def render(*_args):
        if state["loading"]:
            return False
        update_target_summary()
        try:
            snapshot = tools.read_document_tools(app, gui, document_name=documents.currentData(), target_names=selected(),
                                                 property_names=state["property_names"])
            display(snapshot)
            return snapshot.get("document") is not None
        except Exception as error:
            report(error)
            return False

    def load_targets(*_args):
        if state["loading"]:
            return
        try:
            snapshot = tools.read_document_tools(app, gui, document_name=documents.currentData(),
                                                 property_names=state["property_names"])
            state["loading"] = True
            state["checks"] = {}
            table.setRowCount(len(snapshot["candidates"]))
            for index, candidate in enumerate(snapshot["candidates"]):
                name = candidate["Name"]
                check = widgets.QCheckBox()
                check.setObjectName("PhoenixDocumentTarget_" + name)
                check.setChecked(name in snapshot["selected_names"])
                state["checks"][name] = check
                table.setCellWidget(index, 0, check)
                explanation = "内部对象（仅手动）" if candidate["internal"] else "顶层候选" if candidate["recommended"] else "手动候选"
                for column, value in enumerate((name, candidate["Label"], candidate["TypeId"], tr(explanation)), 1):
                    item = widgets.QTableWidgetItem(str(value))
                    item.setToolTip(str(value))
                    editable = _enum(core.Qt, "ItemFlag", "ItemIsEditable")
                    if editable is not None:
                        item.setFlags(item.flags() & ~editable)
                    table.setItem(index, column, item)
                check.stateChanged.connect(render)
            table.resizeColumnsToContents()
            # Inspect long names through the table's own horizontal scrollbar.
            for column, maximum in enumerate((48, 180, 200, 180, 180)):
                table.setColumnWidth(column, min(table.columnWidth(column), maximum))
            display(snapshot)
        except Exception as error:
            state["checks"] = {}
            table.setRowCount(0)
            report(error)
        finally:
            state["loading"] = False
            update_target_summary()

    def document_identity():
        # Poll native document names and wrapper identity only. Reopening a
        # same-named document must invalidate the previous instance's snapshot.
        active = app.ActiveDocument
        opened = app.listDocuments()
        instances = tuple(sorted((name, id(document)) for name, document in opened.items())) if isinstance(opened, dict) else ()
        return (active.Name if active is not None else None, instances)

    def activate_document(document_name):
        # Explicit button action: switch the native document without touching
        # object selection, cached geometry, transactions or CAD files.
        document = app.getDocument(document_name)
        if document is None:
            raise tools.PftDocumentToolsError("DOCUMENT_ACTIVATION_FAILED", "无法激活此文档，请先在 FreeCAD 中切换。")
        app.setActiveDocument(document_name)
        gui_document = gui.activeDocument()
        if app.ActiveDocument is not document or gui_document is None or gui_document.Document is not document:
            raise tools.PftDocumentToolsError("DOCUMENT_ACTIVATION_FAILED", "无法激活此文档，请先在 FreeCAD 中切换。")
        state["identity"] = document_identity()
        snapshot = state["snapshot"]
        if snapshot is not None:
            update_thumbnail_scope(snapshot)
        return True

    def thumbnail_export_filename(document_name):
        document = app.getDocument(document_name)
        snapshot = state["snapshot"]
        if (document is None or document is not state["snapshot_instance"] or snapshot is None
                or snapshot.get("document") is None or snapshot["document"]["Name"] != document_name):
            raise tools.PftDocumentToolsError("DOCUMENT_CHANGED", "文档已关闭或变化，请刷新。")
        # Only this explicit frozen preview's document supplies a suggested path.
        filename = document.FileName
        return _export_filename({"FileName": filename, "Label": document.Label, "Name": document_name}, ".png")

    def reload_documents(*_args, keep_targets=False):
        previous = documents.currentData()
        state["loading"] = True
        documents.clear()
        documents.addItem(tr("（跟随活动文档）"), None)
        index = 0
        for document in tools.list_open_documents(app):
            documents.addItem(document["Label"] + " (" + document["Name"] + ")", document["Name"])
            if previous is not None and document["Name"] == previous:
                index = documents.count() - 1
        documents.setCurrentIndex(index)
        state["loading"] = False
        try:
            state["identity"] = document_identity()
        except Exception:
            state["identity"] = None
        snapshot = state["snapshot"]
        current = documents.currentData()
        desired = current if current is not None else (state["identity"][0] if state["identity"] else None)
        existing_document = snapshot.get("document") if snapshot else None
        if (keep_targets and existing_document is not None and existing_document["Name"] == desired
                and state["snapshot_instance"] is app.getDocument(desired)):
            update_thumbnail_scope(snapshot)
        else:
            load_targets()

    def follow_active_document():
        try:
            identity = document_identity()
        except Exception:
            return
        previous = state.get("identity")
        if identity == previous:
            if state["snapshot"] is not None:
                # Also reconcile an old/stale availability flag without
                # rereading candidates, dimensions or other object content.
                update_thumbnail_scope(state["snapshot"])
            return
        state["identity"] = identity
        if previous is None or identity[1] != previous[1]:
            reload_documents(keep_targets=True)
        elif identity[0] != previous[0]:
            if documents.currentData() is None:
                load_targets()
            elif state["snapshot"] is not None:
                # A pinned document stays pinned; its capture availability still
                # follows whether it is the native active document.
                update_thumbnail_scope(state["snapshot"])

    def copy(kind):
        if not render():
            return
        try:
            widgets.QApplication.clipboard().setText(editors[kind].toPlainText())
        except Exception:
            widgets.QMessageBox.warning(dock, tr("当前文档工具"), tr("复制失败。"))

    def export():
        if not render():
            return
        try:
            options = _enum(widgets.QFileDialog, "Option", "DontConfirmOverwrite")
            args = {} if options is None else {"options": options}
            default_filename = _export_filename(state["snapshot"]["document"], ".md")
            choice = widgets.QFileDialog.getSaveFileName(dock, tr("导出 Markdown"), default_filename, tr("Markdown 文件 (*.md *.markdown)"), **args)
            filename = choice[0] if isinstance(choice, (list, tuple)) else choice
            if not filename:
                return
            if not Path(str(filename)).suffix:
                filename = str(filename) + ".md"
            plan = tools.prepare_markdown_export(str(filename), editors["markdown"].toPlainText())
            if plan is None or not _preview_export(core, widgets, dock, plan):
                return
            tools.write_markdown_export(plan, overwrite=plan["exists"])
            widgets.QMessageBox.information(dock, tr("当前文档工具"), tr("Markdown 已导出；CAD 文档未保存。"))
        except Exception as error:
            message = tr(error.message) if isinstance(error, tools.PftDocumentToolsError) else tr("Markdown 导出失败；未保存 CAD 文档。")
            widgets.QMessageBox.warning(dock, tr("当前文档工具"), message)

    def configure_properties():
        if state["loading"]:
            return
        accepted, names = _choose_property_names(core, widgets, dock, state["property_names"])
        if accepted and names != state["property_names"]:
            state["property_names"] = names
            render()

    property_settings.clicked.connect(lambda _checked=False: configure_properties())
    documents.currentIndexChanged.connect(load_targets)
    refresh.clicked.connect(reload_documents)
    for key in ("information", "dimensions", "markdown"):
        buttons[key].clicked.connect(lambda _checked=False, key=key: copy(key))
    buttons["export"].clicked.connect(lambda _checked=False: export())
    def forget(_object=None):
        global _pft_dock
        if _pft_dock is dock:
            _pft_dock = None
    dock.destroyed.connect(forget)
    _pft_dock = dock
    reload_documents()
    timer = core.QTimer(dock)
    timer.setObjectName("PhoenixTools_DocumentFollowTimer")
    timer.timeout.connect(follow_active_document)
    timer.start(400)
    dock.destroyed.connect(lambda _object=None: timer.stop())
    scroll = widgets.QScrollArea(dock)
    scroll.setObjectName("PhoenixTools_DocumentScroll")
    scroll.setWidgetResizable(True)
    _shrink_horizontal(scroll)
    frame = _enum(widgets.QFrame, "Shape", "NoFrame")
    if frame is not None:
        scroll.setFrameShape(frame)
    horizontal_off = _enum(core.Qt, "ScrollBarPolicy", "ScrollBarAlwaysOff")
    if horizontal_off is not None:
        scroll.setHorizontalScrollBarPolicy(horizontal_off)
    scroll.setWidget(content)
    host.mount_panel_dock(dock, window, scroll)
    return host.present_panel(dock)
