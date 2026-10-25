# SPDX-License-Identifier: Apache-2.0
"""Explicit material diagnostics, complete preview and transactional apply UI."""
import json
import math

from pft_workbench_i18n import tr

TRANSLATABLE_STRINGS = (
    "对象外观", "随机配色", "设置透明度", "当前文档：%s", "所选对象：%s 个",
    "先检查当前材质，再生成完整修改预览；只有明确应用才修改 CAD，可撤销，不自动保存。",
    "当前材质诊断", "诊断包含全局和逐材质透明度及一致性；属性读取不等于屏幕像素验证。",
    "透明度（0% 不透明，100% 完全透明）", "随机色只在点击生成修改预览时抽取；应用不会重新抽取。",
    "生成修改预览", "完整材质修改预览（JSON，未裁剪）", "尚未生成修改预览。", "完整注意事项",
    "应用修改", "取消", "参数已变化，旧预览失效；请重新生成。", "正在生成修改预览…",
    "预览已生成；请核对全部修改和注意事项后再应用。", "此预览不可应用，请检查诊断。",
    "正在应用修改…", "外观检查失败；未修改 CAD。", "外观预览失败；未修改 CAD。",
    "外观应用失败；请检查当前外观与撤销记录，未自动保存 CAD。",
    "外观应用结果无法确认，请检查当前外观与撤销记录；未自动保存 CAD。",
    "当前透明度摘要", "修改内容", "完整材质数据", "未知", "是", "否", "不支持：%s",
    "全局透明度：%s", "材质 %s 透明度：%s", "逐材质透明度不同：%s", "全局与逐材质不一致：%s",
    "Link 实例覆盖：%s", "Link 全局透明度不作推断。", "Link 本地材质（未启用时不代表报告外观）", "报告材质 %s 透明度：%s",
    "报告材质透明度不同：%s", "来源逐材质透明度不同：%s",
    "来源对象：%s", "来源全局透明度：%s", "来源材质 %s 透明度：%s", "来源全局与逐材质不一致：%s",
    "全局透明度：%s → %s", "材质条目数：%s → %s", "材质 %s：透明度 %s → %s",
    "颜色 RGBA：%s → %s", "%s：%s → %s", "Link 实例材质覆盖：%s → %s",
    "实例本地材质属性的修改：", "报告外观透明度：%s → %s", "来源对象不在本次写入范围内。",
    "未列出的材质字段保持不变。", "完整精确属性值可展开“完整材质数据”查看。",
)


def _qt():
    from PySide import QtCore
    try:
        from PySide import QtWidgets
    except ImportError:
        from PySide import QtGui as QtWidgets
    return QtCore, QtWidgets


def _visual_tools():
    import pft_visual_tools
    return pft_visual_tools


def _enum(owner, group, name):
    value = getattr(owner, name, None)
    return value if value is not None else getattr(getattr(owner, group, None), name, None)


def _plain(label, core):
    mode = _enum(core.Qt, "TextFormat", "PlainText")
    if mode is not None:
        label.setTextFormat(mode)
    label.setWordWrap(True)
    return label


def _editor(widgets, parent, name):
    editor = widgets.QPlainTextEdit(parent)
    editor.setObjectName(name)
    editor.setReadOnly(True)
    return editor


def _json_text(value):
    # Complete bounded native DTO/plan. Never slice, abbreviate or replace targets.
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)



def _mapping(value):
    return value if isinstance(value, dict) else {}


def _items(value):
    return value if isinstance(value, (tuple, list)) else ()


def _percent(value, translate):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
        return translate("未知")
    return format(value, ".6g") + "%"


def _boolean(value, translate):
    return translate("是" if value is True else "否" if value is False else "未知")


def _value(value, translate):
    if value is None:
        return translate("未知")
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _heading(row):
    name = str(row.get("Name", ""))
    label = str(row.get("Label", name))
    return name if label == name else label + " (" + name + ")"


def _percent_lines(values, label, translate):
    return [translate(label) % (index + 1, _percent(value, translate))
            for index, value in enumerate(_items(values))]


