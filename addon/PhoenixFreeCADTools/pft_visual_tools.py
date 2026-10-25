# SPDX-License-Identifier: Apache-2.0
"""Explicit current-document view actions and transactional native materials.

Prepare reads snapshots and constructs independent Material values only. Apply
owns one document transaction, never saves/recomputes or changes selection.
Plans are local, short-lived capabilities; native objects never leave storage.
"""

import copy
import json
import math
import random
import secrets
import threading
import time
import unicodedata

PFT_MAX_VISUAL_TARGETS = 100
PFT_MAX_MATERIALS = 256
PFT_MAX_PREVIEW_BYTES = 128 * 1024
PFT_PLAN_SECONDS = 300
_MATERIAL_FIELDS = ("DiffuseColor", "AmbientColor", "SpecularColor", "EmissiveColor", "Shininess", "Transparency")
_COLOR_FIELDS = _MATERIAL_FIELDS[:4]
_VIEW_COMMANDS = {"fit": "Std_ViewFitAll", "axonometric": "Std_ViewIsometric"}
_VIEW_SEQUENCES = {"axonometric_fit": ("Std_ViewIsometric", "Std_ViewFitAll")}
_ACTIONS = frozenset(("random_color", "half_transparent", "opaque", "set_transparency"))
_plans = {}

TRANSLATABLE_STRINGS = (
    "请从 FreeCAD 主界面执行视觉操作。", "请明确指定当前活动文档。", "活动文档已变化，请刷新后重试。",
    "无法确认文档事务或编辑状态，未执行视觉操作。", "当前文档正在编辑或已有未完成事务，请先完成或取消。",
    "当前文档未启用撤销，未修改外观。", "请明确勾选最多100个不重复的完整对象。",
    "视觉目标已关闭或变化，请刷新后重新选择。", "所选对象没有受支持的原生材质或有效缓存 Shape。",
    "材质数量或完整预览超过限制，请减少目标。", "仅支持当前文档内的普通单实例 Link；数组、子元素和多层 Link 尚不支持。",
    "Link 来源包含不同逐面材质，无法保持完整样式；请先使用普通单一材质。", "不能同时处理 Link 与其来源，请分开操作。",
    "随机颜色在预览时固定，应用时不会重新抽取。", "透明度统一应用到所有材质条目，保留其他材质字段。",
    "Link 将使用实例材质覆盖，不修改来源对象。", "外观修改可能标记文档为已修改；不会重算或保存 CAD。",
    "视觉操作类型无效。", "透明度必须是0到100的整数，且仅用于设置透明度操作。",
    "没有可确定的活动三维视图，请先激活目标文档的三维视图。",
    "当前 FreeCAD 未提供所需原生命令。", "原生视图操作失败。", "外观预览已失效或变化，请重新预览。",
    "此外观预览已结束，请重新预览。", "外观快照在预览后变化，未执行修改。", "外观写入结果与预览不一致，事务已取消。",
    "外观修改失败，事务已取消。", "外观回滚无法确认，请检查当前外观与撤销记录。", "Phoenix：修改所选对象外观",
    "外观诊断超过256个材质或128 KiB限制，请减少目标。",
    "透明度诊断仅报告当前原生属性，不证明最终屏幕显示效果。",
    "Link 的本地 ShapeMaterial 与来源材质分别报告；未启用覆盖时，本地值可能尚未生效。",
    "诊断不会修改属性、创建外观预览、重算或保存 CAD。",
)


class PftVisualToolsError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _error(code, message):
    return PftVisualToolsError(code, message)


def _get(value, name, default=None):
    try:
        return getattr(value, name)
    except AttributeError:
        return default
    except Exception:
        raise _error("VISUAL_TARGET_CHANGED", "视觉目标已关闭或变化，请刷新后重新选择。") from None


def _name(value):
    return (isinstance(value, str) and 0 < len(value) <= 256
            and not any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in value))


