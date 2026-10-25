# SPDX-License-Identifier: Apache-2.0
"""Read-only document identity, observed-event counters and native context probes.

Counters begin when this module attaches its observer. They are partial read
consistency diagnostics, never complete revision IDs or guards for writes.
No FreeCAD or Qt module is imported and no model operation is performed here.
"""

import secrets

REVISION_SCOPE = "observed_document_events_since_registration"
PROVENANCE = {
    "source": "live_native_document", "geometry_freshness": "not_verified",
    "recomputed": False, "write_guard": False,
}


def read_attribute(value, name):
    if value is None:
        return None, "unavailable"
    try:
        return getattr(value, name), "available"
    except AttributeError:
        return None, "unavailable"
    except Exception:
        return None, "read_failed"


def _name(value):
    name, _ = read_attribute(value, "Name")
    return name if isinstance(name, str) else None


class _PftDocumentObserver:
    def __init__(self, tracker):
        self.tracker = tracker

    def _document_event(self, document):
        if self.tracker.observer is self:
            self.tracker.event(document)

    def _object_event(self, obj):
        if self.tracker.observer is self:
            self.tracker.object_event(obj)

    def slotCreatedDocument(self, document): self._document_event(document)
    def slotDeletedDocument(self, document):
        if self.tracker.observer is self:
            self.tracker.deleted(document)
    def slotChangedDocument(self, document, _property): self._document_event(document)
    def slotRelabelDocument(self, document): self._document_event(document)
    def slotUndoDocument(self, document): self._document_event(document)
    def slotRedoDocument(self, document): self._document_event(document)
    def slotRecomputedDocument(self, document): self._document_event(document)
    def slotCreatedObject(self, obj): self._object_event(obj)
    def slotDeletedObject(self, obj): self._object_event(obj)
    def slotChangedObject(self, obj, _property): self._object_event(obj)
    def slotRecomputedObject(self, obj): self._object_event(obj)
    def slotAppendDynamicProperty(self, container, _property): self._object_event(container)
    def slotRemoveDynamicProperty(self, container, _property): self._object_event(container)


class PftDocumentStateTracker:
    def __init__(self):
        self.app = None
        self.observer = None
        self.documents = {}
        self.status = "observer_unavailable"
        self.enabled = False

    def close(self):
        # Disable before unregistering: a failed removal must leave a harmless
        # observer, rather than two active counters after a module reload.
        self.enabled = False
        if self.observer is not None:
            remove, _ = read_attribute(self.app, "removeDocumentObserver")
            if callable(remove):
                try:
                    remove(self.observer)
                except Exception:
                    pass
        self.documents.clear()
        self.observer = None
        self.app = None

    def attach(self, app):
        if self.app is app:
            return
        self.close()
        self.app = app
        add, _ = read_attribute(app, "addDocumentObserver")
        self.status = "observer_unavailable"
        if not callable(add):
            return
        observer = _PftDocumentObserver(self)
        self.observer = observer
        self.enabled = True
        try:
            add(observer)
            self.status = "observing" if self.enabled else "observer_unavailable"
        except Exception:
            self.enabled = False
            self.status = "registration_failed"

    def entry(self, document):
        name = _name(document)
        current = self.documents.get(name)
        if current is None or current["document"] is not document:
            current = {"document": document, "token": secrets.token_urlsafe(24), "revision": 0}
            self.documents[name] = current
        return current

    def _current(self, document):
        name, status = read_attribute(document, "Name")
        if status != "available" or not isinstance(name, str) or not name:
            raise RuntimeError("document identity unavailable")
        getter, status = read_attribute(self.app, "getDocument")
        if status == "read_failed":
            raise RuntimeError("document lookup unavailable")
        if callable(getter):
            return getter(name) is document
        entry = self.documents.get(name)
        return entry is None or entry["document"] is document

    def event(self, document):
        if not self.enabled:
            return
        try:
            if self._current(document):
                self.entry(document)["revision"] += 1
        except Exception:
            # Native callbacks must never throw through the GUI observer. If a
            # callback cannot be tracked, stop claiming a usable counter.
            self.status = "observer_unavailable"
            self.enabled = False

    def object_event(self, obj):
        document, status = read_attribute(obj, "Document")
        if status == "available" and document is not None:
            self.event(document)
        elif callable(read_attribute(obj, "getObject")[0]):
            self.event(obj)  # Dynamic properties may belong to the document.
        elif self.enabled:
            self.status = "observer_unavailable"
            self.enabled = False

    def deleted(self, document):
        if not self.enabled:
            return
        name = _name(document)
        entry = self.documents.get(name)
        if entry is not None and entry["document"] is document:
            self.documents.pop(name, None)

    def snapshot(self, document, app):
        self.attach(app)
        entry = self.entry(document)
        observing = self.status == "observing" and self.enabled
        return {
            "document_instance_token": entry["token"], "instance_token": entry["token"],
            "revision": entry["revision"] if observing else None,
            "revision_scope": REVISION_SCOPE,
            "revision_coverage": "partial" if observing else "unknown",
            "revision_status": self.status,
            "provenance": dict(PROVENANCE),
        }


