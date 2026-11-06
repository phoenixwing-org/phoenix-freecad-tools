# SPDX-License-Identifier: Apache-2.0
"""Synthetic associative-dimension lifecycle; no FreeCAD process or user files."""
import importlib.util
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import test_pft_drawing as fixtures
from test_pft_workbench_tools import bind_transaction_api

PATH = Path(__file__).resolve().parents[2] / 'addon/PhoenixFreeCADTools/pft_drawing_dimensions.py'
spec = importlib.util.spec_from_file_location('pft_dimensions_backend_test', PATH)
dimensions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dimensions)
controller = fixtures.controller
NS = types.SimpleNamespace


class Vector:
    def __init__(self, x=0., y=0., z=0.):
        self.x, self.y, self.z = float(x), float(y), float(z)


class Vertex:
    def __init__(self, point): self.Point = point


class Line:
    pass


class Circle:
    def __init__(self, radius=4., center=None):
        self.Radius, self.Center = float(radius), center or Vector()


class Edge:
    def __init__(self, start=(0., 0., 0.), end=(3., 4., 0.), radius=None):
        self.Curve = Line() if radius is None else Circle(radius)
        self.Vertexes = [Vertex(Vector(*start)), Vertex(Vector(*end))] if radius is None else []
        self.Length = (math.dist(start, end) if radius is None else 2 * math.pi * radius)
        self.closed = radius is not None

    def isClosed(self): return self.closed


class ProjectedView(fixtures.NativeObject):
    def __init__(self, name, source):
        super().__init__(name, 'TechDraw::DrawViewPart')
        self.Source, self.CoarseView = [source], False
        self.Direction, self.XDirection = Vector(0., 0., 1.), Vector(1., 0., 0.)
        self.Rotation, self.ScaleType, self.Scale, self.X, self.Y = 0., 'Custom', .1, 85., 100.
        self.edges = {'Edge0': Edge(), 'Edge4': Edge(radius=4.)}
        self.PropertiesList += ['Perspective', 'HardHidden', 'SmoothVisible', 'SmoothHidden',
                                'SeamVisible', 'SeamHidden', 'IsoVisible', 'IsoHidden']
        for key in self.PropertiesList[2:]: setattr(self, key, False)
        self.PropertiesList += ['CosmeticEdges', 'CenterLines']
        self.CosmeticEdges, self.CenterLines = [], []
        self.types = {'CosmeticEdges': 'TechDraw::PropertyCosmeticEdgeList',
                      'CenterLines': 'TechDraw::PropertyCenterLineList'}
        self.edge_reads = []

    def getTypeIdOfProperty(self, field): return self.types[field]

    def getEdgeBySelection(self, name):
        self.edge_reads.append(name)
        if name not in self.edges: raise ValueError('missing projected edge')
        return self.edges[name]


class Dimension(fixtures.NativeObject):
    def __init__(self, name):
        super().__init__(name, 'TechDraw::DrawViewDimension')
        self.State = ['Touched']
        self.Type, self.MeasureType, self.Arbitrary = 'Distance', 'True', True
        self.References2D, self.References3D = [], []
        self.FormatSpec, self.X, self.Y = 'untrusted preference text', 0., 0.
        self.raw_override = None

    def getRawValue(self):
        if self.raw_override is not None: return self.raw_override
        if len(self.References2D) != 1: return 0.
        view, names = self.References2D[0]
        edge = view.getEdgeBySelection(names[0] if not isinstance(names, str) else names)
        if self.Type in ('Radius', 'Diameter'):
            return edge.Curve.Radius * (2 if self.Type == 'Diameter' else 1)
        left, right = (vertex.Point for vertex in edge.Vertexes)
        if self.Type == 'DistanceX': return abs(right.x - left.x)
        if self.Type == 'DistanceY': return abs(right.y - left.y)
        return math.dist((left.x, left.y, left.z), (right.x, right.y, right.z))


class Document(fixtures.Document):
    def addObject(self, kind, name):
        if kind != 'TechDraw::DrawViewDimension': raise AssertionError('only new dimension nodes')
        self.HasPendingTransaction = True
        self.created_calls.append((kind, name))
        obj = Dimension(name)
        obj.Document = self
        self.Objects.append(obj)
        if self.add_hook: self.add_hook(obj)
        return obj

    def recompute(self, objects, force, has_error):
        self.recompute_calls.append((objects[:], force, has_error))
        for obj in objects: obj.State = ['Up-to-date']
        if self.recompute_hook: self.recompute_hook(objects)
        return self.recompute_result


