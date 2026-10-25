# SPDX-License-Identifier: Apache-2.0
"""FreeCAD 1.1 native return types, exercised only with synthetic Python objects."""
import types
import unittest
from unittest.mock import patch

import test_pft_business as fixtures

business = fixtures.business
model, controller = business._mvc()


def unreadable(*args):
    raise RuntimeError("synthetic native details")


class NativeObject(fixtures.Object):
    def __init__(self, *args, **fields):
        super().__init__(*args, **fields)
        self.statuses, self.editor_modes = {}, {}

    def getPropertyStatus(self, field):
        return self.statuses.get(field, [])

    def getEditorMode(self, field):
        return self.editor_modes.get(field, [])


class PropertyGuardTests(unittest.TestCase):
    def setUp(self):
        self.obj = NativeObject("Part", "Old example", PartName="Example part")
        self.before = model._snapshot(self.obj)

    def assert_rejected(self):
        with self.assertRaises(model.PftToolError) as failure:
            model._ensure_writable(self.obj, "PartName")
        self.assertNotIn("synthetic native details", str(failure.exception))
        self.assertEqual(model._snapshot(self.obj), self.before)
        self.assertEqual(self.obj.created, [])

    def test_native_static_and_dynamic_protection_flags_are_rejected(self):
        for statuses, modes in (([24], []), (["ReadOnly"], []), (["Immutable"], []),
                                (["LockDynamic"], []), ([], ["ReadOnly"]),
                                ([21, 24], ["ReadOnly", "Hidden"])):
            with self.subTest(statuses=statuses, modes=modes):
                self.obj.statuses["PartName"] = statuses
                self.obj.editor_modes["PartName"] = modes
                self.assert_rejected()

    def test_hidden_only_and_native_nonblocking_flags_are_writable(self):
        names = ("Hidden", "Transient", "MaterialEdit", "NoMaterialListEdit", "Output",
                 "NoModify", "PartialTrigger", "NoRecompute", "CopyOnChange", "UserEdit", "AllowPartial")
        bits = (12, 13, 14, 15, 18, 19, 20, 21, 22, 23, 25, 26, 27, 28, 29, 30, 31)
        for statuses in ([], [21, 23, 26], list(names), list(bits)):
            with self.subTest(statuses=statuses):
                self.obj.statuses["PartName"] = statuses
                self.obj.editor_modes["PartName"] = ["Hidden"]
                model._ensure_writable(self.obj, "PartName")
                self.assertEqual(model._snapshot(self.obj), self.before)
                self.assertEqual(self.obj.created, [])

    def test_status_requires_native_list_with_known_exact_flags(self):
        values = (None, "ReadOnly", (), set(), {}, 0, False, [True], [False], [0], [1],
                  [16], [17], [32], [-1], [1 << 24], ["FutureFlag"], ["readonly"],
                  [[24]], [{"ReadOnly": True}], [21, 21], ["Hidden", "Hidden"])
        for value in values:
            with self.subTest(value=value):
                self.obj.statuses["PartName"] = value
                self.assert_rejected()

    def test_editor_modes_are_native_lists_without_integer_compatibility(self):
        values = (None, 0, 1, False, True, "ReadOnly", (), {}, [True], [0], [1],
                  ["FutureFlag"], ["hidden"], ["Hidden", "Hidden"], [["Hidden"]])
        for value in values:
            with self.subTest(value=value):
                self.obj.editor_modes["PartName"] = value
                self.assert_rejected()

    def test_both_native_readers_are_mandatory_and_read_failures_are_fixed(self):
        for api in ("getPropertyStatus", "getEditorMode"):
            for replacement in (None, unreadable):
                with self.subTest(api=api, replacement=replacement):
                    with patch.object(self.obj, api, replacement):
                        self.assert_rejected()
            missing = types.SimpleNamespace(**self.obj.__dict__)
            missing.getTypeIdOfProperty = self.obj.getTypeIdOfProperty
            for other_api in ("getPropertyStatus", "getEditorMode"):
                if api != other_api:
                    setattr(missing, other_api, getattr(self.obj, other_api))
            with self.subTest(missing=api), self.assertRaises(model.PftToolError):
                model._ensure_writable(missing, "PartName")

    def test_initialization_needs_native_readers_but_does_not_read_nonexistent_property(self):
        with patch.object(self.obj, "getPropertyStatus", side_effect=AssertionError("absent property")), \
             patch.object(self.obj, "getEditorMode", side_effect=AssertionError("absent property")):
            model._ensure_writable(self.obj, "ModelSeries", may_add=True)
        with patch.object(self.obj, "getPropertyStatus", None), self.assertRaises(model.PftToolError):
            model._ensure_writable(self.obj, "ModelSeries", may_add=True)
        self.assertEqual(self.obj.created, [])


