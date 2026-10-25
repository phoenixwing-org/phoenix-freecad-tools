# SPDX-License-Identifier: Apache-2.0
"""Independent synthetic native-API fixtures; never imports or runs FreeCAD."""
import copy
import importlib.util
import math
from pathlib import Path
import types
import unittest
from unittest.mock import patch

import test_pft_business as fixtures

controller = fixtures.business._mvc()[1]
spec = importlib.util.spec_from_file_location('pad_length_test_module', fixtures.ADDON / 'pft_pad_length.py')
pad_tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pad_tools)
NS = types.SimpleNamespace


def vector(x=0., y=0., z=0.):
    return NS(x=x, y=y, z=z)


def placement():
    return NS(Base=vector(), Rotation=NS(Q=(0., 0., 0., 1.)))


def quantity(value, angle=False):
    return NS(Value=value, Unit=NS(Signature=(0, 0, 0, 0, 0, 0, 0, 1) if angle else (1, 0, 0, 0, 0, 0, 0, 0)))


def material():
    # Deliberately invented values, not taken from an installed material library.
    return NS(UUID='synthetic-material', Name='Synthetic', Description='', URL='', Reference='',
              Parent='', Author='', License='', PhysicalModels=[], AppearanceModels=[], Tags=[],
              PhysicalProperties={}, AppearanceProperties={}, LegacyProperties={})


class Shape:
    def __init__(self, length=5., volume=None, identity=None):
        self.Volume = 6. * length if volume is None else volume
        self.BoundBox = NS(XMin=0., YMin=0., ZMin=0., XMax=2., YMax=3., ZMax=length)
        self.Solids = [object()]
        self.identity = identity if identity is not None else object()
        self.valid, self.null = True, False

    def isValid(self): return self.valid
    def isNull(self): return self.null
    def isSame(self, other): return self.identity is other.identity


def null_shape():
    return NS(isNull=lambda: True)


class Curve:
    def __init__(self, kind):
        self.TypeId = kind
        self.FirstParameter, self.LastParameter = 0., 1.
        self.extension = True
        self.StartPoint, self.EndPoint = vector(), vector(2., 0., 0.)
        self.Location, self.Axis = vector(), vector(0., 0., 1.)
        self.XAxis, self.YAxis = vector(1., 0., 0.), vector(0., 1., 0.)
        self.AngleXU, self.Radius = 0., 2.
        if kind == 'Part::GeomCircle':
            self.Rotation = NS(Q=(0., 0., 0., 1.))
            self.LastParameter = 2 * math.pi
        if kind == 'Part::GeomArcOfCircle':
            self.Circle = Curve('Part::GeomCircle')
            self.LastParameter = math.pi

    def hasExtensionOfType(self, kind):
        if kind != 'Sketcher::SketchGeometryExtension':
            raise AssertionError('unexpected extension read')
        return self.extension

    def __getattr__(self, field):
        if field in ('Content', 'getExtensions', 'Save', 'toShape', 'Rotation'):
            raise AssertionError('unsupported geometry getter')
        raise AttributeError(field)


def constraint(kind='Distance'):
    return NS(Type=kind, First=0, FirstPos=1, Second=-2000, SecondPos=0, Third=-2000,
              ThirdPos=0, Value=2., Name='', Driving=True, InVirtualSpace=False,
              IsActive=True, LabelDistance=10., LabelPosition=0.)


class NativeObject:
    def __init__(self, name, kind):
        self.Name, self.Label, self.TypeId = name, name, kind
        self.PropertiesList, self.ExpressionEngine = ['Label'], []
        self.OutList, self.InList, self.State = [], [], ['Up-to-date']
        self.Placement = placement()
        self.statuses, self.modes = [], []
        self.valid = True

    def getTypeIdOfProperty(self, field):
        return 'App::PropertyLength' if field == 'Length' else 'App::PropertyString'

    def getPropertyStatus(self, field): return self.statuses
    def getEditorMode(self, field): return self.modes
    def isValid(self): return self.valid

    def getConstruction(self, index):
        self.construction_reads += 1
        return self.construction[index]

    @property
    def GeometryCount(self): return len(self.Geometry)


