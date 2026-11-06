# SPDX-License-Identifier: Apache-2.0
"""Owned, serial thumbnail jobs; ordinary FreeCAD bridges never enable this service.

The caller must explicitly authorize trusted native input. FCStd restore is not
an inert read and this process is not a sandbox for untrusted FreeCAD archives.
"""
import base64
from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import threading
import unicodedata
from uuid import UUID

METHODS = frozenset(('get_thumbnail_worker_status', 'render_document_thumbnail',
                     'get_thumbnail_job', 'cancel_thumbnail_job'))
_STATES = frozenset(('done', 'failed', 'cancelled'))
_PROFILE = 'native-isometric-white-v1'


class PftThumbnailWorkerError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _fail(code, message):
    raise PftThumbnailWorkerError(code, message)


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def validate_request(method, params):
    if method not in METHODS or type(params) is not dict:
        _fail('INVALID_REQUEST', 'Unsupported thumbnail worker request.')
    expected = set() if method == 'get_thumbnail_worker_status' else {'workerId', 'sessionId', 'jobId'}
    if method == 'render_document_thumbnail':
        expected |= {'file', 'expectedSha256', 'width', 'height'}
    required = expected - {'width', 'height'}
    if set(params) - expected or required - set(params):
        _fail('INVALID_PARAMS', 'Thumbnail worker parameters are incomplete or unsupported.')
    for key in ('workerId', 'sessionId', 'jobId'):
        if key in params and not _uuid(params[key]):
            _fail('INVALID_PARAMS', 'Worker, session and job identities must be canonical UUIDs.')
    result = dict(params)
    if method == 'render_document_thumbnail':
        filename, digest = result['file'], result['expectedSha256']
        if (not isinstance(filename, str) or not 0 < len(filename) <= 4096 or not os.path.isabs(filename)
                or filename != filename.strip() or any(unicodedata.category(char) in ('Cc', 'Cf', 'Cs') for char in filename)
                or not isinstance(digest, str) or re.fullmatch('[a-f0-9]{64}', digest) is None):
            _fail('INVALID_PARAMS', 'An explicit absolute input path and SHA256 are required.')
        result.setdefault('width', 512)
        result.setdefault('height', 512)
        if any(type(result[key]) is not int or not 1 <= result[key] <= 1024 for key in ('width', 'height')):
            _fail('INVALID_PARAMS', 'Thumbnail dimensions must be integers from 1 to 1024.')
        if result['width'] * result['height'] > 1024 * 1024:
            _fail('INVALID_PARAMS', 'Thumbnail pixel budget exceeded.')
    return result