def _main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise _error("MAIN_THREAD_REQUIRED", "请从 FreeCAD 主界面执行视觉操作。")


def _context(app, gui, document_name, undo=False, owned_transaction_id=None):
    _main_thread()
    if not _name(document_name):
        raise _error("DOCUMENT_REQUIRED", "请明确指定当前活动文档。")
    try:
        document = app.getDocument(document_name)
        if document is None or app.ActiveDocument is not document or document.Name != document_name:
            raise _error("DOCUMENT_NOT_ACTIVE", "活动文档已变化，请刷新后重试。")
        transaction = getattr(app, "getActiveTransaction", None)
        gui_document = gui.getDocument(document_name)
        edit = getattr(gui_document, "getInEdit", None)
        if not callable(transaction) or not callable(edit):
            raise _error("DOCUMENT_STATE_UNKNOWN", "无法确认文档事务或编辑状态，未执行视觉操作。")
        active_transaction = transaction()
        if owned_transaction_id is not None:
            if (not isinstance(active_transaction, (tuple, list)) or len(active_transaction) != 2
                    or type(active_transaction[1]) is not int or active_transaction[1] != owned_transaction_id):
                raise _error("VISUAL_TRANSACTION_CHANGED", "外观事务已变化，未接管或取消其他操作。")
        elif active_transaction:
            raise _error("DOCUMENT_BUSY", "当前文档正在编辑或已有未完成事务，请先完成或取消。")
        if edit():
            raise _error("DOCUMENT_BUSY", "当前文档正在编辑或已有未完成事务，请先完成或取消。")
        if undo and getattr(document, "UndoMode", 0) == 0:
            raise _error("UNDO_DISABLED", "当前文档未启用撤销，未修改外观。")
        if undo and not all(callable(getattr(document, method, None)) for method in ("openTransaction", "commitTransaction", "abortTransaction")):
            raise _error("DOCUMENT_STATE_UNKNOWN", "无法确认文档事务或编辑状态，未执行视觉操作。")
        return document, gui_document
    except PftVisualToolsError:
        raise
    except Exception:
        raise _error("DOCUMENT_STATE_UNKNOWN", "无法确认文档事务或编辑状态，未执行视觉操作。") from None


def run_view_action(app, gui, document_name, action):
    """Dispatch a prevalidated native command sequence to the active 3D view."""
    if not isinstance(action, str) or (action not in _VIEW_COMMANDS and action not in _VIEW_SEQUENCES):
        raise _error("INVALID_VISUAL_ACTION", "视觉操作类型无效。")
    document, gui_document = _context(app, gui, document_name)
    try:
        if gui.activeDocument() is not gui_document:
            raise ValueError()
        view = gui_document.activeView()
        if not all(callable(getattr(view, method, None)) for method in ("fitAll", "viewAxonometric")):
            raise ValueError()
    except Exception:
        raise _error("VIEW_UNAVAILABLE", "没有可确定的活动三维视图，请先激活目标文档的三维视图。") from None
    commands = _VIEW_SEQUENCES[action] if action in _VIEW_SEQUENCES else (_VIEW_COMMANDS[action],)
    try:
        available = gui.listCommands()
        if any(command not in available for command in commands) or not callable(getattr(gui, "runCommand", None)):
            raise _error("VIEW_COMMAND_UNAVAILABLE", "当前 FreeCAD 未提供所需原生命令。")
        for command in commands:
            gui.runCommand(command, 0)
    except PftVisualToolsError:
        raise
    except Exception:
        raise _error("VIEW_ACTION_FAILED", "原生视图操作失败。") from None
    return {"document": document.Name, "action": action,
            **({"command": commands[0]} if len(commands) == 1 else {"commands": list(commands)}),
            "dispatched": True, "saved": False, "recomputed": False}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _material(material):
    result = {}
    for key in _MATERIAL_FIELDS:
        value = _get(material, key)
        if key in _COLOR_FIELDS:
            if not isinstance(value, (tuple, list)) or len(value) != 4 or any(not _finite(n) or not 0 <= n <= 1 for n in value):
                raise _error("MATERIAL_UNSUPPORTED", "所选对象没有受支持的原生材质或有效缓存 Shape。")
            result[key] = tuple(float(n) for n in value)
        elif not _finite(value) or key == "Transparency" and not 0 <= value <= 1:
            raise _error("MATERIAL_UNSUPPORTED", "所选对象没有受支持的原生材质或有效缓存 Shape。")
        else:
            result[key] = float(value)
    return result


