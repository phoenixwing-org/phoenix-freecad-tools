# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import types
import sys
import unittest

HELPER = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools" / "pft_workbench_tools.py"
spec = importlib.util.spec_from_file_location("pft_workbench_tools_test", HELPER)
tools = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(HELPER.parent))
try:
    spec.loader.exec_module(tools)
finally:
    sys.path.remove(str(HELPER.parent))


class Object:
    def __init__(self, name, label, **fields):
        self.Name = name
        self.Label = label
        self.PropertiesList = ["Label"] + list(fields)
        self.types = {field: "App::PropertyString" for field in self.PropertiesList}
        self.ExpressionEngine = []
        self.statuses = {}
        self.created = []
        for field, value in fields.items():
            setattr(self, field, value)

    def getTypeIdOfProperty(self, field):
        return self.types[field]

    def getPropertyStatus(self, field):
        return self.statuses.get(field, [])

    def getEditorMode(self, field):
        return []

    def addProperty(self, kind, field, group, description):
        self.created.append((kind, field, group, description))
        self.PropertiesList.append(field)
        self.types[field] = kind
        setattr(self, field, "")


class Document:
    Name = "Document"
    UndoMode = 1
    HasPendingTransaction = False

    def __init__(self, objects):
        self.Objects = objects
        self.events = []
        self.before = None
        self.active_transaction = None
        self.next_transaction_id = 0
        for obj in objects:
            obj.Document = self

    def getObject(self, name):
        return next((obj for obj in self.Objects if obj.Name == name), None)

    def openTransaction(self, title):
        self.events.append("open")
        self.next_transaction_id += 1
        self.active_transaction = (title, self.next_transaction_id)
        self.before = [(obj, {key: value.copy() if isinstance(value, (dict, list)) else value
                             for key, value in obj.__dict__.items()}) for obj in self.Objects]

    def commitTransaction(self):
        self.events.append("commit")
        self.active_transaction = None

    def abortTransaction(self):
        self.events.append("abort")
        self.active_transaction = None
        self.Objects = [obj for obj, _ in self.before]
        for obj, before in self.before:
            obj.__dict__.clear()
            obj.__dict__.update(before)