class Pad(NativeObject):
    def __init__(self):
        super().__init__('Pad', 'PartDesign::Pad')
        self.PropertiesList += ['Length']
        self._length, self.Length2 = quantity(5.), quantity(10.)
        self.Type, self.Type2, self.SideType = 'Length', 'Length', 'One side'
        self.UseCustomVector, self.AlongSketchNormal = False, True
        self.Reversed, self.Midplane, self.AllowMultiFace, self.Suppressed = False, False, True, False
        self.Refine = True
        self.Direction, self.BaseFeature = vector(0., 0., 1.), None
        self.ReferenceAxis = self.UpToFace = self.UpToFace2 = self.UpToShape = self.UpToShape2 = None
        self.Offset, self.Offset2 = quantity(0.), quantity(0.)
        self.TaperAngle, self.TaperAngle2 = quantity(0., True), quantity(0., True)
        self.ShapeMaterial, self.SuppressedShape = material(), null_shape()
        self.writes, self.setter_hook = [], None

    @property
    def Length(self): return self._length

    @Length.setter
    def Length(self, value):
        self.writes.append(value)
        self._length = quantity(value)
        self.State = ['Touched']
        if self.setter_hook:
            self.setter_hook()


class NativeDocument:
    def __init__(self, curve_kind='line'):
        self.Name, self.UndoMode = 'ExampleDocument', 1
        self.HasPendingTransaction, self.RecomputesFrozen = False, False
        self.active_transaction, self.next_transaction = None, 0
        self.events, self.recompute_calls = [], []
        self.recompute_hook, self.commit_hook, self.abort_hook = None, None, None
        self.recompute_count = 2
        self.body = NativeObject('Body', 'PartDesign::Body')
        self.sketch = NativeObject('Sketch', 'Sketcher::SketchObject')
        self.pad = Pad()
        self.origin = NativeObject('Origin', 'App::Origin')
        self.features = [NativeObject('Axis%d' % n, 'App::Line') for n in range(3)]
        self.features += [NativeObject('Plane%d' % n, 'App::Plane') for n in range(3)]
        self.features += [NativeObject('Point', 'App::Point')]
        self.Objects = [self.body, self.sketch, self.pad, self.origin, *self.features]
        for obj in self.Objects: obj.Document = self
        self.body.Group, self.body.Tip = [self.sketch, self.pad], self.pad
        self.body.BaseFeature, self.body.Origin, self.body.AllowCompound = None, self.origin, True
        self.body.ShapeMaterial, self.body.Shape = material(), Shape()
        self.origin.OriginFeatures = self.features
        self.pad._Body, self.pad.Profile, self.pad.Shape = self.body, (self.sketch, []), Shape()
        self.sketch.MapMode, self.sketch.AttachmentSupport = 'Deactivated', []
        self.sketch.AttacherType, self.sketch.AttacherEngine = 'Attacher::AttachEnginePlane', 'Engine Plane'
        self.sketch.MapReversed, self.sketch.MapPathParameter = False, 0.
        self.sketch.AttachmentOffset = placement()
        self.sketch.ExternalGeometry, self.sketch.ExternalTypes, self.sketch.Exports = [], [], []
        self.sketch.construction_reads = 0
        if curve_kind == 'circle':
            self.sketch.Geometry = [Curve('Part::GeomCircle')]
        elif curve_kind == 'arc':
            self.sketch.Geometry = [Curve('Part::GeomArcOfCircle'), Curve('Part::GeomLineSegment')]
        else:
            self.sketch.Geometry = [Curve('Part::GeomLineSegment') for _ in range(4)]
            endpoints = [(vector(), vector(2., 0., 0.)), (vector(2., 0., 0.), vector(2., 3., 0.)),
                         (vector(2., 3., 0.), vector(0., 3., 0.)), (vector(0., 3., 0.), vector())]
            for geometry, (start, end) in zip(self.sketch.Geometry, endpoints):
                geometry.StartPoint, geometry.EndPoint = start, end
        self.sketch.construction = [False] * len(self.sketch.Geometry)
        self.sketch.Constraints = [constraint()]
        # Group and Tip are distinct native link properties and repeat Pad.
        self.body.OutList = [self.sketch, self.pad, self.origin, self.pad]
        self.pad.OutList = [self.sketch, self.body]
        self.origin.OutList = self.features[:]
        for obj in self.Objects:
            obj.InList = [other for other in self.Objects for linked in other.OutList if linked is obj]

    def getObject(self, name):
        return next((obj for obj in self.Objects if obj.Name == name), None)

    def openTransaction(self, title):
        self.events.append('open')
        self.next_transaction += 1
        self.active_transaction = (title, self.next_transaction)
        self.HasPendingTransaction = True
        # Preserve object/shape identities while restoring only fixture state.
        memo = {id(obj): obj for obj in self.Objects}
        memo[id(self)] = self
        for obj in self.Objects:
            if hasattr(obj, 'Shape'): memo[id(obj.Shape)] = obj.Shape
        self.before = [(obj, copy.deepcopy(obj.__dict__, memo)) for obj in self.Objects]
        self.before_objects = self.Objects[:]

    def commitTransaction(self):
        self.events.append('commit')
        self.active_transaction, self.HasPendingTransaction = None, False
        if self.commit_hook: self.commit_hook()

    def abortTransaction(self):
        self.events.append('abort')
        self.active_transaction, self.HasPendingTransaction = None, False
        self.Objects = self.before_objects[:]
        for obj, values in self.before:
            obj.__dict__.clear()
            obj.__dict__.update(values)
        if self.abort_hook: self.abort_hook()

    def recompute(self, objects, force, has_error):
        self.recompute_calls.append((objects[:], force, has_error))
        self.pad.Shape = Shape(self.pad.Length.Value)
        self.body.Shape = Shape(self.pad.Length.Value)
        for obj in self.Objects: obj.State = ['Up-to-date']
        if self.recompute_hook: self.recompute_hook()
        return self.recompute_count


