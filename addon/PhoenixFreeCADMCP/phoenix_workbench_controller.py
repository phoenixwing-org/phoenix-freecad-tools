# SPDX-License-Identifier: Apache-2.0
"""Controller: validate selection and document state, then apply confirmed edits in an undoable transaction."""

from contextlib import contextmanager
import threading

from phoenix_workbench_model import (
    BOM_FIELDS, BOM_GROUP, ToolError, _property_snapshot, _ensure_writable,
    _snapshot, build_rename_preview, build_link_label_preview, recommend_property_values, validate_final_link_labels,
)


def _ensure_idle(app, gui, document):
    if app.ActiveDocument is not document:
        raise ToolError("活动文档已变化，请重新选择并打开工具。")
    transaction = getattr(app, "getActiveTransaction", None)
    if not callable(transaction):
        raise ToolError("当前 FreeCAD 无法检查事务状态，未修改对象。")
    if transaction():
        raise ToolError("当前已有未完成事务，请先完成或取消正在进行的操作。")
    gui_document = gui.getDocument(document.Name)
    if gui_document is not None and gui_document.getInEdit():
        raise ToolError("当前文档正在编辑，请先完成或取消编辑。")
    if getattr(document, "UndoMode", 1) == 0:
        raise ToolError("当前文档已禁用撤销，请先启用撤销后再使用本工具。")


def _selected_context(app, gui):
    document = app.ActiveDocument
    if document is None:
        raise ToolError("请先打开文档，并在树中选中要处理的对象。")
    objects = list(gui.Selection.getSelection())
    if not objects:
        raise ToolError("请先在树中选中对象；本工具不会自动处理整个文档。")
    if any(obj.Document is not document for obj in objects):
        raise ToolError("请只选择当前活动文档中的对象。")
    for selected in gui.Selection.getSelectionEx():
        if getattr(selected, "SubElementNames", ()):
            raise ToolError("请选择树中的完整对象，不要选择面、边或顶点。")
    _ensure_idle(app, gui, document)
    seen = set()
    objects = [obj for obj in objects if not (obj.Name in seen or seen.add(obj.Name))]
    return document, objects


def _ensure_snapshot(document, objects, snapshots):
    for obj, before in zip(objects, snapshots):
        if document.getObject(before["name"]) is not obj or _snapshot(obj) != before:
            raise ToolError("对象或属性在预览后发生变化，请关闭窗口后重新预览。")


def _run_transaction(document, title, write):
    document.openTransaction(title)
    try:
        write()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise


def _verify_label_targets(rows):
    for row in rows:
        if row["object"].Label != row["new_label"]:
            raise ToolError("FreeCAD 自动调整了预览中的 Label，可能受标签唯一性限制；本次事务已取消，未完成修改。")


def _write_labels_exact(rows):
    """Reject native auto-numbering instead of reporting a label change that did not happen."""
    for row in rows:
        setattr(row["object"], "Label", row["new_label"])
    _verify_label_targets(rows)


def _ensure_main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise ToolError("链接名称同步必须从 FreeCAD 主界面线程执行。")


@contextmanager
def _temporary_duplicate_link_labels(app, rollback_on_failure=None):
    """Use normal Label assignment while restoring the exact DuplicateLabels preference.

    FreeCAD 1.1 DocumentObject::onProposedLabelChange consults this bool;
    ParamGet.GetBools/RemBool preserve even the absence of its original key.
    This synchronous GUI-thread scope never processes events or saves settings.
    """
    _ensure_main_thread()
    key = "DuplicateLabels"
    prefs = app.ParamGet("User parameter:BaseApp/Preferences/Document")
    existed = key in prefs.GetBools()
    original = prefs.GetBool(key) if existed else None
    try:
        if original is not True:
            prefs.SetBool(key, True)
        yield
    except Exception:
        if rollback_on_failure is not None:
            rollback_on_failure()
        raise
    finally:
        try:
            present = key in prefs.GetBools()
            if existed:
                if not present or prefs.GetBool(key) != original:
                    prefs.SetBool(key, original)
            elif present:
                prefs.RemBool(key)
            restored = key in prefs.GetBools()
            if restored != existed or existed and prefs.GetBool(key) != original:
                raise RuntimeError("DuplicateLabels was not restored")
        except Exception as error:
            raise ToolError("临时重复 Label 设置未能恢复；本次事务已取消，请检查文档首选项。") from error