# Explicit importlib.reload creates a fresh observation epoch and invalidates
# tokens. Ordinary operations-module reloads reuse this module's cached tracker.
_previous = globals().get("_tracker")
if _previous is not None:
    _previous.close()
_tracker = PftDocumentStateTracker()


def snapshot(document, app):
    return _tracker.snapshot(document, app)


def check_expected(state, params, Error):
    token = params.get("expectedDocumentInstanceToken")
    if token is not None and token != state["document_instance_token"]:
        raise Error("DOCUMENT_INSTANCE_MISMATCH", "文档实例已变化，请重新读取文档。")
    if "expectedRevision" in params:
        if state["revision"] is None:
            raise Error("DOCUMENT_REVISION_UNAVAILABLE", "当前无法可靠观察文档事件，请重新读取并检查状态来源。")
        if params["expectedRevision"] != state["revision"]:
            raise Error("DOCUMENT_REVISION_MISMATCH", "观察到文档事件变化，请重新读取。")


def check_unchanged(before, document, app, Error):
    name = _name(document)
    getter, _ = read_attribute(app, "getDocument")
    if callable(getter):
        try:
            same = getter(name) is document
        except Exception:
            same = False
        if not same:
            raise Error("DOCUMENT_INSTANCE_MISMATCH", "读取期间文档实例已变化，请重新读取。")
    after = snapshot(document, app)
    if before["document_instance_token"] != after["document_instance_token"]:
        raise Error("DOCUMENT_INSTANCE_MISMATCH", "读取期间文档实例已变化，请重新读取。")
    if before["revision"] is not None:
        if after["revision"] is None:
            raise Error("DOCUMENT_REVISION_UNAVAILABLE", "读取期间事件观察失效，请重新读取并检查状态来源。")
        if before["revision"] != after["revision"]:
            raise Error("DOCUMENT_REVISION_MISMATCH", "读取期间观察到文档事件变化，请重新读取。")


def _context_item(value, source, status):
    return {"value": value, "source": source if status == "available" else None, "status": status}


def _native_bool(value, source, status):
    return _context_item(value if type(value) is bool else None, source,
                         status if status != "available" or type(value) is bool else "unavailable")


def native_context(document, app, gui, reference):
    active, status = read_attribute(app, "ActiveDocument")
    active_name, name_status = read_attribute(active, "Name")
    if active is not None and (name_status != "available" or not isinstance(active_name, str)):
        status = name_status if name_status != "available" else "unavailable"
        active_name = None
    context = {"active_document": _context_item({"document": active_name} if active_name else None, "App.ActiveDocument", status)}
    gui_document, gui_status = None, "unavailable"
    getter, status = read_attribute(gui, "getDocument")
    if callable(getter):
        try:
            gui_document = getter(_name(document))
            gui_status = "available" if gui_document is not None else "unavailable"
        except Exception:
            gui_status = "read_failed"
    elif status == "read_failed":
        gui_status = status

    edit_getter, status = read_attribute(gui_document, "getInEdit")
    edit, edit_status = None, status if gui_document is not None else gui_status
    if callable(edit_getter):
        try:
            edit, edit_status = edit_getter() is not None, "available"
        except Exception:
            edit_status = "read_failed"
    elif edit_status == "available":
        edit_status = "unavailable"
    context["in_edit"] = _context_item(edit, "GuiDocument.getInEdit", edit_status)

    body, body_status = None, gui_status
    view_getter, status = read_attribute(gui_document, "activeView")
    if callable(view_getter):
        try:
            view = view_getter()
            active_getter, body_status = read_attribute(view, "getActiveObject")
            if callable(active_getter):
                native_body = active_getter("pdbody")
                body = reference(native_body, document)
                body_status = "available" if native_body is None or body is not None else "unavailable"
            elif body_status == "available":
                body_status = "unavailable"
        except Exception:
            body_status = "read_failed"
    else:
        body_status = status if gui_document is not None else gui_status
        if body_status == "available":
            body_status = "unavailable"
    context["active_body"] = _context_item(body, "GuiDocument.activeView.getActiveObject(pdbody)", body_status)

    modified, status = read_attribute(gui_document, "Modified")
    modified_info = _native_bool(modified, "GuiDocument.Modified", status)
    if modified_info["status"] == "unavailable":
        modified, status = read_attribute(document, "Modified")
        modified_info = _native_bool(modified, "Document.Modified", status)
        if modified_info["status"] == "unavailable" and gui_status == "read_failed":
            modified_info = _context_item(None, None, "read_failed")
    touched_getter, status = read_attribute(document, "isTouched")
    touched, touched_source = None, "Document.isTouched"
    if callable(touched_getter):
        try:
            touched, status = touched_getter(), "available"
        except Exception:
            status = "read_failed"
    elif status == "unavailable":
        touched, status = read_attribute(document, "Touched")
        touched_source = "Document.Touched"
    touched_info = _native_bool(touched, touched_source, status)
    return context, modified_info, touched_info
