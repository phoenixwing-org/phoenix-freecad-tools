# SPDX-License-Identifier: Apache-2.0
"""Read-only target recommendations and explicit-context guards, without FreeCAD."""
from pathlib import Path
import importlib.util
import sys
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools"
before_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_workbench_controller as controller
    from pft_workbench_model import PftToolError, _snapshot
finally:
    sys.path[:] = before_path


class Object:
    def __init__(self, name, kind, **fields):
        self.Name, self.Label, self.TypeId = name, "Example " + name, kind
        self.PropertiesList = ["Label"] + list(fields)
        self.Group, self.InList, self.ExpressionEngine = [], [], []
        for field, value in fields.items(): setattr(self, field, value)
    def isDerivedFrom(self, base):
        if base == "App::Link": return self.TypeId.startswith("App::Link")
        return base == "Part::Feature" and self.TypeId.startswith(("Part::", "PartDesign::", "Sketcher::"))
    def getTypeIdOfProperty(self, name): return "App::PropertyString"
    def getPropertyStatus(self, name): return []
    def getEditorMode(self, name): return []
    @property
    def Shape(self): raise AssertionError("target picking must not inspect geometry")


class Document:
    UndoMode = 1
    HasPendingTransaction = False
    def __init__(self, name, objects):
        self.Name, self.Objects = name, list(objects)
        for obj in self.Objects: obj.Document = self
    def getObject(self, name): return next((obj for obj in self.Objects if obj.Name == name), None)
    def recompute(self): raise AssertionError("must not recompute")
    def save(self): raise AssertionError("must not save")
    def openTransaction(self, title): raise AssertionError("preview must not open a transaction")


class Selection:
    def __init__(self): self.records, self.plain, self.calls = [], [], []
    def getSelectionEx(self, document="", resolve=1):
        self.calls.append(("extended", document, resolve))
        return list(self.records)
    def getSelection(self, *args):
        self.calls.append(("plain", *args))
        return list(self.plain)
    def clearSelection(self, *args): raise AssertionError("must not change tree selection")
    def addSelection(self, *args): raise AssertionError("must not change tree selection")


