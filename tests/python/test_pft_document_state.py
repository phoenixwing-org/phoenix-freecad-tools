# SPDX-License-Identifier: Apache-2.0
"""Observed document identity and read consistency, without FreeCAD or Qt."""
import importlib.util
import json
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import Mock, patch

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
spec = importlib.util.spec_from_file_location("document_state_operations_test", ADDON / "pft_operations.py")
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class Failure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Document:
    def __init__(self, name="Example"):
        self.Name = self.Label = name
        self.Objects, self.State = [], []
        self.FileName = "/Users/private/never-return-this.FCStd"
        self._touched = False
    def getObject(self, name): return next((obj for obj in self.Objects if obj.Name == name), None)
    def isTouched(self): return self._touched
    def recompute(self): raise AssertionError("read must not recompute")
    def save(self): raise AssertionError("read must not save")


class Object:
    def __init__(self, document, name="Part", kind="Part::Feature"):
        self.Document, self.Name, self.Label, self.TypeId = document, name, name, kind
        self.Group, self.InList, self.OutList, self.State, self.PropertiesList = [], [], [], [], []
        self.Tip = None
        document.Objects.append(self)


class Application:
    def __init__(self, document):
        self.ActiveDocument = document
        self.documents = {document.Name: document}
        self.observers, self.removed = [], []
    def getDocument(self, name): return self.documents.get(name)
    def addDocumentObserver(self, observer): self.observers.append(observer)
    def removeDocumentObserver(self, observer):
        self.observers.remove(observer)
        self.removed.append(observer)


