# SPDX-License-Identifier: Apache-2.0
"""Bounded, one-use preview plans for the existing Phoenix part controllers.

Imports are GUI-independent. The bridge dispatches perform() on FreeCAD's GUI
thread; this module never changes selection, saves files, or runs arbitrary code.
"""

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import secrets
import sys
import threading
import time
import unicodedata

METHODS = frozenset({"prepare_part_changes", "apply_part_changes", "request_part_change_approval", "get_part_change_status"})
OPERATIONS = frozenset({"normalize_names", "sync_link_labels", "initialize_properties", "recommend_properties", "set_properties", "set_pad_length"})
FIELDS = ("PartNumber", "PartVersion", "TypeCode", "ModelSeries", "PartName")
MAX_PLANS = 32
TTL_SECONDS = 300
MAX_TEXT = 2048
MAX_PREVIEW_BYTES = 128 * 1024
MAX_STATUS_BYTES = 4096
PAD_LENGTH_CAPABILITIES = {"version": 1, "operation": "set_pad_length", "confirmation": "native_gui_only",
    "preview_method": "prepare_part_changes", "request_method": "request_part_change_approval",
    "status_method": "get_part_change_status", "length_unit": "mm", "minimum_mm": 0.01, "maximum_mm": 10000,
    "model_scope": "single_document_first_pad", "max_open_documents": 1, "saves_document": False}
APPROVAL_CAPABILITIES = {"version": 1, "request_method": "request_part_change_approval",
    "status_method": "get_part_change_status", "confirmation": "native_gui_only", "max_pending": 1,
    "storage": "memory_only", "ttl_seconds": TTL_SECONDS, "max_status_bytes": MAX_STATUS_BYTES}
_FIXED_ERRORS = frozenset({"PART_CHANGE_FAILED", "PART_APPROVAL_UI_UNAVAILABLE", "PART_APPROVAL_NO_CHANGES",
    "PART_PREVIEW_BLOCKED", "PART_PLAN_STALE", "DOCUMENT_NOT_FOUND", "DOCUMENT_NOT_ACTIVE", "GUI_THREAD_REQUIRED",
    "PART_TRANSACTION_STATE_UNAVAILABLE", "PART_TRANSACTION_OPEN_UNVERIFIED", "PART_TRANSACTION_CHANGED",
    "PART_COMMIT_UNVERIFIED", "PART_ROLLBACK_UNVERIFIED", "PART_PROPERTY_VERIFY_FAILED",
    "PART_PREFERENCE_RESTORE_UNVERIFIED", "PART_ROLLBACK_AND_PREFERENCE_UNVERIFIED", "PAD_UNSUPPORTED", "PAD_PLAN_STALE",
    "PAD_PARAMETER_VERIFY_FAILED", "PAD_GEOMETRY_VERIFY_FAILED", "PAD_RECOMPUTE_UNVERIFIED", "PAD_NATIVE_CONFIRMATION_REQUIRED"})
_approval_epoch = 0
_approvals_enabled = False
_plans = OrderedDict()
_lock = threading.RLock()
_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")
_PRIVATE = re.compile(r"[A-Za-z]:[\\/]|\\\\|/(?:Users|Volumes|Applications|private|home|tmp|etc|opt|usr|var|mnt)/|file://|<\s*(?:svg|\?xml|!DOCTYPE)\b", re.I)


def _valid_name(value):
    return (isinstance(value, str) and 0 < len(value) <= 256
            and not any(unicodedata.category(char) in {"Cc", "Cs"} for char in value))


def _private_text(value):
    if _PRIVATE.search(value):
        return True
    # Absolute paths can follow Chinese punctuation as well as ASCII quotes.
    return any(char == "/" and (i == 0 or value[i - 1].isspace()
               or unicodedata.category(value[i - 1]).startswith("P")) for i, char in enumerate(value))