class DrawingDimensionsTests(unittest.TestCase):
    def setUp(self):
        self.source = fixtures.NativeObject('PublicSolid')
        self.doc = Document(self.source)
        self.page = fixtures.NativeObject('DrawingPage', 'TechDraw::DrawPage')
        self.template = fixtures.NativeObject('DrawingTemplate', 'TechDraw::DrawSVGTemplate')
        self.view = ProjectedView('TopView', self.source)
        self.page.Template, self.page.Views = self.template, [self.view]
        self.page.Scale, self.page.ProjectionType = .1, 'First angle'
        self.template.Template, self.template.EditableTexts = 'installed-template.svg', {'title': 'Neutral sample'}
        self.template.PropertiesList += ['Width', 'Height', 'Orientation', 'PageResult', 'ExpressionEngine']
        self.template.Width, self.template.Height, self.template.Orientation = 297., 210., 'Landscape'
        self.template.PageResult = 'native-embedded-template.svg'
        self.template.InList, self.template.ExpressionEngine = [self.page], []
        self.template.types = {'PageResult': 'App::PropertyFileIncluded'}
        self.template.getTypeIdOfProperty = self.template.types.__getitem__
        for obj in (self.page, self.template, self.view):
            obj.Document = self.doc
            self.doc.Objects.append(obj)
        def add_view(obj):
            self.page.Views.append(obj)
            self.page.State = ['Touched']
            self.doc.HasPendingTransaction = True
        self.page.addView = add_view
        self.app = NS(ActiveDocument=self.doc, Vector=Vector,
                      getDocument=lambda name: self.doc if name == self.doc.Name else None,
                      getActiveTransaction=lambda: self.doc.active_transaction)
        bind_transaction_api(self.app, self.doc, counter_name='transaction_serial')
        self.edit = None
        self.gui = NS(getDocument=lambda name: NS(getInEdit=lambda: self.edit))
        self.request = [{'edge': 'Edge0', 'kind': 'Distance', 'x_mm': -12., 'y_mm': 20.}]
        self.part = NS(Edge=Edge, Vertex=Vertex, Line=Line, Circle=Circle)
        self.native = patch.dict(sys.modules, {'Part': self.part, 'FreeCAD': NS(Vector=Vector)})
        self.native.start()
        self.addCleanup(self.native.stop)

    def prepare(self, rows=None):
        return dimensions.prepare_plan(self.app, self.gui, self.doc, self.page, self.view,
                                       self.request if rows is None else rows, controller)

    def apply(self, plan):
        return dimensions.apply_plan(self.app, self.gui, self.doc, plan, controller)

    def error(self, callback, code, cls=None):
        cls = dimensions.PftDrawingDimensionsError if cls is None else cls
        with self.assertRaises(cls) as failure: callback()
        self.assertEqual(failure.exception.code, code)
        self.assertNotIn('untrusted synthetic diagnostic', str(failure.exception))
        return failure.exception

    def parent(self, child=None, name='Container'):
        child = self.source if child is None else child
        parent = fixtures.NativeObject(name, 'App::Part')
        parent.Document = self.doc
        parent.Group = [child]
        child.InList.append(parent)
        self.doc.Objects.append(parent)
        return parent

    def test_preview_reads_real_projected_reference_without_writes_or_recompute(self):
        objects, source_shape = self.doc.Objects[:], self.source.Shape
        plan, rows = self.prepare()
        self.assertEqual(plan['values'], (5.,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['value_mm'], 5.)
        self.assertEqual(rows[0]['source'], 'native_cached_projected_geometry')
        self.assertEqual(self.doc.Objects, objects)
        self.assertIs(self.source.Shape, source_shape)
        self.assertEqual(self.doc.events, [])
        self.assertEqual(self.doc.created_calls, [])
        self.assertEqual(self.doc.recompute_calls, [])
        self.assertEqual(self.page.Views, [self.view])
        self.assertTrue(self.view.edge_reads)

    def test_each_supported_kind_has_unscaled_mm_value_and_one_native_reference(self):
        rows = [dict(self.request[0], kind=kind) for kind in ('Distance', 'DistanceX', 'DistanceY')]
        rows += [dict(self.request[0], edge='Edge4', kind=kind) for kind in ('Diameter', 'Radius')]
        plan, unused = self.prepare(rows)
        self.assertEqual(plan['values'], (5., 3., 4., 8., 4.))
        source_shape = self.source.Shape
        result = self.apply(plan)
        created = self.doc.Objects[-5:]
        self.assertEqual([row['value_mm'] for row in result['dimensions']], [5., 3., 4., 8., 4.])
        self.assertEqual([dim.FormatSpec for dim in created], ['%.2f', '%.2f', '%.2f', '⌀%.2f', 'R%.2f'])
        self.assertTrue(all(dim.Arbitrary is False and dim.MeasureType == 'Projected' for dim in created))
        self.assertEqual([dim.References2D for dim in created],
                         [[(self.view, (row['edge'],))] for row in rows])
        self.assertEqual(self.doc.events, ['open', 'commit'])
        self.assertEqual(self.doc.recompute_calls, [([self.page, *created], False, True)])
        self.assertIs(self.source.Shape, source_shape)
        self.assertEqual(result['projection_verification'], 'not_checked')
        self.assertTrue(result['manual_review_required'])

    def test_page_touched_by_addview_is_allowed_until_bounded_recompute(self):
        plan, unused = self.prepare()
        result = self.apply(plan)
        self.assertEqual(self.page.State, ['Up-to-date'])
        self.assertEqual(len(result['dimensions']), 1)
        self.assertEqual(self.page.Views, [self.view, self.doc.Objects[-1]])
        self.assertEqual(self.doc.events, ['open', 'commit'])

    def test_opened_page_template_touched_is_read_without_writes_and_kept_out_of_created_roots(self):
        self.template.State = ['Touched']
        before = self.doc.Objects[:]
        plan, unused = self.prepare()
        self.assertEqual(self.doc.Objects, before)
        self.assertEqual(self.template.State, ['Touched'])
        self.assertEqual(self.doc.events, [])
        self.assertEqual(self.doc.recompute_calls, [])
        result = self.apply(plan)
        self.assertEqual(len(result['dimensions']), 1)
        self.assertEqual(self.doc.events, ['open', 'commit'])
        self.assertEqual(self.doc.recompute_calls[0][0], [self.page, self.doc.Objects[-1]])
        self.assertNotIn(self.template, self.doc.recompute_calls[0][0])
        self.assertEqual(self.template.State, ['Touched'])
        self.assertIs(self.page.Template, self.template)

    def test_only_owned_successful_recompute_may_clean_the_original_template_flag(self):
        for flags in (['Touched'], ['Expanded', 'Touched']):
            with self.subTest(flags=flags):
                self.setUp(); self.template.State = flags[:]
                plan, unused = self.prepare()
                frozen = plan['snapshot']['page_state']
                clean = [flag for flag in flags if flag != 'Touched'] + ['Up-to-date']
                self.doc.recompute_hook = lambda objects: setattr(self.template, 'State', clean[:])
                result = self.apply(plan)
                self.assertEqual(result['dimensions'][0]['value_mm'], 5.)
                self.assertEqual(self.doc.events, ['open', 'commit'])
                self.assertEqual(self.template.State, clean)
                self.assertEqual(plan['snapshot']['page_state'], frozen)
                self.assertEqual(frozen[-1], tuple(flags))
                self.assertNotIn(self.template, self.doc.recompute_calls[0][0])
                self.assertEqual(result['projection_verification'], 'not_checked')

    def test_template_cache_transition_permission_is_narrow_and_defaults_to_strict(self):
        self.template.State = ['Expanded', 'Touched']
        plan, unused = self.prepare()
        original = plan['snapshot']
        def captured(flags, field_change=False):
            page = original['page_state'][:-1] + (tuple(flags),)
            if field_change: page = page[:5] + (page[5] + 1.,) + page[6:]
            return dict(original, page_state=page)
        current = captured(['Up-to-date', 'Expanded'])
        self.assertFalse(dimensions._same_capture(current, original, ()))
        self.assertFalse(dimensions._same_capture(current, original, (), 1))
        self.assertTrue(dimensions._same_capture(current, original, (), True))
        for flags in (['Up-to-date'], ['Up-to-date', 'Expanded', 'Expanded'],
                      ['Up-to-date', 'Expanded', 'Touched'], ['Expanded'],
                      ['Up-to-date', 'Expanded', 'Invalid'], ['Up-to-date', 'Up-to-date', 'Expanded']):
            with self.subTest(flags=flags):
                self.assertFalse(dimensions._same_capture(captured(flags), original, (), True))
        self.assertFalse(dimensions._same_capture(captured(['Up-to-date', 'Expanded'], True),
                                                 original, (), True))
        clean_original = captured(['Up-to-date', 'Expanded'])
        self.assertFalse(dimensions._same_capture(original, clean_original, (), True))

    def test_template_cleaned_before_recompute_is_stale_and_is_not_automatically_retried(self):
        self.template.State = ['Touched']
        plan, unused = self.prepare()
        self.doc.add_hook = lambda obj: setattr(self.template, 'State', ['Up-to-date'])
        self.error(lambda: self.apply(plan), 'DIMENSION_PLAN_STALE')
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.assertEqual(self.doc.recompute_calls, [])
        self.assertEqual(self.template.State, ['Touched'])
        self.assertEqual(self.page.Views, [self.view])

    def test_recompute_template_cleanup_cannot_override_lost_transaction_ownership(self):
        self.template.State = ['Touched']
        plan, unused = self.prepare()
        def replace_owner(objects):
            self.template.State = ['Up-to-date']
            self.doc.active_transaction = ('foreign', 900)
        self.doc.recompute_hook = replace_owner
        self.error(lambda: self.apply(plan), 'PART_TRANSACTION_CHANGED', controller.PftWriteError)
        self.assertEqual(self.doc.events, ['open'])
        self.assertEqual(self.doc.active_transaction, ('foreign', 900))

    def test_owned_recompute_template_cleanup_does_not_allow_any_other_template_change(self):
        for field, value in (('Width', 298.), ('Template', 'changed-template.svg'),
                             ('PageResult', 'changed-embedded-template.svg'),
                             ('State', ['Up-to-date', 'Expanded'])):
            with self.subTest(field=field):
                self.setUp(); self.template.State = ['Touched']
                plan, unused = self.prepare()
                def change(objects):
                    self.template.State = ['Up-to-date']
                    setattr(self.template, field, value)
                self.doc.recompute_hook = change
                self.error(lambda: self.apply(plan), 'DIMENSION_PLAN_STALE')
                self.assertEqual(self.doc.events, ['open', 'abort'])
                self.assertEqual(self.template.State, ['Touched'])
                self.assertEqual(self.page.Views, [self.view])

    def test_invalid_recompute_count_does_not_relax_rollback_template_verification(self):
        self.template.State = ['Touched']
        plan, unused = self.prepare()
        self.doc.recompute_result = None
        self.doc.recompute_hook = lambda objects: setattr(self.template, 'State', ['Up-to-date'])
        original_abort = self.doc.abortTransaction
        def abort_without_restoring_cache_flag():
            original_abort()
            self.template.State = ['Up-to-date']
        self.doc.abortTransaction = abort_without_restoring_cache_flag
        self.error(lambda: self.apply(plan), 'PART_ROLLBACK_UNVERIFIED', controller.PftWriteError)
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.assertEqual(self.page.Views, [self.view])

    def test_touched_template_rollback_restores_only_own_new_nodes_and_original_metadata(self):
        self.template.State = ['Touched']
        plan, unused = self.prepare()
        before = self.doc.Objects[:]
        self.doc.recompute_hook = lambda objects: setattr(objects[-1], 'raw_override', 99.)
        self.error(lambda: self.apply(plan), 'DIMENSION_VALUE_UNVERIFIED')
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.assertEqual(self.doc.Objects, before)
        self.assertEqual(self.template.State, ['Touched'])
        self.assertEqual(self.template.PageResult, 'native-embedded-template.svg')
        self.assertEqual(self.page.Views, [self.view])

    def test_touched_template_does_not_allow_dirty_source_page_view_or_unrelated_template(self):
        for target in ('source', 'page', 'view', 'other_template', 'other_object'):
            with self.subTest(target=target):
                self.setUp(); self.template.State = ['Touched']
                if target in ('source', 'page', 'view'): obj = getattr(self, target)
                else:
                    obj = fixtures.NativeObject('OtherNode',
                        'TechDraw::DrawSVGTemplate' if target == 'other_template' else 'Part::Feature')
                    obj.Document = self.doc; self.doc.Objects.append(obj)
                obj.State = ['Touched']
                self.error(self.prepare, 'DIMENSION_UNSUPPORTED')
                self.assertEqual(self.doc.events, [])

    def test_template_mixed_error_or_recompute_state_is_rejected_without_clearing_flags(self):
        for state in ('Invalid', 'Error', 'Recompute', 'Recompute2', 'Restore'):
            self.template.State = ['Touched', state]
            self.error(self.prepare, 'DIMENSION_UNSUPPORTED')
            self.assertEqual(self.template.State, ['Touched', state])
            self.assertEqual(self.doc.events, [])

    def test_touched_template_fields_identity_properties_and_state_are_frozen(self):
        for change in ('path', 'text', 'width', 'height', 'orientation', 'page_result', 'properties',
                       'owners', 'expressions', 'replacement', 'state'):
            with self.subTest(change=change):
                self.setUp(); self.template.State = ['Touched']
                plan, unused = self.prepare()
                if change == 'path': self.template.Template = 'changed-template.svg'
                elif change == 'text': self.template.EditableTexts['title'] = 'Changed neutral title'
                elif change == 'width': self.template.Width += 1.
                elif change == 'height': self.template.Height += 1.
                elif change == 'orientation': self.template.Orientation = 'Portrait'
                elif change == 'page_result': self.template.PageResult = 'different-embedded-template.svg'
                elif change == 'properties': self.template.PropertiesList.append('ExtraNativeField')
                elif change == 'owners': self.template.InList = []
                elif change == 'expressions': self.template.ExpressionEngine = [('Width', '1 + 1')]
                elif change == 'state': self.template.State = ['Up-to-date']
                else:
                    replacement = fixtures.NativeObject(self.template.Name, self.template.TypeId)
                    replacement.__dict__.update(self.template.__dict__)
                    self.doc.Objects[self.doc.Objects.index(self.template)] = replacement
                    self.page.Template = replacement
                self.error(lambda: self.apply(plan), 'DIMENSION_PLAN_STALE')
                self.assertEqual(self.doc.events, [])
                self.assertEqual(self.doc.created_calls, [])

    def test_template_invalid_resources_consumers_or_expressions_are_not_readable(self):
        for change in ('unknown_file_type', 'missing_file_type', 'empty_file', 'long_file',
                       'nonstring_file', 'invalid_width', 'invalid_height', 'unknown_orientation',
                       'unknown_owner', 'no_owner', 'two_owners', 'expressions'):
            with self.subTest(change=change):
                self.setUp(); self.template.State = ['Touched']
                if change == 'unknown_file_type': self.template.types['PageResult'] = 'App::PropertyString'
                elif change == 'missing_file_type': self.template.PropertiesList.remove('PageResult')
                elif change == 'empty_file': self.template.PageResult = ''
                elif change == 'long_file': self.template.PageResult = 'x' * 4097
                elif change == 'nonstring_file': self.template.PageResult = object()
                elif change == 'invalid_width': self.template.Width = float('nan')
                elif change == 'invalid_height': self.template.Height = -1.
                elif change == 'unknown_orientation': self.template.Orientation = 'Unknown'
                elif change == 'unknown_owner': self.template.InList = [self.source]
                elif change == 'no_owner': self.template.InList = []
                elif change == 'two_owners': self.template.InList.append(self.page)
                else: self.template.ExpressionEngine = [('Width', '1 + 1')]
                self.error(self.prepare, 'DIMENSION_UNSUPPORTED')
                self.assertEqual(self.doc.events, [])

    def test_unknown_page_result_type_never_invokes_the_resource_getter(self):
        self.template.State = ['Touched']
        self.template.types['PageResult'] = 'App::PropertyString'
        reads = []
        def resource(obj): reads.append(obj); raise AssertionError('must not read unknown embedded type')
        with patch.object(fixtures.NativeObject, 'PageResult', property(resource), create=True):
            self.error(self.prepare, 'DIMENSION_UNSUPPORTED')
        self.assertEqual(reads, [])

    def test_changed_source_geometry_or_same_geometry_new_shape_is_stale(self):
        for change in ('volume', 'bounds', 'identity', 'body_tip'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'body_tip':
                    self.source.TypeId = 'PartDesign::Body'
                    tip = fixtures.NativeObject('Pocket', 'PartDesign::Pocket')
                    tip.Document = self.doc
                    self.source.Tip, self.source.Group = tip, [tip]
                    self.doc.Objects.append(tip)
                plan, unused = self.prepare()
                if change == 'volume': self.source.Shape.Volume += 1.
                elif change == 'bounds': self.source.Shape.dimensions = (61., 40., 8.)
                elif change == 'identity': self.source.Shape = fixtures.Shape()
                else:
                    replacement = fixtures.NativeObject('PocketReplacement', 'PartDesign::Pocket')
                    replacement.Document = self.doc
                    self.doc.Objects.append(replacement)
                    self.source.Tip, self.source.Group = replacement, [replacement]
                self.error(lambda: self.apply(plan), 'DIMENSION_PLAN_STALE')
                self.assertEqual(self.doc.created_calls, [])
                self.assertEqual(self.doc.events, [])

    def test_parent_placement_membership_and_same_placement_reparent_are_stale(self):
        for change in ('placement', 'members', 'reparent'):
            with self.subTest(change=change):
                self.setUp()
                parent = self.parent()
                plan, unused = self.prepare()
                if change == 'placement': parent.Placement.Base.x += 1.
                elif change == 'members': parent.Group = []
                else:
                    parent.Group = []
                    self.parent(name='DifferentParent')
                self.error(lambda: self.apply(plan), 'DIMENSION_PLAN_STALE')
                self.assertEqual(self.doc.events, [])

    def test_view_direction_scale_rotation_center_and_hlr_flags_are_frozen(self):
        for field, replacement in (('Direction', Vector(1., 0., 0.)), ('XDirection', Vector(0., 1., 0.)),
                                   ('Scale', .2), ('Rotation', 90.), ('X', 86.), ('Y', 101.),
                                   ('HardHidden', True), ('CoarseView', True)):
            with self.subTest(field=field):
                self.setUp()
                plan, unused = self.prepare()
                setattr(self.view, field, replacement)
                self.error(lambda: self.apply(plan), 'DIMENSION_PLAN_STALE')
                self.assertEqual(self.doc.events, [])

    def test_projection_edge_geometry_movement_reindex_or_missing_is_stale(self):
        for change in ('end', 'length', 'replace', 'missing', 'circle_center', 'circle_radius'):
            with self.subTest(change=change):
                self.setUp()
                rows = [dict(self.request[0], edge='Edge4', kind='Diameter')] if change.startswith('circle') else self.request
                plan, unused = self.prepare(rows)
                edge = self.view.edges['Edge4' if change.startswith('circle') else 'Edge0']
                if change == 'end': edge.Vertexes[1].Point.x += 1.
                elif change == 'length': edge.Length += 1.
                elif change == 'replace': self.view.edges['Edge0'] = Edge(end=(4., 3., 0.))
                elif change == 'missing': self.view.edges.pop('Edge0')
                elif change == 'circle_center': edge.Curve.Center.x += 1.
                else: edge.Curve.Radius += 1.
                self.error(lambda: self.apply(plan), 'DIMENSION_PLAN_STALE')
                self.assertEqual(self.doc.events, [])

    def test_page_members_template_and_existing_dimension_changes_are_stale(self):
        for change in ('members', 'template', 'text', 'existing_dimension'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'existing_dimension':
                    old = Dimension('ExistingDimension'); old.State = ['Up-to-date']; old.Document = self.doc
                    old.References2D = [(self.view, ('Edge0',))]
                    old.MeasureType, old.Arbitrary = 'Projected', False
                    self.page.Views.append(old); self.doc.Objects.append(old)
                plan, unused = self.prepare()
                if change == 'members': self.page.Views = []
                elif change == 'template': self.template.Template = 'another-installed-template.svg'
                elif change == 'text': self.template.EditableTexts['title'] = 'Changed neutral title'
                else: old.X += 1.
                self.error(lambda: self.apply(plan), 'DIMENSION_PLAN_STALE')
                self.assertEqual(self.doc.events, [])

    def test_existing_dimension_rejects_out_of_scope_or_unsupported_refs_before_native_value(self):
        for change in ('foreign_2d', 'foreign_3d', 'unregistered_2d', 'outside_page', 'missing_2d',
                       'two_edges', 'vertex', 'leading_zero', 'out_of_range', 'wrong_curve',
                       'unknown_type', 'true_measurement', 'arbitrary'):
            with self.subTest(change=change):
                self.setUp()
                existing = Dimension('ExistingDimension')
                existing.Document, existing.State = self.doc, ['Up-to-date']
                existing.MeasureType, existing.Arbitrary = 'Projected', False
                existing.References2D = [(self.view, ('Edge0',))]
                raw_reads = []
                def raw(): raw_reads.append(True); return 5.
                existing.getRawValue = raw
                self.doc.Objects.append(existing); self.page.Views.append(existing)
                if change in ('foreign_2d', 'foreign_3d'):
                    foreign_source = fixtures.NativeObject('ForeignSolid')
                    foreign_document = Document(foreign_source)
                    foreign_document.Name = 'OtherDocument'
                    foreign_view = ProjectedView('ForeignView', foreign_source)
                    foreign_view.Document = foreign_document
                    foreign_document.Objects.append(foreign_view)
                    if change == 'foreign_2d': existing.References2D = [(foreign_view, ('Edge0',))]
                    else: existing.References3D = [(foreign_source, ('Edge1',))]
                elif change in ('unregistered_2d', 'outside_page'):
                    other_view = ProjectedView('OtherView', self.source); other_view.Document = self.doc
                    if change == 'outside_page': self.doc.Objects.append(other_view)
                    existing.References2D = [(other_view, ('Edge0',))]
                elif change == 'missing_2d': existing.References2D = []
                elif change == 'two_edges': existing.References2D = [(self.view, ('Edge0', 'Edge4'))]
                elif change == 'vertex': existing.References2D = [(self.view, ('Vertex0',))]
                elif change == 'leading_zero': existing.References2D = [(self.view, ('Edge00',))]
                elif change == 'out_of_range': existing.References2D = [(self.view, ('Edge2048',))]
                elif change == 'wrong_curve': existing.References2D = [(self.view, ('Edge4',))]
                elif change == 'unknown_type': existing.Type = 'Angle'
                elif change == 'true_measurement': existing.MeasureType = 'True'
                else: existing.Arbitrary = True
                self.error(self.prepare, 'DIMENSION_UNSUPPORTED')
                self.assertEqual(raw_reads, [])
                self.assertEqual(self.doc.events, [])
                self.assertEqual(self.doc.created_calls, [])

    def test_supported_existing_dimension_is_preserved_while_new_dimensions_are_added(self):
        existing = Dimension('ExistingDiameter')
        existing.Document, existing.State = self.doc, ['Up-to-date']
        existing.Type, existing.MeasureType, existing.Arbitrary = 'Diameter', 'Projected', False
        existing.References2D = [(self.view, ('Edge4',))]
        existing.FormatSpec, existing.X, existing.Y = '⌀%.1f', 15., 10.
        self.doc.Objects.append(existing); self.page.Views.append(existing)
        plan, unused = self.prepare()
        result = self.apply(plan)
        self.assertEqual(len(result['dimensions']), 1)
        self.assertEqual(self.page.Views[:2], [self.view, existing])
        self.assertEqual((existing.getRawValue(), existing.FormatSpec, existing.X, existing.Y),
                         (8., '⌀%.1f', 15., 10.))
        self.assertEqual(existing.References2D, [(self.view, ('Edge4',))])
        self.assertEqual(self.doc.events, ['open', 'commit'])

    def test_page_must_have_only_supported_views_of_the_same_source(self):
        other = fixtures.NativeObject('OtherSolid'); other.Document = self.doc; self.doc.Objects.append(other)
        second = ProjectedView('OtherView', other); second.Document = self.doc
        self.doc.Objects.append(second); self.page.Views.append(second)
        self.error(self.prepare, 'DIMENSION_UNSUPPORTED')
        self.assertEqual(self.doc.events, [])

    def test_extra_xsource_is_rejected_and_later_xsource_change_is_stale(self):
        for initially_present in (True, False):
            with self.subTest(initially_present=initially_present):
                self.setUp()
                other = fixtures.NativeObject('OtherProjectionSource')
                other.Document = self.doc
                self.doc.Objects.append(other)
                self.view.PropertiesList.append('XSource')
                self.view.XSource = [other] if initially_present else []
                if initially_present:
                    self.error(self.prepare, 'DIMENSION_UNSUPPORTED')
                else:
                    plan, unused = self.prepare()
                    self.view.XSource = [other]
                    self.error(lambda: self.apply(plan), 'DIMENSION_PLAN_STALE')
                self.assertEqual(self.doc.events, [])
                self.assertEqual(self.doc.created_calls, [])

    def test_cosmetic_edges_and_centerlines_require_empty_exact_native_lists(self):
        for field in ('CosmeticEdges', 'CenterLines'):
            for change in ('nonempty', 'missing', 'type', 'tuple'):
                with self.subTest(field=field, change=change):
                    self.setUp()
                    if change == 'nonempty': setattr(self.view, field, [object()])
                    elif change == 'missing': self.view.PropertiesList.remove(field)
                    elif change == 'type': self.view.types[field] = 'App::PropertyString'
                    else: setattr(self.view, field, ())
                    self.error(self.prepare, 'DIMENSION_UNSUPPORTED')
                    self.assertEqual(self.doc.events, [])
                    self.assertEqual(self.doc.created_calls, [])

    def test_new_cosmetic_edge_or_centerline_after_preview_is_stale(self):
        for field in ('CosmeticEdges', 'CenterLines'):
            with self.subTest(field=field):
                self.setUp(); plan, unused = self.prepare()
                setattr(self.view, field, [object()])
                self.error(lambda: self.apply(plan), 'DIMENSION_PLAN_STALE')
                self.assertEqual(self.doc.events, [])
                self.assertEqual(self.doc.created_calls, [])

    def test_new_name_collision_and_original_view_replacement_are_stale(self):
        for change in ('collision', 'replacement'):
            with self.subTest(change=change):
                self.setUp(); plan, unused = self.prepare()
                if change == 'collision':
                    collision = fixtures.NativeObject(plan['names'][0]); collision.Document = self.doc
                    self.doc.Objects.append(collision)
                else:
                    replacement = ProjectedView(self.view.Name, self.source); replacement.Document = self.doc
                    self.doc.Objects[self.doc.Objects.index(self.view)] = replacement
                    self.page.Views = [replacement]
                self.error(lambda: self.apply(plan), 'DIMENSION_PLAN_STALE')
                self.assertEqual(self.doc.events, [])

    def test_missing_projection_or_wrong_curve_type_or_zero_axis_has_zero_writes(self):
        for change in ('missing', 'circle', 'zero_x', 'arc', 'proxy'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'missing': self.view.edges.clear()
                elif change == 'circle': self.request[0]['edge'] = 'Edge4'
                elif change == 'zero_x':
                    self.view.edges['Edge0'] = Edge(end=(0., 5., 0.)); self.request[0]['kind'] = 'DistanceX'
                elif change == 'arc':
                    self.view.edges['Edge4'].closed = False
                    self.request[0].update(edge='Edge4', kind='Radius')
                else: self.view.Proxy = object()
                self.error(self.prepare, 'DIMENSION_UNSUPPORTED')
                self.assertEqual(self.doc.events, [])

    def test_wrong_raw_value_aborts_once_and_restores_page_and_source(self):
        plan, unused = self.prepare()
        before = self.doc.Objects[:]
        self.doc.recompute_hook = lambda objects: setattr(objects[-1], 'raw_override', 99.)
        self.error(lambda: self.apply(plan), 'DIMENSION_VALUE_UNVERIFIED')
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.assertEqual(self.doc.Objects, before)
        self.assertEqual(self.page.Views, [self.view])
        self.assertEqual(self.page.State, ['Up-to-date'])

    def test_result_reports_actual_postcommit_native_value_within_comparison_tolerance(self):
        plan, unused = self.prepare()
        before_commit, after_commit = 5. + 1e-7, 5. + 2e-7
        self.doc.recompute_hook = lambda objects: setattr(objects[-1], 'raw_override', before_commit)
        self.doc.commit_hook = lambda: setattr(self.doc.Objects[-1], 'raw_override', after_commit)
        result = self.apply(plan)
        self.assertEqual(plan['values'], (5.,))
        self.assertEqual(result['dimensions'][0]['value_mm'], after_commit)
        self.assertNotEqual(result['dimensions'][0]['value_mm'], before_commit)
        self.assertNotEqual(result['dimensions'][0]['value_mm'], plan['values'][0])
        self.assertEqual(self.doc.events, ['open', 'commit'])

    def test_source_change_during_native_value_read_is_detected_before_commit(self):
        plan, unused = self.prepare()
        source_shape = self.source.Shape
        def instrument(dim):
            original = dim.getRawValue
            def read():
                value = original()
                self.source.Shape = fixtures.Shape(61., 40., 8.)
                return value
            dim.getRawValue = read
        self.doc.add_hook = instrument
        self.error(lambda: self.apply(plan), 'DIMENSION_PLAN_STALE')
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.assertIs(self.source.Shape, source_shape)
        self.assertEqual(self.page.Views, [self.view])

    def test_zero_or_negative_native_value_never_passes_absolute_tolerance_for_a_small_edge(self):
        for actual in (0., -1e-7):
            with self.subTest(actual=actual):
                self.setUp()
                self.view.edges['Edge0'] = Edge(end=(5e-7, 0., 0.))
                plan, unused = self.prepare()
                self.assertEqual(plan['values'], (5e-7,))
                self.doc.recompute_hook = lambda objects: setattr(objects[-1], 'raw_override', actual)
                self.error(lambda: self.apply(plan), 'DIMENSION_VALUE_UNVERIFIED')
                self.assertEqual(self.doc.events, ['open', 'abort'])
                self.assertEqual(self.page.Views, [self.view])

    def test_auto_repaired_reference_wrong_type_position_or_format_aborts(self):
        for change in ('reference', 'type', 'x', 'y', 'format', 'arbitrary', 'measure_type', '3d'):
            with self.subTest(change=change):
                self.setUp(); plan, unused = self.prepare()
                def corrupt(objects):
                    dim = objects[-1]
                    if change == 'reference': dim.References2D = [(self.view, ('Edge4',))]
                    elif change == 'type': dim.Type = 'DistanceX'
                    elif change == 'x': dim.X += 1.
                    elif change == 'y': dim.Y += 1.
                    elif change == 'format': dim.FormatSpec = '777'
                    elif change == 'arbitrary': dim.Arbitrary = True
                    elif change == 'measure_type': dim.MeasureType = 'True'
                    else: dim.References3D = [(self.source, ('Edge1',))]
                    dim.raw_override = 5.  # A plausible scalar does not repair an incorrect reference.
                self.doc.recompute_hook = corrupt
                self.error(lambda: self.apply(plan), 'DIMENSION_CREATE_FAILED')
                self.assertEqual(self.doc.events, ['open', 'abort'])
                self.assertEqual(self.page.Views, [self.view])

    def test_recompute_exception_or_unknown_result_aborts_once(self):
        for result in (True, None, -1, '1', 2 ** 54, 'raise'):
            with self.subTest(result=result):
                self.setUp(); plan, unused = self.prepare()
                if result == 'raise':
                    def fail(objects): raise RuntimeError('untrusted synthetic diagnostic')
                    self.doc.recompute_hook = fail
                else: self.doc.recompute_result = result
                self.error(lambda: self.apply(plan), 'DIMENSION_RECOMPUTE_UNVERIFIED')
                self.assertEqual(self.doc.events, ['open', 'abort'])
                self.assertEqual(self.page.Views, [self.view])

    def test_rollback_uncertainty_does_not_retry_or_close_again(self):
        for change in ('not_closed', 'raised_after_close'):
            with self.subTest(change=change):
                self.setUp(); plan, unused = self.prepare()
                self.doc.recompute_hook = lambda objects: setattr(objects[-1], 'raw_override', 99.)
                original = self.doc.abortTransaction
                if change == 'not_closed':
                    self.doc.abortTransaction = lambda: self.doc.events.append('abort')
                else:
                    def abort():
                        original()
                        raise RuntimeError('untrusted synthetic diagnostic')
                    self.doc.abortTransaction = abort
                self.error(lambda: self.apply(plan), 'PART_ROLLBACK_UNVERIFIED', controller.PftWriteError)
                self.assertEqual(self.doc.events, ['open', 'abort'])

    def test_commit_no_close_or_raising_after_close_is_unknown_without_abort(self):
        for change in ('not_closed', 'raised_after_close', 'foreign_after_close'):
            with self.subTest(change=change):
                self.setUp(); plan, unused = self.prepare()
                original = self.doc.commitTransaction
                if change == 'not_closed': self.doc.commitTransaction = lambda: self.doc.events.append('commit')
                else:
                    def close():
                        original()
                        if change == 'foreign_after_close': self.doc.active_transaction = ('foreign', 900)
                        else: raise RuntimeError('untrusted synthetic diagnostic')
                    self.doc.commitTransaction = close
                self.error(lambda: self.apply(plan), 'PART_COMMIT_UNVERIFIED', controller.PftWriteError)
                self.assertEqual(self.doc.events, ['open', 'commit'])
                self.assertEqual(len(self.page.Views), 2)
                if change == 'foreign_after_close': self.assertEqual(self.doc.active_transaction, ('foreign', 900))

    def test_open_or_add_callback_cannot_claim_or_close_foreign_transaction(self):
        for phase in ('open', 'add'):
            with self.subTest(phase=phase):
                self.setUp(); plan, unused = self.prepare()
                if phase == 'open':
                    original = self.app.setActiveTransaction
                    def allocate(title):
                        allocated = original(title)
                        self.doc.active_transaction = (title, 900)
                        return allocated
                    self.app.setActiveTransaction = allocate
                else:
                    self.doc.add_hook = lambda obj: setattr(self.doc, 'active_transaction', ('foreign', 900))
                self.error(lambda: self.apply(plan), 'PART_TRANSACTION_CHANGED', controller.PftWriteError)
                self.assertEqual(self.doc.events, ['open'])
                self.assertEqual(self.doc.active_transaction[1], 900)
                self.assertEqual(len(self.doc.created_calls), 0 if phase == 'open' else 1)

    def test_postcommit_ref_or_value_failure_is_unknown_and_does_not_abort(self):
        for change in ('reference', 'value'):
            with self.subTest(change=change):
                self.setUp(); plan, unused = self.prepare()
                def corrupt():
                    dim = self.doc.Objects[-1]
                    if change == 'reference': dim.References2D = []
                    else: dim.raw_override = 99.
                self.doc.commit_hook = corrupt
                self.error(lambda: self.apply(plan), 'PART_COMMIT_UNVERIFIED', controller.PftWriteError)
                self.assertEqual(self.doc.events, ['open', 'commit'])


if __name__ == '__main__': unittest.main()