class IdleGuardTests(unittest.TestCase):
    def setUp(self):
        self.doc = types.SimpleNamespace(Name="Demo", HasPendingTransaction=False, UndoMode=1)
        self.app = types.SimpleNamespace(ActiveDocument=self.doc, getActiveTransaction=lambda: None)
        self.gui_doc = types.SimpleNamespace(getInEdit=lambda: None)
        self.gui = types.SimpleNamespace(getDocument=lambda name: self.gui_doc)

    def check(self):
        controller._ensure_idle(self.app, self.gui, self.doc)

    def assert_rejected(self):
        with self.assertRaises(model.PftToolError) as failure:
            self.check()
        self.assertNotIn("synthetic native details", str(failure.exception))

    def test_known_idle_requires_exact_native_none_false_and_positive_int(self):
        for mode in (1, 2):
            self.doc.UndoMode = mode
            self.check()

    def test_falsey_non_none_edit_values_are_never_idle(self):
        for value in (False, 0, "", [], (), {}, object()):
            with self.subTest(value=value):
                self.gui_doc.getInEdit = lambda value=value: value
                self.assert_rejected()

    def test_missing_or_unreadable_gui_edit_api_is_never_idle(self):
        for value in (None, types.SimpleNamespace(), types.SimpleNamespace(getInEdit=None),
                      types.SimpleNamespace(getInEdit=unreadable)):
            with self.subTest(gui_document=value):
                self.gui.getDocument = lambda name, value=value: value
                self.assert_rejected()
        self.gui.getDocument = unreadable
        self.assert_rejected()

    def test_global_transaction_none_does_not_bypass_document_pending_state(self):
        for value in (True, None, 0, 1, "", [], (), {}):
            with self.subTest(pending=value):
                self.doc.HasPendingTransaction = value
                self.assert_rejected()
        del self.doc.HasPendingTransaction
        self.assert_rejected()

    def test_pending_and_undo_native_read_failures_are_fixed(self):
        for field in ("HasPendingTransaction", "UndoMode"):
            class UnreadableDocument:
                Name = "Demo"
                HasPendingTransaction = False
                UndoMode = 1
                def __getattribute__(self, name):
                    if name == field:
                        return unreadable()
                    return super().__getattribute__(name)
            with self.subTest(field=field):
                self.doc = UnreadableDocument()
                self.app.ActiveDocument = self.doc
                self.assert_rejected()

    def test_undo_missing_malformed_and_disabled_are_rejected(self):
        for value in (0, -1, None, False, True, "1", 1.0, [], {}):
            with self.subTest(undo=value):
                self.doc.UndoMode = value
                self.assert_rejected()
        del self.doc.UndoMode
        self.assert_rejected()