def format_current_transparency(inspection, tr=tr):
    """Format every reported row/material; no CAD access or inferred geometry."""
    lines = []
    for value in _items(_mapping(inspection).get("rows")):
        row = _mapping(value)
        lines.append(_heading(row))
        if row.get("status") != "supported":
            lines.append(tr("不支持：%s") % tr(row.get("reason") or "未知"))
        if row.get("kind") == "link":
            lines.append(tr("Link 实例覆盖：%s") % _boolean(row.get("override_material"), tr))
            lines.append(tr("Link 全局透明度不作推断。"))
            lines.append(tr("Link 本地材质（未启用时不代表报告外观）"))
            lines.extend(_percent_lines(row.get("material_transparency_percent"), "材质 %s 透明度：%s", tr))
            lines.extend(_percent_lines(row.get("reported_material_transparency_percent"), "报告材质 %s 透明度：%s", tr))
            lines.append(tr("报告材质透明度不同：%s") % _boolean(row.get("reported_mixed_material_transparency"), tr))
            source = _mapping(row.get("source"))
            lines.append(tr("来源对象：%s") % str(source.get("Name") or tr("未知")))
            lines.append(tr("来源全局透明度：%s") % _percent(source.get("global_transparency_percent"), tr))
            lines.extend(_percent_lines(source.get("material_transparency_percent"), "来源材质 %s 透明度：%s", tr))
            lines.append(tr("来源逐材质透明度不同：%s") % _boolean(source.get("mixed_material_transparency"), tr))
            lines.append(tr("来源全局与逐材质不一致：%s") % _boolean(source.get("global_material_mismatch"), tr))
        else:
            lines.append(tr("全局透明度：%s") % _percent(row.get("global_transparency_percent"), tr))
            lines.extend(_percent_lines(row.get("material_transparency_percent"), "材质 %s 透明度：%s", tr))
            lines.append(tr("逐材质透明度不同：%s") % _boolean(row.get("mixed_material_transparency"), tr))
            lines.append(tr("全局与逐材质不一致：%s") % _boolean(row.get("global_material_mismatch"), tr))
        lines.append("")
    return "\n".join(lines).rstrip()


def _material_changes(before, after, index, translate):
    before, after = _mapping(before), _mapping(after)
    def alpha(material):
        value = material.get("Transparency")
        return value * 100 if type(value) in (int, float) else None
    lines = [translate("材质 %s：透明度 %s → %s") % (
        index, _percent(alpha(before), translate), _percent(alpha(after), translate))]
    if before.get("DiffuseColor") != after.get("DiffuseColor"):
        lines.append("  " + translate("颜色 RGBA：%s → %s") % (
            _value(before.get("DiffuseColor"), translate), _value(after.get("DiffuseColor"), translate)))
    for field in sorted(set(before) | set(after)):
        if field not in ("Transparency", "DiffuseColor") and before.get(field) != after.get(field):
            lines.append("  " + translate("%s：%s → %s") % (
                field, _value(before.get(field), translate), _value(after.get(field), translate)))
    return lines


def format_appearance_changes(plan, tr=tr):
    """List every target/material change; original precise JSON stays available."""
    lines = []
    for value in _items(_mapping(plan).get("targets")):
        row = _mapping(value)
        before, after = _mapping(row.get("before")), _mapping(row.get("after"))
        lines.append(_heading(row))
        if before.get("kind") == "link":
            lines.append(tr("Link 实例材质覆盖：%s → %s") % (
                _boolean(before.get("OverrideMaterial"), tr), _boolean(after.get("OverrideMaterial"), tr)))
            lines.append(tr("实例本地材质属性的修改："))
            lines.extend(_material_changes(before.get("ShapeMaterial"), after.get("ShapeMaterial"), 1, tr))
            effective = _mapping(row.get("effective_material_before"))
            old = effective.get("Transparency")
            new = _mapping(after.get("ShapeMaterial")).get("Transparency")
            lines.append(tr("报告外观透明度：%s → %s") % (
                _percent(old * 100 if type(old) in (int, float) else None, tr),
                _percent(new * 100 if type(new) in (int, float) else None, tr)))
            lines.append(tr("来源对象不在本次写入范围内。"))
        else:
            lines.append(tr("全局透明度：%s → %s") % (
                _percent(before.get("Transparency"), tr), _percent(after.get("Transparency"), tr)))
            old, new = _items(before.get("ShapeAppearance")), _items(after.get("ShapeAppearance"))
            lines.append(tr("材质条目数：%s → %s") % (len(old), len(new)))
            for index in range(max(len(old), len(new))):
                lines.extend(_material_changes(old[index] if index < len(old) else None,
                                               new[index] if index < len(new) else None, index + 1, tr))
        lines.append("")
    lines.extend((tr("未列出的材质字段保持不变。"), tr("完整精确属性值可展开“完整材质数据”查看。")))
    return "\n".join(lines)