class DocumentStateTests(unittest.TestCase):
    def setUp(self):
        self.document = Document()
        self.obj = Object(self.document)
        self.app = Application(self.document)
        self.gui_document = types.SimpleNamespace(Modified=False, getInEdit=lambda: None,
            activeView=lambda: types.SimpleNamespace(getActiveObject=lambda key: None))
        self.gui = types.SimpleNamespace(getDocument=lambda name: self.gui_document)
        self.helper = ops._document_state_helper()
        self.helper._tracker.close()
        self.addCleanup(lambda: self.helper._tracker.close())
    def read(self, method="get_document_info", params=None):
        result = ops.perform(method, params or {}, self.app, self.gui, Failure)
        json.dumps(result, allow_nan=False)
        return result
    def expected(self, result):
        return {"expectedDocumentInstanceToken": result["document_instance_token"], "expectedRevision": result["revision"]}
    def assert_error(self, code, method, params):
        with self.assertRaises(Failure) as caught: self.read(method, params)
        self.assertEqual(caught.exception.code, code)

    def test_three_reads_share_one_instance_and_observer_without_incrementing_reads(self):
        results = [self.read(), self.read("list_objects"), self.read("get_object_info", {"objectName": "Part"})]
        self.assertEqual(len(self.app.observers), 1)
        self.assertEqual(len({result["document_instance_token"] for result in results}), 1)
        for result in results:
            self.assertRegex(result["document_instance_token"], r"^[A-Za-z0-9_-]{32}$")
            self.assertEqual(result["instance_token"], result["document_instance_token"])
            self.assertEqual(result["revision"], 0)
            self.assertEqual(result["revision_coverage"], "partial")
            self.assertEqual(result["revision_scope"], "observed_document_events_since_registration")
            self.assertEqual(result["provenance"], {"source": "live_native_document", "geometry_freshness": "not_verified", "recomputed": False, "write_guard": False})
            self.assertNotIn("/Users/", json.dumps(result))

    def test_unrecomputed_change_undo_redo_and_dynamic_property_callbacks_are_observed(self):
        self.read()
        observer = self.app.observers[0]
        self.document._touched = True
        for index, callback in enumerate((lambda: observer.slotChangedObject(self.obj, "Length"),
                lambda: observer.slotUndoDocument(self.document), lambda: observer.slotRedoDocument(self.document),
                lambda: observer.slotAppendDynamicProperty(self.obj, "PartNumber"),
                lambda: observer.slotRemoveDynamicProperty(self.document, "ExampleField")), 1):
            callback()
            result = self.read()
            self.assertEqual(result["revision"], index)
            self.assertTrue(result["touched"])
            self.assertFalse(result["recomputed"])

    def test_actual_registered_callbacks_and_multiple_documents_are_isolated(self):
        first = self.read()
        other = Document("Other")
        other_object = Object(other)
        self.app.documents[other.Name] = other
        second = self.read(params={"docName": "Other"})
        observer = self.app.observers[0]
        observer.slotCreatedObject(other_object)
        observer.slotChangedDocument(other, "Label")
        observer.slotRecomputedObject(other_object)
        observer.slotRecomputedDocument(other)
        observer.slotDeletedObject(other_object)
        self.assertEqual(self.read()["revision"], first["revision"])
        self.assertEqual(self.read(params={"docName": "Other"})["revision"], second["revision"] + 5)

    def test_same_name_reopen_and_late_old_callbacks_cannot_reuse_or_remove_new_identity(self):
        before = self.read()
        observer, old = self.app.observers[0], self.document
        observer.slotDeletedDocument(old)
        replacement = Document(old.Name)
        self.app.documents[old.Name] = replacement
        self.app.ActiveDocument = replacement
        observer.slotCreatedDocument(replacement)
        after = self.read()
        self.assertNotEqual(before["document_instance_token"], after["document_instance_token"])
        observer.slotChangedObject(self.obj, "Label")
        observer.slotDeletedDocument(old)
        self.assertEqual(self.read()["revision"], after["revision"])
        self.assertEqual(self.read()["document_instance_token"], after["document_instance_token"])
        self.assert_error("DOCUMENT_INSTANCE_MISMATCH", "get_document_info", self.expected(before))

    def test_replacement_without_delete_notification_still_changes_identity(self):
        first = self.read()
        self.app.documents[self.document.Name] = Document(self.document.Name)
        self.app.ActiveDocument = self.app.documents[self.document.Name]
        self.assertNotEqual(self.read()["document_instance_token"], first["document_instance_token"])

    def test_missing_observer_and_registration_failure_never_report_a_reliable_zero(self):
        for failing in (False, True):
            with self.subTest(failing=failing):
                self.helper._tracker.close()
                app = types.SimpleNamespace(ActiveDocument=self.document, getDocument=self.app.getDocument)
                if failing:
                    app.addDocumentObserver = lambda observer: (_ for _ in ()).throw(RuntimeError("private path"))
                result = ops.perform("get_document_info", {}, app, self.gui, Failure)
                self.assertIsNone(result["revision"])
                self.assertEqual(result["revision_coverage"], "unknown")
                self.assertEqual(result["revision_status"], "registration_failed" if failing else "observer_unavailable")
                self.assertNotIn("private path", json.dumps(result))
                with self.assertRaises(Failure) as caught:
                    ops.perform("get_document_info", {"expectedDocumentInstanceToken": result["document_instance_token"], "expectedRevision": 0}, app, self.gui, Failure)
                self.assertEqual(caught.exception.code, "DOCUMENT_REVISION_UNAVAILABLE")

    def test_expected_params_require_instance_for_revision_and_validate_strictly(self):
        invalid = [{"expectedRevision": 0}, {"expectedDocumentInstanceToken": None},
            {"expectedDocumentInstanceToken": "bad/path"}, {"expectedDocumentInstanceToken": "x" * 129}]
        invalid += [{"expectedDocumentInstanceToken": "x" * 32, "expectedRevision": value}
                    for value in (True, False, -1, 1.0, 9007199254740992, None)]
        for method in ("get_document_info", "list_objects", "get_object_info"):
            for params in invalid:
                with self.subTest(method=method, params=params), self.assertRaises(Failure) as caught:
                    ops.validate(method, dict(params, **({"objectName": "Part"} if method == "get_object_info" else {})), Failure)
                self.assertEqual(caught.exception.code, "INVALID_PARAMS")
        self.assertEqual(self.app.observers, [])

    def test_expected_state_detects_change_across_pages_and_can_check_instance_alone(self):
        first = self.read("list_objects", {"limit": 1})
        self.read("get_document_info", self.expected(first))
        self.app.observers[0].slotChangedObject(self.obj, "Length")
        for method in ("get_document_info", "list_objects", "get_object_info"):
            params = dict(self.expected(first), **({"objectName": "Part"} if method == "get_object_info" else {}))
            self.assert_error("DOCUMENT_REVISION_MISMATCH", method, params)
        self.read(params={"expectedDocumentInstanceToken": first["document_instance_token"]})

    def test_change_during_read_is_rejected_instead_of_returning_an_inconsistent_page(self):
        self.read()
        base = self.obj
        observer = self.app.observers[0]
        class ChangedObject:
            Name, Document = base.Name, base.Document
            TypeId, Group, InList, OutList, State = base.TypeId, [], [], [], []
            @property
            def Label(self):
                observer.slotChangedObject(base, "Label")
                return "Changed during read"
        self.document.Objects = [ChangedObject()]
        self.assert_error("DOCUMENT_REVISION_MISMATCH", "list_objects", {})

    def test_instance_replacement_during_read_is_rejected_even_without_observer(self):
        self.app.addDocumentObserver = None
        base, app = self.obj, self.app
        class ReopenedObject:
            Name, Document = base.Name, base.Document
            TypeId, Group, InList, OutList, State = base.TypeId, [], [], [], []
            @property
            def Label(self):
                app.documents[base.Document.Name] = Document(base.Document.Name)
                return "Example"
        self.document.Objects = [ReopenedObject()]
        self.assert_error("DOCUMENT_INSTANCE_MISMATCH", "list_objects", {})

    def test_native_context_reports_sources_and_missing_or_failed_api_remains_unknown(self):
        body = Object(self.document, "Body", "PartDesign::Body")
        self.gui_document.Modified = True
        self.gui_document.getInEdit = lambda: object()
        self.gui_document.activeView = lambda: types.SimpleNamespace(getActiveObject=lambda key: body)
        result = self.read()
        self.assertTrue(result["modified"])
        self.assertEqual(result["modified_source"], "GuiDocument.Modified")
        self.assertEqual(result["touched_source"], "Document.isTouched")
        self.assertEqual(result["context"]["active_document"]["value"], {"document": "Example"})
        self.assertTrue(result["context"]["in_edit"]["value"])
        self.assertEqual(result["context"]["active_body"]["value"]["object"], "Body")
        self.gui_document = object()
        self.document.isTouched = lambda: (_ for _ in ()).throw(RuntimeError("private"))
        result = self.read()
        self.assertIsNone(result["modified"])
        self.assertEqual(result["modified_status"], "unavailable")
        self.assertIsNone(result["touched"])
        self.assertEqual(result["touched_status"], "read_failed")
        self.assertEqual(result["context"]["in_edit"]["status"], "unavailable")

    def test_bodies_are_bounded_with_explicit_failed_tip_and_state(self):
        class FailedBody(Object):
            def __getattribute__(self, name):
                if name in {"Tip", "State"}: raise RuntimeError("native read failed")
                if name == "Shape": raise AssertionError("body summary must not read Shape")
                return super().__getattribute__(name)
        FailedBody(self.document, "Failed", "PartDesign::Body")
        for index in range(54): Object(self.document, "Body%d" % index, "PartDesign::Body")
        result = self.read()["bodies"]
        self.assertEqual((result["total"], len(result["items"]), result["limit"], result["truncated"]), (55, 50, 50, True))
        first = result["items"][0]
        self.assertIsNone(first["tip"])
        self.assertIsNone(first["state"])
        self.assertEqual(first["tip_status"], "read_failed")
        self.assertEqual(first["state_status"], "read_failed")

    def test_read_response_budget_rejects_instead_of_hiding_an_incomplete_page(self):
        self.document.Objects = []
        for index in range(100):
            obj = Object(self.document, "Part%d" % index)
            obj.Label = "段" * 512
        self.assert_error("READ_RESPONSE_TOO_LARGE", "list_objects", {"limit": 100})

    def test_context_failed_gui_api_does_not_claim_unmodified_or_not_in_edit(self):
        class FailedGuiDocument:
            @property
            def Modified(self): raise RuntimeError("private source path")
            def getInEdit(self): raise RuntimeError("private source path")
            def activeView(self): raise RuntimeError("private source path")
        self.gui_document = FailedGuiDocument()
        result = self.read()
        self.assertIsNone(result["modified"])
        self.assertEqual(result["modified_status"], "read_failed")
        for field in ("in_edit", "active_body"):
            self.assertIsNone(result["context"][field]["value"])
            self.assertEqual(result["context"][field]["status"], "read_failed")
        self.assertNotIn("private source path", json.dumps(result))

    def test_document_and_object_list_reads_never_probe_shape_or_change_context(self):
        class NoShapeObject(Object):
            @property
            def Shape(self): raise AssertionError("Shape must not be probed")
        NoShapeObject(self.document, "Body", "PartDesign::Body")
        self.app.ActiveDocument = self.document
        before = dict(self.gui_document.__dict__)
        self.read()
        self.read("list_objects")
        self.assertIs(self.app.ActiveDocument, self.document)
        self.assertFalse(self.document.isTouched())
        self.assertEqual(self.gui_document.__dict__, before)

    def test_failed_observer_removal_disables_late_callbacks_before_new_epoch(self):
        self.read()
        old_observer = self.app.observers[0]
        self.app.removeDocumentObserver = lambda observer: (_ for _ in ()).throw(RuntimeError("native failure"))
        self.helper._tracker.close()
        after = self.read()
        old_observer.slotChangedObject(self.obj, "Label")
        self.assertEqual(self.read()["revision"], after["revision"])

    def test_callback_identity_lookup_failure_invalidates_counter_instead_of_losing_event(self):
        before = self.read()
        getter = self.app.getDocument
        self.app.getDocument = lambda name: (_ for _ in ()).throw(RuntimeError("native lookup failed"))
        self.app.observers[0].slotChangedObject(self.obj, "Length")
        self.app.getDocument = getter
        after = self.read()
        self.assertIsNone(after["revision"])
        self.assertEqual(after["revision_status"], "observer_unavailable")
        self.assert_error("DOCUMENT_REVISION_UNAVAILABLE", "get_document_info", self.expected(before))

    def test_module_reload_invalidates_tokens_removes_own_observer_and_disables_late_callbacks(self):
        before = self.read()
        old_observer = self.app.observers[0]
        helper_spec = importlib.util.spec_from_file_location(self.helper.__name__, ADDON / "pft_document_state.py")
        helper_spec.loader.exec_module(self.helper)
        after = self.read()
        self.assertEqual(len(self.app.observers), 1)
        self.assertEqual(self.app.removed, [old_observer])
        self.assertNotEqual(after["document_instance_token"], before["document_instance_token"])
        old_observer.slotChangedObject(self.obj, "Label")
        self.assertEqual(self.read()["revision"], after["revision"])
        self.helper._tracker.close()
        self.helper._tracker.close()
        self.assertEqual(self.app.observers, [])

    def test_file_loaded_operations_resolve_helper_without_addon_in_sys_path(self):
        self.assertNotIn(str(ADDON), sys.path)
        self.assertEqual(Path(self.helper.__file__).parent, ADDON)
        self.assertEqual(self.read()["revision_status"], "observing")

    def test_foreign_or_unidentified_cached_helper_is_rejected_without_calling_it(self):
        for filename in ("/private/tmp/another-addon/pft_document_state.py", None):
            with self.subTest(filename=filename):
                foreign = types.SimpleNamespace(__file__=filename, snapshot=Mock(),
                    _tracker=types.SimpleNamespace(close=Mock()))
                with patch.dict(sys.modules, {"pft_document_state": foreign}):
                    for method in ("get_document_info", "list_objects", "get_object_info"):
                        params = {"objectName": "Part"} if method == "get_object_info" else {}
                        with self.assertRaises(Failure) as caught:
                            self.read(method, params)
                        self.assertEqual(caught.exception.code, "STATE_SOURCE_MISMATCH")
                        self.assertNotIn("/private/", str(caught.exception))
                    foreign.snapshot.assert_not_called()
                    foreign._tracker.close.assert_not_called()
                self.assertIs(sys.modules["pft_document_state"], self.helper)

    def test_same_source_cache_and_operations_reload_preserve_token_and_observer(self):
        before = self.read()
        with patch.object(self.helper, "__file__", str(ADDON / "unused" / ".." / "pft_document_state.py")):
            self.assertIs(ops._document_state_helper(), self.helper)
            self.assertEqual(self.read()["document_instance_token"], before["document_instance_token"])
        new_spec = importlib.util.spec_from_file_location("pft_operations_second_import", ADDON / "pft_operations.py")
        second_ops = importlib.util.module_from_spec(new_spec)
        new_spec.loader.exec_module(second_ops)
        self.assertIs(second_ops._document_state_helper(), self.helper)
        self.app.observers[0].slotChangedObject(self.obj, "Length")
        after = second_ops.perform("get_document_info", {}, self.app, self.gui, Failure)
        self.assertEqual(after["document_instance_token"], before["document_instance_token"])
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual(len(self.app.observers), 1)


if __name__ == "__main__": unittest.main()