def _clone(app, values):
    try:
        material = app.Material(**values)
        return material, _material(material)
    except PftVisualToolsError:
        raise
    except Exception:
        raise _error("MATERIAL_UNSUPPORTED", "所选对象没有受支持的原生材质或有效缓存 Shape。") from None


def _ordinary_snapshot(obj):
    view = _get(obj, "ViewObject")
    try:
        shape = _get(obj, "Shape")
        if shape is None or not callable(getattr(shape, "isNull", None)) or shape.isNull():
            raise ValueError()
        if view.getTypeIdOfProperty("ShapeAppearance") != "App::PropertyMaterialList":
            raise ValueError()
        materials = view.ShapeAppearance
        transparency = view.Transparency
    except Exception:
        raise _error("MATERIAL_UNSUPPORTED", "所选对象没有受支持的原生材质或有效缓存 Shape。") from None
    if not isinstance(materials, (list, tuple)) or not materials:
        raise _error("MATERIAL_UNSUPPORTED", "所选对象没有受支持的原生材质或有效缓存 Shape。")
    if len(materials) > PFT_MAX_MATERIALS:
        raise _error("VISUAL_PREVIEW_TOO_LARGE", "材质数量或完整预览超过限制，请减少目标。")
    if type(transparency) is not int or not 0 <= transparency <= 100:
        raise _error("MATERIAL_UNSUPPORTED", "所选对象没有受支持的原生材质或有效缓存 Shape。")
    return {"kind": "shape", "ShapeAppearance": [_material(m) for m in materials], "Transparency": transparency}


def _link_source(document, obj):
    if _get(obj, "TypeId") != "App::Link":
        raise _error("LINK_UNSUPPORTED", "仅支持当前文档内的普通单实例 Link；数组、子元素和多层 Link 尚不支持。")
    count = _get(obj, "ElementCount", 0)
    source = _get(obj, "LinkedObject")
    if (type(count) is not int or count != 0 or _get(obj, "ElementList", [])
            or source is None or isinstance(source, (tuple, list)) or _get(source, "Document") is not document
            or not _name(_get(source, "Name")) or document.getObject(source.Name) is not source
            or str(_get(source, "TypeId", "")).startswith("App::Link")):
        raise _error("LINK_UNSUPPORTED", "仅支持当前文档内的普通单实例 Link；数组、子元素和多层 Link 尚不支持。")
    view = _get(obj, "ViewObject")
    if any(_get(view, field, []) for field in ("MaterialList", "OverrideMaterialList", "OverrideColorList")):
        raise _error("LINK_UNSUPPORTED", "仅支持当前文档内的普通单实例 Link；数组、子元素和多层 Link 尚不支持。")
    return source


def _target_snapshot(document, obj):
    if str(_get(obj, "TypeId", "")).startswith("App::Link"):
        _link_source(document, obj)
        view = _get(obj, "ViewObject")
        override = _get(view, "OverrideMaterial")
        if type(override) is not bool:
            raise _error("MATERIAL_UNSUPPORTED", "所选对象没有受支持的原生材质或有效缓存 Shape。")
        return {"kind": "link", "OverrideMaterial": override, "ShapeMaterial": _material(_get(view, "ShapeMaterial"))}
    return _ordinary_snapshot(obj)


