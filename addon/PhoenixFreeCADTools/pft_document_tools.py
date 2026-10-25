# SPDX-License-Identifier: Apache-2.0
"""Read-only current-memory document tools and explicit atomic Markdown export.

No FreeCAD or Qt import. Dimensions are the native object's reported cached
Shape.BoundBox extents in mm. We never resolve Link sources, apply Placement,
recompute, validate geometry, save CAD, activate a document or alter selection.
"""
import html
import importlib.util
import json
import keyword
import math
import os
from pathlib import Path
import stat
import tempfile
import threading
import unicodedata

import pft_document_state as document_state
from pft_workbench_model import BOM_FIELDS


def _load_unit_helper():
    # Bind the small pure helper to this installation, not another cached addon.
    filename = Path(__file__).resolve().with_name("pft_units.py")
    spec = importlib.util.spec_from_file_location("_pft_document_units", filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_units = _load_unit_helper()


PFT_MAX_TARGETS = 200
PFT_MAX_SCAN_OBJECTS = 2000
PFT_MAX_MARKDOWN_BYTES = 1024 * 1024
PFT_MAX_PROPERTY_NAMES = 20
PFT_MAX_PROPERTY_BYTES = 2048
PFT_MAX_PROPERTIES_BYTES = 128 * 1024
TRANSLATABLE_STRINGS = (
    "当前文档对象超过2000项，未自动扫描；请使用较小文档或专用查询。", "当前没有打开的文档。", "对象候选超过上限，仅显示前200项。", "缓存尺寸未重算或验证；不是精确间隙或制造尺寸。",
    "尺寸来自对象报告的 Shape.BoundBox；未额外应用 Placement，也未展开装配世界变换。",
    "Link 仅读取实例自身报告的 Shape，不解析或测量链接来源。", "同时选择了容器和内部对象；尺寸逐对象报告，不合并几何。",
    "可读取", "不可读取", "读取失败", "缓存无效", "未读取", "活动文档", "当前文档信息", "尺寸（mm）", "名称", "显示名称", "已保存路径", "尚未保存", "对象数量", "修改标记", "待重算标记", "编辑状态",
    "活动 Body", "Body 最终 Tip", "未知", "是", "否", "无", "来源", "状态", "对象", "类型", "X 长度", "Y 宽度", "Z 高度", "说明",
    "未选择尺寸对象。", "Markdown 默认省略文件路径，分享前请检查内容。", "信息来自当前内存，可能包含尚未保存的修改。",
    "请从 FreeCAD 主界面打开当前文档工具。", "文档已关闭或变化，请刷新。", "目标对象已变化或不适用，请刷新后重新选择。",
    "Markdown 文件名必须使用绝对路径和 .md 或 .markdown 扩展名。", "导出目录必须已存在，且路径不能经过符号链接。",
    "Markdown 内容无效或超过1 MiB。", "目标不是可覆盖的普通 Markdown 文件。", "目标文件在确认后变化，请重新选择并确认。",
    "目标文件已存在，请明确确认覆盖。", "Markdown 导出失败；未保存 CAD 文档。",
    "所选对象属性（BOM）", "字段", "值", "空值", "缺失", "类型不支持", "已截断", "已净化控制字符",
    "原生对象报告；未验证本地或继承来源。", "未选择属性对象。",
    "所选对象属性汇总超过128 KiB，请减少勾选对象。",
    "所选对象属性", "属性字段必须是至多20个唯一的FreeCAD内部字段名。", "未选择属性字段。",
)


class PftDocumentToolsError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _read(value, name):
    return document_state.read_attribute(value, name)


def _value(value, name, default=None):
    result, status = _read(value, name)
    return result if status == "available" else default


def _text(value, maximum=2048):
    if not isinstance(value, str):
        return ""
    return "".join(char if char in "\n\t" or unicodedata.category(char) not in ("Cc", "Cf", "Cs") else " " for char in value)[:maximum]


def _items(value):
    return list(value) if isinstance(value, (list, tuple)) else []


def _reference(obj, document):
    if obj is None:
        return None
    name = _value(obj, "Name")
    owner = _value(obj, "Document")
    owner_name = _value(owner, "Name")
    if not isinstance(name, str) or not isinstance(owner_name, str):
        return None
    return {"document": owner_name, "object": name, "external": owner is not document}


def _live(document, obj):
    try:
        return obj.Document is document and document.getObject(obj.Name) is obj
    except Exception:
        return False


def list_open_documents(app):
    try:
        documents = app.listDocuments()
        values = list(documents.values()) if isinstance(documents, dict) else []
    except Exception:
        values = []
    return [{"Name": _text(_value(doc, "Name"), 256), "Label": _text(_value(doc, "Label"))}
            for doc in values if isinstance(_value(doc, "Name"), str)]


def _document(app, name):
    try:
        document = app.ActiveDocument if name is None else app.getDocument(name)
        if document is not None and app.getDocument(document.Name) is not document:
            raise ValueError()
        return document
    except Exception:
        raise PftDocumentToolsError("DOCUMENT_CHANGED", "文档已关闭或变化，请刷新。") from None


def _raw_selection(gui, document):
    selection = _value(gui, "Selection")
    extended = _value(selection, "getSelectionEx")
    if callable(extended):
        try:
            try:
                records = list(extended("", 0))
            except TypeError:
                records = list(extended())
            if records:
                result = []
                for record in records:
                    if _value(record, "SubElementNames", ()):
                        continue
                    name = _value(record, "ObjectName")
                    owner = _value(record, "DocumentName")
                    # Prefer raw selection names even if an older API's Object
                    # happens to expose the Link's resolved source.
                    if isinstance(name, str) and owner == document.Name:
                        obj = document.getObject(name)
                    else:
                        obj = _value(record, "Object")
                    if _live(document, obj):
                        result.append(obj)
                return result, True
        except Exception:
            return [], True  # Unknown scope must never broaden to all objects.
    plain = _value(selection, "getSelection")
    if callable(plain):
        try:
            try:
                objects = list(plain("", False))
            except TypeError:
                objects = list(plain())
            return [obj for obj in objects if _live(document, obj)], bool(objects)
        except Exception:
            return [], True
    return [], False


def _shape(obj):
    shape, status = _read(obj, "Shape")
    return shape, status if shape is not None else "unavailable" if status == "available" else status


def _dimension(obj, shape, shape_status):
    name, kind = _text(_value(obj, "Name"), 256), _text(_value(obj, "TypeId"), 256)
    result = {"Name": name, "Label": _text(_value(obj, "Label")), "TypeId": kind, "unit": "mm",
              "length_mm": None, "width_mm": None, "height_mm": None, "status": shape_status,
              "source": "object.Shape.BoundBox", "coordinate_scope": "reported_native_object_shape",
              "placement_applied_by_tool": False, "link_source_resolved": False,
              "recomputed": False, "geometry_freshness": "not_verified"}
    if shape is not None:
        try:
            is_null = _value(shape, "isNull")
            if callable(is_null) and is_null():
                result["status"] = "unavailable"
                return result
            bbox = shape.BoundBox
            numbers = [getattr(bbox, key) for key in ("XLength", "YLength", "ZLength")]
            if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in numbers):
                result["status"] = "invalid_cache"
            else:
                result.update(dict(zip(("length_mm", "width_mm", "height_mm"), map(float, numbers))))
                result["status"] = "available"
        except Exception:
            result["status"] = "read_failed"
    return result


