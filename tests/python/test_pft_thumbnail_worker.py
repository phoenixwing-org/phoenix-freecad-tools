# SPDX-License-Identifier: Apache-2.0
"""Independent fake native host verifies ownership and lifecycle, not rendering."""
import base64
from hashlib import sha256
import importlib.util
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4
import zlib

ADDON = Path(__file__).resolve().parents[2] / 'addon' / 'PhoenixFreeCADTools'
_saved_path = list(sys.path)
sys.path.insert(0, str(ADDON))
try:
    import pft_worker_input
    import pft_thumbnail_capture
    import pft_thumbnail_worker as module
finally:
    sys.path[:] = _saved_path


def png(width=2, height=2):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress((b'\0' + b'\0\xff\0' * width) * height)) + chunk(b'IEND', b'')


class App:
    def __init__(self):
        self.documents, self.observers, self.closed, self.opened = {}, [], [], []
        self.on_open = self.on_close = None
        self.transaction = None
    def listDocuments(self): return dict(self.documents)
    def getActiveTransaction(self): return self.transaction
    def addDocumentObserver(self, observer): self.observers.append(observer)
    def add(self, name, filename):
        document = SimpleNamespace(Name=name, FileName=filename, HasPendingTransaction=False, Objects=[])
        document.getObject = lambda object_name: next((item for item in document.Objects if item.Name == object_name), None)
        self.documents[name] = document
        for observer in self.observers: observer.slotCreatedDocument(document)
        return document
    def openDocument(self, filename):
        self.opened.append(filename)
        document = self.add('Task', filename)
        if self.on_open: self.on_open(document)
        return document
    def closeDocument(self, name):
        if self.on_close: self.on_close(name)
        document = self.documents.pop(name)
        self.closed.append(name)
        for observer in self.observers: observer.slotDeletedDocument(document)


