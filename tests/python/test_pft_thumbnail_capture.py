# SPDX-License-Identifier: Apache-2.0
"""Capture guards, in-place camera restoration, and sanitized PNG handoff."""
import copy
import importlib.util
from pathlib import Path
import struct
import sys
import threading
import types
import unittest
from unittest.mock import patch
import zlib

ADDON = Path(__file__).resolve().parents[2] / 'addon' / 'PhoenixFreeCADTools'
_saved = list(sys.path)
sys.path.insert(0, str(ADDON))
try:
    spec = importlib.util.spec_from_file_location('pft_thumbnail_capture_tests', ADDON / 'pft_thumbnail_capture.py')
    capture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(capture)
finally:
    sys.path[:] = _saved


def png(width, height, rgba=False):
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value) & 0xffffffff)
    channels = 4 if rgba else 3
    raw = b''.join(b'\x00' + bytes([40, 90, 140, 180][:channels]) * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6 if rgba else 2, 0, 0, 0))
            + chunk(b'tEXt', b'Author\x00private-synthetic-metadata') + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


class TypeId:
    def __init__(self, name): self.name = name
    def getName(self): return self.name


class Value:
    def __init__(self, *values): self.values = tuple(values)
    def getValue(self): return self.values


class Field:
    def __init__(self, kind, value):
        self.kind, self.value = kind, value
        self.default, self.ignored, self.connected = False, False, False
        self.fail = False
    def getTypeId(self): return TypeId(self.kind)
    def getValue(self): return Value(*self.value) if isinstance(self.value, tuple) else self.value
    def isConnected(self): return self.connected
    def isDefault(self): return self.default
    def isIgnored(self): return self.ignored
    def setDefault(self, value): self.default = value
    def setIgnored(self, value): self.ignored = value
    def setValue(self, *value):
        if self.fail: raise RuntimeError('private deleted native object')
        value = value[0] if len(value) == 1 else value
        self.value = value.getValue() if isinstance(value, Value) else value
        self.default = False


class Camera:
    def __init__(self, kind='OrthographicCamera'):
        self.kind = kind
        self.fields = {'viewportMapping': Field('SFEnum', 0), 'position': Field('SFVec3f', (8.0, 3.0, 20.0)),
                       'orientation': Field('SFRotation', (0.0, 0.0, 0.0, 1.0)), 'nearDistance': Field('SFFloat', 0.1),
                       'farDistance': Field('SFFloat', 100.0), 'aspectRatio': Field('SFFloat', 1.3),
                       'focalDistance': Field('SFFloat', 9.0),
                       'height' if kind == 'OrthographicCamera' else 'heightAngle': Field('SFFloat', 12.0)}
        self.fields['position'].default = True
        self.fields['nearDistance'].ignored = True
        self.fits, self.raise_fit = [], False
    def getTypeId(self): return TypeId(self.kind)
    def getFieldData(self):
        names = list(self.fields)
        return types.SimpleNamespace(getNumFields=lambda: len(names), getFieldName=lambda index: names[index])
    def getField(self, name): return self.fields[name]
    def viewAll(self, scene, viewport, slack):
        self.fits.append((scene, viewport, slack))
        self.fields['position'].setValue(40.0, 50.0, 60.0)
        for name in ('nearDistance', 'farDistance', 'aspectRatio', 'focalDistance'):
            self.fields[name].setValue(self.fields[name].value + 1.0)
        if self.raise_fit: raise RuntimeError('private fitting failure')


class View:
    def __init__(self):
        self.node = Camera()
        self.calls, self.paths = [], []
        self.failure = self.after = None
        self.payload = None
        self.scene = object()
    def getCameraNode(self): return self.node
    def getSceneGraph(self): return self.scene
    def saveImage(self, filename, width, height, background):
        self.calls.append((width, height, background)); self.paths.append(Path(filename))
        self.node.fields['farDistance'].setValue(5000.0)
        if self.after: self.after()
        if self.failure: raise RuntimeError('private export path: /private/synthetic')
        Path(filename).write_bytes(self.payload if self.payload is not None else png(width, height, background == 'Transparent'))
    def fitAll(self): raise AssertionError('animated GUI camera method forbidden')
    def viewAxonometric(self): raise AssertionError('animated GUI camera method forbidden')
    def setCamera(self, value): raise AssertionError('camera replacement forbidden')


