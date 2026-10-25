# SPDX-License-Identifier: Apache-2.0
"""View: render PySide dialogs and pass user-confirmed values to the injected controller."""

import phoenix_workbench_controller as controller
from phoenix_workbench_model import BOM_FIELDS, ToolError


def _qt():
    from PySide import QtCore
    try:
        from PySide import QtWidgets
    except ImportError:
        from PySide import QtGui as QtWidgets
    return QtCore, QtWidgets


def _dialog_exec(dialog):
    execute = getattr(dialog, "exec", None) or dialog.exec_
    return execute()


def _table_item(widgets, core, text, editable=False):
    item = widgets.QTableWidgetItem(str(text))
    if not editable:
        item.setFlags(item.flags() & ~core.Qt.ItemIsEditable)
    return item


def _report(gui, message, error=False):
    _, widgets = _qt()
    method = widgets.QMessageBox.warning if error else widgets.QMessageBox.information
    method(gui.getMainWindow(), "Phoenix Tools", message)


def _show_label_dialog(app, gui, title, description, prepare, apply, with_source=False):
    core, widgets = _qt()
    try:
        document, objects, snapshots, rows = prepare(app, gui)
        dialog = widgets.QDialog(gui.getMainWindow())
        dialog.setWindowTitle(title)
        dialog.resize(1100 if with_source else 980, 440)
        layout = widgets.QVBoxLayout(dialog)
        text = widgets.QLabel(description)
        text.setWordWrap(True)
        layout.addWidget(text)
        headers = ["内部 Name", "原 Label", "新 Label"] + (["直接来源引用"] if with_source else []) + ["检查结果"]
        table = widgets.QTableWidget(len(rows), len(headers))
        table.setHorizontalHeaderLabels(headers)
        for index, row in enumerate(rows):
            status = row["error"] or ("已一致，无需修改" if row["old_label"] == row["new_label"] else "可修改")
            values = [row["name"], row["old_label"], row["new_label"]] + ([row["source_display"]] if with_source else []) + [status]
            for column, value in enumerate(values):
                table.setItem(index, column, _table_item(widgets, core, value))
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        buttons = widgets.QDialogButtonBox(widgets.QDialogButtonBox.Ok | widgets.QDialogButtonBox.Cancel)
        confirm = buttons.button(widgets.QDialogButtonBox.Ok)
        confirm.setText("确认修改（可撤销）")
        confirm.setEnabled(not any(row["error"] for row in rows) and any(row["old_label"] != row["new_label"] for row in rows))
        buttons.button(widgets.QDialogButtonBox.Cancel).setText("关闭")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if _dialog_exec(dialog) != widgets.QDialog.Accepted:
            return
        count = apply(app, gui, document, objects, snapshots, rows)
        _report(gui, "已修改 %s 个所选对象的 Label。可按撤销恢复；尚未保存文件。" % count)
    except ToolError as error:
        _report(gui, str(error), error=True)
    except Exception as error:
        _report(gui, "操作失败（%s）；未提交的工具事务已取消。" % type(error).__name__, error=True)


def show_rename_dialog(app, gui):
    return _show_label_dialog(app, gui, "Phoenix：命名修正预览",
        "规则：零件号.零件版本-零件名称。仅修改所选对象的 Label。确认后可撤销。",
        controller.prepare_rename_context, controller.apply_rename)


def show_link_labels_dialog(app, gui):
    return _show_label_dialog(app, gui, "Phoenix：同步链接名称预览",
        "将所选 App::Link 实例的 Label 同步为直接来源的 Label。同一来源的实例允许同名；源对象保持原值。确认后可撤销。",
        controller.prepare_link_context, controller.apply_link_labels, with_source=True)


def _confirm_property_changes(gui, core, widgets, rows):
    dialog = widgets.QDialog(gui.getMainWindow())
    dialog.setWindowTitle("Phoenix：确认零件属性差异")
    dialog.resize(1000, 460)
    layout = widgets.QVBoxLayout(dialog)
    count = sum(row["changed"] for row in rows)
    layout.addWidget(widgets.QLabel("将写入 %s 个属性值；请核对现有值与待写入值。" % count))
    table = widgets.QTableWidget(len(rows), 5)
    table.setHorizontalHeaderLabels(["内部 Name", "属性", "现有值", "待写入值", "操作"])
    for index, row in enumerate(rows):
        old = "<缺失字段>" if row["missing"] else row["old_value"]
        action = "创建字段" if row["missing"] else "修改" if row["changed"] else "保留现值"
        for column, value in enumerate((row["name"], row["field"], old, row["new_value"], action)):
            table.setItem(index, column, _table_item(widgets, core, value))
    table.resizeColumnsToContents()
    table.horizontalHeader().setStretchLastSection(True)
    layout.addWidget(table)
    buttons = widgets.QDialogButtonBox(widgets.QDialogButtonBox.Ok | widgets.QDialogButtonBox.Cancel)
    buttons.button(widgets.QDialogButtonBox.Ok).setText("确认写入（可撤销）")
    buttons.button(widgets.QDialogButtonBox.Cancel).setText("取消")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return _dialog_exec(dialog) == widgets.QDialog.Accepted