class VisibilityProvider:
    """Independent callback-capable native view fixture; material stays separate."""
    def __init__(self, visibility):
        self.value = visibility
        self.material = ("neutral material", 0.25)
        self.assignments = []
        self.on_get = self.on_set = None
    @property
    def Visibility(self):
        callback, self.on_get = self.on_get, None
        if callback is not None:
            callback()
        return self.value
    @Visibility.setter
    def Visibility(self, value):
        self.assignments.append(value)
        callback, self.on_set = self.on_set, None
        if callback is not None:
            callback()
        self.value = value


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.app = App()
        self.gui = SimpleNamespace(getDocument=lambda name: SimpleNamespace(getInEdit=lambda: None))
        self.callbacks, self.freezes, self.cleaned, self.verifications, self.captures = [], [], [], [], []
        self.revision = 'a' * 64
        self.record = SimpleNamespace(revision=self.revision, compressed_bytes=256,
                                      staged_path=Path('/private/pft-owned/input.FCStd'), restore_visibility=None)
        self.worker = module.PftThumbnailWorker(self.app, self.gui, '/private/pft-owned', str(uuid4()), str(uuid4()),
             lambda fn, delay: self.callbacks.append(fn), freeze=self.freeze, verify=self.verify,
             cleanup=lambda item: self.cleaned.append(item), capture=self.capture,
             submit=lambda fn: self.freezes.append(fn))
        self.params = {'workerId': self.worker.worker_id, 'sessionId': self.worker.session_id,
                       'jobId': str(uuid4()), 'file': '/source/public.FCStd', 'expectedSha256': self.revision,
                       'width': 2, 'height': 2}
    def freeze(self, filename, staging_root): return self.record
    def verify(self, record): self.verifications.append(record)
    def capture(self, *args, **kwargs):
        self.captures.append((args, kwargs))
        return {'png': png(), 'camera_restored': True, 'saved_cad': False}
    def call(self, method='render_document_thumbnail', **changes):
        params = dict(self.params if method == 'render_document_thumbnail' else {key: self.params[key] for key in ('workerId', 'sessionId', 'jobId')})
        params.update(changes)
        return self.worker.rpc(method, params)
    def step(self): self.callbacks.pop(0)()
    def complete(self):
        while self.callbacks or self.freezes:
            if self.callbacks: self.step()
            else: self.freezes.pop(0)()
        return self.call('get_thumbnail_job')
    def visibility_fixture(self, rows=None, initial=(False, True)):
        rows = (("Visible", "Part::Feature", True), ("Hidden", "Part::Feature", False)) if rows is None else rows
        self.record.restore_visibility = tuple(rows)
        providers = [VisibilityProvider(value) for value in initial]
        self.visibility_objects = []
        def setup(document):
            for (name, kind, _visible), provider in zip(rows, providers):
                obj = SimpleNamespace(Name=name, TypeId=kind, Document=document, ViewObject=provider)
                document.Objects.append(obj)
                self.visibility_objects.append(obj)
        self.app.on_open = setup
        return providers, setup

    def test_no_gui_restores_only_explicit_true_false_on_owned_copy(self):
        providers, _setup = self.visibility_fixture()
        materials = [item.material for item in providers]
        captured = []
        original_capture = self.worker.capture
        def capture(*args, **kwargs):
            captured.append(tuple(item.Visibility for item in providers))
            return original_capture(*args, **kwargs)
        self.worker.capture = capture
        self.call(); result = self.complete()
        self.assertEqual(result['state'], 'done')
        self.assertEqual(captured, [(True, False)])
        self.assertEqual([item.assignments for item in providers], [[True], [False]])
        self.assertEqual([item.material for item in providers], materials)
        self.assertEqual(self.app.closed, ['Task'])
        self.assertEqual(self.cleaned, [self.record])
        self.assertGreaterEqual(len(self.verifications), 4)

    def test_saved_gui_visibility_is_never_read_or_initialized(self):
        providers, _setup = self.visibility_fixture()
        self.record.restore_visibility = None
        for provider in providers:
            provider.on_get = lambda: (_ for _ in ()).throw(AssertionError('saved GUI visibility touched'))
        self.call(); result = self.complete()
        self.assertEqual(result['state'], 'done')
        self.assertEqual([item.assignments for item in providers], [[], []])
        self.assertEqual([item.value for item in providers], [False, True])
        self.assertTrue(all(item.on_get is not None for item in providers))

    def test_explicit_all_hidden_is_preserved_without_show_all(self):
        providers, _setup = self.visibility_fixture(rows=(("Visible", "Part::Feature", False),
                                                         ("Hidden", "Part::Feature", False)), initial=(False, False))
        self.call(); self.assertEqual(self.complete()['state'], 'done')
        self.assertEqual([item.value for item in providers], [False, False])
        self.assertEqual([item.assignments for item in providers], [[], []])

    def test_all_names_and_types_are_validated_before_any_visibility_setter(self):
        for changed in ('Name', 'TypeId', 'Document'):
            with self.subTest(changed=changed):
                self.setUp()
                providers, setup = self.visibility_fixture()
                def corrupt(document):
                    setup(document)
                    setattr(document.Objects[1], changed, 'Unexpected' if changed != 'Document' else object())
                self.app.on_open = corrupt
                self.call(); self.assertEqual(self.complete()['state'], 'failed')
                self.assertEqual([item.assignments for item in providers], [[], []])
                self.assertEqual(self.captures, [])

    def test_visibility_setter_failure_stops_before_other_objects_and_hides_raw_error(self):
        providers, _setup = self.visibility_fixture()
        providers[0].on_set = lambda: (_ for _ in ()).throw(RuntimeError('/synthetic/private-detail'))
        self.call(); result = self.complete()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['error']['code'], 'WORKER_VISIBILITY_RESTORE_FAILED')
        self.assertNotIn('private-detail', str(result))
        self.assertEqual(providers[1].assignments, [])
        self.assertEqual(self.captures, [])
        self.assertEqual(self.app.closed, ['Task'])

    def test_visibility_getter_cancellation_does_not_write_or_capture(self):
        providers, _setup = self.visibility_fixture()
        providers[0].on_get = lambda: self.call('cancel_thumbnail_job')
        self.call(); result = self.complete()
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual([item.assignments for item in providers], [[], []])
        self.assertEqual(self.captures, [])
        self.assertEqual(self.app.closed, ['Task'])

    def test_visibility_setter_stop_cleans_task_without_next_setter(self):
        providers, _setup = self.visibility_fixture()
        providers[0].on_set = self.worker.stop
        self.call(); result = self.complete()
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual([item.assignments for item in providers], [[True], []])
        self.assertEqual(self.captures, [])
        self.assertEqual(self.app.closed, ['Task'])

    def test_visibility_foreign_document_callback_is_preserved_without_next_setter(self):
        providers, _setup = self.visibility_fixture()
        providers[0].on_set = lambda: self.app.add('Foreign', '/source/foreign.FCStd')
        self.call(); result = self.complete()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(providers[1].assignments, [])
        self.assertIn('Foreign', self.app.documents)
        self.assertNotIn('Foreign', self.app.closed)
        self.assertEqual(self.worker.status()['state'], 'quarantined')
        self.assertEqual(self.captures, [])

    def test_visibility_foreign_transaction_is_never_cancelled_or_followed_by_setter(self):
        providers, _setup = self.visibility_fixture()
        def transaction(): self.app.transaction = 42
        providers[0].on_set = transaction
        self.call(); result = self.complete()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(self.app.transaction, 42)
        self.assertEqual(providers[1].assignments, [])
        self.assertEqual(self.app.closed, [])
        self.assertEqual(self.cleaned, [])
        self.assertEqual(self.worker.status()['state'], 'quarantined')

    def test_replaced_visibility_object_or_provider_is_never_written(self):
        for replace_object in (False, True):
            with self.subTest(replace_object=replace_object):
                self.setUp()
                providers, _setup = self.visibility_fixture()
                foreign = VisibilityProvider(True)
                def replace():
                    old = self.visibility_objects[0]
                    if replace_object:
                        other = SimpleNamespace(Name=old.Name, TypeId=old.TypeId, Document=old.Document, ViewObject=foreign)
                        old.Document.Objects[0] = other
                    else:
                        old.ViewObject = foreign
                providers[0].on_get = replace
                self.call(); self.assertEqual(self.complete()['state'], 'failed')
                self.assertEqual(foreign.assignments, [])
                self.assertEqual([item.assignments for item in providers], [[], []])
                self.assertEqual(self.captures, [])

    def test_last_idle_getter_provider_or_object_replacement_has_zero_setters(self):
        for replace_object in (False, True):
            with self.subTest(replace_object=replace_object):
                self.setUp()
                providers, _setup = self.visibility_fixture()
                foreign = VisibilityProvider(True)
                state = {'armed': False, 'idle_calls': 0}
                # First read prevalidates; second read is immediately before a
                # write. Replace only inside that guard's final idle check.
                providers[0].on_get = lambda: setattr(providers[0], 'on_get', lambda: state.update(armed=True))
                def idle():
                    if state['armed']:
                        state['idle_calls'] += 1
                        if state['idle_calls'] == 2:
                            old = self.visibility_objects[0]
                            if replace_object:
                                old.Document.Objects[0] = SimpleNamespace(Name=old.Name, TypeId=old.TypeId,
                                                                         Document=old.Document, ViewObject=foreign)
                            else:
                                old.ViewObject = foreign
                    return None
                self.gui.getDocument = lambda _name: SimpleNamespace(getInEdit=idle)
                self.call(); result = self.complete()
                self.assertEqual(result['state'], 'failed')
                self.assertGreaterEqual(state['idle_calls'], 2)
                self.assertEqual([item.assignments for item in providers], [[], []])
                self.assertEqual(foreign.assignments, [])
                self.assertEqual(self.captures, [])

    def test_visibility_getter_reentry_quarantines_without_writes(self):
        providers, _setup = self.visibility_fixture()
        def reenter():
            with self.assertRaises(module.PftThumbnailWorkerError) as raised:
                self.worker._restore_visibility(self.params['jobId'], self.app.documents['Task'])
            self.assertEqual(raised.exception.code, 'WORKER_VISIBILITY_REENTRANT')
        providers[0].on_get = reenter
        self.call(); result = self.complete()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual([item.assignments for item in providers], [[], []])
        self.assertEqual(self.worker.status()['state'], 'quarantined')
        self.assertEqual(self.captures, [])

    def test_import_and_status_never_open_or_query_cad(self):
        with patch.object(self.app, 'listDocuments', side_effect=AssertionError('App touched')):
            self.assertEqual(self.worker.rpc('get_thumbnail_worker_status', {})['state'], 'ready')
        self.assertEqual(self.app.opened, [])
    def test_acknowledgement_is_async_and_done_requires_close_and_verified_image(self):
        first = self.call()
        self.assertEqual(first['state'], 'queued')
        self.assertEqual(self.app.opened, [])
        self.assertEqual(self.worker.status()['state'], 'busy')
        result = self.complete()
        self.assertEqual(result['state'], 'done')
        self.assertEqual(self.app.opened, [str(self.record.staged_path)])
        self.assertEqual(self.app.closed, ['Task'])
        self.assertEqual(self.app.documents, {})
        self.assertEqual(self.cleaned, [self.record])
        self.assertEqual(base64.b64decode(result['result']['png_base64']), png())
        self.assertEqual(result['result']['sha256'], sha256(png()).hexdigest())
        self.assertFalse(result['result']['saved_cad'])
        self.assertTrue(result['result']['documents_closed'])
        self.assertEqual(self.worker.status()['state'], 'ready')
    def test_same_job_does_not_open_twice_and_changed_parameters_are_rejected(self):
        self.call(); first = self.complete()
        self.assertEqual(self.call(), first)
        self.assertEqual(len(self.app.opened), 1)
        with self.assertRaises(module.PftThumbnailWorkerError) as conflict:
            self.call(width=3)
        self.assertEqual(conflict.exception.code, 'JOB_ID_CONFLICT')
        self.assertEqual(len(self.app.opened), 1)
    def test_cancel_before_freeze_has_no_io_or_open(self):
        self.call()
        self.call('cancel_thumbnail_job')
        result = self.complete()
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(self.app.opened, [])
        self.assertEqual(self.cleaned, [])
    def test_cancel_during_freeze_cleans_without_open(self):
        self.call(); self.step()
        self.assertEqual(self.call('get_thumbnail_job')['state'], 'freezing')
        self.call('cancel_thumbnail_job')
        self.assertEqual(self.complete()['state'], 'cancelled')
        self.assertEqual(self.app.opened, [])
        self.assertEqual(self.cleaned, [self.record])
    def test_cancel_before_render_closes_owned_doc_without_capture(self):
        self.call(); self.step(); self.freezes.pop(0)(); self.step()
        self.assertEqual(self.call('get_thumbnail_job')['state'], 'rendering')
        self.call('cancel_thumbnail_job')
        self.assertEqual(self.complete()['state'], 'cancelled')
        self.assertEqual(self.captures, [])
        self.assertEqual(self.app.closed, ['Task'])
    def test_explicit_revision_mismatch_never_opens_and_cleans(self):
        self.call(expectedSha256='b' * 64)
        result = self.complete()
        self.assertEqual(result['error']['code'], 'INPUT_REVISION_CHANGED')
        self.assertEqual(self.app.opened, [])
        self.assertEqual(self.cleaned, [self.record])
        self.assertEqual(self.worker.status()['state'], 'ready')
    def test_identity_binding_busy_and_stop_are_fail_closed(self):
        for key in ('workerId', 'sessionId'):
            with self.assertRaises(module.PftThumbnailWorkerError) as mismatch:
                self.call(**{key: str(uuid4())})
            self.assertEqual(mismatch.exception.code, 'WORKER_SESSION_CHANGED')
        self.call()
        with self.assertRaises(module.PftThumbnailWorkerError) as busy:
            self.call(jobId=str(uuid4()))
        self.assertEqual(busy.exception.code, 'WORKER_BUSY')
        self.worker.stop()
        self.assertEqual(self.complete()['state'], 'cancelled')
        self.assertEqual(self.worker.status()['state'], 'stopped')
    def test_existing_user_document_is_never_adopted(self):
        self.app.add('Foreign', '/source/user.FCStd')
        self.assertEqual(self.worker.status()['state'], 'quarantined')
        with self.assertRaises(module.PftThumbnailWorkerError): self.call()
        self.assertEqual(self.app.closed, [])
        self.assertIn('Foreign', self.app.documents)
    def test_constructor_refuses_even_idle_user_document(self):
        self.app.add('Foreign', '/source/user.FCStd')
        with self.assertRaises(module.PftThumbnailWorkerError):
            module.PftThumbnailWorker(self.app, self.gui, '/private/owned', str(uuid4()), str(uuid4()), lambda *_: None)
        self.assertEqual(self.app.closed, [])
    def test_failed_open_observer_closes_only_proven_owned_document(self):
        def fail(_doc): raise RuntimeError('/secret/path and native detail')
        self.app.on_open = fail
        self.call(); result = self.complete()
        self.assertEqual(result['state'], 'failed')
        self.assertNotIn('/secret', str(result))
        self.assertEqual(self.app.closed, ['Task'])
        self.assertEqual(self.worker.status()['state'], 'ready')
    def test_unapproved_document_during_restore_is_preserved_and_worker_quarantined(self):
        self.app.on_open = lambda _doc: self.app.add('Foreign', '/source/foreign.FCStd')
        self.call(); result = self.complete()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(self.app.closed, ['Task'])
        self.assertIn('Foreign', self.app.documents)
        self.assertEqual(self.cleaned, [])
        self.assertEqual(self.worker.status()['state'], 'quarantined')
    def test_task_document_replaced_or_renamed_is_not_closed(self):
        def replaced(document): document.FileName = '/source/foreign.FCStd'
        self.app.on_open = replaced
        self.call(); self.assertEqual(self.complete()['state'], 'failed')
        self.assertEqual(self.app.closed, [])
        self.assertEqual(self.cleaned, [])
        self.assertEqual(self.worker.status()['state'], 'quarantined')
    def test_close_failure_preserves_staging_and_quarantines(self):
        self.app.on_close = lambda _: (_ for _ in ()).throw(RuntimeError('close failure'))
        self.call(); result = self.complete()
        self.assertEqual(result['error']['code'], 'WORKER_CLEANUP_UNCERTAIN')
        self.assertIsNone(result['result'])
        self.assertEqual(self.cleaned, [])
        self.assertIn('Task', self.app.documents)
        self.assertEqual(self.worker.status()['state'], 'quarantined')
    def test_close_callback_new_document_does_not_get_closed(self):
        self.app.on_close = lambda _: self.app.add('Foreign', '/source/foreign.FCStd')
        self.call(); result = self.complete()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(self.app.closed, ['Task'])
        self.assertIn('Foreign', self.app.documents)
        self.assertEqual(self.cleaned, [])
    def test_input_change_after_capture_discards_image(self):
        def verify(record):
            if self.app.closed:
                raise module.PftThumbnailWorkerError('INPUT_REVISION_CHANGED', '/private/path')
        self.worker.verify = verify
        self.call(); result = self.complete()
        self.assertEqual(result['error']['code'], 'INPUT_REVISION_CHANGED')
        self.assertIsNone(result['result'])
        self.assertEqual(self.app.closed, ['Task'])
        self.assertEqual(self.cleaned, [self.record])
    def test_unsafe_capture_bytes_are_not_published(self):
        self.worker.capture = lambda *args, **kwargs: {'png': b'not PNG', 'camera_restored': True, 'saved_cad': False}
        self.call(); result = self.complete()
        self.assertEqual(result['state'], 'failed')
        self.assertIsNone(result['result'])
        self.assertEqual(self.app.closed, ['Task'])
    def test_expired_results_are_consumed_and_never_replayed(self):
        self.call(); self.complete()
        first = dict(self.params)
        for _ in range(20):
            self.params['jobId'] = str(uuid4()); self.call(); self.complete()
        self.assertEqual(len(self.worker.jobs), 20)
        with self.assertRaises(module.PftThumbnailWorkerError) as expired:
            self.worker.rpc('render_document_thumbnail', first)
        self.assertEqual(expired.exception.code, 'JOB_RESULT_EXPIRED')
        self.assertEqual(len(self.app.opened), 21)
    def test_failed_initial_gui_handoff_is_terminal_and_never_replays(self):
        self.worker.post = lambda *_: (_ for _ in ()).throw(RuntimeError('deleted QObject'))
        result = self.call()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['error']['code'], 'WORKER_GUI_DISPATCH_FAILED')
        self.assertEqual(self.worker.status()['state'], 'quarantined')
        self.assertEqual(self.call(), result)
        self.assertEqual(self.app.opened, [])
    def test_failed_frozen_gui_handoff_is_terminal_and_preserves_input(self):
        self.call(); self.step()
        self.worker.post = lambda *_: (_ for _ in ()).throw(RuntimeError('deleted QObject'))
        self.freezes.pop(0)()
        result = self.call('get_thumbnail_job')
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(self.worker.status()['state'], 'quarantined')
        self.assertEqual(self.cleaned, [])
        self.assertEqual(self.app.opened, [])
    def test_cancel_during_preopen_verification_does_not_restore(self):
        self.worker.verify = lambda _: self.call('cancel_thumbnail_job')
        self.call()
        self.assertEqual(self.complete()['state'], 'cancelled')
        self.assertEqual(self.app.opened, [])
        self.assertEqual(self.cleaned, [self.record])
    def test_request_validation_rejects_save_scripts_bad_identity_and_dimensions(self):
        for changes in ({'save': True}, {'script': 'pass'}, {'file': 'relative.FCStd'}, {'jobId': 'not-an-id'},
                        {'width': True}, {'height': 1025}, {'expectedSha256': 'A' * 64}, {'file': '/tmp/\nfile'}):
            with self.subTest(changes=changes), self.assertRaises(module.PftThumbnailWorkerError): self.call(**changes)
        self.assertEqual(self.app.opened, [])


if __name__ == '__main__': unittest.main()