def validate(method, params, Error):
    if not isinstance(method, str) or method not in METHODS:
        return False
    if not isinstance(params, dict):
        raise Error("INVALID_PARAMS", "参数必须是对象。")
    if method in {"apply_part_changes", "request_part_change_approval", "get_part_change_status"}:
        if set(params) != {"token"} or not isinstance(params["token"], str) or not _TOKEN.fullmatch(params["token"]):
            raise Error("INVALID_PARAMS", "该操作仅接受预览返回的 token。")
        return True
    if set(params) - {"docName", "objectNames", "operation", "values", "bodyName", "lengthMm"}:
        raise Error("INVALID_PARAMS", "预览参数包含未开放字段。")
    if not _valid_name(params.get("docName")):
        raise Error("INVALID_PARAMS", "docName 必须是已打开文档的内部名称。")
    names = params.get("objectNames")
    if (not isinstance(names, list) or not 1 <= len(names) <= 100
            or any(not _valid_name(name) for name in names)
            or len(set(names)) != len(names)):
        raise Error("INVALID_PARAMS", "objectNames 必须含 1 至 100 个不同的对象内部名称。")
    operation = params.get("operation")
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise Error("INVALID_PARAMS", "operation 不是已开放的零件操作。")
    if operation == "set_pad_length":
        length = params.get("lengthMm")
        if (len(names) != 1 or not _valid_name(params.get("bodyName")) or "values" in params
                or type(length) not in (int, float) or not 0.01 <= length <= 10000):
            raise Error("INVALID_PARAMS", "Pad 长度预览需要一个明确Pad、Body及0.01到10000的有限mm数值。")
        return True
    if "bodyName" in params or "lengthMm" in params:
        raise Error("INVALID_PARAMS", "只有 set_pad_length 可以指定 Body 和长度。")
    if operation != "set_properties":
        if "values" in params:
            raise Error("INVALID_PARAMS", "只有 set_properties 可以提供 values。")
        return True
    values = params.get("values")
    if not isinstance(values, dict) or set(values) != set(names):
        raise Error("INVALID_PARAMS", "values 必须且只能包含每一个已指定对象。")
    field_set = None
    for fields in values.values():
        if not isinstance(fields, dict) or not fields or set(fields) - set(FIELDS):
            raise Error("INVALID_PARAMS", "仅开放五个正式 BOM 字符串字段，且每个对象至少指定一个字段。")
        if field_set is None:
            field_set = set(fields)
        elif set(fields) != field_set:
            raise Error("INVALID_PARAMS", "本批每个对象必须指定相同字段集，可使用不同的值。")
        if any(not isinstance(value, str) or len(value) > MAX_TEXT
               or any(unicodedata.category(char) in {"Cc", "Cs"} for char in value)
               or _private_text(value) for value in fields.values()):
            raise Error("INVALID_PARAMS", "属性值必须是至多 2048 字的单行文本，不能包含路径或模板源码。")
    return True


def _load_sibling(name):
    """Load MVC lazily even when the bridge imported this helper by file path."""
    filename = Path(__file__).resolve().with_name(name + ".py")
    existing = sys.modules.get(name)
    if existing is not None:
        if Path(getattr(existing, "__file__", "")).resolve() != filename:
            raise RuntimeError("MVC module belongs to a different addon installation")
        return existing
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _mvc():
    model = _load_sibling("pft_workbench_model")
    controller = _load_sibling("pft_workbench_controller")
    return model, controller


def _pad():
    module = _load_sibling("pft_pad_length")
    if getattr(module, "CAPABILITIES", None) != PAD_LENGTH_CAPABILITIES:
        raise RuntimeError("Pad design module belongs to an incompatible contract")
    return module


def _display(value):
    if value is None:
        return None, False
    if type(value) in (bool, int, float):
        value = str(value)
    if not isinstance(value, str):
        return "<非文本值>", True
    if _private_text(value):
        return "<路径或源码已隐藏>", True
    if len(value) > MAX_TEXT or any(unicodedata.category(char) in {"Cc", "Cs"} for char in value):
        return "<超长或不支持的文本>", True
    return value, False


def _reason(error):
    text, hidden = _display(str(error))
    return "当前操作不可用，请在 FreeCAD 中检查对象状态。" if hidden else text


def _document(app, name, Error):
    try:
        document = app.getDocument(name)
    except Exception:
        document = None
    if document is None:
        raise Error("DOCUMENT_NOT_FOUND", "指定文档未打开。")
    if app.ActiveDocument is not document:
        raise Error("DOCUMENT_NOT_ACTIVE", "指定文档必须已经是活动文档；预览不会切换文档。")
    return document