def _property_text(value):
    # A native string can be much larger than our output budget. Only inspect
    # the prefix needed to produce <=2 KiB, without encoding the entire value.
    prefix = value[:PFT_MAX_PROPERTY_BYTES + 1]
    clean = _text(prefix, PFT_MAX_PROPERTY_BYTES + 1)
    encoded = clean.encode("utf-8")
    return (encoded[:PFT_MAX_PROPERTY_BYTES].decode("utf-8", "ignore"),
            len(value) > len(prefix) or len(encoded) > PFT_MAX_PROPERTY_BYTES, clean != prefix)


_PROPERTY_KINDS = {"App::PropertyString": "string", "App::PropertyInteger": "integer",
                   "App::PropertyFloat": "float", "App::PropertyBool": "boolean",
                   "App::PropertyEnumeration": "enumeration"}
# These fixed property types use native Quantity.Value and the internal basis.
# A contradictory native signature is unknown, never a guessed conversion.
_QUANTITY_SIGNATURES = {
    "App::PropertyLength": (1, 0, 0, 0, 0, 0, 0, 0),
    "App::PropertyDistance": (1, 0, 0, 0, 0, 0, 0, 0),
    "App::PropertyAngle": (0, 0, 0, 0, 0, 0, 0, 1),
    "App::PropertyArea": (2, 0, 0, 0, 0, 0, 0, 0),
    "App::PropertyVolume": (3, 0, 0, 0, 0, 0, 0, 0),
    "App::PropertyMass": (0, 1, 0, 0, 0, 0, 0, 0),
    "App::PropertyDensity": (-3, 1, 0, 0, 0, 0, 0, 0),
    "App::PropertySpeed": (1, 0, -1, 0, 0, 0, 0, 0),
    "App::PropertyAcceleration": (1, 0, -2, 0, 0, 0, 0, 0),
    "App::PropertyForce": (1, 1, -2, 0, 0, 0, 0, 0),
    "App::PropertyPressure": (-1, 1, -2, 0, 0, 0, 0, 0),
    "App::PropertyFrequency": (0, 0, -1, 0, 0, 0, 0, 0),
    "App::PropertyPower": (2, 1, -3, 0, 0, 0, 0, 0),
    "App::PropertyTemperature": (0, 0, 0, 0, 1, 0, 0, 0),
}
_QUANTITY_TYPES = frozenset(("App::PropertyQuantity", *_QUANTITY_SIGNATURES))


