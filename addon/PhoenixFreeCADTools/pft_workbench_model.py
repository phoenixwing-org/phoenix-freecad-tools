# SPDX-License-Identifier: Apache-2.0
# Phoenix Wing CAD Core, Copyright 2025-2026 上海锟钛.
"""Model: BOM rules, read-only snapshots and naming validation without Qt or FreeCAD imports."""

# compose_bom_label adapts pnwComposeBomLabel from @phoenix-wing/cad-core,
# packages/cad-core/src/index.ts (Apache-2.0); it does not execute TypeScript.
# Field names and the CustomDefine group follow the existing BOM contract.

import re

BOM_FIELDS = (
    ("PartNumber", "零件号"),
    ("PartVersion", "零件版本"),
    ("TypeCode", "类型码 H=一般件 S=标准件 R=虚拟零件"),
    ("ModelSeries", "型号系列"),
    ("PartName", "零件名称"),
)


BOM_GROUP = "CustomDefine"


class PftToolError(RuntimeError):
    pass


def compose_bom_label(fields):
    """Adapt pnwComposeBomLabel without padding, inference, or translation."""
    clean = lambda value: str(value if value is not None else "").strip()
    number = clean(fields.get("PartNumber"))
    version = clean(fields.get("PartVersion"))
    if not number or not version:
        return ""
    tail = clean(fields.get("PartName")) or clean(fields.get("label"))
    return "%s.%s%s" % (number, version, "-" + tail if tail else "")


def _property_snapshot(obj, field):
    if field not in obj.PropertiesList:
        return {"exists": False, "value": "", "type": ""}
    prop_type = obj.getTypeIdOfProperty(field)
    return {"exists": True, "value": getattr(obj, field), "type": prop_type}


def _expression_entries(obj):
    try:
        return list(obj.ExpressionEngine)
    except AttributeError:
        return []
    except Exception:
        raise PftToolError("无法检查对象表达式，请先结束当前编辑后重试。")


def _ensure_writable(obj, field, may_add=False):
    try:
        status_reader = getattr(obj, "getPropertyStatus")
        mode_reader = getattr(obj, "getEditorMode")
        if not callable(status_reader) or not callable(mode_reader):
            raise TypeError("native property access checks unavailable")
    except Exception:
        raise PftToolError("无法读取属性可写状态，已停止以免覆盖受保护属性。") from None
    current = _property_snapshot(obj, field)
    if not current["exists"]:
        if may_add:
            return
        raise PftToolError("%s 缺少 %s。" % (obj.Name, field))
    if current["type"] != "App::PropertyString":
        raise PftToolError("%s.%s 不是字符串属性，已停止以免覆盖原属性。" % (obj.Name, field))
    try:
        statuses = status_reader(field)
        editor_modes = mode_reader(field)
    except Exception:
        raise PftToolError("无法读取属性可写状态，已停止以免覆盖受保护属性。") from None
    # FreeCAD 1.1 returns status names or bit *indices*, not bit masks.
    # Static Prop_ReadOnly is index 24 and may not appear as a named status.
    status_names = {"Immutable", "ReadOnly", "Hidden", "Transient", "MaterialEdit",
                    "NoMaterialListEdit", "Output", "LockDynamic", "NoModify",
                    "PartialTrigger", "NoRecompute", "CopyOnChange", "UserEdit", "AllowPartial"}
    status_bits = {12, 13, 14, 15, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31}
    if (type(statuses) is not list or len(statuses) > 64
            or any(not (type(value) is str and value in status_names
                        or type(value) is int and value in status_bits) for value in statuses)
            or len(set(statuses)) != len(statuses)):
        raise PftToolError("属性状态格式不受支持，无法确认可写，已停止。")
    # Editor mode includes static and dynamic ReadOnly/Hidden flags.  It is
    # always a list of strings; integer compatibility would hide native locks.
    if (type(editor_modes) is not list or len(editor_modes) > 2
            or any(type(value) is not str or value not in {"ReadOnly", "Hidden"} for value in editor_modes)
            or len(set(editor_modes)) != len(editor_modes)):
        raise PftToolError("属性编辑模式格式不受支持，无法确认可写，已停止。")
    if (24 in statuses or any(value in statuses for value in ("ReadOnly", "Immutable", "LockDynamic"))
            or "ReadOnly" in editor_modes):
        raise PftToolError("%s.%s 为只读或锁定属性。" % (obj.Name, field))
    if any(str(path).split(".", 1)[0] == field for path, _ in _expression_entries(obj)):
        raise PftToolError("%s.%s 由表达式控制，已停止。" % (obj.Name, field))


def _snapshot(obj):
    return {
        "name": obj.Name,
        "label": obj.Label,
        "fields": {field: _property_snapshot(obj, field) for field, _ in BOM_FIELDS},
    }


