# SPDX-License-Identifier: Apache-2.0
"""Domain lifecycle and same-byte publication with independent native stubs."""
import base64
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import threading
import types
import sys
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / 'addon/PhoenixFreeCADTools'


def load(name, path=None):
    spec = importlib.util.spec_from_file_location(name, path or ADDON / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class BridgeError(RuntimeError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.export = load('neutral_export_domain', ADDON / 'pft_drawing_export.py')
        self.export.begin_approval_lifecycle()
        self.params = {'docName': 'Sample', 'pageName': 'Page', 'format': 'pdf', 'requestId': 'export-one'}
        temporary = tempfile.TemporaryDirectory(prefix='pft-export-domain-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.document = types.SimpleNamespace(Name='Sample', Label='Independent sample', FileName='')
        self.page = object()
        self.document.getObject = lambda name: self.page if name == 'Page' else None
        self.app = types.SimpleNamespace(ActiveDocument=self.document,
            getDocument=lambda name: self.document if name == 'Sample' else None,
            getHomePath=lambda: '/neutral/application')
        self.gui = object()
        self.events, self.windows = [], []
        self.stale = False
        self.on_native = None
        self.on_verify = None
        self.data = b'%PDF-neutral-frozen-export-domain'
        self.context = {'public_summary': {'document': 'Sample', 'page': 'Page', 'source': 'Source',
            'view_count': 3, 'dimension_count': 0, 'page_open': True, 'paper_mm': [297, 210]}}
        owner = self

        def snapshot(*args):
            owner.events.append('snapshot')
            return deepcopy(owner.context)
        def verify(*args, **kwargs):
            owner.events.append('verify')
            if owner.on_verify is not None:
                callback, owner.on_verify = owner.on_verify, None
                callback()
            if owner.stale: raise owner.export.PftDrawingExportError('DRAWING_EXPORT_DOCUMENT_CHANGED')
        def capture(_app, _gui, context, _controller, *, guard):
            guard(); owner.events.append('capture')
            if owner.on_native: owner.on_native()
            try:
                guard()
            except Exception as error:
                error.capture_state = 'unknown'
                raise
            return {'bytes': owner.data, 'post_context': context, 'capture_state': 'captured', 'cleanup_state': 'complete'}
        def open_page(_app, _gui, context, _controller, *, guard):
            guard(); owner.events.append('open-page')
            return {**context, 'pending_open': True}
        def bind_scene(_app, _gui, context, _controller, *, guard):
            guard(); owner.events.append('bind-scene')
            return {**context, 'pending_open': False}
        self.profile = types.SimpleNamespace(ERROR_CODES=frozenset(), snapshot=snapshot, verify=verify,
            open_page=open_page, bind_open_scene=bind_scene, capture_pdf=capture)
        # Most lifecycle tests use an immediate scheduler. Dedicated tests below
        # hold the real queued boundary to exercise cancellation and staleness.
        qt = types.SimpleNamespace(QTimer=types.SimpleNamespace(singleShot=lambda delay, callback: callback()))
        qt_patch = patch.dict(sys.modules, {'PySide': types.SimpleNamespace(QtCore=qt)})
        qt_patch.start(); self.addCleanup(qt_patch.stop)
        self.controller = types.SimpleNamespace(_ensure_main_thread=lambda: self.events.append('main-thread'))

        class Handle:
            def __init__(self): self.presented, self.closed = 0, 0
            def present(self): self.presented += 1
            def close(self): self.closed += 1
        def open_view(gui, summary, callbacks):
            handle = Handle(); handle.summary, handle.callbacks = summary, callbacks
            owner.windows.append(handle)
            return handle
        self.publisher = load('pft_file_publish')
        self.pdf = load('pft_pdf_contract')
        modules = {'pft_workbench_controller': self.controller, 'pft_drawing_export_profile': self.profile,
            'pft_pdf_qprocess': types.SimpleNamespace(resolve_pdf_runtime=lambda home: {'program': home, 'helper': 'fixed'}),
            'pft_pdf_contract': self.pdf, 'pft_file_publish': self.publisher,
            'pft_drawing_export_views': types.SimpleNamespace(open_drawing_export_approval=open_view)}
        stub = patch.object(self.export, '_load', side_effect=lambda name: modules[name])
        stub.start(); self.addCleanup(stub.stop)

    def perform(self, method, params=None):
        return self.export.perform(method, self.params if params is None else params, self.app, self.gui, BridgeError)

    def prepare(self): return self.perform('prepare_drawing_export')
    def status(self, token):
        return self.export.perform('get_drawing_export_status', {'token': token}, None, None, BridgeError)
    def request(self, token):
        return self.perform('request_drawing_export_approval', {'token': token})

    def opened(self):
        prepared = self.prepare()
        self.assertEqual(prepared['state'], 'prepared')
        requested = self.request(prepared['token'])
        self.assertEqual(requested['state'], 'awaiting_capture')
        return prepared['token'], self.windows[-1].callbacks

    def rendered(self):
        token, callbacks = self.opened()
        bundles, errors = [], []
        callbacks['capture'](bundles.append, errors.append)
        self.assertEqual(errors, [])
        self.assertEqual(bundles[0]['bytes'], self.data)
        self.assertEqual(self.status(token)['state'], 'rendering')
        reply = self.reply()
        result = callbacks['rendered'](reply)
        self.assertEqual(result['state'], 'preview_ready')
        return token, callbacks

    def reply(self):
        return {'ok': True, 'backend': 'coregraphics', 'input_sha256': hashlib.sha256(self.data).hexdigest(),
            'byte_count': len(self.data), 'page_count': 1, 'media_box_points': [0, 0, 842, 595],
            'rotation_degrees': 0, 'user_unit': 1, 'width': 1000, 'height': 1000, 'stride': 4000,
            'pixel_format': 'rgba8888-premultiplied',
            'pixels_base64': base64.b64encode(b'\xff' * 4_000_000).decode('ascii')}

    def target_ready(self):
        token, callbacks = self.rendered()
        self.target = self.root / 'drawing.pdf'
        result = callbacks['choose_target'](str(self.target))
        self.assertEqual(result['state'], 'target_ready')
        return token, callbacks

    def test_claim_before_gui_is_atomic_same_id_replay_does_not_import_or_capture(self):
        results, failures = [], []
        def claim():
            try: results.append(self.export.claim_prepare(self.params, BridgeError))
            except Exception as error: failures.append(error)
        threads = [threading.Thread(target=claim) for _ in range(8)]
        with patch.object(self.export, '_load', side_effect=AssertionError('must not import')):
            for thread in threads: thread.start()
            for thread in threads: thread.join()
        self.assertEqual(failures, [])
        self.assertEqual(sum(fresh for fresh, _ in results), 1)
        self.assertEqual(len({value['token'] for _, value in results}), 1)
        self.assertTrue(all(value['state'] == 'preparing' for _, value in results))
        self.assertEqual(self.events, [])

    def test_prepare_replay_is_read_only_and_conflicting_id_is_rejected(self):
        prepared = self.prepare()
        count = len(self.events)
        replay = self.prepare()
        self.assertEqual(replay, prepared)
        self.assertEqual(len(self.events), count)
        with self.assertRaises(BridgeError) as error:
            self.export.claim_prepare({**self.params, 'pageName': 'Other'}, BridgeError)
        self.assertEqual(error.exception.code, 'DRAWING_EXPORT_REQUEST_CONFLICT')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_status_and_capability_are_pure_memory_and_detached(self):
        prepared = self.prepare()
        with patch.object(self.export, '_load', side_effect=AssertionError('native import')), \
             patch.object(Path, 'stat', side_effect=AssertionError('filesystem')):
            result = self.status(prepared['token'])
        result['summary']['source'] = 'changed'
        self.assertEqual(self.status(prepared['token'])['summary']['source'], 'Source')
        self.assertFalse(result['saves_document'])
        self.assertNotIn('pdf_bytes', result)
        self.assertNotIn('context', result)

    def unknown_cleanup(self):
        error = RuntimeError('private native wrapper diagnostic')
        error.cleanup_state, error.capture_state = 'unknown', 'not_captured'
        return error

    def test_prepare_preserves_unknown_owned_native_cleanup_without_claiming_capture(self):
        with patch.object(self.profile, 'snapshot', side_effect=self.unknown_cleanup()):
            result = self.prepare()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['cleanup_state'], 'unknown')
        self.assertEqual(result['capture_state'], 'not_captured')
        self.assertEqual(result['cad_protection_state'], 'not_checked')
        self.assertNotIn('private native', str(result))
        self.assertEqual(self.windows, [])

    def test_pre_capture_binding_preserves_unknown_cleanup_without_false_CAD_change(self):
        prepared = self.prepare()
        with patch.object(self.profile, 'verify', side_effect=self.unknown_cleanup()):
            result = self.request(prepared['token'])
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['cleanup_state'], 'unknown')
        self.assertEqual(result['capture_state'], 'not_captured')
        self.assertEqual(result['cad_protection_state'], 'not_checked')
        self.assertFalse(result['native_capture_confirmation'])
        self.assertEqual(self.windows, [])

    def test_late_binding_cleanup_unknown_retains_real_capture_receipt(self):
        token, callbacks = self.rendered()
        with patch.object(self.profile, 'verify', side_effect=self.unknown_cleanup()):
            result = callbacks['choose_target'](str(self.root / 'drawing.pdf'))
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['cleanup_state'], 'unknown')
        self.assertEqual(result['capture_state'], 'captured')
        self.assertEqual(result['cad_protection_state'], 'unverified')
        self.assertEqual(result['sha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(result['byte_count'], len(self.data))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_params_never_claim_or_read_native(self):
        for params in ({**self.params, 'path': '/neutral'}, {**self.params, 'confirm': True},
                       {**self.params, 'format': 'svg'}, {**self.params, 'requestId': ''},
                       {**self.params, 'docName': 'bad\n'}, {**self.params, 'overwrite': False}):
            with self.subTest(keys=list(params)), self.assertRaises(BridgeError):
                self.export.claim_prepare(params, BridgeError)
        self.assertEqual(self.export._requests, {})
        self.assertEqual(self.events, [])

    def test_dedup_tombstone_survives_record_retirement_and_capacity(self):
        self.export.MAX_RECORDS = 1
        first = self.prepare()
        self.export.cancel_pending_approvals(); self.export.begin_approval_lifecycle()
        self.params = {**self.params, 'requestId': 'export-two'}
        self.prepare()
        with self.assertRaises(BridgeError) as error:
            self.export.claim_prepare({**self.params, 'requestId': 'export-one'}, BridgeError)
        self.assertEqual(error.exception.code, 'DRAWING_EXPORT_REQUEST_RETIRED')
        self.assertNotIn(first['token'], self.export._records)
        self.assertEqual(self.events.count('snapshot'), 2)

    def test_known_queue_failure_retires_claim_unknown_timeout_keeps_original(self):
        _, first = self.export.claim_prepare(self.params, BridgeError)
        self.export.dispatch_error(self.params['requestId'], BridgeError('GUI_TIMEOUT', 'neutral'))
        self.assertEqual(self.status(first['token'])['state'], 'preparing')
        self.export.dispatch_error(self.params['requestId'], BridgeError('GUI_QUEUE_TIMEOUT', 'neutral'))
        self.assertEqual(self.status(first['token'])['state'], 'cancelled')
        self.assertEqual(self.prepare()['state'], 'cancelled')
        self.assertEqual(self.events, [])

    def test_request_opens_once_replay_presents_no_capture(self):
        token, _ = self.opened()
        self.request(token)
        self.assertEqual(len(self.windows), 1)
        self.assertEqual(self.windows[0].presented, 1)
        self.assertNotIn('capture', self.events)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_second_window_is_rejected_until_first_is_cancelled(self):
        token, callbacks = self.opened()
        self.params = {**self.params, 'requestId': 'export-two'}
        second = self.prepare()
        with self.assertRaises(BridgeError) as error: self.request(second['token'])
        self.assertEqual(error.exception.code, 'DRAWING_EXPORT_BUSY')
        callbacks['cancel']()
        self.assertEqual(self.status(token)['state'], 'cancelled')
        self.assertEqual(self.request(second['token'])['state'], 'awaiting_capture')

    def test_capture_requires_unforgeable_live_view_grant(self):
        prepared = self.prepare()
        record = self.export._records[prepared['token']]
        errors = []
        with self.assertRaises(self.export.PftDrawingExportError):
            self.export._guard(record, object(), {'prepared'})
        self.export._capture(record, object(), self.app, self.gui, lambda _: self.fail('capture'), errors.append)
        self.assertEqual(errors, [])
        self.assertEqual(self.status(prepared['token'])['state'], 'prepared')
        self.assertNotIn('capture', self.events)

    def test_duplicate_native_callbacks_cannot_retire_or_corrupt_live_step(self):
        token, callbacks = self.rendered()
        baseline = self.status(token)
        self.assertEqual(callbacks['rendered']({}), baseline)
        self.assertEqual(callbacks['failed']('PDF_PROCESS_FAILED'), baseline)
        callbacks['capture'](lambda _: self.fail('duplicate capture'), lambda _: self.fail('duplicate failure'))
        self.assertEqual(self.status(token), baseline)
        self.target = self.root / 'drawing.pdf'
        callbacks['choose_target'](str(self.target))
        original = self.publisher.write_file_export
        reentrant = []
        def writer(*args, **kwargs):
            reentrant.append(callbacks['publish']())
            return original(*args, **kwargs)
        with patch.object(self.publisher, 'write_file_export', side_effect=writer):
            self.assertEqual(callbacks['publish']()['state'], 'published')
        self.assertEqual([value['state'] for value in reentrant], ['publishing'])
        self.assertEqual(self.target.read_bytes(), self.data)

    def test_ready_callback_exception_does_not_claim_owned_renderer_has_exited(self):
        token, callbacks = self.opened()
        def started_then_raised(_): raise RuntimeError('neutral-view-after-start')
        callbacks['capture'](started_then_raised, lambda _: self.fail('early child retirement'))
        self.assertEqual(self.status(token)['state'], 'cancelling')
        self.assertIn('pdf_bytes', self.export._records[token])
        self.assertEqual(self.windows[-1].closed, 1)
        terminal = callbacks['failed']('PDF_CANCELLED')
        self.assertEqual(terminal['state'], 'failed')
        self.assertEqual(terminal['error']['code'], 'DRAWING_EXPORT_UI_UNAVAILABLE')

    def test_verify_callback_reentry_cannot_steal_capture_or_renderer_step(self):
        token, callbacks = self.opened()
        inner, outer, errors = [], [], []
        self.on_verify = lambda: callbacks['capture'](inner.append, errors.append)
        callbacks['capture'](outer.append, errors.append)
        self.assertEqual((len(outer), len(inner), len(errors)), (1, 0, 0))
        self.assertEqual(self.events.count('capture'), 1)
        reply = self.reply()
        inner_status = []
        self.on_verify = lambda: inner_status.append(callbacks['rendered'](reply))
        result = callbacks['rendered'](reply)
        self.assertEqual(result['state'], 'preview_ready')
        self.assertEqual([value['state'] for value in inner_status], ['rendering'])
        self.assertIn('pdf_bytes', self.export._records[token])

    def test_verify_reentry_cannot_replace_target_or_publish_twice(self):
        token, callbacks = self.rendered()
        self.target = self.root / 'drawing.pdf'
        self.on_verify = lambda: callbacks['choose_target'](str(self.root / 'reentrant.pdf'))
        self.assertEqual(callbacks['choose_target'](str(self.target))['target'], str(self.target))
        inner_status = []
        self.on_verify = lambda: inner_status.append(callbacks['publish']())
        self.assertEqual(callbacks['publish']()['state'], 'published')
        self.assertEqual([value['state'] for value in inner_status], ['target_ready'])
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertFalse((self.root / 'reentrant.pdf').exists())
        self.assertEqual(self.status(token)['state'], 'published')

    def test_late_duplicate_renderer_error_during_validation_does_not_retire_preview(self):
        token, callbacks = self.opened()
        callbacks['capture'](lambda _: None, lambda _: self.fail('capture'))
        self.on_verify = lambda: callbacks['failed']('PDF_PROCESS_FAILED')
        self.assertEqual(callbacks['rendered'](self.reply())['state'], 'preview_ready')
        self.assertIn('pdf_bytes', self.export._records[token])

    def test_unclassified_writer_exception_after_file_write_remains_unknown(self):
        token, callbacks = self.target_ready()
        def raw_error(*args, **kwargs):
            self.target.write_bytes(self.data)
            raise RuntimeError('private-untrusted-after-write')
        with patch.object(self.publisher, 'write_file_export', side_effect=raw_error) as writer:
            result = callbacks['publish']()
            self.assertEqual(callbacks['publish'](), result)
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(result['publication_state'], 'unknown')
        self.assertNotIn('private-untrusted', str(result))
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertEqual(self.status(token), result)

    def test_file_is_published_once_from_exact_previewed_bytes(self):
        token, callbacks = self.target_ready()
        self.assertFalse(self.target.exists())
        result = callbacks['publish']()
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['publication_state'], 'published')
        self.assertEqual(result['cad_protection_state'], 'verified')
        self.assertTrue(result['native_capture_confirmation'])
        self.assertTrue(result['native_publication_confirmation'])
        self.assertEqual(self.target.read_bytes(), self.data)
        fingerprint = self.target.stat()
        self.assertEqual(callbacks['publish'](), result)
        self.assertEqual(self.target.stat(), fingerprint)
        self.assertEqual(self.events.count('capture'), 1)
        self.assertEqual(list(self.root.glob('.pft-*')), [])
        self.assertNotIn('pdf_bytes', self.export._records[token])

    def test_published_status_is_historical_no_target_or_cad_read(self):
        token, callbacks = self.target_ready(); callbacks['publish'](); self.target.unlink()
        self.app.getDocument = lambda _: self.fail('status must not touch CAD')
        with patch.object(self.export, '_load', side_effect=AssertionError('imports')), \
             patch.object(Path, 'stat', side_effect=AssertionError('target')):
            self.assertEqual(self.status(token)['publication_state'], 'published')

    def test_existing_target_change_blocks_publication_and_preserves_other_bytes(self):
        token, callbacks = self.target_ready()
        self.target.write_bytes(b'foreign-created-file')
        result = callbacks['publish']()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['error']['code'], 'EXPORT_TARGET_CHANGED')
        self.assertEqual(result['publication_state'], 'not_published')
        self.assertEqual(self.target.read_bytes(), b'foreign-created-file')
        self.assertEqual(self.status(token), result)

    def test_existing_target_overwrite_requires_final_native_step(self):
        token, callbacks = self.rendered()
        self.target = self.root / 'drawing.pdf'; self.target.write_bytes(b'old')
        selected = callbacks['choose_target'](str(self.target))
        self.assertTrue(selected['target_exists'])
        self.assertEqual(self.target.read_bytes(), b'old')
        self.assertEqual(callbacks['publish']()['state'], 'published')
        self.assertEqual(self.target.read_bytes(), self.data)

    def test_stale_source_prevents_capture_and_prevents_final_publish(self):
        token, callbacks = self.opened()
        self.stale = True
        errors = []; callbacks['capture'](lambda _: self.fail('capture'), errors.append)
        self.assertEqual(self.status(token)['state'], 'failed')
        self.assertNotIn('capture', self.events)
        self.setUp()
        token, callbacks = self.target_ready(); self.stale = True
        result = callbacks['publish']()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['cad_protection_state'], 'unverified')
        self.assertFalse(self.target.exists())

    def test_late_stale_checks_do_not_keep_an_earlier_verified_cad_claim(self):
        token, callbacks = self.opened()
        callbacks['capture'](lambda _: None, lambda _: self.fail('capture'))
        self.stale = True
        result = callbacks['rendered'](self.reply())
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['capture_state'], 'captured')
        self.assertEqual(result['cad_protection_state'], 'unverified')
        self.setUp()
        token, callbacks = self.rendered()
        self.stale = True
        result = callbacks['choose_target'](str(self.root / 'drawing.pdf'))
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['cad_protection_state'], 'unverified')
        self.assertEqual(result['publication_state'], 'not_published')

    def test_cancel_during_rendering_waits_for_owned_transport_terminal(self):
        token, callbacks = self.opened()
        bundles = []; callbacks['capture'](bundles.append, lambda _: self.fail('capture'))
        self.assertEqual(callbacks['cancel']()['state'], 'cancelling')
        self.assertIn('pdf_bytes', self.export._records[token])
        result = callbacks['failed']('PDF_CANCELLED')
        self.assertEqual(result['state'], 'cancelled')
        self.assertNotIn('pdf_bytes', self.export._records[token])

    def test_expiry_during_rendering_waits_then_expires_without_file_write(self):
        token, callbacks = self.opened()
        callbacks['capture'](lambda _: None, lambda _: self.fail('capture'))
        self.export._records[token]['deadline'] = 0
        self.assertEqual(self.status(token)['state'], 'cancelling')
        self.assertEqual(callbacks['failed']('PDF_CANCELLED')['state'], 'expired')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_stop_during_native_capture_retains_unknown_capture_and_never_renders(self):
        token, callbacks = self.opened()
        self.on_native = self.export.cancel_pending_approvals
        errors = []; callbacks['capture'](lambda _: self.fail('render'), errors.append)
        result = self.status(token)
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(result['capture_state'], 'unknown')
        self.assertEqual(result['cad_protection_state'], 'unverified')
        self.assertEqual(result['publication_state'], 'not_published')

    def test_unclassified_capture_error_cannot_claim_staging_cleanup_complete(self):
        token, callbacks = self.opened()
        marker = self.root / 'neutral-stage-evidence'
        def interrupted_capture():
            marker.write_bytes(b'neutral-owned-stage')
            raise OSError('private-untrusted-cleanup-detail')
        self.on_native = interrupted_capture
        callbacks['capture'](lambda _: self.fail('render'), lambda _: None)
        result = self.status(token)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['capture_state'], 'unknown')
        self.assertEqual(result['cleanup_state'], 'unknown')
        self.assertEqual(result['cad_protection_state'], 'unverified')
        self.assertNotIn('private-untrusted', str(result))
        self.assertEqual(marker.read_bytes(), b'neutral-owned-stage')

    def test_native_open_failure_retains_capture_and_cleanup_outcomes(self):
        self.context['public_summary']['page_open'] = False
        def failed_open(*args, **kwargs):
            error = self.export.PftDrawingExportError('DRAWING_EXPORT_FAILED')
            error.capture_state, error.cleanup_state = 'unknown', 'unknown'
            raise error
        self.profile.open_page = failed_open
        token, callbacks = self.opened()
        errors = []
        callbacks['capture'](lambda _: self.fail('render'), errors.append)
        result = self.status(token)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['capture_state'], 'unknown')
        self.assertEqual(result['cleanup_state'], 'unknown')
        self.assertEqual(result['cad_protection_state'], 'unverified')
        self.assertEqual(result['publication_state'], 'not_published')
        self.assertNotIn('capture', self.events)

    def test_cancel_queued_page_open_waits_for_its_own_callback_without_capture(self):
        self.context['public_summary']['page_open'] = False
        self.profile.open_page = lambda _app, _gui, context, _controller, **kwargs: {
            **context, 'pending_open': True}
        token, callbacks = self.opened()
        queued, errors = [], []
        qt = types.SimpleNamespace(QTimer=types.SimpleNamespace(singleShot=lambda delay, callback: queued.append(callback)))
        with patch.dict(sys.modules, {'PySide': types.SimpleNamespace(QtCore=qt)}):
            callbacks['capture'](lambda _: self.fail('render'), errors.append)
        self.assertEqual(len(queued), 1)
        self.assertEqual(callbacks['cancel']()['state'], 'cancelling')
        self.assertEqual(callbacks['failed']('PDF_CANCELLED')['state'], 'cancelling')
        queued[0]()
        self.assertEqual(self.status(token)['state'], 'cancelled')
        self.assertEqual(self.status(token)['capture_state'], 'not_captured')
        self.assertNotIn('capture', self.events)

    def test_already_open_page_refresh_is_queued_and_cannot_be_skipped(self):
        token, callbacks = self.opened()
        queued, bundles, errors = [], [], []
        qt = types.SimpleNamespace(QTimer=types.SimpleNamespace(singleShot=lambda delay, callback: queued.append(callback)))
        with patch.dict(sys.modules, {'PySide': types.SimpleNamespace(QtCore=qt)}):
            callbacks['capture'](bundles.append, errors.append)
        self.assertEqual(self.events.count('open-page'), 1)
        self.assertNotIn('bind-scene', self.events)
        self.assertNotIn('capture', self.events)
        self.assertEqual(self.status(token)['state'], 'capturing')
        self.assertEqual(len(queued), 1)
        queued[0]()
        self.assertEqual(self.events.count('bind-scene'), 1)
        self.assertEqual(self.events.count('capture'), 1)
        self.assertLess(self.events.index('open-page'), self.events.index('bind-scene'))
        self.assertLess(self.events.index('bind-scene'), self.events.index('capture'))
        self.assertEqual(errors, []); self.assertEqual(len(bundles), 1)

    def test_already_open_cancel_before_queued_bind_never_enters_render_or_export(self):
        token, callbacks = self.opened()
        queued, errors = [], []
        qt = types.SimpleNamespace(QTimer=types.SimpleNamespace(singleShot=lambda delay, callback: queued.append(callback)))
        with patch.dict(sys.modules, {'PySide': types.SimpleNamespace(QtCore=qt)}):
            callbacks['capture'](lambda _: self.fail('render'), errors.append)
        callbacks['cancel'](); queued[0]()
        self.assertEqual(self.status(token)['state'], 'cancelled')
        self.assertEqual(self.status(token)['capture_state'], 'not_captured')
        self.assertNotIn('bind-scene', self.events); self.assertNotIn('capture', self.events)

    def test_whole_paper_preparation_failure_is_not_a_native_PDF_entry(self):
        token, callbacks = self.opened()
        def failed_bind(*args, **kwargs):
            error = self.export.PftDrawingExportError('DRAWING_EXPORT_CAPTURE_UNVERIFIED')
            error.cleanup_state = 'unknown'
            error.cad_protection_state = 'unverified'
            error.capture_state = 'not_captured'
            raise error
        self.profile.bind_open_scene = failed_bind
        callbacks['capture'](lambda _: self.fail('renderer'), lambda _: None)
        result = self.status(token)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['capture_state'], 'not_captured')
        self.assertEqual(result['cleanup_state'], 'unknown')
        self.assertEqual(result['cad_protection_state'], 'unverified')
        self.assertNotIn('capture', self.events)

    def test_failed_native_page_open_can_mark_cad_unverified_without_pdf_capture(self):
        self.context['public_summary']['page_open'] = False
        def failed_open(*args, **kwargs):
            error = self.export.PftDrawingExportError('DRAWING_EXPORT_FAILED')
            error.capture_state = 'not_captured'
            error.cad_protection_state = 'unverified'
            raise error
        self.profile.open_page = failed_open
        token, callbacks = self.opened()
        callbacks['capture'](lambda _: self.fail('render'), lambda _: None)
        result = self.status(token)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['capture_state'], 'not_captured')
        self.assertEqual(result['cad_protection_state'], 'unverified')
        self.assertEqual(result['publication_state'], 'not_published')
        self.assertNotIn('capture', self.events)

    def test_stop_and_restart_cannot_resurrect_old_native_callbacks(self):
        token, callbacks = self.target_ready()
        self.export.cancel_pending_approvals(); self.export.begin_approval_lifecycle()
        self.assertEqual(callbacks['publish']()['state'], 'cancelled')
        self.assertFalse(self.target.exists())
        self.assertEqual(self.prepare()['token'], token)
        self.assertEqual(self.prepare()['state'], 'cancelled')

    def test_filename_or_document_instance_changes_are_rejected(self):
        token, callbacks = self.target_ready()
        self.document.FileName = str(self.root / 'changed.FCStd')
        self.assertEqual(callbacks['publish']()['state'], 'failed')
        self.assertFalse(self.target.exists())
        self.assertEqual(self.status(token)['error']['code'], 'DRAWING_EXPORT_DOCUMENT_CHANGED')

    def test_native_exception_text_and_unknown_code_are_not_exposed(self):
        def broken(*args): raise RuntimeError('private-untrusted-diagnostic')
        self.profile.snapshot = broken
        result = self.prepare()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['error']['code'], 'DRAWING_EXPORT_FAILED')
        self.assertNotIn('private-untrusted', str(result))

    def test_unrecognized_public_summary_cannot_leak_paths_or_raw_native_objects(self):
        self.context['public_summary']['path'] = '/neutral/private-not-for-status'
        result = self.prepare()
        self.assertEqual(result['state'], 'failed')
        self.assertNotIn('/neutral/private', str(result))

    def test_publication_unknown_is_terminal_and_never_retried(self):
        token, callbacks = self.target_ready()
        def unknown(*args, **kwargs):
            raise self.publisher.PftFilePublishError('EXPORT_FAILED', 'fixed', publication_state='unknown', cleanup_state='unknown')
        with patch.object(self.publisher, 'write_file_export', side_effect=unknown) as writer:
            result = callbacks['publish']()
            self.assertEqual(result['state'], 'unknown')
            self.assertEqual(result['cleanup_state'], 'unknown')
            self.assertEqual(callbacks['publish'](), result)
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(self.status(token), result)

    def test_published_cleanup_failure_keeps_publication_separate_from_cleanup(self):
        token, callbacks = self.target_ready()
        def uncertain(*args, **kwargs):
            self.target.write_bytes(self.data)
            raise self.publisher.PftFilePublishError('EXPORT_CLEANUP_FAILED', 'fixed', publication_state='published', cleanup_state='unknown')
        with patch.object(self.publisher, 'write_file_export', side_effect=uncertain):
            result = callbacks['publish']()
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['publication_state'], 'published')
        self.assertEqual(result['cleanup_state'], 'unknown')
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertEqual(self.status(token), result)

    def test_cad_postcheck_failure_does_not_erase_already_published_file_outcome(self):
        token, callbacks = self.target_ready()
        original = self.publisher.write_file_export
        def changed(*args, **kwargs):
            result = original(*args, **kwargs); self.stale = True; return result
        with patch.object(self.publisher, 'write_file_export', side_effect=changed):
            result = callbacks['publish']()
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['cad_protection_state'], 'unverified')
        self.assertEqual(result['error']['code'], 'DRAWING_EXPORT_POST_PUBLISH_CHANGED')
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertEqual(self.status(token), result)


if __name__ == '__main__':
    unittest.main()
