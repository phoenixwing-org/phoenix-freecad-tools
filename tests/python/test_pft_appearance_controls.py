# SPDX-License-Identifier: Apache-2.0
"""Appearance preview transactions and compact UI through injected native/Qt fakes."""
import copy
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

import test_pft_visual_tools as native
from test_pft_document_tools_views import PftSignal, PftWidgets

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
_saved_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_appearance_controls as controls
finally:
    sys.path[:] = _saved_path


class Document(native.Document):
    def add(self, name, materials=None, source=None):
        obj = super().add(name, materials, source)
        # An explicit fake native inheritance query, independent of string
        # prefixes: a Sketch owns materials without being a PD feature.
        obj.isDerivedFrom = lambda base: (obj.TypeId == base or base == "PartDesign::Feature"
                                         and obj.TypeId in ("PartDesign::Pad", "PartDesign::Pocket"))
        return obj

    @property
    def Objects(self):
        return list(self.objects.values())

    def openTransaction(self, title):
        super().openTransaction(title)
        self.app.next_id += 1
        self.app.active_transaction = (title, self.app.next_id)

    def commitTransaction(self):
        super().commitTransaction()
        self.app.active_transaction = None

    def abortTransaction(self):
        super().abortTransaction()
        self.app.active_transaction = None


class AppearanceSessionTests(unittest.TestCase):
    def setUp(self):
        self.document = Document()
        self.app = types.SimpleNamespace(Material=native.Material, ActiveDocument=self.document,
                                         transaction=False, active_transaction=None, next_id=10)
        self.document.app = self.app
        self.app.getDocument = lambda name: self.document if name == self.document.Name else None
        self.app.getActiveTransaction = lambda: self.app.active_transaction
        self.gui_document = types.SimpleNamespace(getInEdit=lambda: None)
        self.gui = types.SimpleNamespace(getDocument=lambda name: self.gui_document)
        self.app_observers, self.gui_observers = [], []
        self.app.addDocumentObserver, self.app.removeDocumentObserver = self.app_observers.append, self.app_observers.remove
        self.gui.addDocumentObserver, self.gui.removeDocumentObserver = self.gui_observers.append, self.gui_observers.remove
        self.object = self.document.add("Part")
        self.before = native.plain_snapshot(self.document)

    def session(self, names=("Part",)):
        return controls.PftAppearancePreviewSession(self.app, self.gui, self.document.Name, names)

    def assert_error(self, code, call):
        with self.assertRaises(controls.visual.PftVisualToolsError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_initialization_and_unchanged_percent_do_not_open_or_write(self):
        plans = dict(controls.visual._plans)
        session = self.session()
        self.assertEqual(session.transparency(), 0)
        self.assertEqual(session.preview_transparency(0)["changed_count"], 0)
        session.accept()
        self.assertEqual((self.document.opened, self.document.commits, self.document.aborts), (0, 0, 0))
        self.assertEqual(self.object.ViewObject.assignments, 0)
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.assertEqual(controls.visual._plans, plans)

    def test_repeated_transparency_and_random_previews_commit_one_undo(self):
        session = self.session()
        session.preview_transparency(25)
        self.assertEqual(self.object.ViewObject.Transparency, 25)
        session.preview_random_color()
        session.preview_transparency(80)
        final = native.plain_snapshot(self.document)
        self.assertEqual((self.document.opened, self.document.commits), (1, 0))
        result = session.accept()
        self.assertEqual((result["changed_count"], result["saved"], result["recomputed"]), (1, False, False))
        self.assertEqual((self.document.opened, self.document.commits, self.document.aborts), (1, 1, 0))
        self.assertEqual(len(self.document.undo_stack), 1)
        self.document.undo()
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.document.redo()
        self.assertEqual(native.plain_snapshot(self.document), final)

    def test_cancel_reverts_random_and_transparency_without_an_undo_entry(self):
        session = self.session()
        session.preview_random_color()
        session.preview_transparency(61)
        session.cancel()
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.assertEqual((self.document.opened, self.document.commits, self.document.aborts), (1, 0, 1))
        self.assertEqual(self.document.undo_stack, [])
        self.assert_error("VISUAL_SESSION_ENDED", lambda: session.preview_transparency(40))

    def test_return_to_initial_values_aborts_empty_result_instead_of_adding_undo(self):
        session = self.session()
        session.preview_transparency(60)
        session.preview_transparency(0)
        self.assertEqual(session.accept()["changed_count"], 0)
        self.assertEqual((self.document.commits, self.document.aborts), (0, 1))
        self.assertEqual(self.document.undo_stack, [])

    def test_link_previews_only_write_instance_and_cancel_restores_override(self):
        source = self.object
        link = self.document.add("Instance", source=source)
        before = native.plain_snapshot(self.document)
        session = self.session(("Instance",))
        self.assertEqual(session.transparency(), 0)
        session.preview_transparency(72)
        session.preview_random_color()
        self.assertTrue(link.ViewObject.OverrideMaterial)
        self.assertEqual(source.ViewObject.assignments, 0)
        self.assertEqual(native.plain_snapshot(self.document)["Part"], before["Part"])
        session.cancel()
        self.assertEqual(native.plain_snapshot(self.document), before)

    def test_mixed_materials_are_read_only_until_explicit_preview(self):
        self.object.ViewObject._appearance.append(native.Material(Transparency=0.5))
        before = native.plain_snapshot(self.document)
        session = self.session()
        self.assertIsNone(session.transparency())
        session.cancel()
        self.assertEqual(native.plain_snapshot(self.document), before)
        self.assertEqual(self.document.opened, 0)

    def test_existing_transaction_is_never_opened_committed_or_aborted(self):
        self.app.active_transaction = ("Existing edit", 999)
        self.assert_error("DOCUMENT_BUSY", self.session)
        self.assertEqual((self.document.opened, self.document.commits, self.document.aborts), (0, 0, 0))

    def test_no_target_or_missing_target_never_expands_scope(self):
        self.assert_error("VISUAL_TARGETS_REQUIRED", lambda: self.session(()))
        self.assert_error("VISUAL_TARGET_CHANGED", lambda: self.session(("Missing",)))
        self.assertEqual((self.document.opened, self.object.ViewObject.assignments), (0, 0))

    def test_document_switch_before_preview_never_writes_old_scope(self):
        session = self.session()
        self.app.ActiveDocument = object()
        self.assert_error("DOCUMENT_NOT_ACTIVE", lambda: session.preview_transparency(50))
        self.assertEqual((self.document.opened, self.object.ViewObject.assignments), (0, 0))

    def test_document_switch_can_cancel_only_the_still_owned_transaction(self):
        session = self.session()
        session.preview_transparency(50)
        self.app.ActiveDocument = object()
        session.cancel()
        self.assertEqual(self.document.aborts, 1)
        self.assertEqual(native.plain_snapshot(self.document), self.before)

    def test_replaced_transaction_even_with_same_title_is_not_committed_or_aborted(self):
        session = self.session()
        session.preview_transparency(50)
        owned = self.app.active_transaction
        self.app.active_transaction = (owned[0], owned[1] + 1)
        assignments = self.object.ViewObject.assignments
        for operation in (lambda: session.preview_transparency(99), session.accept, session.cancel):
            self.assert_error("VISUAL_TRANSACTION_CHANGED", operation)
        self.assertEqual(self.object.ViewObject.assignments, assignments)
        self.assertEqual((self.document.commits, self.document.aborts), (0, 0))

    def test_title_or_boolean_does_not_prove_ownership(self):
        session = self.session()
        session.preview_transparency(50)
        for active in (True, "Phoenix：预览所选对象外观", ("title", True), ("title", 0)):
            self.app.active_transaction = active
            self.assert_error("VISUAL_TRANSACTION_CHANGED", session.cancel)
        self.assertEqual(self.document.aborts, 0)

    def test_foreign_appearance_change_prevents_overwrite_and_cancel(self):
        session = self.session()
        session.preview_transparency(50)
        self.object.ViewObject._appearance[0].Shininess = 5.0
        for operation in (lambda: session.preview_transparency(60), session.accept, session.cancel):
            self.assert_error("VISUAL_SNAPSHOT_CHANGED", operation)
        self.assertEqual((self.document.commits, self.document.aborts), (0, 0))

    def test_disappeared_target_prevents_followup_write_or_unverified_abort(self):
        session = self.session()
        session.preview_transparency(50)
        del self.document.objects["Part"]
        self.assert_error("VISUAL_SNAPSHOT_CHANGED", session.cancel)
        self.assertEqual(self.document.aborts, 0)

    def test_link_source_change_prevents_more_instance_writes_or_unverified_abort(self):
        link = self.document.add("Instance", source=self.object)
        session = self.session(("Instance",))
        session.preview_transparency(50)
        self.object.ViewObject._appearance[0].Shininess = 5.0
        self.assert_error("VISUAL_SNAPSHOT_CHANGED", lambda: session.preview_transparency(60))
        self.assert_error("VISUAL_SNAPSHOT_CHANGED", session.cancel)
        self.assertEqual(self.object.ViewObject.assignments, 0)
        self.assertEqual((self.document.commits, self.document.aborts), (0, 0))

    def test_write_failure_aborts_owned_transaction_and_restores_every_material(self):
        session = self.session()
        self.object.ViewObject.fail_next = True
        self.assert_error("VISUAL_PREVIEW_FAILED", lambda: session.preview_transparency(50))
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.assertEqual((self.document.commits, self.document.aborts), (0, 1))

    def test_transaction_replaced_during_write_is_not_aborted(self):
        session = self.session()
        self.object.ViewObject.pollute = lambda: setattr(self.app, "active_transaction", ("External", 999))
        self.assert_error("VISUAL_TRANSACTION_CHANGED", lambda: session.preview_transparency(50))
        self.assertEqual((self.document.commits, self.document.aborts), (0, 0))
        self.assertEqual(self.app.active_transaction, ("External", 999))

    def test_missing_observer_capability_fails_before_opening_or_writing(self):
        session = self.session()
        self.gui.addDocumentObserver = None
        self.assert_error("DOCUMENT_STATE_UNKNOWN", lambda: session.preview_transparency(50))
        self.assertEqual((self.document.opened, self.object.ViewObject.assignments), (0, 0))
        self.assertEqual(self.app_observers, [])

    def test_cross_document_edit_in_shared_global_transaction_is_not_aborted(self):
        session = self.session()
        session.preview_transparency(50)
        other_document = types.SimpleNamespace(Name="Other")
        self.app_observers[0].slotChangedObject(types.SimpleNamespace(Document=other_document), "Label")
        self.app.ActiveDocument = other_document
        self.assert_error("VISUAL_SESSION_CONTAMINATED", session.cancel)
        self.assertEqual((self.document.commits, self.document.aborts), (0, 0))

    def test_external_non_target_edits_in_same_transaction_are_never_aborted(self):
        observers = []
        self.app.addDocumentObserver = observers.append
        self.app.removeDocumentObserver = observers.remove
        session = self.session()
        session.preview_transparency(50)
        other = self.document.add("Other")
        observers[0].slotChangedObject(other, "Label")
        for operation in (lambda: session.preview_transparency(60), session.accept, session.cancel):
            self.assert_error("VISUAL_SESSION_CONTAMINATED", operation)
        self.assertEqual((self.document.commits, self.document.aborts), (0, 0))
        session.invalidate()
        self.assertEqual(observers, [])

    def test_our_synchronous_native_events_do_not_contaminate_preview_or_abort(self):
        observers = []
        self.gui.addDocumentObserver = observers.append
        self.gui.removeDocumentObserver = observers.remove
        self.object.ViewObject.pollute = lambda: [observer.slotChangedObject(self.object.ViewObject, "ShapeAppearance")
                                                  for observer in list(observers)]
        session = self.session()
        session.preview_transparency(50)
        session.preview_random_color()
        session.cancel()
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.assertEqual(observers, [])

    def test_synchronous_callback_cannot_hide_cross_document_edit_during_our_setter(self):
        session = self.session()
        other = types.SimpleNamespace(Document=types.SimpleNamespace(Name="OtherDocument"), Label="Original")
        def pollute():
            other.Label = "Changed by synchronous callback"
            for observer in list(self.app_observers):
                observer.slotChangedObject(other, "Label")
        self.object.ViewObject.pollute = pollute
        self.assert_error("VISUAL_SESSION_CONTAMINATED", lambda: session.preview_transparency(50))
        self.assertTrue(session._foreign_change)
        self.assertEqual(other.Label, "Changed by synchronous callback")
        self.assertEqual((self.document.commits, self.document.aborts), (0, 0))
        self.assertIsNotNone(self.app.active_transaction)
        self.assertEqual(session.state, "invalid")
        self.assertEqual((self.app_observers, self.gui_observers), ([], []))

    def test_synchronous_model_and_unrelated_view_changes_are_not_our_appearance_events(self):
        for event_kind in ("model_label", "target_display_mode", "other_target_material", "document_label"):
            with self.subTest(event_kind=event_kind):
                self.setUp()
                other = self.document.add("Other")
                session = self.session(("Part", "Other"))
                def pollute():
                    if event_kind == "model_label":
                        for observer in list(self.app_observers): observer.slotChangedObject(self.object, "Label")
                    elif event_kind == "target_display_mode":
                        for observer in list(self.gui_observers): observer.slotChangedObject(self.object.ViewObject, "DisplayMode")
                    elif event_kind == "other_target_material":
                        for observer in list(self.gui_observers): observer.slotChangedObject(other.ViewObject, "ShapeAppearance")
                    else:
                        for observer in list(self.app_observers): observer.slotChangedDocument(self.document, "Label")
                self.object.ViewObject.pollute = pollute
                self.assert_error("VISUAL_SESSION_CONTAMINATED", lambda: session.preview_transparency(50))
                self.assertTrue(session._foreign_change)
                self.assertEqual((self.document.commits, self.document.aborts), (0, 0))

    def test_expected_coupled_target_appearance_events_remain_owned(self):
        session = self.session()
        def native_events():
            for observer in list(self.gui_observers):
                for field in ("ShapeAppearance", "ShapeColor", "DiffuseColor", "Transparency"):
                    observer.slotBeforeChangeObject(self.object.ViewObject, field)
                    observer.slotChangedObject(self.object.ViewObject, field)
            for observer in list(self.app_observers): observer.slotChangedDocument(self.document, "Modified")
        self.object.ViewObject.pollute = native_events
        session.preview_transparency(50)
        self.assertFalse(session._foreign_change)
        session.accept()
        self.assertEqual((self.document.commits, self.document.aborts), (1, 0))

    def test_native_failure_retains_original_stage_and_numeric_id_after_verified_rollback(self):
        session = self.session()
        self.object.ViewObject.fail_next = True
        error = self.assert_error("VISUAL_PREVIEW_FAILED", lambda: session.preview_transparency(6))
        diagnostic = error.appearance_diagnostics
        self.assertEqual(diagnostic["primary_error"], {"phase": "write_shape_appearance",
            "type": "RuntimeError", "code": "NATIVE_EXCEPTION"})
        self.assertEqual(diagnostic["owned_transaction_id"], 11)
        self.assertIsNone(diagnostic["transaction_id"])
        self.assertIsNone(diagnostic["active_transaction_id"])
        self.assertEqual(diagnostic["rollback_status"], "verified")
        self.assertEqual(session.owned_transaction_id, 11)
        self.assertEqual(controls.get_appearance_diagnostics()[-1]["state"], "cancelled")

    def test_new_preview_failure_does_not_inherit_a_previous_preflight_error(self):
        session = self.session()
        self.app.active_transaction = ("External fixture", 999)
        self.assert_error("DOCUMENT_BUSY", lambda: session.preview_transparency(6))
        self.assertEqual(self.document.aborts, 0)
        self.app.active_transaction = None
        self.object.ViewObject.fail_next = True
        error = self.assert_error("VISUAL_PREVIEW_FAILED", lambda: session.preview_transparency(6))
        self.assertEqual(error.appearance_diagnostics["primary_error"]["type"], "RuntimeError")
        self.assertEqual(error.appearance_diagnostics["primary_error"]["phase"], "write_shape_appearance")

    def test_app_target_object_event_is_recorded_without_guessing_a_new_whitelist(self):
        session = self.session()
        def forwarded_event():
            for observer in list(self.app_observers):
                observer.slotChangedObject(self.object, "ShapeAppearance")
        self.object.ViewObject.pollute = forwarded_event
        error = self.assert_error("VISUAL_SESSION_CONTAMINATED", lambda: session.preview_transparency(6))
        diagnostic = error.appearance_diagnostics
        self.assertEqual(diagnostic["events"][-1], {
            "provider": "app", "callback": "changed_object", "role": "target_object",
            "property": "ShapeAppearance", "phase": "write_shape_appearance", "writing": True,
            "decision": "foreign"})
        self.assertEqual(diagnostic["primary_error"]["code"], "VISUAL_SESSION_CONTAMINATED")
        self.assertEqual(diagnostic["rollback_error"]["code"], "VISUAL_SESSION_CONTAMINATED")
        self.assertEqual(diagnostic["rollback_status"], "unverified")
        self.assertEqual((diagnostic["owned_transaction_id"], diagnostic["active_transaction_id"]), (11, 11))
        self.assertEqual((self.document.commits, self.document.aborts), (0, 0))
        session.invalidate()
        retained = controls.get_appearance_diagnostics()[-1]
        self.assertEqual((retained["state"], retained["owned_transaction_id"]), ("invalid", 11))
        retained["owned_transaction_id"] = 999
        self.assertEqual(controls.get_appearance_diagnostics()[-1]["owned_transaction_id"], 11)

    def test_diagnostics_omit_raw_exception_text_names_and_unknown_property_names(self):
        private_fixture = "/Users/example/private-fixture.FCStd?secret=fake"
        session = self.session()
        self.object.ViewObject.pollute = lambda: (_ for _ in ()).throw(RuntimeError(private_fixture))
        self.assert_error("VISUAL_PREVIEW_FAILED", lambda: session.preview_transparency(6))
        diagnostic = json.dumps(session.diagnostics)
        for text in (private_fixture, self.document.Name, self.object.Name, self.object.Label):
            self.assertNotIn(text, diagnostic)
        self.assertIn('"type": "RuntimeError"', diagnostic)
        self.assertNotIn("traceback", diagnostic)

    def test_event_phase_and_session_diagnostics_have_fixed_memory_bounds(self):
        session = self.session()
        session.preview_transparency(6)
        observer = self.app_observers[0]
        for index in range(controls._DIAGNOSTIC_EVENT_LIMIT + 10):
            observer.slotChangedObject(self.object, "private_custom_property")
            session._checkpoint("previewed")
        diagnostic = session.diagnostics
        self.assertEqual(len(diagnostic["events"]), controls._DIAGNOSTIC_EVENT_LIMIT)
        self.assertEqual(len(diagnostic["phases"]), controls._DIAGNOSTIC_PHASE_LIMIT)
        self.assertTrue(all(event["property"] == "other" for event in diagnostic["events"]))
        self.assertNotIn("private_custom_property", json.dumps(diagnostic))
        session.invalidate()
        for _ in range(controls._DIAGNOSTIC_SESSION_LIMIT + 2):
            self.setUp()
            self.session()
        self.assertEqual(len(controls.get_appearance_diagnostics()), controls._DIAGNOSTIC_SESSION_LIMIT)

    def test_abort_return_and_restored_appearance_do_not_prove_same_id_transaction_closed(self):
        session = self.session()
        session.preview_transparency(6)
        owned_id = session.transaction_id
        # Native-like abort restores all properties but leaves the same global ID.
        self.document.abortTransaction = lambda: native.Document.abortTransaction(self.document)
        error = self.assert_error("VISUAL_ROLLBACK_UNVERIFIED", session.cancel)
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.assertEqual((session.transaction_id, session.owned_transaction_id), (owned_id, owned_id))
        self.assertEqual(self.app.active_transaction[1], owned_id)
        diagnostic = error.appearance_diagnostics
        self.assertEqual(diagnostic["rollback_status"], "unverified")
        self.assertEqual(diagnostic["rollback_error"]["phase"], "rollback_close_verify")
        self.assertEqual(diagnostic["active_transaction_id"], owned_id)
        self.assertEqual((self.document.commits, self.document.aborts), (0, 1))

    def test_abort_closure_check_does_not_clear_or_close_a_new_foreign_id(self):
        session = self.session()
        session.preview_transparency(6)
        owned_id = session.transaction_id
        def abort_then_foreign():
            native.Document.abortTransaction(self.document)
            self.app.active_transaction = ("Foreign fixture", 999)
        self.document.abortTransaction = abort_then_foreign
        error = self.assert_error("VISUAL_TRANSACTION_CHANGED", session.cancel)
        self.assertEqual((session.transaction_id, session.owned_transaction_id), (owned_id, owned_id))
        self.assertEqual(self.app.active_transaction, ("Foreign fixture", 999))
        self.assertEqual((self.document.commits, self.document.aborts), (0, 1))
        diagnostic = error.appearance_diagnostics
        self.assertEqual(diagnostic["active_transaction_id"], 999)
        self.assertEqual(diagnostic["rollback_status"], "unverified")
        self.assertEqual(diagnostic["rollback_error"]["code"], "VISUAL_TRANSACTION_CHANGED")

    def test_commit_return_does_not_prove_same_id_transaction_closed(self):
        session = self.session()
        session.preview_transparency(6)
        owned_id = session.transaction_id
        self.document.commitTransaction = lambda: native.Document.commitTransaction(self.document)
        error = self.assert_error("VISUAL_COMMIT_UNVERIFIED", session.accept)
        self.assertEqual((session.state, session.transaction_id, session.owned_transaction_id), ("invalid", owned_id, owned_id))
        self.assertEqual(self.app.active_transaction[1], owned_id)
        self.assertEqual((self.document.commits, self.document.aborts), (1, 0))
        diagnostic = error.appearance_diagnostics
        self.assertEqual(diagnostic["commit_status"], "unverified")
        self.assertEqual(diagnostic["primary_error"]["phase"], "commit_close_verify")
        self.assertEqual(diagnostic["active_transaction_id"], owned_id)
        self.assertEqual((self.app_observers, self.gui_observers), ([], []))
        self.assert_error("VISUAL_SESSION_ENDED", session.accept)
        self.assert_error("VISUAL_SESSION_ENDED", session.cancel)
        self.assertEqual((self.document.commits, self.document.aborts), (1, 0))

    def test_commit_closure_check_never_commits_aborts_or_clears_a_new_foreign_id(self):
        session = self.session()
        session.preview_transparency(6)
        owned_id = session.transaction_id
        def commit_then_foreign():
            native.Document.commitTransaction(self.document)
            self.app.active_transaction = ("Foreign fixture", 999)
        self.document.commitTransaction = commit_then_foreign
        error = self.assert_error("VISUAL_TRANSACTION_CHANGED", session.accept)
        self.assertEqual((session.state, session.transaction_id, session.owned_transaction_id), ("invalid", owned_id, owned_id))
        self.assertEqual(self.app.active_transaction, ("Foreign fixture", 999))
        self.assertEqual((self.document.commits, self.document.aborts), (1, 0))
        diagnostic = error.appearance_diagnostics
        self.assertEqual(diagnostic["active_transaction_id"], 999)
        self.assertEqual(diagnostic["commit_status"], "unverified")
        self.assertEqual(diagnostic["primary_error"]["code"], "VISUAL_TRANSACTION_CHANGED")
        self.assert_error("VISUAL_SESSION_ENDED", session.cancel)
        self.assertEqual((self.document.commits, self.document.aborts), (1, 0))

    def test_synchronous_foreign_commit_callback_never_declares_accepted_or_retries_closure(self):
        for timing in ("before_close", "after_close"):
            with self.subTest(timing=timing):
                self.setUp()
                other_document = types.SimpleNamespace(Label="Initial")
                session = self.session()
                session.preview_transparency(6)
                owned_id = session.transaction_id
                def pollute():
                    other_document.Label = "Changed"
                    for observer in list(self.app_observers):
                        observer.slotChangedDocument(other_document, "Label")
                def commit_then_callback():
                    if timing == "before_close":
                        pollute()
                    Document.commitTransaction(self.document)
                    if timing == "after_close":
                        pollute()
                self.document.commitTransaction = commit_then_callback
                error = self.assert_error("VISUAL_SESSION_CONTAMINATED", session.accept)
                self.assertIsNone(self.app.active_transaction)
                self.assertEqual((session.state, session.transaction_id, session.owned_transaction_id),
                                 ("invalid", owned_id, owned_id))
                self.assertEqual((self.document.commits, self.document.aborts), (1, 0))
                self.assertEqual(other_document.Label, "Changed")
                diagnostic = error.appearance_diagnostics
                self.assertEqual(diagnostic["commit_status"], "closed_contaminated")
                self.assertEqual(diagnostic["primary_error"]["phase"], "commit_close_verify")
                self.assertIsNone(diagnostic["active_transaction_id"])
                self.assertNotIn("accepted", diagnostic["phases"])
                self.assertEqual(diagnostic["events"][-1]["decision"], "foreign")
                self.assertEqual(diagnostic["events"][-1]["phase"], "commit_transaction")
                self.assertEqual((self.app_observers, self.gui_observers), ([], []))
                self.assert_error("VISUAL_SESSION_ENDED", session.cancel)
                self.assertEqual((self.document.commits, self.document.aborts), (1, 0))

    def test_error_diagnostics_never_trust_unknown_backend_codes(self):
        error = controls.visual.PftVisualToolsError("/private/fake-secret", "private error fixture")
        self.assertEqual(controls._safe_failure(error)["code"], "NATIVE_EXCEPTION")
        error.code = []
        self.assertEqual(controls._safe_failure(error)["code"], "NATIVE_EXCEPTION")

    def body_fixture(self):
        body = self.object
        body.TypeId = "PartDesign::Body"
        tip = self.document.add("Tip")
        tip.TypeId = "PartDesign::Pad"
        body.Tip, body.Group = tip, [tip]
        def coupled_native_setter():
            for observer in list(self.gui_observers):
                observer.slotBeforeChangeObject(tip.ViewObject, "ShapeAppearance")
                observer.slotBeforeChangeObject(tip.ViewObject, "Transparency")
            tip.ViewObject.ShapeAppearance = copy.deepcopy(body.ViewObject.ShapeAppearance)
            for observer in list(self.gui_observers):
                observer.slotChangedObject(tip.ViewObject, "Transparency")
                observer.slotChangedObject(tip.ViewObject, "ShapeAppearance")
                observer.slotChangedObject(body.ViewObject, "Transparency")
                observer.slotChangedObject(body.ViewObject, "ShapeAppearance")
        body.ViewObject.pollute = coupled_native_setter
        self.before = native.plain_snapshot(self.document)
        return body, tip, coupled_native_setter

    def body_sketch_fixture(self):
        body, tip, propagate = self.body_fixture()
        sketch = self.document.add("Profile", materials=[native.Material(Shininess=9.5, Transparency=0.25)])
        sketch.TypeId = "Sketcher::SketchObject"
        body.Group.insert(0, sketch)
        self.before = native.plain_snapshot(self.document)
        return body, tip, sketch, propagate

    def test_body_sketch_and_pad_keep_independent_materials_through_preview_cancel(self):
        body, tip, sketch, _ = self.body_sketch_fixture()
        sketch_before = controls.visual._target_snapshot(self.document, sketch)
        session = self.session()
        self.assertEqual([member["propagates"] for member in session.body_tips[0]["members"]], [False, True])
        session.preview_transparency(6)
        session.preview_transparency(37)
        session.preview_random_color()
        self.assertEqual((body.ViewObject.Transparency, tip.ViewObject.Transparency), (37, 37))
        self.assertEqual(controls.visual._target_snapshot(self.document, sketch), sketch_before)
        self.assertFalse(session._foreign_change)
        self.assertEqual(sketch.ViewObject.assignments, 0)
        session.cancel()
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.assertEqual((self.document.commits, self.document.aborts), (0, 1))
        self.assertIsNone(self.app.active_transaction)

    def test_body_sketch_and_pad_ok_undo_redo_preserve_both_complete_materials(self):
        _, _, sketch, _ = self.body_sketch_fixture()
        sketch_before = controls.visual._target_snapshot(self.document, sketch)
        session = self.session()
        session.preview_transparency(6)
        session.preview_random_color()
        session.preview_transparency(63)
        final = native.plain_snapshot(self.document)
        session.accept()
        self.assertEqual(controls.visual._target_snapshot(self.document, sketch), sketch_before)
        self.assertEqual((self.document.commits, len(self.document.undo_stack)), (1, 1))
        self.document.undo()
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.document.redo()
        self.assertEqual(native.plain_snapshot(self.document), final)

    def test_body_write_does_not_adopt_an_unexpected_silent_sketch_material_change(self):
        body, _, sketch, propagate = self.body_sketch_fixture()
        session = self.session()
        def incorrect_propagation():
            propagate()
            sketch.ViewObject.ShapeAppearance = copy.deepcopy(body.ViewObject.ShapeAppearance)
        body.ViewObject.pollute = incorrect_propagation
        error = self.assert_error("VISUAL_PREVIEW_FAILED", lambda: session.preview_transparency(6))
        self.assertEqual(error.appearance_diagnostics["primary_error"]["code"], "VISUAL_SNAPSHOT_CHANGED")
        self.assertEqual(error.appearance_diagnostics["rollback_status"], "verified")
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.assertEqual((self.document.commits, self.document.aborts), (0, 1))
        self.assertIsNone(self.app.active_transaction)

    def test_sketch_material_and_label_events_during_body_setter_remain_foreign(self):
        for prop in ("ShapeAppearance", "Transparency", "Label"):
            with self.subTest(prop=prop):
                self.setUp()
                body, _, sketch, propagate = self.body_sketch_fixture()
                session = self.session()
                def external_sketch_change():
                    propagate()
                    if prop == "Label":
                        sketch.Label = "Public external fixture"
                    provider = self.app_observers if prop == "Label" else self.gui_observers
                    for observer in list(provider):
                        observer.slotChangedObject(sketch if prop == "Label" else sketch.ViewObject, prop)
                body.ViewObject.pollute = external_sketch_change
                self.assert_error("VISUAL_SESSION_CONTAMINATED", lambda: session.preview_transparency(6))
                self.assertTrue(session._foreign_change)
                self.assertEqual((self.document.commits, self.document.aborts), (0, 0))
                self.assertIsNotNone(self.app.active_transaction)
                self.assertEqual(session.diagnostics["events"][-1]["decision"], "foreign")
                session.invalidate()

    def test_body_tip_must_be_a_native_pd_feature_even_when_material_is_supported(self):
        body, tip, _ = self.body_fixture()
        tip.TypeId = "Sketcher::SketchObject"
        self.assert_error("MATERIAL_UNSUPPORTED", self.session)
        self.assertEqual((self.document.opened, body.ViewObject.assignments), (0, 0))

    def test_body_member_unknown_native_inheritance_query_fails_before_writes(self):
        for query in (None, lambda base: 1, Mock(side_effect=RuntimeError("private fake detail"))):
            with self.subTest(query=type(query).__name__):
                self.setUp()
                body, _, sketch, _ = self.body_sketch_fixture()
                sketch.isDerivedFrom = query
                self.assert_error("MATERIAL_UNSUPPORTED", self.session)
                self.assertEqual((self.document.opened, body.ViewObject.assignments), (0, 0))

    def test_body_member_native_inheritance_is_rechecked_without_type_prefix_fallback(self):
        body, tip, sketch, _ = self.body_sketch_fixture()
        tip.TypeId = "Public::NativePdFeature"
        tip.isDerivedFrom = lambda base: base == "PartDesign::Feature"
        sketch.TypeId = "PartDesign::Lookalike"
        sketch.isDerivedFrom = lambda base: False
        session = self.session()
        session.preview_transparency(6)
        self.assertEqual(sketch.ViewObject.Transparency, 25)
        tip.isDerivedFrom = lambda base: False
        self.assert_error("VISUAL_SNAPSHOT_CHANGED", lambda: session.preview_transparency(37))
        self.assertEqual((self.document.opened, body.ViewObject.assignments, self.document.aborts), (1, 1, 0))
        session.invalidate()

    def test_body_tip_first_six_percent_and_repeated_previews_cancel_restore_both(self):
        body, tip, _ = self.body_fixture()
        session = self.session()
        session.preview_transparency(6)
        session.preview_transparency(27)
        session.preview_random_color()
        self.assertEqual((body.ViewObject.Transparency, tip.ViewObject.Transparency), (27, 27))
        self.assertFalse(session._foreign_change)
        self.assertTrue(any(event["role"] == "current_tip_view" and event["decision"] == "expected"
                            for event in session.diagnostics["events"]))
        session.cancel()
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.assertIsNone(self.app.active_transaction)
        self.assertEqual((self.document.opened, self.document.commits, self.document.aborts), (1, 0, 1))

    def test_body_tip_accept_undo_redo_cover_both_full_appearances(self):
        body, tip, _ = self.body_fixture()
        session = self.session()
        session.preview_transparency(6)
        session.preview_transparency(63)
        session.preview_random_color()
        final = native.plain_snapshot(self.document)
        session.accept()
        self.assertEqual((self.document.opened, self.document.commits, len(self.document.undo_stack)), (1, 1, 1))
        self.document.undo()
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.document.redo()
        self.assertEqual(native.plain_snapshot(self.document), final)

    def test_body_and_its_explicit_tip_are_rejected_before_any_write(self):
        self.body_fixture()
        self.assert_error("VISUAL_BODY_TIP_OVERLAP", lambda: self.session(("Part", "Tip")))
        self.assertEqual((self.document.opened, self.object.ViewObject.assignments), (0, 0))
        self.assertEqual(native.plain_snapshot(self.document), self.before)

    def test_body_tip_identity_and_full_material_snapshot_are_rechecked_before_writes(self):
        for change in ("tip_identity", "tip_material"):
            with self.subTest(change=change):
                self.setUp()
                body, tip, _ = self.body_fixture()
                session = self.session()
                if change == "tip_identity":
                    replacement = self.document.add("Replacement")
                    body.Tip = replacement
                else:
                    tip.ViewObject._appearance[0].Shininess = 17
                self.assert_error("VISUAL_SNAPSHOT_CHANGED", lambda: session.preview_transparency(6))
                self.assertEqual((self.document.opened, self.document.aborts, body.ViewObject.assignments), (0, 0, 0))

    def test_body_tip_wrong_coupled_result_rolls_back_both_and_preserves_original_error(self):
        body, tip, propagate = self.body_fixture()
        def incorrect_native_result():
            propagate()
            tip.ViewObject._appearance[0].Shininess = 17
        body.ViewObject.pollute = incorrect_native_result
        session = self.session()
        error = self.assert_error("VISUAL_PREVIEW_FAILED", lambda: session.preview_transparency(6))
        self.assertEqual(error.appearance_diagnostics["primary_error"]["code"], "VISUAL_SNAPSHOT_CHANGED")
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.assertEqual(self.document.aborts, 1)

    def test_tip_model_unrelated_view_and_linked_instance_events_remain_foreign(self):
        for change in ("tip_label", "tip_shape", "other_object", "linked_instance", "foreign_document"):
            with self.subTest(change=change):
                self.setUp()
                body, tip, propagate = self.body_fixture()
                other = self.document.add("Other")
                instance = self.document.add("Instance", source=body)
                foreign = types.SimpleNamespace(Document=object())
                session = self.session()
                def native_then_external():
                    propagate()
                    if change in ("tip_label", "tip_shape"):
                        for observer in list(self.app_observers): observer.slotChangedObject(tip, "Label" if change == "tip_label" else "Shape")
                    elif change == "other_object":
                        for observer in list(self.gui_observers): observer.slotChangedObject(other.ViewObject, "Transparency")
                    elif change == "linked_instance":
                        for observer in list(self.gui_observers): observer.slotChangedObject(instance.ViewObject, "ShapeAppearance")
                    else:
                        for observer in list(self.app_observers): observer.slotChangedObject(foreign, "Label")
                body.ViewObject.pollute = native_then_external
                self.assert_error("VISUAL_SESSION_CONTAMINATED", lambda: session.preview_transparency(6))
                self.assertEqual((self.document.commits, self.document.aborts), (0, 0))
                if change == "linked_instance":
                    self.assertEqual(session.diagnostics["events"][-1]["role"], "linked_instance_view")
                    self.assertEqual(session.diagnostics["events"][-1]["decision"], "foreign")

    def test_link_source_body_tip_is_readonly_guard_not_a_writable_companion(self):
        body, tip, _ = self.body_fixture()
        instance = self.document.add("Instance", source=body)
        before_source = native.plain_snapshot(self.document)
        session = self.session(("Instance",))
        self.assertEqual(session.body_tips, [])
        self.assertEqual(len(session.source_body_tips), 1)
        session.preview_transparency(6)
        session.preview_random_color()
        self.assertEqual((body.ViewObject.assignments, tip.ViewObject.assignments), (0, 0))
        self.assertEqual(native.plain_snapshot(self.document)["Tip"], before_source["Tip"])
        session.cancel()
        self.assertEqual(native.plain_snapshot(self.document), before_source)
        self.assert_error("LINK_SOURCE_SELECTED", lambda: self.session(("Instance", "Tip")))

    def test_link_source_body_tip_material_change_is_detected_without_instance_writes(self):
        body, tip, _ = self.body_fixture()
        instance = self.document.add("Instance", source=body)
        session = self.session(("Instance",))
        tip.ViewObject._appearance[0].Shininess = 17
        self.assert_error("VISUAL_SNAPSHOT_CHANGED", lambda: session.preview_transparency(6))
        self.assertEqual((self.document.opened, instance.ViewObject.assignments), (0, 0))

    def history_fixture(self):
        body, tip, _ = self.body_fixture()
        history = [self.document.add("History%d" % index) for index in range(3)]
        for index, member in enumerate(history):
            member.TypeId = "PartDesign::Feature"
            member.ViewObject._appearance = [native.Material(Shininess=13 + index)]
        body.Group = history + [tip]
        links = [self.document.add("Instance%d" % index, source=body) for index in range(3)]
        def propagate_history():
            for member in history + [tip]:
                for observer in list(self.gui_observers):
                    observer.slotBeforeChangeObject(member.ViewObject, "ShapeAppearance")
                    observer.slotBeforeChangeObject(member.ViewObject, "Transparency")
                member.ViewObject.ShapeAppearance = copy.deepcopy(body.ViewObject.ShapeAppearance)
                for observer in list(self.gui_observers):
                    observer.slotChangedObject(member.ViewObject, "Transparency")
                    observer.slotChangedObject(member.ViewObject, "ShapeAppearance")
            for observer in list(self.gui_observers):
                observer.slotChangedObject(body.ViewObject, "Transparency")
                observer.slotChangedObject(body.ViewObject, "ShapeAppearance")
        body.ViewObject.pollute = propagate_history
        self.before = native.plain_snapshot(self.document)
        return body, tip, history, links, propagate_history

    def test_body_history_first_six_continuous_random_cancel_restores_complete_members(self):
        body, tip, history, links, _ = self.history_fixture()
        session = self.session()
        self.assertEqual(len(session.body_tips[0]["members"]), 4)
        session.preview_transparency(6)
        session.preview_transparency(37)
        session.preview_random_color()
        self.assertTrue(all(member.ViewObject.Transparency == 37 for member in history + [tip]))
        self.assertFalse(session._foreign_change)
        events = session.diagnostics["events"]
        self.assertTrue(any(event["role"] == "current_body_member_view" for event in events))
        self.assertTrue(all(event["decision"] == "expected" for event in events))
        session.cancel()
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.assertEqual((self.document.opened, self.document.commits, self.document.aborts), (1, 0, 1))
        self.assertIsNone(self.app.active_transaction)
        self.assertTrue(all(link.ViewObject.assignments == 0 for link in links))

    def test_body_history_ok_undo_redo_covers_every_frozen_member(self):
        self.history_fixture()
        session = self.session()
        session.preview_transparency(6)
        session.preview_transparency(73)
        session.preview_random_color()
        final = native.plain_snapshot(self.document)
        session.accept()
        self.assertEqual((self.document.commits, len(self.document.undo_stack)), (1, 1))
        self.document.undo()
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.document.redo()
        self.assertEqual(native.plain_snapshot(self.document), final)

    def test_body_and_any_history_member_overlap_is_rejected_before_writing(self):
        _, _, history, _, _ = self.history_fixture()
        self.assert_error("VISUAL_BODY_TIP_OVERLAP", lambda: self.session(("Part", history[0].Name)))
        self.assertEqual((self.document.opened, self.object.ViewObject.assignments), (0, 0))

    def test_body_group_membership_order_view_identity_and_materials_remain_frozen(self):
        for change in ("append", "remove", "reorder", "view_identity", "member_material"):
            with self.subTest(change=change):
                self.setUp()
                body, tip, history, _, _ = self.history_fixture()
                session = self.session()
                if change == "append": body.Group.append(self.document.add("Extra"))
                elif change == "remove": body.Group.remove(history[0])
                elif change == "reorder": body.Group = list(reversed(body.Group))
                elif change == "view_identity": history[0].ViewObject = native.View()
                else: history[0].ViewObject._appearance[0].Shininess = 99
                self.assert_error("VISUAL_SNAPSHOT_CHANGED", lambda: session.preview_transparency(6))
                self.assertEqual((self.document.opened, self.document.aborts, body.ViewObject.assignments), (0, 0, 0))

    def test_history_model_changes_and_inherited_link_view_notifications_still_contaminate(self):
        for change in ("label", "shape", "visibility", "link"):
            with self.subTest(change=change):
                self.setUp()
                body, _, history, links, propagate = self.history_fixture()
                session = self.session()
                def then_external():
                    propagate()
                    if change in ("label", "shape"):
                        for observer in list(self.app_observers): observer.slotChangedObject(history[0], "Label" if change == "label" else "Shape")
                    else:
                        view = history[0].ViewObject if change == "visibility" else links[0].ViewObject
                        for observer in list(self.gui_observers): observer.slotChangedObject(view, "Visibility" if change == "visibility" else "ShapeAppearance")
                body.ViewObject.pollute = then_external
                self.assert_error("VISUAL_SESSION_CONTAMINATED", lambda: session.preview_transparency(6))
                self.assertEqual((self.document.commits, self.document.aborts), (0, 0))
                if change == "link": self.assertEqual(session.diagnostics["events"][-1]["role"], "linked_instance_view")

    def test_source_body_history_members_are_only_readonly_guards(self):
        body, tip, history, links, _ = self.history_fixture()
        session = self.session((links[0].Name,))
        self.assertEqual(session.body_tips, [])
        self.assertEqual(len(session.source_body_tips[0]["members"]), 4)
        session.preview_transparency(6)
        self.assertTrue(all(member.ViewObject.assignments == 0 for member in history + [tip, body]))
        session.cancel()
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.assert_error("LINK_SOURCE_SELECTED", lambda: self.session((links[0].Name, history[0].Name)))

    def test_writable_body_member_cannot_overlap_another_links_source_body_member(self):
        _, _, history, _, _ = self.history_fixture()
        other_body = self.document.add("OtherBody")
        other_body.TypeId = "PartDesign::Body"
        other_body.Group, other_body.Tip = [history[0]], history[0]
        instance = self.document.add("OtherInstance", source=other_body)
        self.assert_error("LINK_SOURCE_SELECTED", lambda: self.session(("Part", instance.Name)))
        self.assertEqual((self.document.opened, self.object.ViewObject.assignments), (0, 0))

    def test_source_body_history_material_change_prevents_instance_write(self):
        _, _, history, links, _ = self.history_fixture()
        session = self.session((links[0].Name,))
        history[0].ViewObject._appearance[0].Shininess = 99
        self.assert_error("VISUAL_SNAPSHOT_CHANGED", lambda: session.preview_transparency(6))
        self.assertEqual((self.document.opened, links[0].ViewObject.assignments), (0, 0))

    def test_body_member_material_budget_is_checked_before_opening_a_transaction(self):
        body, tip, _ = self.body_fixture()
        members = [self.document.add("Feature%d" % index) for index in range(controls.visual.PFT_MAX_MATERIALS)]
        body.Group = members + [tip]
        self.assert_error("VISUAL_PREVIEW_TOO_LARGE", self.session)
        self.assertEqual((self.document.opened, body.ViewObject.assignments), (0, 0))

    def test_unsupported_body_group_member_is_frozen_without_material_or_shape_reads(self):
        body, _, _ = self.body_fixture()
        helper = self.document.add("Origin")
        helper.TypeId = "App::Origin"
        helper.ViewObject.getTypeIdOfProperty = lambda name: "App::PropertyString"
        helper.ViewObject._appearance = None  # Any accidental material snapshot must fail.
        body.Group.insert(0, helper)
        session = self.session()
        self.assertEqual(len(session.body_tips[0]["members"]), 1)
        self.assertIs(session.body_tips[0]["group"][0], helper)
        session.preview_transparency(6)
        session.cancel()
        self.assertEqual(helper.ViewObject.assignments, 0)

    def test_direct_random_helper_preserves_original_backend_summary(self):
        with patch.object(controls.visual, "prepare_appearance", return_value={"token": "cap"}) as prepare:
            with patch.object(controls.visual, "apply_appearance", return_value={"saved": False}) as apply:
                self.assertEqual(controls.apply_random_color(self.app, self.gui, self.document.Name, ["Part"]),
                                 {"saved": False})
        prepare.assert_called_once_with(self.app, self.gui, self.document.Name, ["Part"], "random_color")
        apply.assert_called_once_with(self.app, self.gui, {"token": "cap"})


class AppearancePanelTests(unittest.TestCase):
    assert_error = AppearanceSessionTests.assert_error

    def setUp(self):
        AppearanceSessionTests.setUp(self)
        widgets = self.widgets = PftWidgets()
        class Dialog(widgets.QDialog):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.finished = PftSignal()
            def setModal(self, value): self.modal = value
            def isVisible(self): return self.visible
            def accept(self): super().accept(); self.finished.emit(1)
            def reject(self): super().reject(); self.finished.emit(0)
        class Integer(widgets.Base):
            def __init__(self, parent):
                super().__init__(parent)
                self._value, self.minimum, self.maximum = 0, 0, 100
                self.valueChanged = PftSignal()
            def setRange(self, minimum, maximum): self.minimum, self.maximum = minimum, maximum
            def setOrientation(self, value): self.orientation = value
            def setSuffix(self, value): self.suffix = value
            def setKeyboardTracking(self, value): self.keyboard_tracking = value
            def value(self): return self._value
            def setValue(self, value):
                if value != self._value:
                    self._value = value
                    self.valueChanged.emit(value)
        class Timer:
            def __init__(self, parent): self.timeout, self.running = PftSignal(), False
            def setInterval(self, value): self.interval = value
            def start(self): self.running = True
            def stop(self): self.running = False
        widgets.QDialog, widgets.QSlider, widgets.QSpinBox = Dialog, Integer, Integer
        core = types.SimpleNamespace(Qt=types.SimpleNamespace(Horizontal=1), QTimer=Timer)
        handle = patch.object(controls, "_qt", return_value=(core, widgets))
        handle.start()
        self.addCleanup(handle.stop)

    def show(self, **kwargs):
        return controls.show_appearance_panel(self.app, self.gui, scope=(self.document.Name, ["Part"]), **kwargs)

    def named(self, suffix):
        return self.widgets.named("PhoenixTools_Appearance" + suffix)

    def test_nonmodal_small_panel_initialization_has_no_write(self):
        dialog = self.show()
        self.assertFalse(dialog.modal)
        self.assertEqual((dialog._width, dialog._height), (300, 140))
        self.assertTrue(dialog.visible)
        self.assertEqual(self.widgets.exec_calls, 0)
        self.assertEqual((self.document.opened, self.object.ViewObject.assignments), (0, 0))
        self.assertEqual(self.named("TransparencyValue").suffix, "%")
        self.assertFalse(self.named("TransparencyValue").keyboard_tracking)

    def test_slider_spin_sync_previews_once_per_change_and_ok_commits_once(self):
        applied = Mock()
        dialog = self.show(on_applied=applied)
        self.named("TransparencySlider").setValue(36)
        self.assertEqual(self.named("TransparencyValue").value(), 36)
        self.assertEqual(self.object.ViewObject.assignments, 1)
        self.named("TransparencyValue").setValue(81)
        self.assertEqual(self.named("TransparencySlider").value(), 81)
        self.assertEqual(self.object.ViewObject.assignments, 2)
        self.named("RandomColor").clicked.emit()
        self.assertEqual(self.document.opened, 1)
        self.named("Buttons").accepted.emit()
        self.assertEqual(self.document.commits, 1)
        applied.assert_called_once()
        self.assertFalse(dialog._pft_guard_timer.running)

    def test_cancel_escape_and_windowclose_restore_preview(self):
        for close in ("cancel", "escape", "window"):
            with self.subTest(close=close):
                dialog = self.show()
                self.named("TransparencySlider").setValue(40)
                if close == "cancel":
                    self.named("Buttons").rejected.emit()
                elif close == "escape":
                    dialog.reject()
                else:
                    event = types.SimpleNamespace(accept=Mock())
                    dialog.closeEvent(event)
                    event.accept.assert_called_once()
                self.assertEqual(native.plain_snapshot(self.document), self.before)
                self.assertEqual(self.document.commits, 0)
                self.assertFalse(dialog._pft_guard_timer.running)

    def test_initial_mixed_value_and_open_cancel_never_write(self):
        self.object.ViewObject._appearance.append(native.Material(Transparency=0.5))
        dialog = self.show()
        self.assertEqual(self.named("Status").text, controls.tr("混合"))
        self.named("Buttons").rejected.emit()
        self.assertEqual((self.document.opened, self.object.ViewObject.assignments), (0, 0))

    def test_timer_document_switch_restores_owned_preview_and_disables_controls(self):
        dialog = self.show()
        self.named("TransparencySlider").setValue(40)
        self.app.ActiveDocument = object()
        dialog._pft_guard_timer.timeout.emit()
        self.assertEqual(native.plain_snapshot(self.document), self.before)
        self.assertFalse(self.named("TransparencySlider").enabled)
        self.assertFalse(dialog._pft_guard_timer.running)

    def test_timer_never_aborts_replacement_transaction(self):
        errors = Mock()
        dialog = self.show(on_error=errors)
        self.named("TransparencySlider").setValue(40)
        self.app.active_transaction = ("Other", 987)
        dialog._pft_guard_timer.timeout.emit()
        self.assertEqual((self.document.commits, self.document.aborts), (0, 0))
        self.assertFalse(self.named("TransparencySlider").enabled)
        self.assertTrue(errors.called)

    def test_failed_cancel_persists_warning_after_reject_without_closing_foreign_transaction(self):
        printed, messages = [], []
        self.app.Console = types.SimpleNamespace(PrintError=printed.append)
        self.gui.getMainWindow = lambda: types.SimpleNamespace(statusBar=lambda: types.SimpleNamespace(
            showMessage=lambda *args: messages.append(args)))
        dialog = self.show()
        self.named("TransparencySlider").setValue(40)
        self.app_observers[0].slotChangedObject(types.SimpleNamespace(Document=object()), "Label")
        dialog.reject()
        expected = controls.tr("检测到其他修改，未提交或取消这些修改。")
        self.assertEqual((self.document.commits, self.document.aborts), (0, 0))
        self.assertIsNotNone(self.app.active_transaction)
        self.assertEqual(dialog._pft_session.state, "invalid")
        self.assertEqual(printed[0], "[Phoenix Tools] " + expected + "\n")
        self.assertIn('"owned_transaction_id": 11', printed[1])
        self.assertIn('"code": "VISUAL_SESSION_CONTAMINATED"', printed[1])
        self.assertEqual(messages, [(expected, 15000)])
        self.assertFalse(dialog._pft_guard_timer.running)

    def test_ok_with_unverified_commit_does_not_accept_or_emit_applied(self):
        applied = Mock()
        dialog = self.show(on_applied=applied)
        self.named("TransparencySlider").setValue(6)
        finished = []
        dialog.finished.connect(lambda *args: finished.append(args))
        self.document.commitTransaction = lambda: native.Document.commitTransaction(self.document)
        self.named("Buttons").accepted.emit()
        self.assertEqual(dialog._pft_session.state, "invalid")
        applied.assert_not_called()
        self.assertEqual(finished, [])
        self.assertEqual((self.document.commits, self.document.aborts), (1, 0))
        self.named("Buttons").rejected.emit()
        self.assertEqual((self.document.commits, self.document.aborts), (1, 0))
        self.assertEqual(len(finished), 1)

    def test_ok_with_foreign_commit_callback_does_not_accept_or_emit_applied(self):
        applied = Mock()
        dialog = self.show(on_applied=applied)
        self.named("TransparencySlider").setValue(6)
        finished = []
        dialog.finished.connect(lambda *args: finished.append(args))
        def commit_then_callback():
            Document.commitTransaction(self.document)
            for observer in list(self.app_observers):
                observer.slotChangedObject(self.object, "Label")
        self.document.commitTransaction = commit_then_callback
        self.named("Buttons").accepted.emit()
        self.assertEqual(dialog._pft_session.state, "invalid")
        self.assertEqual(dialog._pft_session.diagnostics["commit_status"], "closed_contaminated")
        applied.assert_not_called()
        self.assertEqual(finished, [])
        self.assertIsNone(self.app.active_transaction)
        self.assertEqual((self.document.commits, self.document.aborts), (1, 0))
        self.named("Buttons").rejected.emit()
        self.assertEqual((self.document.commits, self.document.aborts), (1, 0))
        self.assertEqual(len(finished), 1)

    def test_reopening_reuses_visible_panel_before_resolving_scope(self):
        parent = self.widgets.QWidget()
        first = self.show(parent=parent)
        self.named("TransparencySlider").setValue(40)
        resolve = Mock(side_effect=AssertionError("do not resolve a new scope during preview"))
        second = controls.show_appearance_panel(self.app, self.gui, parent=parent, get_scope=resolve)
        self.assertIs(second, first)
        resolve.assert_not_called()
        self.assertEqual(self.document.opened, 1)

    def test_invalid_scope_never_uses_current_selection_as_fallback(self):
        self.gui.Selection = types.SimpleNamespace(getSelection=Mock(side_effect=AssertionError("no fallback")))
        self.assert_error("VISUAL_TARGETS_REQUIRED",
                          lambda: controls.show_appearance_panel(self.app, self.gui, get_scope=lambda: None))
        self.assertEqual(self.document.opened, 0)


if __name__ == "__main__":
    unittest.main()