def show_properties_dialog(app, gui):
    core, widgets = _qt()
    try:
        document, objects, snapshots = controller.prepare_properties_context(app, gui)
        intent = {"initialize_only": False}
        dialog = widgets.QDialog(gui.getMainWindow())
        dialog.setWindowTitle("Phoenix：填写零件属性")
        dialog.resize(1200, 500)
        layout = widgets.QVBoxLayout(dialog)
        text = widgets.QLabel("勾选要写入的字段并编辑值。初始化只补缺失字段；Label 推荐仅填空值并保留现有内容。写入前会逐项展示差异。")
        text.setWordWrap(True)
        layout.addWidget(text)
        choices = widgets.QHBoxLayout()
        checks = {}
        for field, description in BOM_FIELDS:
            checkbox = widgets.QCheckBox("%s (%s)" % (description.split(" ")[0], field))
            checkbox.setChecked(field in ("PartNumber", "PartVersion", "PartName"))
            checks[field] = checkbox
            choices.addWidget(checkbox)
        layout.addLayout(choices)
        table = widgets.QTableWidget(len(snapshots), 3 + len(BOM_FIELDS))
        table.setHorizontalHeaderLabels(["内部 Name", "当前 Label"] + [field for field, _ in BOM_FIELDS] + ["推荐 / 检查"])
        for row, snapshot in enumerate(snapshots):
            table.setItem(row, 0, _table_item(widgets, core, snapshot["name"]))
            table.setItem(row, 1, _table_item(widgets, core, snapshot["label"]))
            for column, (field, _) in enumerate(BOM_FIELDS, 2):
                item = _table_item(widgets, core, snapshot["fields"][field]["value"], editable=True)
                if not snapshot["fields"][field]["exists"]:
                    item.setToolTip("字段缺失；勾选后将在 CustomDefine 组中创建字符串属性。")
                table.setItem(row, column, item)
            table.setItem(row, 2 + len(BOM_FIELDS), _table_item(widgets, core, "待编辑"))
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)

        def read_values():
            return {snapshot["name"]: {field: table.item(row, column).text() for column, (field, _) in enumerate(BOM_FIELDS, 2)} for row, snapshot in enumerate(snapshots)}

        def initialize_missing():
            intent["initialize_only"] = True
            for checkbox in checks.values():
                checkbox.setChecked(True)
            for row, snapshot in enumerate(snapshots):
                missing = []
                for column, (field, _) in enumerate(BOM_FIELDS, 2):
                    value = snapshot["fields"][field]
                    table.setItem(row, column, _table_item(widgets, core, value["value"] if value["exists"] else ""))
                    if not value["exists"]:
                        missing.append(field)
                note = "待初始化为空：" + "、".join(missing) if missing else "字段均已存在，保留现值"
                table.item(row, 2 + len(BOM_FIELDS)).setText(note)

        def recommend_from_label():
            intent["initialize_only"] = False
            staged, notes = controller.recommend_property_values(snapshots, read_values())
            for field, checkbox in checks.items():
                checkbox.setChecked(field in ("PartNumber", "PartVersion", "PartName"))
            for row, snapshot in enumerate(snapshots):
                for column, (field, _) in enumerate(BOM_FIELDS, 2):
                    table.setItem(row, column, _table_item(widgets, core, staged[snapshot["name"]][field], editable=True))
                table.item(row, 2 + len(BOM_FIELDS)).setText(notes[snapshot["name"]])

        actions = widgets.QHBoxLayout()
        initialize = widgets.QPushButton("初始化缺失字段")
        initialize.setToolTip("仅准备创建缺失的五个正式字符串字段，默认空值；现有属性保持原值。")
        initialize.clicked.connect(initialize_missing)
        actions.addWidget(initialize)
        recommend = widgets.QPushButton("从当前 Label 推荐")
        recommend.setToolTip("只接受明确完整的编号.版本-名称；仅补空值，不推测类型码和型号。")
        recommend.clicked.connect(recommend_from_label)
        actions.addWidget(recommend)
        layout.addLayout(actions)
        buttons = widgets.QDialogButtonBox(widgets.QDialogButtonBox.Ok | widgets.QDialogButtonBox.Cancel)
        buttons.button(widgets.QDialogButtonBox.Ok).setText("预览写入差异")
        buttons.button(widgets.QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if _dialog_exec(dialog) != widgets.QDialog.Accepted:
            return
        selected_fields = [field for field, _ in BOM_FIELDS if checks[field].isChecked()]
        values = read_values()
        rows = controller.build_property_preview(app, gui, document, objects, snapshots, selected_fields, values, intent["initialize_only"])
        if not any(row["changed"] for row in rows):
            _report(gui, "属性已一致，无需写入。")
            return
        if not _confirm_property_changes(gui, core, widgets, rows):
            return
        count = controller.apply_properties(app, gui, document, objects, snapshots, selected_fields, values, intent["initialize_only"])
        _report(gui, "已写入 %s 个属性值。可按撤销恢复；尚未保存文件。" % count)
    except ToolError as error:
        _report(gui, str(error), error=True)
    except Exception as error:
        _report(gui, "操作失败（%s）；未提交的工具事务已取消。" % type(error).__name__, error=True)