class WorkbenchToolsTests(unittest.TestCase):
    def setUp(self):
        self.selected = Object("Body", "Old", PartNumber="7001", PartVersion="002", PartName="Test part")
        self.other = Object("Body001", "Other")
        self.document = Document([self.selected, self.other])
        self.app = types.SimpleNamespace(
            ActiveDocument=self.document,
            getActiveTransaction=lambda: self.document.active_transaction,
            listDocuments=lambda: {self.document.Name: self.document},
        )
        self.gui = types.SimpleNamespace(
            getDocument=lambda name: types.SimpleNamespace(getInEdit=lambda: None),
            Selection=types.SimpleNamespace(
                getSelection=lambda: [self.selected],
                getSelectionEx=lambda: [],
            ),
        )

    def snapshots(self):
        return [tools._snapshot(self.selected)]

    def preview(self):
        return tools.build_rename_preview(self.document, [self.selected])

    def values(self, **changes):
        values = {field: str(tools._property_snapshot(self.selected, field)["value"]) for field, _ in tools.BOM_FIELDS}
        values.update(changes)
        return {self.selected.Name: values}

    def test_public_rule_has_same_semantics_without_guessing(self):
        self.assertEqual(tools.compose_bom_label({"PartNumber": " 7001 ", "PartVersion": " 2 ", "PartName": " Test part "}), "7001.2-Test part")
        self.assertEqual(tools.compose_bom_label({"PartNumber": "7001", "PartVersion": "002", "label": "fallback"}), "7001.002-fallback")
        self.assertEqual(tools.compose_bom_label({"PartNumber": "7001", "PartVersion": "002"}), "7001.002")
        self.assertEqual(tools.compose_bom_label({"PartNumber": "7001", "PartName": "Test part"}), "")

    def test_preview_is_read_only_and_apply_changes_only_selected_label(self):
        before = self.snapshots()
        rows = self.preview()
        self.assertEqual(rows[0]["new_label"], "7001.002-Test part")
        self.assertEqual(self.document.events, [])
        self.assertEqual(self.selected.Label, "Old")
        self.assertEqual(tools.apply_rename(self.app, self.gui, self.document, [self.selected], before, rows), 1)
        self.assertEqual(self.selected.Label, "7001.002-Test part")
        self.assertEqual(self.selected.Name, "Body")
        self.assertEqual(self.other.Label, "Other")
        self.assertEqual(self.document.events, ["open", "commit"])

    def test_missing_part_name_does_not_guess_from_label(self):
        self.selected.PartName = ""
        rows = self.preview()
        self.assertEqual(rows[0]["new_label"], "")
        self.assertIn("PartName", rows[0]["error"])
        with self.assertRaises(tools.PftToolError):
            tools.apply_rename(self.app, self.gui, self.document, [self.selected], self.snapshots(), rows)
        self.assertEqual(self.document.events, [])

    def test_conflicts_with_existing_and_planned_labels_are_blocked(self):
        self.other.Label = "7001.002-Test part"
        self.assertIn("重名冲突", self.preview()[0]["error"])
        self.other.Label = "Other"
        for field in ("PartNumber", "PartVersion", "PartName"):
            self.other.addProperty("App::PropertyString", field, "CustomDefine", "")
            setattr(self.other, field, getattr(self.selected, field))
        rows = tools.build_rename_preview(self.document, [self.selected, self.other])
        self.assertTrue(all("重名冲突" in row["error"] for row in rows))

    def install_rename_callback(self, callback):
        class PftCallbackObject(Object):
            def __setattr__(self, field, value):
                super().__setattr__(field, value)
                if field == "Label" and "after_label" in self.__dict__:
                    self.after_label(value)
        self.selected.__class__ = PftCallbackObject
        self.selected.after_label = callback
        # Plain renaming must not read or change DuplicateLabels preferences.
        self.app.ParamGet = lambda path: (_ for _ in ()).throw(AssertionError("no preference access"))

    def test_rename_callback_collision_aborts_entire_transaction(self):
        self.install_rename_callback(lambda value: setattr(self.other, "Label", value))
        before, rows = self.snapshots(), self.preview()
        self.assertEqual(rows[0]["error"], "")
        with self.assertRaisesRegex(tools.PftToolError, "重名冲突"):
            tools.apply_rename(self.app, self.gui, self.document, [self.selected], before, rows)
        self.assertEqual(self.document.events, ["open", "abort"])
        self.assertEqual((self.selected.Label, self.other.Label), ("Old", "Other"))
        self.assertEqual(self.selected.Name, "Body")

    def test_rename_native_auto_numbering_still_aborts(self):
        self.install_rename_callback(lambda value: self.selected.__dict__.__setitem__("Label", value + "001"))
        before, rows = self.snapshots(), self.preview()
        with self.assertRaisesRegex(tools.PftToolError, "自动调整"):
            tools.apply_rename(self.app, self.gui, self.document, [self.selected], before, rows)
        self.assertEqual(self.document.events, ["open", "abort"])
        self.assertEqual(self.selected.Label, "Old")

    def test_rename_checks_unchanged_selected_rows_after_callback(self):
        unchanged = Object("Stable", "7100.001-Stable part", PartNumber="7100", PartVersion="001", PartName="Stable part")
        unchanged.Document = self.document
        self.document.Objects.append(unchanged)
        self.install_rename_callback(lambda value: setattr(unchanged, "Label", "Unexpected callback label"))
        objects = [self.selected, unchanged]
        before = [tools._snapshot(obj) for obj in objects]
        rows = tools.build_rename_preview(self.document, objects)
        self.assertTrue(all(not row["error"] for row in rows))
        with self.assertRaisesRegex(tools.PftToolError, "预览不一致"):
            tools.apply_rename(self.app, self.gui, self.document, objects, before, rows)
        self.assertEqual(self.document.events, ["open", "abort"])
        self.assertEqual((self.selected.Label, unchanged.Label), ("Old", "7100.001-Stable part"))

    def test_rename_does_not_reject_unrelated_preexisting_duplicate_labels(self):
        unrelated = Object("Unrelated", self.other.Label)
        unrelated.Document = self.document
        self.document.Objects.append(unrelated)
        self.app.ParamGet = lambda path: (_ for _ in ()).throw(AssertionError("no preference access"))
        before, rows = self.snapshots(), self.preview()
        self.assertEqual(tools.apply_rename(self.app, self.gui, self.document, [self.selected], before, rows), 1)
        self.assertEqual((self.other.Label, unrelated.Label), ("Other", "Other"))
        self.assertEqual(self.document.events, ["open", "commit"])

    def test_label_expression_references_are_blocked_but_internal_names_are_safe(self):
        self.other.ExpressionEngine = [("Length", "<<Old>>.Length")]
        self.assertIn("表达式", self.preview()[0]["error"])
        self.other.ExpressionEngine = [("Length", "Body.Length")]
        self.assertEqual(self.preview()[0]["error"], "")

    def test_new_expression_after_preview_invalidates_confirmation(self):
        rows, before = self.preview(), self.snapshots()
        self.other.ExpressionEngine = [("Length", "<<Old>>.Length")]
        with self.assertRaises(tools.PftToolError):
            tools.apply_rename(self.app, self.gui, self.document, [self.selected], before, rows)
        self.assertEqual(self.document.events, [])

    def test_stale_values_and_active_edit_or_transaction_do_not_write(self):
        before, rows = self.snapshots(), self.preview()
        self.selected.PartName = "Changed outside dialog"
        with self.assertRaises(tools.PftToolError):
            tools.apply_rename(self.app, self.gui, self.document, [self.selected], before, rows)
        self.app.getActiveTransaction = lambda: "Other operation"
        with self.assertRaises(tools.PftToolError):
            tools._selected_context(self.app, self.gui)
        self.app.getActiveTransaction = lambda: None
        self.gui.getDocument = lambda name: types.SimpleNamespace(getInEdit=lambda: object())
        with self.assertRaises(tools.PftToolError):
            tools._selected_context(self.app, self.gui)
        self.assertEqual(self.document.events, [])

    def test_empty_selection_and_subelements_are_rejected(self):
        self.gui.Selection.getSelection = lambda: []
        with self.assertRaises(tools.PftToolError):
            tools._selected_context(self.app, self.gui)
        self.gui.Selection.getSelection = lambda: [self.selected]
        self.gui.Selection.getSelectionEx = lambda: [types.SimpleNamespace(SubElementNames=["Face1"])]
        with self.assertRaises(tools.PftToolError):
            tools._selected_context(self.app, self.gui)
        self.assertEqual(self.document.events, [])

    def test_only_checked_fields_are_written_and_missing_properties_use_contract(self):
        count = tools.apply_properties(self.app, self.gui, self.document, [self.selected], self.snapshots(), ["PartName", "TypeCode"], self.values(PartName="Changed name", TypeCode="H", ModelSeries="Unchecked"))
        self.assertEqual(count, 2)
        self.assertEqual(self.selected.PartName, "Changed name")
        self.assertEqual(self.selected.TypeCode, "H")
        self.assertNotIn("ModelSeries", self.selected.PropertiesList)
        self.assertEqual(self.selected.created[0][:3], ("App::PropertyString", "TypeCode", "CustomDefine"))
        self.assertEqual(self.selected.Label, "Old")
        self.assertEqual(self.document.events, ["open", "commit"])

    def test_property_type_readonly_and_expression_collisions_are_rejected(self):
        for mode in ("type", "readonly", "expression"):
            self.selected.types["PartName"] = "App::PropertyString"
            self.selected.statuses.clear()
            self.selected.ExpressionEngine = []
            if mode == "type": self.selected.types["PartName"] = "App::PropertyInteger"
            if mode == "readonly": self.selected.statuses["PartName"] = ["ReadOnly"]
            if mode == "expression": self.selected.ExpressionEngine = [("PartName", "Other.Label")]
            with self.assertRaises(tools.PftToolError):
                tools.apply_properties(self.app, self.gui, self.document, [self.selected], self.snapshots(), ["PartName"], self.values(PartName="Changed"))
        self.assertEqual(self.document.events, [])

    def test_write_failure_aborts_transaction(self):
        self.selected.addProperty = lambda *args: (_ for _ in ()).throw(RuntimeError("simulated property failure"))
        before = self.snapshots()
        with self.assertRaises(RuntimeError):
            tools.apply_properties(self.app, self.gui, self.document, [self.selected], before, ["PartName", "TypeCode"], self.values(PartName="Changed", TypeCode="H"))
        self.assertEqual(self.document.events, ["open", "abort"])
        self.assertEqual(self.selected.PartName, "Test part")

    def test_no_change_does_not_open_a_transaction(self):
        self.assertEqual(tools.apply_properties(self.app, self.gui, self.document, [self.selected], self.snapshots(), ["PartName"], self.values()), 0)
        self.assertEqual(self.document.events, [])


if __name__ == "__main__":
    unittest.main()
