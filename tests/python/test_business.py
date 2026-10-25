# SPDX-License-Identifier: Apache-2.0
"""Token lifetime, read-only previews, binding and one-use business controller checks."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADMCP"
spec = importlib.util.spec_from_file_location("phoenix_business_test", ADDON / "phoenix_freecad_business.py")
business = importlib.util.module_from_spec(spec)
spec.loader.exec_module(business)


class Error(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Object:
    def __init__(self, name, label, **fields):
        self.Name, self.Label = name, label
        self.TypeId = "PartDesign::Body"
        self.PropertiesList = ["Label"] + list(fields)
        self.ExpressionEngine = []
        self.types = {field: "App::PropertyString" for field in self.PropertiesList}
        self.created = []
        for field, value in fields.items(): setattr(self, field, value)

    def getTypeIdOfProperty(self, field): return self.types[field]
    def getPropertyStatus(self, field): return []
    def getEditorMode(self, field): return 0
    def addProperty(self, kind, field, group, description):
        self.created.append((kind, field, group))
        self.PropertiesList.append(field)
        self.types[field] = kind
        setattr(self, field, "")


class Document:
    UndoMode = 1
    def __init__(self, name, objects):
        self.Name, self.Objects = name, objects
        self.events = []
        for obj in objects: obj.Document = self

    def getObject(self, name): return next((obj for obj in self.Objects if obj.Name == name), None)
    def openTransaction(self, title):
        self.events.append("open")
        self.before = [(obj, {key: value.copy() if isinstance(value, (dict, list)) else value for key, value in obj.__dict__.items()}) for obj in self.Objects]
    def commitTransaction(self): self.events.append("commit")
    def abortTransaction(self):
        self.events.append("abort")
        self.restore()
    def restore(self):
        self.Objects = [obj for obj, _ in self.before]
        for obj, values in self.before:
            obj.__dict__.clear()
            obj.__dict__.update(values)


class Preferences:
    def __init__(self): self.bools, self.events = {"DuplicateLabels": False}, []
    def GetBools(self): return list(self.bools)
    def GetBool(self, key, default=False): return self.bools.get(key, default)
    def SetBool(self, key, value): self.events.append((key, value)); self.bools[key] = value
    def RemBool(self, key): self.events.append((key, "removed")); self.bools.pop(key, None)


class BusinessTests(unittest.TestCase):
    def setUp(self):
        with business._lock: business._plans.clear()
        self.part = Object("Part", "7305.4-Example bracket", PartNumber="7305", PartVersion="004", PartName="Example bracket")
        self.other = Object("Other", "Other example")
        self.doc = Document("Demo", [self.part, self.other])
        self.documents = {self.doc.Name: self.doc}
        self.prefs = Preferences()
        self.app = types.SimpleNamespace(ActiveDocument=self.doc, getDocument=self.documents.get,
            listDocuments=lambda: self.documents, getActiveTransaction=lambda: None, ParamGet=lambda path: self.prefs)
        def selection_forbidden(*args): raise AssertionError("selection must not be inspected or changed")
        self.gui = types.SimpleNamespace(getDocument=lambda name: types.SimpleNamespace(getInEdit=lambda: None),
            Selection=types.SimpleNamespace(getSelection=selection_forbidden, getSelectionEx=selection_forbidden,
                                            clearSelection=selection_forbidden, addSelection=selection_forbidden))

    def call(self, method, params): return business.perform(method, params, self.app, self.gui, Error)
    def prepare(self, operation="normalize_names", names=None, values=None):
        params = {"docName": self.doc.Name, "objectNames": names or [self.part.Name], "operation": operation}
        if values is not None: params["values"] = values
        return self.call("prepare_part_changes", params)
    def apply(self, token): return self.call("apply_part_changes", {"token": token})
    def assert_code(self, code, callback):
        with self.assertRaises(Error) as failure: callback()
        self.assertEqual(failure.exception.code, code)

    def test_bridge_style_file_import_does_not_need_addon_path_or_import_freecad(self):
        script = """import importlib.util,sys