def apply_rename(app, gui, document, objects, snapshots, rows):
    _ensure_idle(app, gui, document)
    _ensure_snapshot(document, objects, snapshots)
    fresh = build_rename_preview(document, objects, app.listDocuments().values())
    expected = [(row["name"], row["old_label"], row["new_label"], row["error"]) for row in rows]
    actual = [(row["name"], row["old_label"], row["new_label"], row["error"]) for row in fresh]
    if expected != actual or any(row["error"] for row in fresh):
        raise ToolError("命名预览已失效或存在冲突，请重新预览。")
    changed = [row for row in fresh if row["old_label"] != row["new_label"]]
    if changed:
        _run_transaction(document, "Phoenix：修正所选对象命名", lambda: _write_labels_exact(changed))
    return len(changed)


def _reject_link_attributes(objects):
    if any(getattr(obj, "TypeId", "") == "App::Link" for obj in objects):
        raise ToolError("请到链接的源节点填写零件属性；本工具不向 App::Link 转发写入属性。")


def build_property_preview(app, gui, document, objects, snapshots, selected_fields, values, initialize_only=False):
    """Validate and describe every selected field before the view asks to confirm."""
    _ensure_idle(app, gui, document)
    _reject_link_attributes(objects)
    _ensure_snapshot(document, objects, snapshots)
    allowed = {field for field, _ in BOM_FIELDS}
    if not selected_fields or set(selected_fields) - allowed:
        raise ToolError("请至少勾选一个支持的字段。")
    rows = []
    for obj in objects:
        for field in selected_fields:
            current = _property_snapshot(obj, field)
            value = current["value"] if initialize_only and current["exists"] else "" if initialize_only else values[obj.Name][field]
            changed = not current["exists"] or current["value"] != value
            if not initialize_only or not current["exists"]:
                if not isinstance(value, str) or any(ord(char) < 32 for char in value):
                    raise ToolError("属性必须是单行文本，不能含控制字符。")
                _ensure_writable(obj, field, may_add=True)
            rows.append({"name": obj.Name, "field": field, "old_value": current["value"], "new_value": value,
                         "missing": not current["exists"], "changed": changed, "object": obj})
    return rows


def apply_properties(app, gui, document, objects, snapshots, selected_fields, values, initialize_only=False):
    rows = build_property_preview(app, gui, document, objects, snapshots, selected_fields, values, initialize_only)
    changed = [row for row in rows if row["changed"]]
    descriptions = dict(BOM_FIELDS)
    def write():
        for row in changed:
            obj, field = row["object"], row["field"]
            if row["missing"]:
                obj.addProperty("App::PropertyString", field, BOM_GROUP, descriptions[field])
            setattr(obj, field, row["new_value"])
    if changed:
        _run_transaction(document, "Phoenix：填写所选零件属性", write)
    return len(changed)


def _run_link_label_transaction(app, document, rows, changed):
    """Validate while duplicates are allowed; rollback failures before restoring preferences."""
    aborted = [False]
    def abort_once():
        if not aborted[0]:
            aborted[0] = True
            document.abortTransaction()
    document.openTransaction("Phoenix：同步所选链接名称")
    try:
        with _temporary_duplicate_link_labels(app, rollback_on_failure=abort_once):
            _write_labels_exact(changed)
            validate_final_link_labels(document, rows)
        _verify_label_targets(rows)
        document.commitTransaction()
    except Exception:
        abort_once()
        raise


def apply_link_labels(app, gui, document, objects, snapshots, rows):
    _ensure_main_thread()
    _ensure_idle(app, gui, document)
    _ensure_snapshot(document, objects, snapshots)
    fresh = build_link_label_preview(document, objects, app.listDocuments().values())
    def signature(preview):
        return [(row["name"], row["old_label"], row["new_label"], row["source_identity"], row["source_object"], row["subnames"], row["error"]) for row in preview]
    if signature(rows) != signature(fresh) or any(row["error"] for row in fresh):
        raise ToolError("链接预览已失效或存在错误，请重新预览。")
    changed = [row for row in fresh if row["old_label"] != row["new_label"]]
    if changed:
        _run_link_label_transaction(app, document, fresh, changed)
    return len(changed)


def prepare_context(app, gui):
    """Capture the selected objects and their initial values without changing them."""
    document, objects = _selected_context(app, gui)
    return document, objects, [_snapshot(obj) for obj in objects]


def prepare_rename_context(app, gui):
    """Prepare a read-only naming preview for the view to render."""
    document, objects, snapshots = prepare_context(app, gui)
    rows = build_rename_preview(document, objects, app.listDocuments().values())
    return document, objects, snapshots, rows


def prepare_properties_context(app, gui):
    """Refuse Link forwarding before showing a property editor."""
    document, objects = _selected_context(app, gui)
    _reject_link_attributes(objects)
    return document, objects, [_snapshot(obj) for obj in objects]


def prepare_link_context(app, gui):
    """Prepare selected Link instances and their direct-source display names."""
    document, objects, snapshots = prepare_context(app, gui)
    rows = build_link_label_preview(document, objects, app.listDocuments().values())
    return document, objects, snapshots, rows