def build_rename_preview(document, objects, all_documents=None):
    """Read-only preview; the caller must show every row before accepting."""
    rows = []
    documents = list(all_documents) if all_documents is not None else [document]
    for obj in objects:
        fields = {field: _property_snapshot(obj, field)["value"] for field, _ in BOM_FIELDS}
        missing = [field for field in ("PartNumber", "PartVersion", "PartName") if not str(fields[field]).strip()]
        new_label = compose_bom_label(fields) if not missing else ""
        row = {"name": obj.Name, "old_label": obj.Label, "new_label": new_label, "error": "", "object": obj}
        try:
            if missing:
                raise PftToolError("缺少 %s；请先填写零件属性。" % "、".join(missing))
            if any(ord(char) < 32 for char in new_label):
                raise PftToolError("属性含控制字符，请先修正。")
            _ensure_writable(obj, "Label")
            if new_label != obj.Label:
                token = "<<%s>>" % obj.Label
                for opened in documents:
                    for dependent in opened.Objects:
                        if any(token in str(expression) for _, expression in _expression_entries(dependent)):
                            raise PftToolError("存在引用此 Label 的表达式，需人工检查；本次不改名。")
        except PftToolError as error:
            row["error"] = str(error)
        rows.append(row)
    targets = {}
    for row in rows:
        if row["new_label"]:
            targets.setdefault(row["new_label"], []).append(row)
    for target, planned in targets.items():
        same = [obj for obj in document.Objects if obj.Label == target]
        for row in planned:
            conflicts = [obj.Name for obj in same if obj is not row["object"]]
            if len(planned) > 1 or conflicts:
                row["error"] = "目标 Label 重名冲突：%s" % "、".join(sorted(set(conflicts + [other["name"] for other in planned if other is not row])))
    return rows


def validate_final_rename_labels(document, rows):
    """Verify this preview's exact targets and collisions after synchronous callbacks."""
    try:
        for row in rows:
            obj = row["object"]
            if (document.getObject(row["name"]) is not obj or obj.Document is not document
                    or obj.Label != row["new_label"]):
                raise PftToolError("改名后的对象或 Label 与预览不一致；本次修改将回滚。")
            if any(other is not obj and other.Label == row["new_label"] for other in document.Objects):
                raise PftToolError("改名后发现目标 Label 重名冲突；本次修改将回滚。")
    except PftToolError:
        raise
    except Exception as error:
        raise PftToolError("无法复核改名后的对象或 Label；本次修改将回滚。") from error


def recommend_fields_from_label(label):
    """Accept a conservative complete Label, following the tool's numeric version padding.

    Existing tool references: PartNamingParser.parse_part_number_version_from_stem
    and bomNameRecommendation.assertValidPartName. This local subset deliberately
    rejects type/series prefixes and filenames instead of guessing their meaning.
    """
    text = str(label or "").strip()
    match = re.fullmatch(r"(\d+)\.(\d{1,3})-(.+)", text)
    if match is None or any(ord(char) < 32 for char in text):
        raise PftToolError("Label 不符合明确的“编号.版本-名称”，请手动填写。")
    name = match.group(3).strip()
    if not name or re.fullmatch(r"[A-Za-z]", name):
        raise PftToolError("零件名称为空或仅为单个英文字母，请手动填写。")
    if re.match(r"[HSR]-", name, re.IGNORECASE) or re.search(r"\.(?:FCStd|ASSY|Drawing)$", name, re.IGNORECASE):
        raise PftToolError("Label 含类型前缀或文件后缀，含义需人工确认；未推荐。")
    return {"PartNumber": match.group(1), "PartVersion": match.group(2).zfill(3), "PartName": name}


def recommend_property_values(snapshots, values):
    """Stage recommendations in a copy; keep every nonempty value untouched."""
    staged = {name: dict(fields) for name, fields in values.items()}
    notes = {}
    for snapshot in snapshots:
        name = snapshot["name"]
        try:
            proposal = recommend_fields_from_label(snapshot["label"])
            filled, conflicts = [], []
            for field, value in proposal.items():
                current = str(staged[name][field])
                if not current.strip():
                    staged[name][field] = value
                    filled.append(field)
                elif current != value:
                    conflicts.append("%s 推荐 %s（保留现值）" % (field, value))
            notes[name] = "；".join((["待补全：" + "、".join(filled)] if filled else ["已有值保留"]) + conflicts)
        except PftToolError as error:
            notes[name] = str(error)
    return staged, notes


def _is_link_target(obj):
    """Recognize Link subclasses; unreadable native type information is unsafe."""
    try:
        kind = getattr(obj, "TypeId", "")
        if not isinstance(kind, str):
            raise TypeError("invalid native type")
        if kind.startswith("App::Link"):
            return True
        derived = getattr(obj, "isDerivedFrom", None)
        if callable(derived):
            result = derived("App::Link")
            if type(result) is not bool:
                raise TypeError("invalid native inheritance result")
            return result
        return False
    except Exception as error:
        raise PftToolError("无法确认对象的 Link 类型，已停止以免误用来源。") from error