def show_appearance_dialog(app, gui, parent, document_name, object_names, action):
    """Return an applied summary only; cancelling/unapplied dialogs return None.

    Diagnostics read native attributes. Explicit Generate invokes prepare once;
    Apply uses that exact plan and never regenerates a random color or writes
    native properties itself. All capabilities stay in this dialog's memory.
    """
    core, widgets = _qt()
    dialog = widgets.QDialog(parent)
    dialog.setObjectName("PhoenixTools_AppearanceDialog")
    title = "随机配色" if action == "random_color" else "设置透明度"
    dialog.setWindowTitle(tr("对象外观") + " — " + tr(title))
    dialog.resize(760, 680)
    layout = widgets.QVBoxLayout(dialog)
    layout.addWidget(_plain(widgets.QLabel(tr("当前文档：%s") % str(document_name)), core))
    names = list(object_names) if isinstance(object_names, (tuple, list)) else []
    layout.addWidget(_plain(widgets.QLabel(tr("所选对象：%s 个") % len(names)), core))
    layout.addWidget(_plain(widgets.QLabel(tr("先检查当前材质，再生成完整修改预览；只有明确应用才修改 CAD，可撤销，不自动保存。")), core))
    layout.addWidget(widgets.QLabel(tr("当前透明度摘要")))
    summary = _editor(widgets, dialog, "PhoenixTools_AppearanceSummary")
    summary.setMaximumHeight(150)
    layout.addWidget(summary, 1)
    data_section = widgets.QWidget(dialog)
    data_section.setObjectName("PhoenixTools_AppearanceDataSection")
    data_layout = widgets.QVBoxLayout(data_section)
    data_layout.setContentsMargins(0, 0, 0, 0)
    data_layout.addWidget(widgets.QLabel(tr("当前材质诊断")))
    diagnostics = _editor(widgets, data_section, "PhoenixTools_AppearanceInspection")
    diagnostics.setMaximumHeight(160)
    data_layout.addWidget(diagnostics)
    spin = slider = None
    if action == "set_transparency":
        layout.addWidget(_plain(widgets.QLabel(tr("透明度（0% 不透明，100% 完全透明）")), core))
        controls = widgets.QHBoxLayout()
        spin = widgets.QSpinBox(dialog)
        spin.setObjectName("PhoenixTools_AppearanceTransparencySpin")
        spin.setRange(0, 100)
        spin.setSingleStep(5)
        spin.setSuffix("%")
        spin.setValue(50)
        controls.addWidget(spin)
        slider = widgets.QSlider(dialog)
        slider.setObjectName("PhoenixTools_AppearanceTransparencySlider")
        horizontal = _enum(core.Qt, "Orientation", "Horizontal")
        if horizontal is not None:
            slider.setOrientation(horizontal)
        slider.setRange(0, 100)
        slider.setSingleStep(5)
        slider.setValue(50)
        controls.addWidget(slider, 1)
        layout.addLayout(controls)
    else:
        layout.addWidget(_plain(widgets.QLabel(tr("随机色只在点击生成修改预览时抽取；应用不会重新抽取。")), core))
    generate = widgets.QPushButton(tr("生成修改预览"), dialog)
    generate.setObjectName("PhoenixTools_AppearanceGenerate")
    layout.addWidget(generate)
    layout.addWidget(widgets.QLabel(tr("修改内容")))
    changes = _editor(widgets, dialog, "PhoenixTools_AppearanceChanges")
    changes.setPlainText(tr("尚未生成修改预览。"))
    layout.addWidget(changes, 2)
    data_layout.addWidget(widgets.QLabel(tr("完整材质修改预览（JSON，未裁剪）")))
    preview = _editor(widgets, data_section, "PhoenixTools_AppearancePreview")
    preview.setPlainText(tr("尚未生成修改预览。"))
    preview.setMaximumHeight(180)
    data_layout.addWidget(preview)
    data_toggle = widgets.QToolButton(dialog)
    data_toggle.setObjectName("PhoenixTools_AppearanceDataToggle")
    data_toggle.setText(tr("完整材质数据"))
    data_toggle.setCheckable(True)
    style = _enum(core.Qt, "ToolButtonStyle", "ToolButtonTextBesideIcon")
    if style is not None:
        data_toggle.setToolButtonStyle(style)
    def expand_data(expanded):
        data_section.setVisible(bool(expanded))
        arrow = _enum(core.Qt, "ArrowType", "DownArrow" if expanded else "RightArrow")
        if arrow is not None:
            data_toggle.setArrowType(arrow)
    data_toggle.toggled.connect(expand_data)
    data_toggle.setChecked(False)
    expand_data(False)
    layout.addWidget(data_toggle)
    layout.addWidget(data_section)
    layout.addWidget(widgets.QLabel(tr("完整注意事项")))
    warnings = _editor(widgets, dialog, "PhoenixTools_AppearanceWarnings")
    warnings.setMaximumHeight(110)
    layout.addWidget(warnings)
    status = _plain(widgets.QLabel("", dialog), core)
    status.setObjectName("PhoenixTools_AppearanceStatus")
    layout.addWidget(status)
    ok = _enum(widgets.QDialogButtonBox, "StandardButton", "Ok")
    cancel = _enum(widgets.QDialogButtonBox, "StandardButton", "Cancel")
    buttons = widgets.QDialogButtonBox(ok | cancel)
    buttons.setObjectName("PhoenixTools_AppearanceButtons")
    confirm = buttons.button(ok)
    confirm.setObjectName("PhoenixTools_AppearanceApply")
    confirm.setText(tr("应用修改"))
    confirm.setDefault(False)
    if callable(getattr(confirm, "setAutoDefault", None)):
        confirm.setAutoDefault(False)
    cancel_button = buttons.button(cancel)
    cancel_button.setObjectName("PhoenixTools_AppearanceCancel")
    cancel_button.setText(tr("取消"))
    cancel_button.setDefault(True)
    layout.addWidget(buttons)
    state = {"plan": None, "summary": None, "busy": False, "inspection_ok": False,
             "inspection_warnings": [], "plan_warnings": []}
    visual = None

    def update_controls():
        generate.setEnabled(state["inspection_ok"] and not state["busy"] and state["summary"] is None)
        confirm.setEnabled(bool(state["plan"] is not None and state["plan"].get("can_apply") is True
                                and not state["busy"] and state["summary"] is None))
        for control in (spin, slider):
            if control is not None:
                control.setEnabled(state["inspection_ok"] and not state["busy"] and state["summary"] is None)

    def all_warnings():
        return state["inspection_warnings"] + state["plan_warnings"]

    def show_warnings():
        warnings.setPlainText("\n".join(tr(message) for message in all_warnings()))

    def invalidate(message="参数已变化，旧预览失效；请重新生成。"):
        state["plan"], state["plan_warnings"] = None, []
        preview.setPlainText(tr("尚未生成修改预览。"))
        changes.setPlainText(tr("尚未生成修改预览。"))
        show_warnings()
        status.setText(tr(message))
        update_controls()

    def error_message(error, fallback):
        return error.message if isinstance(error, getattr(visual, "PftVisualToolsError", ())) else fallback

    def option_changed(value, source):
        other = slider if source is spin else spin
        if other.value() != value:
            other.setValue(value)
        invalidate()

    if spin is not None:
        spin.valueChanged.connect(lambda value: option_changed(value, spin))
        slider.valueChanged.connect(lambda value: option_changed(value, slider))

    def generate_preview():
        if state["busy"] or not state["inspection_ok"] or state["summary"] is not None:
            return
        invalidate("正在生成修改预览…")
        state["busy"] = True
        update_controls()
        value = spin.value() if spin is not None else None
        try:
            arguments = {"transparency": value} if action == "set_transparency" else {}
            plan = visual.prepare_appearance(app, gui, document_name, names, action, **arguments)
            if (not isinstance(plan, dict) or plan.get("document") != document_name or plan.get("action") != action
                    or type(plan.get("can_apply")) is not bool or not isinstance(plan.get("targets"), list)
                    or len(plan["targets"]) != len(names)
                    or any(not isinstance(row, dict) or "before" not in row or "after" not in row for row in plan["targets"])
                    or [row.get("Name") for row in plan["targets"]] != names
                    or not isinstance(plan.get("warnings"), (list, tuple))
                    or any(not isinstance(message, str) for message in plan["warnings"])
                    or spin is not None and (spin.value() != value or plan.get("transparency") != value)):
                raise ValueError("invalid plan")
            text = _json_text({key: value for key, value in plan.items() if key not in ("token", "seed")})
            state["plan"], state["plan_warnings"] = plan, list(plan["warnings"])
            preview.setPlainText(text)
            changes.setPlainText(format_appearance_changes(plan))
            show_warnings()
            status.setText(tr("预览已生成；请核对全部修改和注意事项后再应用。" if plan["can_apply"]
                              else "此预览不可应用，请检查诊断。"))
        except Exception as error:
            invalidate(error_message(error, "外观预览失败；未修改 CAD。"))
        finally:
            state["busy"] = False
            update_controls()

    def apply_preview():
        if state["busy"] or state["plan"] is None or not state["plan"].get("can_apply") or state["summary"] is not None:
            return
        plan = state["plan"]
        state["busy"] = True
        status.setText(tr("正在应用修改…"))
        update_controls()
        try:
            summary = visual.apply_appearance(app, gui, plan)
            if not isinstance(summary, dict):
                invalidate("外观应用结果无法确认，请检查当前外观与撤销记录；未自动保存 CAD。")
                return
            state["summary"] = {**summary, "warnings": list(all_warnings())}
            dialog.accept()
        except Exception as error:
            invalidate(error_message(error, "外观应用失败；请检查当前外观与撤销记录，未自动保存 CAD。"))
        finally:
            state["busy"] = False
            update_controls()

    update_controls()
    try:
        visual = _visual_tools()
        if action not in ("random_color", "set_transparency"):
            raise ValueError("invalid action")
        inspection = visual.inspect_appearance(app, gui, document_name, names)
        diagnostics.setPlainText(_json_text(inspection))
        summary.setPlainText(format_current_transparency(inspection))
        if (not isinstance(inspection, dict) or inspection.get("document") != document_name
                or not isinstance(inspection.get("rows"), list) or not inspection["rows"]
                or not isinstance(inspection.get("warnings"), (list, tuple))
                or any(not isinstance(message, str) for message in inspection["warnings"])):
            raise ValueError("invalid inspection")
        rows = inspection["rows"]
        state["inspection_warnings"] = list(inspection["warnings"])
        state["inspection_ok"] = (len(rows) == len(names)
            and all(isinstance(row, dict) and row.get("status") == "supported" for row in rows)
            and [row.get("Name") for row in rows] == names)
        show_warnings()
        if not state["inspection_ok"]:
            status.setText(tr("此预览不可应用，请检查诊断。"))
    except Exception as error:
        state["inspection_ok"] = False
        status.setText(tr(error_message(error, "外观检查失败；未修改 CAD。")))
    update_controls()
    generate.clicked.connect(lambda _checked=False: generate_preview())
    buttons.accepted.connect(apply_preview)
    buttons.rejected.connect(dialog.reject)
    execute = getattr(dialog, "exec", None) or dialog.exec_
    try:
        execute()
        return state["summary"]
    finally:
        release = getattr(dialog, "deleteLater", None)
        if callable(release):
            try:
                release()
            except (RuntimeError, ReferenceError):
                pass  # The parent may already have destroyed its native QObject.