def _identical(document, entry):
    obj = entry["object"]
    return (document.getObject(entry["name"]) is obj and _get(obj, "Document") is document
            and _get(obj, "ViewObject") is entry["view"] and _get(obj, "TypeId") == entry["type"])


def _check_before(document, entries):
    try:
        for entry in entries:
            if not _identical(document, entry) or _target_snapshot(document, entry["object"]) != entry["before"]:
                raise ValueError()
            source = entry.get("source")
            if source is not None and (not _identical(document, source)
                    or _link_source(document, entry["object"]) is not source["object"]
                    or _ordinary_snapshot(source["object"]) != source["before"]):
                raise ValueError()
    except Exception:
        raise _error("VISUAL_SNAPSHOT_CHANGED", "外观快照在预览后变化，未执行修改。") from None


def _inspection_too_large():
    return _error("VISUAL_INSPECTION_TOO_LARGE", "外观诊断超过256个材质或128 KiB限制，请减少目标。")


def _inspection_percent(material, budget):
    budget[0] += 1
    if budget[0] > PFT_MAX_MATERIALS:
        raise _inspection_too_large()
    value = _get(material, "Transparency")
    if not _finite(value) or not 0 <= value <= 1:
        raise _error("MATERIAL_UNSUPPORTED", "所选对象没有受支持的原生材质或有效缓存 Shape。")
    return float(value) * 100.0


def _inspection_metrics(values, global_percent):
    mixed = any(not math.isclose(value, values[0], rel_tol=0, abs_tol=0.00001) for value in values[1:])
    mismatch = (any(not math.isclose(value, global_percent, rel_tol=0, abs_tol=0.00001) for value in values)
                if global_percent is not None else None)
    return {"global_transparency_percent": global_percent, "material_transparency_percent": values,
            "mixed_material_transparency": mixed, "global_material_mismatch": mismatch}


def _inspection_shape(view, budget):
    try:
        if view.getTypeIdOfProperty("ShapeAppearance") != "App::PropertyMaterialList":
            raise ValueError()
        materials = view.ShapeAppearance
        if not isinstance(materials, (list, tuple)) or not materials:
            raise ValueError()
        if len(materials) + budget[0] > PFT_MAX_MATERIALS:
            raise _inspection_too_large()
        values = [_inspection_percent(material, budget) for material in materials]
        global_percent = _get(view, "Transparency")
        if type(global_percent) is not int or not 0 <= global_percent <= 100:
            global_percent = None
        return _inspection_metrics(values, global_percent)
    except PftVisualToolsError:
        raise
    except Exception:
        raise _error("MATERIAL_UNSUPPORTED", "所选对象没有受支持的原生材质或有效缓存 Shape。") from None


