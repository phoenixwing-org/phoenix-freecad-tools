# SPDX-License-Identifier: Apache-2.0
"""Controller: validate selection and document state, then apply confirmed edits in an undoable transaction."""

from contextlib import contextmanager
import threading

from pft_workbench_model import (
    BOM_FIELDS, BOM_GROUP, PftToolError, _property_snapshot, _ensure_writable,
    _snapshot, _is_link_target, _direct_link_source, build_rename_preview, build_link_label_preview, recommend_property_values,
    validate_final_rename_labels, validate_final_link_labels,
)


def _ensure_idle(app, gui, document):
    if app.ActiveDocument is not document:
        raise PftToolError("活动文档已变化，请重新选择并打开工具。")
    transaction = getattr(app, "getActiveTransaction", None)
    if not callable(transaction):
        raise PftToolError("当前 FreeCAD 无法检查事务状态，未修改对象。")
    try:
        active_transaction = transaction()
    except Exception:
        raise PftWriteError("PART_TRANSACTION_STATE_UNAVAILABLE", "无法读取当前事务状态，未执行新的写入或关闭操作。") from None
    if active_transaction is not None:
        raise PftToolError("当前已有未完成事务，请先完成或取消正在进行的操作。")
    try:
        pending = document.HasPendingTransaction
    except Exception:
        raise PftWriteError("PART_TRANSACTION_STATE_UNAVAILABLE", "无法读取当前文档事务状态，未执行新的写入或关闭操作。") from None
    if type(pending) is not bool:
        raise PftWriteError("PART_TRANSACTION_STATE_UNAVAILABLE", "当前文档事务状态格式不受支持，未执行新的写入或关闭操作。")
    if pending:
        raise PftToolError("当前文档已有未完成事务，请先完成或取消正在进行的操作。")
    try:
        gui_document = gui.getDocument(document.Name)
        edit_reader = getattr(gui_document, "getInEdit")
        if not callable(edit_reader):
            raise TypeError("native edit state check unavailable")
        editing = edit_reader()
    except Exception:
        raise PftToolError("无法读取当前文档编辑状态，未修改对象。") from None
    # Native getInEdit() returns an editing object or exactly None.  Falsey
    # non-None values do not establish that the GUI document is idle.
    if editing is not None:
        raise PftToolError("当前文档正在编辑，请先完成或取消编辑。")
    try:
        undo_mode = document.UndoMode
    except Exception:
        raise PftToolError("无法读取当前文档撤销状态，未修改对象。") from None
    if type(undo_mode) is not int or undo_mode < 0:
        raise PftToolError("当前文档撤销状态格式不受支持，未修改对象。")
    if undo_mode == 0:
        raise PftToolError("当前文档已禁用撤销，请先启用撤销后再使用本工具。")


def _selected_context(app, gui):
    document = app.ActiveDocument
    if document is None:
        raise PftToolError("请先打开文档，并在树中选中要处理的对象。")
    objects = list(gui.Selection.getSelection())
    if not objects:
        raise PftToolError("请先在树中选中对象；本工具不会自动处理整个文档。")
    if any(obj.Document is not document for obj in objects):
        raise PftToolError("请只选择当前活动文档中的对象。")
    for selected in gui.Selection.getSelectionEx():
        if getattr(selected, "SubElementNames", ()):
            raise PftToolError("请选择树中的完整对象，不要选择面、边或顶点。")
    _ensure_idle(app, gui, document)
    seen = set()
    objects = [obj for obj in objects if not (obj.Name in seen or seen.add(obj.Name))]
    return document, objects


def _ensure_snapshot(document, objects, snapshots):
    for obj, before in zip(objects, snapshots):
        if document.getObject(before["name"]) is not obj or _snapshot(obj) != before:
            raise PftToolError("对象或属性在预览后发生变化，请关闭窗口后重新预览。")


