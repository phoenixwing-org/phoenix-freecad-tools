# SPDX-License-Identifier: Apache-2.0
"""Bounded, structured FreeCAD operations. Call perform() on the GUI thread.

This module intentionally has no dependency on FreeCAD, Qt, or the HTTP bridge
at import time. Read operations never recompute, save, change selection, or
activate a document. Coordinates describe the Shape returned by FreeCAD; no
assembly-wide transform or fresh geometry validation is implied.
"""

import math
import re
import unicodedata

METHODS = frozenset({
    "get_document_info", "list_objects", "get_object_info", "get_selection",
    "measure_geometry", "list_drawing_pages", "inspect_drawing_page", "view_standard",
    "set_selection", "set_visibility", "recompute_document",
})
MAX_ITEMS = 100
MAX_PROPERTY_ITEMS = 50
MAX_STRING = 512
_VIEWS = {
    "front": "viewFront", "rear": "viewRear", "left": "viewLeft", "right": "viewRight",
    "top": "viewTop", "bottom": "viewBottom", "axonometric": "viewAxonometric",
}
_KEYS = {
    "get_document_info": {"docName"},
    "list_objects": {"docName", "offset", "limit", "typeId"},
    "get_object_info": {"docName", "objectName", "properties"},
    "get_selection": {"docName"},
    "measure_geometry": {"docName", "objectName", "subelement"},
    "list_drawing_pages": {"docName"},
    "inspect_drawing_page": {"docName", "pageName"},
    "view_standard": {"docName", "view", "fit"},
    "set_selection": {"docName", "objectNames"},
    "set_visibility": {"docName", "objectNames", "visible"},
    "recompute_document": {"docName"},
}


def _valid_name(value, maximum=256):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and not any(unicodedata.category(char) in {"Cc", "Cs"} for char in value))