def inspect_appearance(app, gui, document_name, object_names):
    """Report native transparency properties; do not infer final rendered alpha.

    A Link's primary material list/mixed flag describes its local ShapeMaterial.
    Its reported list selects local or source values by OverrideMaterial, without
    claiming a verified screen result. Link global/mismatch stay unknown.
    """
    _main_thread()
    if (not isinstance(object_names, (tuple, list)) or not 1 <= len(object_names) <= PFT_MAX_VISUAL_TARGETS
            or any(not _name(name) for name in object_names) or len(set(object_names)) != len(object_names)):
        raise _error("VISUAL_TARGETS_REQUIRED", "请明确勾选最多100个不重复的完整对象。")
    document, _ = _context(app, gui, document_name)
    budget, rows = [0], []
    warnings = ["透明度诊断仅报告当前原生属性，不证明最终屏幕显示效果。",
                "诊断不会修改属性、创建外观预览、重算或保存 CAD。"]
    for name in object_names:
        row = {"Name": name, "Label": name, "kind": "unknown", "status": "unsupported", "reason": None,
               "global_transparency_percent": None, "material_transparency_percent": [],
               "mixed_material_transparency": None, "global_material_mismatch": None,
               "reported_material_transparency_percent": [], "reported_mixed_material_transparency": None}
        try:
            obj = document.getObject(name)
            if obj is None or _get(obj, "Document") is not document or _get(obj, "Name") != name:
                raise _error("VISUAL_TARGET_CHANGED", "视觉目标已关闭或变化，请刷新后重新选择。")
            label = _get(obj, "Label", name)
            label = label if isinstance(label, str) else name
            if len(label.encode("utf-8")) > PFT_MAX_PREVIEW_BYTES:
                raise _inspection_too_large()
            row["Label"] = label
            is_link = str(_get(obj, "TypeId", "")).startswith("App::Link")
            row["kind"] = "link" if is_link else "shape"
            view = _get(obj, "ViewObject")
            if is_link:
                source = _link_source(document, obj)
                override = _get(view, "OverrideMaterial")
                if type(override) is not bool:
                    raise _error("MATERIAL_UNSUPPORTED", "所选对象没有受支持的原生材质或有效缓存 Shape。")
                local = _inspection_percent(_get(view, "ShapeMaterial"), budget)
                source_values = _inspection_shape(_get(source, "ViewObject"), budget)
                reported = [local] if override else list(source_values["material_transparency_percent"])
                row.update(_inspection_metrics([local], None))
                row.update(override_material=override, local_shape_material_transparency_percent=local,
                           source={"Name": source.Name, **source_values},
                           reported_material_transparency_percent=reported,
                           reported_mixed_material_transparency=_inspection_metrics(reported, None)["mixed_material_transparency"])
                if "Link 的本地 ShapeMaterial 与来源材质分别报告；未启用覆盖时，本地值可能尚未生效。" not in warnings:
                    warnings.append("Link 的本地 ShapeMaterial 与来源材质分别报告；未启用覆盖时，本地值可能尚未生效。")
            else:
                row.update(_inspection_shape(view, budget))
                row["reported_material_transparency_percent"] = list(row["material_transparency_percent"])
                row["reported_mixed_material_transparency"] = row["mixed_material_transparency"]
            row["status"] = "supported"
        except PftVisualToolsError as error:
            if error.code == "VISUAL_INSPECTION_TOO_LARGE":
                raise
            row["reason"] = error.message
        except Exception:
            row["reason"] = "所选对象没有受支持的原生材质或有效缓存 Shape。"
        rows.append(row)
        if len(json.dumps({"document": document_name, "rows": rows, "warnings": warnings},
                          ensure_ascii=False, allow_nan=False).encode("utf-8")) > PFT_MAX_PREVIEW_BYTES:
            raise _inspection_too_large()
    current, _ = _context(app, gui, document_name)
    if current is not document:
        raise _error("VISUAL_TARGET_CHANGED", "视觉目标已关闭或变化，请刷新后重新选择。")
    return {"document": document_name, "rows": rows, "warnings": warnings}


def _expire_plans():
    now = time.monotonic()
    for token in list(_plans):
        if now - _plans[token]["created"] > PFT_PLAN_SECONDS:
            del _plans[token]
    while len(_plans) >= 20:
        del _plans[next(iter(_plans))]