def _objects(document, names, Error):
    result = []
    for name in names:
        try:
            obj = document.getObject(name)
            valid = obj is not None and obj.Document is document and obj.Name == name
        except Exception:
            valid = False
        if not valid:
            raise Error("OBJECT_NOT_FOUND", "指定对象不在当前文档中。")
        result.append(obj)
    return result


def _link_states(model, objects):
    result = []
    for obj in objects:
        if getattr(obj, "TypeId", "") == "App::Link":
            source, identity, subnames = model._direct_link_source(obj)
            result.append((obj, source, source.Document, identity, subnames, source.Label))
    return result


def _public_rows(rows, labels=False):
    result = []
    for row in rows:
        old, old_hidden = _display(row["old_label"] if labels else None if row["missing"] else row["old_value"])
        new, new_hidden = _display(row["new_label"] if labels else row["new_value"])
        error = row.get("error", "")
        status = "blocked" if error or old_hidden or new_hidden else "change" if (row["old_label"] != row["new_label"] if labels else row["changed"]) else "unchanged"
        item = {"object": row["name"], "field": "Label" if labels else row["field"], "old": old, "new": new, "status": status}
        if error:
            item["reason"] = _reason(error)
        elif old_hidden or new_hidden:
            item["reason"] = "值无法完整、安全地显示，请在 FreeCAD 中检查后重新预览。"
        if labels and "source_display" in row:
            source, hidden = _display(row["source_display"])
            item["source"] = source
            if hidden:
                item["status"] = "blocked"
        result.append(item)
    return result


def _plan(model, controller, app, gui, document, objects, snapshots, operation, supplied):
    if operation in {"normalize_names", "sync_link_labels"}:
        builder = model.build_rename_preview if operation == "normalize_names" else model.build_link_label_preview
        rows = builder(document, objects, app.listDocuments().values())
        return {"rows": rows}, _public_rows(rows, labels=True)
    controller._reject_link_attributes(objects)
    values = {snapshot["name"]: {field: snapshot["fields"][field]["value"] for field in FIELDS} for snapshot in snapshots}
    initialize = operation == "initialize_properties"
    fields = list(FIELDS) if initialize else ["PartNumber", "PartVersion", "PartName"]
    notes = {}
    if operation == "set_properties":
        fields = [field for field in FIELDS if field in next(iter(supplied.values()))]
        values = {name: dict(properties) for name, properties in supplied.items()}
    elif operation == "recommend_properties":
        # An unparseable Label blocks the batch instead of silently creating
        # empty attributes under the guise of a recommendation.
        for snapshot in snapshots:
            model.recommend_fields_from_label(snapshot["label"])
        values, notes = model.recommend_property_values(snapshots, values)
    rows = controller.build_property_preview(app, gui, document, objects, snapshots, fields, values, initialize)
    public = _public_rows(rows)
    for row in public:
        if row["object"] in notes:
            row["note"] = _reason(notes[row["object"]])
    return {"fields": fields, "values": values, "initialize": initialize}, public


def _drop_private(record):
    for key in ("document_ref", "objects", "snapshots", "plan", "link_states", "public_preview",
                "approval_grant", "approval_view", "approval_ready", "approval_epoch"):
        record.pop(key, None)


def _expire(record, now):
    if now >= record["deadline"] and record["state"] in {"prepared", "awaiting_confirmation"}:
        record["state"] = "expired"
        record.pop("result", None)
        record.pop("error_code", None)
        _drop_private(record)


def _fixed_error(error):
    try:
        code = getattr(error, "code", None)
    except Exception:
        code = None
    return code if type(code) is str and code in _FIXED_ERRORS else "PART_CHANGE_FAILED"


def _fail(record, code):
    record["state"] = "failed"
    record["error_code"] = code if code in _FIXED_ERRORS else "PART_CHANGE_FAILED"
    record.pop("result", None)
    _drop_private(record)


def _status_dto(record, token):
    state = record["state"]
    result = {"changed_count": record["result"]["changed_count"], "saves_document": False} if state == "applied" else None
    if result is not None and record["operation"] == "set_pad_length":
        result["design_result"] = deepcopy(record["result"]["design_result"])
    return {"document": record["document"], "operation": record["operation"], "token": token,
            "state": state, "changed_count": record["changed_count"], "requires_confirmation": True,
            "saves_document": False, "storage": "memory_only", "native_confirmation": record.get("native_confirmation", False),
            "result": result, "error_code": record["error_code"] if state == "failed" else None}


