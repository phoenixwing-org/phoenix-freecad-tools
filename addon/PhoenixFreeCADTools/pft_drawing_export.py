# SPDX-License-Identifier: Apache-2.0
"""One-use, memory-only PDF export approvals, separate from CAD transactions.

Import, status, lifecycle changes and request claims do not access Qt, FreeCAD,
native references or files. Only callbacks from the owned native approval view
can open/capture a drawing or publish the exact PDF bytes it has displayed.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import secrets
import sys
import threading
import time
import unicodedata

METHODS = frozenset(('prepare_drawing_export', 'request_drawing_export_approval', 'get_drawing_export_status'))
CAPABILITIES = {'version': 1, 'formats': ['pdf'], 'confirmation': 'native_capture_and_publish',
    'prepare_method': 'prepare_drawing_export', 'request_method': 'request_drawing_export_approval',
    'status_method': 'get_drawing_export_status', 'storage': 'memory_only', 'saves_document': False,
    'max_pdf_bytes': 8388608, 'preview_backend': 'macos_coregraphics', 'projection_verification': 'not_checked'}
TTL_SECONDS, MAX_RECORDS, MAX_REQUESTS, MAX_STATUS_BYTES = 300, 32, 256, 16384
_TOKEN = re.compile(r'[A-Za-z0-9_-]{16,128}\Z')
_REQUEST = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
_TERMINAL = frozenset(('published', 'unknown', 'failed', 'cancelled', 'expired'))
_MESSAGES = {
    'DRAWING_EXPORT_INVALID_PARAMS': 'Drawing export requires an explicit document, page, PDF format and request ID.',
    'DRAWING_EXPORT_NOT_FOUND': 'The export token is not retained in this bridge memory.',
    'DRAWING_EXPORT_REQUEST_CONFLICT': 'The request ID already belongs to a different export request.',
    'DRAWING_EXPORT_REQUEST_RETIRED': 'This request was retired; it will not be prepared or captured again.',
    'DRAWING_EXPORT_CAPACITY': 'The bounded export request registry is full.',
    'DRAWING_EXPORT_BUSY': 'Complete or cancel the other native export approval first.',
    'DRAWING_EXPORT_STOPPED': 'The owning bridge lifecycle stopped or changed.',
    'DRAWING_EXPORT_EXPIRED': 'The native export approval expired.',
    'DRAWING_EXPORT_NATIVE_CONFIRMATION_REQUIRED': 'Only the owned native approval callback can perform this step.',
    'DRAWING_EXPORT_GUI_REQUIRED': 'This operation requires the FreeCAD GUI thread.',
    'DRAWING_EXPORT_DOCUMENT_CHANGED': 'The bound document or its filename changed.',
    'DRAWING_EXPORT_PREVIEW_INVALID': 'The frozen PDF preview does not match this export plan.',
    'DRAWING_EXPORT_UI_UNAVAILABLE': 'The native export approval view could not be opened.',
    'DRAWING_EXPORT_POST_PUBLISH_CHANGED': 'The file outcome is retained, but CAD protection could not be verified afterwards.',
    'DRAWING_EXPORT_FAILED': 'Drawing export could not be verified. Inspect its separate capture, publication and cleanup outcomes.',
}
_PDF_CODES = frozenset(('PDF_INVALID_REQUEST', 'PDF_INPUT_TOO_LARGE', 'PDF_INPUT_MISMATCH',
    'PDF_INVALID_RESPONSE', 'PDF_UNSUPPORTED_BACKEND', 'PDF_INVALID_DOCUMENT', 'PDF_ENCRYPTED',
    'PDF_PAGE_COUNT', 'PDF_INVALID_PAPER', 'PDF_USER_UNIT_UNSUPPORTED', 'PDF_RENDER_FAILED',
    'PDF_BUSY', 'PDF_CANCELLED', 'PDF_TIMEOUT', 'PDF_PROCESS_FAILED', 'PDF_WRITE_FAILED',
    'PDF_OUTPUT_TOO_LARGE', 'PDF_BACKEND_UNAVAILABLE', 'PDF_RUNTIME_UNAVAILABLE', 'PDF_PROTOCOL_INVALID'))
_FILE_CODES = frozenset(('INVALID_EXPORT_POLICY', 'INVALID_EXPORT_PATH', 'INVALID_EXPORT_DIRECTORY',
    'INVALID_EXPORT_TARGET', 'INVALID_EXPORT_DATA', 'EXPORT_TARGET_TOO_LARGE', 'EXPORT_PLAN_INVALID',
    'EXPORT_TARGET_CHANGED', 'OVERWRITE_REQUIRED', 'EXPORT_DIRECTORY_FD_REQUIRED',
    'EXPORT_CLEANUP_FAILED', 'EXPORT_FAILED'))
_profile_codes = frozenset()
_records, _requests = OrderedDict(), {}
_lock = threading.RLock()
_enabled, _epoch = False, 0


class PftDrawingExportError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in _MESSAGES else 'DRAWING_EXPORT_FAILED'
        self.message = _MESSAGES[self.code]
        super().__init__(self.message)


def _name(value):
    return (type(value) is str and 0 < len(value) <= 256
            and not any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in value))


def validate(method, params, Error):
    if type(method) is not str or method not in METHODS:
        return False
    valid = type(params) is dict
    if valid and method == 'prepare_drawing_export':
        valid = (set(params) == {'docName', 'pageName', 'format', 'requestId'}
            and _name(params['docName']) and _name(params['pageName'])
            and type(params['format']) is str and params['format'] == 'pdf' and type(params['requestId']) is str
            and _REQUEST.fullmatch(params['requestId']) is not None)
    elif valid:
        valid = (set(params) == {'token'} and type(params['token']) is str
                 and _TOKEN.fullmatch(params['token']) is not None)
    if not valid:
        raise Error('DRAWING_EXPORT_INVALID_PARAMS', _MESSAGES['DRAWING_EXPORT_INVALID_PARAMS'])
    return True


def _load(name):
    filename = Path(__file__).resolve().with_name(name + '.py')
    existing = sys.modules.get(name)
    if existing is not None:
        if Path(getattr(existing, '__file__', '')).resolve() != filename:
            raise PftDrawingExportError('DRAWING_EXPORT_FAILED')
        return existing
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _fixed(error):
    try:
        code = getattr(error, 'code', None)
    except Exception:
        code = None
    return code if type(code) is str and code in (_MESSAGES.keys() | _PDF_CODES | _FILE_CODES | _profile_codes) else 'DRAWING_EXPORT_FAILED'


def _drop(record):
    for key in ('context', 'document_ref', 'runtime', 'pdf_bytes', 'file_plan', 'grant',
                'view', 'controller', 'profile', 'default_path', 'doc_file_name', '_action'):
        record.pop(key, None)
    record['ready'] = False


def _finish(record, state, code=None):
    record.update(state=state, error_code=code)
    _drop(record)


def _cancel(record, reason='cancelled'):
    if record['state'] in _TERMINAL:
        return
    record.setdefault('cancel_reason', reason)
    if record['state'] == 'publishing':
        return  # A publication already entered is not cancelled or replayed.
    if record.get('capture_active') or record.get('render_active'):
        record['state'] = 'cancelling'
    else:
        _finish(record, reason)


def _expire(record):
    if time.monotonic() >= record['deadline'] and record['state'] not in _TERMINAL:
        _cancel(record, 'expired')


def _dto(record):
    code = record.get('error_code')
    error = None if code is None else {'code': code, 'message': _MESSAGES.get(code, _MESSAGES['DRAWING_EXPORT_FAILED'])}
    result = {key: deepcopy(record[key]) for key in ('document', 'page', 'format', 'request_id',
        'token', 'state', 'expires_at', 'summary', 'byte_count', 'sha256', 'target', 'target_exists',
        'capture_state', 'publication_state', 'cleanup_state', 'cad_protection_state',
        'native_capture_confirmation', 'native_publication_confirmation')}
    result.update(storage='memory_only', saves_document=False, requires_confirmation=True, error=error)
    if len(json.dumps(result, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')) > MAX_STATUS_BYTES:
        raise PftDrawingExportError('DRAWING_EXPORT_FAILED')
    return result


def _record(token, Error):
    record = _records.get(token)
    if record is None:
        raise Error('DRAWING_EXPORT_NOT_FOUND', _MESSAGES['DRAWING_EXPORT_NOT_FOUND'])
    _expire(record)
    return record


def claim_prepare(params, Error):
    """Atomic admission before any GUI queue; every ID remains spent for this module."""
    validate('prepare_drawing_export', params, Error)
    signature = (params['docName'], params['pageName'], params['format'])
    with _lock:
        previous = _requests.get(params['requestId'])
        if previous is not None:
            if previous[0] != signature:
                raise Error('DRAWING_EXPORT_REQUEST_CONFLICT', _MESSAGES['DRAWING_EXPORT_REQUEST_CONFLICT'])
            if previous[1] not in _records:
                raise Error('DRAWING_EXPORT_REQUEST_RETIRED', _MESSAGES['DRAWING_EXPORT_REQUEST_RETIRED'])
            record = _record(previous[1], Error)
            return False, _dto(record)
        if not _enabled:
            raise Error('DRAWING_EXPORT_STOPPED', _MESSAGES['DRAWING_EXPORT_STOPPED'])
        if len(_requests) >= MAX_REQUESTS:
            raise Error('DRAWING_EXPORT_CAPACITY', _MESSAGES['DRAWING_EXPORT_CAPACITY'])
        for existing in _records.values():
            _expire(existing)
        if len(_records) >= MAX_RECORDS:
            retired = next((token for token, value in _records.items() if value['state'] in _TERMINAL), None)
            if retired is None:
                raise Error('DRAWING_EXPORT_CAPACITY', _MESSAGES['DRAWING_EXPORT_CAPACITY'])
            del _records[retired]
        token = secrets.token_urlsafe(24)
        expires = datetime.fromtimestamp(time.time() + TTL_SECONDS, timezone.utc).isoformat()
        record = {'document': params['docName'], 'page': params['pageName'], 'format': 'pdf',
            'request_id': params['requestId'], 'token': token, 'state': 'preparing',
            'epoch': _epoch, 'deadline': time.monotonic() + TTL_SECONDS, 'expires_at': expires,
            'summary': {'document': params['docName'], 'page': params['pageName'], 'source': '', 'expires_at': expires},
            'byte_count': None, 'sha256': None, 'target': None, 'target_exists': None,
            'capture_state': 'not_captured', 'publication_state': 'not_published', 'cleanup_state': 'complete',
            'cad_protection_state': 'not_checked', 'native_capture_confirmation': False,
            'native_publication_confirmation': False, 'error_code': None, 'ready': False,
            'prepare_started': False, 'capture_active': False, 'render_active': False}
        _records[token] = record
        _requests[params['requestId']] = (signature, token)
        return True, _dto(record)


def dispatch_error(request_id, error):
    known_not_started = {'GUI_QUEUE_TIMEOUT', 'SERVER_STOPPING', 'SERVER_NOT_RUNNING',
        'INSTANCE_TARGET_MISMATCH', 'INSTANCE_AFFINITY_LIMIT', 'INSTANCE_AFFINITY_MISMATCH'}
    with _lock:
        previous = _requests.get(request_id)
        record = _records.get(previous[1]) if previous is not None else None
        if (record is not None and record['state'] == 'preparing' and not record['prepare_started']
                and getattr(error, 'code', None) in known_not_started):
            _finish(record, 'cancelled', 'DRAWING_EXPORT_STOPPED')


def begin_approval_lifecycle():
    global _epoch, _enabled
    with _lock:
        _epoch += 1
        for record in _records.values():
            _cancel(record)
        _enabled = True
        return _epoch


def cancel_pending_approvals():
    global _epoch, _enabled
    with _lock:
        _enabled = False
        _epoch += 1
        for record in _records.values():
            _cancel(record)


def _status(token, Error=PftDrawingExportError):
    with _lock:
        # Error adapters passed by the bridge accept (code, message).
        error_type = Error if Error is not PftDrawingExportError else lambda code, _message: PftDrawingExportError(code)
        return _dto(_record(token, error_type))


def _guard(record, grant, states):
    with _lock:
        _expire(record)
        if (not _enabled or record['epoch'] != _epoch):
            _cancel(record)
            raise PftDrawingExportError('DRAWING_EXPORT_STOPPED')
        if (record['state'] not in states or record.get('grant') is not grant
                or record.get('ready') is not True or _records.get(record['token']) is not record):
            raise PftDrawingExportError('DRAWING_EXPORT_NATIVE_CONFIRMATION_REQUIRED')


def _enter_action(record, grant, states):
    with _lock:
        _expire(record)
        if (record.get('_action') is not None or record.get('grant') is not grant
                or record['state'] not in states or record.get('ready') is not True):
            return None
        ticket = object()
        record['_action'] = ticket
        return ticket


def _leave_action(record, ticket):
    with _lock:
        if record.get('_action') is ticket:
            record.pop('_action', None)


def _binding(record, app, gui, *, after_capture=False):
    try:
        controller = record['controller']
        controller._ensure_main_thread()
        document = record['document_ref']
        if (app.getDocument(record['document']) is not document or app.ActiveDocument is not document
                or document.FileName != record['doc_file_name']):
            raise PftDrawingExportError('DRAWING_EXPORT_DOCUMENT_CHANGED')
        return record['profile'].verify(app, gui, record['context'], controller, after_capture=after_capture)
    except Exception as error:
        if getattr(error, 'cleanup_state', None) == 'unknown':
            record['cleanup_state'] = 'unknown'
        if after_capture or record.get('native_capture_confirmation') is True:
            record['cad_protection_state'] = 'unverified'
        raise


def _default_path(document):
    filename = document.FileName
    if type(filename) is not str:
        raise PftDrawingExportError('DRAWING_EXPORT_DOCUMENT_CHANGED')
    if filename:
        if not Path(filename).is_absolute():
            raise PftDrawingExportError('DRAWING_EXPORT_DOCUMENT_CHANGED')
        return str(Path(filename).with_suffix('.pdf')), filename
    label = document.Label
    if type(label) is not str:
        raise PftDrawingExportError('DRAWING_EXPORT_DOCUMENT_CHANGED')
    label = ''.join(c for c in label if c not in '/\\:' and unicodedata.category(c) not in ('Cc', 'Cf', 'Cs'))[:120].strip()
    return (label or 'Drawing') + '.pdf', filename


def _summary(value, params):
    keys = {'document', 'page', 'source', 'view_count', 'dimension_count', 'page_open',
            'paper_mm', 'projection_verification', 'manual_review_required'}
    if (type(value) is not dict or set(value) - keys
            or value.get('document') != params['docName'] or value.get('page') != params['pageName']
            or not _name(value.get('source')) or type(value.get('view_count')) is not int
            or not 1 <= value['view_count'] <= 3 or type(value.get('dimension_count')) is not int
            or not 0 <= value['dimension_count'] <= 8 or type(value.get('page_open')) is not bool
            or value.get('projection_verification', 'not_checked') != 'not_checked'
            or value.get('manual_review_required', True) is not True):
        raise PftDrawingExportError('DRAWING_EXPORT_FAILED')
    paper = value.get('paper_mm')
    if (type(paper) not in (list, tuple) or len(paper) != 2 or any(
            type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 10000 for v in paper)):
        raise PftDrawingExportError('DRAWING_EXPORT_FAILED')
    result = deepcopy(value)
    result['paper_mm'] = list(paper)
    return result


def _prepare(params, app, gui, Error, expected_epoch):
    global _profile_codes
    claim_prepare(params, Error)
    with _lock:
        record = _record(_requests[params['requestId']][1], Error)
        if record['state'] != 'preparing' or record['prepare_started']:
            return _dto(record)
        if expected_epoch is not None and expected_epoch != record['epoch']:
            _finish(record, 'cancelled', 'DRAWING_EXPORT_STOPPED')
            return _dto(record)
        record['prepare_started'] = True
    try:
        controller = _load('pft_workbench_controller')
        controller._ensure_main_thread()
        profile = _load('pft_drawing_export_profile')
        codes = getattr(profile, 'ERROR_CODES', frozenset())
        if type(codes) is not frozenset or any(type(code) is not str for code in codes):
            raise PftDrawingExportError('DRAWING_EXPORT_FAILED')
        _profile_codes = codes
        document = app.getDocument(params['docName'])
        if document is None:
            raise PftDrawingExportError('DRAWING_EXPORT_DOCUMENT_CHANGED')
        page = document.getObject(params['pageName'])
        context = profile.snapshot(app, gui, document, page, controller)
        summary = _summary(context['public_summary'], params)
        summary['expires_at'] = record['expires_at']
        runtime = _load('pft_pdf_qprocess').resolve_pdf_runtime(app.getHomePath())
        default_path, doc_file_name = _default_path(document)
        with _lock:
            _expire(record)
            if record['state'] == 'preparing' and _enabled and record['epoch'] == _epoch:
                record.update(state='prepared', context=context, summary=summary, document_ref=document,
                    controller=controller, profile=profile, runtime=runtime, default_path=default_path,
                    doc_file_name=doc_file_name)
            elif record['state'] not in _TERMINAL:
                _finish(record, 'cancelled', 'DRAWING_EXPORT_STOPPED')
    except Exception as error:
        with _lock:
            if getattr(error, 'cleanup_state', None) == 'unknown':
                record['cleanup_state'] = 'unknown'
            if record['state'] not in _TERMINAL:
                _finish(record, 'failed', _fixed(error))
    return _status(record['token'], Error)


def _capture_failure(record, code, error=None):
    with _lock:
        _expire(record)
        if getattr(error, 'capture_state', None) in ('not_captured', 'unknown', 'captured'):
            record['capture_state'] = error.capture_state
        if getattr(error, 'cleanup_state', None) in ('complete', 'unknown'):
            record['cleanup_state'] = error.cleanup_state
        if (record['capture_state'] == 'unknown'
                or getattr(error, 'cad_protection_state', None) == 'unverified'):
            record['cad_protection_state'] = 'unverified'
        record['capture_active'] = False
        record['render_active'] = False
        if record['state'] == 'cancelling':
            state = record.get('cancel_reason', 'cancelled')
            _finish(record, state, record.get('error_code') if state == 'failed' else None)
        elif record['state'] not in _TERMINAL:
            _finish(record, 'failed', code)


def _notify(callback, value):
    try:
        callback(value)
    except Exception:
        pass  # Terminal state is retained even if its widget was deleted.


def _capture(record, grant, app, gui, on_ready, on_failed):
    ticket = _enter_action(record, grant, {'awaiting_capture'})
    if ticket is None:
        return
    try:
        _guard(record, grant, {'awaiting_capture'})
        with _lock:
            _guard(record, grant, {'awaiting_capture'})
            record.update(state='capturing', capture_active=True, native_capture_confirmation=True)
        _binding(record, app, gui)
        def guard():
            _guard(record, grant, {'capturing'})
            record['controller']._ensure_main_thread()
        def capture_opened():
            try:
                guard()
                if record['context'].get('pending_open') is True:
                    record['context'] = record['profile'].bind_open_scene(app, gui, record['context'], record['controller'], guard=guard)
                with _lock:
                    # Once the native call may be entered, an unclassified
                    # exception or concurrent stop cannot prove no capture.
                    record.update(capture_state='unknown', cleanup_state='unknown', cad_protection_state='unverified')
                result = record['profile'].capture_pdf(app, gui, record['context'], record['controller'], guard=guard)
                data = result['bytes']
                pdf = _load('pft_pdf_contract')
                pdf.encode_pdf_request(data, 1000, 1000)
                if result.get('capture_state') != 'captured':
                    raise PftDrawingExportError('DRAWING_EXPORT_FAILED')
                record.update(context=result['post_context'], pdf_bytes=data, capture_state='captured',
                              cad_protection_state='verified', cleanup_state=result.get('cleanup_state', 'complete'))
                if record['cleanup_state'] != 'complete':
                    raise PftDrawingExportError('DRAWING_EXPORT_FAILED')
                with _lock:
                    guard()
                    record.update(state='rendering', capture_active=False, render_active=True,
                                  byte_count=len(data), sha256=hashlib.sha256(data).hexdigest())
                bundle = {'bytes': data, 'runtime': deepcopy(record['runtime']), 'default_path': record['default_path']}
                _leave_action(record, ticket)  # A synchronous renderer terminal is now legitimate.
                try:
                    on_ready(bundle)
                except Exception:
                    # The view may have started its owned child before raising.
                    # Do not infer exit or release the frozen bytes from that.
                    with _lock:
                        record['error_code'] = 'DRAWING_EXPORT_UI_UNAVAILABLE'
                        _cancel(record, 'failed')
                    try:
                        record['view'].close()
                    except Exception:
                        pass
            except Exception as error:
                _leave_action(record, ticket)
                code = _fixed(error)
                _capture_failure(record, code, error)
                _notify(on_failed, code)
        # Native show refreshes even an existing page after Undo/Redo. Bind its
        # refreshed whole-paper scene on a later turn before the single export.
        record['context'] = record['profile'].open_page(app, gui, record['context'], record['controller'], guard=guard)
        from PySide import QtCore
        QtCore.QTimer.singleShot(0, capture_opened)
    except Exception as error:
        _leave_action(record, ticket)
        code = _fixed(error)
        _capture_failure(record, code, error)
        _notify(on_failed, code)


def _rendered(record, grant, app, gui, reply):
    ticket = _enter_action(record, grant, {'rendering', 'cancelling'})
    if ticket is None:
        return _status(record['token'])
    with _lock:
        record['render_active'] = False  # The transport callback occurs after real finish.
    try:
        _guard(record, grant, {'rendering'})
        pdf = _load('pft_pdf_contract')
        value = pdf.validate_pdf_response(reply, input_sha256=record['sha256'], byte_count=record['byte_count'], width=1000, height=1000)
        if value.get('ok') is not True:
            raise PftDrawingExportError('DRAWING_EXPORT_PREVIEW_INVALID')
        width, height = record['context']['public_summary']['paper_mm']
        paper = value['media_box_points']
        if (value['rotation_degrees'] != 0 or any(abs(actual - wanted * 72 / 25.4) > 1.5
                                                 for actual, wanted in zip(paper[2:], (width, height)))):
            raise PftDrawingExportError('DRAWING_EXPORT_PREVIEW_INVALID')
        _binding(record, app, gui, after_capture=True)
        with _lock:
            _guard(record, grant, {'rendering'})
            record['state'] = 'preview_ready'
    except Exception as error:
        _capture_failure(record, _fixed(error))
    finally:
        _leave_action(record, ticket)
    return _status(record['token'])


def _failed(record, grant, code):
    with _lock:
        _expire(record)
        if record.get('grant') is not grant or record['state'] not in {'capturing', 'rendering', 'cancelling'}:
            return _dto(record)
        if record.get('render_active') is not True:
            # A queued/entered native capture still owns its cleanup boundary.
            # Closing an unstarted renderer is not proof that capture ended.
            return _dto(record)
        record['render_active'] = False
        _capture_failure(record, code if code in _PDF_CODES else 'DRAWING_EXPORT_FAILED')
        return _dto(record)


def _choose_target(record, grant, app, gui, path):
    ticket = _enter_action(record, grant, {'preview_ready', 'target_ready'})
    if ticket is None:
        return _status(record['token'])
    try:
        _guard(record, grant, {'preview_ready', 'target_ready'})
        _binding(record, app, gui, after_capture=True)
        if type(path) is not str or len(path) > 4096:
            raise PftDrawingExportError('DRAWING_EXPORT_PREVIEW_INVALID')
        publication = _load('pft_file_publish')
        plan = publication.prepare_file_export(path, record['pdf_bytes'], extensions=('.pdf',), max_bytes=8388608)
        if plan is None:
            return _status(record['token'])
        with _lock:
            _guard(record, grant, {'preview_ready', 'target_ready'})
            record.update(state='target_ready', file_plan=plan, target=plan['path'], target_exists=plan['exists'], error_code=None)
    except Exception as error:
        with _lock:
            if record['state'] not in _TERMINAL and record['state'] != 'cancelling':
                _finish(record, 'failed', _fixed(error))
    finally:
        _leave_action(record, ticket)
    return _status(record['token'])


def _publish(record, grant, app, gui):
    ticket = _enter_action(record, grant, {'target_ready'})
    if ticket is None:
        return _status(record['token'])
    try:
        _guard(record, grant, {'target_ready'})
        _binding(record, app, gui, after_capture=True)
        with _lock:
            _guard(record, grant, {'target_ready'})
            record.update(state='publishing', native_publication_confirmation=True)
        publication = _load('pft_file_publish')
        try:
            record['publication_state'] = 'unknown'
            receipt = publication.write_file_export(record['file_plan'], extensions=('.pdf',), max_bytes=8388608,
                overwrite=record['target_exists'], require_directory_fd=True, stage_prefix='.pft-drawing-export-')
            record.update(publication_state=receipt['publication_state'], cleanup_state=receipt['cleanup_state'])
            if (receipt.get('path') != record['target'] or receipt.get('digest') != record['sha256']
                    or receipt.get('size') != record['byte_count'] or receipt['publication_state'] != 'published'):
                record['publication_state'] = 'unknown'
                raise PftDrawingExportError('DRAWING_EXPORT_FAILED')
        except Exception as error:
            if getattr(error, 'publication_state', None) in ('not_published', 'unknown', 'published'):
                record['publication_state'] = error.publication_state
            if getattr(error, 'cleanup_state', None) in ('complete', 'unknown'):
                record['cleanup_state'] = error.cleanup_state
            raise
        try:
            _binding(record, app, gui, after_capture=True)
        except Exception:
            record['cad_protection_state'] = 'unverified'
            _finish(record, 'published', 'DRAWING_EXPORT_POST_PUBLISH_CHANGED')
        else:
            _finish(record, 'published')
    except Exception as error:
        with _lock:
            if record['state'] not in _TERMINAL:
                if record['state'] == 'publishing':
                    try:
                        _binding(record, app, gui, after_capture=True)
                    except Exception:
                        record['cad_protection_state'] = 'unverified'
                state = {'published': 'published', 'unknown': 'unknown'}.get(record['publication_state'], 'failed')
                _finish(record, state, _fixed(error))
    finally:
        _leave_action(record, ticket)
    return _status(record['token'])


def _request(params, app, gui, Error, expected_epoch):
    with _lock:
        record = _record(params['token'], Error)
        if record['state'] in _TERMINAL or record['state'] == 'preparing':
            return _dto(record)
        if not _enabled or record['epoch'] != _epoch or expected_epoch is not None and expected_epoch != _epoch:
            _cancel(record)
            return _dto(record)
        if record['state'] != 'prepared':
            handle = record.get('view')
            if handle is not None:
                record['ready'] = False
                try:
                    handle.present()
                except Exception:
                    record['error_code'] = 'DRAWING_EXPORT_UI_UNAVAILABLE'
                    _cancel(record, 'failed')
                    try:
                        handle.close()
                    except Exception:
                        pass
                finally:
                    if record.get('grant') is not None and record['state'] not in _TERMINAL:
                        record['ready'] = True
            return _dto(record)
        if any(other is not record and other['state'] not in _TERMINAL | {'prepared', 'preparing'} for other in _records.values()):
            raise Error('DRAWING_EXPORT_BUSY', _MESSAGES['DRAWING_EXPORT_BUSY'])
    try:
        _binding(record, app, gui)
        grant = object()
        with _lock:
            _expire(record)
            if record['state'] != 'prepared':
                return _dto(record)
            if any(other is not record and other['state'] not in _TERMINAL | {'prepared', 'preparing'} for other in _records.values()):
                raise PftDrawingExportError('DRAWING_EXPORT_BUSY')
            record.update(state='awaiting_capture', grant=grant)
        def cancel():
            with _lock:
                if record.get('grant') is grant:
                    _cancel(record)
                return _dto(record)
        callbacks = {'capture': lambda ready, failed: _capture(record, grant, app, gui, ready, failed),
            'rendered': lambda reply: _rendered(record, grant, app, gui, reply),
            'failed': lambda code: _failed(record, grant, code),
            'choose_target': lambda path: _choose_target(record, grant, app, gui, path),
            'publish': lambda: _publish(record, grant, app, gui), 'cancel': cancel,
            'status': lambda: _status(record['token'])}
        handle = _load('pft_drawing_export_views').open_drawing_export_approval(gui, deepcopy(record['summary']), callbacks)
        if not callable(getattr(handle, 'present', None)) or not callable(getattr(handle, 'close', None)):
            raise PftDrawingExportError('DRAWING_EXPORT_UI_UNAVAILABLE')
        with _lock:
            if record['state'] == 'awaiting_capture' and record.get('grant') is grant:
                record.update(view=handle, ready=True)
            else:
                handle.close()
    except Exception as error:
        with _lock:
            if record['state'] not in _TERMINAL:
                _finish(record, 'failed', _fixed(error))
    return _status(record['token'], Error)


def perform(method, params, app, gui, Error, *, _approval_epoch=None):
    validate(method, params, Error)
    if method == 'get_drawing_export_status':
        return _status(params['token'], Error)
    if method == 'prepare_drawing_export':
        return _prepare(params, app, gui, Error, _approval_epoch)
    if method == 'request_drawing_export_approval':
        return _request(params, app, gui, Error, _approval_epoch)
    raise Error('DRAWING_EXPORT_INVALID_PARAMS', _MESSAGES['DRAWING_EXPORT_INVALID_PARAMS'])
