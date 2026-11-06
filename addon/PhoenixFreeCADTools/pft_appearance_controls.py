# SPDX-License-Identifier: Apache-2.0
"""Small nonmodal appearance controls with one explicitly owned preview transaction."""
import copy
import json
from collections import deque
from functools import wraps
from pathlib import Path

from pft_workbench_i18n import tr
import pft_visual_tools as visual

TRANSLATABLE_STRINGS = (
    "对象外观", "随机配色", "轴测并适合窗口", "透明度", "确定", "取消", "混合",
    "不透明", "微透", "半透", "高透明",
    "0% 不透明，100% 完全透明", "Phoenix：预览所选对象外观",
    "外观事务已变化，未接管或取消其他操作。",
    "外观预览已结束，请重新打开。", "外观预览已取消。",
    "外观操作正在处理中，请稍后再试。",
    "外观预览失败，已恢复初始外观。", "外观回滚无法确认，请检查当前外观与撤销记录。",
    "请先选择当前文档中的完整对象。", "对象或外观已变化，未覆盖其他修改。",
    "检测到其他修改，未提交或取消这些修改。",
    "不能同时处理 Body 与其内部外观成员，请只选择 Body。",
    "外观事务提交后未确认关闭，请检查当前外观与撤销记录。",
    "外观事务提交期间检测到其他修改，请检查当前外观与撤销记录。",
)


def _transaction_id(app):
    value = app.getActiveTransaction()
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or type(value[1]) is not int or value[1] <= 0):
        return None
    return value[1]


_DIAGNOSTIC_SESSION_LIMIT = 12
_DIAGNOSTIC_EVENT_LIMIT = 64
_DIAGNOSTIC_PHASE_LIMIT = 32
_DIAGNOSTIC_LINK_SCAN_LIMIT = 2000
_recent_diagnostics = deque(maxlen=_DIAGNOSTIC_SESSION_LIMIT)
_DIAGNOSTIC_PROPERTIES = frozenset((
    "ShapeAppearance", "ShapeMaterial", "OverrideMaterial", "ShapeColor", "DiffuseColor",
    "Transparency", "LineColor", "PointColor", "Visibility", "DisplayMode", "Shape",
    "Label", "Description", "ModelDescription", "modelDescription", "Modified", "Touched", "Tip",
))
_SAFE_ERROR_CODES = frozenset((
    "DOCUMENT_BUSY", "DOCUMENT_NOT_ACTIVE", "DOCUMENT_REQUIRED", "DOCUMENT_STATE_UNKNOWN",
    "INVALID_TRANSPARENCY", "INVALID_VISUAL_ACTION", "LINK_MATERIAL_UNSUPPORTED",
    "LINK_SOURCE_SELECTED", "LINK_UNSUPPORTED", "MAIN_THREAD_REQUIRED", "MATERIAL_UNSUPPORTED",
    "UNDO_DISABLED", "VISUAL_PREVIEW_FAILED", "VISUAL_PREVIEW_TOO_LARGE", "VISUAL_ROLLBACK_UNVERIFIED",
    "VISUAL_SESSION_CONTAMINATED", "VISUAL_SESSION_ENDED", "VISUAL_SNAPSHOT_CHANGED",
    "VISUAL_TARGET_CHANGED", "VISUAL_TARGETS_REQUIRED", "VISUAL_TRANSACTION_CHANGED", "VISUAL_BODY_TIP_OVERLAP",
    "VISUAL_COMMIT_UNVERIFIED", "VISUAL_OPERATION_IN_PROGRESS",
))


def _safe_failure(error):
    # Never retain exception text, repr, traceback, object/document names or paths.
    name = type(error).__name__
    if not name.isascii() or len(name) > 64 or not name.isidentifier():
        name = "Exception"
    code = getattr(error, "code", None) if isinstance(error, visual.PftVisualToolsError) else None
    return {"type": name, "code": code if isinstance(code, str) and code in _SAFE_ERROR_CODES else "NATIVE_EXCEPTION"}


def get_appearance_diagnostics():
    """Return bounded, de-identified records, including closed/invalid sessions."""
    return copy.deepcopy(list(_recent_diagnostics))


def _observer_event(callback):
    def receive(self, *args):
        self._changed(*args, callback=callback)
    return receive


class _AppearanceObserver:
    """Conservatively notice edits that might join the same global transaction."""
    def __init__(self, session, provider="unknown"):
        self.session, self.provider = session, provider

    def _changed(self, *args, callback="other"):
        session = self.session
        active = ((session.transaction_id is not None or session._writing)
                  and session.state in ("ready", "previewing"))
        if active:
            session._foreign_change = True
        session._event(self.provider, callback, args[0] if args else None,
                       args[1] if len(args) > 1 else None, "foreign" if active else "outside_transaction")

    def _object_changed(self, obj, property_name, callback="changed_object"):
        session = self.session
        expected = session._expected_view_change
        # App/GUI may use different callback object wrappers. Preserve their
        # roles in diagnostics; do not infer new trusted identities from that.
        own_view = expected is not None and obj is expected[0]
        own_member = (isinstance(property_name, str) and property_name in ("ShapeAppearance", "Transparency")
                      and any(obj is view for view in session._expected_body_member_views))
        if (session._writing and expected is not None and isinstance(property_name, str)
                and ((own_view and property_name in expected[1]) or own_member)):
            session._event(self.provider, callback, obj, property_name, "expected")
            return
        self._changed(obj, property_name, callback=callback)

    def _document_changed(self, document, property_name, callback="changed_document"):
        session = self.session
        if (session._writing and session._expected_view_change is not None
                and document is session.document and property_name == "Modified"):
            session._event(self.provider, callback, document, property_name, "expected")
            return
        self._changed(document, property_name, callback=callback)

    def slotBeforeChangeObject(self, obj, property_name):
        self._object_changed(obj, property_name, "before_change_object")

    def slotChangedObject(self, obj, property_name):
        self._object_changed(obj, property_name, "changed_object")

    def slotBeforeChangeDocument(self, document, property_name):
        self._document_changed(document, property_name, "before_change_document")

    def slotChangedDocument(self, document, property_name):
        self._document_changed(document, property_name, "changed_document")

    slotCreatedObject = _observer_event("created_object")
    slotDeletedObject = _observer_event("deleted_object")
    slotRelabelDocument = _observer_event("relabel_document")
    slotCreatedDocument = _observer_event("created_document")
    slotDeletedDocument = _observer_event("deleted_document")
    slotAppendDynamicProperty = _observer_event("append_dynamic_property")
    slotRemoveDynamicProperty = _observer_event("remove_dynamic_property")
    slotInEdit = _observer_event("in_edit")
    slotUndoDocument = _observer_event("undo_document")
    slotRedoDocument = _observer_event("redo_document")
    slotRecomputedDocument = _observer_event("recomputed_document")
    slotRecomputedObject = _observer_event("recomputed_object")
    slotBeforeAddingDynamicExtension = _observer_event("before_adding_dynamic_extension")
    slotAddedDynamicExtension = _observer_event("added_dynamic_extension")


