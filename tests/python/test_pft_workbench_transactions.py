# SPDX-License-Identifier: Apache-2.0
"""Native-style transaction ownership failures must never close another edit."""
from pathlib import Path
import sys
import types
import unittest

from test_pft_workbench_tools import Document, Object, bind_transaction_api

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
        bind_transaction_api(self.app, self.document)
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
                self.assert_code("PART_TRANSACTION_CHANGED", self.run_write)
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
        self.assert_code("PART_TRANSACTION_CHANGED", self.run_write)
        self.assertEqual(self.document.events, ["open"])
        self.assertEqual(self.part.PartName, "Before")

    def test_same_title_open_callback_cannot_adopt_replacement_id(self):
        for same_title in (False, True):
            with self.subTest(same_title=same_title):
                self.setUp()
                allocate = self.app.setActiveTransaction
                closes = []
                self.app.closeActiveTransaction = lambda *args: closes.append(args)
                def replace(title):
                    allocated_id = allocate(title)
                    self.document.commitTransaction()  # External opening callback.
                    self.document.openTransaction(title if same_title else "Other operation")
                    return allocated_id
                self.app.setActiveTransaction = replace
                self.assert_code("PART_TRANSACTION_CHANGED", self.run_write)
                self.assertEqual(self.document.events, ["open", "commit", "open"])
                self.assertEqual(self.document.active_transaction[1], 2)
                self.assertEqual(self.part.PartName, "Before")
                self.assertEqual(closes, [])

    def test_invalid_allocation_result_never_adopts_current_transaction(self):
        for result in (None, False, True, 0, -1, 1., "1", ("Public fixture edit", 1), [1]):
            with self.subTest(result=result):
                self.setUp()
                allocate = self.app.setActiveTransaction
                closes = []
                def invalid(title):
                    allocate(title)
                    return result
                self.app.setActiveTransaction = invalid
                self.app.closeActiveTransaction = lambda *args: closes.append(args)
                self.assert_code("PART_TRANSACTION_OPEN_UNVERIFIED", self.run_write)
                self.assertEqual(self.document.events, ["open"])
                self.assertEqual(self.part.PartName, "Before")
                self.assertIsNotNone(self.document.active_transaction)
                self.assertEqual(closes, [])

    def test_application_transaction_api_is_required_without_document_fallback(self):
        for name in ("setActiveTransaction", "closeActiveTransaction"):
            with self.subTest(name=name):
                self.setUp()
                delattr(self.app, name)
                self.assert_code("PART_TRANSACTION_STATE_UNAVAILABLE", self.run_write)
                self.assertEqual(self.document.events, [])
                self.assertEqual(self.part.PartName, "Before")

    def test_direct_application_open_and_close_do_not_call_document_methods(self):
        calls = []
        def forbidden(*args):
            raise AssertionError("document transaction fallback must not run")
        self.document.openTransaction = self.document.commitTransaction = self.document.abortTransaction = forbidden
        def allocate(title):
            self.document.active_transaction = (title, 37)
            self.document.HasPendingTransaction = True
            return 37
        def close(abort, transaction_id):
            calls.append((abort, transaction_id))
            self.document.active_transaction = None
            self.document.HasPendingTransaction = False
        self.app.setActiveTransaction, self.app.closeActiveTransaction = allocate, close
        self.run_write()
        self.assertEqual(calls, [(False, 37)])
        self.assertEqual(self.part.PartName, "After")
        self.assertEqual(self.document.events, [])

    def test_pending_getter_replacement_is_checked_before_either_bound_close(self):
        for abort in (False, True):
            with self.subTest(abort=abort):
                self.setUp()
                owner = controller._PftOwnedTransaction(self.app, self.document)
                owner.open("Public fixture edit")
                document = self.document
                class PendingCallbackDocument(Document):
                    def __getattribute__(self, name):
                        result = super().__getattribute__(name)
                        if name == "HasPendingTransaction":
                            self.active_transaction = ("Public fixture edit", 99)
                        return result
                document.__class__ = PendingCallbackDocument
                closes = []
                self.app.closeActiveTransaction = lambda *args: closes.append(args)
                self.assert_code("PART_TRANSACTION_CHANGED", owner.abort_once if abort else owner.commit)
                owner.abort_once()
                self.assertEqual(closes, [])
                self.assertEqual(document.active_transaction, ("Public fixture edit", 99))
                self.assertEqual(document.events, ["open"])

    def test_bound_close_exceptions_before_or_after_closure_are_one_shot(self):
        for abort in (False, True):
            for close_first in (False, True):
                with self.subTest(abort=abort, close_first=close_first):
                    self.setUp()
                    owner = controller._PftOwnedTransaction(self.app, self.document)
                    owner.open("Public fixture edit")
                    closes = []
                    def uncertain(actual_abort, transaction_id):
                        closes.append((actual_abort, transaction_id))
                        if close_first:
                            self.document.active_transaction = None
                            self.document.HasPendingTransaction = False
                        raise RuntimeError("synthetic close details")
                    self.app.closeActiveTransaction = uncertain
                    code = "PART_ROLLBACK_UNVERIFIED" if abort else "PART_COMMIT_UNVERIFIED"
                    error = self.assert_code(code, owner.abort_once if abort else owner.commit)
                    owner.abort_once()
                    self.assert_code("PART_COMMIT_UNVERIFIED", owner.commit)
                    self.assertEqual(closes, [(abort, 1)])
                    self.assertEqual(self.document.events, ["open"])
                    self.assertNotIn("synthetic close details", str(error))

    def test_target_verifier_transaction_replacement_is_not_reported_closed(self):
        for abort in (False, True):
            with self.subTest(abort=abort):
                self.setUp()
                def verify():
                    self.document.active_transaction = ("Other operation", 99)
                owner = controller._PftOwnedTransaction(self.app, self.document,
                    verify_rollback=verify if abort else None,
                    verify_commit=None if abort else verify)
                owner.open("Public fixture edit")
                code = "PART_ROLLBACK_UNVERIFIED" if abort else "PART_COMMIT_UNVERIFIED"
                self.assert_code(code, owner.abort_once if abort else owner.commit)
                owner.abort_once()
                self.assertEqual(self.document.events, ["open", "abort" if abort else "commit"])
                self.assertEqual(self.document.active_transaction, ("Other operation", 99))

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