def validate_property_names(names):
    """Normalize explicit internal names; an empty selection reads no fields."""
    if names is None:
        return tuple(name for name, _ in BOM_FIELDS)
    if (not isinstance(names, (list, tuple)) or len(names) > PFT_MAX_PROPERTY_NAMES
            or any(not isinstance(name, str) or len(name) > 128 or not name.isidentifier()
                   or keyword.iskeyword(name) for name in names) or len(set(names)) != len(names)):
        raise PftDocumentToolsError("INVALID_PROPERTY_NAMES", "属性字段必须是至多20个唯一的FreeCAD内部字段名。")
    return tuple(names)


def _finite_property_number(value):
    if type(value) is int:
        return value if -(2 ** 63) <= value <= 2 ** 63 - 1 else None
    if type(value) is float and math.isfinite(value):
        return value
    return None


def _property_quantity(value):
    if type(value).__name__ != "Quantity":
        return None, "unsupported"
    number, number_status = _read(value, "Value")
    if number_status == "read_failed":
        return None, "read_failed"
    number = _finite_property_number(number)
    if number_status != "available" or number is None:
        return None, "unknown"
    unit, unit_status = _read(value, "Unit")
    if unit_status == "read_failed":
        return None, "read_failed"
    if unit_status != "available" or unit is None:
        return None, "unknown"
    signature, signature_status = _read(unit, "Signature")
    if signature_status == "read_failed":
        return None, "read_failed"
    if signature_status != "available":
        return None, "unknown"
    description = _units.pft_internal_unit(signature)
    if description is None:
        return None, "unknown"
    return {"kind": "quantity", "value": number, **description}, "available"


