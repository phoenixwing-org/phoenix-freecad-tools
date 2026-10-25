# SPDX-License-Identifier: Apache-2.0
"""Native-style transaction ownership failures must never close another edit."""
from pathlib import Path
import sys
import types
import unittest

from test_pft_workbench_tools import Document, Object

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
sys.path.insert(0, str(ADDON))
try:
    import pft_workbench_controller as controller
    import pft_workbench_model as model
finally:
    sys.path.remove(str(ADDON))


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.part = Object("Part", "Example", PartName="Before", TypeCode="H")
        self.document = Document([self.part])
        self.app = types.SimpleNamespace(
            ActiveDocument=self.document,
            getActiveTransaction=lambda: self.document.active_transaction,
            listDocuments=lambda: {self.document.Name: self.document},
        )
        self.gui = types.SimpleNamespace(getDocument=lambda name: types.SimpleNamespace(getInEdit=lambda: None))

    def run_write(self, write=None):
        def default_write(check):
            self.part.PartName = "After"
            check()
        return controller._run_transaction(self.app, self.document, "Public fixture edit", write or default_write)

    def assert_code(self, code, callback):
        with self.assertRaises(controller.PftWriteError) as failure:
            callback()
        self.assertEqual(failure.exception.code, code)
        return failure.exception

    def fail_write(self, check):
        self.part.PartName = "Partial"
        raise ValueError("synthetic setter failure")

    def apply_properties(self, values=None):
        values = values or {"PartName": "After", "TypeCode": "R"}
        return controller.apply_properties(self.app, self.gui, self.document, [self.part],
            [model._snapshot(self.part)], list(values), {self.part.Name: values})

    def install_setter(self, callback):
        class CallbackObject(Object):
            def __setattr__(self, name, value):
                super().__setattr__(name, value)
                if name == "PartName" and "after_set" in self.__dict__:
                    self.after_set()
        self.part.__class__ = CallbackObject
        self.part.after_set = callback

    def test_known_id_commits_once_and_is_closed(self):
        self.run_write()
        self.assertEqual(self.document.events, ["open", "commit"])
        self.assertIsNone(self.document.active_transaction)
        self.assertEqual(self.part.PartName, "After")

    def test_only_exact_none_is_idle_before_open(self):
        for active in (False, 0, "", (), [], ("Other", 9)):
            with self.subTest(active=active):
                self.document.active_transaction = active
                self.assert_code("PART_TRANSACTION_CHANGED", self.run_write)
                self.assertEqual(self.document.events, [])
                self.assertEqual(self.part.PartName, "Before")

    def test_unreadable_preexisting_transaction_does_not_open(self):
        self.app.getActiveTransaction = lambda: (_ for _ in ()).throw(RuntimeError("private native details"))
        error = self.assert_code("PART_TRANSACTION_STATE_UNAVAILABLE", self.run_write)
        self.assertNotIn("private native details", str(error))
        self.assertEqual(self.document.events, [])

    def test_invalid_open_identity_never_writes_or_guesses_abort(self):
        original = self.document.openTransaction
        for active in (None, ("Public fixture edit", True), ("Public fixture edit", 0),
                       ("Public fixture edit", -1), ("Public fixture edit", "1"), (3, 1), ("x", 1, 2)):
            with self.subTest(active=active):
                self.document.events.clear()
                self.document.active_transaction = None
                def open_invalid(title):
                    original(title)
                    self.document.active_transaction = active
                self.document.openTransaction = open_invalid
                self.assert_code("PART_TRANSACTION_OPEN_UNVERIFIED", self.run_write)
                self.assertEqual(self.document.events, ["open"])
                self.assertEqual(self.part.PartName, "Before")

    def test_open_exception_after_native_state_change_does_not_abort_unknown_id(self):
        original = self.document.openTransaction
        def open_raising(title):
            original(title)
            raise RuntimeError("synthetic open failure")
        self.document.openTransaction = open_raising
        self.assert_code("PART_TRANSACTION_OPEN_UNVERIFIED", self.run_write)
        self.assertEqual(self.document.events, ["open"])
        self.assertIsNotNone(self.document.active_transaction)
        self.assertEqual(self.part.PartName, "Before")

    def test_open_read_exception_leaves_unknown_transaction_for_explicit_review(self):
        original = self.app.getActiveTransaction
        def read():
            if self.document.events:
                raise RuntimeError("synthetic unavailable state")
            return original()
        self.app.getActiveTransaction = read
        self.assert_code("PART_TRANSACTION_OPEN_UNVERIFIED", self.run_write)
        self.assertEqual(self.document.events, ["open"])
        self.assertEqual(self.part.PartName, "Before")

    def test_same_title_different_id_stops_batch_before_next_property(self):
        def change_id():
            title, old_id = self.document.active_transaction
            self.document.active_transaction = (title, old_id + 1)
        self.install_setter(change_id)
        self.assert_code("PART_TRANSACTION_CHANGED", self.apply_properties)
        self.assertEqual(self.document.events, ["open"])
        self.assertEqual(self.part.PartName, "After")  # No false claim of rollback.
        self.assertEqual(self.part.TypeCode, "H")

    def test_write_failure_aborts_only_owned_id_once(self):
        with self.assertRaisesRegex(ValueError, "synthetic setter failure"):
            self.run_write(self.fail_write)
        self.assertEqual(self.document.events, ["open", "abort"])
        self.assertIsNone(self.document.active_transaction)
        self.assertEqual(self.part.PartName, "Before")

    def test_failure_after_foreign_takeover_does_not_abort_it(self):
        def write(check):
            self.part.PartName = "Partial"
            self.document.active_transaction = ("Other operation", 900)
            raise ValueError("synthetic write failure")
        self.assert_code("PART_TRANSACTION_CHANGED", lambda: self.run_write(write))
        self.assertEqual(self.document.events, ["open"])
        self.assertEqual(self.document.active_transaction, ("Other operation", 900))

    def test_read_failure_after_write_does_not_guess_owner(self):
        def write(check):
            self.part.PartName = "Partial"
            self.app.getActiveTransaction = lambda: (_ for _ in ()).throw(RuntimeError("unavailable"))
            check()
        self.assert_code("PART_TRANSACTION_CHANGED", lambda: self.run_write(write))
        self.assertEqual(self.document.events, ["open"])

    def test_commit_without_closure_or_with_foreign_id_never_triggers_abort(self):
        for mode in ("same", "foreign", "malformed"):
            with self.subTest(mode=mode):
                self.setUp()
                def commit():
                    self.document.events.append("commit")
                    if mode == "foreign": self.document.active_transaction = ("Other", 100)
                    if mode == "malformed": self.document.active_transaction = False
                self.document.commitTransaction = commit
                self.assert_code("PART_COMMIT_UNVERIFIED", self.run_write)
                self.assertEqual(self.document.events, ["open", "commit"])
                self.assertEqual(self.part.PartName, "After")

    def test_commit_exception_before_or_after_native_closure_is_not_reclosed(self):
        for close_first in (False, True):
            with self.subTest(close_first=close_first):
                self.setUp()
                def commit():
                    self.document.events.append("commit")
                    if close_first: self.document.active_transaction = None
                    raise RuntimeError("synthetic commit exception")
                self.document.commitTransaction = commit
                self.assert_code("PART_COMMIT_UNVERIFIED", self.run_write)
                self.assertEqual(self.document.events, ["open", "commit"])

    def test_commit_readback_failure_is_not_reclosed(self):
        original = self.document.commitTransaction
        def commit():
            original()
            self.app.getActiveTransaction = lambda: (_ for _ in ()).throw(RuntimeError("unavailable"))
        self.document.commitTransaction = commit
        self.assert_code("PART_COMMIT_UNVERIFIED", self.run_write)
        self.assertEqual(self.document.events, ["open", "commit"])

    def test_commit_callback_changing_property_after_close_is_not_reported_applied(self):
        original = self.document.commitTransaction
        def commit():
            original()
            self.part.PartName = "Changed by commit callback"
        self.document.commitTransaction = commit
        self.assert_code("PART_COMMIT_UNVERIFIED", self.apply_properties)
        self.assertEqual(self.document.events, ["open", "commit"])
        self.assertIsNone(self.document.active_transaction)
        self.assertEqual(self.part.PartName, "Changed by commit callback")

    def test_commit_callback_changing_label_after_close_is_not_reported_applied(self):
        self.part.addProperty("App::PropertyString", "PartNumber", "CustomDefine", "")
        self.part.addProperty("App::PropertyString", "PartVersion", "CustomDefine", "")
        self.part.PartNumber, self.part.PartVersion = "7305", "004"
        snapshots = [model._snapshot(self.part)]
        rows = model.build_rename_preview(self.document, [self.part])
        original = self.document.commitTransaction
        def commit():
            original()
            self.part.Label = "Changed by commit callback"
        self.document.commitTransaction = commit
        self.assert_code("PART_COMMIT_UNVERIFIED", lambda: controller.apply_rename(
            self.app, self.gui, self.document, [self.part], snapshots, rows))
        self.assertEqual(self.document.events, ["open", "commit"])
        self.assertIsNone(self.document.active_transaction)
        self.assertEqual(self.part.Label, "Changed by commit callback")

    def test_initialize_preserves_existing_nonstring_unchanged_property(self):
        self.part.TypeCode = 7
        self.part.types["TypeCode"] = "App::PropertyInteger"
        values = {self.part.Name: {"TypeCode": "do not overwrite", "ModelSeries": "ignored"}}
        count = controller.apply_properties(self.app, self.gui, self.document, [self.part],
            [model._snapshot(self.part)], ["TypeCode", "ModelSeries"], values, initialize_only=True)
        self.assertEqual(count, 1)
        self.assertEqual(self.part.TypeCode, 7)
        self.assertEqual(self.part.types["TypeCode"], "App::PropertyInteger")
        self.assertEqual(self.part.ModelSeries, "")
        self.assertEqual(self.document.events, ["open", "commit"])

    def test_abort_without_closure_or_with_foreign_id_is_not_retried(self):
        for mode, code in (("same", "PART_ROLLBACK_UNVERIFIED"),
                           ("foreign", "PART_TRANSACTION_CHANGED"),
                           ("malformed", "PART_ROLLBACK_UNVERIFIED")):
            with self.subTest(mode=mode):
                self.setUp()
                def abort():
                    self.document.events.append("abort")
                    if mode == "foreign": self.document.active_transaction = ("Other", 100)
                    if mode == "malformed": self.document.active_transaction = False
                self.document.abortTransaction = abort
                self.assert_code(code, lambda: self.run_write(self.fail_write))
                self.assertEqual(self.document.events, ["open", "abort"])

    def test_abort_exception_is_not_retried(self):
        def abort():
            self.document.events.append("abort")
            raise RuntimeError("synthetic abort exception")
        self.document.abortTransaction = abort
        self.assert_code("PART_ROLLBACK_UNVERIFIED", lambda: self.run_write(self.fail_write))
        self.assertEqual(self.document.events, ["open", "abort"])

    def test_abort_readback_failure_is_not_retried(self):
        original = self.document.abortTransaction
        def abort():
            original()
            self.app.getActiveTransaction = lambda: (_ for _ in ()).throw(RuntimeError("unavailable"))
        self.document.abortTransaction = abort
        self.assert_code("PART_ROLLBACK_UNVERIFIED", lambda: self.run_write(self.fail_write))
        self.assertEqual(self.document.events, ["open", "abort"])

    def test_abort_closed_but_not_restored_is_explicitly_unverified(self):
        self.install_setter(lambda: self.part.__dict__.__setitem__("PartName", "Native rejected value"))
        def abort_without_restore():
            self.document.events.append("abort")
            self.document.active_transaction = None
        self.document.abortTransaction = abort_without_restore
        self.assert_code("PART_ROLLBACK_UNVERIFIED", self.apply_properties)
        self.assertEqual(self.document.events, ["open", "abort"])
        self.assertEqual(self.part.PartName, "Native rejected value")
        self.assertIsNone(self.document.active_transaction)

    def test_worker_cannot_open_or_write(self):
        import threading
        errors = []
        def worker():
            try:
                self.run_write()
            except model.PftToolError as error:
                errors.append(error)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertIn("主界面线程", str(errors[0]))
        self.assertEqual(self.document.events, [])
        self.assertEqual(self.part.PartName, "Before")

    def test_silently_rejected_property_setter_is_not_reported_applied(self):
        self.install_setter(lambda: self.part.__dict__.__setitem__("PartName", "Native rejected value"))
        self.assert_code("PART_PROPERTY_VERIFY_FAILED", self.apply_properties)
        self.assertEqual(self.document.events, ["open", "abort"])
        self.assertEqual((self.part.PartName, self.part.TypeCode), ("Before", "H"))

    def test_wrong_new_property_type_or_missing_property_is_not_reported_applied(self):
        for mode in ("type", "missing"):
            with self.subTest(mode=mode):
                self.setUp()
                original = self.part.addProperty
                def add(kind, field, group, description):
                    if mode == "type": original("App::PropertyInteger", field, group, description)
                self.part.addProperty = add
                self.assert_code("PART_PROPERTY_VERIFY_FAILED", lambda: self.apply_properties({"ModelSeries": "Example"}))
                self.assertEqual(self.document.events, ["open", "abort"])
                self.assertNotIn("ModelSeries", self.part.PropertiesList)

    def test_other_checked_unchanged_property_changed_by_callback_is_rejected(self):
        self.install_setter(lambda: self.part.__dict__.__setitem__("TypeCode", "Unexpected"))
        self.assert_code("PART_PROPERTY_VERIFY_FAILED", lambda: self.apply_properties({"PartName": "After", "TypeCode": "H"}))
        self.assertEqual(self.document.events, ["open", "abort"])
        self.assertEqual((self.part.PartName, self.part.TypeCode), ("Before", "H"))

    def test_same_name_object_replacement_during_write_is_rejected(self):
        replacement = Object(self.part.Name, self.part.Label, PartName="After", TypeCode="R")
        replacement.Document = self.document
        self.install_setter(lambda: self.document.Objects.__setitem__(0, replacement))
        self.assert_code("PART_PROPERTY_VERIFY_FAILED", self.apply_properties)
        self.assertEqual(self.document.events, ["open", "abort"])
        self.assertIs(self.document.getObject("Part"), self.part)


if __name__ == "__main__":
    unittest.main()