def _build_appearance(app, gui, document_name, object_names, action, transparency=None, owned_transaction_id=None):
    """Build independent values without storing a public plan or writing CAD."""
    if not isinstance(action, str) or action not in _ACTIONS:
        raise _error("INVALID_VISUAL_ACTION", "视觉操作类型无效。")
    if ((action == "set_transparency" and (type(transparency) is not int or not 0 <= transparency <= 100))
            or (action != "set_transparency" and transparency is not None)):
        raise _error("INVALID_TRANSPARENCY", "透明度必须是0到100的整数，且仅用于设置透明度操作。")
    percent = transparency if action == "set_transparency" else 50 if action == "half_transparent" else 0
    document, _ = _context(app, gui, document_name, undo=True, owned_transaction_id=owned_transaction_id)
    if (not isinstance(object_names, (tuple, list)) or not 1 <= len(object_names) <= PFT_MAX_VISUAL_TARGETS
            or any(not _name(name) for name in object_names) or len(set(object_names)) != len(object_names)):
        raise _error("VISUAL_TARGETS_REQUIRED", "请明确勾选最多100个不重复的完整对象。")
    seed = random.SystemRandom().getrandbits(53) if action == "random_color" else None
    generator = random.Random(seed)
    entries, rows, sources = [], [], {}
    for name in object_names:
        try:
            obj = document.getObject(name)
        except Exception:
            raise _error("VISUAL_TARGET_CHANGED", "视觉目标已关闭或变化，请刷新后重新选择。") from None
        if obj is None or _get(obj, "Document") is not document or _get(obj, "Name") != name:
            raise _error("VISUAL_TARGET_CHANGED", "视觉目标已关闭或变化，请刷新后重新选择。")
        before = _target_snapshot(document, obj)
        entry = {"object": obj, "view": obj.ViewObject, "name": name, "type": obj.TypeId, "before": before}
        if before["kind"] == "link":
            source = _link_source(document, obj)
            if source.Name in object_names:
                raise _error("LINK_SOURCE_SELECTED", "不能同时处理 Link 与其来源，请分开操作。")
            if id(source) not in sources:
                sources[id(source)] = {"object": source, "view": source.ViewObject, "name": source.Name,
                                       "type": source.TypeId, "before": _ordinary_snapshot(source)}
            entry["source"] = sources[id(source)]
            source_before = entry["source"]["before"]
            inherited = source_before["ShapeAppearance"]
            if not before["OverrideMaterial"] and any(m != inherited[0] for m in inherited[1:]):
                raise _error("LINK_MATERIAL_UNSUPPORTED", "Link 来源包含不同逐面材质，无法保持完整样式；请先使用普通单一材质。")
            baselines = [before["ShapeMaterial"] if before["OverrideMaterial"] else inherited[0]]
        else:
            baselines = before["ShapeAppearance"]
        color = tuple(generator.uniform(0.2, 0.9) for _ in range(3)) if seed is not None else None
        changed_field = "DiffuseColor" if color is not None else "Transparency"
        after_materials = []
        for baseline in baselines:
            values = dict(baseline)
            values[changed_field] = (*color, baseline["DiffuseColor"][3]) if color is not None else percent / 100.0
            _, canonical = _clone(app, values)
            if any(canonical[key] != baseline[key] for key in _MATERIAL_FIELDS if key != changed_field):
                raise _error("MATERIAL_UNSUPPORTED", "所选对象没有受支持的原生材质或有效缓存 Shape。")
            after_materials.append(canonical)
        after = ({"kind": "link", "OverrideMaterial": True, "ShapeMaterial": after_materials[0]}
                 if before["kind"] == "link" else {"kind": "shape", "ShapeAppearance": after_materials,
                    "Transparency": before["Transparency"] if color is not None else percent})
        entry["after"] = after
        entries.append(entry)
        row = {"Name": name, "Label": str(_get(obj, "Label", name)), "before": copy.deepcopy(before), "after": copy.deepcopy(after),
               "enables_link_override": before["kind"] == "link" and not before["OverrideMaterial"]}
        if before["kind"] == "link":
            row["effective_material_before"] = copy.deepcopy(baselines[0])
        rows.append(row)
    warnings = ["外观修改可能标记文档为已修改；不会重算或保存 CAD。",
                "随机颜色在预览时固定，应用时不会重新抽取。" if seed is not None else "透明度统一应用到所有材质条目，保留其他材质字段。"]
    if any(entry["before"]["kind"] == "link" for entry in entries):
        warnings.append("Link 将使用实例材质覆盖，不修改来源对象。")
    public = {"token": secrets.token_urlsafe(24), "document": document_name, "action": action, "seed": seed,
              "targets": rows, "target_count": len(rows), "warnings": warnings, "can_apply": True,
              "expires_in_seconds": PFT_PLAN_SECONDS}
    if action == "set_transparency":
        public["transparency"] = percent
    bounded = {"plan": public, "source_guards": [source["before"] for source in sources.values()]}
    if len(json.dumps(bounded, ensure_ascii=False, allow_nan=False).encode("utf-8")) > PFT_MAX_PREVIEW_BYTES:
        raise _error("VISUAL_PREVIEW_TOO_LARGE", "材质数量或完整预览超过限制，请减少目标。")
    _check_before(document, entries)
    return public, {"app": app, "gui": gui, "document": document, "entries": entries,
                    "public": copy.deepcopy(public), "created": time.monotonic(), "state": "pending"}


