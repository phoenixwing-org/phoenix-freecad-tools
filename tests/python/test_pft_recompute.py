# SPDX-License-Identifier: Apache-2.0
"""Independent native-API-shaped fixtures, never a FreeCAD process or document."""
import types
import unittest
from unittest.mock import patch
import test_pft_actions as fixture


class RecomputeContractTests(unittest.TestCase):
    setUp = fixture.ActionTests.setUp
    call = fixture.ActionTests.call
    fails = fixture.ActionTests.fails

    def test_zero_and_positive_counts_are_observations_not_success_or_geometry(self):
        for count in (0, 1, 7, 9007199254740991):
            with self.subTest(count=count):
                self.doc.recompute_result = count
                result = self.call('recompute_document')
                self.assertEqual(result, {'document': 'Doc', 'saved': False, 'recompute_invoked': True,
                    'native_object_count': count, 'checked_object_count': 2,
                    'object_status': 'no_invalid_or_pending', 'native_completion': 'not_verified',
                    'geometry_validity': 'not_verified', 'geometry_freshness': 'not_verified'})
                self.assertNotIn('native_success', result)
                self.assertNotIn('recomputed', result)
        self.assertEqual(self.doc.events, ['recompute'] * 4)

    def test_empty_native_document_and_expanded_flags_are_supported(self):
        self.doc.Objects[0].State = ['Expanded', 'Up-to-date']
        self.assertEqual(self.call('recompute_document')['checked_object_count'], 2)
        self.doc.Objects = []
        self.doc.recompute_result = 0
        self.assertEqual(self.call('recompute_document')['checked_object_count'], 0)

    def test_malformed_native_counts_are_uncertain_after_exactly_one_call(self):
        for count in (True, False, -1, 0.0, 1.5, float('nan'), '2', None, 9007199254740992):
            with self.subTest(kind=type(count).__name__):
                self.doc.recompute_result = count
                before = len(self.doc.events)
                self.fails('RECOMPUTE_UNVERIFIED', 'recompute_document')
                self.assertEqual(len(self.doc.events), before + 1)

    def test_recompute_exception_is_fixed_and_not_retried_or_rolled_back(self):
        def failed():
            self.doc.events.append('recompute')
            raise RuntimeError('SYNTHETIC_INTERNAL_DETAIL')
        self.doc.recompute = failed
        with self.assertRaises(fixture.Failure) as caught: self.call('recompute_document')
        self.assertEqual(caught.exception.code, 'RECOMPUTE_UNVERIFIED')
        self.assertNotIn('SYNTHETIC_INTERNAL_DETAIL', str(caught.exception))
        self.assertEqual(self.doc.events, ['recompute'])

    def test_freeze_is_not_overridden_and_unknown_freeze_never_calls_native(self):
        for value, code in ((True, 'RECOMPUTE_FROZEN'), (None, 'RECOMPUTE_PRECHECK_FAILED'),
                            (0, 'RECOMPUTE_PRECHECK_FAILED'), ('false', 'RECOMPUTE_PRECHECK_FAILED')):
            self.doc.RecomputesFrozen = value
            self.fails(code, 'recompute_document')
            self.assertIs(self.doc.RecomputesFrozen, value)
        del self.doc.RecomputesFrozen
        self.fails('RECOMPUTE_PRECHECK_FAILED', 'recompute_document')
        self.assertEqual(self.doc.events, [])

    def test_all_falsey_non_none_application_transaction_and_edit_values_block(self):
        for value in (False, 0, '', [], (), {}):
            with self.subTest(kind=type(value).__name__):
                self.transaction = value
                self.fails('DOCUMENT_BUSY', 'recompute_document')
                self.transaction = None
                self.editing = value
                self.fails('DOCUMENT_BUSY', 'recompute_document')
                self.editing = None
        self.assertEqual(self.doc.events, [])

    def test_document_undo_transaction_must_be_explicitly_idle(self):
        for value in (True, None, 0, '', []):
            self.doc.HasPendingTransaction = value
            self.fails('DOCUMENT_BUSY', 'recompute_document')
        del self.doc.HasPendingTransaction
        self.fails('DOCUMENT_BUSY', 'recompute_document')
        self.assertEqual(self.doc.events, [])

    def test_application_and_gui_state_exceptions_are_typed_before_writes(self):
        def failed(*args): raise RuntimeError('SYNTHETIC_INTERNAL_DETAIL')
        with patch.object(self.app, 'getActiveTransaction', failed): self.fails('DOCUMENT_BUSY', 'recompute_document')
        with patch.object(self.gui, 'getDocument', failed): self.fails('DOCUMENT_BUSY', 'recompute_document')
        self.assertEqual(self.doc.events, [])

    def test_invalid_objects_cannot_be_hidden_by_positive_count(self):
        for states, valid in ((['Invalid'], True), (['Up-to-date'], False)):
            self.doc.recompute_result = 9
            self.doc.Objects[0].State = states
            self.doc.Objects[0].isValid = lambda: valid
            self.fails('RECOMPUTE_FAILED', 'recompute_document')
        self.assertEqual(self.doc.events, ['recompute'] * 2)

    def test_up_to_date_alone_does_not_override_pending_and_partial_flags(self):
        for state in ('Touched', 'Recompute', 'Recompute2', 'Restore', 'Partial', 'Importing'):
            with self.subTest(state=state):
                self.doc.Objects[0].State = [state, 'Up-to-date']
                self.fails('RECOMPUTE_INCOMPLETE', 'recompute_document')
        self.doc.Objects[0].State = ['Expanded']
        self.fails('RECOMPUTE_INCOMPLETE', 'recompute_document')
        self.assertEqual(self.doc.events, ['recompute'] * 7)

    def test_unknown_states_and_non_boolean_validity_are_not_silent_empty_success(self):
        obj = self.doc.Objects[0]
        for state in (None, [], (), 'Up-to-date', [1], [True], ['Up-to-date'] * 2, ['Unknown'], ['Error']):
            obj.State = state
            self.fails('RECOMPUTE_UNVERIFIED', 'recompute_document')
        obj.State = ['Up-to-date']
        for value in (1, 0, None, 'true'):
            obj.isValid = lambda: value
            self.fails('RECOMPUTE_UNVERIFIED', 'recompute_document')
        del obj.State
        self.fails('RECOMPUTE_UNVERIFIED', 'recompute_document')
        self.assertEqual(self.doc.events, ['recompute'] * 14)

    def test_unreadable_isvalid_and_state_are_unknown_with_no_native_detail(self):
        obj = self.doc.Objects[0]
        def failed(): raise RuntimeError('SYNTHETIC_INTERNAL_DETAIL')
        obj.isValid = failed
        with self.assertRaises(fixture.Failure) as caught: self.call('recompute_document')
        self.assertEqual(caught.exception.code, 'RECOMPUTE_UNVERIFIED')
        self.assertNotIn('SYNTHETIC_INTERNAL_DETAIL', str(caught.exception))
        self.assertEqual(self.doc.events, ['recompute'])

    def test_object_collection_budget_and_identity_fail_before_native_call(self):
        obj = self.doc.Objects[0]
        self.doc.Objects = [obj] * (fixture.ops.MAX_RECOMPUTE_OBJECTS + 1)
        self.fails('RECOMPUTE_PRECHECK_FAILED', 'recompute_document')
        self.doc.Objects = [obj, obj]
        self.fails('RECOMPUTE_PRECHECK_FAILED', 'recompute_document')
        self.doc.Objects = (obj,)
        self.fails('RECOMPUTE_PRECHECK_FAILED', 'recompute_document')
        self.assertEqual(self.doc.events, [])

    def test_recompute_does_not_rebind_closed_or_replaced_document(self):
        def replaced():
            self.doc.events.append('recompute')
            self.app.getDocument = lambda name: types.SimpleNamespace(Name='Doc')
            return 1
        self.doc.recompute = replaced
        self.fails('RECOMPUTE_UNVERIFIED', 'recompute_document')
        self.assertEqual(self.doc.events, ['recompute'])

    def test_new_transaction_edit_or_freeze_after_call_is_not_closed(self):
        for field in ('transaction', 'editing', 'freeze', 'pending'):
            self.setUp()
            def changed():
                self.doc.events.append('recompute')
                if field == 'transaction': self.transaction = ('Foreign', 3)
                if field == 'editing': self.editing = object()
                if field == 'freeze': self.doc.RecomputesFrozen = True
                if field == 'pending': self.doc.HasPendingTransaction = True
                return 1
            self.doc.recompute = changed
            self.fails('RECOMPUTE_UNVERIFIED', 'recompute_document')
            self.assertEqual(self.doc.events, ['recompute'])

    def test_final_observations_recheck_identity_and_do_not_close_other_transactions(self):
        obj = self.doc.Objects[0]
        def callback():
            self.transaction = ('Foreign', 7)
            return True
        obj.isValid = callback
        self.fails('RECOMPUTE_UNVERIFIED', 'recompute_document')
        self.assertEqual(self.doc.events, ['recompute'])
        self.assertEqual(self.transaction, ('Foreign', 7))

    def test_final_state_getter_cannot_switch_documents_or_detach_observed_objects(self):
        for change in ('active', 'collection', 'replacement'):
            self.setUp()
            def callback():
                if change == 'active': self.app.ActiveDocument = None
                elif change == 'collection': self.doc.Objects = self.doc.Objects[1:]
                else:
                    old = self.doc.Objects[0]
                    self.doc.Objects = [types.SimpleNamespace(**vars(old)), *self.doc.Objects[1:]]
                return True
            self.doc.Objects[0].isValid = callback
            self.fails('RECOMPUTE_UNVERIFIED', 'recompute_document')
            self.assertEqual(self.doc.events, ['recompute'])

    def test_unreadable_native_state_or_object_collection_never_becomes_empty(self):
        obj = self.doc.Objects[0]
        class Unreadable:
            Name, Document = obj.Name, obj.Document
            @property
            def State(self): raise RuntimeError('SYNTHETIC_INTERNAL_DETAIL')
            def isValid(self): raise AssertionError('State failed first')
        self.doc.Objects[0] = Unreadable()
        self.fails('RECOMPUTE_UNVERIFIED', 'recompute_document')
        self.assertEqual(self.doc.events, ['recompute'])
        self.setUp()
        def unavailable(document): raise RuntimeError('SYNTHETIC_INTERNAL_DETAIL')
        with patch.object(type(self.doc), 'Objects', property(unavailable), create=True):
            self.fails('RECOMPUTE_PRECHECK_FAILED', 'recompute_document')
        self.assertEqual(self.doc.events, [])

    def test_geometry_and_save_are_never_used_as_shortcuts_for_validity(self):
        class ForbiddenShape:
            @property
            def Shape(self): raise AssertionError('must not inspect Shape')
        obj = ForbiddenShape()
        obj.Name, obj.Document, obj.State, obj.isValid = 'Plain', self.doc, ['Up-to-date'], lambda: True
        self.doc.Objects = [obj]
        result = self.call('recompute_document')
        self.assertEqual(result['geometry_validity'], 'not_verified')
        self.assertEqual(self.doc.events, ['recompute'])

    def test_visibility_uses_the_same_strict_native_idle_and_undo_guards(self):
        for undo in (False, None, '1', 0):
            self.doc.UndoMode = undo
            self.fails('UNDO_DISABLED', 'set_visibility', objectNames=['One'], visible=False)
        self.doc.UndoMode = 1
        self.editing = False
        self.fails('DOCUMENT_BUSY', 'set_visibility', objectNames=['One'], visible=False)
        self.assertTrue(self.doc.Objects[0].ViewObject.Visibility)
        self.assertEqual(self.doc.events, [])
