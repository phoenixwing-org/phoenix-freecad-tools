# SPDX-License-Identifier: Apache-2.0
"""Independent bootstrap fixtures; real loopback HTTP, no FreeCAD or model IO."""
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

ADDON = Path(__file__).resolve().parents[2] / 'addon' / 'PhoenixFreeCADTools'
_saved_path = list(sys.path)
sys.path.insert(0, str(ADDON))
try:
    import pft_bridge as bridge
    import pft_development as development
    import pft_thumbnail_worker as worker_module
finally:
    sys.path[:] = _saved_path


class Signal:
    def __init__(self): self.callbacks = []
    def connect(self, callback, *_args): self.callbacks.append(callback)
    def disconnect(self, callback): self.callbacks.remove(callback)
    def emit(self, *args):
        for callback in tuple(self.callbacks): callback(*args)


class QObject:
    def __init__(self, parent=None): self.parent, self.deleted = parent, False
    def deleteLater(self): self.deleted = True


class NativeApp:
    """Native methods must never escape the test's GUI thread."""
    def __init__(self): self.observers, self.documents = [], {}
    def _gui_thread(self):
        if threading.current_thread() is not threading.main_thread():
            raise AssertionError('Native API called from HTTP thread')
    def listDocuments(self): self._gui_thread(); return dict(self.documents)
    def getActiveTransaction(self): self._gui_thread(); return None
    def addDocumentObserver(self, observer): self._gui_thread(); self.observers.append(observer)
    def removeDocumentObserver(self, observer): self._gui_thread(); self.observers.remove(observer)