def prepare_appearance(app, gui, document_name, object_names, action, transparency=None):
    """Read a complete immutable before/after plan; never assign a CAD property."""
    public, record = _build_appearance(app, gui, document_name, object_names, action, transparency)
    _expire_plans()
    _plans[public["token"]] = record
    return public


def apply_appearance(app, gui, plan):
    """Recheck the full plan and apply only its intended materials atomically."""
    _main_thread()
    token = plan.get("token") if isinstance(plan, dict) else None
    record = _plans.get(token) if isinstance(token, str) else None
    if (record is None or time.monotonic() - record["created"] > PFT_PLAN_SECONDS
            or plan != record["public"] or app is not record["app"] or gui is not record["gui"]):
        raise _error("VISUAL_PLAN_INVALID", "外观预览已失效或变化，请重新预览。")
    if record["state"] == "applied":
        return {**record["summary"], "replayed": True}
    if record["state"] != "pending":
        raise _error("VISUAL_PLAN_USED", "此外观预览已结束，请重新预览。")
    document, _ = _context(app, gui, plan["document"], undo=True)
    if document is not record["document"]:
        raise _error("VISUAL_SNAPSHOT_CHANGED", "外观快照在预览后变化，未执行修改。")
    entries = record["entries"]
    _check_before(document, entries)
    opened = False
    try:
        document.openTransaction("Phoenix：修改所选对象外观")
        opened = True
        for entry in entries:
            view, after = entry["view"], entry["after"]
            if after["kind"] == "shape":
                view.ShapeAppearance = [_clone(app, values)[0] for values in after["ShapeAppearance"]]
            else:
                view.ShapeMaterial = _clone(app, after["ShapeMaterial"])[0]
                view.OverrideMaterial = True
        if app.ActiveDocument is not document or app.getDocument(plan["document"]) is not document:
            raise ValueError()
        for entry in entries:
            if not _identical(document, entry) or _target_snapshot(document, entry["object"]) != entry["after"]:
                raise ValueError()
            source = entry.get("source")
            if source is not None and (not _identical(document, source)
                    or _link_source(document, entry["object"]) is not source["object"]
                    or _ordinary_snapshot(source["object"]) != source["before"]):
                raise ValueError()
        document.commitTransaction()
        opened = False
    except Exception:
        record["state"] = "failed"
        if opened:
            try:
                document.abortTransaction()
                _check_before(document, entries)
            except Exception:
                raise _error("VISUAL_ROLLBACK_UNVERIFIED", "外观回滚无法确认，请检查当前外观与撤销记录。") from None
        raise _error("VISUAL_APPLY_FAILED", "外观修改失败，事务已取消。") from None
    record["state"] = "applied"
    record["summary"] = {"document": plan["document"], "action": plan["action"], "target_count": len(entries),
                         "changed_count": sum(entry["before"] != entry["after"] for entry in entries),
                         "saved": False, "recomputed": False, "replayed": False}
    return dict(record["summary"])
