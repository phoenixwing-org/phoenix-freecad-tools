# SPDX-License-Identifier: Apache-2.0
"""Check effects, scope and rollback of the structured native actions."""
import importlib.util
from pathlib import Path
import types
import unittest

path = Path(__file__).resolve().parents[2] / 'addon/PhoenixFreeCADMCP/phoenix_freecad_operations.py'
spec = importlib.util.spec_from_file_location('phoenix_actions_test', path)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class Failure(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class View:
    def __init__(self):
        self._visibility = True
        self.reject = False
    @property
    def Visibility(self): return self._visibility
    @Visibility.setter
    def Visibility(self, value):
        self._visibility = not value if self.reject else value


class Document:
    def __init__(self):
        self.Name, self.UndoMode, self.events = 'Doc', 1, []
        self.Objects = [types.SimpleNamespace(Name=name, ViewObject=View(), State=['Up-to-date']) for name in ['One', 'Two']]
        self.recompute_result = True
    def getObject(self, name): return next((obj for obj in self.Objects if obj.Name == name), None)
    def openTransaction(self, title):
        self.events.append('open')
        self.before = [obj.ViewObject.Visibility for obj in self.Objects]
    def commitTransaction(self): self.events.append('commit')
    def abortTransaction(self):
        self.events.append('abort')
        for obj, value in zip(self.Objects, self.before): obj.ViewObject._visibility = value
    def recompute(self):
        self.events.append('recompute')
        return self.recompute_result
    def save(self): raise AssertionError('must not save')


class Selection:
    def __init__(self):
        self.records = {'Doc': [('One', ('Face2',))], 'Other': [('Private', ())]}
        self.fail_once = False
    def getSelectionEx(self, name, resolve):
        assert resolve == 0
        return [types.SimpleNamespace(ObjectName=obj, SubElementNames=subs) for obj, subs in self.records[name]]
    def clearSelection(self, name, clear):
        assert clear is False
        self.records[name] = []
    def addSelection(self, doc, name, sub, x, y, z, clear):
        assert clear is False
        if self.fail_once and name == 'Two':
            self.fail_once = False
            raise RuntimeError('selection rejected')
        self.records[doc].append((name, (sub,) if sub else ()))


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.doc = Document()
        self.editing = None
        self.transaction = None
        self.selection = Selection()
        self.app = types.SimpleNamespace(ActiveDocument=self.doc, getDocument=lambda name: self.doc if name == 'Doc' else None,
                                         getActiveTransaction=lambda: self.transaction)
        self.gui = types.SimpleNamespace(Selection=self.selection, getDocument=lambda name: types.SimpleNamespace(getInEdit=lambda: self.editing))
    def call(self, method, **params):
        return ops.perform(method, {'docName': 'Doc', **params}, self.app, self.gui, Failure)
    def fails(self, code, method, **params):
        with self.assertRaises(Failure) as caught: self.call(method, **params)
        self.assertEqual(caught.exception.code, code)

    def test_action_schema_requires_explicit_targets_and_strict_types(self):
        bad = [('set_selection', {}), ('set_selection', {'docName': 'Doc', 'objectNames': ['One', 'One']}),
               ('set_selection', {'docName': 'Doc', 'objectNames': [dict()]}),
               ('set_selection', {'docName': 'Doc', 'objectNames': ['One\n']}),
               ('set_visibility', {'docName': 'Doc', 'objectNames': [], 'visible': True}),
               ('set_visibility', {'docName': 'Doc', 'objectNames': ['One'], 'visible': 1}),
               ('recompute_document', {'docName': 'Doc', 'save': True})]
        for method, params in bad:
            with self.subTest(method=method, params=params), self.assertRaises(Failure): ops.validate(method, params, Failure)

    def test_selection_replaces_only_target_document_and_can_clear_it(self):
        answer = self.call('set_selection', objectNames=['Two'])
        self.assertTrue(answer['changed'])
        self.assertEqual(self.selection.records['Doc'], [('Two', ())])
        self.assertEqual(self.selection.records['Other'], [('Private', ())])
        self.assertEqual(self.doc.events, [])
        self.call('set_selection', objectNames=[])
        self.assertEqual(self.selection.records['Doc'], [])
        self.assertEqual(self.selection.records['Other'], [('Private', ())])

    def test_missing_target_never_clears_selection_or_opens_transaction(self):
        before = dict(self.selection.records)
        self.fails('OBJECT_NOT_FOUND', 'set_selection', objectNames=['One', 'Missing'])
        self.fails('OBJECT_NOT_FOUND', 'set_visibility', objectNames=['One', 'Missing'], visible=False)
        self.assertEqual(self.selection.records, before)
        self.assertEqual(self.doc.events, [])

    def test_partial_selection_failure_restores_original_subelement_selection(self):
        before = dict(self.selection.records)
        self.selection.fail_once = True
        self.fails('SELECTION_FAILED', 'set_selection', objectNames=['One', 'Two'])
        self.assertEqual(self.selection.records, before)

    def test_silently_rejected_selection_restore_is_not_reported_as_restored(self):
        original_add = self.selection.addSelection
        failed = [False]
        def gate(doc, name, sub, x, y, z, clear):
            if name == 'Two':
                failed[0] = True
                raise RuntimeError('target rejected')
            if failed[0]:
                return
            return original_add(doc, name, sub, x, y, z, clear)
        self.selection.addSelection = gate
        self.fails('SELECTION_RESTORE_FAILED', 'set_selection', objectNames=['Two'])
        self.assertEqual(self.selection.records['Other'], [('Private', ())])

    def test_visibility_has_one_transaction_and_reports_real_values(self):
        result = self.call('set_visibility', objectNames=['One', 'Two'], visible=False)
        self.assertEqual(self.doc.events, ['open', 'commit'])
        self.assertEqual(result['changed_count'], 2)
        self.assertTrue(result['undoable'])
        self.assertFalse(result['saved'])
        self.assertTrue(all(not row['visible'] and row['before'] for row in result['objects']))
        self.call('set_visibility', objectNames=['One', 'Two'], visible=False)
        self.assertEqual(self.doc.events, ['open', 'commit'])

    def test_visibility_override_rolls_back_all_targets(self):
        self.doc.Objects[1].ViewObject.reject = True
        self.fails('VISIBILITY_FAILED', 'set_visibility', objectNames=['One', 'Two'], visible=False)
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.assertTrue(all(obj.ViewObject.Visibility for obj in self.doc.Objects))

    def test_background_editing_and_active_transaction_are_rejected(self):
        self.app.ActiveDocument = object()
        self.fails('DOCUMENT_NOT_ACTIVE', 'recompute_document')
        self.app.ActiveDocument = self.doc
        self.editing = object()
        self.fails('DOCUMENT_BUSY', 'recompute_document')
        self.editing, self.transaction = None, 'User operation'
        self.fails('DOCUMENT_BUSY', 'set_visibility', objectNames=['One'], visible=False)
        self.assertEqual(self.doc.events, [])

    def test_visibility_without_undo_is_rejected(self):
        self.doc.UndoMode = 0
        self.fails('UNDO_DISABLED', 'set_visibility', objectNames=['One'], visible=False)
        self.assertEqual(self.doc.events, [])

    def test_recompute_checks_native_return_and_object_errors_without_save(self):
        self.assertTrue(self.call('recompute_document')['recomputed'])
        self.doc.recompute_result = False
        self.fails('RECOMPUTE_FAILED', 'recompute_document')
        self.doc.recompute_result = True
        self.doc.Objects[0].State = ['Invalid']
        self.fails('RECOMPUTE_FAILED', 'recompute_document')
        self.assertEqual(self.doc.events, ['recompute'] * 3)