def _get_status(params, Error):
    # This route must remain independent of FreeCAD, Qt, imports and native refs.
    with _lock:
        record = _plans.get(params["token"])
        if record is None:
            raise Error("PART_TOKEN_NOT_FOUND", "未知预览 token，请重新预览。")
        _expire(record, time.monotonic())
        result = _status_dto(record, params["token"])
        if len(json.dumps(result, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")) > MAX_STATUS_BYTES:
            raise Error("PART_STATUS_TOO_LARGE", "零件状态超过响应大小限制。")
        return result


def _approval_status(record, token):
    with _lock:
        _expire(record, time.monotonic())
        return _status_dto(record, token)


def _cancel_approval(record, token, grant):
    with _lock:
        _expire(record, time.monotonic())
        if (_plans.get(token) is record and record["state"] == "awaiting_confirmation"
                and record.get("approval_grant") is grant):
            record["state"] = "cancelled"
            _drop_private(record)
        return _status_dto(record, token)


def _cancel_pending_locked():
    for record in _plans.values():
        if record["state"] == "awaiting_confirmation":
            record["state"] = "cancelled"
            _drop_private(record)


def begin_approval_lifecycle():
    """Open a fresh bridge-owned lifecycle; caller serializes start/stop."""
    global _approval_epoch, _approvals_enabled
    with _lock:
        _approval_epoch += 1
        _cancel_pending_locked()
        _approvals_enabled = True
        return _approval_epoch


def cancel_pending_approvals():
    """Close the lifecycle and invalidate grants without Qt/CAD/transaction access."""
    global _approval_epoch, _approvals_enabled
    with _lock:
        _approvals_enabled = False
        _approval_epoch += 1
        _cancel_pending_locked()


def _confirm_approval(record, token, grant, app, gui, Error):
    with _lock:
        _expire(record, time.monotonic())
        if (_plans.get(token) is not record or record["state"] != "awaiting_confirmation"
                or record.get("approval_grant") is not grant or record.get("approval_ready") is not True
                or not _approvals_enabled or record.get("approval_epoch") != _approval_epoch):
            return _status_dto(record, token)
    try:
        _, controller = _mvc()
        controller._ensure_main_thread()
    except Exception:
        with _lock:
            if record["state"] == "awaiting_confirmation" and record.get("approval_grant") is grant:
                _fail(record, "GUI_THREAD_REQUIRED")
        return _approval_status(record, token)
    try:
        _apply({"token": token}, app, gui, Error, _native_grant=(record, grant))
    except Exception as error:
        # Usually _apply already stored a fixed terminal error.  Guard the
        # callback boundary too, without forwarding any native exception text.
        with _lock:
            if record["state"] in {"awaiting_confirmation", "applying"} and record.get("approval_grant") is grant:
                _fail(record, _fixed_error(error))
    return _approval_status(record, token)


def _open_approval(gui, preview, on_confirm, on_cancel, read_status):
    view = _load_sibling("pft_part_approval_views")
    translate = _load_sibling("pft_workbench_i18n").tr
    return view.open_part_approval(gui, preview, on_confirm, on_cancel, read_status, translate=translate)


def _request_approval(params, app, gui, Error, _expected_epoch=None):
    with _lock:
        if not _approvals_enabled:
            raise Error("SERVER_NOT_RUNNING", "原生确认生命周期未启用，请启动服务后重新请求。")
        epoch = _approval_epoch
        if _expected_epoch is not None and _expected_epoch != epoch:
            raise Error("SERVER_STOPPING", "请求属于已停止的服务生命周期，未打开确认窗口。")
    model, controller = _mvc()
    try:
        controller._ensure_main_thread()
    except model.PftToolError:
        raise Error("GUI_THREAD_REQUIRED", "零件确认窗口必须从 FreeCAD 界面线程打开。") from None
    token = params["token"]
    with _lock:
        if not _approvals_enabled or epoch != _approval_epoch:
            raise Error("SERVER_STOPPING", "请求期间服务已停止或重启，未打开确认窗口。")
        record = _plans.get(token)
        if record is None:
            raise Error("PART_TOKEN_NOT_FOUND", "未知预览 token，请重新预览。")
        for existing in _plans.values():
            _expire(existing, time.monotonic())
        state = record["state"]
        if state == "awaiting_confirmation":
            handle = record.get("approval_view")
            if handle is None:
                return _status_dto(record, token)
        elif state != "prepared":
            return _status_dto(record, token)
        else:
            handle = None
            if any(other is not record and other["state"] == "awaiting_confirmation" for other in _plans.values()):
                raise Error("PART_APPROVAL_BUSY", "已有一个原生确认窗口，请先完成或取消。")
            if not record["can_apply"] or record["changed_count"] <= 0:
                _fail(record, "PART_PREVIEW_BLOCKED" if not record["can_apply"] else "PART_APPROVAL_NO_CHANGES")
                return _status_dto(record, token)
    try:
        _binding(record, model, controller, app, gui, Error)
    except Exception as error:
        with _lock:
            if record["state"] in {"prepared", "awaiting_confirmation"}:
                _fail(record, _fixed_error(error))
        return _approval_status(record, token)
    if handle is not None:
        with _lock:
            if record["state"] != "awaiting_confirmation":
                return _status_dto(record, token)
            grant = record.get("approval_grant")
            record["approval_ready"] = False
        try:
            handle.present()
        except Exception:
            with _lock:
                if record["state"] == "awaiting_confirmation" and record.get("approval_grant") is grant:
                    _fail(record, "PART_APPROVAL_UI_UNAVAILABLE")
        finally:
            with _lock:
                if record["state"] == "awaiting_confirmation" and record.get("approval_grant") is grant:
                    record["approval_ready"] = True
        return _approval_status(record, token)
    with _lock:
        _expire(record, time.monotonic())
        if record["state"] != "prepared":
            return _status_dto(record, token)
        if not _approvals_enabled or epoch != _approval_epoch:
            record["state"] = "cancelled"
            _drop_private(record)
            return _status_dto(record, token)
        if any(other is not record and other["state"] == "awaiting_confirmation" for other in _plans.values()):
            raise Error("PART_APPROVAL_BUSY", "已有一个原生确认窗口，请先完成或取消。")
        grant = object()
        record.update(state="awaiting_confirmation", approval_grant=grant, approval_ready=False, approval_epoch=epoch)
        preview = deepcopy(record["public_preview"])
    try:
        handle = _open_approval(gui, preview,
            lambda: _confirm_approval(record, token, grant, app, gui, Error),
            lambda: _cancel_approval(record, token, grant),
            lambda: _approval_status(record, token))
        if not callable(getattr(handle, "present", None)) or not callable(getattr(handle, "close", None)):
            raise TypeError("native approval handle unavailable")
    except Exception:
        with _lock:
            if record["state"] == "awaiting_confirmation" and record.get("approval_grant") is grant:
                _fail(record, "PART_APPROVAL_UI_UNAVAILABLE")
        return _approval_status(record, token)
    with _lock:
        if record["state"] == "awaiting_confirmation" and record.get("approval_grant") is grant:
            record.update(approval_view=handle, approval_ready=True)
        return _status_dto(record, token)


def _store(record, Error):
    with _lock:
        now = time.monotonic()
        for existing in _plans.values():
            _expire(existing, now)
        if len(_plans) >= MAX_PLANS:
            disposable = next((token for token, existing in _plans.items()
                               if existing["state"] in {"applied", "failed", "expired", "cancelled"} or not existing["can_apply"]), None)
            if disposable is None:
                raise Error("PART_PREVIEW_CAPACITY", "已有 32 个待处理预览，请先应用或等待旧预览过期。")
            del _plans[disposable]
        token = secrets.token_urlsafe(24)
        while token in _plans:
            token = secrets.token_urlsafe(24)
        _plans[token] = record
        return token


def _prepare(params, app, gui, Error):
    model, controller = _mvc()
    try:
        controller._ensure_main_thread()
    except model.PftToolError:
        raise Error("GUI_THREAD_REQUIRED", "零件预览必须从 FreeCAD 界面线程执行。")
    document = _document(app, params["docName"], Error)
    try:
        controller._ensure_idle(app, gui, document)
    except model.PftToolError as error:
        raise Error("PART_PREVIEW_UNAVAILABLE", _reason(error))
    objects = _objects(document, params["objectNames"], Error)
    operation = params["operation"]
    snapshots = [] if operation == "set_pad_length" else [model._snapshot(obj) for obj in objects]
    plan, link_states = {}, []
    if operation == "set_pad_length":
        pad = _pad()
        try:
            design_plan, rows = pad.prepare_plan(app, gui, document, params["bodyName"], objects[0], params["lengthMm"], controller)
            plan = {"pad_plan": design_plan, "pad_input": {"body": params["bodyName"],
                    "pad": objects[0].Name, "length_mm": params["lengthMm"]}}
        except pad.PftPadLengthError as error:
            rows = [{"object": objects[0].Name, "field": "Length (mm)", "old": None, "new": params["lengthMm"],
                     "status": "blocked", "reason": str(error)}]
    else:
        try:
            plan, rows = _plan(model, controller, app, gui, document, objects, snapshots, operation, params.get("values"))
            link_states = _link_states(model, objects)
        except model.PftToolError as error:
            rows = [{"object": obj.Name, "field": "Label" if operation in {"normalize_names", "sync_link_labels"} else "PartName",
                     "old": None, "new": None, "status": "blocked", "reason": _reason(error)} for obj in objects]
    can_apply = all(row["status"] != "blocked" for row in rows)
    changed = sum(row["status"] == "change" for row in rows)
    expires_at = datetime.fromtimestamp(time.time() + TTL_SECONDS, timezone.utc).isoformat().replace("+00:00", "Z")
    record = {"state": "prepared", "document": document.Name, "document_ref": document, "operation": operation,
              "objects": objects, "snapshots": snapshots, "plan": plan, "link_states": link_states,
              "deadline": time.monotonic() + TTL_SECONDS, "can_apply": can_apply, "changed_count": changed,
              "native_confirmation": False}
    response = {"document": document.Name, "operation": operation, "token": "x" * 32, "state": "prepared", "can_apply": can_apply,
            "expires_at": expires_at, "ttl_seconds": TTL_SECONDS, "changed_count": changed, "rows": rows,
            "requires_confirmation": True, "saves_document": False, "storage": "memory_only"}
    if operation == "set_pad_length":
        response["design_request"] = {"body": params["bodyName"], "pad": objects[0].Name, "length_mm": params["lengthMm"]}
        for row in rows:
            row.update(body=params["bodyName"], unit="mm", recompute_roots=[params["bodyName"]])
    if len(json.dumps(response, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")) > MAX_PREVIEW_BYTES:
        raise Error("PART_PREVIEW_TOO_LARGE", "完整预览超过 128 KiB，请减少本批对象或字段；未创建 token，也未修改对象。")
    with _lock:
        response["token"] = _store(record, Error)
        record["public_preview"] = deepcopy(response)
    return response


def _binding(record, model, controller, app, gui, Error):
    document = _document(app, record["document"], Error)
    if document is not record["document_ref"]:
        raise Error("PART_PLAN_STALE", "文档已关闭或重开，请重新预览。")
    if record["operation"] == "set_pad_length":
        pad = _pad()
        try:
            pad.verify_plan(app, gui, document, record["plan"]["pad_plan"], controller)
        except pad.PftPadLengthError as error:
            raise Error(error.code, str(error)) from None
        return document
    try:
        controller._ensure_idle(app, gui, document)
        controller._ensure_snapshot(document, record["objects"], record["snapshots"])
        for link, source, source_document, identity, subnames, label in record["link_states"]:
            current, current_identity, current_subnames = model._direct_link_source(link)
            if (current is not source or current.Document is not source_document
                    or app.getDocument(source_document.Name) is not source_document
                    or current_identity != identity or current_subnames != subnames or current.Label != label):
                raise model.PftToolError("链接来源已变化。")
    except Exception:
        raise Error("PART_PLAN_STALE", "对象、属性、链接来源或编辑状态已变化，请重新预览。")
    return document


def _apply(params, app, gui, Error, _native_grant=None):
    token = params["token"]
    with _lock:
        record = _plans.get(token)
        if record is None:
            raise Error("PART_TOKEN_NOT_FOUND", "未知预览 token，请重新预览。")
        _expire(record, time.monotonic())
        if record["state"] == "expired":
            raise Error("PART_TOKEN_EXPIRED", "预览已超过五分钟，请重新预览。")
        if record["operation"] == "set_pad_length" and _native_grant is None:
            raise Error("PAD_NATIVE_CONFIRMATION_REQUIRED", "Pad 长度只能由原生确认窗口的人工确认回调执行，未消费预览。")
        if record["state"] == "applied":
            return {**record["result"], "replayed": True}
        if record["state"] == "awaiting_confirmation":
            if (_native_grant is None or _native_grant[0] is not record
                    or record.get("approval_grant") is not _native_grant[1]
                    or record.get("approval_ready") is not True or not _approvals_enabled
                    or record.get("approval_epoch") != _approval_epoch):
                raise Error("PART_APPROVAL_PENDING", "请在已打开的原生窗口中确认；外部调用不会替代人工确认。")
            record["native_confirmation"] = True
        elif record["state"] != "prepared" or _native_grant is not None:
            raise Error("PART_TOKEN_USED", "token 已消费或正在处理，不会再次执行；请重新预览。")
        record["state"] = "applying"
    controller = None
    try:
        if not record["can_apply"]:
            raise Error("PART_PREVIEW_BLOCKED", "预览存在冲突或不可安全显示的值，未修改对象；请重新预览。")
        model, controller = _mvc()
        controller._ensure_main_thread()
        document = _binding(record, model, controller, app, gui, Error)
        objects, snapshots, plan = record["objects"], record["snapshots"], record["plan"]
        operation = record["operation"]
        design_result = None
        if operation == "set_pad_length":
            pad = _pad()
            try:
                design_result = pad.apply_plan(app, gui, document, plan["pad_plan"], controller)
                design_result = _load_sibling("pft_pad_contract").validate_design_result(design_result, plan["pad_input"])
            except pad.PftPadLengthError as error:
                raise Error(error.code, str(error)) from None
            changed = 1
        elif operation == "normalize_names":
            changed = controller.apply_rename(app, gui, document, objects, snapshots, plan["rows"])
        elif operation == "sync_link_labels":
            changed = controller.apply_link_labels(app, gui, document, objects, snapshots, plan["rows"])
        else:
            changed = controller.apply_properties(app, gui, document, objects, snapshots, plan["fields"], plan["values"], plan["initialize"])
        if type(changed) is not int or not 0 <= changed <= 500:
            raise Error("PART_CHANGE_FAILED", "控制器未返回有效的修改数量，请检查当前对象状态。")
        result = {"document": record["document"], "operation": operation, "token": token,
                  "state": "applied", "changed_count": changed, "replayed": False, "saves_document": False}
        if design_result is not None:
            result["design_result"] = design_result
        with _lock:
            record["state"] = "applied"
            record["changed_count"] = changed
            record["result"] = result
            _drop_private(record)
        return dict(result)
    except Exception as error:
        with _lock:
            _fail(record, _fixed_error(error))
        if isinstance(error, Error):
            raise
        if controller is not None and isinstance(error, controller.PftWriteError) and error.code in {
                "PART_TRANSACTION_STATE_UNAVAILABLE", "PART_TRANSACTION_OPEN_UNVERIFIED",
                "PART_TRANSACTION_CHANGED", "PART_COMMIT_UNVERIFIED", "PART_ROLLBACK_UNVERIFIED",
                "PART_PROPERTY_VERIFY_FAILED", "PART_PREFERENCE_RESTORE_UNVERIFIED",
                "PART_ROLLBACK_AND_PREFERENCE_UNVERIFIED"}:
            raise Error(error.code, _reason(error)) from None
        raise Error("PART_CHANGE_FAILED", "本次应用未完成；token 已消费，请检查对象后重新预览。") from error


def perform(method, params, App, Gui, Error, _approval_epoch=None):
    if not validate(method, params, Error):
        raise Error("METHOD_NOT_FOUND", "该业务方法未开放。")
    if method == "get_part_change_status":
        return _get_status(params, Error)
    if method == "request_part_change_approval":
        return _request_approval(params, App, Gui, Error, _expected_epoch=_approval_epoch)
    return _prepare(params, App, Gui, Error) if method == "prepare_part_changes" else _apply(params, App, Gui, Error)