class PreviewApplyGuardTests(unittest.TestCase):
    OPERATIONS = ("normalize_names", "sync_link_labels", "set_properties")

    def setUp(self):
        with business._lock:
            business._plans.clear()

    def context(self, operation):
        self.part = NativeObject("Part", "Old example", PartNumber="5120", PartVersion="001", PartName="Example part")
        self.other = NativeObject("Other", "Other example")
        self.source = NativeObject("Source", "Example source")
        self.source_doc = fixtures.Document("SourceDemo", [self.source])
        self.doc = fixtures.Document("Demo", [self.part, self.other])
        self.target = self.part
        if operation == "sync_link_labels":
            self.target = NativeObject("Instance", "Old instance")
            self.target.TypeId = "App::Link"
            self.target.LinkedObject = self.source
            self.target.Document = self.doc
            self.doc.Objects.append(self.target)
        self.field = "PartName" if operation == "set_properties" else "Label"
        self.prefs = fixtures.Preferences()
        documents = {self.doc.Name: self.doc, self.source_doc.Name: self.source_doc}
        self.app = types.SimpleNamespace(ActiveDocument=self.doc, getActiveTransaction=lambda: self.doc.active_transaction,
            getDocument=documents.get, listDocuments=lambda: documents, ParamGet=lambda path: self.prefs)
        self.gui_doc = types.SimpleNamespace(getInEdit=lambda: None)
        self.gui = types.SimpleNamespace(getDocument=lambda name: self.gui_doc)
        self.params = {"docName": self.doc.Name, "objectNames": [self.target.Name], "operation": operation}
        if operation == "set_properties":
            self.params["values"] = {self.target.Name: {"PartName": "New example part"}}
        self.original = [(obj, model._snapshot(obj)) for obj in (*self.doc.Objects, self.source)]

    def call(self, method, params):
        return business.perform(method, params, self.app, self.gui, fixtures.Error)

    def prepare(self):
        return self.call("prepare_part_changes", self.params)

    def apply(self, token):
        return self.call("apply_part_changes", {"token": token})

    def assert_untouched(self):
        for obj, snapshot in self.original:
            self.assertEqual(model._snapshot(obj), snapshot)
            self.assertEqual(obj.created, [])
        self.assertEqual(self.doc.events, [])
        self.assertEqual(self.source_doc.events, [])
        self.assertEqual(self.prefs.events, [])

    def assert_consumed(self, token):
        with self.assertRaises(fixtures.Error) as failure:
            self.apply(token)
        self.assertEqual(failure.exception.code, "PART_TOKEN_USED")
        self.assert_untouched()

    def test_native_protection_blocks_all_three_previews_without_writes(self):
        for operation in self.OPERATIONS:
            for protection in ("editor", "static", "immutable", "unreadable"):
                with self.subTest(operation=operation, protection=protection):
                    self.context(operation)
                    if protection == "editor":
                        self.target.editor_modes[self.field] = ["ReadOnly"]
                    elif protection == "static":
                        self.target.statuses[self.field] = [24]
                    elif protection == "immutable":
                        self.target.statuses[self.field] = ["Immutable"]
                    else:
                        self.target.getPropertyStatus = unreadable
                    preview = self.prepare()
                    self.assertFalse(preview["can_apply"])
                    self.assertEqual(preview["rows"][0]["status"], "blocked")
                    self.assertNotIn("synthetic native details", str(preview))
                    with self.assertRaises(fixtures.Error) as failure:
                        self.apply(preview["token"])
                    self.assertEqual(failure.exception.code, "PART_PREVIEW_BLOCKED")
                    self.assert_consumed(preview["token"])

    def test_property_flags_changing_after_preview_block_every_apply_before_transaction(self):
        for operation in self.OPERATIONS:
            for protection in ("editor", "static", "unreadable"):
                with self.subTest(operation=operation, protection=protection):
                    self.context(operation)
                    preview = self.prepare()
                    self.assertTrue(preview["can_apply"])
                    if protection == "editor":
                        self.target.editor_modes[self.field] = ["ReadOnly"]
                    elif protection == "static":
                        self.target.statuses[self.field] = [24]
                    else:
                        self.target.getEditorMode = unreadable
                    with self.assertRaises(fixtures.Error) as failure:
                        self.apply(preview["token"])
                    self.assertEqual(failure.exception.code, "PART_CHANGE_FAILED")
                    self.assert_consumed(preview["token"])

    def test_hidden_only_remains_writable_in_all_three_existing_paths(self):
        for operation in self.OPERATIONS:
            with self.subTest(operation=operation):
                self.context(operation)
                self.target.statuses[self.field] = [21, 23, 26]
                self.target.editor_modes[self.field] = ["Hidden"]
                preview = self.prepare()
                self.assertTrue(preview["can_apply"])
                self.assertEqual(self.apply(preview["token"])["changed_count"], 1)
                self.assertEqual(self.doc.events, ["open", "commit"])
                for obj, snapshot in self.original:
                    if obj is not self.target:
                        self.assertEqual(model._snapshot(obj), snapshot)
                self.assertEqual(self.source_doc.events, [])
                self.assertFalse(self.prefs.bools["DuplicateLabels"])

    def test_document_state_changes_after_preview_reject_without_any_writes_or_closure(self):
        states = ("pending", "unknown_pending", "missing_pending", "edit_false", "edit_zero", "edit_empty",
                  "missing_edit", "edit_error", "undo_disabled", "undo_bool", "undo_string", "missing_undo")
        for operation in self.OPERATIONS:
            for state in states:
                with self.subTest(operation=operation, state=state):
                    self.context(operation)
                    preview = self.prepare()
                    self.assertTrue(preview["can_apply"])
                    if state in {"pending", "unknown_pending"}:
                        self.doc.HasPendingTransaction = True if state == "pending" else 0
                    elif state == "missing_pending":
                        self.doc.__class__ = type("MissingPendingDocument", (), {
                            key: value for key, value in fixtures.Document.__dict__.items()
                            if key not in {"HasPendingTransaction", "__dict__", "__weakref__"}})
                    elif state.startswith("edit_"):
                        edit = {"edit_false": False, "edit_zero": 0, "edit_empty": []}.get(state)
                        self.gui_doc.getInEdit = unreadable if state == "edit_error" else lambda edit=edit: edit
                    elif state == "missing_edit":
                        del self.gui_doc.getInEdit
                    elif state == "missing_undo":
                        self.doc.__class__ = type("MissingUndoDocument", (), {
                            key: value for key, value in fixtures.Document.__dict__.items()
                            if key not in {"UndoMode", "__dict__", "__weakref__"}})
                    else:
                        self.doc.UndoMode = {"undo_disabled": 0, "undo_bool": True, "undo_string": "1"}[state]
                    with self.assertRaises(fixtures.Error) as failure:
                        self.apply(preview["token"])
                    self.assertEqual(failure.exception.code, "PART_PLAN_STALE")
                    self.assert_consumed(preview["token"])


