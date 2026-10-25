# SPDX-License-Identifier: Apache-2.0
"""View: render PySide dialogs and pass user-confirmed values to the injected controller."""

import pft_workbench_controller as controller
from pft_workbench_model import BOM_FIELDS, PftToolError
from pft_workbench_i18n import tr
import pft_workbench_settings as settings


def _qt():
    from PySide import QtCore
    try:
        from PySide import QtWidgets
    except ImportError:
        from PySide import QtGui as QtWidgets
    return QtCore, QtWidgets


def _shrink_horizontal(widget):
    widget.setMinimumWidth(0)
    policy = widget.sizePolicy()
    ignored = getattr(type(policy), "Ignored", None)
    if ignored is None:
        ignored = type(policy).Policy.Ignored
    policy.setHorizontalPolicy(ignored)
    widget.setSizePolicy(policy)
    return widget


def _plain_label(widgets, core, text):
    label = _shrink_horizontal(widgets.QLabel(text))
    plain = getattr(core.Qt, "PlainText", None)
    if plain is not None:
        label.setTextFormat(plain)
    label.setWordWrap(True)
    return label


def _responsive_table(core, widgets, table):
    _shrink_horizontal(table)
    # Keep every preview cell intact and scroll inside the table. Resizing its
    # columns to their values must not determine the enclosing dialog's width.
    owner = getattr(widgets, "QAbstractScrollArea", None)
    adjust = getattr(owner, "AdjustIgnored", None)
    if adjust is None:
        adjust = getattr(getattr(owner, "SizeAdjustPolicy", None), "AdjustIgnored", None)
    if adjust is not None:
        table.setSizeAdjustPolicy(adjust)
    scrolling = getattr(core.Qt, "ScrollBarAsNeeded", None)
    if scrolling is not None:
        table.setHorizontalScrollBarPolicy(scrolling)
    return table


def _dialog_exec(dialog):
    execute = getattr(dialog, "exec", None) or dialog.exec_
    return execute()


def _table_item(widgets, core, text, editable=False):
    item = widgets.QTableWidgetItem(str(text))
    item.setToolTip(str(text))
    if not editable:
        item.setFlags(item.flags() & ~core.Qt.ItemIsEditable)
    return item


def _report(gui, message, error=False):
    _, widgets = _qt()
    method = widgets.QMessageBox.warning if error else widgets.QMessageBox.information
    method(gui.getMainWindow(), "Phoenix Tools", tr(message))


def _target_checkbox(widgets, table, row, name, checked):
    checkbox = widgets.QCheckBox()
    checkbox.setObjectName("PhoenixTarget_" + name)
    checkbox.setChecked(checked)
    table.setCellWidget(row, 0, checkbox)
    return checkbox


