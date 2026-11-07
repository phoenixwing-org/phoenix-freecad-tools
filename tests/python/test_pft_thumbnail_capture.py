# SPDX-License-Identifier: Apache-2.0
"""Capture guards, in-place camera restoration, and sanitized PNG handoff."""
import copy
import importlib.util
import itertools
import math
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
        self.native32 = False
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
        if self.native32 and self.kind != 'SFEnum':
            convert = lambda number: struct.unpack('!f', struct.pack('!f', number))[0]
            self.value = tuple(convert(number) for number in self.value) if isinstance(self.value, tuple) else convert(self.value)
        self.default = False


class Camera:
    def __init__(self, kind='OrthographicCamera'):
        self.kind = kind
        self.fields = {'viewportMapping': Field('SFEnum', 0), 'position': Field('SFVec3f', (8.0, 3.0, 20.0)),
                       'orientation': Field('SFRotation', (0.0, 0.0, 0.0, 1.0)), 'nearDistance': Field('SFFloat', 0.1),
                       'farDistance': Field('SFFloat', 100.0), 'aspectRatio': Field('SFFloat', 1.3),
                       'focalDistance': Field('SFFloat', 9.0),
                       'height' if kind == 'OrthographicCamera' else 'heightAngle': Field('SFFloat', 12.0 if kind == 'OrthographicCamera' else .6)}
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


class Box:
    def __init__(self, empty=False, low=(0., 0., 0.), high=(32., 18., 6.)):
        self.empty, self.low, self.high = empty, low, high
    def isEmpty(self): return self.empty
    def getMin(self): return Value(*self.low)
    def getMax(self): return Value(*self.high)
    def hasVolume(self): raise AssertionError('flat and line bounds must not be discarded')