def _property_field(obj, name, properties, valid_list, explicit):
    field = {"name": name, "type": None, "status": "read_failed", "value": None,
             "truncated": False, "sanitized": False}
    if explicit:
        field["kind"] = None
    if not valid_list:
        return field
    if name not in properties:
        field["status"] = "missing"
        return field
    try:
        native_type = obj.getTypeIdOfProperty(name)
        field["type"] = _text(native_type, 256) if isinstance(native_type, str) else None
        kind = _PROPERTY_KINDS.get(native_type) if isinstance(native_type, str) else None
        if explicit and isinstance(native_type, str) and native_type in _QUANTITY_TYPES:
            kind = "quantity"
        if kind is None or not explicit and kind != "string":
            field["status"] = "unsupported"
            return field  # Do not even get unsupported properties (Links, Shape, Python objects).
        if explicit:
            field["kind"] = kind
        value = getattr(obj, name)
        if value is None:
            field["status"] = "unknown" if explicit else "unsupported"
        elif kind in ("string", "enumeration"):
            if not isinstance(value, str):
                field["status"] = "unsupported"
            else:
                field["value"], field["truncated"], field["sanitized"] = _property_text(value)
                field["status"] = "empty" if value == "" else "available"
        elif kind == "boolean":
            field["value"], field["status"] = (value, "available") if type(value) is bool else (None, "unsupported")
        elif kind in ("integer", "float"):
            number = _finite_property_number(value)
            if kind == "integer" and type(value) is not int:
                field["status"] = "unsupported"
            elif number is None or kind == "float" and type(number) is int and abs(number) > 2 ** 53:
                field["status"] = "unknown" if type(value) in (int, float) else "unsupported"
            else:
                field["value"], field["status"] = (float(number) if kind == "float" else number), "available"
        else:
            field["value"], field["status"] = _property_quantity(value)
            expected = _QUANTITY_SIGNATURES.get(native_type)
            if (field["status"] == "available" and expected is not None
                    and field["value"]["unit_signature"] != list(expected)):
                field["value"], field["status"] = None, "unknown"
    except Exception:
        field["value"], field["status"] = None, "read_failed"
    return field