class PftThumbnailWorker:
    """The HTTP thread touches only this ledger. App/Gui calls use post()'s GUI thread."""
    def __init__(self, app, gui, staging_root, worker_id, session_id, post, *,
                 freeze=None, verify=None, cleanup=None, capture=None, submit=None):
        if threading.current_thread() is not threading.main_thread():
            _fail('GUI_THREAD_REQUIRED', 'Initialize the worker on the FreeCAD GUI thread.')
        if not _uuid(worker_id) or not _uuid(session_id):
            _fail('WORKER_IDENTITY_INVALID', 'Worker identity is invalid.')
        from pft_worker_input import freeze_fcstd, verify_frozen_input, cleanup_frozen_input
        from pft_thumbnail_capture import capture_thumbnail
        self.app, self.gui, self.staging_root = app, gui, staging_root
        self.worker_id, self.session_id, self.post = worker_id, session_id, post
        self.freeze, self.verify = freeze or freeze_fcstd, verify or verify_frozen_input
        self.cleanup, self.capture = cleanup or cleanup_frozen_input, capture or capture_thumbnail
        self.submit = submit or self._thread
        self.lock = threading.RLock()
        self.jobs, self.seen = OrderedDict(), {}
        self.active, self.quarantined, self.stopped = None, False, False
        self._require_empty()
        # Installed before any open, including an open that raises after creating a document.
        self.app.addDocumentObserver(self)

    @staticmethod
    def _thread(function):
        threading.Thread(target=function, name='PhoenixThumbnailInput', daemon=True).start()

    def _require_empty(self):
        documents = self.app.listDocuments()
        if type(documents) is not dict or documents or self.app.getActiveTransaction() is not None:
            _fail('WORKER_NOT_EMPTY', 'The dedicated worker must have no documents or transaction.')

    def slotCreatedDocument(self, document):
        with self.lock:
            job = self.jobs.get(self.active)
            if job is None:
                self.quarantined = True
                return
            job['created'].append(document)
            if job['snapshot']['state'] != 'opening' or len(job['created']) != 1:
                self.quarantined = True

    def slotDeletedDocument(self, document):
        with self.lock:
            job = self.jobs.get(self.active)
            if job is None or document is not job.get('closing'):
                self.quarantined = True

    def status(self):
        with self.lock:
            return {'schema_version': 1, 'worker_id': self.worker_id, 'session_id': self.session_id,
                    'state': 'quarantined' if self.quarantined else 'busy' if self.active else 'stopped' if self.stopped else 'ready',
                    'active_job_id': self.active, 'saves_cad': False, 'render_profile': _PROFILE,
                    'supported_file_scope': 'trusted-native-self-contained', 'jobs_retained': 20, 'max_jobs': 256,
                    'max_input_bytes': 64 * 1024 * 1024, 'max_png_bytes': 4 * 1024 * 1024,
                    'dimensions': {'max_width': 1024, 'max_height': 1024, 'max_pixels': 1024 * 1024}}

    def rpc(self, method, params):
        params = validate_request(method, params)
        if method == 'get_thumbnail_worker_status':
            return self.status()
        with self.lock:
            if params['workerId'] != self.worker_id or params['sessionId'] != self.session_id:
                _fail('WORKER_SESSION_CHANGED', 'The owned worker session no longer matches; do not replay into another process.')
            job_id = params['jobId']
            if method == 'render_document_thumbnail':
                fingerprint = sha256(json.dumps(params, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
                if job_id in self.seen:
                    if self.seen[job_id] != fingerprint:
                        _fail('JOB_ID_CONFLICT', 'This job identity is already bound to different input.')
                    return self._snapshot(job_id)
                if self.stopped or self.quarantined:
                    _fail('WORKER_UNAVAILABLE', 'The owned worker cannot accept another job.')
                if self.active:
                    _fail('WORKER_BUSY', 'Submit only one file at a time to this worker.')
                if len(self.seen) >= 256:
                    _fail('WORKER_SESSION_LIMIT', 'Start a fresh owned worker after its job identity budget is exhausted.')
                snapshot = {'schema_version': 1, 'worker_id': self.worker_id, 'session_id': self.session_id,
                            'job_id': job_id, 'state': 'queued', 'phase': 'queued', 'cancel_requested': False,
                            'input_revision': None, 'result': None, 'error': None}
                job = {'snapshot': snapshot, 'params': params, 'frozen': None, 'created': [], 'closing': None}
                self.seen[job_id], self.jobs[job_id], self.active = fingerprint, job, job_id
                # post() never runs App or filesystem work on this HTTP thread.
                try:
                    self.post(lambda: self._start(job_id), 0)
                except Exception:
                    self._dispatch_failed(job_id)
                return deepcopy(snapshot)
            snapshot = self._snapshot(job_id)
            if method == 'cancel_thumbnail_job' and snapshot['state'] not in _STATES:
                self.jobs[job_id]['snapshot']['cancel_requested'] = True
                snapshot = self._snapshot(job_id)
            return snapshot

    def _snapshot(self, job_id):
        if job_id not in self.jobs:
            _fail('JOB_RESULT_EXPIRED' if job_id in self.seen else 'JOB_NOT_FOUND',
                  'Job result has expired; it will not be executed again.' if job_id in self.seen else 'Job does not exist in this worker session.')
        return deepcopy(self.jobs[job_id]['snapshot'])

    def _phase(self, job, phase):
        with self.lock:
            job['snapshot']['state'] = job['snapshot']['phase'] = phase

    def _start(self, job_id):
        job = self.jobs[job_id]
        try:
            self._require_empty()
            if job['snapshot']['cancel_requested']:
                self._finish(job_id)
                return
            if self.quarantined or self.stopped:
                _fail('WORKER_UNAVAILABLE', 'Worker ownership or lifecycle changed before the job began.')
            self._phase(job, 'freezing')
            self.submit(lambda: self._freeze(job_id))
        except Exception as error:
            self._finish(job_id, error)

    def _freeze(self, job_id):
        job = self.jobs[job_id]
        error = None
        try:
            frozen = self.freeze(job['params']['file'], self.staging_root)
            job['frozen'] = frozen
            if frozen.revision != job['params']['expectedSha256']:
                _fail('INPUT_REVISION_CHANGED', 'Input bytes do not match the explicitly approved SHA256.')
            with self.lock:
                job['snapshot']['input_revision'] = {'sha256': frozen.revision, 'bytes': frozen.compressed_bytes}
        except Exception as caught:
            error = caught
        try:
            self.post(lambda: self._after_freeze(job_id, error), 0)
        except Exception:
            self._dispatch_failed(job_id)

    def _dispatch_failed(self, job_id):
        # No App calls from a failed HTTP/background-to-GUI handoff. Preserve any
        # frozen input for diagnosis; this session must not run another file.
        with self.lock:
            self.quarantined = True
            job = self.jobs[job_id]['snapshot']
            job['state'] = job['phase'] = 'failed'
            job['result'] = None
            job['error'] = {'code': 'WORKER_GUI_DISPATCH_FAILED', 'message': 'GUI handoff failed; worker is quarantined and this job will not replay.'}
            self.active = None

    def _after_freeze(self, job_id, error):
        if error is not None or self.jobs[job_id]['snapshot']['cancel_requested']:
            self._finish(job_id, error)
        else:
            self._open(job_id)

    def _open(self, job_id):
        job = self.jobs[job_id]
        try:
            self._require_empty()
            if self.quarantined or self.stopped:
                _fail('WORKER_UNAVAILABLE', 'Worker became unavailable before restore.')
            self.verify(job['frozen'])
            with self.lock:
                if job['snapshot']['cancel_requested'] or self.stopped:
                    self._finish(job_id)
                    return
                if self.quarantined:
                    _fail('WORKER_UNAVAILABLE', 'Worker ownership changed before restore.')
                self._phase(job, 'opening')
            document = self.app.openDocument(str(job['frozen'].staged_path))
            documents = self.app.listDocuments()
            if (self.quarantined or len(job['created']) != 1 or document is not job['created'][0]
                    or type(documents) is not dict or len(documents) != 1
                    or documents.get(document.Name) is not document or not self._matches(job, document)):
                self.quarantined = True
                _fail('WORKER_DOCUMENT_OWNERSHIP_LOST', 'Restore produced an unapproved or unidentifiable document.')
            self._idle_document(document)
            self.verify(job['frozen'])
            if not self._restore_visibility(job_id, document):
                self._finish(job_id)
                return
            self._phase(job, 'rendering')
            # Let the native view finish initial creation without a nested Qt event loop.
            self.post(lambda: self._render(job_id), 100)
        except Exception as error:
            self._finish(job_id, error)

    def _matches(self, job, document):
        try:
            return (any(document is item for item in job['created']) and job['frozen'] is not None
                    and Path(document.FileName) == Path(job['frozen'].staged_path))
        except Exception:
            return False

    def _idle_document(self, document):
        gui_document = self.gui.getDocument(document.Name)
        if (self.app.getActiveTransaction() is not None or document.HasPendingTransaction is not False
                or gui_document is None or gui_document.getInEdit() is not None):
            _fail('WORKER_DOCUMENT_BUSY', 'Task document edit or transaction state cannot be confirmed idle.')

    def _visibility_guard(self, job_id, document, context, target=None):
        job = self.jobs[job_id]
        def ownership_lost():
            self.quarantined = True
            _fail('WORKER_DOCUMENT_OWNERSHIP_LOST', 'Visibility restore lost its exact task document or object identity.')
        with self.lock:
            if self.active != job_id or self.quarantined or job['snapshot']['state'] != 'opening':
                ownership_lost()
            if self.stopped or job['snapshot']['cancel_requested']:
                return False
        name, identities = context
        self._idle_document(document)
        documents = self.app.listDocuments()
        if (type(documents) is not dict or len(documents) != 1 or documents.get(name) is not document
                or len(job['created']) != 1 or job['created'][0] is not document or not self._matches(job, document)):
            ownership_lost()
        if identities is not None:
            current = document.Objects
            if (type(current) not in (list, tuple) or len(current) != len(identities)
                    or frozenset(id(item) for item in current) != identities):
                ownership_lost()
        if target is not None:
            object_name, kind, _visible, obj, provider = target
            if (document.getObject(object_name) is not obj or obj.Name != object_name or obj.TypeId != kind
                    or obj.Document is not document or obj.ViewObject is not provider):
                ownership_lost()
        # Native getters/setters can synchronously reenter application code.
        # Recheck after them before allowing the next native write.
        self._idle_document(document)
        documents = self.app.listDocuments()
        if (type(documents) is not dict or len(documents) != 1 or documents.get(name) is not document
                or not self._matches(job, document)):
            ownership_lost()
        # The idle/document getters above can themselves replace an object or
        # provider. Recheck these bindings last, before the next write; the
        # remaining lifecycle check below is process-local only.
        if identities is not None:
            current = document.Objects
            if (type(current) not in (list, tuple) or len(current) != len(identities)
                    or frozenset(id(item) for item in current) != identities):
                ownership_lost()
        if target is not None:
            object_name, kind, _visible, obj, provider = target
            if (document.getObject(object_name) is not obj or obj.Name != object_name or obj.TypeId != kind
                    or obj.Document is not document or obj.ViewObject is not provider):
                ownership_lost()
        with self.lock:
            if self.active != job_id or self.quarantined or job['snapshot']['state'] != 'opening':
                ownership_lost()
            return not self.stopped and not job['snapshot']['cancel_requested']

    def _restore_visibility(self, job_id, document):
        job = self.jobs[job_id]
        rows = job['frozen'].restore_visibility
        if rows is None:
            # A saved GuiDocument owns all its view state, including deliberate
            # hidden objects. Never initialize or show its providers here.
            return True
        if job.get('visibility_restoring'):
            self.quarantined = True
            _fail('WORKER_VISIBILITY_REENTRANT', 'Visibility restore cannot reenter the same task.')
        job['visibility_restoring'] = True
        try:
            from pft_worker_input import MAX_OBJECTS, SUPPORTED_OBJECT_TYPES
            if (type(rows) is not tuple or not 0 < len(rows) <= MAX_OBJECTS
                    or any(type(row) is not tuple or len(row) != 3 or type(row[0]) is not str
                           or not 0 < len(row[0]) <= 256 or row[1] not in SUPPORTED_OBJECT_TYPES
                           or type(row[2]) is not bool for row in rows)
                    or len({row[0] for row in rows}) != len(rows)):
                _fail('WORKER_VISIBILITY_INVALID', 'Frozen visibility is outside the supported input profile.')
            context = (document.Name, None)
            if not self._visibility_guard(job_id, document, context):
                return False
            objects = document.Objects
            if type(objects) not in (list, tuple) or len(objects) != len(rows):
                self.quarantined = True
                _fail('WORKER_VISIBILITY_INVALID', 'Restored object set does not match the approved input.')
            objects = tuple(objects)
            context = (context[0], frozenset(id(item) for item in objects))
            if len(context[1]) != len(rows):
                self.quarantined = True
                _fail('WORKER_VISIBILITY_INVALID', 'Restored object identities are not unique.')
            if not self._visibility_guard(job_id, document, context):
                return False
            by_name = {}
            for obj in objects:
                name = obj.Name
                if name in by_name:
                    _fail('WORKER_VISIBILITY_INVALID', 'Restored object names do not match the approved input.')
                by_name[name] = obj
                if not self._visibility_guard(job_id, document, context):
                    return False
            if set(by_name) != {row[0] for row in rows}:
                _fail('WORKER_VISIBILITY_INVALID', 'Restored object names do not match the approved input.')
            targets = []
            for name, kind, visible in rows:
                obj = by_name[name]
                provider = obj.ViewObject
                target = (name, kind, visible, obj, provider)
                if provider is None or not self._visibility_guard(job_id, document, context, target):
                    if provider is None:
                        _fail('WORKER_VISIBILITY_INVALID', 'A restored native view provider is unavailable.')
                    return False
                current = provider.Visibility
                if not self._visibility_guard(job_id, document, context, target):
                    return False
                if type(current) is not bool:
                    _fail('WORKER_VISIBILITY_INVALID', 'Native visibility is not an exact boolean.')
                targets.append(target)
            # Validate every object/provider before writing any visibility.
            for target in targets:
                _name, _kind, visible, _obj, provider = target
                if not self._visibility_guard(job_id, document, context, target):
                    return False
                current = provider.Visibility
                if not self._visibility_guard(job_id, document, context, target):
                    return False
                if type(current) is not bool:
                    _fail('WORKER_VISIBILITY_INVALID', 'Native visibility is not an exact boolean.')
                if current is not visible:
                    failure = None
                    try:
                        provider.Visibility = visible
                    except Exception as error:
                        failure = error
                    if not self._visibility_guard(job_id, document, context, target):
                        return False
                    if failure is not None:
                        _fail('WORKER_VISIBILITY_RESTORE_FAILED', 'Native visibility could not be restored.')
            # Group callbacks must not silently undo another object's explicit
            # false/true state. No image is published unless the whole set agrees.
            for target in targets:
                if not self._visibility_guard(job_id, document, context, target):
                    return False
                current = target[4].Visibility
                if not self._visibility_guard(job_id, document, context, target):
                    return False
                if type(current) is not bool or current is not target[2]:
                    _fail('WORKER_VISIBILITY_RESTORE_FAILED', 'Restored visibility does not match the approved input.')
            return True
        finally:
            job['visibility_restoring'] = False

    def _render(self, job_id):
        job = self.jobs[job_id]
        try:
            if job['snapshot']['cancel_requested']:
                self._finish(job_id)
                return
            if self.quarantined or self.stopped:
                _fail('WORKER_UNAVAILABLE', 'Worker state changed before rendering.')
            documents = self.app.listDocuments()
            if type(documents) is not dict or len(documents) != 1:
                self.quarantined = True
                _fail('WORKER_DOCUMENT_OWNERSHIP_LOST', 'Document set changed before rendering.')
            document = next(iter(documents.values()))
            if not self._matches(job, document):
                self.quarantined = True
                _fail('WORKER_DOCUMENT_OWNERSHIP_LOST', 'Task document identity changed before rendering.')
            self._idle_document(document)
            self.verify(job['frozen'])
            if job['snapshot']['cancel_requested'] or self.stopped:
                self._finish(job_id)
                return
            result = self.capture(self.app, self.gui, document.Name,
                                  width=job['params']['width'], height=job['params']['height'],
                                  background='white', axonometric_fit=True)
            if result.get('camera_restored') is not True or result.get('saved_cad') is not False:
                _fail('WORKER_CAPTURE_INVALID', 'Capture did not confirm camera restoration and no save.')
            from pft_thumbnail import sanitize_png
            png = bytes(result['png'])
            if len(png) > 4 * 1024 * 1024 or sanitize_png(png, job['params']['width'], job['params']['height']) != png:
                _fail('WORKER_CAPTURE_INVALID', 'Capture PNG is outside the sanitized output contract.')
            job['png'] = png
            self._finish(job_id)
        except Exception as error:
            self._finish(job_id, error)

    def _finish(self, job_id, error=None):
        job = self.jobs[job_id]
        self._phase(job, 'closing')
        try:
            documents = self.app.listDocuments()
            if type(documents) is not dict:
                raise ValueError()
            for name, document in tuple(documents.items()):
                if not self._matches(job, document):
                    self.quarantined = True
                    continue
                # Do not touch a foreign edit/transaction or a replaced document wrapper.
                self._idle_document(document)
                if self.app.listDocuments().get(name) is not document:
                    raise ValueError()
                job['closing'] = document
                try:
                    self.app.closeDocument(name)
                finally:
                    job['closing'] = None
            self._require_empty()
        except Exception:
            self.quarantined = True
            error = PftThumbnailWorkerError('WORKER_CLEANUP_UNCERTAIN', 'Task document cleanup could not be verified; worker is quarantined.')
        frozen = job['frozen']
        if frozen is not None:
            try:
                self.verify(frozen)
            except Exception as caught:
                if error is None:
                    error = caught
            # Keep the staged file when a document may still refer to it.
            if not self.quarantined:
                try:
                    self.cleanup(frozen)
                except Exception:
                    self.quarantined = True
                    error = PftThumbnailWorkerError('WORKER_CLEANUP_UNCERTAIN', 'Private input cleanup could not be verified; worker is quarantined.')
        if self.quarantined and error is None:
            error = PftThumbnailWorkerError('WORKER_QUARANTINED', 'Worker ownership changed; no successful image is published.')
        with self.lock:
            snapshot = job['snapshot']
            if error is not None:
                snapshot['state'] = snapshot['phase'] = 'failed'
                # Never expose native exceptions, file paths or restore output.
                code = getattr(error, 'code', 'WORKER_JOB_FAILED')
                snapshot['error'] = {'code': code if isinstance(code, str) and re.fullmatch('[A-Z][A-Z0-9_]{0,63}', code) else 'WORKER_JOB_FAILED',
                                     'message': 'Thumbnail task failed; inspect worker status before submitting another file.'}
            elif snapshot['cancel_requested'] or self.stopped:
                snapshot['state'] = snapshot['phase'] = 'cancelled'
            else:
                png = job.get('png')
                if not png or snapshot['input_revision'] is None:
                    snapshot['state'] = snapshot['phase'] = 'failed'
                    snapshot['error'] = {'code': 'WORKER_CAPTURE_MISSING', 'message': 'No verified thumbnail was produced.'}
                else:
                    snapshot['state'] = snapshot['phase'] = 'done'
                    snapshot['result'] = {'png_base64': base64.b64encode(png).decode('ascii'), 'sha256': sha256(png).hexdigest(),
                        'width': job['params']['width'], 'height': job['params']['height'], 'bytes': len(png),
                        'camera_restored': True, 'documents_closed': True, 'saved_cad': False,
                        'worker_reusable': True, 'render_profile': _PROFILE}
            job.pop('png', None)
            job['created'].clear()
            job['frozen'] = None
            self.active = None
            while len(self.jobs) > 20:
                self.jobs.popitem(last=False)

    def stop(self):
        with self.lock:
            self.stopped = True
            if self.active:
                self.jobs[self.active]['snapshot']['cancel_requested'] = True
        # No close-all or process termination. An in-flight task cleans at its next safe phase.


def bootstrap(app, gui):
    """Launcher-only entry; validate the private development profile before serving."""
    import pft_bridge
    import pft_development
    from PySide import QtCore, QtWidgets
    profile, marker = pft_development._owned_profile()
    if Path(__file__).resolve().parent != Path(marker['source']):
        _fail('WORKER_SOURCE_MISMATCH', 'Worker source does not match the owned profile.')
    marker_file = profile / '.phoenix-thumbnail-worker.json'
    info = marker_file.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 4096):
        _fail('WORKER_IDENTITY_INVALID', 'Worker profile marker is not private and owned.')
    owned = json.loads(marker_file.read_text(encoding='utf-8'))
    session_id = os.environ.get('PFT_THUMBNAIL_WORKER_SESSION')
    worker_id = os.environ.get('PFT_THUMBNAIL_WORKER_ID')
    if (owned != {'schema_version': 1, 'owner': 'phoenix-thumbnail-worker', 'development_owner_id': marker['owner_id'],
                  'source': marker['source'], 'worker_id': worker_id} or not _uuid(session_id) or not _uuid(worker_id)):
        _fail('WORKER_IDENTITY_INVALID', 'Worker profile and launch identities do not match.')
    if app.listDocuments() or app.getActiveTransaction() is not None or pft_bridge.get_status()['running']:
        _fail('WORKER_NOT_EMPTY', 'Do not adopt an existing user FreeCAD or bridge as a batch worker.')
    runtime = profile / 'runtime'
    staging = runtime / 'thumbnail-input'
    if not staging.exists():
        staging.mkdir(mode=0o700)
    staging_info = staging.lstat()
    if (staging.is_symlink() or not stat.S_ISDIR(staging_info.st_mode) or staging_info.st_uid != os.getuid()
            or stat.S_IMODE(staging_info.st_mode) != 0o700 or list(staging.iterdir())):
        _fail('WORKER_STAGING_UNAVAILABLE', 'Worker staging must be private, empty and owned.')
    lease_path = runtime / 'thumbnail-worker.lock'
    application = QtWidgets.QApplication.instance()
    if application is None:
        _fail('GUI_THREAD_REQUIRED', 'An existing FreeCAD GUI application is required.')
    fd = os.open(str(lease_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        lease_info = os.fstat(fd)
    except BaseException:
        os.close(fd)
        raise
    worker = dispatcher = server = serving = window = receipt_info = None
    accepting = [False]
    connected = False
    receipt = runtime / ('thumbnail-worker-' + session_id + '.json')
    def remove_owned_lease():
        current = lease_path.lstat()
        if (current.st_dev, current.st_ino) == (lease_info.st_dev, lease_info.st_ino):
            lease_path.unlink()
    def exiting():
        accepting[0] = False
        worker.stop()
        def shutdown():
            try:
                server.shutdown()
            finally:
                server.server_close()
        threading.Thread(target=shutdown, daemon=True).start()
        if worker.active is None and not worker.quarantined and not app.listDocuments():
            remove_owned_lease()
    try:
        with os.fdopen(fd, 'w', encoding='ascii') as stream:
            json.dump({'worker_id': worker_id, 'session_id': session_id, 'pid': os.getpid()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        class Dispatcher(QtCore.QObject):
            requested = QtCore.Signal(object, int)
            def __init__(self):
                super().__init__(application)
                self.requested.connect(self.deliver, QtCore.Qt.QueuedConnection)
            @QtCore.Slot(object, int)
            def deliver(self, function, delay):
                QtCore.QTimer.singleShot(delay, function)
            def post(self, function, delay):
                self.requested.emit(function, delay)
        dispatcher = Dispatcher()
        worker = PftThumbnailWorker(app, gui, staging, worker_id, session_id, dispatcher.post)
        server = pft_bridge._PftBridgeHttpServer(('127.0.0.1', 0), pft_bridge._PftRequestHandler)
        def process(payload):
            try:
                if not accepting[0]:
                    _fail('WORKER_STARTING_OR_STOPPING', 'Worker readiness has not been committed or it is stopping.')
                if type(payload) is not dict or set(payload) - {'method', 'params'}:
                    _fail('INVALID_REQUEST', 'Invalid worker request envelope.')
                method, params = payload.get('method'), payload.get('params', {})
                if method in ('ping', 'get_status'):
                    if params != {}:
                        _fail('INVALID_PARAMS', 'This method accepts no parameters.')
                    value = {'pong': True} if method == 'ping' else {
                        'protocol_version': 1, 'addon_name': 'PhoenixFreeCADTools', 'addon_version': '0.3.2',
                        'running': not worker.stopped, 'stopping': worker.stopped, 'host': '127.0.0.1',
                        'port': server.server_address[1], 'supported_methods': sorted(METHODS | {'ping', 'get_status'})}
                else:
                    value = worker.rpc(method, params)
                return {'ok': True, 'data': value}
            except PftThumbnailWorkerError as error:
                return {'ok': False, 'error': {'code': error.code, 'message': error.message}}
            except Exception:
                return {'ok': False, 'error': {'code': 'WORKER_REQUEST_FAILED', 'message': 'Worker request failed without automatic replay.'}}
        # Never override socketserver.process_request(request, client_address).
        server.pft_process_payload = process
        serving = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.2}, name='PhoenixThumbnailHTTP', daemon=True)
        application.aboutToQuit.connect(exiting)
        connected = True
        window = gui.getMainWindow()
        # Strong references must outlive every signal and the process listener.
        window._pft_thumbnail_worker = (worker, dispatcher, server, serving, exiting)
        window.setWindowTitle('FreeCAD — Phoenix Thumbnail Worker')
        serving.start()
        data = {'schema_version': 1, 'owner': 'phoenix-thumbnail-worker', 'worker_id': worker_id, 'session_id': session_id,
                'pid': os.getpid(), 'protocol_version': 1, 'addon_version': '0.3.2',
                'url': 'http://127.0.0.1:' + str(server.server_address[1]) + '/', 'ready': True}
        with os.fdopen(os.open(str(receipt), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as stream:
            receipt_info = os.fstat(stream.fileno())
            json.dump(data, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        accepting[0] = True
        return data
    except BaseException:
        accepting[0] = False
        if worker is not None:
            worker.stop()
        if server is not None:
            if serving is not None and serving.is_alive():
                server.shutdown()
                serving.join(2)
            server.server_close()
        if connected:
            application.aboutToQuit.disconnect(exiting)
        if window is not None and getattr(window, '_pft_thumbnail_worker', (None,))[0] is worker:
            del window._pft_thumbnail_worker
        if dispatcher is not None:
            dispatcher.deleteLater()
        if worker is not None and worker.active is None:
            app.removeDocumentObserver(worker)
        if receipt_info is not None:
            current = receipt.lstat()
            if (current.st_dev, current.st_ino) == (receipt_info.st_dev, receipt_info.st_ino):
                receipt.unlink()
        if (worker is None or worker.active is None) and not app.listDocuments():
            remove_owned_lease()
        raise
