# SPDX-License-Identifier: Apache-2.0
"""Safety checks for staged BOM setup and direct-source Link Label synchronization."""
import sys
from pathlib import Path
import types
import unittest

from test_workbench_tools import Object, Document

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADMCP"
sys.path.insert(0, str(ADDON))
try:
    import phoenix_workbench_model as model
    import phoenix_workbench_controller as controller
finally:
    sys.path.remove(str(ADDON))


class Preferences:
    def __init__(self):
        self.values = {"DuplicateLabels": False}
        self.events = []

    def GetBools(self): return list(self.values)
    def GetBool(self, key, default=False): return self.values.get(key, default)
    def SetBool(self, key, value):
        self.events.append(("set", key, value))
        self.values[key] = value
    def RemBool(self, key):
        self.events.append(("remove", key))
        self.values.pop(key, None)


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.source = Object("Source", "Example bracket")
        self.source.TypeId = "PartDesign::Body"
        self.source_doc = Document([self.source])
        self.source_doc.Name = "SourceDocument"
        self.link = self.make_link("Instance", "Old instance")
        self.document = Document([self.link])
        self.prefs = Preferences()
        self.app = types.SimpleNamespace(ParamGet=lambda path: self.prefs, ActiveDocument=self.document, getActiveTransaction=lambda: None,
            listDocuments=lambda: {self.document.Name: self.document, self.source_doc.Name: self.source_doc})
        self.gui = types.SimpleNamespace(getDocument=lambda name: types.SimpleNamespace(getInEdit=lambda: None),
            Selection=types.SimpleNamespace(getSelection=lambda: [self.link], getSelectionEx=lambda: []))

    def make_link(self, name, label, source=None):
        link = Object(name, label)
        link.TypeId = "App::Link"
        link.LinkedObject = source if source is not None else self.source
        return link

    def preview(self, links=None):
        return model.build_link_label_preview(self.document, links or [self.link], self.app.listDocuments().values())

    def test_complete_label_recommendation_is_conservative_and_does_not_mutate_values(self):
        fields = model.recommend_fields_from_label("7305.4-Example bracket")
        self.assertEqual(fields, {"PartNumber": "7305", "PartVersion": "004", "PartName": "Example bracket"})
        snapshot = {"name": "Part", "label": "7305.4-Example bracket"}
        values = {"Part": {"PartNumber": "", "PartVersion": "", "PartName": "Existing name", "TypeCode": "R", "ModelSeries": "Explicit series"}}
        staged, notes = model.recommend_property_values([snapshot], values)
        self.assertEqual(values["Part"]["PartNumber"], "")
        self.assertEqual(staged["Part"]["PartNumber"], "7305")
        self.assertEqual(staged["Part"]["PartName"], "Existing name")
        self.assertEqual(staged["Part"]["TypeCode"], "R")
        self.assertEqual(staged["Part"]["ModelSeries"], "Explicit series")
        self.assertIn("保留现值", notes["Part"])

    def test_ambiguous_incomplete_labels_are_not_guessed(self):
        for label in ("Body", "Example bracket", "7305.1234-Example", "7305.004-H-SERIES-Example", "7305.004-Example.FCStd", "7305.004-A", "7305.004-", "7305.004-Name\nOther"):
            with self.subTest(label=label), self.assertRaises(model.ToolError):
                model.recommend_fields_from_label(label)

    def test_initialization_stages_missing_empty_fields_and_preserves_existing_locked_values(self):
        part = Object("Part", "Example part", PartNumber="explicit number", PartName="Explicit name")
        part.statuses["PartNumber"] = ["ReadOnly"]
        self.document.Objects = [part]
        part.Document = self.document
        before = [model._snapshot(part)]
        values = {part.Name: {field: "must not overwrite" for field, _ in model.BOM_FIELDS}}
        fields = [field for field, _ in model.BOM_FIELDS]
        rows = controller.build_property_preview(self.app, self.gui, self.document, [part], before, fields, values, initialize_only=True)
        self.assertEqual(self.document.events, [])
        self.assertEqual(sum(row["changed"] for row in rows), 3)
        self.assertTrue(all(row["new_value"] == "" for row in rows if row["missing"]))
        self.assertEqual(controller.apply_properties(self.app, self.gui, self.document, [part], before, fields, values, initialize_only=True), 3)
        self.assertEqual(part.PartNumber, "explicit number")
        self.assertEqual(part.PartName, "Explicit name")
        self.assertTrue(all(getattr(part, field) == "" for field in ("PartVersion", "TypeCode", "ModelSeries")))
        self.assertTrue(all(created[:1] == ("App::PropertyString",) and created[2] == "CustomDefine" for created in part.created))

    def test_link_attributes_are_never_forwarded_to_source(self):
        with self.assertRaisesRegex(model.ToolError, "源节点"):
            controller.apply_properties(self.app, self.gui, self.document, [self.link], [model._snapshot(self.link)], ["PartName"], {self.link.Name: {"PartName": "New"}})
        self.assertEqual(self.document.events, [])
        self.assertNotIn("PartName", self.source.PropertiesList)

    def test_direct_and_canonical_single_target_linksub_values_are_read_only(self):
        for value in (self.source, (self.source, []), [self.source, ""], [self.source], (self.source, ["Face1"])):
            self.link.LinkedObject = value
            row = self.preview()[0]
            self.assertEqual(row["error"], "")
            self.assertEqual(row["new_label"], "Example bracket")
            self.assertEqual(row["source_identity"], "SourceDocument#Source")
        self.assertEqual(self.document.events, [])
        self.assertEqual(self.link.Label, "Old instance")

    def test_same_source_instances_and_source_object_may_share_label(self):
        sibling = self.make_link("Instance2", "Example bracket")
        self.document.Objects.extend([self.source, sibling])
        self.source.Document = self.document
        sibling.Document = self.document
        rows = self.preview()
        self.assertEqual(rows[0]["error"], "")
        count = controller.apply_link_labels(self.app, self.gui, self.document, [self.link], [model._snapshot(self.link)], rows)
        self.assertEqual(count, 1)
        self.assertEqual(self.link.Label, self.source.Label)
        self.assertEqual(self.link.Name, "Instance")
        self.assertEqual(sibling.Label, "Example bracket")
        self.assertEqual(self.document.events, ["open", "commit"])

    def test_unrelated_objects_or_different_sources_with_same_label_conflict(self):
        unrelated = Object("Unrelated", self.source.Label)
        unrelated.Document = self.document
        self.document.Objects.append(unrelated)
        self.assertIn("无关对象", self.preview()[0]["error"])
        self.document.Objects.remove(unrelated)
        other_source = Object("OtherSource", self.source.Label)
        other_source.Document = self.source_doc
        self.source_doc.Objects.append(other_source)
        other_link = self.make_link("OtherInstance", self.source.Label, other_source)
        other_link.Document = self.document
        self.document.Objects.append(other_link)
        self.assertIn("不同来源", self.preview()[0]["error"])

    def test_broken_chained_multiple_and_nonlink_inputs_are_refused(self):
        source2 = Object("Source2", "Other part")
        source2.Document = self.source_doc
        self.source_doc.Objects.append(source2)
        chained = self.make_link("Chain", "Chained")
        chained.Document = self.source_doc
        self.source_doc.Objects.append(chained)
        for value in (None, [self.source, source2], (self.source, ["Face1", "Face2"]), chained):
            self.link.LinkedObject = value
            self.assertTrue(self.preview()[0]["error"])
        self.link.LinkedObject = self.source
        self.link.TypeId = "PartDesign::Body"
        self.assertTrue(self.preview()[0]["error"])

    def test_expression_control_and_references_block_label_changes(self):
        self.link.ExpressionEngine = [("Label", "Source.Label")]
        self.assertIn("表达式", self.preview()[0]["error"])
        self.link.ExpressionEngine = []
        self.source.ExpressionEngine = [("Length", "<<Old instance>>.Length")]
        self.assertIn("表达式", self.preview()[0]["error"])

    def test_already_matching_automatic_label_does_not_write_or_require_unlock(self):
        self.link.Label = self.source.Label
        self.link.statuses["Label"] = ["ReadOnly"]
        rows = self.preview()
        self.assertEqual(rows[0]["error"], "")
        self.assertEqual(controller.apply_link_labels(self.app, self.gui, self.document, [self.link], [model._snapshot(self.link)], rows), 0)
        self.assertEqual(self.document.events, [])

    def test_source_changes_invalidate_confirmation_without_side_effects(self):
        snapshots, rows = [model._snapshot(self.link)], self.preview()
        self.source.Label = "Changed source name"
        with self.assertRaises(model.ToolError):
            controller.apply_link_labels(self.app, self.gui, self.document, [self.link], snapshots, rows)
        self.assertEqual(self.link.Label, "Old instance")
        self.assertEqual(self.document.events, [])

    def test_multiple_same_source_selected_instances_can_sync_in_one_transaction(self):
        sibling = self.make_link("Instance2", "Old second instance")
        sibling.Document = self.document
        self.document.Objects.append(sibling)
        links = [self.link, sibling]
        rows = self.preview(links)
        self.assertTrue(all(not row["error"] for row in rows))
        count = controller.apply_link_labels(self.app, self.gui, self.document, links, [model._snapshot(link) for link in links], rows)
        self.assertEqual(count, 2)
        self.assertEqual([link.Label for link in links], [self.source.Label] * 2)
        self.assertEqual(self.document.events, ["open", "commit"])

    def test_matching_object_names_in_different_documents_are_distinct_sources(self):
        different_source = Object(self.source.Name, self.source.Label)
        different_document = Document([different_source])
        different_document.Name = "DifferentSourceDocument"
        other_link = self.make_link("DifferentInstance", "Old different instance", different_source)
        other_link.Document = self.document
        self.document.Objects.append(other_link)
        rows = self.preview([self.link, other_link])
        self.assertTrue(all("不同来源" in row["error"] for row in rows))

    def test_native_auto_numbering_aborts_instead_of_reporting_success(self):
        class AutoNumbered(Object):
            def __setattr__(self, field, value):
                if field == "Label" and getattr(self, "auto_number", False):
                    value += "001"
                super().__setattr__(field, value)
        sibling = AutoNumbered("Instance2", "Old second instance")
        sibling.TypeId = "App::Link"
        sibling.LinkedObject = self.source
        sibling.Document = self.document
        sibling.auto_number = True
        self.document.Objects.append(sibling)
        links = [self.link, sibling]
        rows = self.preview(links)
        with self.assertRaisesRegex(model.ToolError, "自动调整"):
            controller.apply_link_labels(self.app, self.gui, self.document, links, [model._snapshot(link) for link in links], rows)
        self.assertEqual(self.document.events, ["open", "abort"])
        self.assertEqual(self.link.Label, "Old instance")
        self.assertEqual(sibling.Label, "Old second instance")
        self.assertEqual(self.source.Label, "Example bracket")

    def test_link_label_failure_rolls_back_all_instance_changes(self):
        class FailingObject(Object):
            def __setattr__(self, field, value):
                if field == "Label" and getattr(self, "fail_label_write", False):
                    raise RuntimeError("simulated instance failure")
                super().__setattr__(field, value)
        sibling = FailingObject("Instance2", "Old second instance")
        sibling.TypeId = "App::Link"
        sibling.LinkedObject = self.source
        sibling.Document = self.document
        sibling.fail_label_write = True
        self.document.Objects.append(sibling)
        links = [self.link, sibling]
        rows = self.preview(links)
        with self.assertRaises(RuntimeError):
            controller.apply_link_labels(self.app, self.gui, self.document, links, [model._snapshot(link) for link in links], rows)
        self.assertEqual(self.document.events, ["open", "abort"])
        self.assertEqual(self.link.Label, "Old instance")
        self.assertEqual(sibling.Label, "Old second instance")
        self.assertEqual(self.source.Label, "Example bracket")

    def test_temporary_preference_restores_false_after_success_and_exception(self):
        with controller._temporary_duplicate_link_labels(self.app):
            self.assertTrue(self.prefs.GetBool("DuplicateLabels"))
        self.assertIs(self.prefs.GetBool("DuplicateLabels"), False)
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            with controller._temporary_duplicate_link_labels(self.app):
                self.assertTrue(self.prefs.GetBool("DuplicateLabels"))
                raise RuntimeError("simulated write error")
        self.assertIs(self.prefs.GetBool("DuplicateLabels"), False)

    def test_temporary_preference_restores_absent_key_and_does_not_rewrite_true(self):
        self.prefs.values.clear()
        with controller._temporary_duplicate_link_labels(self.app):
            self.assertTrue(self.prefs.GetBool("DuplicateLabels"))
        self.assertNotIn("DuplicateLabels", self.prefs.GetBools())
        self.assertEqual(self.prefs.events[-1], ("remove", "DuplicateLabels"))
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            with controller._temporary_duplicate_link_labels(self.app):
                raise RuntimeError("simulated missing-key write failure")
        self.assertNotIn("DuplicateLabels", self.prefs.GetBools())
        self.prefs.values["DuplicateLabels"] = True
        self.prefs.events.clear()
        with controller._temporary_duplicate_link_labels(self.app):
            self.assertTrue(self.prefs.GetBool("DuplicateLabels"))
        self.assertIs(self.prefs.GetBool("DuplicateLabels"), True)
        self.assertEqual(self.prefs.events, [])

    def test_temporary_preference_rejects_worker_threads_before_any_setting_change(self):
        import threading
        failures = []
        def worker():
            try:
                with controller._temporary_duplicate_link_labels(self.app):
                    failures.append("unexpected success")
            except model.ToolError as error:
                failures.append(str(error))
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(len(failures), 1)
        self.assertIn("主界面线程", failures[0])
        self.assertEqual(self.prefs.events, [])

    def test_native_uniqueness_policy_is_temporarily_relaxed_and_undo_redo_keep_preferences(self):
        prefs = self.prefs
        class NativeUniqueLink(Object):
            def __setattr__(self, field, value):
                if field == "Label" and getattr(self, "native_check", False) and not prefs.GetBool("DuplicateLabels"):
                    value += "001"
                super().__setattr__(field, value)
        class UndoDocument(Document):
            def commitTransaction(self):
                super().commitTransaction()
                self.undo_state = self.before
                self.redo_state = [(obj, dict(obj.__dict__)) for obj in self.Objects]
            def restore_state(self, state):
                for obj, values in state:
                    obj.__dict__.clear()
                    obj.__dict__.update(values)
            def undo(self): self.restore_state(self.undo_state)
            def redo(self): self.restore_state(self.redo_state)
        link = NativeUniqueLink("NativeInstance", "Before sync")
        link.TypeId = "App::Link"
        link.LinkedObject = self.source
        link.native_check = True
        self.document = UndoDocument([link])
        self.app.ActiveDocument = self.document
        rows = model.build_link_label_preview(self.document, [link], self.app.listDocuments().values())
        count = controller.apply_link_labels(self.app, self.gui, self.document, [link], [model._snapshot(link)], rows)
        self.assertEqual(count, 1)
        self.assertEqual(link.Label, self.source.Label)
        self.assertIs(prefs.GetBool("DuplicateLabels"), False)
        setting_events = list(prefs.events)
        self.document.undo()
        self.assertEqual(link.Label, "Before sync")
        self.document.redo()
        self.assertEqual(link.Label, self.source.Label)
        self.assertIs(prefs.GetBool("DuplicateLabels"), False)
        self.assertEqual(prefs.events, setting_events)
        self.assertEqual(self.source.Label, "Example bracket")


    def install_label_callback(self, callback):
        class CallbackLink(Object):
            def __setattr__(self, field, value):
                super().__setattr__(field, value)
                if field == "Label" and "on_label" in self.__dict__:
                    self.on_label()
        link = CallbackLink("CallbackInstance", "Before callback")
        link.TypeId = "App::Link"
        link.LinkedObject = self.source
        link.Document = self.document
        link.on_label = callback
        self.document.Objects[0] = link
        self.link = link
        return link

    def test_final_callback_conflict_rolls_back_before_preference_restore(self):
        trace = []
        original_set = self.prefs.SetBool
        def set_bool(key, value):
            trace.append(("pref", value))
            original_set(key, value)
        self.prefs.SetBool = set_bool
        original_abort = self.document.abortTransaction
        def abort():
            trace.append(("abort", self.prefs.GetBool("DuplicateLabels")))
            original_abort()
        self.document.abortTransaction = abort
        def add_conflict():
            unrelated = Object("CallbackConflict", self.source.Label)
            unrelated.Document = self.document
            self.document.Objects.append(unrelated)
        link = self.install_label_callback(add_conflict)
        rows = self.preview()
        with self.assertRaisesRegex(model.ToolError, "重名"):
            controller.apply_link_labels(self.app, self.gui, self.document, [link], [model._snapshot(link)], rows)
        self.assertEqual(link.Label, "Before callback")
        self.assertEqual(trace, [("pref", True), ("abort", True), ("pref", False)])
        self.assertEqual(self.document.events, ["open", "abort"])
        self.assertIs(self.prefs.GetBool("DuplicateLabels"), False)

    def test_final_callback_source_identity_change_is_rejected(self):
        other = Object("OtherSource", self.source.Label)
        other.Document = self.source_doc
        self.source_doc.Objects.append(other)
        link = self.install_label_callback(lambda: setattr(self.link, "LinkedObject", other))
        rows = self.preview()
        with self.assertRaisesRegex(model.ToolError, "来源"):
            controller.apply_link_labels(self.app, self.gui, self.document, [link], [model._snapshot(link)], rows)
        self.assertIs(link.LinkedObject, self.source)
        self.assertEqual(link.Label, "Before callback")
        self.assertIs(self.prefs.GetBool("DuplicateLabels"), False)

    def test_final_callback_source_label_change_is_rejected_and_rolled_back(self):
        self.source.Document = self.document
        self.document.Objects.append(self.source)
        link = self.install_label_callback(lambda: setattr(self.source, "Label", "Changed during sync"))
        rows = self.preview()
        with self.assertRaisesRegex(model.ToolError, "来源"):
            controller.apply_link_labels(self.app, self.gui, self.document, [link], [model._snapshot(link)], rows)
        self.assertEqual(self.source.Label, "Example bracket")
        self.assertEqual(link.Label, "Before callback")
        self.assertIs(self.prefs.GetBool("DuplicateLabels"), False)

    def test_success_checks_while_enabled_then_restores_before_commit(self):
        trace = []
        original_validate = controller.validate_final_link_labels
        original_commit = self.document.commitTransaction
        def validate(document, rows):
            trace.append(("validate", self.prefs.GetBool("DuplicateLabels")))
            return original_validate(document, rows)
        def commit():
            trace.append(("commit", self.prefs.GetBool("DuplicateLabels")))
            original_commit()
        controller.validate_final_link_labels = validate
        self.document.commitTransaction = commit
        try:
            rows = self.preview()
            self.assertEqual(controller.apply_link_labels(self.app, self.gui, self.document, [self.link], [model._snapshot(self.link)], rows), 1)
        finally:
            controller.validate_final_link_labels = original_validate
        self.assertEqual(trace, [("validate", True), ("commit", False)])
        self.assertEqual(self.link.Label, self.source.Label)
        self.assertIs(self.prefs.GetBool("DuplicateLabels"), False)


if __name__ == "__main__":
    unittest.main()