def _object_properties(objects, names, explicit=False):
    """Read selected native objects only; never follow Links or introspect arbitrary values."""
    if not names:
        return []
    rows, byte_count = [], 2
    for obj in objects:
        properties, property_status = _read(obj, "PropertiesList")
        valid_list = (property_status == "available" and isinstance(properties, (list, tuple))
                      and all(isinstance(name, str) for name in properties))
        row = {"Name": _text(_value(obj, "Name"), 256), "Label": _text(_value(obj, "Label")),
               "TypeId": _text(_value(obj, "TypeId"), 256), "source": "native_object",
               "locality": "not_verified", "link_source_resolved": False,
               "status": "available" if valid_list else "read_failed", "fields": []}
        for name in names:
            field = _property_field(obj, name, properties, valid_list, explicit)
            if field["status"] == "read_failed":
                row["status"] = "read_failed"
            row["fields"].append(field)
        byte_count += len(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")) + bool(rows)
        if byte_count > PFT_MAX_PROPERTIES_BYTES:
            raise PftDocumentToolsError("BOM_PROPERTIES_TOO_LARGE", "所选对象属性汇总超过128 KiB，请减少勾选对象。")
        rows.append(row)
    return rows


def read_document_tools(app, gui, document_name=None, target_names=None, property_names=None):
    if threading.current_thread() is not threading.main_thread():
        raise PftDocumentToolsError("MAIN_THREAD_REQUIRED", "请从 FreeCAD 主界面打开当前文档工具。")
    names = validate_property_names(property_names)
    property_scope = "bom" if property_names is None else "explicit"
    document = _document(app, document_name)
    if document is None:
        return {"document": None, "candidates": [], "selected_names": [], "dimensions": [], "properties": [], "property_scope": property_scope, "property_names": list(names), "warnings": ["当前没有打开的文档。"]}
    native_objects = _items(_value(document, "Objects", []))
    if len(native_objects) > PFT_MAX_SCAN_OBJECTS:
        raise PftDocumentToolsError("DOCUMENT_TOO_LARGE", "当前文档对象超过2000项，未自动扫描；请使用较小文档或专用查询。")
    objects = [obj for obj in native_objects if _live(document, obj)]
    containers = {id(obj): _items(_value(obj, "Group", [])) for obj in objects
                  if _value(obj, "TypeId") in ("PartDesign::Body", "App::Part")}
    body_members = {id(child) for obj in objects if _value(obj, "TypeId") == "PartDesign::Body" for child in containers.get(id(obj), [])}
    candidates, shapes = [], {}
    for obj in objects:
        kind = _value(obj, "TypeId", "")
        if not isinstance(kind, str) or kind.startswith(("TechDraw::", "App::Origin")) or "Datum" in kind:
            continue
        if not (kind.startswith(("Part::", "PartDesign::", "Sketcher::", "App::Link")) or kind == "App::Part"):
            continue
        if kind == "App::Part" and "Shape" not in _items(_value(obj, "PropertiesList", [])):
            continue
        if kind in {"PartDesign::Plane", "PartDesign::Line", "PartDesign::Point", "PartDesign::CoordinateSystem"}:
            continue
        # Candidate discovery reads metadata only. Hidden/unselected Shape
        # properties are never accessed just to populate the picker.
        status = "not_read"
        if len(candidates) >= PFT_MAX_TARGETS:
            break
        internal = id(obj) in body_members or kind.startswith(("Sketcher::", "PartDesign::")) and kind != "PartDesign::Body"
        candidates.append({"Name": _text(_value(obj, "Name"), 256), "Label": _text(_value(obj, "Label")), "TypeId": kind,
                           "internal": internal, "recommended": False, "shape_status": status})
        shapes[candidates[-1]["Name"]] = obj
    candidate_ids = {id(obj) for obj in shapes.values()}
    descendants = {id(child) for parent_id, children in containers.items() if parent_id in candidate_ids for child in children}
    for row in candidates:
        visible = _value(_value(shapes[row["Name"]], "ViewObject"), "Visibility", True)
        row["recommended"] = not row["internal"] and id(shapes[row["Name"]]) not in descendants and visible is not False
    if target_names is None:
        selected, had_raw = _raw_selection(gui, document)
        selected_names = [obj.Name for obj in selected if obj.Name in shapes]
        if not had_raw:
            selected_names = [row["Name"] for row in candidates if row["recommended"]]
        selected_names = list(dict.fromkeys(selected_names))
    else:
        if (not isinstance(target_names, (list, tuple)) or len(target_names) > PFT_MAX_TARGETS
                or any(not isinstance(name, str) or name not in shapes for name in target_names)
                or len(set(target_names)) != len(target_names)):
            raise PftDocumentToolsError("TARGET_CHANGED", "目标对象已变化或不适用，请刷新后重新选择。")
        selected_names = list(target_names)
    context, modified, touched = document_state.native_context(document, app, gui, _reference)
    bodies = []
    for obj in objects:
        if _value(obj, "TypeId") == "PartDesign::Body":
            tip, tip_status = _read(obj, "Tip")
            reference = _reference(tip, document)
            if tip is not None and reference is None and tip_status == "available":
                tip_status = "unavailable"
            bodies.append({"Name": _text(_value(obj, "Name"), 256), "Tip": reference, "tip_status": tip_status})
            if len(bodies) == 100:
                break
    info = {"Name": _text(_value(document, "Name"), 256), "Label": _text(_value(document, "Label")),
            "FileName": _text(_value(document, "FileName"), 8192), "object_count": len(objects),
            "modified": modified["value"], "modified_source": modified["source"], "modified_status": modified["status"],
            "touched": touched["value"], "touched_source": touched["source"], "touched_status": touched["status"],
            "context": context, "bodies": bodies}
    warnings = ["缓存尺寸未重算或验证；不是精确间隙或制造尺寸。",
                "尺寸来自对象报告的 Shape.BoundBox；未额外应用 Placement，也未展开装配世界变换。"]
    if len(candidates) == PFT_MAX_TARGETS:
        warnings.append("对象候选超过上限，仅显示前200项。")
    if any(shapes[name].TypeId.startswith("App::Link") for name in selected_names):
        warnings.append("Link 仅读取实例自身报告的 Shape，不解析或测量链接来源。")
    selected_ids = {id(shapes[name]) for name in selected_names}
    if any(parent in selected_ids and any(id(child) in selected_ids for child in children) for parent, children in containers.items()):
        warnings.append("同时选择了容器和内部对象；尺寸逐对象报告，不合并几何。")
    dimensions = [_dimension(shapes[name], *_shape(shapes[name])) for name in selected_names]
    properties = _object_properties([shapes[name] for name in selected_names], names, explicit=property_names is not None)
    if any(not _live(document, shapes[name]) for name in selected_names):
        raise PftDocumentToolsError("TARGET_CHANGED", "目标对象已变化或不适用，请刷新后重新选择。")
    if _document(app, info["Name"]) is not document:
        raise PftDocumentToolsError("DOCUMENT_CHANGED", "文档已关闭或变化，请刷新。")
    return {"document": info, "candidates": candidates, "selected_names": selected_names,
            "dimensions": dimensions, "properties": properties, "property_scope": property_scope,
            "property_names": list(names), "warnings": warnings}


def _boolean(value, tr):
    return tr("未知") if value is None else tr("是") if value is True else tr("否")


def _status(value, tr):
    return tr({"available": "可读取", "unavailable": "不可读取", "read_failed": "读取失败", "invalid_cache": "缓存无效", "not_read": "未读取",
               "empty": "空值", "missing": "缺失", "unsupported": "类型不支持"}.get(value, "未知"))


def _info_lines(snapshot, tr, include_path):
    info = snapshot.get("document")
    if not info:
        return [tr("当前没有打开的文档。")]
    lines = [("名称", info["Name"]), ("显示名称", info["Label"]), ("对象数量", info["object_count"])]
    if include_path:
        lines.append(("已保存路径", info["FileName"] or tr("尚未保存")))
    for key, field in (("修改标记", "modified"), ("待重算标记", "touched")):
        value = _boolean(info[field], tr) + " (" + tr("来源") + ": " + str(info[field + "_source"] or tr("未知")) + "; " + tr("状态") + ": " + _status(info[field + "_status"], tr) + ")"
        lines.append((key, value))
    active = info["context"].get("active_document", {}).get("value")
    lines.append(("活动文档", active.get("document") if isinstance(active, dict) else tr("无")))
    edit = info["context"].get("in_edit", {})
    lines.append(("编辑状态", _boolean(edit.get("value"), tr) + " (" + tr("来源") + ": " + str(edit.get("source") or tr("未知")) + "; " + tr("状态") + ": " + _status(edit.get("status"), tr) + ")"))
    body = info["context"].get("active_body", {}).get("value")
    lines.append(("活动 Body", body["object"] if body else tr("无") if info["context"].get("active_body", {}).get("status") == "available" else tr("未知")))
    for item in info["bodies"]:
        lines.append(("Body 最终 Tip", item["Name"] + " → " + (item["Tip"]["object"] if item["Tip"] else tr("无") if item["tip_status"] == "available" else tr("未知"))))
    return [tr(key) + ": " + str(value) for key, value in lines]


def format_information(snapshot, tr=lambda value: value):
    lines = [tr("当前文档信息"), *_info_lines(snapshot, tr, True), tr("信息来自当前内存，可能包含尚未保存的修改。"),
             "", tr(_property_title(snapshot)), " | ".join(tr(key) for key in _property_headers(snapshot))]
    lines.extend(" | ".join(str(value).replace("\n", "\\n").replace("\t", "\\t") for value in cells)
                 for cells in _property_cells(snapshot, tr))
    if not snapshot.get("properties"):
        lines.append(tr(_no_properties_message(snapshot)))
    return "\n".join(lines)


_PROPERTY_HEADERS = ("对象", "显示名称", "字段", "值", "状态", "来源", "说明")



def _no_properties_message(snapshot):
    return "未选择属性字段。" if snapshot.get("property_scope") == "explicit" and not snapshot.get("property_names") else "未选择属性对象。"


def _property_title(snapshot):
    return "所选对象属性" if snapshot.get("property_scope") == "explicit" else "所选对象属性（BOM）"


def _property_headers(snapshot):
    return _PROPERTY_HEADERS[:3] + ("类型",) + _PROPERTY_HEADERS[3:] if snapshot.get("property_scope") == "explicit" else _PROPERTY_HEADERS


def _property_display(field, tr):
    value = field["value"]
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if type(value) is bool:
        return tr("是") if value else tr("否")
    if type(value) is int:
        return format(value, "d")
    if type(value) is float and math.isfinite(value):
        return format(value, ".12g")
    if (isinstance(value, dict) and value.get("kind") == "quantity" and value.get("unit_system") == "freecad_internal"
            and _finite_property_number(value.get("value")) is not None and isinstance(value.get("unit"), str)):
        return format(value["value"], ".12g") + " " + _text(value["unit"], 128)
    return tr("未知")


def _property_cells(snapshot, tr):
    for obj in snapshot.get("properties", []):
        for field in obj["fields"]:
            notes = [tr("原生对象报告；未验证本地或继承来源。")]
            if field["truncated"]:
                notes.append(tr("已截断"))
            if field["sanitized"]:
                notes.append(tr("已净化控制字符"))
            prefix = (obj["Name"], obj["Label"], field["name"])
            if snapshot.get("property_scope") == "explicit":
                prefix += (field["type"] or tr("未知"),)
            yield prefix + (_property_display(field, tr), _status(field["status"], tr), obj["source"], "; ".join(notes))


def _number(value, tr):
    return tr("未知") if value is None else format(value, ".12g")


def format_dimensions(snapshot, tr=lambda value: value):
    lines = [tr("尺寸（mm）"), " | ".join(tr(key) for key in ("对象", "显示名称", "X 长度", "Y 宽度", "Z 高度", "状态"))]
    for row in snapshot.get("dimensions", []):
        lines.append(" | ".join((row["Name"], row["Label"], *(_number(row[key], tr) for key in ("length_mm", "width_mm", "height_mm")), _status(row["status"], tr))))
    if not snapshot.get("dimensions"):
        lines.append(tr("未选择尺寸对象。"))
    lines.extend(tr(warning) for warning in snapshot.get("warnings", []))
    return "\n".join(lines)


def escape_markdown(value):
    text = html.escape(str(value), quote=False).replace("\r\n", "\n").replace("\r", "\n")
    for char in "\\`*_{}[]()#+-.!|":
        text = text.replace(char, "\\" + char)
    return text.replace("\n", "<br>").replace("\t", " ")


def format_markdown(snapshot, tr=lambda value: value):
    # Intentionally omit FileName even though the local information UI shows it.
    lines = ["# " + escape_markdown(tr("当前文档信息")), ""]
    lines.extend(escape_markdown(line) for line in _info_lines(snapshot, tr, False))
    lines.extend(("", "## " + escape_markdown(tr("尺寸（mm）")), "", "| " + " | ".join(escape_markdown(tr(key)) for key in ("对象", "显示名称", "X 长度", "Y 宽度", "Z 高度", "状态")) + " |", "| --- | --- | --- | --- | --- | --- |"))
    for row in snapshot.get("dimensions", []):
        values = (row["Name"], row["Label"], *(_number(row[key], tr) for key in ("length_mm", "width_mm", "height_mm")), _status(row["status"], tr))
        lines.append("| " + " | ".join(escape_markdown(value) for value in values) + " |")
    if not snapshot.get("dimensions"):
        lines.extend(("", escape_markdown(tr("未选择尺寸对象。"))))
    headers = _property_headers(snapshot)
    lines.extend(("", "## " + escape_markdown(tr(_property_title(snapshot))), "",
                  "| " + " | ".join(escape_markdown(tr(key)) for key in headers) + " |",
                  "| " + " | ".join("---" for _ in headers) + " |"))
    for cells in _property_cells(snapshot, tr):
        lines.append("| " + " | ".join(escape_markdown(value) for value in cells) + " |")
    if not snapshot.get("properties"):
        lines.extend(("", escape_markdown(tr(_no_properties_message(snapshot)))))
    lines.extend(("", escape_markdown(tr("Markdown 默认省略文件路径，分享前请检查内容。")), ""))
    lines.extend("- " + escape_markdown(tr(warning)) for warning in snapshot.get("warnings", []))
    return "\n".join(lines) + "\n"


def _export_target(filename):
    if (not isinstance(filename, str) or not os.path.isabs(filename) or Path(filename).suffix.lower() not in (".md", ".markdown")
            or any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in filename)):
        raise PftDocumentToolsError("INVALID_EXPORT_PATH", "Markdown 文件名必须使用绝对路径和 .md 或 .markdown 扩展名。")
    target = Path(os.path.abspath(filename))
    current = Path(target.anchor)
    for part in target.parts[1:-1]:
        current /= part
        try:
            info = current.lstat()
        except OSError:
            raise PftDocumentToolsError("INVALID_EXPORT_DIRECTORY", "导出目录必须已存在，且路径不能经过符号链接。") from None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise PftDocumentToolsError("INVALID_EXPORT_DIRECTORY", "导出目录必须已存在，且路径不能经过符号链接。")
    return target