class TargetContextTests(unittest.TestCase):
    def setUp(self):
        fields = {"PartNumber": "5410", "PartVersion": "003", "PartName": "Example part"}
        self.body = Object("Body", "PartDesign::Body", **fields)
        self.part = Object("Assembly", "App::Part")
        self.imported = Object("Imported", "Part::Feature")
        self.custom = Object("BusinessNode", "App::FeaturePython", PartNumber="5411")
        self.pad = Object("Pad", "PartDesign::Pad", **fields)
        self.sketch = Object("Sketch", "Sketcher::SketchObject")
        self.inner = Object("BaseFeature", "Part::Feature", **fields)
        self.origin = Object("Origin", "App::Origin")
        self.datum = Object("Datum", "PartDesign::Plane", **fields)
        self.page = Object("Page", "TechDraw::DrawPage", **fields)
        self.link = Object("Instance", "App::Link")
        self.body.Group = [self.pad, self.sketch, self.inner, self.origin, self.datum]
        self.pad.InList = [self.body]
        self.imported.InList = [self.body]  # A dependency alone is not containment.
        self.part.Group = [self.body, self.imported]
        self.body.InList = [self.part]
        self.doc = Document("ExampleDoc", [self.body, self.part, self.imported, self.custom,
            self.pad, self.sketch, self.inner, self.origin, self.datum, self.page, self.link])
        self.link.LinkedObject = self.body
        self.selection = Selection()
        self.app = types.SimpleNamespace(ActiveDocument=self.doc, getActiveTransaction=lambda: None,
            listDocuments=lambda: {self.doc.Name: self.doc})
        self.gui = types.SimpleNamespace(Selection=self.selection, getDocument=lambda name: types.SimpleNamespace(getInEdit=lambda: None))
    def options(self, operation="properties"):
        return controller.prepare_target_options(self.app, self.gui, operation)
    def record(self, obj, subnames=()):
        return types.SimpleNamespace(Object=obj, ObjectName=obj.Name, DocumentName=obj.Document.Name, SubElementNames=subnames)

    def test_no_selection_recommends_part_level_nodes_without_geometry_or_selection_changes(self):
        document, candidates, defaults = self.options()
        self.assertIs(document, self.doc)
        self.assertEqual(defaults, ["Body", "Assembly", "Imported", "BusinessNode"])
        self.assertIn(self.pad, candidates)  # Available only by an explicit choice.
        self.assertIn(self.sketch, candidates)
        for forbidden in (self.link, self.origin, self.datum, self.page): self.assertNotIn(forbidden, candidates)
        self.assertEqual(self.selection.records, [])
        self.assertEqual(self.selection.calls[0], ("extended", "", 0))

    def test_suitable_raw_whole_selection_wins_and_is_filtered_deduplicated(self):
        foreign = Object("Foreign", "PartDesign::Body")
        Document("Elsewhere", [foreign])
        stale = Object("Stale", "PartDesign::Body")
        stale.Document = self.doc
        self.selection.records = [self.record(self.imported), self.record(self.pad), self.record(self.imported),
            self.record(foreign), self.record(stale), self.record(self.sketch, ("Edge1",)), self.record(self.page)]
        before = list(self.selection.records)
        self.assertEqual(self.options()[2], ["Imported", "Pad"])
        self.assertEqual(self.selection.records, before)

    def test_raw_link_identity_is_not_replaced_by_resolved_source(self):
        self.selection.records = [self.record(self.link)]
        self.selection.plain = [self.body]  # What resolved getSelection could return.
        _, candidates, defaults = self.options("link_labels")
        self.assertEqual(candidates, [self.link])
        self.assertEqual(defaults, ["Instance"])
        self.assertFalse(any(call[0] == "plain" for call in self.selection.calls))
        self.assertNotIn(self.link, self.options("properties")[1])

    def test_unsuitable_raw_selection_leaves_all_targets_unchecked(self):
        foreign = Object("Foreign", "PartDesign::Body")
        Document("Elsewhere", [foreign])
        stale = Object("Stale", "PartDesign::Body")
        stale.Document = self.doc
        for record in (self.record(self.link), self.record(self.body, ("Edge1",)),
                       self.record(foreign), self.record(stale)):
            with self.subTest(name=record.ObjectName, subnames=record.SubElementNames):
                self.selection.records = [record]
                # A resolved fallback must not broaden the raw record scope.
                self.selection.plain = [self.body]
                _, candidates, defaults = self.options("properties")
                self.assertIn(self.body, candidates)
                self.assertEqual(defaults, [])
                self.assertEqual(self.selection.records, [record])

    def test_unsuitable_plain_selection_does_not_fall_back_to_recommendations(self):
        foreign = Object("Foreign", "PartDesign::Body")
        Document("Elsewhere", [foreign])
        stale = Object("Stale", "PartDesign::Body")
        stale.Document = self.doc
        for obj in (self.link, foreign, stale):
            with self.subTest(name=obj.Name):
                self.selection.plain = [obj]
                self.assertEqual(self.options("properties")[2], [])
        self.selection.plain = []
        self.assertEqual(self.options("properties")[2], ["Body", "Assembly", "Imported", "BusinessNode"])

    def test_selection_record_names_and_no_argument_stub_are_supported(self):
        self.selection.records = [types.SimpleNamespace(ObjectName="Imported", DocumentName=self.doc.Name, SubElementNames=())]
        get_extended = self.selection.getSelectionEx
        self.selection.getSelectionEx = lambda: get_extended()
        self.assertEqual(self.options()[2], ["Imported"])

    def test_internal_features_never_become_automatic_defaults_even_with_bom(self):
        _, candidates, defaults = self.options("rename")
        self.assertIn(self.inner, candidates)
        for name in ("Pad", "Sketch", "BaseFeature", "Origin", "Datum", "Page", "Instance"):
            self.assertNotIn(name, defaults)
        self.assertIn("Imported", defaults)  # InList dependency is not containment.

    def test_no_suitable_nodes_returns_empty_without_defaulting_all_document_objects(self):
        self.doc.Objects = [self.origin, self.datum, self.page]
        self.assertEqual(self.options()[1:], ([], []))
        self.assertEqual(self.options("link_labels")[1:], ([], []))

    def test_candidates_with_no_recommended_part_are_left_unchecked(self):
        self.doc.Objects = [self.pad, self.sketch]
        _, candidates, defaults = self.options()
        self.assertEqual(candidates, [self.pad, self.sketch])
        self.assertEqual(defaults, [])

    def test_link_python_properties_are_excluded_and_explicit_writes_refused(self):
        link = Object("PythonInstance", "App::LinkPython", PartNumber="would forward")
        link.Document = self.doc
        self.doc.Objects.append(link)
        self.assertNotIn(link, self.options()[1])
        with self.assertRaises(PftToolError):
            controller.prepare_properties_context(self.app, self.gui, document=self.doc, objects=[link])

    def test_explicit_context_ignores_tree_selection_and_snapshots_exact_targets(self):
        self.selection.getSelection = lambda *args: (_ for _ in ()).throw(AssertionError("must not read tree selection"))
        self.selection.getSelectionEx = self.selection.getSelection
        document, objects, snapshots = controller.prepare_properties_context(self.app, self.gui, document=self.doc, objects=[self.imported, self.body])
        self.assertIs(document, self.doc)
        self.assertEqual(objects, [self.imported, self.body])
        self.assertEqual(snapshots, [_snapshot(self.imported), _snapshot(self.body)])
        preview = controller.prepare_rename_context(self.app, self.gui, document=self.doc, objects=[self.body])
        self.assertEqual(preview[1], [self.body])
        self.assertEqual(preview[3][0]["new_label"], "5410.003-Example part")
        links = controller.prepare_link_context(self.app, self.gui, document=self.doc, objects=[self.link])
        self.assertIs(links[1][0], self.link)

    def test_explicit_empty_foreign_stale_and_partial_arguments_are_rejected(self):
        foreign = Object("Foreign", "Part::Feature")
        other = Document("Other", [foreign])
        stale = Object("Body", "PartDesign::Body")
        stale.Document = self.doc
        for prepare in (controller.prepare_rename_context, controller.prepare_properties_context, controller.prepare_link_context):
            for kwargs in ({"document": self.doc, "objects": []}, {"document": self.doc, "objects": [foreign]},
                           {"document": self.doc, "objects": [stale]}, {"document": other, "objects": [foreign]},
                           {"document": self.doc}, {"objects": [self.body]}):
                with self.subTest(prepare=prepare.__name__, kwargs=list(kwargs)), self.assertRaises(PftToolError):
                    prepare(self.app, self.gui, **kwargs)

    def test_explicit_snapshots_still_reject_changes_before_apply(self):
        document, objects, snapshots, rows = controller.prepare_rename_context(self.app, self.gui, document=self.doc, objects=[self.body])
        self.body.PartName = "Changed after preview"
        with self.assertRaises(PftToolError):
            controller.apply_rename(self.app, self.gui, document, objects, snapshots, rows)

    def test_legacy_context_stays_strict_when_selection_is_empty(self):
        for prepare in (controller.prepare_context, controller.prepare_rename_context, controller.prepare_properties_context, controller.prepare_link_context):
            with self.subTest(prepare=prepare.__name__), self.assertRaises(PftToolError): prepare(self.app, self.gui)

    def test_active_edit_transaction_undo_and_main_thread_guards_are_preserved(self):
        for mode in ("transaction", "editing", "undo", "worker", "no_document"):
            with self.subTest(mode=mode):
                app = types.SimpleNamespace(**self.app.__dict__)
                gui = types.SimpleNamespace(**self.gui.__dict__)
                if mode == "transaction": app.getActiveTransaction = lambda: "User transaction"
                if mode == "editing": gui.getDocument = lambda name: types.SimpleNamespace(getInEdit=lambda: object())
                if mode == "undo": self.doc.UndoMode = 0
                if mode == "no_document": app.ActiveDocument = None
                thread_patch = patch.object(controller.threading, "current_thread", return_value=object()) if mode == "worker" else patch.object(controller.threading, "current_thread", wraps=controller.threading.current_thread)
                with thread_patch:
                    with self.assertRaises(PftToolError): controller.prepare_target_options(app, gui, "properties")
                    with self.assertRaises(PftToolError): controller.prepare_properties_context(app, gui, document=self.doc, objects=[self.body])
                self.doc.UndoMode = 1

    def test_unknown_operation_does_not_inspect_app_or_selection(self):
        with self.assertRaises(PftToolError): controller.prepare_target_options(None, None, "arbitrary")


if __name__ == "__main__": unittest.main()