class ThumbnailCaptureTests(unittest.TestCase):
    def setUp(self):
        self.doc = types.SimpleNamespace(Name='PublicFixture', save=lambda: self.fail('CAD save forbidden'),
                                         recompute=lambda: self.fail('CAD recompute forbidden'))
        self.view, self.editing, self.transaction = View(), None, False
        self.gui_doc = types.SimpleNamespace(activeView=lambda: self.view, getInEdit=lambda: self.editing)
        self.app = types.SimpleNamespace(ActiveDocument=self.doc, getDocument=lambda name: self.doc if name == self.doc.Name else None,
                                         getActiveTransaction=lambda: self.transaction,
                                         setActiveDocument=lambda *args: self.fail('activation forbidden'))
        self.gui = types.SimpleNamespace(getDocument=lambda name: self.gui_doc, activeDocument=lambda: self.gui_doc,
                                         runCommand=lambda *args: self.fail('GUI commands forbidden'))
        self.coin = types.SimpleNamespace(SbRotation=Value, SbViewportRegion=lambda width, height: (width, height))
        self.patcher = patch.dict(sys.modules, {'pivy': types.SimpleNamespace(coin=self.coin)})
        self.patcher.start(); self.addCleanup(self.patcher.stop)

    def call(self, **kwargs):
        return capture.capture_thumbnail(self.app, self.gui, self.doc.Name, **kwargs)

    def assert_error(self, code, callback):
        with self.assertRaises(capture.PftThumbnailCaptureError) as error: callback()
        self.assertEqual(error.exception.code, code)
        self.assertNotIn('/private', error.exception.message)
        return error.exception

    def test_ortho_and_perspective_restore_all_fields_flags_and_original_node(self):
        for kind in ('OrthographicCamera', 'PerspectiveCamera'):
            with self.subTest(kind=kind):
                node = self.view.node = Camera(kind)
                before = capture._camera_snapshot(node)
                result = self.call(width=128, height=96)
                self.assertIs(self.view.node, node)
                self.assertEqual(capture._camera_snapshot(node), before)
                self.assertEqual(node.fits, [(self.view.scene, (128, 96), 1.1)])
                self.assertEqual(self.view.calls[-1], (128, 96, 'White'))
                self.assertEqual((result['width'], result['height']), (128, 96))
                self.assertEqual(result['document'], self.doc.Name)
                self.assertEqual(result['source_kind'], 'live_freecad_gui')
                self.assertTrue(result['camera_restored']); self.assertFalse(result['saved_cad'])
                self.assertTrue(result['captured_at'].endswith('+00:00'))
                self.assertNotIn('private-synthetic-metadata'.encode(), result['png'])
                self.assertEqual(capture.sanitize_png(result['png'], 128, 96), result['png'])
                self.assertFalse(any(path.exists() or path.parent.exists() for path in self.view.paths))
                self.assertEqual(set(result), {'png', 'document', 'width', 'height', 'captured_at', 'source_kind',
                                               'camera_restored', 'saved_cad', 'warnings'})

    def test_all_explicit_backgrounds_and_current_camera_option(self):
        for background, native in (('white', 'White'), ('current', 'Current'), ('transparent', 'Transparent')):
            with self.subTest(background=background):
                before = capture._camera_snapshot(self.view.node)
                result = self.call(width=32, height=32, background=background, axonometric_fit=False)
                self.assertEqual(self.view.calls[-1], (32, 32, native))
                self.assertEqual(result['png'][25], 6 if background == 'transparent' else 2)
                self.assertEqual(self.view.node.fits, [])
                self.assertEqual(capture._camera_snapshot(self.view.node), before)

    def test_invalid_parameters_do_not_read_native_state(self):
        invalid = ({'width': True}, {'height': 0}, {'width': 1025}, {'width': 1.5}, {'height': '512'},
                   {'background': []}, {'background': 'White'}, {'axonometric_fit': 1})
        with patch.object(self.app, 'getDocument', side_effect=AssertionError('invalid input must not read CAD')):
            for options in invalid:
                with self.subTest(options=options):
                    self.assert_error('THUMBNAIL_OPTIONS_INVALID' if 'background' in options or 'axonometric_fit' in options else 'THUMBNAIL_SIZE_INVALID', lambda: self.call(**options))
            self.assert_error('DOCUMENT_REQUIRED', lambda: capture.capture_thumbnail(self.app, self.gui, 'bad\x00name'))
        self.assertEqual(self.view.calls, [])

    def test_edit_transaction_active_gui_and_missing_view_fail_before_camera_change(self):
        before = capture._camera_snapshot(self.view.node)
        self.editing = object(); self.assert_error('THUMBNAIL_DOCUMENT_BUSY', self.call)
        self.editing = None; self.transaction = True; self.assert_error('THUMBNAIL_DOCUMENT_BUSY', self.call)
        self.transaction = False; self.app.ActiveDocument = None; self.assert_error('THUMBNAIL_DOCUMENT_CHANGED', self.call)
        self.app.ActiveDocument = self.doc
        with patch.object(self.gui, 'activeDocument', return_value=object()): self.assert_error('THUMBNAIL_DOCUMENT_CHANGED', self.call)
        with patch.object(self.gui_doc, 'activeView', return_value=object()): self.assert_error('THUMBNAIL_CAMERA_UNSUPPORTED', self.call)
        with patch.object(self.app, 'getActiveTransaction', None): self.assert_error('THUMBNAIL_STATE_UNKNOWN', self.call)
        self.assertEqual(capture._camera_snapshot(self.view.node), before)
        self.assertEqual(self.view.calls, [])

    def test_connected_unknown_or_incomplete_camera_fields_are_rejected_before_export(self):
        self.view.node.fields['orientation'].connected = True
        self.assert_error('THUMBNAIL_CAMERA_UNSUPPORTED', self.call)
        self.view.node = Camera(); self.view.node.fields['unknown'] = Field('SFFloat', 1.0)
        self.assert_error('THUMBNAIL_CAMERA_UNSUPPORTED', self.call)
        self.view.node = Camera(); del self.view.node.fields['focalDistance']
        self.assert_error('THUMBNAIL_CAMERA_UNSUPPORTED', self.call)
        self.view.node = Camera('UnsupportedCamera')
        self.assert_error('THUMBNAIL_CAMERA_UNSUPPORTED', self.call)
        self.assertEqual(self.view.calls, [])

    def test_save_failure_and_fit_failure_restore_every_camera_field(self):
        before = capture._camera_snapshot(self.view.node)
        self.view.failure = True
        self.assert_error('THUMBNAIL_CAPTURE_FAILED', self.call)
        self.assertEqual(capture._camera_snapshot(self.view.node), before)
        self.assertFalse(any(path.exists() or path.parent.exists() for path in self.view.paths))
        self.view.failure = False; self.view.node.raise_fit = True
        self.assert_error('THUMBNAIL_CAPTURE_FAILED', self.call)
        self.assertEqual(capture._camera_snapshot(self.view.node), before)

    def test_invalid_png_size_mismatch_and_oversize_restore_camera_and_clean_temp(self):
        before = capture._camera_snapshot(self.view.node)
        for data, code in ((b'not PNG', 'THUMBNAIL_CAPTURE_FAILED'), (png(2, 2), 'THUMBNAIL_CAPTURE_FAILED'),
                           (b'x' * (4 * 1024 * 1024 + 1), 'THUMBNAIL_PNG_TOO_LARGE')):
            with self.subTest(code=code):
                self.view.payload = data
                self.assert_error(code, lambda: self.call(width=32, height=32))
                self.assertEqual(capture._camera_snapshot(self.view.node), before)
                self.assertFalse(any(path.exists() or path.parent.exists() for path in self.view.paths))

    def test_sanitizer_failure_still_restores_camera_without_exposing_raw_exception(self):
        before = capture._camera_snapshot(self.view.node)
        with patch.object(capture, 'sanitize_png', side_effect=ValueError('/private/synthetic-metadata')):
            self.assert_error('THUMBNAIL_CAPTURE_FAILED', lambda: self.call(width=32, height=32))
        self.assertEqual(capture._camera_snapshot(self.view.node), before)

    def test_restore_failure_or_replaced_camera_never_returns_false_success(self):
        self.view.after = lambda: setattr(self.view.node.fields['position'], 'fail', True)
        self.assert_error('THUMBNAIL_CAMERA_RESTORE_FAILED', self.call)
        self.view = View(); self.gui_doc.activeView = lambda: self.view
        self.view.after = lambda: setattr(self.view, 'node', Camera())
        self.assert_error('THUMBNAIL_CAMERA_RESTORE_FAILED', self.call)

    def test_one_restore_setter_or_flag_failure_still_restores_all_other_values_and_flags(self):
        for failure in ("value", "default"):
            with self.subTest(failure=failure):
                self.view = View(); self.gui_doc.activeView = lambda: self.view
                node = self.view.node
                before = capture._camera_snapshot(node)
                def fail_one():
                    field = node.fields["position"]
                    field.ignored = True
                    if failure == "value":
                        field.fail = True
                    else:
                        def failed_flag(_value): raise RuntimeError("synthetic flag setter failure")
                        field.setDefault = failed_flag
                self.view.after = fail_one
                self.assert_error('THUMBNAIL_CAMERA_RESTORE_FAILED', self.call)
                after = capture._camera_snapshot(node)
                for name, state in before['fields'].items():
                    if name != 'position': self.assertEqual(after['fields'][name], state, name)
                position = after['fields']['position']
                # The other flag is restored even when this field's value or first flag fails.
                self.assertEqual(position['ignored'], before['fields']['position']['ignored'])
                if failure == 'value':
                    self.assertEqual(position['default'], before['fields']['position']['default'])
                    self.assertNotEqual(position['value'], before['fields']['position']['value'])
                else:
                    self.assertEqual(position['value'], before['fields']['position']['value'])
                    self.assertNotEqual(position['default'], before['fields']['position']['default'])
                self.assertFalse(any(path.exists() or path.parent.exists() for path in self.view.paths))

    def test_active_document_change_during_export_restores_original_camera_and_rejects_image(self):
        before = capture._camera_snapshot(self.view.node)
        self.view.after = lambda: setattr(self.app, 'ActiveDocument', None)
        self.assert_error('THUMBNAIL_DOCUMENT_CHANGED', self.call)
        self.assertEqual(capture._camera_snapshot(self.view.node), before)

    def test_background_thread_never_reads_native_state(self):
        errors = []
        def worker():
            try: self.call()
            except capture.PftThumbnailCaptureError as error: errors.append(error.code)
        thread = threading.Thread(target=worker); thread.start(); thread.join()
        self.assertEqual(errors, ['MAIN_THREAD_REQUIRED'])
        self.assertEqual(self.view.calls, [])


if __name__ == '__main__': unittest.main()