def _exclusive_mutation(method):
    @wraps(method)
    def invoke(session, *args, **kwargs):
        visual._main_thread()
        if session._mutating:
            raise session._error("VISUAL_OPERATION_IN_PROGRESS", "外观操作正在处理中，请稍后再试。")
        session._mutating = True
        try:
            return method(session, *args, **kwargs)
        finally:
            session._mutating = False
    return invoke


class PftAppearancePreviewSession:
    """Read on construction; preview writes stay in one native transaction.

    Transaction names are presentation only. Every later write, commit and abort
    checks the application transaction's numeric ID, the original document and
    the expected full native appearance. Losing ownership never closes a newer
    transaction. No save, recompute, selection or source-object writes occur.
    """
    def __init__(self, app, gui, document_name, object_names):
        self.app, self.gui, self.document_name = app, gui, document_name
        self.object_names = tuple(object_names) if isinstance(object_names, (tuple, list)) else object_names
        _, record = visual._build_appearance(app, gui, document_name, self.object_names,
                                             "set_transparency", 0)
        self.document = record["document"]
        self.entries = record["entries"]
        self.initial = [copy.deepcopy(entry["before"]) for entry in self.entries]
        self.body_tips, self.source_body_tips = self._body_tip_guards()
        self._member_initial = [copy.deepcopy(member["before"]) for guard in self.body_tips + self.source_body_tips
                                for member in guard["members"]]
        self._linked_instance_views = self._diagnostic_linked_instance_views()
        self.transaction_id = None
        self.state = "ready"
        self.preview_count = 0
        self._foreign_change, self._writing = False, False
        self._mutating = False
        self._expected_view_change = None
        self._expected_tip_view = None
        self._expected_body_member_views = ()
        self._observers = []
        self.owned_transaction_id = None  # Historical numeric ownership survives invalidation/closure.
        self._phase = "ready"
        self._diagnostic = {"events": [], "phases": [], "errors": [], "primary_error": None,
                            "rollback_error": None, "rollback_status": "not_attempted",
                            "commit_status": "not_attempted", "active_transaction_id": None}
        _recent_diagnostics.append(self._diagnostic)
        self._checkpoint("ready")

    def _member_snapshot(self, obj):
        # Companion verification needs the complete native material, not hidden
        # feature geometry. No Shape getter or recompute is needed for members.
        try:
            view = visual._get(obj, "ViewObject")
            if view.getTypeIdOfProperty("ShapeAppearance") != "App::PropertyMaterialList":
                raise ValueError()
            materials, transparency = view.ShapeAppearance, view.Transparency
            if (not isinstance(materials, (tuple, list)) or not materials
                    or len(materials) > visual.PFT_MAX_MATERIALS
                    or type(transparency) is not int or not 0 <= transparency <= 100):
                raise ValueError()
            return {"kind": "shape", "ShapeAppearance": [visual._material(value) for value in materials],
                    "Transparency": transparency}
        except visual.PftVisualToolsError:
            raise
        except Exception:
            raise self._error("MATERIAL_UNSUPPORTED", "所选对象没有受支持的原生材质或有效缓存 Shape。") from None

    def _is_body_feature(self, obj):
        # FreeCAD 1.1.0 ViewProviderBody::unifyVisualProperty only Pastes
        # into Group members derived from PartDesign::Feature. Sketches also
        # have ShapeAppearance but keep their independent native material.
        try:
            query = visual._get(obj, "isDerivedFrom")
            if not callable(query):
                raise ValueError()
            result = query("PartDesign::Feature")
            if type(result) is not bool:
                raise ValueError()
            return result
        except Exception:
            raise self._error("MATERIAL_UNSUPPORTED", "所选对象没有受支持的原生材质或有效缓存 Shape。") from None

    def _body_tip_guards(self):
        writable, readonly = [], []
        seen_sources = set()
        material_count = [0]

        def capture(body_entry):
            body = body_entry["object"]
            if visual._get(body, "TypeId") != "PartDesign::Body":
                return None
            group = visual._get(body, "Group")
            tip = visual._get(body, "Tip")
            if (not isinstance(group, (tuple, list)) or len(group) > _DIAGNOSTIC_LINK_SCAN_LIMIT
                    or len({id(member) for member in group}) != len(group)
                    or tip is not None and not any(tip is member for member in group)):
                raise self._error("VISUAL_TARGET_CHANGED", "视觉目标已关闭或变化，请刷新后重新选择。")
            members = []
            for member in group:
                name = visual._get(member, "Name")
                if (member is body or visual._get(member, "Document") is not self.document
                        or not visual._name(name) or self.document.getObject(name) is not member):
                    raise self._error("VISUAL_TARGET_CHANGED", "视觉目标已关闭或变化，请刷新后重新选择。")
                # Link instances never become writable Body companions.
                if str(visual._get(member, "TypeId", "")).startswith("App::Link"):
                    continue
                view = visual._get(member, "ViewObject")
                try:
                    supported = view is not None and view.getTypeIdOfProperty("ShapeAppearance") == "App::PropertyMaterialList"
                except Exception:
                    supported = False
                if not supported:
                    continue
                before = self._member_snapshot(member)
                material_count[0] += len(before["ShapeAppearance"])
                if material_count[0] > visual.PFT_MAX_MATERIALS:
                    raise self._error("VISUAL_PREVIEW_TOO_LARGE", "材质数量或完整预览超过限制，请减少目标。")
                members.append({"object": member, "view": view, "name": name,
                                "type": visual._get(member, "TypeId"), "before": before,
                                "propagates": self._is_body_feature(member)})
            tip_entry = next((member for member in members if member["object"] is tip), None)
            if tip is not None and (tip_entry is None or not tip_entry["propagates"]):
                raise self._error("MATERIAL_UNSUPPORTED", "所选对象没有受支持的原生材质或有效缓存 Shape。")
            return {"body": body_entry, "group": tuple(group), "tip_object": tip, "tip": tip_entry,
                    "members": members}

        for entry in self.entries:
            guard = capture(entry) if entry["before"]["kind"] == "shape" else None
            if guard is not None:
                writable.append(guard)
            source = entry.get("source")
            if source is not None and id(source["object"]) not in seen_sources:
                seen_sources.add(id(source["object"]))
                guard = capture(source)
                if guard is not None:
                    readonly.append(guard)
        selected = {id(entry["object"]) for entry in self.entries}
        companion_ids = [id(member["object"]) for guard in writable for member in guard["members"]]
        source_objects = {id(entry["source"]["object"]) for entry in self.entries if "source" in entry}
        if selected & set(companion_ids) or len(set(companion_ids)) != len(companion_ids):
            raise self._error("VISUAL_BODY_TIP_OVERLAP", "不能同时处理 Body 与其内部外观成员，请只选择 Body。")
        readonly_members = {id(member["object"]) for guard in readonly for member in guard["members"]}
        if set(companion_ids) & source_objects or selected & readonly_members or set(companion_ids) & readonly_members:
            raise self._error("LINK_SOURCE_SELECTED", "不能同时处理 Link 与其来源，请分开操作。")
        snapshots = [member["before"] for guard in writable + readonly for member in guard["members"]]
        if len(json.dumps(snapshots, allow_nan=False).encode("utf-8")) > visual.PFT_MAX_PREVIEW_BYTES:
            raise self._error("VISUAL_PREVIEW_TOO_LARGE", "材质数量或完整预览超过限制，请减少目标。")
        return writable, readonly

    def _diagnostic_linked_instance_views(self):
        # Metadata/identity only: diagnose local dependent instances without
        # reading their materials or granting them any write-event exemption.
        objects = visual._get(self.document, "Objects", ())
        if not isinstance(objects, (tuple, list)) or len(objects) > _DIAGNOSTIC_LINK_SCAN_LIMIT:
            return []
        sources = [entry["object"] for entry in self.entries]
        sources += [entry["source"]["object"] for entry in self.entries if "source" in entry]
        sources += [member["object"] for guard in self.body_tips + self.source_body_tips for member in guard["members"]]
        views = []
        for obj in objects:
            if visual._get(obj, "Document") is not self.document or visual._get(obj, "TypeId") != "App::Link":
                continue
            name = visual._get(obj, "Name")
            if not visual._name(name) or self.document.getObject(name) is not obj:
                continue
            source = visual._get(obj, "LinkedObject")
            if any(source is candidate for candidate in sources):
                view = visual._get(obj, "ViewObject")
                if view is not None:
                    views.append(view)
        return views

    def _check_body_tips(self, after_entries=None, initial=False):
        guards = self.body_tips + self.source_body_tips
        after = {id(entry["object"]): entry["after"] for entry in after_entries or ()}
        initial_index = 0
        for guard_index, guard in enumerate(guards):
            body_entry = guard["body"]
            group = visual._get(body_entry["object"], "Group")
            if (not visual._identical(self.document, body_entry)
                    or visual._get(body_entry["object"], "TypeId") != "PartDesign::Body"
                    or visual._get(body_entry["object"], "Tip") is not guard["tip_object"]
                    or not isinstance(group, (tuple, list)) or len(group) != len(guard["group"])
                    or any(current is not frozen for current, frozen in zip(group, guard["group"]))):
                raise self._error("VISUAL_SNAPSHOT_CHANGED", "外观快照在预览后变化，未执行修改。")
            for member in guard["members"]:
                expected = (self._member_initial[initial_index] if initial else
                            after[id(body_entry["object"])] if member["propagates"] and guard_index < len(self.body_tips)
                            and id(body_entry["object"]) in after else member["before"])
                initial_index += 1
                if (not visual._identical(self.document, member)
                        or self._is_body_feature(member["object"]) != member["propagates"]
                        or self._member_snapshot(member["object"]) != expected):
                    raise self._error("VISUAL_SNAPSHOT_CHANGED", "外观快照在预览后变化，未执行修改。")

    def _retain_tip_preview(self, entries):
        after = {id(entry["object"]): entry["after"] for entry in entries}
        for guard in self.body_tips:
            for member in guard["members"]:
                if member["propagates"]:
                    member["before"] = copy.deepcopy(after[id(guard["body"]["object"])])

    @property
    def diagnostics(self):
        self._update_diagnostics()
        return copy.deepcopy(self._diagnostic)

    def _update_diagnostics(self):
        self._diagnostic.update(state=self.state, phase=self._phase,
                                owned_transaction_id=self.owned_transaction_id,
                                transaction_id=self.transaction_id, foreign_change=self._foreign_change)

    def _checkpoint(self, phase):
        self._phase = phase
        self._diagnostic["phases"].append(phase)
        del self._diagnostic["phases"][:-_DIAGNOSTIC_PHASE_LIMIT]
        self._update_diagnostics()

    def _event(self, provider, callback, obj, property_name, decision):
        expected = self._expected_view_change
        role = "other"
        if expected is not None and obj is expected[0]:
            role = "current_view"
        elif self._expected_tip_view is not None and obj is self._expected_tip_view:
            role = "current_tip_view"
        elif any(obj is view for view in self._expected_body_member_views):
            role = "current_body_member_view"
        elif obj is self.document:
            role = "document"
        else:
            for entry in self.entries:
                if obj is entry["object"]:
                    role = "target_object"
                    break
                if obj is entry["view"]:
                    role = "target_view"
                    break
                source = entry.get("source")
                if source is not None:
                    if obj is source["object"]:
                        role = "source_object"
                        break
                    if obj is source["view"]:
                        role = "source_view"
                        break
            if role == "other":
                for guards, prefix in ((self.body_tips, "body_tip"), (self.source_body_tips, "source_body_tip")):
                    for guard in guards:
                        tip_entry = guard["tip"]
                        if tip_entry is not None and obj is tip_entry["object"]:
                            role = prefix + "_object"
                            break
                        if tip_entry is not None and obj is tip_entry["view"]:
                            role = prefix + "_view"
                            break
                        for member in guard["members"]:
                            if obj is member["object"]:
                                role = prefix.replace("tip", "member") + "_object"
                                break
                            if obj is member["view"]:
                                role = prefix.replace("tip", "member") + "_view"
                                break
                        if role != "other":
                            break
                    if role != "other":
                        break
            if role == "other" and any(obj is view for view in self._linked_instance_views):
                role = "linked_instance_view"
        event = {"provider": provider, "callback": callback, "role": role,
                 "property": property_name if isinstance(property_name, str) and property_name in _DIAGNOSTIC_PROPERTIES else "other",
                 "phase": self._phase, "writing": self._writing, "decision": decision}
        self._diagnostic["events"].append(event)
        del self._diagnostic["events"][:-_DIAGNOSTIC_EVENT_LIMIT]
        self._update_diagnostics()

    def _failure(self, kind, error):
        failure = {"phase": self._phase, **_safe_failure(error)}
        if self._diagnostic["primary_error"] is None and kind != "rollback":
            self._diagnostic["primary_error"] = failure
        if kind == "rollback":
            self._diagnostic["rollback_error"] = failure
        self._diagnostic["errors"].append({"kind": kind, **failure})
        del self._diagnostic["errors"][:-8]
        try:
            self._diagnostic["active_transaction_id"] = _transaction_id(self.app)
        except Exception:
            self._diagnostic["active_transaction_id"] = None
        self._update_diagnostics()

    def _diagnosed_error(self, error):
        # Capture again after rollback: a verified abort must not report the
        # formerly active ID as though the global transaction were still open.
        try:
            self._diagnostic["active_transaction_id"] = _transaction_id(self.app)
        except Exception:
            self._diagnostic["active_transaction_id"] = None
        error.appearance_diagnostics = self.diagnostics
        return error

    def _error(self, code, message):
        return visual.PftVisualToolsError(code, message)

    def _ensure_open(self):
        visual._main_thread()
        if self.state not in ("ready", "previewing"):
            raise self._error("VISUAL_SESSION_ENDED", "外观预览已结束，请重新打开。")

    def _owner(self):
        self._ensure_open()
        if self.transaction_id is None or _transaction_id(self.app) != self.transaction_id:
            raise self._error("VISUAL_TRANSACTION_CHANGED", "外观事务已变化，未接管或取消其他操作。")
        if self._foreign_change:
            raise self._error("VISUAL_SESSION_CONTAMINATED", "检测到其他修改，未提交或取消这些修改。")
        if self.app.getDocument(self.document_name) is not self.document:
            raise self._error("VISUAL_TARGET_CHANGED", "视觉目标已关闭或变化，请刷新后重新选择。")
        # The document getter can synchronously replace the global transaction.
        # Read its numeric ID last, before the remaining process-local checks.
        if _transaction_id(self.app) != self.transaction_id:
            raise self._error("VISUAL_TRANSACTION_CHANGED", "外观事务已变化，未接管或取消其他操作。")
        # A native ownership getter can synchronously invalidate the session or
        # report a foreign edit. Never revive a terminal session after it returns.
        self._ensure_open()
        if self._foreign_change:
            raise self._error("VISUAL_SESSION_CONTAMINATED", "检测到其他修改，未提交或取消这些修改。")

    def _current(self, require_active=True):
        self._ensure_open()
        if self.transaction_id is None:
            document, _ = visual._context(self.app, self.gui, self.document_name, undo=True)
            if document is not self.document:
                raise self._error("VISUAL_TARGET_CHANGED", "视觉目标已关闭或变化，请刷新后重新选择。")
        else:
            self._owner()
            if require_active:
                visual._context(self.app, self.gui, self.document_name, undo=True,
                                owned_transaction_id=self.transaction_id)
        visual._check_before(self.document, self.entries)
        self._check_body_tips()

    def validate(self):
        """Read-only validity check for the panel's lifecycle timer."""
        self._current()
        return True

    def transparency(self):
        """Return an integer percent, or None for mixed native material values."""
        self._current()
        values = []
        for entry in self.entries:
            before = entry["before"]
            if before["kind"] == "link":
                effective = (before["ShapeMaterial"] if before["OverrideMaterial"]
                             else entry["source"]["before"]["ShapeAppearance"][0])
                values.append(effective["Transparency"] * 100)
            else:
                values.extend(material["Transparency"] * 100 for material in before["ShapeAppearance"])
                values.append(before["Transparency"])
        first = values[0]
        return round(first) if all(abs(value - first) < 0.00001 for value in values) else None

    def _summary(self, action=None):
        return {"document": self.document_name, "action": action or "appearance",
                "target_count": len(self.entries),
                "changed_count": sum(before != entry["before"] for before, entry in zip(self.initial, self.entries)),
                "saved": False, "recomputed": False, "replayed": False}

    def _attach_observers(self):
        # App and GUI callbacks are additional conservative edit detectors;
        # ownership still depends on the native numeric transaction ID.
        for provider_name, provider in (("app", self.app), ("gui", self.gui)):
            add = getattr(provider, "addDocumentObserver", None)
            remove = getattr(provider, "removeDocumentObserver", None)
            if not callable(add) or not callable(remove):
                self._detach_observers()
                raise self._error("DOCUMENT_STATE_UNKNOWN", "无法确认文档事务或编辑状态，未执行视觉操作。")
            observer = _AppearanceObserver(self, provider_name)
            try:
                add(observer)
            except Exception:
                self._detach_observers()
                raise self._error("DOCUMENT_STATE_UNKNOWN", "无法确认文档事务或编辑状态，未执行视觉操作。") from None
            self._observers.append((provider, observer))

    def _detach_observers(self):
        observers, self._observers = self._observers, []
        for provider, observer in observers:
            try:
                provider.removeDocumentObserver(observer)
            except Exception:
                pass

    def invalidate(self):
        self.state = "invalid"
        self._detach_observers()
        self._checkpoint("invalid")

    def _open_transaction(self):
        if not all(callable(getattr(self.app, name, None)) for name in
                   ("setActiveTransaction", "closeActiveTransaction")):
            raise self._error("DOCUMENT_STATE_UNKNOWN", "无法确认文档事务或编辑状态，未执行视觉操作。")
        self._attach_observers()
        self._writing = True
        try:
            # Plan construction and observer installation call native getters.
            # Recheck idle/identity after those callbacks, before opening an ID.
            self._current()
            if self.app.getActiveTransaction() is not None:
                raise self._error("DOCUMENT_BUSY", "当前文档正在编辑或已有未完成事务，请先完成或取消。")
            # Document.openTransaction is void. Only the native allocation result
            # identifies this operation if an opening callback replaces the ID.
            transaction_id = self.app.setActiveTransaction("Phoenix：预览所选对象外观")
            if type(transaction_id) is not int or transaction_id <= 0:
                raise self._error("VISUAL_TRANSACTION_CHANGED", "外观事务已变化，未接管或取消其他操作。")
            self.transaction_id = transaction_id
            self.owned_transaction_id = transaction_id
            self._update_diagnostics()
            self._owner()
        except Exception as error:
            # A failed/unknown open does not authorize adopting whichever ID is
            # current or attempting a compensating close on it.
            reported = (error if isinstance(error, visual.PftVisualToolsError) else
                        self._error("VISUAL_ROLLBACK_UNVERIFIED", "外观回滚无法确认，请检查当前外观与撤销记录。"))
            self._failure("primary", reported)
            self.invalidate()
            raise reported from None
        finally:
            self._writing = False

    def _restore_initial_check(self):
        expected = []
        for entry, before in zip(self.entries, self.initial):
            value = dict(entry)
            value["before"] = before
            expected.append(value)
        visual._check_before(self.document, expected)
        self._check_body_tips(initial=True)

    def _abort(self, check_current=True):
        self._checkpoint("rollback_validate")
        self._owner()
        if check_current:
            visual._check_before(self.document, self.entries)
            self._check_body_tips()
        self._writing = True
        self._diagnostic["rollback_status"] = "unverified"
        self._checkpoint("rollback_abort")
        try:
            # Snapshot reads above may synchronously report foreign edits.
            # Confirm ownership again immediately before the bound native close.
            self._owner()
            self.app.closeActiveTransaction(True, self.transaction_id)
            self._checkpoint("rollback_close_verify")
            visual._check_transaction_closed(self.app, self.transaction_id)
            self._checkpoint("rollback_verify")
            self._restore_initial_check()
            visual._check_transaction_closed(self.app, self.transaction_id)
            self._ensure_open()
        except Exception as error:
            self._diagnostic["rollback_status"] = "unverified"
            reported = (error if isinstance(error, visual.PftVisualToolsError) else
                        self._error("VISUAL_ROLLBACK_UNVERIFIED", "外观回滚无法确认，请检查当前外观与撤销记录。"))
            self._failure("rollback", reported)
            self.invalidate()
            raise self._diagnosed_error(reported) from None
        finally:
            self._writing = False
        self.transaction_id = None
        for entry, before in zip(self.entries, self.initial):
            entry["before"] = copy.deepcopy(before)
        members = [member for guard in self.body_tips + self.source_body_tips for member in guard["members"]]
        for member, before in zip(members, self._member_initial):
            member["before"] = copy.deepcopy(before)
        self._detach_observers()
        self._diagnostic["rollback_status"] = "verified"
        self._update_diagnostics()

    def _write_view_property(self, view, property_name, value):
        # These legacy aliases are native projections of ShapeAppearance or
        # ShapeMaterial. Full target/source snapshots still verify their result.
        coupled = {"ShapeAppearance", "ShapeMaterial", "ShapeColor", "DiffuseColor", "Transparency"}
        allowed = coupled if property_name in {"ShapeAppearance", "ShapeMaterial"} else {property_name}
        self._expected_view_change = (view, allowed)
        self._expected_tip_view = None
        self._expected_body_member_views = ()
        if property_name == "ShapeAppearance":
            for guard in self.body_tips:
                if view is guard["body"]["view"]:
                    self._expected_tip_view = guard["tip"]["view"] if guard["tip"] is not None else None
                    self._expected_body_member_views = tuple(member["view"] for member in guard["members"]
                                                             if member["propagates"])
                    break
        try:
            # Native material construction can call back after the outer loop's
            # guard; recheck immediately around every actual view setter.
            self._owner()
            if self.app.ActiveDocument is not self.document:
                raise self._error("DOCUMENT_NOT_ACTIVE", "活动文档已变化，请刷新后重试。")
            setattr(view, property_name, value)
            self._owner()
            if self.app.ActiveDocument is not self.document:
                raise self._error("DOCUMENT_NOT_ACTIVE", "活动文档已变化，请刷新后重试。")
        finally:
            self._expected_view_change = None
            self._expected_tip_view = None
            self._expected_body_member_views = ()

    @_exclusive_mutation
    def _preview(self, action, transparency=None):
        rollback_permitted = False
        if self.state in ("ready", "previewing"):
            self._diagnostic.update(primary_error=None, rollback_error=None, rollback_status="not_attempted")
        try:
            self._checkpoint("preview_validate")
            self._current()
            self._checkpoint("build_plan")
            _, record = visual._build_appearance(self.app, self.gui, self.document_name, self.object_names,
                                                 action, transparency, owned_transaction_id=self.transaction_id)
            entries = record["entries"]
            if all(entry["before"] == entry["after"] for entry in entries):
                self._checkpoint("unchanged")
                return self._summary(action)
            if self.transaction_id is None:
                self._checkpoint("transaction_open")
                self._open_transaction()
            rollback_permitted = True
            self._writing = True
            for entry in entries:
                self._checkpoint("write_validate")
                self._owner()
                if self.app.ActiveDocument is not self.document:
                    raise self._error("DOCUMENT_NOT_ACTIVE", "活动文档已变化，请刷新后重试。")
                view, after = entry["view"], entry["after"]
                if after["kind"] == "shape":
                    self._checkpoint("write_shape_appearance")
                    self._write_view_property(view, "ShapeAppearance", [visual._clone(self.app, material)[0]
                                                                       for material in after["ShapeAppearance"]])
                else:
                    self._checkpoint("write_shape_material")
                    self._write_view_property(view, "ShapeMaterial", visual._clone(self.app, after["ShapeMaterial"])[0])
                    self._owner()
                    self._checkpoint("write_override_material")
                    self._write_view_property(view, "OverrideMaterial", True)
            expected = []
            for entry in entries:
                value = dict(entry)
                value["before"] = copy.deepcopy(entry["after"])
                expected.append(value)
            self._checkpoint("verify_after")
            self._owner()
            visual._check_before(self.document, expected)
            self._check_body_tips(after_entries=entries)
            self._owner()
            self._retain_tip_preview(entries)
            self.entries = expected
            self.state = "previewing"
            self.preview_count += 1
            self._checkpoint("previewed")
            return self._summary(action)
        except Exception as error:
            self._failure("primary", error)
            if not rollback_permitted:
                # A failed preflight must never abort earlier previews or edits
                # made outside this operation, even if a numeric ID is retained.
                raise self._diagnosed_error(error) from None
            try:
                self._abort(check_current=False)
            except Exception as rollback_error:
                self._failure("rollback", rollback_error)
                self._diagnostic["rollback_status"] = "unverified"
                self.invalidate()
                # Keep a known primary code/message, rather than replacing every
                # original cause with the same rollback wrapper.
                reported = (error if isinstance(error, visual.PftVisualToolsError) else
                            self._error("VISUAL_ROLLBACK_UNVERIFIED", "外观回滚无法确认，请检查当前外观与撤销记录。"))
                raise self._diagnosed_error(reported) from None
            self.state = "cancelled"
            self._checkpoint("preview_failed_restored")
            raise self._diagnosed_error(self._error("VISUAL_PREVIEW_FAILED", "外观预览失败，已恢复初始外观。")) from None
        finally:
            self._writing = False
            self._update_diagnostics()

    def preview_random_color(self):
        return self._preview("random_color")

    def preview_transparency(self, value):
        return self._preview("set_transparency", value)

    @_exclusive_mutation
    def accept(self):
        self._current()
        summary = self._summary()
        if self.transaction_id is not None:
            if summary["changed_count"]:
                self._owner()
                self._diagnostic["commit_status"] = "unverified"
                self._checkpoint("commit_transaction")
                try:
                    self._writing = True
                    try:
                        self._owner()
                        self.app.closeActiveTransaction(False, self.transaction_id)
                    finally:
                        self._writing = False
                    self._checkpoint("commit_close_verify")
                    visual._check_transaction_closed(self.app, self.transaction_id, committing=True)
                    # Closing the native transaction is not proof that its
                    # synchronous callbacks stayed within our appearance edit.
                    # It is already closed: report the contamination and retain
                    # historical ownership without issuing another native call.
                    if self._foreign_change:
                        self._diagnostic["commit_status"] = "closed_contaminated"
                        raise self._error("VISUAL_SESSION_CONTAMINATED", "外观事务提交期间检测到其他修改，请检查当前外观与撤销记录。")
                    if self.app.getDocument(self.document_name) is not self.document:
                        raise self._error("VISUAL_TARGET_CHANGED", "视觉目标已关闭或变化，请刷新后重新选择。")
                    visual._check_before(self.document, self.entries)
                    self._check_body_tips()
                    visual._check_transaction_closed(self.app, self.transaction_id, committing=True)
                    self._ensure_open()
                except Exception as error:
                    reported = (error if isinstance(error, visual.PftVisualToolsError) and error.code in (
                        "VISUAL_TRANSACTION_CHANGED", "VISUAL_COMMIT_UNVERIFIED", "VISUAL_SESSION_CONTAMINATED") else
                        self._error("VISUAL_COMMIT_UNVERIFIED", "外观事务提交后未确认关闭，请检查当前外观与撤销记录。"))
                    self._failure("commit", reported)
                    self.invalidate()
                    # Neither another commit nor an abort is authorized once
                    # native closure is uncertain or a foreign ID has appeared.
                    raise self._diagnosed_error(reported) from None
                self.transaction_id = None
                self._diagnostic["commit_status"] = "verified"
            else:
                self._abort()
        self._ensure_open()
        self.state = "accepted"
        self._detach_observers()
        self._checkpoint("accepted")
        return summary

    @_exclusive_mutation
    def cancel(self):
        self._ensure_open()
        if self.transaction_id is not None:
            # Switching documents may cancel our transaction only while its ID
            # and the exact expected appearance still prove it is ours.
            self._current(require_active=False)
            self._abort()
        self._ensure_open()
        self.state = "cancelled"
        self._detach_observers()
        self._checkpoint("cancelled")
        return self._summary()