class CallbackObject(NativeObject):
    def __setattr__(self, field, value):
        super().__setattr__(field, value)
        if field in {"Label", "PartName", "TypeCode", "ModelSeries"}:
            trace = self.__dict__.get("write_trace")
            if trace is not None:
                trace.append((self.Name, field, value))
            callback = self.__dict__.get("after_write")
            if callback is not None:
                callback(field, value)


class SynchronousWriteGuardTests(unittest.TestCase):
    def context(self, operation):
        self.first = CallbackObject("First", "Old first", PartNumber="5120", PartVersion="001", PartName="First example", TypeCode="H")
        self.second = CallbackObject("Second", "Old second", PartNumber="5121", PartVersion="001", PartName="Second example", TypeCode="H")
        self.document = fixtures.Document("Demo", [self.first, self.second])
        self.sources = [NativeObject("SourceFirst", "First source"), NativeObject("SourceSecond", "Second source")]
        self.source_document = fixtures.Document("SourceDemo", self.sources)
        self.prefs = fixtures.Preferences()
        self.operation = operation
        if operation == "sync_link_labels":
            for target, source in zip((self.first, self.second), self.sources):
                target.TypeId, target.LinkedObject = "App::Link", source
        documents = {self.document.Name: self.document, self.source_document.Name: self.source_document}
        self.app = types.SimpleNamespace(ActiveDocument=self.document,
            getActiveTransaction=lambda: self.document.active_transaction,
            listDocuments=lambda: documents, ParamGet=lambda path: self.prefs)
        self.gui = types.SimpleNamespace(getDocument=lambda name: types.SimpleNamespace(getInEdit=lambda: None))
        self.trace = []
        for obj in (self.first, self.second):
            obj.write_trace = self.trace
        self.snapshots = [model._snapshot(obj) for obj in (self.first, self.second)]
        self.source_snapshots = [model._snapshot(obj) for obj in self.sources]
        if operation != "set_properties":
            builder = model.build_link_label_preview if operation == "sync_link_labels" else model.build_rename_preview
            self.rows = builder(self.document, [self.first, self.second], documents.values())
            self.assertFalse(any(row["error"] for row in self.rows))

    def apply(self):
        if self.operation == "set_properties":
            return controller.apply_properties(self.app, self.gui, self.document, [self.first, self.second],
                self.snapshots, ["PartName"], {"First": {"PartName": "New first"}, "Second": {"PartName": "New second"}})
        apply = controller.apply_link_labels if self.operation == "sync_link_labels" else controller.apply_rename
        return apply(self.app, self.gui, self.document, [self.first, self.second], self.snapshots, self.rows)

    def assert_rolled_back(self):
        self.assertEqual(self.document.events, ["open", "abort"])
        self.assertIsNone(self.document.active_transaction)
        self.assertEqual([model._snapshot(obj) for obj in (self.first, self.second)], self.snapshots)
        self.assertEqual([model._snapshot(obj) for obj in self.sources], self.source_snapshots)
        self.assertEqual(self.source_document.events, [])
        self.assertFalse(self.prefs.bools["DuplicateLabels"])

    def test_previous_label_or_bom_setter_cannot_write_next_newly_locked_target(self):
        for operation in PreviewApplyGuardTests.OPERATIONS:
            for protection in ("editor", "static"):
                with self.subTest(operation=operation, protection=protection):
                    self.context(operation)
                    field = "PartName" if operation == "set_properties" else "Label"
                    def lock_next(changed_field, value):
                        if changed_field == field:
                            if protection == "editor":
                                self.second.editor_modes[field] = ["ReadOnly"]
                            else:
                                self.second.statuses[field] = [24]
                    self.first.after_write = lock_next
                    with self.assertRaises(model.PftToolError):
                        self.apply()
                    self.assertEqual([(name, key) for name, key, _ in self.trace], [("First", field)])
                    self.assert_rolled_back()

    def test_previous_bom_field_setter_cannot_write_next_newly_locked_field(self):
        for protection in ("editor", "static"):
            with self.subTest(protection=protection):
                self.context("set_properties")
                def lock_type_code(field, value):
                    if field == "PartName":
                        if protection == "editor":
                            self.first.editor_modes["TypeCode"] = ["ReadOnly"]
                        else:
                            self.first.statuses["TypeCode"] = [24]
                self.first.after_write = lock_type_code
                with self.assertRaises(model.PftToolError):
                    controller.apply_properties(self.app, self.gui, self.document, [self.first],
                        self.snapshots[:1], ["PartName", "TypeCode"],
                        {"First": {"PartName": "New first", "TypeCode": "R"}})
                self.assertEqual(self.trace, [("First", "PartName", "New first")])
                self.assert_rolled_back()

    def test_newly_added_property_is_rechecked_before_requested_setter_and_rolled_back(self):
        for protection in ("editor", "static"):
            with self.subTest(protection=protection):
                self.context("set_properties")
                original_add = self.first.addProperty
                def add_locked(kind, field, group, description):
                    original_add(kind, field, group, description)
                    if protection == "editor":
                        self.first.editor_modes[field] = ["ReadOnly"]
                    else:
                        self.first.statuses[field] = [24]
                self.first.addProperty = add_locked
                with self.assertRaises(model.PftToolError):
                    controller.apply_properties(self.app, self.gui, self.document, [self.first],
                        self.snapshots[:1], ["ModelSeries"], {"First": {"ModelSeries": "EXAMPLE"}})
                # The fake native add initializes an empty value internally;
                # the requested Python property setter must never run.
                self.assertNotIn(("First", "ModelSeries", "EXAMPLE"), self.trace)
                self.assertNotIn("ModelSeries", self.first.PropertiesList)
                self.assertEqual(self.first.created, [])
                self.assert_rolled_back()

    def test_guard_read_callback_cannot_write_or_close_a_foreign_transaction(self):
        for operation in PreviewApplyGuardTests.OPERATIONS:
            with self.subTest(operation=operation):
                self.context(operation)
                field = "PartName" if operation == "set_properties" else "Label"
                original_mode = self.first.getEditorMode
                def mode_with_takeover(requested_field):
                    modes = original_mode(requested_field)
                    if requested_field == field and self.document.active_transaction is not None:
                        self.document.active_transaction = ("Foreign fixture operation", 900)
                    return modes
                self.first.getEditorMode = mode_with_takeover
                with self.assertRaises(controller.PftWriteError) as failure:
                    self.apply()
                self.assertEqual(failure.exception.code, "PART_TRANSACTION_CHANGED")
                self.assertEqual(self.trace, [])
                self.assertEqual(self.document.events, ["open"])
                self.assertEqual(self.document.active_transaction, ("Foreign fixture operation", 900))
                self.assertEqual([model._snapshot(obj) for obj in (self.first, self.second)], self.snapshots)
                self.assertFalse(self.prefs.bools["DuplicateLabels"])

    def test_locking_callback_and_foreign_takeover_stop_without_closing_foreign_edit(self):
        for operation in PreviewApplyGuardTests.OPERATIONS:
            with self.subTest(operation=operation):
                self.context(operation)
                field = "PartName" if operation == "set_properties" else "Label"
                def lock_and_takeover(changed_field, value):
                    if changed_field == field:
                        self.second.statuses[field] = [24]
                        self.document.active_transaction = ("Foreign fixture operation", 900)
                self.first.after_write = lock_and_takeover
                with self.assertRaises(controller.PftWriteError) as failure:
                    self.apply()
                self.assertEqual(failure.exception.code, "PART_TRANSACTION_CHANGED")
                self.assertEqual([(name, key) for name, key, _ in self.trace], [("First", field)])
                self.assertEqual(self.document.events, ["open"])
                self.assertEqual(self.document.active_transaction, ("Foreign fixture operation", 900))
                self.assertEqual(model._snapshot(self.second), self.snapshots[1])
                self.assertFalse(self.prefs.bools["DuplicateLabels"])


if __name__ == "__main__":
    unittest.main()