def _direct_link_source(link):
    """Resolve only a direct object or canonical single-target LinkSub value."""
    try:
        kind = getattr(link, "TypeId", "")
    except Exception as error:
        raise PftToolError("无法确认对象的 Link 类型，已停止以免误用来源。") from error
    if kind != "App::Link":
        raise PftToolError("仅支持选中的 App::Link 实例。")
    try:
        value = getattr(link, "LinkedObject", None)
    except (AttributeError, ReferenceError, RuntimeError):
        raise PftToolError("链接已断开或来源不可读。")
    subnames = ()
    if isinstance(value, (tuple, list)):
        if len(value) == 1:
            value = value[0]
        elif len(value) == 2 and hasattr(value[0], "Name"):
            source, raw_subnames = value
            if isinstance(raw_subnames, str):
                subnames = (raw_subnames,) if raw_subnames else ()
            elif isinstance(raw_subnames, (tuple, list)) and all(isinstance(name, str) for name in raw_subnames):
                subnames = tuple(name for name in raw_subnames if name)
            else:
                raise PftToolError("链接有多个来源候选或不支持的引用格式。")
            if len(subnames) > 1:
                raise PftToolError("链接引用多个子元素，来源含义需人工确认。")
            value = source
        else:
            raise PftToolError("链接有多个来源候选或不支持的引用格式。")
    source = value
    if source is None or not getattr(source, "Name", "") or getattr(source, "Document", None) is None:
        raise PftToolError("链接已断开或来源未加载。")
    if source is link or _is_link_target(source):
        raise PftToolError("链式链接来源需人工确认；本工具不沿链接猜测目标。")
    if source.Document.getObject(source.Name) is not source:
        raise PftToolError("链接来源对象已失效。")
    identity = "%s#%s" % (source.Document.Name, source.Name)
    return source, identity, subnames


def _ensure_label_references_safe(obj, documents):
    token = "<<%s>>" % obj.Label
    for document in documents:
        for dependent in document.Objects:
            if any(token in str(expression) for _, expression in _expression_entries(dependent)):
                raise PftToolError("存在引用此 Label 的表达式，需人工检查；本次不同步。")


def build_link_label_preview(document, objects, all_documents=None):
    """Preview instance Labels; equal names are allowed only for the same direct source."""
    documents = list(all_documents) if all_documents is not None else [document]
    rows = []
    for link in objects:
        row = {"name": link.Name, "old_label": link.Label, "new_label": "", "source_identity": "", "source_object": None, "subnames": (), "source_display": "", "error": "", "object": link}
        try:
            source, identity, subnames = _direct_link_source(link)
            source_label = getattr(source, "Label", "")
            if not isinstance(source_label, str) or not source_label.strip() or any(ord(char) < 32 for char in source_label):
                raise PftToolError("来源 Label 为空或含控制字符。")
            row.update(new_label=source_label, source_identity=identity, source_object=source, subnames=subnames,
                       source_display=identity + (" [" + subnames[0] + "]" if subnames else ""))
            if link.Label != source_label:
                _ensure_writable(link, "Label")
                _ensure_label_references_safe(link, documents)
        except PftToolError as error:
            row["error"] = str(error)
        rows.append(row)
    for row in rows:
        if row["error"] or row["old_label"] == row["new_label"]:
            continue
        conflicts = []
        for other in document.Objects:
            if other is row["object"] or other.Label != row["new_label"]:
                continue
            other_identity = "%s#%s" % (other.Document.Name, other.Name)
            if other_identity == row["source_identity"]:
                continue
            try:
                if _direct_link_source(other)[1] == row["source_identity"]:
                    continue
            except PftToolError:
                pass
            conflicts.append(other.Name)
        conflicts.extend(other["name"] for other in rows if other is not row and other["new_label"] == row["new_label"] and other["source_identity"] != row["source_identity"])
        if conflicts:
            row["error"] = "目标 Label 被无关对象或不同来源使用：%s" % "、".join(sorted(set(conflicts)))
    return rows


def validate_final_link_labels(document, rows):
    """Check actual sources and final collisions before duplicate-label permission is restored."""
    for row in rows:
        link = row["object"]
        if document.getObject(row["name"]) is not link or link.Label != row["new_label"]:
            raise PftToolError("同步后的链接或 Label 与预览不一致。")
        source, identity, subnames = _direct_link_source(link)
        if (source is not row["source_object"] or identity != row["source_identity"]
                or subnames != row["subnames"] or source.Label != row["new_label"]):
            raise PftToolError("同步时直接来源身份或 Label 发生变化，请重新预览。")
        for other in document.Objects:
            if other is link or other.Label != link.Label:
                continue
            if other is source:
                continue
            try:
                if _direct_link_source(other)[1] == identity:
                    continue
            except PftToolError:
                pass
            raise PftToolError("同步后发现无关对象或不同来源重名；本次修改将回滚。")