def _stamp(target):
    try:
        info = target.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise PftDocumentToolsError("EXPORT_FAILED", "Markdown 导出失败；未保存 CAD 文档。") from None
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise PftDocumentToolsError("INVALID_EXPORT_TARGET", "目标不是可覆盖的普通 Markdown 文件。")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def prepare_markdown_export(filename, text):
    if filename is None or filename == "":
        return None
    target = _export_target(filename)
    try:
        data = text.encode("utf-8") if isinstance(text, str) else None
    except UnicodeError:
        data = None
    if data is None or len(data) > PFT_MAX_MARKDOWN_BYTES or "\x00" in text:
        raise PftDocumentToolsError("INVALID_MARKDOWN", "Markdown 内容无效或超过1 MiB。")
    stamp = _stamp(target)
    return {"path": str(target), "text": text, "exists": stamp is not None, "_stamp": stamp}


def write_markdown_export(plan, overwrite=False):
    if plan is None:
        return None
    checked = prepare_markdown_export(plan.get("path"), plan.get("text"))
    if checked is None or checked["_stamp"] != plan.get("_stamp"):
        raise PftDocumentToolsError("EXPORT_TARGET_CHANGED", "目标文件在确认后变化，请重新选择并确认。")
    if checked["exists"] and overwrite is not True:
        raise PftDocumentToolsError("OVERWRITE_REQUIRED", "目标文件已存在，请明确确认覆盖。")
    target, temporary = Path(checked["path"]), None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".pft-markdown-", suffix=".tmp", dir=str(target.parent))
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(checked["text"].encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        _export_target(str(target))
        if _stamp(target) != checked["_stamp"]:
            raise PftDocumentToolsError("EXPORT_TARGET_CHANGED", "目标文件在确认后变化，请重新选择并确认。")
        if checked["exists"]:
            os.replace(temporary, str(target))
        else:
            # Publish without overwriting a concurrently created file.
            os.link(temporary, str(target))
        return str(target)
    except PftDocumentToolsError:
        raise
    except OSError:
        raise PftDocumentToolsError("EXPORT_FAILED", "Markdown 导出失败；未保存 CAD 文档。") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