def apply_random_color(app, gui, document_name, objects):
    """Direct single-action helper, retaining the original backend contract."""
    plan = visual.prepare_appearance(app, gui, document_name, objects, "random_color")
    return visual.apply_appearance(app, gui, plan)


def _qt():
    from PySide import QtCore, QtGui
    try:
        from PySide import QtWidgets
    except ImportError:
        from PySide import QtGui as QtWidgets
    return QtCore, QtGui, QtWidgets


def _enum(owner, group, name):
    direct = getattr(owner, name, None)
    return direct if direct is not None else getattr(getattr(owner, group, None), name, None)


def _selected_scope(app, gui):
    document = app.ActiveDocument
    selected = list(gui.Selection.getSelection())
    if (document is None or not selected or any(obj.Document is not document for obj in selected)
            or any(getattr(item, "SubElementNames", ()) for item in gui.Selection.getSelectionEx())):
        raise visual.PftVisualToolsError("VISUAL_TARGETS_REQUIRED", "请先选择当前文档中的完整对象。")
    return document.Name, [obj.Name for obj in selected]


def show_appearance_panel(app, gui, parent=None, scope=None, get_scope=None,
                          on_applied=None, on_error=None):
    """Show a small nonmodal panel for a fixed explicit scope; return its dialog."""
    core, qtgui, widgets = _qt()
    if parent is not None:
        for existing in parent.findChildren(widgets.QDialog, "PhoenixTools_AppearancePanel"):
            if existing.isVisible():
                if getattr(getattr(existing, "_pft_session", None), "state", None) in ("ready", "previewing"):
                    existing.raise_()
                    existing.activateWindow()
                    return existing
                existing.close()
    if scope is None:
        scope = get_scope() if get_scope is not None else _selected_scope(app, gui)
    if not isinstance(scope, (tuple, list)) or len(scope) != 2:
        raise visual.PftVisualToolsError("VISUAL_TARGETS_REQUIRED", "请先选择当前文档中的完整对象。")
    session = PftAppearancePreviewSession(app, gui, scope[0], scope[1])
    ended = [False]

    class AppearanceDialog(widgets.QDialog):
        def accept(self):
            if session._mutating:
                return
            if ended[0] or session.state not in ("ready", "previewing"):
                finish_preview()
                return
            try:
                summary = session.accept()
            except Exception as error:
                report(error)
                if session.state not in ("ready", "previewing"):
                    finish_preview()
                return
            finish_preview()
            try:
                if on_applied is not None:
                    on_applied(summary)
            finally:
                super().accept()

        def reject(self):
            if session._mutating:
                return
            try:
                if session.state in ("ready", "previewing"):
                    session.cancel()
            except Exception as error:
                report(error)
                # Ownership loss is terminal: leave the external transaction alone.
                session.invalidate()
            finish_preview()
            super().reject()

        def closeEvent(self, event):
            if session._mutating:
                # A close while a native call is reentering cannot discard its
                # live transaction. Do not queue or replay the close later.
                event.ignore()
                return
            self.reject()
            event.accept()

    dialog = AppearanceDialog(parent)
    dialog.setObjectName("PhoenixTools_AppearancePanel")
    dialog.setWindowTitle(tr("对象外观"))
    dialog.setModal(False)
    delete_on_close = _enum(core.Qt, "WidgetAttribute", "WA_DeleteOnClose")
    if delete_on_close is not None:
        dialog.setAttribute(delete_on_close, True)
    dialog.resize(300, 140)
    dialog._pft_session = session
    layout = widgets.QVBoxLayout(dialog)
    layout.setContentsMargins(10, 8, 10, 8)
    random_button = widgets.QPushButton(tr("随机配色"), dialog)
    random_button.setObjectName("PhoenixTools_AppearanceRandomColor")
    if callable(getattr(random_button, "setAutoDefault", None)):
        random_button.setAutoDefault(False)
    random_row = widgets.QHBoxLayout()
    random_row.addWidget(random_button, 1)
    view_button = widgets.QToolButton(dialog)
    view_button.setObjectName("PhoenixTools_AppearanceAxonometricFit")
    view_button.setToolTip(tr("轴测并适合窗口"))
    view_button.setAccessibleName(tr("轴测并适合窗口"))
    view_button.setIcon(qtgui.QIcon(str(Path(__file__).resolve().parent / "Resources" / "icons" / "axonometric-fit.svg")))
    view_button.setToolButtonStyle(_enum(core.Qt, "ToolButtonStyle", "ToolButtonIconOnly"))
    random_row.addWidget(view_button)
    layout.addLayout(random_row)
    row = widgets.QHBoxLayout()
    row.addWidget(widgets.QLabel(tr("透明度"), dialog))
    slider = widgets.QSlider(dialog)
    slider.setObjectName("PhoenixTools_AppearanceTransparencySlider")
    slider.setOrientation(_enum(core.Qt, "Orientation", "Horizontal"))
    slider.setRange(0, 100)
    slider.setToolTip(tr("0% 不透明，100% 完全透明"))
    row.addWidget(slider)
    spin = widgets.QSpinBox(dialog)
    spin.setObjectName("PhoenixTools_AppearanceTransparencyValue")
    spin.setRange(0, 100)
    spin.setSuffix("%")
    spin.setKeyboardTracking(False)
    row.addWidget(spin)
    layout.addLayout(row)
    preset_row = widgets.QHBoxLayout()
    preset_buttons = {}
    for value, label in ((0, "不透明"), (25, "微透"), (50, "半透"), (75, "高透明")):
        button = widgets.QToolButton(dialog)
        button.setObjectName("PhoenixTools_AppearanceTransparencyPreset%d" % value)
        button.setText(tr(label))
        button.setToolTip("%s: %d%%" % (tr("透明度"), value))
        button.setCheckable(True)
        button.setSizePolicy(_enum(widgets.QSizePolicy, "Policy", "Expanding"),
                             _enum(widgets.QSizePolicy, "Policy", "Fixed"))
        preset_buttons[value] = button
        preset_row.addWidget(button, 1)
    layout.addLayout(preset_row)
    status = widgets.QLabel("", dialog)
    status.setObjectName("PhoenixTools_AppearanceStatus")
    status.setWordWrap(True)
    layout.addWidget(status)
    buttons = widgets.QDialogButtonBox(
        _enum(widgets.QDialogButtonBox, "StandardButton", "Ok")
        | _enum(widgets.QDialogButtonBox, "StandardButton", "Cancel"), dialog)
    buttons.setObjectName("PhoenixTools_AppearanceButtons")
    buttons.button(_enum(widgets.QDialogButtonBox, "StandardButton", "Ok")).setText(tr("确定"))
    buttons.button(_enum(widgets.QDialogButtonBox, "StandardButton", "Ok")).setDefault(True)
    buttons.button(_enum(widgets.QDialogButtonBox, "StandardButton", "Cancel")).setText(tr("取消"))
    layout.addWidget(buttons)
    updating = [False]

    def finish_preview():
        if ended[0]:
            return
        ended[0] = True
        random_button.setEnabled(False)
        view_button.setEnabled(False)
        slider.setEnabled(False)
        spin.setEnabled(False)
        for button in preset_buttons.values():
            button.setEnabled(False)
        buttons.button(_enum(widgets.QDialogButtonBox, "StandardButton", "Ok")).setEnabled(False)
        timer.stop()

    def sync(value):
        updating[0] = True
        try:
            slider.setValue(value if value is not None else 0)
            spin.setValue(value if value is not None else 0)
            for percent, button in preset_buttons.items():
                button.setChecked(value == percent)
            status.setText(tr("混合") if value is None else "")
        finally:
            updating[0] = False

    def report(error):
        if ended[0]:
            return
        session._failure("reported", error)
        message = getattr(error, "message", None)
        message = (tr(message) if message in (visual.TRANSLATABLE_STRINGS + TRANSLATABLE_STRINGS)
                   else tr("对象或外观已变化，未覆盖其他修改。"))
        status.setText(message)
        # Reject/close can delete the dialog immediately after a failed cancel.
        # Preserve the sanitized warning outside the disappearing panel.
        try:
            printer = getattr(getattr(app, "Console", None), "PrintError", None)
            if callable(printer):
                printer("[Phoenix Tools] " + message + "\n")
                diagnostic = session.diagnostics
                safe_summary = {key: diagnostic[key] for key in (
                    "state", "phase", "owned_transaction_id", "transaction_id", "active_transaction_id",
                    "primary_error", "rollback_error", "rollback_status", "commit_status")}
                printer("[Phoenix Tools] appearance diagnostics: " + json.dumps(safe_summary, sort_keys=True) + "\n")
        except Exception:
            pass
        try:
            get_window = getattr(gui, "getMainWindow", None)
            window = get_window() if callable(get_window) else parent
            get_bar = getattr(window, "statusBar", None)
            if callable(get_bar):
                get_bar().showMessage(message, 15000)
        except Exception:
            pass
        if on_error is not None:
            try:
                on_error(error)
            except Exception:
                pass

    def preview(action, value=None):
        if updating[0]:
            return
        if ended[0] or session.state not in ("ready", "previewing"):
            finish_preview()
            return
        try:
            if action == "random_color":
                session.preview_random_color()
            else:
                session.preview_transparency(value)
            sync(session.transparency())
        except Exception as error:
            report(error)
            if session.state not in ("ready", "previewing"):
                finish_preview()

    def adjust_view(*_):
        if ended[0] or session.state not in ("ready", "previewing"):
            finish_preview()
            return
        try:
            # Fixed scope must remain active and idle, allowing only this
            # session's own preview transaction. Never activate another doc.
            session.validate()
        except Exception as error:
            report(error)
            return
        try:
            from pft_workbench_tools import run_axonometric_fit
            run_axonometric_fit(app, gui)
        except Exception:
            report(visual.PftVisualToolsError("VIEW_ACTION_FAILED", "原生视图操作失败。"))

    def slider_changed(value):
        if updating[0] or ended[0]:
            return
        updating[0] = True
        try:
            spin.setValue(value)
        finally:
            updating[0] = False
        preview("set_transparency", value)

    def spin_changed(value):
        if updating[0] or ended[0]:
            return
        preview("set_transparency", value)

    sync(session.transparency())
    slider.valueChanged.connect(slider_changed)
    spin.valueChanged.connect(spin_changed)
    random_button.clicked.connect(lambda *_: preview("random_color"))
    view_button.clicked.connect(adjust_view)
    for value, button in preset_buttons.items():
        # Direct preview also handles a mixed state whose slider already shows 0.
        button.clicked.connect(lambda _checked=False, percent=value: preview("set_transparency", percent))
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    timer = core.QTimer(dialog)
    timer.setInterval(250)

    def guard():
        if session._mutating:
            return
        if ended[0] or session.state not in ("ready", "previewing"):
            finish_preview()
            return
        try:
            session.validate()
        except Exception as error:
            report(error)
            # Only cancel while the exact ID and snapshots remain owned.
            try:
                session.cancel()
            except Exception:
                session.invalidate()
            finish_preview()

    timer.timeout.connect(guard)
    timer.start()
    dialog._pft_guard_timer = timer
    dialog.finished.connect(lambda *_: timer.stop())
    dialog.show()
    return dialog