def _show_label_dialog(app, gui, title, description, prepare, apply, with_source=False, operation="rename"):
    core, widgets = _qt()
    try:
        document, candidates, defaults = controller.prepare_target_options(app, gui, operation)
        if not candidates:
            raise PftToolError("当前文档没有适合此工具的对象。")
        candidate_rows = [(obj, obj.Name, obj.Label) for obj in candidates]
        shown = {"context": None, "can_apply": False}
        dialog = widgets.QDialog(gui.getMainWindow())
        dialog.setWindowTitle(title)
        dialog.resize(700, 480)
        layout = widgets.QVBoxLayout(dialog)
        text = _plain_label(widgets, core, description)
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addWidget(_plain_label(widgets, core, tr("当前文档：%s") % document.Name))
        layout.addWidget(_plain_label(widgets, core, tr("勾选要处理的对象；不会改变树中的选择。")))
        notice = _plain_label(widgets, core, "")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        headers = [tr("处理"), tr("内部 Name"), tr("原 Label"), tr("新 Label")] + ([tr("直接来源引用")] if with_source else []) + [tr("检查结果")]
        table = _responsive_table(core, widgets, widgets.QTableWidget(len(candidate_rows), len(headers)))
        table.setHorizontalHeaderLabels(headers)
        target_checks = [_target_checkbox(widgets, table, index, name, name in defaults)
                         for index, (_, name, _) in enumerate(candidate_rows)]
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        buttons = widgets.QDialogButtonBox(widgets.QDialogButtonBox.Ok | widgets.QDialogButtonBox.Cancel)
        confirm = buttons.button(widgets.QDialogButtonBox.Ok)
        confirm.setText(tr("确认修改"))
        confirm.setEnabled(False)
        buttons.button(widgets.QDialogButtonBox.Cancel).setText(tr("关闭"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def refresh_preview():
            shown["context"] = None
            shown["can_apply"] = False
            confirm.setEnabled(False)
            selected = [obj for (obj, _, _), check in zip(candidate_rows, target_checks) if check.isChecked()]
            rows_by_name = {}
            failure = ""
            if selected:
                try:
                    context = prepare(app, gui, document=document, objects=selected)
                    rows_by_name = {row["name"]: row for row in context[3]}
                    shown["context"] = context
                    shown["can_apply"] = (not any(row["error"] for row in context[3]) and
                                          any(row["old_label"] != row["new_label"] for row in context[3]))
                    confirm.setEnabled(shown["can_apply"])
                except PftToolError as error:
                    failure = tr(str(error))
                except Exception as error:
                    failure = tr("预览失败（%s）；请重新打开工具。") % type(error).__name__
            notice.setText(failure or ("" if selected else tr("请至少勾选一个处理对象。")))
            for index, (_, name, old_label) in enumerate(candidate_rows):
                row = rows_by_name.get(name)
                if row is not None:
                    status = tr(row["error"]) or (tr("已一致，无需修改") if row["old_label"] == row["new_label"] else tr("可修改"))
                    values = [name, row["old_label"], row["new_label"]] + ([row["source_display"]] if with_source else []) + [status]
                else:
                    status = failure if target_checks[index].isChecked() else tr("未选择")
                    values = [name, old_label, ""] + ([""] if with_source else []) + [status]
                for column, value in enumerate(values, 1):
                    table.setItem(index, column, _table_item(widgets, core, value))
            table.resizeColumnsToContents()

        # Connect only after table, buttons and preview state have been created.
        for check in target_checks:
            check.toggled.connect(lambda _checked: refresh_preview())
        refresh_preview()
        if _dialog_exec(dialog) != widgets.QDialog.Accepted:
            return
        if shown["context"] is None or not shown["can_apply"]:
            return
        document, objects, snapshots, rows = shown["context"]
        count = apply(app, gui, document, objects, snapshots, rows)
        _report(gui, tr("已修改 %s 个所选对象的 Label。可按撤销恢复；尚未保存文件。") % count)
    except PftToolError as error:
        _report(gui, str(error), error=True)
    except Exception as error:
        _report(gui, tr("操作失败（%s）；未提交的工具事务已取消。") % type(error).__name__, error=True)


def show_rename_dialog(app, gui):
    return _show_label_dialog(app, gui, tr("Phoenix：命名修正预览"),
        tr("规则：零件号.零件版本-零件名称。仅修改所选对象的 Label。确认后可撤销。"),
        controller.prepare_rename_context, controller.apply_rename)


def show_link_labels_dialog(app, gui):
    return _show_label_dialog(app, gui, tr("Phoenix：同步链接名称预览"),
        tr("将所选 App::Link 实例的 Label 同步为直接来源的 Label。同一来源的实例允许同名；源对象保持原值。确认后可撤销。"),
        controller.prepare_link_context, controller.apply_link_labels, with_source=True, operation="link_labels")


def _confirm_property_changes(gui, core, widgets, rows):
    dialog = widgets.QDialog(gui.getMainWindow())
    dialog.setWindowTitle(tr("Phoenix：确认零件属性差异"))
    dialog.resize(740, 460)
    layout = widgets.QVBoxLayout(dialog)
    count = sum(row["changed"] for row in rows)
    layout.addWidget(_plain_label(widgets, core, tr("将写入 %s 个属性值；请核对现有值与待写入值。") % count))
    table = _responsive_table(core, widgets, widgets.QTableWidget(len(rows), 5))
    table.setHorizontalHeaderLabels([tr("内部 Name"), tr("属性"), tr("现有值"), tr("待写入值"), tr("操作")])
    for index, row in enumerate(rows):
        old = tr("<缺失字段>") if row["missing"] else row["old_value"]
        action = tr("创建字段") if row["missing"] else tr("修改") if row["changed"] else tr("保留现值")
        for column, value in enumerate((row["name"], row["field"], old, row["new_value"], action)):
            table.setItem(index, column, _table_item(widgets, core, value))
    table.resizeColumnsToContents()
    table.horizontalHeader().setStretchLastSection(True)
    layout.addWidget(table)
    buttons = widgets.QDialogButtonBox(widgets.QDialogButtonBox.Ok | widgets.QDialogButtonBox.Cancel)
    buttons.button(widgets.QDialogButtonBox.Ok).setText(tr("确认写入"))
    buttons.button(widgets.QDialogButtonBox.Cancel).setText(tr("取消"))
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return _dialog_exec(dialog) == widgets.QDialog.Accepted


def show_properties_dialog(app, gui):
    core, widgets = _qt()
    try:
        document, candidates, defaults = controller.prepare_target_options(app, gui, "properties")
        if not candidates:
            raise PftToolError("当前文档没有适合此工具的对象。")
        document, objects, snapshots = controller.prepare_properties_context(app, gui, document=document, objects=candidates)
        intent = {"initialize_only": False}
        dialog = widgets.QDialog(gui.getMainWindow())
        dialog.setWindowTitle(tr("Phoenix：填写零件属性"))
        dialog.resize(820, 500)
        layout = widgets.QVBoxLayout(dialog)
        text = _plain_label(widgets, core, tr("勾选要写入的字段并编辑值。初始化只补缺失字段；Label 推荐仅填空值并保留现有内容。写入前会逐项展示差异。"))
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addWidget(_plain_label(widgets, core, tr("当前文档：%s") % document.Name))
        layout.addWidget(_plain_label(widgets, core, tr("勾选要处理的对象；不会改变树中的选择。")))
        notice = _plain_label(widgets, core, "")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        choices = widgets.QGridLayout()
        checks = {}
        for field_index, (field, description) in enumerate(BOM_FIELDS):
            checkbox = widgets.QCheckBox("%s (%s)" % (tr(description.split(" ")[0]), field))
            checkbox.setChecked(field in ("PartNumber", "PartVersion", "PartName"))
            checks[field] = checkbox
            choices.addWidget(checkbox, field_index // 2, field_index % 2)
        layout.addLayout(choices)
        table = _responsive_table(core, widgets, widgets.QTableWidget(len(snapshots), 4 + len(BOM_FIELDS)))
        table.setHorizontalHeaderLabels([tr("处理"), tr("内部 Name"), tr("当前 Label")] + [field for field, _ in BOM_FIELDS] + [tr("推荐 / 检查")])
        target_checks = []
        for row, snapshot in enumerate(snapshots):
            target_checks.append(_target_checkbox(widgets, table, row, snapshot["name"], snapshot["name"] in defaults))
            table.setItem(row, 1, _table_item(widgets, core, snapshot["name"]))
            table.setItem(row, 2, _table_item(widgets, core, snapshot["label"]))
            for column, (field, _) in enumerate(BOM_FIELDS, 3):
                item = _table_item(widgets, core, snapshot["fields"][field]["value"], editable=True)
                if not snapshot["fields"][field]["exists"]:
                    item.setToolTip(tr("字段缺失；勾选后将在 CustomDefine 组中创建字符串属性。"))
                table.setItem(row, column, item)
            table.setItem(row, 3 + len(BOM_FIELDS), _table_item(widgets, core, tr("待编辑")))
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)

        def selected_rows():
            return [(row, obj, snapshot) for row, (obj, snapshot, check) in enumerate(zip(objects, snapshots, target_checks)) if check.isChecked()]

        def read_values():
            return {snapshot["name"]: {field: table.item(row, column).text() for column, (field, _) in enumerate(BOM_FIELDS, 3)} for row, _, snapshot in selected_rows()}

        def initialize_missing():
            intent["initialize_only"] = True
            for checkbox in checks.values():
                checkbox.setChecked(True)
            for row, _, snapshot in selected_rows():
                missing = []
                for column, (field, _) in enumerate(BOM_FIELDS, 3):
                    value = snapshot["fields"][field]
                    table.setItem(row, column, _table_item(widgets, core, value["value"] if value["exists"] else ""))
                    if not value["exists"]:
                        missing.append(field)
                note = tr("待初始化为空：") + tr("、").join(missing) if missing else tr("字段均已存在，保留现值")
                table.item(row, 3 + len(BOM_FIELDS)).setText(note)

        def recommend_from_label():
            intent["initialize_only"] = False
            selected = selected_rows()
            staged, notes = controller.recommend_property_values([snapshot for _, _, snapshot in selected], read_values())
            for field, checkbox in checks.items():
                checkbox.setChecked(field in ("PartNumber", "PartVersion", "PartName"))
            for row, _, snapshot in selected:
                for column, (field, _) in enumerate(BOM_FIELDS, 3):
                    table.setItem(row, column, _table_item(widgets, core, staged[snapshot["name"]][field], editable=True))
                table.item(row, 3 + len(BOM_FIELDS)).setText(tr(notes[snapshot["name"]]))

        actions = widgets.QHBoxLayout()
        initialize = widgets.QPushButton(tr("补齐字段"))
        initialize.setToolTip(tr("仅准备创建缺失的五个正式字符串字段，默认空值；现有属性保持原值。"))
        initialize.clicked.connect(initialize_missing)
        actions.addWidget(initialize)
        recommend = widgets.QPushButton(tr("名称推荐"))
        recommend.setToolTip(tr("只接受明确完整的编号.版本-名称；仅补空值，不推测类型码和型号。"))
        recommend.clicked.connect(recommend_from_label)
        actions.addWidget(recommend)
        layout.addLayout(actions)
        buttons = widgets.QDialogButtonBox(widgets.QDialogButtonBox.Ok | widgets.QDialogButtonBox.Cancel)
        confirm = buttons.button(widgets.QDialogButtonBox.Ok)
        confirm.setText(tr("差异预览"))
        buttons.button(widgets.QDialogButtonBox.Cancel).setText(tr("取消"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def refresh_targets():
            any_selected = bool(selected_rows())
            confirm.setEnabled(any_selected)
            initialize.setEnabled(any_selected)
            recommend.setEnabled(any_selected)
            notice.setText("" if any_selected else tr("请至少勾选一个处理对象。"))

        for check in target_checks:
            check.toggled.connect(lambda _checked: refresh_targets())
        refresh_targets()
        if _dialog_exec(dialog) != widgets.QDialog.Accepted:
            return
        selected = selected_rows()
        if not selected:
            return
        selected_objects = [obj for _, obj, _ in selected]
        selected_snapshots = [snapshot for _, _, snapshot in selected]
        selected_fields = [field for field, _ in BOM_FIELDS if checks[field].isChecked()]
        values = read_values()
        rows = controller.build_property_preview(app, gui, document, selected_objects, selected_snapshots, selected_fields, values, intent["initialize_only"])
        if not any(row["changed"] for row in rows):
            _report(gui, tr("属性已一致，无需写入。"))
            return
        if not _confirm_property_changes(gui, core, widgets, rows):
            return
        count = controller.apply_properties(app, gui, document, selected_objects, selected_snapshots, selected_fields, values, intent["initialize_only"])
        _report(gui, tr("已写入 %s 个属性值。可按撤销恢复；尚未保存文件。") % count)
    except PftToolError as error:
        _report(gui, str(error), error=True)
    except Exception as error:
        _report(gui, tr("操作失败（%s）；未提交的工具事务已取消。") % type(error).__name__, error=True)


def show_mcp_status_dialog(gui, snapshot):
    """Display only the supplied bridge snapshot; never inspect a document or service."""
    _, widgets = _qt()
    state = tr("正在停止") if snapshot.get("stopping") else tr("运行中") if snapshot.get("running") else tr("已停止")
    unknown = tr("未知")
    host, port = snapshot.get("host"), snapshot.get("port")
    address = "%s:%s" % (host, port) if host and port is not None else unknown
    message = "\n".join((
        tr("FreeCAD 桥接状态：%s") % state,
        tr("这是打开面板时读取的状态快照。"),
        tr("桥接地址：%s") % address,
        tr("插件版本：%s") % (snapshot.get("addon_version") or unknown),
        tr("协议版本：%s") % (snapshot.get("protocol_version") or unknown),
        "",
        tr("这里仅表示 FreeCAD 内的本机桥接状态。Node MCP 服务和聊天客户端连接需另行验证。"),
    ))
    return widgets.QMessageBox.information(gui.getMainWindow(), tr("Phoenix：MCP 桥接状态"), message)


def confirm_mcp_stop(gui):
    """A cancellation-default modal; stopping does not cancel already-started work."""
    core, widgets = _qt()
    dialog = widgets.QDialog(gui.getMainWindow())
    dialog.setWindowTitle(tr("Phoenix：确认停止 MCP 桥接"))
    dialog.resize(620, 220)
    layout = widgets.QVBoxLayout(dialog)
    explanation = _plain_label(widgets, core, tr("请确认要停止 FreeCAD 桥接。停止后不再接受新连接；已经开始的操作不会被取消。"))
    explanation.setWordWrap(True)
    layout.addWidget(explanation)
    buttons = widgets.QDialogButtonBox(widgets.QDialogButtonBox.Ok | widgets.QDialogButtonBox.Cancel)
    stop = buttons.button(widgets.QDialogButtonBox.Ok)
    cancel = buttons.button(widgets.QDialogButtonBox.Cancel)
    stop.setText(tr("停止桥接"))
    stop.setAutoDefault(False)
    stop.setDefault(False)
    cancel.setText(tr("取消"))
    cancel.setAutoDefault(True)
    cancel.setDefault(True)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return _dialog_exec(dialog) == widgets.QDialog.Accepted


def show_settings_dialog(app, gui, on_applied=None, bridge_snapshot=None):
    """Choose local presentation and startup preferences without CAD access."""
    core, widgets = _qt()
    try:
        options = settings.load_options(app)
        language = options["language"]
        dialog = widgets.QDialog(gui.getMainWindow())
        dialog.setWindowTitle(tr("Phoenix：设置"))
        dialog.resize(520, 360)
        layout = widgets.QVBoxLayout(dialog)
        explanation = _plain_label(widgets, core, tr("语言设置仅影响 Phoenix Tools；不修改零件数据。部分 FreeCAD 提示可能仍使用原语言。"))
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        row = widgets.QHBoxLayout()
        row.addWidget(_plain_label(widgets, core, tr("界面语言")))
        choices = _shrink_horizontal(widgets.QComboBox())
        choices.addItem(tr("简体中文"), "zh")
        choices.addItem(tr("English"), "en")
        choices.setCurrentIndex(0 if language == "zh" else 1)
        row.addWidget(choices)
        layout.addLayout(row)
        toolbar_row = widgets.QHBoxLayout()
        toolbar_row.addWidget(_plain_label(widgets, core, tr("工具条显示")))
        toolbar_choices = _shrink_horizontal(widgets.QComboBox())
        toolbar_choices.addItem(tr("图标和文字"), "icon_text")
        toolbar_choices.addItem(tr("仅图标"), "icon")
        toolbar_choices.setCurrentIndex(1 if options.get("toolbar_button_style") == "icon" else 0)
        toolbar_row.addWidget(toolbar_choices)
        layout.addLayout(toolbar_row)
        auto_start = widgets.QCheckBox(tr("FreeCAD 启动后自动启动 MCP 桥接"))
        auto_start.setChecked(options["auto_start_mcp"])
        layout.addWidget(auto_start)
        startup_note = _plain_label(widgets, core, tr("自动启动设置在下次 FreeCAD 启动时生效；保存设置不会当场启停桥接。"))
        startup_note.setWordWrap(True)
        layout.addWidget(startup_note)
        snapshot = bridge_snapshot if isinstance(bridge_snapshot, dict) else {}
        host, port = snapshot.get("host"), snapshot.get("port")
        address = "%s:%s" % (host, port) if host and port is not None else tr("未知")
        layout.addWidget(_plain_label(widgets, core, tr("桥接地址：%s") % address))
        layout.addWidget(_plain_label(widgets, core, tr("已加载插件版本：%s") % (snapshot.get("addon_version") or tr("未知"))))
        bridge_note = _plain_label(widgets, core, tr("以上只读信息来自当前 FreeCAD 桥接；服务地址和端口不在此处修改。"))
        bridge_note.setWordWrap(True)
        layout.addWidget(bridge_note)
        layout.addStretch(1)
        buttons = widgets.QDialogButtonBox(widgets.QDialogButtonBox.Ok | widgets.QDialogButtonBox.Cancel)
        buttons.button(widgets.QDialogButtonBox.Ok).setText(tr("应用设置"))
        buttons.button(widgets.QDialogButtonBox.Cancel).setText(tr("取消"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if _dialog_exec(dialog) != widgets.QDialog.Accepted:
            return
        settings.save_options(app, choices.currentData(), auto_start.isChecked(), toolbar_choices.currentData())
        if on_applied is not None:
            on_applied()
        _report(gui, tr("工作台设置已更新。"))
    except settings.PftSettingsError as error:
        _report(gui, str(error), error=True)
    except Exception as error:
        _report(gui, tr("设置操作失败（%s）。") % type(error).__name__, error=True)