class LazyTransactionOwnershipTests(unittest.TestCase):
    """Application IDs exist before a native document Undo entry is created."""
    def setUp(self):
        self.part = Object("Part", "Example", PartName="Before")
        self.document = Document([self.part])
        self.document.UndoCount = 0
        self.app = types.SimpleNamespace(getActiveTransaction=lambda: self.document.active_transaction)
        bind_transaction_api(self.app, self.document)
        self.app_closes = []
        original = self.document.openTransaction
        def open_lazy(title):
            original(title)
            self.document.HasPendingTransaction = False
        self.document.openTransaction = open_lazy
        native_close = self.app.closeActiveTransaction
        def close_bound(abort, transaction_id):
            self.assertEqual(transaction_id, self.document.active_transaction[1])
            self.assertGreater(transaction_id, 0)
            self.app_closes.append((abort, transaction_id))
            if self.document.HasPendingTransaction:
                return native_close(abort, transaction_id)
            self.document.events.append("app_abort" if abort else "app_commit")
            self.document.active_transaction = None
            self.document.HasPendingTransaction = False
        self.app.closeActiveTransaction = close_bound

    def owner(self):
        owner = controller._PftOwnedTransaction(self.app, self.document)
        owner.open("Synthetic lazy edit")
        self.assertFalse(self.document.HasPendingTransaction)
        return owner

    def assert_code(self, code, callback):
        with self.assertRaises(controller.PftWriteError) as caught: callback()
        self.assertEqual(caught.exception.code, code)

    def test_failure_before_any_write_closes_only_empty_application_id_once(self):
        def fail(check):
            self.assertIs(check(), False)
            raise ValueError("synthetic prewrite failure")
        with self.assertRaisesRegex(ValueError, "synthetic prewrite failure"):
            controller._run_transaction(self.app, self.document, "Synthetic lazy edit", fail)
        self.assertEqual(self.document.events, ["open", "app_abort"])
        self.assertEqual(len(self.app_closes), 1)
        self.assertIsNone(self.document.active_transaction)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual((self.part.PartName, self.document.UndoCount), ("Before", 0))

    def test_first_write_materializes_then_commits_once(self):
        owner = self.owner()
        self.assertIs(owner.check(before_write=True), False)
        self.part.PartName = "After"
        self.document.HasPendingTransaction = True
        self.assertIs(owner.check(require_pending=True), True)
        owner.commit()
        owner.abort_once()  # A closed commit never authorizes another close.
        self.assert_code("PART_COMMIT_UNVERIFIED", owner.commit)
        self.assertEqual(self.document.events, ["open", "commit"])
        self.assertEqual(self.app_closes, [(False, 1)])
        self.assertIsNone(self.document.active_transaction)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_materialized_write_aborts_by_application_id_once(self):
        owner = self.owner()
        owner.check(before_write=True)
        self.part.PartName = "Partial"
        self.document.HasPendingTransaction = True
        owner.check(require_pending=True)
        owner.abort_once(); owner.abort_once()
        self.assertEqual(self.document.events, ["open", "abort"])
        self.assertEqual(self.app_closes, [(True, 1)])
        self.assertEqual(self.part.PartName, "Before")

    def test_materialized_pending_loss_cannot_be_reclassified_as_empty(self):
        owner = self.owner()
        self.document.HasPendingTransaction = True
        owner.check()
        self.document.HasPendingTransaction = False
        self.assert_code("PART_TRANSACTION_CHANGED", owner.abort_once)
        owner.abort_once()
        self.assertEqual(self.document.events, ["open"])
        self.assertEqual(self.app_closes, [])
        self.assertIsNotNone(self.document.active_transaction)

    def test_postwrite_requirement_marks_missing_materialization_as_uncertain(self):
        owner = self.owner()
        self.assert_code("PART_TRANSACTION_CHANGED", lambda: owner.check(require_pending=True))
        self.assert_code("PART_TRANSACTION_CHANGED", owner.abort_once)
        self.assertEqual(self.document.events, ["open"])
        self.assertEqual(self.app_closes, [])

    def test_setter_can_materialize_and_lose_pending_before_return_or_exception(self):
        for raises in (False, True):
            with self.subTest(raises=raises):
                self.setUp()
                def write(check):
                    check(before_write=True)
                    self.document.HasPendingTransaction = True
                    self.part.PartName = "Partial"
                    self.document.HasPendingTransaction = False
                    if raises: raise ValueError("synthetic callback failure")
                    check(require_pending=True)
                self.assert_code("PART_TRANSACTION_CHANGED", lambda: controller._run_transaction(
                    self.app, self.document, "Synthetic lazy edit", write))
                self.assertEqual(self.document.events, ["open"])
                self.assertEqual(self.app_closes, [])
                self.assertIsNotNone(self.document.active_transaction)

    def controller_write(self, kind, lose_pending=False):
        document = self.document
        class LazyNativeObject(Object):
            def __setattr__(self, name, value):
                super().__setattr__(name, value)
                if name in ('Label', 'PartName', 'ModelSeries') and document.active_transaction is not None:
                    document.HasPendingTransaction = not lose_pending
        self.part.__class__ = LazyNativeObject
        self.app.ActiveDocument = document
        self.app.listDocuments = lambda: {document.Name: document}
        gui = types.SimpleNamespace(getDocument=lambda name: types.SimpleNamespace(getInEdit=lambda: None))
        if kind == 'label':
            rows = [{'object': self.part, 'new_label': 'After'}]
            return controller._run_transaction(self.app, document, 'Synthetic lazy labels',
                lambda check: controller._write_labels_exact(rows, check))
        field = 'ModelSeries' if kind == 'new_property' else 'PartName'
        return controller.apply_properties(self.app, gui, document, [self.part], [model._snapshot(self.part)],
            [field], {self.part.Name: {field: 'After'}})

    def test_existing_label_property_and_property_creation_materialize_before_commit(self):
        for kind in ('label', 'property', 'new_property'):
            with self.subTest(kind=kind):
                self.setUp()
                self.controller_write(kind)
                self.assertEqual(self.document.events, ['open', 'commit'])
                self.assertEqual(self.app_closes, [(False, 1)])
                self.assertFalse(self.document.HasPendingTransaction)

    def test_existing_controller_writes_cannot_hide_missing_document_undo_state(self):
        for kind in ('label', 'property', 'new_property'):
            with self.subTest(kind=kind):
                self.setUp()
                self.assert_code('PART_TRANSACTION_CHANGED', lambda: self.controller_write(kind, lose_pending=True))
                self.assertEqual(self.document.events, ['open'])
                self.assertEqual(self.app_closes, [])
                self.assertIsNotNone(self.document.active_transaction)

    def test_unknown_pending_state_never_authorizes_either_close(self):
        for pending in (None, 0, 1, "false", []):
            with self.subTest(pending=pending):
                self.setUp(); owner = self.owner()
                self.document.HasPendingTransaction = pending
                self.assert_code("PART_TRANSACTION_STATE_UNAVAILABLE", owner.abort_once)
                owner.abort_once()
                self.assertEqual(self.document.events, ["open"])
                self.assertEqual(self.app_closes, [])

    def test_foreign_id_is_not_closed_even_when_document_is_still_empty(self):
        owner = self.owner()
        self.document.active_transaction = ("Other", 999)
        self.assert_code("PART_TRANSACTION_CHANGED", owner.abort_once)
        owner.abort_once()
        self.assertEqual(self.document.events, ["open"])
        self.assertEqual(self.document.active_transaction, ("Other", 999))
        self.assertEqual(self.app_closes, [])

    def test_missing_native_empty_closer_is_unverified_without_document_fallback(self):
        owner = self.owner()
        del self.app.closeActiveTransaction
        self.assert_code("PART_ROLLBACK_UNVERIFIED", owner.abort_once)
        owner.abort_once()
        self.assertEqual(self.document.events, ["open"])

    def test_empty_close_ambiguity_is_never_retried_or_switched_to_document_abort(self):
        for mode in ("raises_before", "raises_after", "same", "foreign", "read_error", "doc_pending", "doc_unknown"):
            with self.subTest(mode=mode):
                self.setUp(); owner = self.owner()
                def uncertain(abort, transaction_id):
                    self.app_closes.append((abort, transaction_id))
                    self.document.events.append("app_abort")
                    if mode == "raises_before": raise RuntimeError("synthetic close failure")
                    if mode == "same": return
                    self.document.active_transaction = ("Other", 999) if mode == "foreign" else None
                    if mode == "doc_pending": self.document.HasPendingTransaction = True
                    if mode == "doc_unknown": self.document.HasPendingTransaction = 0
                    if mode == "raises_after": raise RuntimeError("synthetic post-close failure")
                    if mode == "read_error":
                        self.app.getActiveTransaction = lambda: (_ for _ in ()).throw(RuntimeError("unavailable"))
                self.app.closeActiveTransaction = uncertain
                self.assert_code("PART_TRANSACTION_CHANGED" if mode == "foreign" else "PART_ROLLBACK_UNVERIFIED", owner.abort_once)
                owner.abort_once()
                self.assertEqual(self.document.events, ["open", "app_abort"])
                self.assertEqual(len(self.app_closes), 1)

    def test_uncertain_commit_does_not_authorize_a_later_abort(self):
        owner = self.owner()
        self.document.HasPendingTransaction = True
        owner.check(require_pending=True)
        def raises():
            self.document.events.append("commit")
            raise RuntimeError("synthetic commit failure")
        self.document.commitTransaction = raises
        self.assert_code("PART_COMMIT_UNVERIFIED", owner.commit)
        owner.abort_once()
        self.assertEqual(self.document.events, ["open", "commit"])
        self.assertEqual(self.app_closes, [(False, 1)])


if __name__ == "__main__":
    unittest.main()