def post(server, method, params=None):
    connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=3)
    try:
        connection.request('POST', '/rpc', body=json.dumps({'method': method, 'params': params or {}}),
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise AssertionError('Unexpected HTTP status: ' + str(response.status))
        return json.loads(response.read())
    finally:
        connection.close()


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pft-bootstrap-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name)
        self.profile.chmod(0o700)
        self.runtime = self.profile / 'runtime'
        self.runtime.mkdir(mode=0o700)
        self.worker_id, self.session_id, self.owner_id = str(uuid4()), str(uuid4()), str(uuid4())
        self.marker = {'owner_id': self.owner_id, 'source': str(ADDON.resolve())}
        (self.profile / '.phoenix-thumbnail-worker.json').write_text(json.dumps({
            'schema_version': 1, 'owner': 'phoenix-thumbnail-worker',
            'development_owner_id': self.owner_id, 'source': self.marker['source'],
            'worker_id': self.worker_id}), encoding='utf-8')
        (self.profile / '.phoenix-thumbnail-worker.json').chmod(0o600)
        self.receipt = self.runtime / ('thumbnail-worker-' + self.session_id + '.json')
        self.lease = self.runtime / 'thumbnail-worker.lock'
        self.app = NativeApp()
        self.application = SimpleNamespace(aboutToQuit=Signal())
        self.window = SimpleNamespace(setWindowTitle=lambda _title: None)
        self.gui = SimpleNamespace(getMainWindow=lambda: self.window)
        self.gui_callbacks = []
        qtcore = SimpleNamespace(QObject=QObject, Signal=lambda *_: Signal(),
            Slot=lambda *_: lambda function: function, Qt=SimpleNamespace(QueuedConnection=1),
            QTimer=SimpleNamespace(singleShot=lambda delay, function: self.gui_callbacks.append(function)))
        qtwidgets = SimpleNamespace(QApplication=SimpleNamespace(instance=lambda: self.application))
        pyside = SimpleNamespace(QtCore=qtcore, QtWidgets=qtwidgets)
        for context in (
            patch.dict(sys.modules, {'PySide': pyside}),
            patch.object(development, '_owned_profile', return_value=(self.profile, self.marker)),
            patch.object(bridge, 'get_status', return_value={'running': False}),
            patch.dict(os.environ, {'PFT_THUMBNAIL_WORKER_ID': self.worker_id,
                                    'PFT_THUMBNAIL_WORKER_SESSION': self.session_id}),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.addCleanup(self.stop_started_server)

    def stop_started_server(self):
        handles = getattr(self.window, '_pft_thumbnail_worker', None)
        if handles is None: return
        worker, _dispatcher, server, serving, _exiting = handles
        worker.stop()
        if serving.is_alive():
            server.shutdown()
            serving.join(3)
        server.server_close()
        del self.window._pft_thumbnail_worker

    def assert_rolled_back(self):
        self.assertFalse(self.lease.exists())
        self.assertEqual(self.app.observers, [])
        self.assertEqual(self.application.aboutToQuit.callbacks, [])
        self.assertFalse(hasattr(self.window, '_pft_thumbnail_worker'))
        self.assertEqual(self.app.documents, {})
        self.assertEqual(self.gui_callbacks, [])

    def test_success_receipt_and_real_worker_http_are_separate_from_socket_dispatch(self):
        data = worker_module.bootstrap(self.app, self.gui)
        self.assertEqual(json.loads(self.receipt.read_text()), data)
        self.assertTrue(self.lease.exists())
        self.assertEqual(len(self.app.observers), 1)
        worker, _dispatcher, server, _serving, exiting = self.window._pft_thumbnail_worker
        self.assertEqual(post(server, 'ping'), {'ok': True, 'data': {'pong': True}})
        status = post(server, 'get_thumbnail_worker_status')
        self.assertTrue(status['ok'])
        self.assertEqual(status['data']['state'], 'ready')
        self.assertEqual(status['data']['session_id'], self.session_id)
        self.assertTrue(callable(server.process_request))
        self.assertEqual(post(server, 'get_status')['data']['port'], server.server_address[1])
        self.assertEqual(post(server, 'execute_script')['error']['code'], 'INVALID_REQUEST')
        exiting()
        self.assertEqual(worker.status()['state'], 'stopped')
        self.assertFalse(self.lease.exists())

    def test_existing_receipt_is_preserved_and_started_listener_is_closed(self):
        self.receipt.write_text('unrelated receipt')
        servers = []
        original = bridge._PftBridgeHttpServer
        def create(*args, **kwargs):
            server = original(*args, **kwargs); servers.append(server); return server
        with patch.object(bridge, '_PftBridgeHttpServer', side_effect=create):
            with self.assertRaises(FileExistsError): worker_module.bootstrap(self.app, self.gui)
        self.assert_rolled_back()
        self.assertEqual(self.receipt.read_text(), 'unrelated receipt')
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].socket.fileno(), -1)

    def test_receipt_fsync_failure_removes_only_created_receipt_and_rolls_back(self):
        calls = [0]
        original = os.fsync
        def fsync(fd):
            calls[0] += 1
            if calls[0] == 2: raise OSError('independent receipt fault')
            return original(fd)
        with patch.object(os, 'fsync', side_effect=fsync):
            with self.assertRaises(OSError): worker_module.bootstrap(self.app, self.gui)
        self.assert_rolled_back()
        self.assertFalse(self.receipt.exists())

    def test_bind_failure_removes_observer_and_lease(self):
        with patch.object(bridge, '_PftBridgeHttpServer', side_effect=OSError('synthetic bind failure')):
            with self.assertRaises(OSError): worker_module.bootstrap(self.app, self.gui)
        self.assert_rolled_back()
        self.assertFalse(self.receipt.exists())

    def test_thread_start_failure_closes_bound_listener_without_shutdown_deadlock(self):
        servers = []
        original = bridge._PftBridgeHttpServer
        def create(*args, **kwargs):
            server = original(*args, **kwargs); servers.append(server); return server
        with patch.object(bridge, '_PftBridgeHttpServer', side_effect=create), \
                patch.object(threading.Thread, 'start', side_effect=RuntimeError('synthetic start failure')):
            with self.assertRaises(RuntimeError): worker_module.bootstrap(self.app, self.gui)
        self.assert_rolled_back()
        self.assertFalse(self.receipt.exists())
        self.assertEqual(servers[0].socket.fileno(), -1)

    def test_window_setup_failure_cleans_prepared_listener(self):
        self.window.setWindowTitle = lambda _: (_ for _ in ()).throw(RuntimeError('synthetic window failure'))
        with self.assertRaises(RuntimeError): worker_module.bootstrap(self.app, self.gui)
        self.assert_rolled_back()
        self.assertFalse(self.receipt.exists())

    def test_real_http_cannot_accept_jobs_before_readiness_is_committed(self):
        calls = [0]
        replies = []
        original = os.fsync
        def fsync(fd):
            calls[0] += 1
            if calls[0] == 2:
                server = self.window._pft_thumbnail_worker[2]
                replies.append(post(server, 'render_document_thumbnail', {
                    'workerId': self.worker_id, 'sessionId': self.session_id, 'jobId': str(uuid4()),
                    'file': '/synthetic/never-opened.FCStd', 'expectedSha256': 'a' * 64,
                }))
                raise OSError('readiness publication intentionally failed')
            return original(fd)
        with patch.object(os, 'fsync', side_effect=fsync):
            with self.assertRaises(OSError): worker_module.bootstrap(self.app, self.gui)
        self.assertEqual(replies[0]['error']['code'], 'WORKER_STARTING_OR_STOPPING')
        self.assert_rolled_back()
        self.assertFalse(self.receipt.exists())

    def test_ordinary_bridge_real_http_uses_default_payload_handler(self):
        server = bridge._PftBridgeHttpServer(('127.0.0.1', 0), bridge._PftRequestHandler)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        try:
            self.assertEqual(post(server, 'ping'), {'ok': True, 'data': {'pong': True}})
            self.assertFalse(hasattr(server, 'pft_process_payload'))
        finally:
            server.shutdown(); thread.join(3); server.server_close()


if __name__ == '__main__': unittest.main()