def validate(method, params, ErrorClass):
    """Return False for another module's method; reject malformed own requests."""
    if not isinstance(method, str) or method not in METHODS:
        return False
    if not isinstance(params, dict) or set(params) - _KEYS[method]:
        raise ErrorClass("INVALID_PARAMS", "参数必须是对象且不能含未开放字段。")
    for field in ("docName", "objectName", "pageName"):
        if field in params and not _valid_name(params[field]):
            raise ErrorClass("INVALID_PARAMS", field + " 必须是非空内部名称，不能含控制字符。")
    required = "objectName" if method in {"get_object_info", "measure_geometry"} else "pageName" if method == "inspect_drawing_page" else None
    if required and required not in params:
        raise ErrorClass("INVALID_PARAMS", "缺少 " + required + "。")
    if method in {"set_selection", "set_visibility", "recompute_document"} and "docName" not in params:
        raise ErrorClass("INVALID_PARAMS", "修改操作必须明确指定 docName。")
    if method in {"set_selection", "set_visibility"}:
        names = params.get("objectNames")
        if (not isinstance(names, list) or len(names) > MAX_ITEMS
                or method == "set_visibility" and not names
                or any(not _valid_name(value) for value in names)
                or len(set(names)) != len(names)):
            raise ErrorClass("INVALID_PARAMS", "objectNames 必须是最多100个不重复内部名称；显隐操作不能为空。")
    if method == "set_visibility" and type(params.get("visible")) is not bool:
        raise ErrorClass("INVALID_PARAMS", "visible 必须是布尔值。")
    if "offset" in params and (type(params["offset"]) is not int or not 0 <= params["offset"] <= 2147483647):
        raise ErrorClass("INVALID_PARAMS", "offset 必须是非负整数。")
    if "limit" in params and (type(params["limit"]) is not int or not 1 <= params["limit"] <= MAX_ITEMS):
        raise ErrorClass("INVALID_PARAMS", "limit 必须是 1 到 100 的整数。")
    if "typeId" in params and (not isinstance(params["typeId"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:]{0,127}", params["typeId"])):
        raise ErrorClass("INVALID_PARAMS", "typeId 必须是 FreeCAD 类型名称，使用精确类型匹配。")
    if "properties" in params:
        properties = params["properties"]
        if (not isinstance(properties, list) or len(properties) > 20
                or any(not _valid_name(value, 128) or not value.isidentifier() or value.startswith("_") for value in properties)
                or len(set(properties)) != len(properties)):
            raise ErrorClass("INVALID_PARAMS", "properties 最多包含 20 个不同的公开属性名称，不支持属性路径。")
    if "subelement" in params and (not isinstance(params["subelement"], str) or not re.fullmatch(r"(?:Face|Edge|Vertex)[1-9][0-9]{0,6}", params["subelement"])):
        raise ErrorClass("INVALID_PARAMS", "subelement 仅支持 FaceN、EdgeN 或 VertexN，编号从 1 开始。")
    if method == "view_standard" and (not isinstance(params.get("view"), str) or params["view"] not in _VIEWS):
        raise ErrorClass("INVALID_PARAMS", "view 必须是已开放的标准视图。")
    if "fit" in params and type(params["fit"]) is not bool:
        raise ErrorClass("INVALID_PARAMS", "fit 必须是布尔值。")
    return True


def _attr(value, key, default=None):
    try:
        return getattr(value, key)
    except Exception:
        return default


def _finite(value):
    if type(value) is int:
        return value if abs(value) <= 9007199254740991 else None
    return value if type(value) is float and math.isfinite(value) else None


def _sequence(value):
    return value if isinstance(value, (list, tuple)) else []


def _name(value):
    result = _attr(value, "Name")
    return result if isinstance(result, str) else None


def _ref(value, document=None, subelements=None):
    object_name = _name(value)
    target_document = _name(_attr(value, "Document"))
    if object_name is None or target_document is None:
        return None
    result = {"document": target_document, "object": object_name}
    if subelements is not None:
        accepted = [item for item in subelements[:MAX_ITEMS] if _valid_name(item, 512) and not _is_private_text(item)]
        result["subelements"] = accepted
        if len(accepted) != len(subelements):
            # Never shorten an identifier into a different, apparently valid
            # topology reference. Omitted references are explicit instead.
            result["subelements_limited"] = True
    if document is not None:
        result["external"] = target_document != _name(document)
    return result


def _vector(value):
    result = {axis: _finite(_attr(value, axis)) for axis in ("x", "y", "z")}
    return result if all(number is not None for number in result.values()) else None


def _is_private_text(value):
    # Do not emit absolute filesystem paths, file URIs, or inline SVG/template
    # source, including paths embedded in an expression or error description.
    return bool(re.search(r"(?:^|[\s\"'=([{])(?:/|[A-Za-z]:[\\/]|\\\\)|file://|<\s*(?:svg|\?xml|!DOCTYPE)\b", value, re.I))


class _Json:
    def __init__(self):
        self.warnings = []
        self.remaining = 1024

    def warn(self, message):
        if message not in self.warnings and len(self.warnings) < 30:
            self.warnings.append(message)

    def text(self, value):
        if _is_private_text(value):
            self.warn("路径或模板源码已隐藏。")
            return {"redacted": True, "reason": "private_path_or_source"}
        if len(value) > MAX_STRING:
            self.warn("长文本已截断。")
        return "".join(char for char in value[:MAX_STRING] if unicodedata.category(char) not in {"Cc", "Cs"} or char in "\n\t")

    def value(self, value, depth=0):
        self.remaining -= 1
        if self.remaining < 0:
            self.warn("属性数据达到本次操作的总返回预算。")
            return {"truncated": True, "reason": "value_budget"}
        if depth > 4:
            self.warn("嵌套数据达到深度上限。")
            return {"truncated": True, "reason": "depth_limit"}
        if value is None or type(value) is bool:
            return value
        if type(value) in (int, float):
            if type(value) is float and not math.isfinite(value):
                self.warn("非有限数值返回 null。")
                return None
            if type(value) is int and abs(value) > 9007199254740991:
                self.warn("大整数使用带类型的十进制文本，避免客户端精度丢失。")
                return {"kind": "integer", "decimal": str(value) if value.bit_length() < 420 else None, "truncated": value.bit_length() >= 420}
            return value
        if isinstance(value, str):
            return self.text(value)
        reference = _ref(value)
        if reference is not None:
            return {"kind": "object_reference", **reference}
        if isinstance(value, (list, tuple)):
            if len(value) > MAX_PROPERTY_ITEMS:
                self.warn("数组达到返回数量上限。")
            return [self.value(item, depth + 1) for item in value[:MAX_PROPERTY_ITEMS]]
        if isinstance(value, dict):
            if len(value) > MAX_PROPERTY_ITEMS:
                self.warn("字典达到返回数量上限。")
            result = {}
            for key, item in list(value.items())[:MAX_PROPERTY_ITEMS]:
                if isinstance(key, str) and _valid_name(key, 128):
                    safe_key = self.text(key)
                    if isinstance(safe_key, str):
                        result[safe_key] = self.value(item, depth + 1)
            return result
        if type(value).__name__ == "Quantity":
            unit = _attr(value, "Unit")
            signature = _attr(unit, "Signature")
            valid_signature = (isinstance(signature, (list, tuple)) and len(signature) == 8
                               and all(type(power) is int and -128 <= power <= 127 for power in signature))
            # Quantity.Value uses FreeCAD's internal base units, independently
            # of the display schema. Official Base/Unit.h unitSymbols order:
            # length, mass, time, current, temperature, substance, light, angle.
            # Quantity angle is deg; Rotation.Angle elsewhere is rad.
            symbols = ("mm", "kg", "s", "A", "K", "mol", "cd", "deg")
            unit_text = None
            if valid_signature:
                unit_text = "*".join(symbol if power == 1 else "%s^%s" % (symbol, power)
                                     for symbol, power in zip(symbols, signature) if power) or "1"
            else:
                self.warn("量值维度不可读取，未推测数值单位。")
            unit_type = _attr(unit, "Type")
            result = {"kind": "quantity", "value": self.value(_attr(value, "Value"), depth + 1),
                      "unit": unit_text, "unit_signature": list(signature) if valid_signature else None,
                      "unit_type": self.text(unit_type) if isinstance(unit_type, str) else None,
                      "unit_system": "freecad_internal" if valid_signature else None}
            if not valid_signature:
                native = unit if isinstance(unit, str) else str(unit) if type(unit).__name__ == "Unit" else None
                result["native_unit"] = self.text(native) if isinstance(native, str) else None
            return result

        vector = _vector(value)
        if vector is not None:
            return {"kind": "vector", **vector}
        base = _attr(value, "Base")
        rotation = _attr(value, "Rotation")
        if base is not None and rotation is not None and _vector(base) is not None:
            return {"kind": "placement", "base": self.value(base, depth + 1), "translation_unit": "mm", "rotation": self.value(rotation, depth + 1)}
        axis = _vector(_attr(value, "Axis"))
        angle = _finite(_attr(value, "Angle"))
        if axis is not None and angle is not None:
            return {"kind": "rotation", "axis": axis, "angle": angle, "angle_unit": "rad"}
        if _attr(value, "BoundBox") is not None:
            return _geometry(value, self)
        self.warn("部分属性类型未展开。")
        return {"unsupported_type": type(value).__name__[:80]}


def _states(obj, serializer):
    value = _attr(obj, "State")
    if not isinstance(value, (list, tuple)):
        serializer.warn("部分原生状态不可读取；null 不表示没有错误。")
        return None
    return serializer.value(value)


def _refs(values, document=None, maximum=MAX_ITEMS):
    result = []
    for value in _sequence(values)[:maximum]:
        reference = _ref(value, document)
        if reference is not None:
            result.append(reference)
    return result


def _source_refs(value, document, depth=0):
    if depth > 4:
        return []
    reference = _ref(value, document, [])
    if reference is not None:
        return [reference]
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and _ref(value[0]) is not None and isinstance(value[1], (list, tuple, str)):
            names = [value[1]] if isinstance(value[1], str) else list(value[1])
            return [_ref(value[0], document, names)]
        result = []
        for entry in value[:MAX_ITEMS]:
            result.extend(_source_refs(entry, document, depth + 1))
            if len(result) >= MAX_ITEMS:
                break
        return result[:MAX_ITEMS]
    return []


def _document(App, params, ErrorClass):
    name = params.get("docName")
    if name:
        try:
            document = App.getDocument(name)
        except Exception:
            raise ErrorClass("DOCUMENT_NOT_FOUND", "指定文档未打开。")
    else:
        document = _attr(App, "ActiveDocument")
    if document is None:
        raise ErrorClass("DOCUMENT_NOT_FOUND" if name else "NO_ACTIVE_DOCUMENT", "指定文档未打开。" if name else "没有活动文档。")
    return document


def _object(document, name, ErrorClass):
    try:
        result = document.getObject(name)
    except Exception:
        result = None
    if result is None:
        raise ErrorClass("OBJECT_NOT_FOUND", "指定对象不存在；请使用内部 Name。")
    return result


def _view(document, Gui, ErrorClass, required=()):
    """Resolve the active view, or the sole existing 3D view behind a drawing.

    Never activate/create views or guess between multiple non-active 3D views.
    """
    try:
        gui_document = Gui.getDocument(document.Name)
        view = gui_document.activeView() if gui_document is not None else None
    except Exception:
        gui_document, view = None, None
    def supports(candidate):
        return candidate is not None and all(callable(_attr(candidate, name)) for name in required)
    if supports(view):
        return view
    try:
        candidates = [candidate for candidate in gui_document.mdiViewsOfType("Gui::View3DInventor") if supports(candidate)]
    except Exception:
        candidates = []
    if len(candidates) == 1:
        return candidates[0]
    raise ErrorClass("VIEW_UNAVAILABLE", "没有可确定的三维视图；请先激活目标文档的三维视图。")


def _guard_action(document, App, Gui, ErrorClass, undo=False):
    if App.ActiveDocument is not document:
        raise ErrorClass("DOCUMENT_NOT_ACTIVE", "请先激活明确指定的目标文档。")
    check = _attr(App, "getActiveTransaction")
    if not callable(check) or check():
        raise ErrorClass("DOCUMENT_BUSY", "无法确认事务空闲，或当前已有未完成事务。")
    gui_document = Gui.getDocument(document.Name)
    editing = _attr(gui_document, "getInEdit")
    if not callable(editing) or editing():
        raise ErrorClass("DOCUMENT_BUSY", "无法确认编辑状态，或当前正在编辑。")
    if undo and _attr(document, "UndoMode", 0) == 0:
        raise ErrorClass("UNDO_DISABLED", "目标文档没有启用撤销，未执行修改。")


def _selection_snapshot(selection, doc_name):
    # ResolveMode=0 retains selected Link instances instead of their sources.
    records = selection.getSelectionEx(doc_name, 0)
    if len(records) > 1000:
        raise ValueError("selection too large")
    return [(record.ObjectName, tuple(record.SubElementNames)) for record in records]


def _replace_selection(selection, doc_name, records):
    selection.clearSelection(doc_name, False)
    for name, subelements in records:
        for sub in subelements or ("",):
            selection.addSelection(doc_name, name, sub, 0, 0, 0, False)


def _perform_action(method, params, document, App, Gui, ErrorClass):
    _guard_action(document, App, Gui, ErrorClass, undo=method == "set_visibility")
    result = {"document": document.Name, "saved": False}
    if method == "recompute_document":
        native_success = document.recompute()
        invalid = [obj.Name for obj in document.Objects if any("invalid" in str(state).lower() or "error" in str(state).lower() for state in _sequence(_attr(obj, "State", [])))]
        if native_success is False or invalid:
            raise ErrorClass("RECOMPUTE_FAILED", "重算未通过，请读取对象状态。" + (" 对象：" + ", ".join(invalid[:20]) if invalid else ""))
        result.update(recomputed=True, native_success=native_success if type(native_success) is bool else None, invalid_objects=[])
        return result
    # Resolve every object before changing selection, visibility or transactions.
    objects = [_object(document, name, ErrorClass) for name in params["objectNames"]]
    if method == "set_selection":
        selection = Gui.Selection
        try:
            before = _selection_snapshot(selection, document.Name)
        except Exception:
            raise ErrorClass("SELECTION_UNAVAILABLE", "无法完整记录目标文档的现有选择，未修改选择。")
        desired = [(obj.Name, ()) for obj in objects]
        try:
            _replace_selection(selection, document.Name, desired)
            after = _selection_snapshot(selection, document.Name)
            if sorted(after) != sorted(desired):
                raise ValueError("selection rejected")
        except Exception:
            try:
                _replace_selection(selection, document.Name, before)
                if sorted(_selection_snapshot(selection, document.Name)) != sorted(before):
                    raise ValueError("selection restore rejected")
            except Exception:
                raise ErrorClass("SELECTION_RESTORE_FAILED", "选择修改失败且未能完整恢复，请检查当前选择。")
            raise ErrorClass("SELECTION_FAILED", "选择修改未通过，已恢复目标文档原选择。")
        result.update(objects=[obj.Name for obj in objects], selection_scope="target_document_whole_objects", changed=sorted(before) != sorted(desired))
        return result
    target = params["visible"]
    for obj in objects:
        if type(_attr(_attr(obj, "ViewObject"), "Visibility")) is not bool:
            raise ErrorClass("VISIBILITY_UNAVAILABLE", "部分目标没有可用的可见性属性，未修改。")
    before = [obj.ViewObject.Visibility for obj in objects]
    changed = [obj for obj in objects if obj.ViewObject.Visibility != target]
    if changed:
        document.openTransaction("Phoenix: set object visibility")
        try:
            for obj in changed:
                obj.ViewObject.Visibility = target
            if any(obj.ViewObject.Visibility is not target for obj in objects):
                raise ValueError("visibility rejected")
            document.commitTransaction()
        except Exception:
            document.abortTransaction()
            raise ErrorClass("VISIBILITY_FAILED", "显隐修改未通过，工具事务已撤销。")
    result.update(objects=[{"name": obj.Name, "before": old, "visible": obj.ViewObject.Visibility} for obj, old in zip(objects, before)], changed_count=len(changed), undoable=bool(changed))
    return result


def _summary(obj, document, serializer):
    group = _sequence(_attr(obj, "Group", []))
    parents = []
    incoming = _sequence(_attr(obj, "InList", []))
    for parent in incoming[:MAX_ITEMS]:
        if any(member is obj for member in _sequence(_attr(parent, "Group", []))):
            reference = _ref(parent, document)
            if reference is not None:
                parents.append(reference)
    visibility = _attr(_attr(obj, "ViewObject"), "Visibility")
    return {
        "Name": _name(obj), "Label": serializer.value(_attr(obj, "Label")),
        "TypeId": serializer.value(_attr(obj, "TypeId")),
        "parents": parents, "children": _refs(group, document),
        "dependencies": _refs(_attr(obj, "OutList", []), document),
        "dependents": _refs(incoming, document),
        "relationships_truncated": len(group) > MAX_ITEMS or len(incoming) > MAX_ITEMS or len(_sequence(_attr(obj, "OutList", []))) > MAX_ITEMS,
        "visibility": visibility if type(visibility) is bool else None,
        "state": _states(obj, serializer),
        "tip": _ref(_attr(obj, "Tip"), document),
    }


def _geometry(shape, serializer):
    box = _attr(shape, "BoundBox")
    bounds = None
    if box is not None:
        bounds = {key: _finite(_attr(box, key)) for key in ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax", "XLength", "YLength", "ZLength")}
    centre = _vector(_attr(shape, "CenterOfMass"))
    if centre is None:
        centre = _vector(_attr(shape, "Point"))
    radius, radius_source, geometry_type = None, None, None
    for key in ("Curve", "Surface"):
        native_geometry = _attr(shape, key)
        candidate = _finite(_attr(native_geometry, "Radius"))
        if candidate is not None:
            radius, radius_source, geometry_type = candidate, key.lower(), type(native_geometry).__name__
            break
    counts = {}
    for label, attr in (("vertices", "Vertexes"), ("edges", "Edges"), ("faces", "Faces"), ("solids", "Solids")):
        items = _attr(shape, attr)
        counts[label] = len(items) if isinstance(items, (list, tuple)) else None
    volume_applicable = counts["solids"] is not None and counts["solids"] > 0
    serializer.warn("BoundBox 是原生几何内核返回的包络，曲面可能存在近似误差；不可据此作精确间隙校核。")
    if not volume_applicable:
        serializer.warn("当前形状不含实体，体积不适用。")
    return {
        "shape_type": serializer.value(_attr(shape, "ShapeType")),
        "bounding_box": bounds, "length": _finite(_attr(shape, "Length")),
        "area": _finite(_attr(shape, "Area")),
        "volume": _finite(_attr(shape, "Volume")) if volume_applicable else None,
        "volume_applicable": volume_applicable,
        "bounding_box_source": "native_shape_bound_box",
        "bounding_box_precision": "kernel_enclosure_not_exact_clearance",
        "bounding_box_error_bound_mm": None,
        "geometric_centroid": centre, "radius": radius, "radius_source": radius_source,
        "geometry_type": geometry_type, "counts": counts,
        "units": {"length": "mm", "area": "mm^2", "volume": "mm^3", "centroid": "mm", "radius": "mm"},
        "coordinate_scope": "reported_shape_coordinates", "world_transform_applied": False,
        "recomputed": False, "validity_checked": False,
    }


def _sketch(obj, serializer):
    geometry = _sequence(_attr(obj, "Geometry", []))
    constraints = _sequence(_attr(obj, "Constraints", []))
    degrees = _attr(obj, "SolverDOFs")
    if type(degrees) is not int or degrees < 0:
        degrees = None
        serializer.warn("未取得缓存的草图自由度；没有调用求解或重算。")
    fully = _attr(obj, "FullyConstrained")
    entries = []
    for item in geometry[:MAX_PROPERTY_ITEMS]:
        entry = {"kind": type(item).__name__[:80]}
        for field in ("StartPoint", "EndPoint", "Center", "Radius"):
            value = _attr(item, field)
            if value is not None:
                entry[field] = serializer.value(value)
        entries.append(entry)
    encoded_constraints = []
    for constraint in constraints[:MAX_PROPERTY_ITEMS]:
        entry = {field: serializer.value(_attr(constraint, field)) for field in ("Type", "Name", "First", "FirstPos", "Second", "SecondPos", "Third", "ThirdPos", "Value")}
        kind = _attr(constraint, "Type")
        entry["value_unit"] = "rad" if kind in {"Angle", "AngleViaPoint"} else "mm" if kind in {"Distance", "DistanceX", "DistanceY", "Radius", "Diameter"} else None
        encoded_constraints.append(entry)
    return {
        "geometry_count": len(geometry), "constraint_count": len(constraints),
        "degrees_of_freedom": degrees, "fully_constrained": fully if type(fully) is bool else None,
        "geometry": entries, "constraints": encoded_constraints,
        "geometry_length_unit": "mm",
        "truncated": len(geometry) > MAX_PROPERTY_ITEMS or len(constraints) > MAX_PROPERTY_ITEMS,
        "solver_invoked": False,
    }


def _link(obj, document, serializer):
    serializer.warn("Link 仅报告原生来源与 Placement；未展开完整装配实例变换链。")
    transform = _attr(obj, "LinkTransform")
    return {
        "sources": _source_refs(_attr(obj, "LinkedObject"), document),
        "placement": serializer.value(_attr(obj, "Placement")),
        "link_placement": serializer.value(_attr(obj, "LinkPlacement")),
        "link_transform": transform if type(transform) is bool else None,
        "instance_transform_resolved": False,
    }


def _property(obj, key, serializer):
    try:
        getter = _attr(obj, "getTypeIdOfProperty")
        property_type = getter(key) if callable(getter) else None
        if isinstance(property_type, str) and ("PropertyFile" in property_type or property_type == "App::PropertyPath"):
            serializer.warn("文件路径属性已隐藏。")
            return {"redacted": True, "reason": "file_property"}
        # key has been validated as a public identifier and checked against
        # PropertiesList. No dotted expression or attribute path is evaluated.
        return serializer.value(getattr(obj, key))
    except Exception:
        serializer.warn("部分属性不可读取。")
        return {"unavailable": True, "reason": "property_read_failed"}


def _is_page(obj):
    return _attr(obj, "TypeId") == "TechDraw::DrawPage"


def _page_summary(page, document, serializer):
    views = _sequence(_attr(page, "Views", []))
    return {
        "Name": _name(page), "Label": serializer.value(_attr(page, "Label")),
        "TypeId": _attr(page, "TypeId"), "scale": serializer.value(_attr(page, "Scale")),
        "template": _ref(_attr(page, "Template"), document),
        "object_count": len(views), "state": _states(page, serializer),
    }


def perform(method, params, App, Gui, ErrorClass):
    """Execute one validated fixed operation on FreeCAD's GUI main thread."""
    if not validate(method, params, ErrorClass):
        raise ErrorClass("METHOD_NOT_FOUND", "本操作模块没有开放该方法。")
    document = _document(App, params, ErrorClass)
    if method in {"set_selection", "set_visibility", "recompute_document"}:
        return _perform_action(method, params, document, App, Gui, ErrorClass)
    serializer = _Json()
    document_name = _name(document)
    result = {"document": document_name, "source_kind": "live_freecad_gui"}

    if method == "get_document_info":
        modified = _attr(document, "Modified")
        touched = _attr(document, "Touched")
        if type(modified) is not bool:
            modified = None
            serializer.warn("当前接口未提供可确认的 Modified 标记；修改状态为 unknown。")
        serializer.warn("未提供文档实例或修订令牌；这些读结果不能作为写入并发校验。")
        active_body = None
        try:
            view = _view(document, Gui, ErrorClass)
            getter = _attr(view, "getActiveObject")
            active_body = _ref(getter("pdbody"), document) if callable(getter) else None
        except Exception:
            pass
        file_name = _attr(document, "FileName")
        result.update({
            "Name": document_name, "Label": serializer.value(_attr(document, "Label")),
            "object_count": len(_sequence(_attr(document, "Objects", []))),
            "modified": modified, "touched": touched if type(touched) is bool else None,
            "has_saved_file": bool(file_name) if isinstance(file_name, str) else None,
            "instance_token": None, "revision": None, "active_body": active_body,
            "core_units": {"length": "mm", "angle": "rad"}, "display_unit_schema": None,
            "state": _states(document, serializer), "recomputed": False,
        })
    elif method == "list_objects":
        objects = _sequence(_attr(document, "Objects", []))
        if "typeId" in params:
            objects = [obj for obj in objects if _attr(obj, "TypeId") == params["typeId"]]
        offset, limit = params.get("offset", 0), params.get("limit", 50)
        result.update({
            "objects": [_summary(obj, document, serializer) for obj in objects[offset:offset + limit]],
            "offset": offset, "limit": limit, "total": len(objects), "has_more": offset + limit < len(objects),
        })
    elif method == "get_object_info":
        obj = _object(document, params["objectName"], ErrorClass)
        result["object"] = _summary(obj, document, serializer)
        available = [key for key in _sequence(_attr(obj, "PropertiesList", [])) if _valid_name(key, 128) and key.isidentifier() and not key.startswith("_")]
        preferred = ("Length", "Width", "Height", "Radius", "Thickness", "Angle", "Placement", "Profile", "Tip", "Group", "Support", "Source", "LinkedObject", "Scale", "X", "Y", "Rotation", "ExpressionEngine")
        requested = params.get("properties")
        if requested is None:
            requested = [key for key in preferred if key in available][:20]
        unknown = [key for key in requested if key not in available]
        if unknown:
            raise ErrorClass("PROPERTY_NOT_FOUND", "指定属性不在该对象的公开 PropertiesList 中。")
        result["properties"] = {key: _property(obj, key, serializer) for key in requested}
        result["property_names"] = available[:MAX_ITEMS]
        result["property_count"] = len(available)
        result["placement"] = serializer.value(_attr(obj, "Placement"))
        shape = _attr(obj, "Shape")
        result["geometry"] = _geometry(shape, serializer) if shape is not None else None
        type_id = _attr(obj, "TypeId", "")
        if isinstance(type_id, str) and type_id.startswith("Sketcher::"):
            result["sketch"] = _sketch(obj, serializer)
        if isinstance(type_id, str) and type_id.startswith("App::Link"):
            result["link"] = _link(obj, document, serializer)
    elif method == "get_selection":
        try:
            entries = Gui.Selection.getSelectionEx(document_name)
        except TypeError:
            entries = Gui.Selection.getSelectionEx()
        except Exception:
            raise ErrorClass("SELECTION_UNAVAILABLE", "无法读取当前选择。")
        selected = []
        for entry in _sequence(entries):
            obj = _attr(entry, "Object")
            entry_document = _attr(entry, "DocumentName", _name(_attr(obj, "Document")))
            if entry_document != document_name:
                continue
            selected.append({
                "reference": _ref(obj, document, _sequence(_attr(entry, "SubElementNames", []))),
                "picked_points": serializer.value(_sequence(_attr(entry, "PickedPoints", []))),
                "coordinate_scope": "reported_selection_coordinates",
                "length_unit": "mm",
            })
        result.update({"selection": selected[:MAX_ITEMS], "total": len(selected), "truncated": len(selected) > MAX_ITEMS})
    elif method == "measure_geometry":
        obj = _object(document, params["objectName"], ErrorClass)
        shape = _attr(obj, "Shape")
        if shape is None:
            raise ErrorClass("SHAPE_UNAVAILABLE", "对象没有可读取的 Shape。")
        is_null = _attr(shape, "isNull")
        if callable(is_null) and is_null():
            raise ErrorClass("SHAPE_UNAVAILABLE", "对象的 Shape 为空。")
        subelement = params.get("subelement")
        if subelement:
            match = re.fullmatch(r"(Face|Edge|Vertex)([1-9][0-9]{0,6})", subelement)
            collection = _attr(shape, {"Face": "Faces", "Edge": "Edges", "Vertex": "Vertexes"}[match.group(1)])
            index = int(match.group(2)) - 1
            if not isinstance(collection, (list, tuple)) or index >= len(collection):
                raise ErrorClass("SUBELEMENT_NOT_FOUND", "子元素不存在或引用已失效。")
            shape = collection[index]
            serializer.warn("子元素仅核对当前 Shape 的编号存在；不保证跨编辑的拓扑身份稳定。")
        result.update({"object": _ref(obj, document), "subelement": subelement, "geometry": _geometry(shape, serializer), "placement": serializer.value(_attr(obj, "Placement"))})
        serializer.warn("坐标来自对象返回的 Shape；未展开完整装配变换链，也未重算或验证形状新鲜度。")
    elif method == "list_drawing_pages":
        pages = [obj for obj in _sequence(_attr(document, "Objects", [])) if _is_page(obj)]
        result.update({"pages": [_page_summary(page, document, serializer) for page in pages[:MAX_ITEMS]], "total": len(pages), "truncated": len(pages) > MAX_ITEMS})
    elif method == "inspect_drawing_page":
        page = _object(document, params["pageName"], ErrorClass)
        if not _is_page(page):
            raise ErrorClass("WRONG_OBJECT_TYPE", "pageName 必须指向 TechDraw::DrawPage。")
        template = _attr(page, "Template")
        template_data = None
        if template is not None:
            file_source = _attr(template, "Template")
            template_data = {
                "reference": _ref(template, document),
                "editable_fields": serializer.value(_attr(template, "EditableTexts")),
                "width": serializer.value(_attr(template, "Width")), "height": serializer.value(_attr(template, "Height")),
                "has_file_reference": isinstance(file_source, str) and bool(file_source),
            }
        views = _sequence(_attr(page, "Views", []))
        contents = []
        for obj in views[:MAX_ITEMS]:
            content = {"Name": _name(obj), "Label": serializer.value(_attr(obj, "Label")), "TypeId": _attr(obj, "TypeId"), "state": _states(obj, serializer)}
            sources = []
            for field in ("Source", "Source2", "References2D", "References3D", "BaseView"):
                references = _source_refs(_attr(obj, field), document)
                for reference in references:
                    reference["field"] = field
                sources.extend(references)
            content["sources"] = sources[:MAX_ITEMS]
            content["sources_truncated"] = len(sources) > MAX_ITEMS
            content["properties"] = {field: serializer.value(_attr(obj, field)) for field in ("X", "Y", "Scale", "Direction", "Rotation", "Type", "FormatSpec", "MeasureType") if _attr(obj, field) is not None}
            contents.append(content)
        result.update({"page": _page_summary(page, document, serializer), "template": template_data, "objects": contents, "total": len(views), "truncated": len(views) > MAX_ITEMS, "recomputed": False, "references_validated": False})
        serializer.warn("仅返回原生来源和现有状态；没有修复、重算或证明所有尺寸引用有效。")
    elif method == "view_standard":
        fit = params.get("fit", False)
        required = (_VIEWS[params["view"]],) + (("fitAll",) if fit else ())
        view = _view(document, Gui, ErrorClass, required=required)
        operation = _attr(view, _VIEWS[params["view"]])
        fit_all = _attr(view, "fitAll")
        if not callable(operation) or (fit and not callable(fit_all)):
            raise ErrorClass("VIEW_UNAVAILABLE", "目标视图不支持所请求的标准方向或适合窗口。")
        operation()
        if fit:
            fit_all()
        result.update({"view": params["view"], "fit": fit})
    result["warnings"] = serializer.warnings
    return result