class ThumbnailCaptureTests(unittest.TestCase):
    def setUp(self):
        self.doc = types.SimpleNamespace(Name='PublicFixture', save=lambda: self.fail('CAD save forbidden'),
                                         recompute=lambda: self.fail('CAD recompute forbidden'))
        self.view, self.editing, self.transaction = View(), None, False
        self.gui_doc = types.SimpleNamespace(activeView=lambda: self.view, getInEdit=lambda: self.editing)
        self.app = types.SimpleNamespace(ActiveDocument=self.doc, getDocument=lambda name: self.doc if name == self.doc.Name else None,
                                         getActiveTransaction=lambda: self.transaction,
                                         setActiveDocument=lambda *args: self.fail('activation forbidden'))
        self.backend, self.preference_reads = '', []
        def parameter(path):
            self.assertEqual(path, 'User parameter:BaseApp/Preferences/View')
            def get_string(key, default):
                self.preference_reads.append((key, default))
                return self.backend
            return types.SimpleNamespace(GetString=get_string,
                                         SetString=lambda *args: self.fail('preference writes forbidden'),
                                         SetBool=lambda *args: self.fail('preference writes forbidden'))
        self.app.ParamGet = parameter
        self.gui = types.SimpleNamespace(getDocument=lambda name: self.gui_doc, activeDocument=lambda: self.gui_doc,
                                         runCommand=lambda *args: self.fail('GUI commands forbidden'))
        self.coin = types.SimpleNamespace(SbRotation=Value, SbViewportRegion=lambda width, height: (width, height))
        self.box, self.bounds_calls, self.bounds_after = Box(), [], None
        def bounds_action(viewport):
            def apply(scene):
                self.bounds_calls.append((scene, viewport))
                if self.bounds_after: self.bounds_after()
            return types.SimpleNamespace(apply=apply, getBoundingBox=lambda: self.box)
        self.coin.SoGetBoundingBoxAction = bounds_action
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
                self.assertEqual(node.fits, [(self.view.scene, (128, 96), 1.1)] if kind == 'OrthographicCamera' else [])
                if kind == 'PerspectiveCamera':
                    self.assertEqual(self.bounds_calls, [(self.view.scene, (128, 96))])
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

    def on_position_restore(self, callback):
        armed = [False]
        events = []
        original = self.view.node.fields['position'].setValue
        def set_position(*value):
            original(*value)
            if armed[0]:
                armed[0] = False
                events.append('position_restored')
                callback()
        self.view.node.fields['position'].setValue = set_position
        self.view.after = lambda: armed.__setitem__(0, True)
        return events

    def test_restoration_callback_invalidating_target_or_idle_state_rejects_the_image(self):
        for kind in ('OrthographicCamera', 'PerspectiveCamera'):
            for transition in ('active_document', 'gui_document', 'view', 'transaction', 'edit'):
                with self.subTest(camera=kind, transition=transition):
                    self.setUp()
                    original_view = self.view
                    node = self.view.node = Camera(kind)
                    before = capture._camera_snapshot(node)
                    foreign = ('NeutralExternalTransaction', 999)
                    def change():
                        if transition == 'active_document': self.app.ActiveDocument = None
                        elif transition == 'gui_document': self.gui.activeDocument = lambda: None
                        elif transition == 'view': self.view = View()
                        elif transition == 'transaction': self.transaction = foreign
                        else: self.editing = object()
                    events = self.on_position_restore(change)
                    code = ('THUMBNAIL_DOCUMENT_BUSY' if transition in ('transaction', 'edit')
                            else 'THUMBNAIL_DOCUMENT_CHANGED')
                    self.assert_error(code, lambda: self.call(width=32, height=32))
                    self.assertEqual(events, ['position_restored'])
                    self.assertEqual(capture._camera_snapshot(node), before)
                    self.assertEqual(len(original_view.calls), 1)
                    self.assertFalse(any(path.exists() or path.parent.exists() for path in original_view.paths))
                    if transition == 'active_document': self.assertIsNone(self.app.ActiveDocument)
                    if transition == 'transaction': self.assertIs(self.transaction, foreign)

    def test_original_capture_error_is_preserved_when_restoration_also_changes_context(self):
        before = capture._camera_snapshot(self.view.node)
        self.view.failure = True
        self.on_position_restore(lambda: setattr(self.app, 'ActiveDocument', None))
        self.assert_error('THUMBNAIL_CAPTURE_FAILED', lambda: self.call(width=32, height=32))
        self.assertEqual(capture._camera_snapshot(self.view.node), before)
        self.assertIsNone(self.app.ActiveDocument)

    def test_camera_restore_failure_keeps_priority_over_context_failure(self):
        def fail_restore_and_context():
            self.view.node.fields['position'].fail = True
            self.app.ActiveDocument = None
        self.view.after = fail_restore_and_context
        self.assert_error('THUMBNAIL_CAMERA_RESTORE_FAILED', self.call)
        self.assertIsNone(self.app.ActiveDocument)

    def test_background_thread_never_reads_native_state(self):
        errors = []
        def worker():
            try: self.call()
            except capture.PftThumbnailCaptureError as error: errors.append(error.code)
        thread = threading.Thread(target=worker); thread.start(); thread.join()
        self.assertEqual(errors, ['MAIN_THREAD_REQUIRED'])
        self.assertEqual(self.view.calls, [])

    def test_perspective_all_mappings_and_output_shapes_use_native_float32_readback(self):
        for mapping, aspect, size in itertools.product(range(5), (.3, 1., 2.5), ((96, 96), (192, 64), (64, 192))):
            with self.subTest(mapping=mapping, aspect=aspect, size=size):
                node = self.view.node = Camera('PerspectiveCamera')
                node.fields['viewportMapping'].value = mapping
                node.fields['aspectRatio'].value = aspect
                for field in node.fields.values():
                    field.native32 = True
                    value, default, ignored = field.value, field.default, field.ignored
                    field.setValue(Value(*value) if isinstance(value, tuple) else value)
                    field.default, field.ignored = default, ignored
                before = capture._camera_snapshot(node)
                observed = []
                self.view.after = lambda: observed.append(capture._camera_snapshot(node))
                result = self.call(width=size[0], height=size[1])
                self.assertTrue(result['camera_restored'])
                self.assertEqual(capture._camera_snapshot(node), before)
                # saveImage's synthetic far-plane mutation happens after the
                # strict pre-render readback; pose and focal remain observable.
                self.assertGreater(observed[0]['fields']['focalDistance']['value'], 0.)
                self.assertEqual(node.fits, [])

    def test_empty_flat_line_and_point_bounds_are_not_confused(self):
        class EmptyBox(Box):
            def getMin(self): raise AssertionError('empty extrema must not be read')
            def getMax(self): raise AssertionError('empty extrema must not be read')
        for box in (EmptyBox(empty=True), Box(high=(32., 18., 0.)), Box(high=(0., 18., 0.)),
                    Box(low=(2., 3., 4.), high=(2., 3., 4.))):
            with self.subTest(box=type(box).__name__, high=box.high):
                self.view.node = Camera('PerspectiveCamera'); self.box = box
                before = capture._camera_snapshot(self.view.node)
                result = self.call(width=64, height=64)
                self.assertTrue(result['camera_restored'])
                self.assertEqual(capture._camera_snapshot(self.view.node), before)

    def test_native_integer_sbbool_accepts_empty_and_nonempty_without_reading_empty_extrema(self):
        class EmptyBox(Box):
            def getMin(self): raise AssertionError('empty extrema must not be read')
            def getMax(self): raise AssertionError('empty extrema must not be read')
        for box in (Box(empty=0), EmptyBox(empty=1)):
            with self.subTest(empty=box.empty):
                self.setUp(); self.view.node = Camera('PerspectiveCamera'); self.box = box
                before = capture._camera_snapshot(self.view.node)
                result = self.call(width=64, height=64)
                self.assertTrue(result['camera_restored'])
                self.assertEqual(capture._camera_snapshot(self.view.node), before)
                self.assertEqual(len(self.view.calls), 1)

    def test_invalid_empty_flag_rejects_without_truthiness_or_image_export(self):
        class TruthinessTrap:
            def __bool__(self): raise AssertionError('truthiness must not be called')
        class IntegerSubclass(int):
            def __eq__(self, other): raise AssertionError('subclass equality must not be called')
        class HostileMeta(type):
            def __eq__(self, other): raise AssertionError('metaclass equality must not be called')
        class NativeImpostor(metaclass=HostileMeta):
            def __bool__(self): raise AssertionError('impostor truthiness must not be called')
            def __eq__(self, other): raise AssertionError('impostor equality must not be called')
        for flag in (None, -1, 2, 0.0, 1.0, '', '0', [], TruthinessTrap(), IntegerSubclass(1), NativeImpostor()):
            with self.subTest(flag_type=type(flag).__name__):
                self.setUp(); self.view.node = Camera('PerspectiveCamera'); self.box = Box(empty=flag)
                before = capture._camera_snapshot(self.view.node)
                self.assert_error('THUMBNAIL_CAMERA_UNSUPPORTED', self.call)
                self.assertEqual(capture._camera_snapshot(self.view.node), before)
                self.assertEqual(self.view.calls, [])

    def test_bad_bbox_or_camera_projection_rejects_and_restores_without_export(self):
        for change in ('mapping', 'angle', 'aspect', 'point_precision', 'invalid_extrema', 'bbox_failure'):
            with self.subTest(change=change):
                self.setUp(); self.view.node = Camera('PerspectiveCamera')
                if change == 'mapping': self.view.node.fields['viewportMapping'].value = 9
                elif change == 'angle': self.view.node.fields['heightAngle'].value = math.pi
                elif change == 'aspect': self.view.node.fields['aspectRatio'].value = 0.
                elif change == 'point_precision': self.box = Box(low=(1e30,)*3, high=(1e30,)*3)
                elif change == 'invalid_extrema': self.box = Box(low=(1., 0., 0.), high=(0., 1., 1.))
                else: self.bounds_after = lambda: (_ for _ in ()).throw(RuntimeError('private bbox path'))
                before = capture._camera_snapshot(self.view.node)
                self.assert_error('THUMBNAIL_CAPTURE_FAILED' if change == 'bbox_failure' else 'THUMBNAIL_CAMERA_UNSUPPORTED', self.call)
                self.assertEqual(capture._camera_snapshot(self.view.node), before)
                self.assertEqual(self.view.calls, [])

    def test_perspective_readback_and_bbox_callbacks_cannot_change_projection_silently(self):
        for change in ('native_position', 'projection_during_bounds', 'empty_projection_during_bounds'):
            with self.subTest(change=change):
                self.setUp(); self.view.node = Camera('PerspectiveCamera')
                node = self.view.node; before = capture._camera_snapshot(node)
                if change == 'native_position':
                    original = node.fields['position'].setValue
                    first = [True]
                    def dropped_position(*args):
                        if first[0]: first[0] = False
                        else: original(*args)
                    node.fields['position'].setValue = dropped_position
                else:
                    self.bounds_after = lambda: setattr(node.fields['heightAngle'], 'value', 1.0)
                    if change == 'empty_projection_during_bounds': self.box = Box(empty=True)
                self.assert_error('THUMBNAIL_CAMERA_UNSUPPORTED', self.call)
                self.assertEqual(capture._camera_snapshot(node), before)
                self.assertEqual(self.view.calls, [])

    def test_framebuffer_backend_fit_is_refused_without_preference_or_camera_writes(self):
        for kind in ('OrthographicCamera', 'PerspectiveCamera'):
            self.view.node = Camera(kind); self.backend = 'GrabFramebuffer'
            before = capture._camera_snapshot(self.view.node)
            self.assert_error('THUMBNAIL_FIT_BACKEND_UNSUPPORTED', self.call)
            self.assertEqual(capture._camera_snapshot(self.view.node), before)
        self.assertEqual(self.preference_reads, [('SavePicture', ''), ('SavePicture', '')])
        self.assertEqual(self.view.calls, [])
        # Reading an existing framebuffer remains possible without promising
        # a new camera render. Every capture still reads the safety policy.
        self.call(width=32, height=32, axonometric_fit=False)
        self.assertEqual(self.preference_reads[-1], ('SavePicture', ''))
        self.view.payload = png(2, 2)
        self.assert_error('THUMBNAIL_CAPTURE_FAILED', lambda: self.call(width=32, height=32, axonometric_fit=False))

    def test_macos_coin_backend_refuses_both_cameras_and_fit_modes_before_native_or_temp_access(self):
        self.backend = 'CoinOffscreenRenderer'
        for kind, fit in itertools.product(('OrthographicCamera', 'PerspectiveCamera'), (True, False)):
            with self.subTest(camera=kind, fit=fit):
                self.view.node = Camera(kind)
                before = capture._camera_snapshot(self.view.node)
                with (patch.object(capture.sys, 'platform', 'darwin'),
                      patch.dict(sys.modules, {'pivy': None}),
                      patch.object(self.view, 'getCameraNode') as camera_read,
                      patch.object(self.view, 'getSceneGraph') as scene_read,
                      patch.object(self.view, 'saveImage') as export,
                      patch.object(capture.tempfile, 'TemporaryDirectory') as temporary):
                    error = self.assert_error('THUMBNAIL_BACKEND_UNSUPPORTED', lambda: self.call(axonometric_fit=fit))
                    self.assertEqual(error.message, '当前 macOS 的 Coin 离屏后端存在崩溃风险，未捕获缩略图；请选择默认或 FramebufferObject 后端。')
                    self.assertIn(error.message, capture.TRANSLATABLE_STRINGS)
                    for operation in (camera_read, scene_read, export, temporary):
                        operation.assert_not_called()
                self.assertEqual(capture._camera_snapshot(self.view.node), before)
        self.assertEqual(self.preference_reads, [('SavePicture', '')] * 4)
        self.assertEqual(self.bounds_calls, [])
        self.assertEqual(self.view.calls, [])

    def test_non_macos_coin_backend_preserves_native_delegate_for_both_fit_modes(self):
        self.backend = 'CoinOffscreenRenderer'
        for platform, kind, fit in itertools.product(('linux', 'win32'), ('OrthographicCamera', 'PerspectiveCamera'), (True, False)):
            with self.subTest(platform=platform, camera=kind, fit=fit):
                self.view.node = Camera(kind)
                before = capture._camera_snapshot(self.view.node)
                with patch.object(capture.sys, 'platform', platform):
                    result = self.call(width=32, height=32, axonometric_fit=fit)
                self.assertTrue(result['camera_restored'])
                self.assertEqual(capture._camera_snapshot(self.view.node), before)
        self.assertEqual(len(self.view.calls), 8)

    def test_macos_default_fbo_and_other_exact_native_values_preserve_delegate(self):
        for backend, kind, fit in itertools.product(('', 'default', 'FramebufferObject', 'unknown-native-default', 'CoinOffscreenRenderer '),
                                                    ('OrthographicCamera', 'PerspectiveCamera'), (True, False)):
            with self.subTest(backend=backend, camera=kind, fit=fit):
                self.backend = backend; self.view.node = Camera(kind)
                before = capture._camera_snapshot(self.view.node)
                with patch.object(capture.sys, 'platform', 'darwin'):
                    result = self.call(width=32, height=32, axonometric_fit=fit)
                self.assertTrue(result['camera_restored'])
                self.assertEqual(capture._camera_snapshot(self.view.node), before)
        self.assertEqual(len(self.view.calls), 20)

    def test_unreadable_backend_is_unknown_with_or_without_fit_before_camera_or_temp_access(self):
        parameters = (None, lambda _path: object(), lambda _path: (_ for _ in ()).throw(RuntimeError('private preference')),
                      lambda _path: types.SimpleNamespace(GetString=lambda *_args: None),
                      lambda _path: types.SimpleNamespace(GetString=lambda *_args: b'CoinOffscreenRenderer'))
        for fit, parameter in itertools.product((True, False), parameters):
            with self.subTest(fit=fit, parameter=parameter):
                with (patch.object(self.app, 'ParamGet', parameter),
                      patch.object(self.view, 'getCameraNode') as camera_read,
                      patch.object(self.view, 'saveImage') as export,
                      patch.object(capture.tempfile, 'TemporaryDirectory') as temporary):
                    self.assert_error('THUMBNAIL_STATE_UNKNOWN', lambda: self.call(axonometric_fit=fit))
                    for operation in (camera_read, export, temporary):
                        operation.assert_not_called()
        self.assertEqual(self.view.calls, [])

    def test_backend_read_follows_active_view_and_idle_context_checks(self):
        with patch.object(self.app, 'ParamGet', side_effect=AssertionError('invalid context must not read backend')):
            for fit in (True, False):
                self.app.ActiveDocument = None; self.transaction = False
                self.assert_error('THUMBNAIL_DOCUMENT_CHANGED', lambda: self.call(axonometric_fit=fit))
                self.app.ActiveDocument = self.doc; self.transaction = True
                self.assert_error('THUMBNAIL_DOCUMENT_BUSY', lambda: self.call(axonometric_fit=fit))
        self.assertEqual(self.view.calls, [])


if __name__ == '__main__': unittest.main()
