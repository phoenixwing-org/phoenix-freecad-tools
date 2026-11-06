# SPDX-License-Identifier: Apache-2.0
import copy
import importlib.util
import json
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / 'addon' / 'PhoenixFreeCADTools'
if not ADDON.exists():
    ADDON = Path(__file__).parent


def load(name):
    spec = importlib.util.spec_from_file_location('reference_test_' + name, ADDON / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


refs = load('pft_drawing_references')
ops = load('pft_operations')


class Vector:
    def __init__(self, x=0., y=0., z=0.):
        self.x, self.y, self.z = x, y, z


class Vertex:
    def __init__(self, point):
        self.Point = point


class Line:
    pass


class Circle:
    def __init__(self, radius=40., center=None):
        self.Radius = radius
        self.Center = center or Vector()


class Edge:
    def __init__(self, curve=None, length=600., start=None, end=None, closed=False):
        self.Curve = curve if curve is not None else Line()
        self.Length = length
        self.Vertexes = [Vertex(start or Vector()), Vertex(end or Vector(600., 0., 0.))]
        self.closed = closed

    def isClosed(self):
        return self.closed


class Document:
    def __init__(self):
        self.Name, self.Label = 'NeutralDocument', 'Neutral document'
        self.Objects = []

    def getObject(self, name):
        return next((obj for obj in self.Objects if obj.Name == name), None)

    def recompute(self, *args):
        raise AssertionError('A cached read must not recompute')

    def save(self):
        raise AssertionError('A cached read must not save')


class View:
    def __init__(self, document, name='NeutralView'):
        self.Name, self.Label, self.TypeId = name, 'Neutral view', 'TechDraw::DrawViewPart'
        self.Document, self.PropertiesList, self.State = document, [], ['Up-to-date']
        self.edges, self.calls = {'Edge0': Edge()}, []
        document.Objects.append(self)

    def getEdgeBySelection(self, edge):
        self.calls.append(edge)
        value = self.edges.get(edge)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise ValueError('Native missing-index result')
        return value


class Dimension:
    def __init__(self, document, kind='Distance', value=600.):
        self.Name, self.Label, self.TypeId = 'NeutralDimension' + str(len(document.Objects)), 'Neutral dimension', 'TechDraw::DrawViewDimension'
        self.Document, self.PropertiesList, self.State = document, [], ['Up-to-date']
        self.Type, self.MeasureType, self.Arbitrary = kind, 'Projected', False
        self.References2D = [(document.Objects[0], ('Edge0',))] if document.Objects else []
        self.References3D = []
        self.raw, self.calls = value, 0
        document.Objects.append(self)

    def getRawValue(self):
        self.calls += 1
        return self.raw

    def getText(self):
        raise AssertionError('A raw cached measurement must not read formatted label text')


class BridgeError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class DrawingReferenceTests(unittest.TestCase):
    def setUp(self):
        self.part = types.SimpleNamespace(Edge=Edge, Vertex=Vertex, Line=Line, Circle=Circle)
        self.freecad = types.SimpleNamespace(Vector=Vector)
        self.modules = patch.dict(sys.modules, {'Part': self.part, 'FreeCAD': self.freecad})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.document = Document()
        self.view = View(self.document)
        self.page = types.SimpleNamespace(Name='NeutralPage', Label='Neutral page', TypeId='TechDraw::DrawPage',
            Document=self.document, Views=[self.view], Template=None, Scale=1., State=['Up-to-date'])
        self.document.Objects.append(self.page)
        self.app = types.SimpleNamespace(ActiveDocument=self.document,
            getDocument=lambda name: self.document if name == self.document.Name else None)
        self.gui = types.SimpleNamespace()

    def test_zero_based_reference_is_native_unscaled_and_frozen(self):
        result = refs.read_edge(self.view, 'Edge0')
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['length_mm'], 600.)
        self.assertEqual(result['end'], [600., 0., 0.])
        self.assertEqual(self.view.calls, ['Edge0'])
        signature = refs.edge_signature(result)
        self.view.edges['Edge0'].Vertexes[1].Point.x = 25.
        self.assertEqual(result['end'][0], 600.)
        result['end'][0] = 30.
        self.assertNotEqual(refs.edge_signature(result), signature)
        self.assertIn(600., signature)

    def test_index_fallback_does_not_shift_to_part_topology_or_rescale(self):
        calls = []
        self.view.getEdgeBySelection = None
        self.view.getEdgeByIndex = lambda index: calls.append(index) or Edge()
        result = refs.read_edge(self.view, 'Edge2047')
        self.assertEqual(calls, [2047])
        self.assertEqual(result['length_mm'], 600.)
        for edge in ('Edge-1', 'Edge01', 'Edge2048', 'Edge0\n', 'Edge0.body', 'Vertex0', True):
            with self.subTest(edge=edge), self.assertRaises(refs.PftDrawingReferencesError) as error:
                refs.read_edge(self.view, edge)
            self.assertEqual(error.exception.code, 'DRAWING_REFERENCES_INVALID_ARGUMENT')
        self.assertEqual(calls, [2047])

    def test_circle_has_native_mm_radius_and_no_fabricated_endpoints(self):
        curve = Circle(40., Vector(-0., 10., 0.))
        self.view.edges['Edge0'] = Edge(curve, length=2. * math.pi * 40., closed=True)
        result = refs.read_edge(self.view, 'Edge0')
        self.assertEqual(result['curve'], 'circle')
        self.assertEqual(result['radius_mm'], 40.)
        self.assertEqual(result['center'], [-0., 10., 0.])
        self.assertIsNone(result['start'])
        self.assertIsNone(result['end'])
        self.assertEqual(result['supported_kinds'], ['Radius', 'Diameter'])
        json.dumps(result, allow_nan=False)
        refs.edge_signature(result)

    def test_open_circle_and_other_curve_do_not_read_unsupported_fields(self):
        class UnknownCurve:
            @property
            def Radius(self):
                raise AssertionError('Unknown geometry must not be traversed')
        self.view.edges['Edge0'] = Edge(UnknownCurve())
        self.assertEqual(refs.read_edge(self.view, 'Edge0')['status'], 'unsupported')
        self.view.edges['Edge0'] = Edge(Circle(), length=float('nan'), closed=False)
        self.assertEqual(refs.read_edge(self.view, 'Edge0')['status'], 'unsupported')
        class LineSubclass(Line):
            pass
        self.view.edges['Edge0'] = Edge(LineSubclass())
        self.assertEqual(refs.read_edge(self.view, 'Edge0')['status'], 'unsupported')

    def test_missing_and_failed_native_reads_have_distinct_unknown_values(self):
        self.view.edges['Edge1'] = RuntimeError('Never disclose native error detail')
        self.assertEqual(refs.read_edge(self.view, 'Edge1'), refs._empty('Edge1', 'read_failed'))
        self.assertEqual(refs.read_edge(self.view, 'Edge2'), refs._empty('Edge2', 'missing'))
        for edge in (Edge(length=0.), Edge(length=True), Edge(length=float('inf')),
                     Edge(start=Vector(float('nan'), 0., 0.)), Edge(start=Vector(0, 0., 0.)),
                     Edge(start=Vector(600., 0., 0.)), Edge(closed=1), Edge(closed=True)):
            self.view.edges['Edge0'] = edge
            with self.subTest(edge=edge):
                result = refs.read_edge(self.view, 'Edge0')
                self.assertEqual(result['status'], 'read_failed')
                self.assertIsNone(result['length_mm'])
                self.assertIsNone(result['start'])

    def test_non_native_edges_vertices_and_points_are_rejected(self):
        self.view.edges['Edge0'] = types.SimpleNamespace(Curve=Line(), Length=600.)
        self.assertEqual(refs.read_edge(self.view, 'Edge0')['status'], 'unsupported')
        for vertices in ((Vertex(Vector()), Vertex(Vector(600., 0., 0.))), [], [Vertex(Vector())],
                         [types.SimpleNamespace(Point=Vector()), Vertex(Vector(600., 0., 0.))],
                         [Vertex(types.SimpleNamespace(x=0., y=0., z=0.)), Vertex(Vector(600., 0., 0.))]):
            edge = Edge(); edge.Vertexes = vertices
            self.view.edges['Edge0'] = edge
            self.assertEqual(refs.read_edge(self.view, 'Edge0')['status'], 'read_failed')

    def test_signature_rejects_partial_fabricated_or_nonfinite_records(self):
        original = refs.read_edge(self.view, 'Edge0')
        invalid = []
        for field, value in [('start', None), ('radius_mm', 5.), ('curve', 'circle'), ('closed', True),
                             ('length_mm', float('nan')), ('supported_kinds', ['Distance']), ('extra', True)]:
            record = copy.deepcopy(original); record[field] = value; invalid.append(record)
        partial = copy.deepcopy(original); del partial['center']; invalid.append(partial)
        unavailable = refs._empty('Edge0', 'missing'); unavailable['length_mm'] = 0.; invalid.append(unavailable)
        for record in invalid:
            with self.subTest(record=record), self.assertRaises(refs.PftDrawingReferencesError):
                refs.edge_signature(record)

    def test_window_queries_each_requested_index_after_missing_without_total_claim(self):
        self.view.edges['Edge2'] = Edge()
        result = refs.read_window(self.document, self.page, self.view, 0, 4)
        self.assertEqual(self.view.calls, ['Edge0', 'Edge1', 'Edge2', 'Edge3'])
        self.assertEqual([item['status'] for item in result['items']], ['available', 'missing', 'available', 'missing'])
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['coordinate_system'], 'view_local_unscaled_qt_y_down')
        self.assertIsNone(result['total_count'])
        self.assertFalse(result['enumeration_complete'])
        self.assertEqual(result['projection_verification'], 'not_checked')
        for offset, limit in ((True, 1), (0, False), (-1, 1), (2048, 1), (2047, 2), (0, 51), (0, 0)):
            with self.subTest(offset=offset, limit=limit), self.assertRaises(refs.PftDrawingReferencesError):
                refs.read_window(self.document, self.page, self.view, offset, limit)

    def test_proxy_or_wrong_type_view_has_no_native_edge_reads(self):
        for change in ({'Proxy': object()}, {'PropertiesList': ['Proxy']}, {'TypeId': 'TechDraw::DrawViewPartPython'},
                       {'TypeId': 'TechDraw::DrawViewSection'}, {'PropertiesList': ()}):
            view = View(self.document, 'Other' + str(len(self.document.Objects)))
            for key, value in change.items():
                setattr(view, key, value)
            self.page.Views = [view]
            result = refs.read_window(self.document, self.page, view, 0, 1)
            self.assertEqual(result['status'], 'unsupported')
            self.assertEqual(result['items'], [])
            self.assertEqual(view.calls, [])

    def test_membership_and_document_identity_change_during_getter_are_stale(self):
        for mutation in (lambda: self.page.Views.append(object()), lambda: self.page.Views.clear(),
                         lambda: self.document.Objects.remove(self.view)):
            self.document = Document(); self.view = View(self.document)
            self.page = types.SimpleNamespace(Name='NeutralPage', TypeId='TechDraw::DrawPage',
                Document=self.document, Views=[self.view])
            self.document.Objects.append(self.page)
            original = self.view.getEdgeBySelection
            self.view.getEdgeBySelection = lambda edge: mutation() or original(edge)
            with self.assertRaises(refs.PftDrawingReferencesError) as error:
                refs.read_window(self.document, self.page, self.view, 0, 1)
            self.assertEqual(error.exception.code, 'DRAWING_REFERENCES_STALE')

    def test_operations_legacy_summary_is_unchanged_but_explicit_view_is_provenanced(self):
        old = ops.perform('inspect_drawing_page', {'pageName': self.page.Name}, self.app, self.gui, BridgeError)
        self.assertNotIn('projected_geometry', old)
        self.assertNotIn('document_instance_token', old)
        result = ops.perform('inspect_drawing_page', {'pageName': self.page.Name, 'viewName': self.view.Name,
            'offset': 0, 'limit': 2}, self.app, self.gui, BridgeError)
        self.assertEqual(result['page'], old['page'])
        self.assertEqual(result['objects'], old['objects'])
        self.assertEqual(result['projected_geometry']['items'][0]['edge'], 'Edge0')
        self.assertEqual(result['projected_geometry']['items'][1]['status'], 'missing')
        self.assertEqual(len(result['document_instance_token']), 32)
        self.assertFalse(result['provenance']['recomputed'])
        self.assertEqual(result['provenance']['geometry_freshness'], 'not_verified')
        for fields in ({'offset': 0}, {'limit': 2}, {'viewName': self.view.Name, 'limit': 51},
                       {'viewName': self.view.Name, 'offset': 2047, 'limit': 2},
                       {'viewName': self.view.Name, 'offset': True}, {'viewName': 'bad\u200bname'}):
            with self.subTest(fields=fields), self.assertRaises(BridgeError):
                ops.perform('inspect_drawing_page', {'pageName': self.page.Name, **fields}, self.app, self.gui, BridgeError)

    def test_operations_detect_same_name_document_replacement_and_output_budget(self):
        original = self.view.getEdgeBySelection
        def replaced(edge):
            self.app.getDocument = lambda name: Document()
            return original(edge)
        self.view.getEdgeBySelection = replaced
        with self.assertRaises(BridgeError) as error:
            ops.perform('inspect_drawing_page', {'pageName': self.page.Name, 'viewName': self.view.Name, 'limit': 1},
                self.app, self.gui, BridgeError)
        self.assertEqual(error.exception.code, 'DOCUMENT_INSTANCE_MISMATCH')
        self.app.getDocument = lambda name: self.document
        self.view.getEdgeBySelection = original
        with patch.object(ops, 'MAX_READ_BYTES', 128):
            with self.assertRaises(BridgeError) as error:
                ops.perform('inspect_drawing_page', {'pageName': self.page.Name, 'viewName': self.view.Name, 'limit': 1},
                    self.app, self.gui, BridgeError)
            self.assertEqual(error.exception.code, 'READ_RESPONSE_TOO_LARGE')

    def test_native_dimension_five_kinds_use_raw_mm_and_freeze_after_update(self):
        for kind in ('Distance', 'DistanceX', 'DistanceY', 'Diameter', 'Radius'):
            dimension = Dimension(self.document, kind)
            result = refs.read_dimension(dimension)
            self.assertEqual(result, {'status': 'available', 'kind': kind, 'value_mm': 600.,
                'unit': 'mm', 'source': 'native_cached_dimension', 'geometry_freshness': 'not_verified'})
            self.assertEqual(dimension.calls, 1)
            dimension.raw = 800.
            self.assertEqual(refs.read_dimension(dimension)['value_mm'], 800.)
            self.assertEqual(result['value_mm'], 600.)

    def test_native_dimension_unsupported_modes_do_not_read_raw(self):
        for fields in ({'Type': 'Angle'}, {'Type': 'Angle3Pt'}, {'Type': 'Area'}, {'Type': 'DistanceZ'},
                       {'MeasureType': 'True'}, {'Arbitrary': True}, {'Arbitrary': 0}, {'Proxy': object()},
                       {'PropertiesList': ['Proxy']}, {'TypeId': 'TechDraw::DrawViewDimensionPython'},
                       {'PropertiesList': ()}):
            dimension = Dimension(self.document)
            for name, value in fields.items():
                setattr(dimension, name, value)
            result = refs.read_dimension(dimension)
            self.assertEqual(result['status'], 'unsupported', fields)
            self.assertIsNone(result['value_mm'])
            self.assertEqual(dimension.calls, 0)
            self.assertEqual(result['kind'], 'Distance' if 'MeasureType' in fields or 'Arbitrary' in fields else None)

    def test_native_dimension_pending_zero_nonfinite_and_failed_are_not_valid_zero(self):
        for states in ([], ['Touched'], ['Invalid'], ['Up-to-date', 'Touched'], ['Up-to-date', object()], ('Up-to-date',)):
            dimension = Dimension(self.document); dimension.State = states
            result = refs.read_dimension(dimension)
            self.assertEqual(result['status'], 'unknown')
            self.assertEqual(dimension.calls, 0)
        for value in (0., -1., 1.e9 + 1., float('inf'), float('nan'), True, 800, None):
            dimension = Dimension(self.document, value=value)
            result = refs.read_dimension(dimension)
            self.assertEqual(result['status'], 'unknown')
            self.assertIsNone(result['value_mm'])
            self.assertEqual(dimension.calls, 1)
        dimension = Dimension(self.document, value=1.e9)
        dimension.State = ['Up-to-date', 'Expanded']
        self.assertEqual(refs.read_dimension(dimension)['value_mm'], 1.e9)
        def failed():
            raise RuntimeError('Native exception detail must not be returned')
        dimension.getRawValue = failed
        result = refs.read_dimension(dimension)
        self.assertEqual(result['status'], 'read_failed')
        self.assertEqual(result['kind'], 'Distance')
        self.assertNotIn('Native exception', json.dumps(result))
        dimension.getRawValue = None
        self.assertEqual(refs.read_dimension(dimension)['status'], 'unsupported')

    def test_operations_only_explicit_view_adds_native_dimension_readback(self):
        dimension = Dimension(self.document)
        dimension.References2D = [(self.view, ('Edge0',))]
        self.page.Views.append(dimension)
        legacy = ops.perform('inspect_drawing_page', {'pageName': self.page.Name}, self.app, self.gui, BridgeError)
        self.assertNotIn('native_measurement', legacy['objects'][1])
        self.assertEqual(dimension.calls, 0)
        params = {'pageName': self.page.Name, 'viewName': self.view.Name, 'limit': 1}
        first = ops.perform('inspect_drawing_page', params, self.app, self.gui, BridgeError)
        self.assertNotIn('native_measurement', first['objects'][0])
        self.assertEqual(first['objects'][1]['native_measurement']['value_mm'], 600.)
        dimension.raw = 800.
        second = ops.perform('inspect_drawing_page', params, self.app, self.gui, BridgeError)
        self.assertEqual(second['objects'][1]['native_measurement']['value_mm'], 800.)
        self.assertEqual(first['objects'][1]['native_measurement']['value_mm'], 600.)
        self.assertEqual(second['objects'][1]['sources'][0]['subelements'], ['Edge0'])
        dimension.Type = 'Angle'
        unsupported = ops.perform('inspect_drawing_page', params, self.app, self.gui, BridgeError)
        self.assertEqual(unsupported['objects'][1]['native_measurement']['status'], 'unsupported')
        self.assertEqual(dimension.calls, 2)

    def test_native_dimension_references_never_read_other_document_geometry(self):
        other = Document(); other.Name = 'OtherNeutralDocument'
        external_view = View(other)
        for field in ('References2D', 'References3D'):
            dimension = Dimension(self.document)
            setattr(dimension, field, [(external_view, ('Edge0' if field == 'References2D' else 'Edge1',))])
            result = refs.read_dimension(dimension)
            self.assertEqual(result['status'], 'unsupported')
            self.assertEqual(dimension.calls, 0)
            self.assertEqual(external_view.calls, [])
        unregistered = View(self.document, 'UnregisteredView')
        self.document.Objects.remove(unregistered)
        dimension = Dimension(self.document); dimension.References2D = [(unregistered, ('Edge0',))]
        self.assertEqual(refs.read_dimension(dimension)['status'], 'unsupported')
        self.assertEqual(dimension.calls, 0)

    def test_native_dimension_reference_forms_are_bounded_and_same_document_only(self):
        invalid = [[], None, (), [(self.view, ())], [(self.view, '')], [(self.view, ('',))],
                   [(self.view, ('Edge01',))], [(self.view, ('Edge2048',))], [(self.view, ('Edge0\n',))],
                   [(self.view, ('Linked.Edge0',))], [(self.view, ('Face0',))],
                   [[self.view, ('Edge0',)]], [(self.view, ('Edge0',))] * 9]
        for references in invalid:
            dimension = Dimension(self.document); dimension.References2D = references
            self.assertEqual(refs.read_dimension(dimension)['status'], 'unsupported', references)
            self.assertEqual(dimension.calls, 0)
        for names in (('Face0',), ('Edge0',), ('Edge2049',), ('Linked.Edge1',), (), ('Edge1\n',)):
            dimension = Dimension(self.document); dimension.References3D = [(self.view, names)]
            self.assertEqual(refs.read_dimension(dimension)['status'], 'unsupported', names)
            self.assertEqual(dimension.calls, 0)
        dimension = Dimension(self.document)
        dimension.References2D = [(self.view, ('Vertex0', 'Vertex1'))]
        dimension.References3D = [(self.view, ('Edge1',))]
        self.assertEqual(refs.read_dimension(dimension)['value_mm'], 600.)
        self.assertEqual(dimension.calls, 1)

    def test_operations_never_reads_native_dimension_from_another_document_or_replacement(self):
        other = Document()
        other.Name = 'OtherNeutralDocument'
        foreign = Dimension(other)
        self.page.Views.append(foreign)
        params = {'pageName': self.page.Name, 'viewName': self.view.Name, 'limit': 1}
        with self.assertRaises(BridgeError) as error:
            ops.perform('inspect_drawing_page', params, self.app, self.gui, BridgeError)
        self.assertEqual(error.exception.code, 'DRAWING_REFERENCES_UNSUPPORTED')
        self.assertEqual(foreign.calls, 0)
        self.page.Views.remove(foreign)
        replaced = Dimension(self.document)
        self.page.Views.append(replaced)
        self.document.Objects.remove(replaced)
        Dimension(self.document)
        with self.assertRaises(BridgeError) as error:
            ops.perform('inspect_drawing_page', params, self.app, self.gui, BridgeError)
        self.assertEqual(error.exception.code, 'DRAWING_REFERENCES_UNSUPPORTED')
        self.assertEqual(replaced.calls, 0)

    def test_bridge_capability_matches_and_is_detached(self):
        bridge = load('pft_bridge')
        status = bridge.get_status()
        self.assertEqual(status['drawing_references'], refs.CAPABILITIES)
        self.assertIn('inspect_drawing_page', status['read_provenance']['methods'])
        status['drawing_references']['max_index'] = 5
        self.assertEqual(bridge.get_status()['drawing_references']['max_index'], 2047)
        status['drawing_dimensions']['kinds'].clear()
        self.assertGreater(len(bridge.get_status()['drawing_dimensions']['kinds']), 0)


if __name__ == '__main__':
    unittest.main()