class PftWriteError(PftToolError):
    """Fixed, public errors for uncertain native writes and transaction closure."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class PftPreferenceRestoreError(PftWriteError):
    """The preference restoration phase ended; callers must not late-abort."""


def _valid_transaction_id(value):
    if (isinstance(value, (tuple, list)) and len(value) == 2
            and isinstance(value[0], str) and type(value[1]) is int and value[1] > 0):
        return value[1]
    return None


class _PftOwnedTransaction:
    """Own only the ID opened here; never infer ownership from a title.

    An unchanged ID is a closure guard, not proof that callbacks or observers
    made no other edits inside this global transaction. No events are pumped.
    """
    def __init__(self, app, document, verify_rollback=None, verify_commit=None):
        self.app, self.document = app, document
        self.verify_rollback = verify_rollback
        self.verify_commit = verify_commit
        self.transaction_id = None
        self.abort_attempted = False

    def _read(self):
        getter = getattr(self.app, "getActiveTransaction", None)
        if not callable(getter):
            raise RuntimeError("transaction getter unavailable")
        return getter()

    def open(self, title):
        _ensure_main_thread()
        try:
            current = self._read()
        except Exception:
            raise PftWriteError("PART_TRANSACTION_STATE_UNAVAILABLE", "无法读取当前事务状态，未执行新的写入或关闭操作。") from None
        if current is not None:
            raise PftWriteError("PART_TRANSACTION_CHANGED", "工具事务归属已变化或无法确认，未提交或取消其他操作。")
        try:
            self.document.openTransaction(title)
            transaction_id = _valid_transaction_id(self._read())
            if transaction_id is None:
                raise RuntimeError("opened transaction identity unavailable")
        except Exception:
            # Native open may already have changed state; no known ID means
            # neither abort nor another open is authorized.
            raise PftWriteError("PART_TRANSACTION_OPEN_UNVERIFIED", "工具事务开启结果无法确认；未写入对象或尝试关闭未知事务，请检查当前事务。") from None
        self.transaction_id = transaction_id

    def check(self):
        try:
            owned = self.transaction_id is not None and _valid_transaction_id(self._read()) == self.transaction_id
        except Exception:
            owned = False
        if not owned:
            raise PftWriteError("PART_TRANSACTION_CHANGED", "工具事务归属已变化或无法确认，未提交或取消其他操作。")

    def abort_once(self):
        if self.abort_attempted:
            return
        # Lock the one-shot intent before inspecting ownership. A failed read
        # must not permit an outer exception handler to retry after the Link
        # preference scope has already restored DuplicateLabels.
        self.abort_attempted = True
        self.check()
        try:
            self.document.abortTransaction()
            current = self._read()
        except Exception:
            raise PftWriteError("PART_ROLLBACK_UNVERIFIED", "工具事务回滚结果无法确认，请检查当前修改与撤销记录；不会重复关闭。") from None
        if current is not None:
            current_id = _valid_transaction_id(current)
            if current_id is not None and current_id != self.transaction_id:
                raise PftWriteError("PART_TRANSACTION_CHANGED", "工具事务归属已变化或无法确认，未提交或取消其他操作。")
            raise PftWriteError("PART_ROLLBACK_UNVERIFIED", "工具事务回滚结果无法确认，请检查当前修改与撤销记录；不会重复关闭。")
        self.transaction_id = None
        if self.verify_rollback is not None:
            try:
                self.verify_rollback()
            except Exception:
                raise PftWriteError("PART_ROLLBACK_UNVERIFIED", "工具事务回滚结果无法确认，请检查当前修改与撤销记录；不会重复关闭。") from None

    def commit(self):
        self.check()
        try:
            self.document.commitTransaction()
            closed = self._read() is None
        except Exception:
            closed = False
        if not closed:
            # A raising commit may already have committed. Re-closing or aborting
            # is unsafe even if a later read happens to show the old ID.
            raise PftWriteError("PART_COMMIT_UNVERIFIED", "工具事务提交关闭无法确认，请检查当前修改与撤销记录；不会再次提交或回滚。") from None
        self.transaction_id = None
        if self.verify_commit is not None:
            try:
                self.verify_commit()
            except Exception:
                # The native transaction has already closed. Target verification
                # failure cannot justify aborting or committing anything else.
                raise PftWriteError("PART_COMMIT_UNVERIFIED", "工具事务提交后目标状态与预览不一致或无法验证，请检查当前修改与撤销记录；不会再次提交或回滚。") from None


def _run_transaction(app, document, title, write, verify_rollback=None, verify_commit=None):
    owner = _PftOwnedTransaction(app, document, verify_rollback, verify_commit)
    owner.open(title)
    try:
        write(owner.check)
        owner.check()
    except Exception:
        owner.abort_once()
        raise
    # Commit uncertainty must not enter the write-failure abort path.
    owner.commit()


def _verify_label_targets(rows):
    for row in rows:
        if row["object"].Label != row["new_label"]:
            raise PftToolError("FreeCAD 自动调整了预览中的 Label，可能受标签唯一性限制；本次事务已取消，未完成修改。")


def _write_labels_exact(rows, check_owner):
    """Reject native auto-numbering instead of reporting a label change that did not happen."""
    for row in rows:
        check_owner()
        _ensure_writable(row["object"], "Label")
        check_owner()
        setattr(row["object"], "Label", row["new_label"])
        check_owner()
    _verify_label_targets(rows)


def _ensure_main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise PftToolError("本工具必须从 FreeCAD 主界面线程执行。")


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
    rollback_unverified = False
    try:
        if original is not True:
            prefs.SetBool(key, True)
        yield
    except Exception:
        if rollback_on_failure is not None:
            try:
                rollback_on_failure()
            except Exception:
                rollback_unverified = True
                raise
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
        except Exception:
            # Restoration may already have taken effect before its readback
            # failed. Never let an outer handler abort after this phase, and
            # never hide an earlier rollback failure behind a generic error.
            if rollback_unverified:
                raise PftPreferenceRestoreError("PART_ROLLBACK_AND_PREFERENCE_UNVERIFIED", "工具事务回滚与临时重复 Label 设置恢复均无法确认；未再次关闭事务，请检查当前修改、撤销记录和文档首选项。") from None
            raise PftPreferenceRestoreError("PART_PREFERENCE_RESTORE_UNVERIFIED", "临时重复 Label 设置恢复无法确认；未继续提交或关闭事务，请检查当前修改、事务和文档首选项。") from None


def apply_rename(app, gui, document, objects, snapshots, rows):
    _ensure_idle(app, gui, document)
    _ensure_snapshot(document, objects, snapshots)
    fresh = build_rename_preview(document, objects, app.listDocuments().values())
    expected = [(row["name"], row["old_label"], row["new_label"], row["error"]) for row in rows]
    actual = [(row["name"], row["old_label"], row["new_label"], row["error"]) for row in fresh]
    if expected != actual or any(row["error"] for row in fresh):
        raise PftToolError("命名预览已失效或存在冲突，请重新预览。")
    changed = [row for row in fresh if row["old_label"] != row["new_label"]]
    if changed:
        def write(check_owner):
            _write_labels_exact(changed, check_owner)
            validate_final_rename_labels(document, fresh)
        _run_transaction(app, document, "Phoenix：修正所选对象命名", write,
                         lambda: _ensure_snapshot(document, objects, snapshots),
                         lambda: validate_final_rename_labels(document, fresh))
    return len(changed)


def _reject_link_attributes(objects):
    if any(_is_link_target(obj) for obj in objects):
        raise PftToolError("请到链接的源节点填写零件属性；本工具不向 App::Link 转发写入属性。")


def build_property_preview(app, gui, document, objects, snapshots, selected_fields, values, initialize_only=False):
    """Validate and describe every selected field before the view asks to confirm."""
    _ensure_idle(app, gui, document)
    _reject_link_attributes(objects)
    _ensure_snapshot(document, objects, snapshots)
    allowed = {field for field, _ in BOM_FIELDS}
    if not selected_fields or set(selected_fields) - allowed:
        raise PftToolError("请至少勾选一个支持的字段。")
    rows = []
    for obj in objects:
        for field in selected_fields:
            current = _property_snapshot(obj, field)
            value = current["value"] if initialize_only and current["exists"] else "" if initialize_only else values[obj.Name][field]
            changed = not current["exists"] or current["value"] != value
            if not initialize_only or not current["exists"]:
                if not isinstance(value, str) or any(ord(char) < 32 for char in value):
                    raise PftToolError("属性必须是单行文本，不能含控制字符。")
                _ensure_writable(obj, field, may_add=True)
            rows.append({"name": obj.Name, "field": field, "old_value": current["value"], "new_value": value,
                         "missing": not current["exists"], "changed": changed, "object": obj,
                         "expected_type": current["type"] if current["exists"] else "App::PropertyString"})
    return rows


def apply_properties(app, gui, document, objects, snapshots, selected_fields, values, initialize_only=False):
    rows = build_property_preview(app, gui, document, objects, snapshots, selected_fields, values, initialize_only)
    changed = [row for row in rows if row["changed"]]
    descriptions = dict(BOM_FIELDS)
    def verify_values():
        # Include unchanged checked rows: a native setter or commit callback can
        # alter another row without raising or returning the requested value.
        for row in rows:
            try:
                obj = row["object"]
                current = _property_snapshot(obj, row["field"])
                valid = (document.getObject(row["name"]) is obj and obj.Document is document
                         and current["exists"] and current["type"] == row["expected_type"]
                         and current["value"] == row["new_value"])
            except Exception:
                valid = False
            if not valid:
                raise PftWriteError("PART_PROPERTY_VERIFY_FAILED", "写入后的对象、属性类型或值与预览不一致，未提交修改。")
    def write(check_owner):
        for row in changed:
            check_owner()
            obj, field = row["object"], row["field"]
            if row["missing"]:
                obj.addProperty("App::PropertyString", field, BOM_GROUP, descriptions[field])
                check_owner()
                try:
                    current = _property_snapshot(obj, field)
                    created = current["exists"] and current["type"] == row["expected_type"]
                except Exception:
                    created = False
                if not created:
                    raise PftWriteError("PART_PROPERTY_VERIFY_FAILED", "新增属性的存在或类型与预览不一致，未写入请求值。")
            _ensure_writable(obj, field)
            check_owner()
            setattr(obj, field, row["new_value"])
            check_owner()
        verify_values()
        check_owner()
    if changed:
        _run_transaction(app, document, "Phoenix：填写所选零件属性", write,
                         lambda: _ensure_snapshot(document, objects, snapshots), verify_values)
    return len(changed)


def _run_link_label_transaction(app, document, rows, changed, verify_rollback):
    """Abort only our ID before restoring the exact temporary preference."""
    def verify_commit():
        _verify_label_targets(rows)
        validate_final_link_labels(document, rows)
    owner = _PftOwnedTransaction(app, document, verify_rollback, verify_commit)
    owner.open("Phoenix：同步所选链接名称")
    try:
        with _temporary_duplicate_link_labels(app, rollback_on_failure=owner.abort_once):
            _write_labels_exact(changed, owner.check)
            validate_final_link_labels(document, rows)
            owner.check()
    except PftPreferenceRestoreError:
        raise
    except Exception:
        owner.abort_once()
        if owner.transaction_id is None:
            # The inner abort checked the original state before the preference
            # finally block ran. Its restoration callbacks can change the Link
            # or source afterwards; recheck only, never close another edit.
            try:
                verify_rollback()
            except Exception:
                raise PftWriteError("PART_ROLLBACK_UNVERIFIED", "工具事务回滚结果无法确认，请检查当前修改与撤销记录；不会重复关闭。") from None
        raise
    # Preference restoration has ended. Even a newly available ownership read
    # cannot authorize an abort here, after duplicate labels were disabled.
    try:
        verify_commit()
        owner.check()
    except Exception:
        raise PftWriteError("PART_COMMIT_UNVERIFIED", "偏好恢复后目标或事务状态与预览不一致或无法验证，未继续提交或关闭事务，请检查当前修改与撤销记录。") from None
    owner.commit()


def apply_link_labels(app, gui, document, objects, snapshots, rows):
    _ensure_main_thread()
    _ensure_idle(app, gui, document)
    _ensure_snapshot(document, objects, snapshots)
    fresh = build_link_label_preview(document, objects, app.listDocuments().values())
    def signature(preview):
        return [(row["name"], row["old_label"], row["new_label"], row["source_identity"], row["source_object"], row["subnames"], row["error"]) for row in preview]
    if signature(rows) != signature(fresh) or any(row["error"] for row in fresh):
        raise PftToolError("链接预览已失效或存在错误，请重新预览。")
    changed = [row for row in fresh if row["old_label"] != row["new_label"]]
    if changed:
        sources = []
        for row in fresh:
            source, identity, subnames = _direct_link_source(row["object"])
            sources.append((row["object"], source, source.Document, identity, subnames, source.Label))
        def verify_rollback():
            _ensure_snapshot(document, objects, snapshots)
            for link, source, source_document, identity, subnames, label in sources:
                current, current_identity, current_subnames = _direct_link_source(link)
                if (current is not source or current.Document is not source_document
                        or app.listDocuments().get(source_document.Name) is not source_document
                        or current_identity != identity or current_subnames != subnames or current.Label != label):
                    raise PftToolError("链接来源在回滚后与原状态不一致。")
        _run_link_label_transaction(app, document, fresh, changed, verify_rollback)
    return len(changed)


def prepare_context(app, gui):
    """Capture the selected objects and their initial values without changing them."""
    document, objects = _selected_context(app, gui)
    return document, objects, [_snapshot(obj) for obj in objects]


def _live_target(document, obj):
    try:
        return obj.Document is document and bool(obj.Name) and document.getObject(obj.Name) is obj
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def _target_classification(obj, operation, body_members):
    """Read type/property metadata only; distinguish selectable from recommended."""
    kind = getattr(obj, "TypeId", "")
    if operation == "link_labels":
        return (kind == "App::Link", kind == "App::Link")
    if operation == "properties" and _is_link_target(obj):
        return False, False
    auxiliary = kind.startswith(("TechDraw::", "App::Origin")) or kind in {
        "PartDesign::Plane", "PartDesign::Line", "PartDesign::Point", "PartDesign::CoordinateSystem",
    } or "Datum" in kind
    if auxiliary:
        return False, False
    if kind in {"PartDesign::Body", "App::Part"}:
        return True, True
    properties = getattr(obj, "PropertiesList", ())
    has_bom = any(field in properties for field, _ in BOM_FIELDS)
    derived = getattr(obj, "isDerivedFrom", None)
    geometry = bool(derived("Part::Feature")) if callable(derived) else kind.startswith(("Part::", "PartDesign::", "Sketcher::"))
    selectable = has_bom or geometry or operation == "rename" and _is_link_target(obj)
    # Internal features can be explicitly checked, but do not become a batch
    # default merely because they derive from Part::Feature or carry BOM data.
    internal = id(obj) in body_members or kind.startswith(("PartDesign::", "Sketcher::"))
    preferred = selectable and not internal and not _is_link_target(obj) and (kind == "Part::Feature" or has_bom)
    return bool(selectable), bool(preferred)


def _raw_target_selection(gui, document):
    """Return live whole objects plus whether any raw selection was present.

    An unsuitable selection is an explicit scope choice, so it must not be
    mistaken for no selection and replaced by recommended document objects.
    """
    selection = gui.Selection
    extended = getattr(selection, "getSelectionEx", None)
    records = []
    if callable(extended):
        try:
            records = list(extended("", 0))
        except TypeError:
            records = list(extended())  # Lightweight/older test adapters.
    if records:
        objects = []
        for record in records:
            if getattr(record, "SubElementNames", ()):
                continue
            obj = getattr(record, "Object", None)
            if obj is None:
                name = getattr(record, "ObjectName", "")
                doc_name = getattr(record, "DocumentName", document.Name)
                if name and doc_name == document.Name:
                    obj = document.getObject(name)
            if _live_target(document, obj):
                objects.append(obj)
        return objects, True
    plain = getattr(selection, "getSelection", None)
    if not callable(plain):
        return [], False
    try:
        objects = list(plain("", False))
    except TypeError:
        objects = list(plain())
    return [obj for obj in objects if _live_target(document, obj)], bool(objects)


def prepare_target_options(app, gui, operation):
    """Return current-document candidates and explicit, read-only default names.

    Picker policy never changes tree selection. It does not replace the strict
    legacy context or the object/snapshot guards used by MCP and apply methods.
    """
    if operation not in {"rename", "properties", "link_labels"}:
        raise PftToolError("不支持的目标选择操作。")
    _ensure_main_thread()
    document = app.ActiveDocument
    if document is None:
        raise PftToolError("请先打开要处理的文档。")
    _ensure_idle(app, gui, document)
    objects = [obj for obj in document.Objects if _live_target(document, obj)]
    body_members = {id(member) for obj in objects if getattr(obj, "TypeId", "") == "PartDesign::Body"
                    for member in getattr(obj, "Group", ())}
    candidates, recommended = [], []
    for obj in objects:
        selectable, preferred = _target_classification(obj, operation, body_members)
        if selectable:
            candidates.append(obj)
        if preferred:
            recommended.append(obj.Name)
    selected = []
    seen = set()
    raw_objects, had_selection = _raw_target_selection(gui, document)
    for obj in raw_objects:
        if any(obj is candidate for candidate in candidates) and obj.Name not in seen:
            selected.append(obj.Name)
            seen.add(obj.Name)
    return document, candidates, selected if had_selection else recommended


def _explicit_target_context(app, gui, document, objects):
    """Validate caller-picked references without reading or changing tree selection."""
    _ensure_main_thread()
    if document is None or objects is None:
        raise PftToolError("请同时明确指定文档和处理对象。")
    _ensure_idle(app, gui, document)
    objects = list(objects)
    if not objects:
        raise PftToolError("请至少勾选一个处理对象。")
    if any(not _live_target(document, obj) for obj in objects):
        raise PftToolError("处理对象不属于当前文档或已失效，请重新打开工具。")
    seen = set()
    objects = [obj for obj in objects if not (obj.Name in seen or seen.add(obj.Name))]
    return document, objects


def prepare_rename_context(app, gui, *, document=None, objects=None):
    """Prepare a read-only naming preview for the view to render."""
    if document is None and objects is None:
        document, objects, snapshots = prepare_context(app, gui)
    else:
        document, objects = _explicit_target_context(app, gui, document, objects)
        snapshots = [_snapshot(obj) for obj in objects]
    rows = build_rename_preview(document, objects, app.listDocuments().values())
    return document, objects, snapshots, rows


def prepare_properties_context(app, gui, *, document=None, objects=None):
    """Refuse Link forwarding before showing a property editor."""
    if document is None and objects is None:
        document, objects = _selected_context(app, gui)
    else:
        document, objects = _explicit_target_context(app, gui, document, objects)
    _reject_link_attributes(objects)
    return document, objects, [_snapshot(obj) for obj in objects]


def prepare_link_context(app, gui, *, document=None, objects=None):
    """Prepare selected Link instances and their direct-source display names."""
    if document is None and objects is None:
        document, objects, snapshots = prepare_context(app, gui)
    else:
        document, objects = _explicit_target_context(app, gui, document, objects)
        snapshots = [_snapshot(obj) for obj in objects]
    rows = build_link_label_preview(document, objects, app.listDocuments().values())
    return document, objects, snapshots, rows