from pathlib import Path
p=Path(sys.argv[1]); sys.path[:]=[s for s in sys.path if Path(s or '.').resolve()!=p.parent]
s=importlib.util.spec_from_file_location('isolated_business',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
assert 'phoenix_workbench_model' not in sys.modules
model,controller=m._mvc()
assert model.BOM_GROUP=='CustomDefine'
assert not any(n.startswith(('FreeCAD','PySide')) for n in sys.modules)
"""
        result = subprocess.run([sys.executable, "-B", "-c", script, str(ADDON / "phoenix_freecad_business.py")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_prepare_is_read_only_and_bounded_then_normalize_is_undoable(self):
        preview = self.prepare()
        self.assertTrue(preview["can_apply"])
        self.assertEqual(preview["changed_count"], 1)
        self.assertEqual(preview["rows"][0], {"object": "Part", "field": "Label", "old": "7305.4-Example bracket", "new": "7305.004-Example bracket", "status": "change"})
        self.assertRegex(preview["token"], r"^[A-Za-z0-9_-]{32}$")
        self.assertEqual(preview["ttl_seconds"], 300)
        self.assertTrue(preview["requires_confirmation"])
        self.assertFalse(preview["saves_document"])
        self.assertEqual(self.doc.events, [])
        self.assertEqual(self.prefs.events, [])
        json.dumps(preview, allow_nan=False)
        result = self.apply(preview["token"])
        self.assertEqual(result["changed_count"], 1)
        self.assertEqual(self.part.Label, "7305.004-Example bracket")
        self.assertEqual(self.doc.events, ["open", "commit"])

    def test_repeated_success_returns_cached_summary_even_after_user_undo(self):
        preview = self.prepare()
        first = self.apply(preview["token"])
        self.doc.restore()
        before_events = list(self.doc.events)
        replay = self.apply(preview["token"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["changed_count"], first["changed_count"])
        self.assertEqual(self.part.Label, "7305.4-Example bracket")
        self.assertEqual(self.doc.events, before_events)

    def test_expired_and_unknown_tokens_are_explicit_and_do_not_modify(self):
        with patch.object(business.time, "monotonic", return_value=100): preview = self.prepare()
        with patch.object(business.time, "monotonic", return_value=400):
            self.assert_code("PART_TOKEN_EXPIRED", lambda: self.apply(preview["token"]))
        self.assert_code("PART_TOKEN_NOT_FOUND", lambda: self.apply("x" * 32))
        self.assertEqual(self.doc.events, [])

    def test_conflict_preview_gets_token_but_cannot_apply_or_retry(self):
        self.other.Label = "7305.004-Example bracket"
        preview = self.prepare()
        self.assertFalse(preview["can_apply"])
        self.assertEqual(preview["rows"][0]["status"], "blocked")
        self.assert_code("PART_PREVIEW_BLOCKED", lambda: self.apply(preview["token"]))
        self.assert_code("PART_TOKEN_USED", lambda: self.apply(preview["token"]))
        self.assertEqual(self.doc.events, [])

    def test_changed_attributes_consume_token_and_require_new_preview(self):
        preview = self.prepare()
        self.part.PartName = "Changed externally"
        self.assert_code("PART_PLAN_STALE", lambda: self.apply(preview["token"]))
        self.assert_code("PART_TOKEN_USED", lambda: self.apply(preview["token"]))
        self.assertEqual(self.doc.events, [])

    def test_document_close_reopen_and_object_replacement_cannot_reuse_token(self):
        preview = self.prepare()
        replacement = Document("Demo", [Object("Part", self.part.Label)])
        self.documents["Demo"] = replacement
        self.app.ActiveDocument = replacement
        self.assert_code("PART_PLAN_STALE", lambda: self.apply(preview["token"]))
        self.assertEqual(replacement.events, [])
        self.documents["Demo"] = self.doc
        self.app.ActiveDocument = self.doc
        preview = self.prepare()
        replacement_obj = Object("Part", self.part.Label)
        replacement_obj.Document = self.doc
        self.doc.Objects[0] = replacement_obj
        self.assert_code("PART_PLAN_STALE", lambda: self.apply(preview["token"]))
        self.assertEqual(self.doc.events, [])

    def test_nonactive_and_editing_documents_are_rejected_without_switching(self):
        self.app.ActiveDocument = None
        self.assert_code("DOCUMENT_NOT_ACTIVE", lambda: self.prepare())
        self.assertIsNone(self.app.ActiveDocument)
        self.app.ActiveDocument = self.doc
        self.app.getActiveTransaction = lambda: "Other edit"
        self.assert_code("PART_PREVIEW_UNAVAILABLE", lambda: self.prepare())
        self.assertEqual(self.doc.events, [])

    def test_unicode_document_and_object_internal_names_are_supported(self):
        self.documents.pop(self.doc.Name)
        self.doc.Name = "_2045_001_H_示例文档"
        self.part.Name = "示例零件"
        self.documents[self.doc.Name] = self.doc
        preview = self.prepare()
        self.assertEqual(preview["document"], self.doc.Name)
        self.assertEqual(preview["rows"][0]["object"], self.part.Name)
        self.apply(preview["token"])
        self.assertEqual(self.part.Label, "7305.004-Example bracket")

    def test_native_lookup_and_worker_thread_errors_are_fixed_messages(self):
        self.doc.getObject = lambda name: (_ for _ in ()).throw(RuntimeError("/Users/private/internal.py"))
        self.assert_code("OBJECT_NOT_FOUND", lambda: self.prepare())
        self.assertEqual(len(business._plans), 0)
        _, controller = business._mvc()
        model, _ = business._mvc()
        with patch.object(controller, "_ensure_main_thread", side_effect=model.ToolError("/Users/private/internal.py")):
            self.assert_code("GUI_THREAD_REQUIRED", lambda: self.prepare())
        self.assertEqual(self.doc.events, [])

    def test_initialize_preserves_existing_values_and_creates_only_missing_fields(self):
        preview = self.prepare("initialize_properties")
        self.assertEqual(preview["changed_count"], 2)
        self.assertEqual(self.doc.events, [])
        self.apply(preview["token"])
        self.assertEqual(self.part.PartName, "Example bracket")
        self.assertEqual(self.part.TypeCode, "")
        self.assertEqual(self.part.ModelSeries, "")
        self.assertEqual([field for _, field, _ in self.part.created], ["TypeCode", "ModelSeries"])

    def test_recommend_only_fills_empty_values_and_blocks_ambiguous_labels(self):
        self.part.PartNumber = "explicit number"
        self.part.PartVersion = ""
        preview = self.prepare("recommend_properties")
        self.apply(preview["token"])
        self.assertEqual(self.part.PartNumber, "explicit number")
        self.assertEqual(self.part.PartVersion, "004")
        self.assertNotIn("TypeCode", self.part.PropertiesList)
        self.other.Label = "Unstructured example"
        blocked = self.prepare("recommend_properties", names=[self.other.Name])
        self.assertFalse(blocked["can_apply"])
        self.assert_code("PART_PREVIEW_BLOCKED", lambda: self.apply(blocked["token"]))

    def test_set_properties_copies_values_and_only_touches_named_fields(self):
        values = {self.part.Name: {"PartName": "Confirmed name"}, self.other.Name: {"PartName": "Other confirmed name"}}
        preview = self.prepare("set_properties", names=[self.part.Name, self.other.Name], values=values)
        values[self.part.Name]["PartName"] = "Changed after prepare"
        preview["rows"][0]["new"] = "Changed response"
        self.apply(preview["token"])
        self.assertEqual(self.part.PartName, "Confirmed name")
        self.assertEqual(self.other.PartName, "Other confirmed name")
        self.assertNotIn("PartNumber", self.other.PropertiesList)
        self.assertEqual(self.doc.events, ["open", "commit"])

    def test_invalid_values_scope_fields_and_multiline_are_rejected(self):
        bad_values = [{}, {"Part": {}, "Other": {"PartName": "extra"}}, {"Part": {"Name": "bad"}},
                      {"Part": {"PartName": "line\nbreak"}}, {"Part": {"PartName": "a" * 2049}},
                      {"Part": {"PartName": "/Users/private/part.FCStd"}}]
        for values in bad_values:
            with self.subTest(values=values):
                self.assert_code("INVALID_PARAMS", lambda: self.prepare("set_properties", values=values))
        values = {"Part": {"PartName": "one"}, "Other": {"PartVersion": "002"}}
        self.assert_code("INVALID_PARAMS", lambda: self.prepare("set_properties", names=["Part", "Other"], values=values))
        self.assertEqual(self.doc.events, [])

    def test_long_or_private_current_values_do_not_leak_and_block_approval(self):
        self.part.PartName = "/Users/private/source.FCStd"
        preview = self.prepare("set_properties", values={"Part": {"PartName": "Safe name"}})
        self.assertFalse(preview["can_apply"])
        self.assertNotIn("/Users/private", json.dumps(preview))
        self.assert_code("PART_PREVIEW_BLOCKED", lambda: self.apply(preview["token"]))

    def test_paths_after_unicode_punctuation_are_hidden_and_rejected(self):
        examples = ["源文件（C:\\clients\\Example\\secret.FCStd）", "文件：/opt/customer/private.FCStd", "Example-/Users/private/secret.FCStd"]
        for value in examples:
            with self.subTest(value=value):
                self.assert_code("INVALID_PARAMS", lambda: self.prepare("set_properties", values={"Part": {"PartName": value}}))
                self.part.PartName = value
                preview = self.prepare("set_properties", values={"Part": {"PartName": "Safe name"}})
                self.assertFalse(preview["can_apply"])
                self.assertNotIn(value, json.dumps(preview, ensure_ascii=False))

    def test_capacity_is_32_and_expiration_releases_slots(self):
        with patch.object(business.time, "monotonic", return_value=100):
            for _ in range(32): self.prepare()
            self.assert_code("PART_PREVIEW_CAPACITY", lambda: self.prepare())
        self.assertEqual(len(business._plans), 32)
        with patch.object(business.time, "monotonic", return_value=401):
            self.assertTrue(self.prepare()["can_apply"])
        self.assertLessEqual(len(business._plans), 32)
        self.assertEqual(self.doc.events, [])

    def test_oversized_complete_preview_is_rejected_without_token_or_write(self):
        objects = [Object("Item" + str(i), "Example " + str(i)) for i in range(20)]
        for obj in objects:
            obj.Document = self.doc
        self.doc.Objects.extend(objects)
        values = {obj.Name: {field: "示" * 2048 for field in business.FIELDS} for obj in objects}
        self.assert_code("PART_PREVIEW_TOO_LARGE", lambda: self.prepare("set_properties", names=[obj.Name for obj in objects], values=values))
        self.assertEqual(len(business._plans), 0)
        self.assertEqual(self.doc.events, [])

    def make_link(self):
        link = Object("Instance", "Old instance")
        link.TypeId, link.LinkedObject = "App::Link", self.part
        link.Document = self.doc
        self.doc.Objects.append(link)
        return link

    def test_link_prepare_does_not_touch_preferences_and_apply_uses_controller(self):
        link = self.make_link()
        preview = self.prepare("sync_link_labels", names=[link.Name])
        self.assertTrue(preview["can_apply"])
        self.assertEqual(preview["rows"][0]["source"], "Demo#Part")
        self.assertEqual(self.prefs.events, [])
        self.apply(preview["token"])
        self.assertEqual(link.Label, self.part.Label)
        self.assertIs(self.prefs.GetBool("DuplicateLabels"), False)
        self.assertEqual(self.doc.events, ["open", "commit"])

    def test_link_source_change_and_external_source_document_reopen_invalidate(self):
        link = self.make_link()
        preview = self.prepare("sync_link_labels", names=[link.Name])
        link.LinkedObject = self.other
        self.assert_code("PART_PLAN_STALE", lambda: self.apply(preview["token"]))
        link.LinkedObject = self.part
        source_doc = Document("SourceDemo", [self.part])
        self.documents[source_doc.Name] = source_doc
        self.doc.Objects.remove(self.part)
        preview = self.prepare("sync_link_labels", names=[link.Name])
        self.documents[source_doc.Name] = Document("SourceDemo", [Object(self.part.Name, self.part.Label)])
        self.assert_code("PART_PLAN_STALE", lambda: self.apply(preview["token"]))
        self.assertEqual(self.prefs.events, [])

    def test_failure_does_not_retry_and_duplicate_claim_does_not_reenter_controller(self):
        preview = self.prepare("set_properties", values={"Part": {"PartName": "Confirmed"}})
        _, controller = business._mvc()
        with patch.object(controller, "apply_properties", side_effect=RuntimeError("simulated")) as apply:
            self.assert_code("PART_CHANGE_FAILED", lambda: self.apply(preview["token"]))
            self.assert_code("PART_TOKEN_USED", lambda: self.apply(preview["token"]))
            self.assertEqual(apply.call_count, 1)
        self.assertEqual(self.doc.events, [])

    def test_unknown_methods_and_additional_apply_parameters_are_rejected(self):
        self.assertFalse(business.validate("execute_code", {}, Error))
        self.assert_code("METHOD_NOT_FOUND", lambda: self.call("execute_code", {}))
        self.assert_code("INVALID_PARAMS", lambda: self.call("apply_part_changes", {"token": "x" * 32, "docName": "Demo"}))
        self.assert_code("INVALID_PARAMS", lambda: self.call("prepare_part_changes", {"docName": "Demo", "objectNames": ["Part", "Part"], "operation": "normalize_names"}))


if __name__ == "__main__": unittest.main()