class PadLengthTests(unittest.TestCase):
    def setUp(self): self.setup_model()

    def setup_model(self, kind='line'):
        self.doc = NativeDocument(kind)
        self.app = NS(ActiveDocument=self.doc, documents={self.doc.Name: self.doc})
        self.app.getDocument = lambda name: self.app.documents.get(name)
        self.app.listDocuments = lambda: dict(self.app.documents)
        self.app.getActiveTransaction = lambda: self.doc.active_transaction
        self.edit = None
        self.gui = NS(getDocument=lambda name: NS(getInEdit=lambda: self.edit))

    def prepare(self, length=8.):
        return pad_tools.prepare_plan(self.app, self.gui, self.doc, 'Body', self.doc.pad, length, controller)

    def apply(self, plan):
        return pad_tools.apply_plan(self.app, self.gui, self.doc, plan, controller)

    def assert_error(self, function, code=None):
        with self.assertRaises(pad_tools.PftPadLengthError) as caught:
            function()
        if code: self.assertEqual(caught.exception.code, code)
        self.assertNotIn('synthetic native details', str(caught.exception))
        return caught.exception.code

    def test_capability_exact_and_public_preview_has_no_native_refs(self):
        plan, rows = self.prepare()
        self.assertEqual(set(pad_tools.CAPABILITIES), {'version', 'operation', 'confirmation', 'preview_method',
            'request_method', 'status_method', 'length_unit', 'minimum_mm', 'maximum_mm', 'model_scope',
            'max_open_documents', 'saves_document'})
        self.assertEqual(rows[0]['old'], 5.)
        self.assertEqual(rows[0]['new'], 8.)
        self.assertEqual(rows[0]['source'], 'cached_native_typed_snapshot')
        self.assertIn('11', rows[0]['note'])
        self.assertIn('identity', rows[0]['note'])
        self.assertTrue(all(type(v) in (str, int, float, bool, type(None)) for v in rows[0].values()))
        self.assertEqual(self.doc.events, [])
        self.assertEqual(self.doc.pad.writes, [])
        self.assertEqual(self.doc.recompute_calls, [])
        self.assertIs(plan['pad'], self.doc.pad)

    def test_line_circle_and_arc_profiles_apply_once_with_owned_transaction(self):
        for kind in ('line', 'circle', 'arc'):
            with self.subTest(kind=kind):
                self.setup_model(kind)
                plan, _ = self.prepare()
                result = self.apply(plan)
                self.assertEqual(self.doc.events, ['open', 'commit'])
                self.assertEqual(self.doc.pad.writes, [8.])
                self.assertEqual(self.doc.recompute_calls, [([self.doc.body], False, True)])
                self.assertEqual(result['length_mm'], 8.)
                self.assertEqual(result['recompute_count'], 2)
                self.assertEqual(result['checked_objects'], 11)
                self.assertEqual(result['geometry']['pad'], {'volume_mm3': 48., 'dimensions_mm': [2., 3., 8.], 'solids': 1})
                self.assertEqual(result['geometry']['body'], result['geometry']['pad'])
                self.assertIs(self.app.ActiveDocument, self.doc)
                self.assertIsNone(self.doc.active_transaction)

    def test_unchanged_preview_has_no_writes(self):
        plan, rows = self.prepare(5.)
        self.assertEqual(rows[0]['status'], 'unchanged')
        self.assert_error(lambda: self.apply(plan), 'PAD_UNSUPPORTED')
        self.assertEqual(self.doc.events, [])

    def test_parameter_boundaries_and_invalid_types(self):
        for value in (0.01, 10000): self.prepare(value)
        for value in (True, False, '8', None, -1, 0, .001, 10000.01, math.nan, math.inf):
            with self.subTest(value=value): self.assert_error(lambda: self.prepare(value))
        self.assertEqual(self.doc.events, [])

    def test_unsupported_scope_and_native_type_rejected_without_writes(self):
        mutations = [lambda: setattr(self.doc.pad, 'TypeId', 'PartDesign::FeaturePython'),
            lambda: setattr(self.doc.pad, 'Proxy', None),
            lambda: self.doc.pad.ExpressionEngine.append(('Length', '2 mm')),
            lambda: setattr(self.doc.pad, 'SideType', 'Two sides'),
            lambda: setattr(self.doc.pad, 'Type', 'UpToFace'),
            lambda: setattr(self.doc.pad, 'UseCustomVector', True),
            lambda: setattr(self.doc.pad, 'TaperAngle', quantity(1., True)),
            lambda: setattr(self.doc.pad, 'Offset', quantity(1.)),
            lambda: setattr(self.doc.pad, 'Reversed', True),
            lambda: setattr(self.doc.pad, 'BaseFeature', self.doc.sketch),
            lambda: setattr(self.doc.body, 'Tip', self.doc.sketch),
            lambda: setattr(self.doc.pad, '_Body', None),
            lambda: setattr(self.doc.sketch, 'MapMode', 'FlatFace'),
            lambda: setattr(self.doc.sketch, 'AttachmentSupport', [(self.doc.features[3], [])]),
            lambda: setattr(self.doc.pad.Direction, 'z', -1.),
            lambda: setattr(self.doc.body.Placement.Base, 'x', 1.),
            lambda: setattr(self.doc.pad.ShapeMaterial, 'UUID', 'different-synthetic'),
            lambda: self.doc.sketch.ExternalGeometry.append((self.doc.pad, [])),
            lambda: self.doc.body.Group.append(self.doc.features[0]),
            lambda: self.doc.origin.OriginFeatures.pop(),
            lambda: setattr(self.doc.pad, 'Profile', (self.doc.sketch, ['Edge1'])),
            lambda: setattr(self.doc.pad.Length.Unit, 'Signature', (0,) * 8)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.setup_model()
                mutate()
                self.assert_error(self.prepare)
                self.assertEqual(self.doc.events, [])
                self.assertEqual(self.doc.pad.writes, [])

    def test_second_document_foreign_graph_or_unexpected_object_blocks(self):
        self.app.documents['Other'] = object()
        self.assert_error(self.prepare)
        self.setup_model()
        other = NativeObject('Extra', 'PartDesign::Body')
        other.Document = self.doc
        self.doc.Objects.append(other)
        self.assert_error(self.prepare)
        self.setup_model()
        foreign = NativeObject('Other', 'PartDesign::Body')
        foreign.Document = object()
        self.doc.pad.InList.append(foreign)
        self.assert_error(self.prepare)
        self.assertEqual(self.doc.events, [])

    def test_extension_is_checked_before_construction_and_unknown_curves_reject(self):
        self.doc.sketch.Geometry[0].extension = False
        self.assert_error(self.prepare)
        self.assertEqual(self.doc.sketch.construction_reads, 0)
        self.setup_model()
        self.doc.sketch.Geometry[0].TypeId = 'Part::GeomBSplineCurve'
        self.assert_error(self.prepare)
        self.setup_model()
        self.doc.sketch.Constraints[0].Type = 'InternalAlignment'
        self.assert_error(self.prepare)
        self.setup_model()
        self.doc.sketch.Constraints[0].Second = -3
        self.assert_error(self.prepare)
        self.assertEqual(self.doc.events, [])

    def test_writable_gate_matches_native_list_semantics(self):
        for statuses, modes in (([24], []), (['ReadOnly'], []), (['Immutable'], []), (['LockDynamic'], []),
            ([], ['ReadOnly']), (None, []), ((), []), ([True], []), ([], 0), ([], ['Unknown'])):
            with self.subTest(statuses=statuses, modes=modes):
                self.setup_model()
                self.doc.pad.statuses, self.doc.pad.modes = statuses, modes
                self.assert_error(self.prepare)
        self.setup_model()
        self.doc.pad.statuses, self.doc.pad.modes = [21, 23, 26], ['Hidden']
        self.prepare()
        self.assertEqual(self.doc.events, [])

    def test_precise_curve_constraint_material_and_metadata_snapshots_are_bound(self):
        mutations = [lambda: setattr(self.doc.sketch.Geometry[0].StartPoint, 'x', 1e-15),
            lambda: setattr(self.doc.sketch.Constraints[0], 'LabelPosition', 1e-15),
            lambda: self.doc.pad.ShapeMaterial.PhysicalProperties.update({'Density': '2'}),
            lambda: setattr(self.doc.pad, 'Length2', quantity(11.)),
            lambda: setattr(self.doc.pad, 'Label', 'Changed'),
            lambda: self.doc.pad.PropertiesList.append('Extra'),
            lambda: setattr(self.doc.pad, 'statuses', [24])]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.setup_model()
                plan, _ = self.prepare()
                mutate()
                self.assert_error(lambda: pad_tools.verify_plan(self.app, self.gui, self.doc, plan, controller))
                self.assertEqual(self.doc.events, [])

    def test_arc_orientation_and_trim_are_bound_without_arc_rotation_getter(self):
        for mutate in (lambda g: setattr(g.Circle, 'AngleXU', .1),
                       lambda g: setattr(g.Circle.Rotation, 'Q', (0., 0., 1., 0.)),
                       lambda g: setattr(g, 'LastParameter', 2.)):
            self.setup_model('arc')
            plan, _ = self.prepare()
            mutate(self.doc.sketch.Geometry[0])
            self.assert_error(lambda: self.apply(plan))
            self.assertEqual(self.doc.events, [])

    def test_shape_replacement_same_metrics_invalidates_preview(self):
        plan, _ = self.prepare()
        self.doc.pad.Shape = Shape()
        self.assert_error(lambda: self.apply(plan), 'PAD_PLAN_STALE')
        self.assertEqual(self.doc.events, [])

    def test_native_errors_are_fixed_and_no_raw_details_escape(self):
        def bad(): raise RuntimeError('synthetic native details')
        with patch.object(self.doc.pad, 'getPropertyStatus', side_effect=bad):
            self.assert_error(self.prepare)
        self.assertEqual(self.doc.events, [])

    def test_bad_recompute_counts_or_completion_roll_back_once(self):
        for value in (True, None, -1, 1.5, 9007199254740992):
            self.setup_model()
            self.doc.recompute_count = value
            plan, _ = self.prepare()
            self.assert_error(lambda: self.apply(plan), 'PAD_RECOMPUTE_UNVERIFIED')
            self.assertEqual(self.doc.events, ['open', 'abort'])
            self.assertEqual(self.doc.pad.Length.Value, 5.)
            self.assertEqual(len(self.doc.recompute_calls), 1)
        self.setup_model()
        plan, _ = self.prepare()
        self.doc.recompute_hook = lambda: setattr(self.doc.pad, 'State', ['Touched'])
        self.assert_error(lambda: self.apply(plan), 'PAD_RECOMPUTE_UNVERIFIED')
        self.assertEqual(self.doc.events, ['open', 'abort'])

    def test_old_or_incorrect_shape_cannot_succeed_even_with_positive_count(self):
        hooks = [lambda old: setattr(self.doc.pad, 'Shape', old),
                 lambda old: setattr(self.doc.pad.Shape, 'Volume', 31.),
                 lambda old: setattr(self.doc.pad.Shape, 'valid', False),
                 lambda old: setattr(self.doc.body.Shape, 'Solids', [object(), object()]),
                 lambda old: setattr(self.doc.body.Shape.BoundBox, 'XMax', math.inf)]
        for hook in hooks:
            self.setup_model()
            plan, _ = self.prepare()
            old = self.doc.pad.Shape
            self.doc.recompute_hook = lambda: hook(old)
            self.assert_error(lambda: self.apply(plan))
            self.assertEqual(self.doc.events, ['open', 'abort'])
            self.assertEqual(self.doc.pad.Length.Value, 5.)

    def test_setter_callback_changes_configuration_stops_before_recompute(self):
        plan, _ = self.prepare()
        self.doc.pad.setter_hook = lambda: setattr(self.doc.pad.Direction, 'x', 1.)
        self.assert_error(lambda: self.apply(plan))
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.assertEqual(self.doc.recompute_calls, [])
        self.assertEqual(self.doc.pad.Length.Value, 5.)

    def test_setter_callback_foreign_transaction_is_never_closed(self):
        plan, _ = self.prepare()
        self.doc.pad.setter_hook = lambda: setattr(self.doc, 'active_transaction', ('foreign', 999))
        with self.assertRaises(controller.PftWriteError) as caught: self.apply(plan)
        self.assertEqual(caught.exception.code, 'PART_TRANSACTION_CHANGED')
        self.assertEqual(self.doc.events, ['open'])
        self.assertEqual(self.doc.active_transaction, ('foreign', 999))
        self.assertEqual(self.doc.recompute_calls, [])

    def test_recompute_failure_and_document_change_never_retry(self):
        plan, _ = self.prepare()
        def bad(): raise RuntimeError('synthetic native details')
        self.doc.recompute_hook = bad
        self.assert_error(lambda: self.apply(plan), 'PAD_RECOMPUTE_UNVERIFIED')
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.assertEqual(len(self.doc.recompute_calls), 1)
        self.setup_model()
        plan, _ = self.prepare()
        self.doc.recompute_hook = lambda: setattr(self.app, 'ActiveDocument', object())
        with self.assertRaises(controller.PftWriteError) as caught: self.apply(plan)
        self.assertEqual(caught.exception.code, 'PART_ROLLBACK_UNVERIFIED')
        self.assertEqual(self.doc.events, ['open', 'abort'])

    def test_commit_and_rollback_callbacks_validate_actual_state(self):
        plan, _ = self.prepare()
        self.doc.commit_hook = lambda: setattr(self.doc.pad, '_length', quantity(7.))
        with self.assertRaises(controller.PftWriteError) as caught: self.apply(plan)
        self.assertEqual(caught.exception.code, 'PART_COMMIT_UNVERIFIED')
        self.assertEqual(self.doc.events, ['open', 'commit'])
        self.setup_model()
        plan, _ = self.prepare()
        self.doc.recompute_count = None
        self.doc.abort_hook = lambda: setattr(self.doc.pad, '_length', quantity(7.))
        with self.assertRaises(controller.PftWriteError) as caught: self.apply(plan)
        self.assertEqual(caught.exception.code, 'PART_ROLLBACK_UNVERIFIED')
        self.assertEqual(self.doc.events, ['open', 'abort'])

    def test_recompute_foreign_transaction_is_not_closed_or_retried(self):
        plan, _ = self.prepare()
        self.doc.recompute_hook = lambda: setattr(self.doc, 'active_transaction', ('foreign', 999))
        with self.assertRaises(controller.PftWriteError) as caught:
            self.apply(plan)
        self.assertEqual(caught.exception.code, 'PART_TRANSACTION_CHANGED')
        self.assertEqual(self.doc.events, ['open'])
        self.assertEqual(self.doc.active_transaction, ('foreign', 999))
        self.assertEqual(len(self.doc.recompute_calls), 1)

    def test_result_contract_failure_is_before_commit_and_rolls_back(self):
        plan, _ = self.prepare()
        foreign = NS(__file__='/synthetic/other-addon/pft_pad_contract.py')
        with patch.dict(pad_tools.sys.modules, {'pft_pad_contract': foreign}):
            self.assert_error(lambda: self.apply(plan), 'PAD_PARAMETER_VERIFY_FAILED')
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.assertEqual(self.doc.pad.Length.Value, 5.)

    def test_native_shape_read_failure_after_recompute_has_fixed_error(self):
        plan, _ = self.prepare()
        def bad(other): raise RuntimeError('synthetic native details')
        self.doc.recompute_hook = lambda: setattr(self.doc.pad.Shape, 'isSame', bad)
        self.assert_error(lambda: self.apply(plan), 'PAD_PARAMETER_VERIFY_FAILED')
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.assertEqual(self.doc.pad.Length.Value, 5.)

    def test_recompute_changed_object_set_is_not_committed(self):
        plan, _ = self.prepare()
        def add_object():
            extra = NativeObject('Extra', 'App::Point')
            extra.Document = self.doc
            self.doc.Objects.append(extra)
        self.doc.recompute_hook = add_object
        self.assert_error(lambda: self.apply(plan))
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.assertEqual(len(self.doc.Objects), 11)

    def test_length_coercion_and_geometry_with_wrong_bounds_are_rejected(self):
        plan, _ = self.prepare()
        self.doc.pad.setter_hook = lambda: setattr(self.doc.pad, '_length', quantity(7.))
        self.assert_error(lambda: self.apply(plan), 'PAD_PARAMETER_VERIFY_FAILED')
        self.assertEqual(self.doc.recompute_calls, [])
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.setup_model()
        plan, _ = self.prepare()
        def wrong_bounds():
            for obj in (self.doc.pad, self.doc.body):
                obj.Shape.BoundBox.XMax = 4.
        self.doc.recompute_hook = wrong_bounds
        self.assert_error(lambda: self.apply(plan), 'PAD_GEOMETRY_VERIFY_FAILED')
        self.assertEqual(self.doc.events, ['open', 'abort'])

    def test_zero_native_count_is_not_a_failure_or_success_bit(self):
        self.doc.recompute_count = 0
        plan, _ = self.prepare()
        self.assertEqual(self.apply(plan)['recompute_count'], 0)
        self.assertEqual(self.doc.events, ['open', 'commit'])

    def test_no_second_write_from_direct_plan_replay(self):
        plan, _ = self.prepare()
        self.apply(plan)
        self.assert_error(lambda: self.apply(plan), 'PAD_PLAN_STALE')
        self.assertEqual(self.doc.events, ['open', 'commit'])
        self.assertEqual(self.doc.pad.writes, [8.])
        self.assertEqual(len(self.doc.recompute_calls), 1)

    def test_every_constraint_public_field_change_is_bound_exactly(self):
        updates = {'Type': 'Horizontal', 'First': 1, 'FirstPos': 2, 'Second': 1, 'SecondPos': 1,
                   'Third': 1, 'ThirdPos': 1, 'Value': 2.000000000000001, 'Name': 'Changed',
                   'Driving': False, 'InVirtualSpace': True, 'IsActive': False,
                   'LabelDistance': 10.000000000000002, 'LabelPosition': 1e-15}
        for key, value in updates.items():
            with self.subTest(key=key):
                self.setup_model()
                plan, _ = self.prepare()
                setattr(self.doc.sketch.Constraints[0], key, value)
                self.assert_error(lambda: self.apply(plan), 'PAD_PLAN_STALE')
                self.assertEqual(self.doc.events, [])

    def test_repeated_native_edges_are_preserved_and_count_changes_are_stale(self):
        plan, _ = self.prepare()
        self.assertEqual(self.doc.body.OutList.count(self.doc.pad), 2)
        self.assertEqual(self.doc.pad.InList.count(self.doc.body), 2)
        graph = {row[0]: row for row in plan['snapshot'][0]}
        self.assertEqual(graph['Body'][4].count('Pad'), 2)
        self.assertEqual(graph['Pad'][5].count('Body'), 2)
        self.doc.body.OutList.pop()
        self.assert_error(lambda: self.apply(plan), 'PAD_PLAN_STALE')
        self.assertEqual(self.doc.events, [])
        self.setup_model()
        plan, _ = self.prepare()
        self.doc.pad.InList.remove(self.doc.body)
        self.assert_error(lambda: self.apply(plan), 'PAD_PLAN_STALE')
        self.assertEqual(self.doc.events, [])

    def test_refine_is_strict_bool_and_exactly_bound_to_preview(self):
        self.doc.pad.Refine = 1
        self.assert_error(self.prepare, 'PAD_UNSUPPORTED')
        self.setup_model()
        plan, _ = self.prepare()
        self.doc.pad.Refine = False
        self.assert_error(lambda: self.apply(plan), 'PAD_PLAN_STALE')
        self.assertEqual(self.doc.events, [])

    def test_refine_setter_and_recompute_pollution_roll_back(self):
        for phase in ('setter', 'recompute'):
            with self.subTest(phase=phase):
                self.setup_model()
                plan, _ = self.prepare()
                def flip(): self.doc.pad.Refine = not self.doc.pad.Refine
                if phase == 'setter':
                    self.doc.pad.setter_hook = flip
                else:
                    self.doc.recompute_hook = flip
                self.assert_error(lambda: self.apply(plan), 'PAD_PARAMETER_VERIFY_FAILED')
                self.assertEqual(self.doc.events, ['open', 'abort'])
                self.assertTrue(self.doc.pad.Refine)
                self.assertEqual(self.doc.pad.Length.Value, 5.)
                self.assertEqual(len(self.doc.recompute_calls), 0 if phase == 'setter' else 1)

    def test_refine_commit_pollution_is_unverified_without_reclose(self):
        plan, _ = self.prepare()
        self.doc.commit_hook = lambda: setattr(self.doc.pad, 'Refine', False)
        with self.assertRaises(controller.PftWriteError) as caught:
            self.apply(plan)
        self.assertEqual(caught.exception.code, 'PART_COMMIT_UNVERIFIED')
        self.assertEqual(self.doc.events, ['open', 'commit'])
        self.assertFalse(self.doc.pad.Refine)

    def test_attachment_typed_engine_pairs_and_dynamic_base_rejection(self):
        for kind, label in (('3D', '3D'), ('Plane', 'Plane'), ('Line', 'Line'), ('Point', 'Point')):
            self.setup_model()
            self.doc.sketch.AttacherType = 'Attacher::AttachEngine' + kind
            self.doc.sketch.AttacherEngine = 'Engine ' + label
            self.prepare()
        for mutate in (lambda: setattr(self.doc.sketch, 'AttacherType', 'SyntheticUnknownEngine'),
                       lambda: setattr(self.doc.sketch, 'AttacherEngine', 'Engine 3D'),
                       lambda: setattr(self.doc.sketch, 'MapReversed', 0),
                       lambda: setattr(self.doc.sketch, 'MapPathParameter', True),
                       lambda: setattr(self.doc.sketch, 'MapPathParameter', math.nan),
                       lambda: setattr(self.doc.sketch.AttachmentOffset.Base, 'x', math.inf)):
            self.setup_model()
            mutate()
            self.assert_error(self.prepare)
            self.assertEqual(self.doc.events, [])
        for key in ('BaseAttacherType', 'BaseAttacherEngine', 'BaseAttachment', 'BaseMapMode',
                    'BaseMapReversed', 'BaseMapPathParameter'):
            self.setup_model()
            self.doc.sketch.PropertiesList.append(key)
            self.assert_error(self.prepare, 'PAD_UNSUPPORTED')
            self.assertNotIn(key, self.doc.sketch.__dict__)
            self.assertEqual(self.doc.events, [])

    def test_attachment_field_pollution_after_setter_or_recompute_rolls_back(self):
        mutations = (lambda: setattr(self.doc.sketch, 'MapReversed', True),
                     lambda: setattr(self.doc.sketch, 'MapPathParameter', .5),
                     lambda: setattr(self.doc.sketch.AttachmentOffset.Base, 'x', .5),
                     lambda: setattr(self.doc.sketch.AttachmentOffset.Rotation, 'Q', (0., 0., 1., 0.)),
                     lambda: (setattr(self.doc.sketch, 'AttacherType', 'Attacher::AttachEngine3D'),
                              setattr(self.doc.sketch, 'AttacherEngine', 'Engine 3D')))
        for phase in ('setter', 'recompute'):
            for mutate in mutations:
                with self.subTest(phase=phase, mutation=mutate):
                    self.setup_model()
                    plan, _ = self.prepare()
                    if phase == 'setter':
                        self.doc.pad.setter_hook = mutate
                    else:
                        self.doc.recompute_hook = mutate
                    self.assert_error(lambda: self.apply(plan), 'PAD_PARAMETER_VERIFY_FAILED')
                    self.assertEqual(self.doc.events, ['open', 'abort'])
                    self.assertEqual(self.doc.pad.Length.Value, 5.)
                    self.assertEqual(self.doc.sketch.AttacherType, 'Attacher::AttachEnginePlane')
                    self.assertFalse(self.doc.sketch.MapReversed)
                    self.assertEqual(self.doc.sketch.MapPathParameter, 0.)
                    self.assertEqual(self.doc.sketch.AttachmentOffset.Base.x, 0.)

    def test_empty_or_unknown_edit_transaction_and_frozen_state_reject(self):
        for mutate in (lambda: setattr(self, 'edit', False), lambda: setattr(self.doc, 'HasPendingTransaction', None),
                       lambda: setattr(self.doc, 'UndoMode', True), lambda: setattr(self.doc, 'RecomputesFrozen', True)):
            self.setup_model()
            mutate()
            self.assert_error(self.prepare)
            self.assertEqual(self.doc.events, [])


if __name__ == '__main__':
    unittest.main()
