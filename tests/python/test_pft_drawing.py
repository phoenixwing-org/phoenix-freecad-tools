# SPDX-License-Identifier: Apache-2.0
"""Synthetic native API fixtures; never opens FreeCAD or user CAD files."""
import copy
import importlib.util
import math
import json
from pathlib import Path
import tempfile
import types
import unittest
from test_pft_workbench_tools import bind_transaction_api
from unittest.mock import patch

import test_pft_business as fixtures

controller = fixtures.business._mvc()[1]
spec = importlib.util.spec_from_file_location('pft_drawing_test_module', fixtures.ADDON / 'pft_drawing.py')
drawing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drawing)
NS = types.SimpleNamespace
SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="297mm" height="210mm"><text>synthetic</text></svg>'


def vector(x=0., y=0., z=0.):
    return NS(x=x, y=y, z=z)


def rotate(quaternion, xyz):
    x, y, z, w = quaternion
    vx, vy, vz = xyz
    tx, ty, tz = 2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx)
    return (vx + w * tx + y * tz - z * ty,
            vy + w * ty + z * tx - x * tz,
            vz + w * tz + x * ty - y * tx)


def placement(base=(0., 0., 0.), quaternion=(0., 0., 0., 1.)):
    return NS(Base=vector(*base), Rotation=NS(Q=quaternion))


def compose(parent, local):
    x, y, z, w = parent.Rotation.Q
    u, v, s, t = local.Rotation.Q
    quaternion = (w*u + x*t + y*s - z*v, w*v - x*s + y*t + z*u,
                  w*s + x*v - y*u + z*t, w*t - x*u - y*v - z*s)
    offset = rotate(parent.Rotation.Q, (local.Base.x, local.Base.y, local.Base.z))
    return placement(tuple(getattr(parent.Base, axis) + value for axis, value in zip(('x', 'y', 'z'), offset)),
                     quaternion)


class Shape:
    def __init__(self, x=60., y=40., z=8.):
        self.Volume, self.Area = x * y * z, 2 * (x * y + x * z + y * z)
        self.dimensions = x, y, z
        self.Placement, self.copies = placement(), []
        self.Solids, self.valid, self.null = [object()], True, False
        self.identity = object()

    @property
    def BoundBox(self):
        points = []
        for x in (0., self.dimensions[0]):
            for y in (0., self.dimensions[1]):
                for z in (0., self.dimensions[2]):
                    rotated = rotate(self.Placement.Rotation.Q, (x, y, z))
                    points.append(tuple(value + getattr(self.Placement.Base, axis)
                                        for value, axis in zip(rotated, ('x', 'y', 'z'))))
        return NS(**{axis + suffix: operation(point[index] for point in points)
                     for index, axis in enumerate(('X', 'Y', 'Z'))
                     for suffix, operation in (('Min', min), ('Max', max))})

    def copy(self):
        result = Shape(*self.dimensions)
        result.Volume, result.Area, result.Placement = self.Volume, self.Area, copy.deepcopy(self.Placement)
        result.Solids, result.valid, result.null = self.Solids[:], self.valid, self.null
        self.copies.append(result)
        return result

    def isSame(self, other): return self.identity is other.identity
    def isNull(self): return self.null
    def isValid(self): return self.valid


class NativeObject:
    def __init__(self, name, kind='Part::Feature'):
        self.Name, self.Label, self.TypeId = name, name, kind
        self.PropertiesList, self.State = ['Label', 'Placement'], ['Up-to-date']
        self.Placement = placement()
        self.Shape = Shape()
        self.InList, self.Group = [], []
        self.OutList, self.ExpressionEngine = [], []
        self.valid = True

    def isValid(self): return self.valid
    def hasExtension(self, name):
        return name == 'App::GeoFeatureGroupExtension' and self.TypeId in ('App::Part', 'PartDesign::Body')
    def getParentGeoFeatureGroup(self):
        return next((item for item in self.InList if item.hasExtension('App::GeoFeatureGroupExtension')
                     and any(child is self for child in item.Group)), None)
    def getGlobalPlacement(self):
        current, parents, seen = self, [], set()
        while current is not None:
            if id(current) in seen or len(parents) > 32:
                raise RuntimeError('synthetic cyclic placement')
            seen.add(id(current))
            parents.append(current.Placement)
            current = current.getParentGeoFeatureGroup()
        result = placement()
        for local in reversed(parents):
            result = compose(result, local)
        return result


class DraftNode:
    def __init__(self, name, kind):
        self.Name, self.TypeId, self.State = name, kind, ['Touched']
        self.PropertiesList, self.ExpressionEngine = ['ExpressionEngine'], []
        self._outlist_override = None
        self.Views, self.Source, self.EditableTexts = [], [], {'title': 'synthetic placeholder'}
        if kind == 'TechDraw::DrawViewPart':
            self.PropertiesList.extend(['Source', 'XSource'])
            self.XSource = []
        self.Scale, self.X, self.Y = 1., 0., 0.
        self.ProjectionType = 'Third angle'  # A user preference must not change the contract.
        self.CoarseView = True  # An existing preference must not downgrade exact HLR.

    def addView(self, view): self.Views.append(view)

    @property
    def OutList(self):
        if self._outlist_override is not None:
            return self._outlist_override
        if self.TypeId == 'TechDraw::DrawPage':
            template = getattr(self, 'Template', None)
            return ([] if template is None else [template]) + self.Views[:]
        if self.TypeId == 'TechDraw::DrawViewPart':
            return self.Source[:] + self.XSource[:]
        return []

    @OutList.setter
    def OutList(self, value): self._outlist_override = value


class Document:
    def __init__(self, source):
        self.Name, self.Objects = 'SyntheticDrawing', [source]
        source.Document = self
        self.RecomputesFrozen, self.HasPendingTransaction, self.UndoMode = False, False, 1
        self.active_transaction, self.transaction_serial = None, 0
        self.events, self.recompute_calls, self.created_calls = [], [], []
        self.add_hook = self.recompute_hook = self.commit_hook = None
        self.recompute_result = 5

    def getObject(self, name):
        return next((obj for obj in self.Objects if obj.Name == name), None)

    def addObject(self, kind, name):
        self.HasPendingTransaction = True  # Native undo record begins at the first write.
        self.created_calls.append((kind, name))
        obj = DraftNode(name, kind)
        obj.Document = self
        self.Objects.append(obj)
        if self.add_hook: self.add_hook(obj)
        return obj

    def openTransaction(self, title):
        self.events.append('open')
        self.transaction_serial += 1
        self.active_transaction = (title, self.transaction_serial)
        self.HasPendingTransaction = False  # FreeCAD 1.1 opens the record lazily.
        self.before_objects = self.Objects[:]
        self.before = [(obj, {k: v[:] if type(v) is list else v for k, v in obj.__dict__.items()})
                       for obj in self.Objects]

    def abortTransaction(self):
        self.events.append('abort')
        self.active_transaction, self.HasPendingTransaction = None, False
        self.Objects = self.before_objects[:]
        for obj, values in self.before:
            obj.__dict__.clear()
            obj.__dict__.update(values)

    def commitTransaction(self):
        self.events.append('commit')
        self.active_transaction, self.HasPendingTransaction = None, False
        if self.commit_hook: self.commit_hook()

    def recompute(self, objects, force, has_error):
        self.recompute_calls.append((objects[:], force, has_error))
        if self.recompute_hook: self.recompute_hook(objects)
        return self.recompute_result


class DrawingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pft-drawing-test-')
        self.addCleanup(self.temp.cleanup)
        self.resource = Path(self.temp.name)
        self.template = self.resource / 'Mod' / 'TechDraw' / 'Templates' / 'ISO' / 'A4_Landscape_ISO5457_advanced.svg'
        self.template.parent.mkdir(parents=True)
        self.template.write_text(SVG, encoding='utf-8')
        self.source = NativeObject('SyntheticSolid')
        self.doc = Document(self.source)
        self.app = NS(ActiveDocument=self.doc, Vector=vector, getResourceDir=lambda: str(self.resource))
        self.app.getDocument = lambda name: self.doc if name == self.doc.Name else None
        self.app.getActiveTransaction = lambda: self.doc.active_transaction
        bind_transaction_api(self.app, self.doc, counter_name="transaction_serial")
        self.edit = None
        self.gui = NS(getDocument=lambda name: NS(getInEdit=lambda: self.edit))
        self.prime_calls = []
        self.prime = patch.object(drawing, '_prime_projection_engine', lambda app: self.prime_calls.append(app))
        self.prime.start()
        self.addCleanup(self.prime.stop)

    def prepare(self):
        return drawing.prepare_plan(self.app, self.gui, self.doc, self.source, controller)

    def apply(self, plan):
        return drawing.apply_plan(self.app, self.gui, self.doc, plan, controller)

    def error(self, callback, code=None, cls=drawing.PftDrawingError):
        with self.assertRaises(cls) as caught: callback()
        if code: self.assertEqual(caught.exception.code, code)
        self.assertNotIn(str(self.resource), str(caught.exception))
        return caught.exception

    def parent(self, child=None, name='Container'):
        child = self.source if child is None else child
        parent = NativeObject(name, 'App::Part')
        parent.Document = self.doc
        parent.Group.append(child)
        child.InList.append(parent)
        self.doc.Objects.append(parent)
        return parent

    def native_template_registry(self):
        template = NativeObject('RegisteredTemplate', 'TechDraw::DrawSVGTemplate')
        page = NativeObject('RegisteredPage', 'TechDraw::DrawPage')
        template.Document, page.Document = self.doc, self.doc
        template.InList, template.ExpressionEngine = [page], []
        page.Template = template
        self.doc.Objects.extend([page, template])
        return page, template

    def test_default_drawing_capture_still_rejects_touched_template_page_and_source(self):
        page, template = self.native_template_registry()
        for obj in (template, page, self.source):
            with self.subTest(kind=obj.TypeId):
                obj.State = ['Touched']
                self.error(self.prepare, 'DRAWING_UNSUPPORTED')
                self.assertEqual(self.doc.events, [])
                obj.State = ['Up-to-date']

    def test_registry_touched_exception_is_explicit_identity_based_and_not_a_created_node(self):
        page, template = self.native_template_registry()
        template.State = ['Touched']
        self.error(lambda: drawing._registry(self.doc), 'DRAWING_UNSUPPORTED')
        self.assertEqual(drawing._registry(self.doc, allowed_touched=(template,)), tuple(self.doc.Objects))
        captured = drawing._capture(self.app, self.gui, self.doc, self.source, controller,
                                    allowed_touched=(template,))
        self.assertIs(captured[2][0], self.source.Shape)
        self.assertFalse(self.doc.HasPendingTransaction)
        self.assertEqual(self.doc.events, [])
        other = NativeObject('OtherTemplate', 'TechDraw::DrawSVGTemplate')
        other.Document, other.State = self.doc, ['Touched']
        self.doc.Objects.append(other)
        self.error(lambda: drawing._registry(self.doc, allowed_touched=(template,)), 'DRAWING_UNSUPPORTED')
        self.assertEqual(template.State, ['Touched'])

    def test_registry_requires_bounded_exact_tuple_of_unique_native_registered_svg_templates(self):
        for change in ('list', 'none', 'over_bound', 'duplicate', 'source', 'wrong_type',
                       'foreign', 'unregistered', 'registry_identity', 'proxy', 'proxy_property', 'invalid'):
            with self.subTest(change=change):
                self.setUp(); page, template = self.native_template_registry()
                template.State = ['Touched']
                allowed = (template,)
                if change == 'list': allowed = [template]
                elif change == 'none': allowed = None
                elif change == 'over_bound': allowed = (template,) * 9
                elif change == 'duplicate': allowed = (template, template)
                elif change == 'source': allowed = (self.source,)
                elif change == 'wrong_type': template.TypeId = 'TechDraw::DrawSVGTemplatePython'
                elif change == 'foreign': template.Document = NS(Name='OtherDocument')
                elif change == 'unregistered': self.doc.Objects.remove(template)
                elif change == 'registry_identity':
                    other = NativeObject(template.Name, template.TypeId); other.Document = self.doc
                    self.doc.Objects.insert(0, other)
                elif change == 'proxy': template.Proxy = None
                elif change == 'proxy_property': template.PropertiesList.append('Proxy')
                else: template.valid = False
                self.error(lambda: drawing._registry(self.doc, allowed_touched=allowed), 'DRAWING_UNSUPPORTED')
                self.assertEqual(self.doc.events, [])

    def test_registry_touched_exception_never_masks_invalid_recompute_or_unknown_states(self):
        page, template = self.native_template_registry()
        for state in ('Invalid', 'Error', 'Recompute', 'Recompute2', 'Restore', 'Partial'):
            template.State = ['Touched', state]
            self.error(lambda: drawing._registry(self.doc, allowed_touched=(template,)), 'DRAWING_UNSUPPORTED')
            self.assertEqual(template.State, ['Touched', state])

    def test_registry_template_exception_rejects_geometry_consumers_or_expressions(self):
        for change in ('no_owner', 'two_owners', 'geometry_owner', 'wrong_page_link', 'foreign_owner', 'expressions'):
            with self.subTest(change=change):
                self.setUp(); page, template = self.native_template_registry()
                template.State = ['Touched']
                if change == 'no_owner': template.InList = []
                elif change == 'two_owners': template.InList.append(page)
                elif change == 'geometry_owner': template.InList = [self.source]
                elif change == 'wrong_page_link': page.Template = None
                elif change == 'foreign_owner': page.Document = NS(Name='OtherDocument')
                else: template.ExpressionEngine = [('Width', '1 + 1')]
                self.error(lambda: drawing._registry(self.doc, allowed_touched=(template,)), 'DRAWING_UNSUPPORTED')
                self.assertEqual(self.doc.events, [])

    def test_rotated_parent_bbox_fits_the_real_global_projection_without_source_writes(self):
        self.source.Shape = Shape(1000., 1., 1.)
        parent = self.parent()
        parent.Placement = placement((3., 4., 5.), (0., math.sqrt(.5), 0., math.sqrt(.5)))
        original_shape, original_placement = self.source.Shape, copy.deepcopy(self.source.Shape.Placement)
        plan, _ = self.prepare()
        self.assertEqual(plan['scale'], .05)
        for observed, expected in zip(plan['snapshot'][6][3], (1., 1., 1000.)):
            self.assertAlmostEqual(observed, expected)
        self.assertEqual(plan['parents'][0][:2], (parent, self.source))
        self.assertTrue(original_shape.copies)
        self.assertTrue(all(item is not original_shape for item in original_shape.copies))
        self.assertEqual(vars(original_shape.Placement.Base), vars(original_placement.Base))
        self.assertEqual(original_shape.Placement.Rotation.Q, original_placement.Rotation.Q)
        self.assertEqual(self.doc.events, [])
        self.assertEqual(self.doc.recompute_calls, [])
        result = self.apply(plan)
        self.assertEqual(result['scale'], .05)
        self.assertIs(self.source.Shape, original_shape)
        self.assertEqual(vars(original_shape.Placement.Base), vars(original_placement.Base))
        self.assertEqual(original_shape.Placement.Rotation.Q, original_placement.Rotation.Q)
        self.assertEqual(self.doc.events, ['open', 'commit'])
        self.assertEqual(self.doc.recompute_calls[0][0], self.doc.Objects[-5:])
        self.assertTrue(all(view.CoarseView is False for view in self.doc.Objects[-3:]))

    def test_local_and_parent_rotations_replace_shape_placement_without_double_transform(self):
        self.source.Shape = Shape(1000., 1., 1.)
        self.source.Placement = placement((1., 2., 3.), (0., math.sqrt(.5), 0., math.sqrt(.5)))
        self.source.Shape.Placement = copy.deepcopy(self.source.Placement)
        parent = self.parent()
        parent.Placement = placement((3., 4., 5.), (0., math.sqrt(.5), 0., math.sqrt(.5)))
        original = copy.deepcopy(self.source.Shape.Placement)
        plan, _ = self.prepare()
        # Two 90 degree Y rotations yield 180 degrees: the long axis is X.
        # Multiplying the already-local Shape placement again would make it Z.
        self.assertEqual(plan['scale'], .1)
        for observed, expected in zip(plan['snapshot'][6][3], (1000., 1., 1.)):
            self.assertAlmostEqual(observed, expected)
        global_placement = self.source.getGlobalPlacement()
        for copied in self.source.Shape.copies:
            self.assertEqual(vars(copied.Placement.Base), vars(global_placement.Base))
            self.assertEqual(copied.Placement.Rotation.Q, global_placement.Rotation.Q)
        self.assertEqual(vars(self.source.Shape.Placement.Base), vars(original.Base))
        self.assertEqual(self.source.Shape.Placement.Rotation.Q, original.Rotation.Q)

    def test_nested_native_parts_and_body_tip_keep_distinct_source_and_tip_identity(self):
        self.source.TypeId = 'PartDesign::Body'
        tip = NativeObject('Pocket', 'PartDesign::Pocket')
        tip.Document = self.doc
        tip.InList = [self.source]
        self.source.Tip, self.source.Group = tip, [tip]
        self.doc.Objects.append(tip)
        inner = self.parent()
        outer = self.parent(inner, 'OuterContainer')
        inner.Placement = placement((1., 2., 3.))
        outer.Placement = placement((4., 5., 6.), (0., 0., math.sqrt(.5), math.sqrt(.5)))
        before = (self.source.Shape, tip.Shape, copy.deepcopy(inner.Placement), copy.deepcopy(outer.Placement))
        plan, _ = self.prepare()
        self.assertEqual([row[0] for row in plan['parents']], [inner, outer])
        result = self.apply(plan)
        self.assertEqual(result['source'], self.source.Name)
        self.assertIs(self.source.Shape, before[0])
        self.assertIs(tip.Shape, before[1])
        self.assertTrue(all(view.Source == [self.source] for view in self.doc.Objects[-3:]))
        self.assertEqual(self.doc.recompute_calls[0][0], self.doc.Objects[-5:])
        self.assertEqual(inner.Placement.Rotation.Q, before[2].Rotation.Q)
        self.assertEqual(outer.Placement.Rotation.Q, before[3].Rotation.Q)
        self.assertEqual(self.doc.events, ['open', 'commit'])

    def test_parent_placement_state_global_read_and_membership_changes_invalidate_plan(self):
        for mutation in ('translation', 'rotation', 'global', 'state', 'membership_order'):
            with self.subTest(mutation=mutation):
                self.setUp()
                parent = self.parent()
                helper = NativeObject('Helper')
                helper.Document = self.doc
                self.doc.Objects.append(helper)
                parent.Group.append(helper)
                plan, _ = self.prepare()
                if mutation == 'translation':
                    parent.Placement.Base.x += 1.
                elif mutation == 'rotation':
                    parent.Placement.Rotation.Q = (0., 0., math.sqrt(.5), math.sqrt(.5))
                elif mutation == 'global':
                    parent.getGlobalPlacement = lambda: placement((1., 0., 0.))
                elif mutation == 'state':
                    parent.State.append('Expanded')
                else:
                    parent.Group.reverse()
                self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')
                self.assertEqual(self.doc.events, [])
                self.assertEqual(self.doc.created_calls, [])
                self.assertEqual(self.prime_calls, [])

    def test_equal_transform_reparenting_and_first_parent_addition_invalidate_plan(self):
        for original_parent in (False, True):
            with self.subTest(original_parent=original_parent):
                self.setUp()
                first = self.parent() if original_parent else None
                other = NativeObject('OtherContainer', 'App::Part')
                other.Document = self.doc
                self.doc.Objects.append(other)
                plan, _ = self.prepare()
                if first is not None:
                    first.Group.remove(self.source)
                    self.source.InList.remove(first)
                other.Group.append(self.source)
                self.source.InList.append(other)
                self.assertEqual(plan['snapshot'][4], drawing._placement_value(self.source.getGlobalPlacement()))
                self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')
                self.assertEqual(self.doc.created_calls, [])
                self.assertEqual(self.doc.events, [])

    def test_parent_depth_bound_and_cycles_fail_before_creating_nodes(self):
        current = self.source
        for index in range(drawing._MAX_PARENT_DEPTH):
            current = self.parent(current, 'Container%d' % index)
        plan, _ = self.prepare()
        self.assertEqual(len(plan['parents']), 16)
        self.parent(current, 'OneTooMany')
        self.error(self.prepare, 'DRAWING_UNSUPPORTED')
        self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')
        self.assertEqual(self.doc.events, [])
        self.setUp()
        first = self.parent()
        second = self.parent(first, 'OtherContainer')
        first.InList.append(second)
        second.InList.append(first)
        first.Group.append(second)
        self.error(self.prepare, 'DRAWING_UNSUPPORTED')
        self.assertEqual(self.doc.created_calls, [])

    def test_parent_unknown_types_proxies_reads_and_ambiguous_ownership_are_rejected(self):
        for mutation in ('body', 'python', 'link', 'proxy', 'missing_reader', 'read_error',
                         'wrong_document', 'wrong_registry', 'duplicate_member', 'ambiguous_parent', 'wrong_parent'):
            with self.subTest(mutation=mutation):
                self.setUp()
                parent = self.parent()
                if mutation == 'body':
                    parent.TypeId = 'PartDesign::Body'
                elif mutation in ('python', 'link'):
                    parent.TypeId = 'App::PartPython' if mutation == 'python' else 'App::Link'
                    # This returned geometric parent is not an unrelated Link
                    # merely referencing the source through LinkedObject.
                    parent.hasExtension = lambda name: name == 'App::GeoFeatureGroupExtension'
                    self.source.getParentGeoFeatureGroup = lambda: parent
                elif mutation == 'proxy':
                    parent.Proxy = None
                elif mutation == 'missing_reader':
                    parent.getGlobalPlacement = None
                elif mutation == 'read_error':
                    parent.getGlobalPlacement = lambda: (_ for _ in ()).throw(RuntimeError('synthetic private detail'))
                elif mutation == 'wrong_document':
                    parent.Document = object()
                elif mutation == 'wrong_registry':
                    self.doc.Objects.remove(parent)
                elif mutation == 'duplicate_member':
                    parent.Group.append(self.source)
                elif mutation == 'ambiguous_parent':
                    self.parent(name='OtherContainer')
                else:
                    self.source.getParentGeoFeatureGroup = lambda: None
                self.error(self.prepare, 'DRAWING_UNSUPPORTED')
                self.assertEqual(self.doc.events, [])
                self.assertEqual(self.doc.created_calls, [])
                self.assertEqual(self.source.Shape.copies, [])

    def test_precise_view_assignment_is_verified_and_never_overwrites_a_source_flag(self):
        self.source.CoarseView = True
        plan, _ = self.prepare()
        self.apply(plan)
        self.assertIs(self.source.CoarseView, True)
        self.assertTrue(all(view.CoarseView is False for view in self.doc.Objects[-3:]))
        self.setUp()
        plan, _ = self.prepare()
        class UncooperativeNode(DraftNode):
            def __setattr__(self, name, value):
                if name == 'CoarseView' and value is False:
                    return
                super().__setattr__(name, value)
        self.doc.add_hook = lambda node: setattr(node, '__class__', UncooperativeNode)
        self.error(lambda: self.apply(plan), 'DRAWING_CREATE_FAILED')
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.assertEqual(self.doc.Objects, [self.source])

    def test_preview_is_read_only_has_five_rows_and_private_template(self):
        original_shape = self.source.Shape
        plan, rows = self.prepare()
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row['status'] == 'change' and row['old'] is None for row in rows))
        self.assertEqual(plan['request'], {'source': self.source.Name, 'page': plan['names'][0]})
        self.assertNotIn(str(self.resource), json.dumps(rows))
        self.assertEqual(self.doc.events, [])
        self.assertEqual(self.doc.recompute_calls, [])
        self.assertEqual(self.doc.created_calls, [])
        self.assertEqual(self.prime_calls, [])
        self.assertIs(self.source.Shape, original_shape)
        self.assertEqual(len(plan['template_digest']), 64)

    def test_capability_matches_exact_advertised_scope(self):
        self.assertEqual(drawing.CAPABILITIES, {
            'version': 1, 'operation': 'create_drawing', 'confirmation': 'native_gui_only',
            'preview_method': 'prepare_part_changes', 'request_method': 'request_part_change_approval',
            'status_method': 'get_part_change_status', 'paper': 'A4_landscape',
            'projection': 'first_angle', 'views': ['front', 'top', 'axonometric'],
            'source_scope': 'single_native_solid', 'saves_document': False,
            'projection_verification': 'not_checked'})

    def test_apply_creates_first_angle_draft_with_one_local_recompute(self):
        plan, _ = self.prepare()
        result = self.apply(plan)
        self.assertEqual(self.doc.events, ['open', 'commit'])
        self.assertEqual(self.prime_calls, [self.app])
        self.assertEqual(len(self.doc.Objects), 6)
        self.assertEqual(len(self.doc.recompute_calls), 1)
        objects, force, has_error = self.doc.recompute_calls[0]
        self.assertEqual(objects, self.doc.Objects[1:])
        self.assertNotIn(self.source, objects)
        self.assertIs(force, False)
        self.assertIs(has_error, True)
        self.assertEqual(result, {'kind': 'drawing_draft', 'page': plan['names'][0], 'template': plan['names'][1],
            'source': self.source.Name, 'views': [{'name': name, 'kind': kind} for name, kind in
                zip(plan['names'][2:], ('front', 'top', 'axonometric'))],
            'scale': 1., 'projection': 'first_angle', 'paper': 'A4_landscape',
            'projection_verification': 'not_checked', 'manual_review_required': True})
        page = self.doc.getObject(result['page'])
        self.assertEqual(page.ProjectionType, 'First angle')
        self.assertTrue(all(view.CoarseView is False for view in page.Views))
        self.assertEqual(self.source.Shape.copies, [])  # Root sources need no placed copy.
        self.assertLess(page.Views[1].Y, page.Views[0].Y)
        self.assertEqual(page.Template.EditableTexts, {'title': ''})
        self.assertNotIn(str(self.resource), json.dumps(result))
        self.assertEqual([view.Source for view in page.Views], [[self.source]] * 3)

    def test_lazy_transaction_is_allowed_only_before_first_created_node(self):
        plan, _ = self.prepare()
        first_write_pending = []
        self.doc.add_hook = lambda node: first_write_pending.append(self.doc.HasPendingTransaction)
        self.apply(plan)
        self.assertEqual(first_write_pending, [True] * 5)
        self.assertEqual(self.doc.events, ['open', 'commit'])

    def test_lost_pending_record_after_node_creation_is_not_closed(self):
        plan, _ = self.prepare()
        self.doc.add_hook = lambda node: setattr(self.doc, 'HasPendingTransaction', False)
        self.error(lambda: self.apply(plan), 'PART_TRANSACTION_CHANGED', cls=controller.PftWriteError)
        self.assertEqual(self.doc.events, ['open'])
        self.assertEqual(len(self.doc.Objects), 2)  # Unknown closure must not be retried.

    def test_projection_initialization_failure_opens_no_transaction(self):
        plan, _ = self.prepare()
        with patch.object(drawing, '_prime_projection_engine', side_effect=drawing.PftDrawingError('DRAWING_PROJECTION_UNAVAILABLE')):
            self.error(lambda: self.apply(plan), 'DRAWING_PROJECTION_UNAVAILABLE')
        self.assertEqual(self.doc.events, [])
        self.assertEqual(self.doc.created_calls, [])
        self.assertEqual(self.doc.Objects, [self.source])

    def test_native_initialization_side_effect_invalidates_plan_before_transaction(self):
        plan, _ = self.prepare()
        with patch.object(drawing, '_prime_projection_engine', side_effect=lambda app: setattr(self.source, 'Shape', Shape())):
            self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')
        self.assertEqual(self.doc.events, [])
        self.assertEqual(self.doc.created_calls, [])

    def test_stale_plan_is_rejected_before_initializing_projection(self):
        plan, _ = self.prepare()
        self.source.Shape = Shape()
        self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')
        self.assertEqual(self.prime_calls, [])
        self.assertEqual(self.doc.events, [])

    def test_native_projection_may_still_be_pending(self):
        plan, _ = self.prepare()
        self.doc.recompute_result = 0
        result = self.apply(plan)
        self.assertEqual(result['projection_verification'], 'not_checked')
        self.assertTrue(all(obj.State == ['Touched'] for obj in self.doc.Objects[1:]))

    def test_scale_uses_all_three_conservative_projected_spans(self):
        self.source.Shape = Shape(600., 400., 80.)
        plan, _ = self.prepare()
        self.assertEqual(plan['scale'], .1)

    def test_conservative_layout_clears_a4_title_block_and_page_edges(self):
        for spec in drawing._SPECS:
            x, y, width, height = spec[3:]
            self.assertGreaterEqual(x - width / 2, 20.)
            self.assertLessEqual(x + width / 2, 287.)
            self.assertGreaterEqual(y - height / 2, 80.)
            self.assertLessEqual(y + height / 2, 200.)
        front, top, _ = drawing._SPECS
        self.assertLess(top[4] + top[6] / 2, front[4] - front[6] / 2)
        _, rows = self.prepare()
        self.assertTrue(all('视图中心' in row['note'] for row in rows[2:]))

    def test_long_source_name_produces_distinct_bounded_names(self):
        self.source.Name = 'X' * 64
        plan, _ = self.prepare()
        self.assertEqual(len(set(plan['names'])), 5)
        self.assertTrue(all(len(name) <= 64 and name != self.source.Name for name in plan['names']))

    def test_unicode_document_and_source_names_remain_supported(self):
        self.doc.Name = '中文螺栓文档' * 30
        self.source.Name = '原生实体'
        plan, _ = self.prepare()
        result = self.apply(plan)
        self.assertEqual(result['source'], '原生实体')
        self.assertTrue(all(name.isascii() and len(name) <= 64 for name in plan['names']))

    def test_source_name_paths_and_invisible_characters_are_rejected(self):
        for name in ('/tmp/x', 'x/y', 'x\\y', ' x', 'x ', '.', '..', 'x\u200by'):
            self.source.Name = name
            self.error(self.prepare, 'DRAWING_UNSUPPORTED')

    def test_body_binds_tip_identity_shape_and_state(self):
        self.source.TypeId = 'PartDesign::Body'
        tip = NativeObject('Pad', 'PartDesign::Pad')
        tip.Document = self.doc
        self.doc.Objects.append(tip)
        self.source.Tip, self.source.Group = tip, [tip]
        plan, _ = self.prepare()
        tip.Shape = Shape()
        self.error(lambda: drawing.verify_plan(self.app, self.gui, self.doc, plan, controller), 'DRAWING_PLAN_STALE')
        self.assertEqual(self.doc.events, [])

    def test_rejects_python_link_pad_and_invalid_solids_before_writes(self):
        for kind in ('Part::FeaturePython', 'App::Link', 'PartDesign::Pad'):
            self.source.TypeId = kind
            self.error(self.prepare, 'DRAWING_UNSUPPORTED')
        self.source.TypeId = 'Part::Feature'
        self.source.Shape.Solids = []
        self.error(self.prepare, 'DRAWING_UNSUPPORTED')
        self.assertEqual(self.doc.events, [])

    def test_proxy_and_touched_source_are_not_supported(self):
        self.source.Proxy = None
        self.error(self.prepare, 'DRAWING_UNSUPPORTED')
        del self.source.Proxy
        self.source.State = ['Touched']
        self.error(self.prepare, 'DRAWING_UNSUPPORTED')

    def test_shape_identity_change_rejects_same_dimensions(self):
        plan, _ = self.prepare()
        self.source.Shape = Shape()
        self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')
        self.assertEqual(self.doc.events, [])

    def test_shape_geometry_and_source_placement_changes_reject(self):
        for mutate in (lambda: setattr(self.source.Shape, 'Volume', 1.),
                       lambda: setattr(self.source.Placement.Base, 'x', 1.)):
            plan, _ = self.prepare()
            mutate()
            self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')
        self.assertEqual(self.doc.events, [])

    def test_source_replacement_and_document_replacement_are_rejected(self):
        plan, _ = self.prepare()
        replacement = NativeObject(self.source.Name)
        replacement.Document = self.doc
        self.doc.Objects[0] = replacement
        self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')
        self.doc.Objects[0] = self.source
        self.app.ActiveDocument = NS(Name=self.doc.Name)
        self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')

    def test_template_change_or_missing_template_rejects_stale_plan(self):
        plan, _ = self.prepare()
        self.template.write_text(SVG + '\n', encoding='utf-8')
        self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')
        self.template.unlink()
        self.error(self.prepare, 'DRAWING_TEMPLATE_UNAVAILABLE')

    def test_invalid_a4_or_malformed_svg_is_unavailable(self):
        for value in ('not XML', SVG.replace('297mm', '210mm')):
            self.template.write_text(value, encoding='utf-8')
            self.error(self.prepare, 'DRAWING_TEMPLATE_UNAVAILABLE')

    def test_template_symlink_must_remain_in_installed_template_root(self):
        outside = self.resource / 'outside.svg'
        outside.write_text(SVG, encoding='utf-8')
        self.template.unlink()
        self.template.symlink_to(outside)
        self.error(self.prepare, 'DRAWING_TEMPLATE_UNAVAILABLE')

    def test_name_collision_never_overwrites(self):
        plan, _ = self.prepare()
        collision = NativeObject(plan['names'][0])
        collision.Document = self.doc
        self.doc.Objects.append(collision)
        self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')
        self.assertEqual(self.doc.events, [])
        self.error(self.prepare, 'DRAWING_UNSUPPORTED')

    def test_recompute_failure_rolls_back_five_new_nodes(self):
        plan, _ = self.prepare()
        self.doc.recompute_hook = lambda objects: (_ for _ in ()).throw(RuntimeError('private details'))
        self.error(lambda: self.apply(plan), 'DRAWING_RECOMPUTE_UNVERIFIED')
        self.assertEqual(self.doc.Objects, [self.source])
        self.assertEqual(self.doc.events, ['open', 'abort'])

    def test_invalid_recompute_return_is_not_success(self):
        for value in (True, None, -1, '5'):
            plan, _ = self.prepare()
            self.doc.recompute_result = value
            self.error(lambda: self.apply(plan), 'DRAWING_RECOMPUTE_UNVERIFIED')
            self.assertEqual(self.doc.Objects, [self.source])

    def test_native_creation_renaming_aborts_own_transaction(self):
        plan, _ = self.prepare()
        self.doc.add_hook = lambda obj: setattr(obj, 'Name', obj.Name + '001')
        self.error(lambda: self.apply(plan), 'DRAWING_CREATE_FAILED')
        self.assertEqual(self.doc.events, ['open', 'abort'])

    def test_source_mutation_during_native_callback_aborts_and_restores(self):
        plan, _ = self.prepare()
        self.doc.recompute_hook = lambda objects: setattr(self.source, 'Shape', Shape(60., 40., 9.))
        self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.assertIs(self.source.Shape, plan['shapes'][0])

    def test_transaction_transfer_never_closes_another_operation(self):
        plan, _ = self.prepare()
        self.doc.add_hook = lambda obj: setattr(self.doc, 'active_transaction', ('different owner', 999))
        self.error(lambda: self.apply(plan), 'PART_TRANSACTION_CHANGED', controller.PftWriteError)
        self.assertEqual(self.doc.events, ['open'])

    def test_commit_verification_failure_never_late_aborts(self):
        plan, _ = self.prepare()
        self.doc.commit_hook = lambda: setattr(self.source, 'Shape', Shape())
        self.error(lambda: self.apply(plan), 'PART_COMMIT_UNVERIFIED', controller.PftWriteError)
        self.assertEqual(self.doc.events, ['open', 'commit'])

    def test_no_save_export_sleep_gui_events_or_whole_document_recompute(self):
        source = (fixtures.ADDON / 'pft_drawing.py').read_text(encoding='utf-8')
        for forbidden in ('saveAs(', 'saveCopy(', 'save(', 'exportPage', 'processEvents(',
                          'updateGui(', 'sleep(', 'recompute()', 'ViewObject.show(', 'setActiveDocument('):
            self.assertNotIn(forbidden, source)




class DependencySnapshotTests(unittest.TestCase):
    setUp = DrawingTests.setUp
    prepare = DrawingTests.prepare
    apply = DrawingTests.apply
    error = DrawingTests.error

    def helper(self, name='Origin', kind='App::Origin'):
        obj = NS(Name=name, TypeId=kind, Document=self.doc,
                 PropertiesList=['ExpressionEngine'], State=['Up-to-date'],
                 ExpressionEngine=[], OutList=[], isValid=lambda: True)
        self.doc.Objects.append(obj)
        return obj

    def test_native_auxiliary_graph_needs_no_shape_or_placement_and_commits(self):
        origin = self.helper()
        plane = self.helper('Plane', 'App::Plane')
        sketch = self.helper('Sketch', 'Sketcher::SketchObject')
        self.source.OutList = [origin, sketch]
        origin.OutList = [plane]
        sketch.OutList = [plane]
        self.app.ParamGet = lambda path: (_ for _ in ()).throw(AssertionError('prepare initialized preferences'))
        plan, _ = self.prepare()
        signature = plan['snapshot'][7]
        self.assertEqual([row[2] for row in signature], [self.source.Name, 'Origin', 'Plane', 'Sketch'])
        self.assertIs(signature[1][1], origin)
        self.assertEqual(signature[0][5], (id(origin), id(sketch)))
        self.assertTrue(all(type(row) is tuple and type(row[4]) is tuple and type(row[5]) is tuple
                            for row in signature))
        self.assertFalse(any(hasattr(obj, 'Shape') or hasattr(obj, 'Placement') for obj in (origin, plane, sketch)))
        result = self.apply(plan)
        self.assertEqual(result['kind'], 'drawing_draft')
        self.assertEqual(self.doc.events, ['open', 'commit'])

    def test_body_tip_is_a_root_even_when_not_in_sources_cached_outlist(self):
        self.source.TypeId = 'PartDesign::Body'
        tip = NativeObject('Pocket', 'PartDesign::Pocket')
        tip.Document = self.doc
        self.doc.Objects.append(tip)
        self.source.Tip, self.source.Group = tip, [tip]
        tip.OutList = [self.helper('Sketch', 'Sketcher::SketchObject')]
        plan, _ = self.prepare()
        self.assertEqual([row[2] for row in plan['snapshot'][7]], [self.source.Name, 'Pocket', 'Sketch'])
        tip.OutList[0].Proxy = None
        self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')
        self.assertEqual(self.doc.events, [])

    def test_foreign_dependency_is_rejected_before_reading_any_other_foreign_field(self):
        accesses = []
        foreign_document = object()
        class Foreign:
            def __getattribute__(self, key):
                accesses.append(key)
                if key == 'Document':
                    return foreign_document
                raise AssertionError('foreign field was inspected')
        self.source.OutList = [Foreign()]
        self.error(self.prepare, 'DRAWING_UNSUPPORTED')
        self.assertEqual(accesses, ['Document'])
        self.assertEqual((self.prime_calls, self.doc.events, self.doc.created_calls), ([], [], []))

    def test_proxy_python_link_invalid_and_expression_dependencies_are_rejected(self):
        for mode in ('proxy', 'proxy_property', 'python', 'link', 'link_element', 'invalid', 'expression',
                     'touched', 'unregistered', 'wrong_registration'):
            with self.subTest(mode=mode):
                self.setUp()
                dependency = self.helper()
                self.source.OutList = [dependency]
                if mode == 'proxy': dependency.Proxy = None
                elif mode == 'proxy_property': dependency.PropertiesList.append('Proxy')
                elif mode == 'python': dependency.TypeId = 'PartDesign::FeaturePython'
                elif mode == 'link': dependency.TypeId = 'App::Link'
                elif mode == 'link_element': dependency.TypeId = 'App::LinkElement'
                elif mode == 'invalid': dependency.isValid = lambda: False
                elif mode == 'expression': dependency.ExpressionEngine = [('Length', '1 + 1')]
                elif mode == 'touched': dependency.State = ['Touched']
                elif mode == 'unregistered': self.doc.Objects.remove(dependency)
                else:
                    self.doc.Objects.remove(dependency)
                    self.helper(dependency.Name)
                self.error(self.prepare, 'DRAWING_UNSUPPORTED')
                self.assertEqual((self.prime_calls, self.doc.events), ([], []))

    def test_nonempty_source_expression_is_rejected_before_source_shape_read(self):
        reads = []
        class ShapeReadTrap(NativeObject):
            @property
            def Shape(self):
                reads.append('Shape')
                raise AssertionError('source Shape read before dependency validation')
        self.source.__class__ = ShapeReadTrap
        self.source.ExpressionEngine = [('Placement.Base.x', '1 + 1')]
        self.error(self.prepare, 'DRAWING_UNSUPPORTED')
        self.assertEqual(reads, [])

    def test_local_edge_replacement_removal_and_child_identity_are_frozen(self):
        for mode in ('replace_edge', 'remove_edge', 'rename', 'state', 'type', 'replace_identity', 'foreign'):
            with self.subTest(mode=mode):
                self.setUp()
                before, other = self.helper('Before'), self.helper('Other')
                self.source.OutList = [before]
                plan, _ = self.prepare()
                if mode == 'replace_edge': self.source.OutList = [other]
                elif mode == 'remove_edge': self.source.OutList = []
                elif mode == 'rename': before.Name = 'Renamed'
                elif mode == 'state': before.State = ['Up-to-date', 'Expanded']
                elif mode == 'type': before.TypeId = 'App::Plane'
                elif mode == 'replace_identity':
                    self.doc.Objects.remove(before)
                    self.source.OutList = [self.helper('Before')]
                else: before.Document = object()
                self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')
                self.assertEqual((self.prime_calls, self.doc.events), ([], []))

    def test_dependencies_are_rechecked_after_initialization_before_any_cad_write(self):
        dependency, replacement = self.helper('Before'), self.helper('After')
        self.source.OutList = [dependency]
        plan, _ = self.prepare()
        with patch.object(drawing, '_prime_projection_engine', side_effect=lambda app: setattr(self.source, 'OutList', [replacement])):
            self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')
        self.assertEqual((self.doc.events, self.doc.created_calls), ([], []))

    def test_dependency_change_during_recompute_is_not_committed_as_success(self):
        dependency, replacement = self.helper('Before'), self.helper('After')
        self.source.OutList = [dependency]
        plan, _ = self.prepare()
        self.doc.recompute_hook = lambda nodes: setattr(self.source, 'OutList', [replacement])
        self.error(lambda: self.apply(plan), 'DRAWING_PLAN_STALE')
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.assertEqual(self.source.OutList, [dependency])

    def test_cycles_and_malformed_or_failed_native_reads_are_fixed_errors(self):
        for mode in ('self_cycle', 'nested_cycle', 'roots_list', 'too_many_roots', 'outlist_iterator',
                     'expression_iterator', 'read_failure'):
            with self.subTest(mode=mode):
                self.setUp()
                dependency = self.helper()
                self.source.OutList = [dependency]
                roots = (self.source,)
                if mode == 'self_cycle': self.source.OutList = [self.source]
                elif mode == 'nested_cycle': dependency.OutList = [self.source]
                elif mode == 'roots_list': roots = [self.source]
                elif mode == 'too_many_roots': roots = (self.source,) * 3
                elif mode == 'outlist_iterator': dependency.OutList = iter(())
                elif mode == 'expression_iterator': dependency.ExpressionEngine = iter(())
                else:
                    dependency.isValid = lambda: (_ for _ in ()).throw(RuntimeError('synthetic-private-read-detail'))
                error = self.error(lambda: drawing._dependency_snapshot(self.doc, roots), 'DRAWING_UNSUPPORTED')
                self.assertNotIn('synthetic-private-read-detail', str(error))

    def test_iterative_node_limit_accepts_boundary_and_rejects_one_more(self):
        previous = self.source
        for index in range(drawing._MAX_DEPENDENCY_NODES - 1):
            current = self.helper('Aux%03d' % index)
            previous.OutList = [current]
            previous = current
        signature = drawing._dependency_snapshot(self.doc, (self.source,))
        self.assertEqual(len(signature), drawing._MAX_DEPENDENCY_NODES)
        previous.OutList = [self.helper('OneTooMany')]
        self.error(lambda: drawing._dependency_snapshot(self.doc, (self.source,)), 'DRAWING_UNSUPPORTED')

    def test_total_edges_have_a_separate_budget_with_shared_dependencies(self):
        dependency, leaf = self.helper('Shared'), self.helper('Leaf')
        half = drawing._MAX_DEPENDENCY_EDGES // 2
        self.source.OutList = [dependency] * half
        dependency.OutList = [leaf] * half
        self.assertEqual(len(drawing._dependency_snapshot(self.doc, (self.source,))), 3)
        dependency.OutList.append(leaf)
        self.error(lambda: drawing._dependency_snapshot(self.doc, (self.source,)), 'DRAWING_UNSUPPORTED')


class CreatedDependencyTests(unittest.TestCase):
    setUp = DrawingTests.setUp
    apply = DrawingTests.apply
    error = DrawingTests.error

    def prepare_mode(self, automatic):
        if automatic:
            self.app.Version = lambda: ('1', '1', '4', 'Independent build')
            patches = (patch.dict('sys.modules', {'PySide6.QtCore': NS(qVersion=lambda: '6.8.3')}),
                       patch.object(drawing, '_DRAFT_TEMPLATE_SHA256',
                                    __import__('hashlib').sha256(self.template.read_bytes()).hexdigest()))
            for item in patches:
                item.start(); self.addCleanup(item.stop)
        return drawing.prepare_plan(self.app, self.gui, self.doc, self.source, controller,
                                    draft_layout=automatic)[0]

    def test_created_native_links_are_identity_sets_not_an_assumed_property_order(self):
        for automatic in (False, True):
            with self.subTest(automatic=automatic):
                self.setUp()
                plan = self.prepare_mode(automatic)
                def reorder(node):
                    if len(self.doc.created_calls) == 5:
                        page, template, *views = self.doc.Objects[1:]
                        page.OutList = list(reversed([template, *views]))
                self.doc.add_hook = reorder
                self.assertEqual(self.apply(plan)['kind'], 'drawing_draft')
                self.assertEqual(self.doc.events, ['open', 'commit'])
                self.assertEqual(len(self.doc.recompute_calls), 1)

    def test_new_page_template_and_view_cannot_add_unreviewed_dependencies(self):
        for automatic in (False, True):
            for target, attack in (('view', 'xsource'), ('view', 'expression'), ('page', 'expression'),
                                   ('template', 'expression'), ('view', 'outlist'), ('page', 'outlist'),
                                   ('template', 'outlist'), ('view', 'proxy'), ('page', 'proxy_property')):
                with self.subTest(automatic=automatic, target=target, attack=attack):
                    self.setUp()
                    plan = self.prepare_mode(automatic)
                    accesses = []
                    class Foreign:
                        def __getattribute__(self, key):
                            accesses.append(key)
                            raise AssertionError('unreviewed target must not be inspected')
                    foreign = Foreign()
                    kinds = {'view': 'TechDraw::DrawViewPart', 'page': 'TechDraw::DrawPage',
                             'template': 'TechDraw::DrawSVGTemplate'}
                    def mutate(node):
                        if node.TypeId != kinds[target]: return
                        if attack == 'xsource': node.XSource = [foreign]
                        elif attack == 'expression': node.ExpressionEngine = [('X', 'OtherDocument.Value')]
                        elif attack == 'outlist': node.OutList = [foreign]
                        elif attack == 'proxy': node.Proxy = None
                        else: node.PropertiesList.append('Proxy')
                    self.doc.add_hook = mutate
                    self.error(lambda: self.apply(plan), 'DRAWING_CREATE_FAILED')
                    self.assertEqual(self.doc.recompute_calls, [])
                    self.assertEqual(self.doc.events, ['open', 'abort'])
                    self.assertEqual(self.doc.Objects, [self.source])
                    self.assertEqual(accesses, [])

    def test_duplicate_missing_and_wrong_local_links_are_not_a_valid_created_graph(self):
        for automatic in (False, True):
            for mode in ('duplicate_page', 'duplicate_source', 'missing_source', 'local_wrong_target', 'missing_xsource'):
                with self.subTest(automatic=automatic, mode=mode):
                    self.setUp()
                    plan = self.prepare_mode(automatic)
                    def mutate(node):
                        if len(self.doc.created_calls) != 5: return
                        page, template, front, top, iso = self.doc.Objects[1:]
                        if mode == 'duplicate_page': page.OutList = [template, front, front, iso]
                        elif mode == 'duplicate_source': iso.OutList = [self.source, self.source]
                        elif mode == 'missing_source': iso.OutList = []
                        elif mode == 'local_wrong_target': iso.OutList = [template]
                        else: iso.PropertiesList.remove('XSource')
                    self.doc.add_hook = mutate
                    self.error(lambda: self.apply(plan), 'DRAWING_CREATE_FAILED')
                    self.assertEqual(self.doc.recompute_calls, [])
                    self.assertEqual(self.doc.events, ['open', 'abort'])

    def test_final_owner_and_before_write_callback_cannot_expand_the_recompute_graph(self):
        original_verify, original_run = drawing._verify_created, controller._run_transaction
        for automatic in (False, True):
            for stage in ('owner', 'before_write') if automatic else ('owner',):
                with self.subTest(automatic=automatic, stage=stage):
                    self.setUp()
                    plan = self.prepare_mode(automatic)
                    armed, injected = [], []
                    def verify(*args, **kwargs):
                        result = original_verify(*args, **kwargs)
                        if not self.doc.recompute_calls: armed.append(True)
                        return result
                    def run(app, document, title, write, rollback, committed):
                        def checked_write(native_owner):
                            def owner(*args, **kwargs):
                                result = native_owner(*args, **kwargs)
                                if armed and not injected and (stage == 'owner' or kwargs.get('before_write')):
                                    document.Objects[-1].XSource = [object()]
                                    injected.append(True)
                                return result
                            return write(owner)
                        return original_run(app, document, title, checked_write, rollback, committed)
                    with patch.object(drawing, '_verify_created', side_effect=verify), patch.object(controller, '_run_transaction', side_effect=run):
                        self.error(lambda: self.apply(plan), 'DRAWING_CREATE_FAILED')
                    self.assertEqual(injected, [True])
                    self.assertEqual(self.doc.recompute_calls, [])
                    self.assertEqual(self.doc.events, ['open', 'abort'])

    def test_recompute_and_commit_readbacks_keep_the_new_node_guard(self):
        for automatic in (False, True):
            for stage in ('recompute', 'commit'):
                with self.subTest(automatic=automatic, stage=stage):
                    self.setUp()
                    plan = self.prepare_mode(automatic)
                    def change(): self.doc.Objects[-1].ExpressionEngine = [('X', 'OtherDocument.Value')]
                    if stage == 'recompute': self.doc.recompute_hook = lambda nodes: change()
                    else: self.doc.commit_hook = change
                    if stage == 'recompute':
                        self.error(lambda: self.apply(plan), 'DRAWING_CREATE_FAILED')
                        self.assertEqual(self.doc.events, ['open', 'abort'])
                    else:
                        self.error(lambda: self.apply(plan), 'PART_COMMIT_UNVERIFIED', controller.PftWriteError)
                        self.assertEqual(self.doc.events, ['open', 'commit'])
                    self.assertEqual(len(self.doc.recompute_calls), 1)


class ProjectionInitializationTests(unittest.TestCase):
    def setUp(self):
        previous = drawing._projection_primed
        drawing._projection_primed = False
        self.addCleanup(setattr, drawing, '_projection_primed', previous)
        self.calls = []
        self.preference_calls = []
        self.preferences = {}
        self.groups = NS(GetGroup=self.group)
        self.shape = object()
        self.projected = [NS(isNull=lambda: False, Edges=[object()] * 4), None, None, None]
        def make_box(*dimensions):
            self.calls.append(('box', dimensions))
            return self.shape
        def project(shape, direction):
            self.calls.append(('project', shape, direction))
            return self.projected
        modules = patch.dict('sys.modules', {'Part': NS(makeBox=make_box), 'TechDraw': NS(project=project)})
        modules.start()
        self.addCleanup(modules.stop)
        self.app = NS(Vector=lambda *xyz: xyz, ParamGet=self.param_get)

    def group(self, name):
        self.preference_calls.append(('group', name))
        return self.preferences.setdefault(name, NS())

    def param_get(self, path):
        self.preference_calls.append(('root', path))
        return self.groups

    def test_fixed_in_memory_cube_primes_exact_projection_once(self):
        drawing._prime_projection_engine(self.app)
        drawing._prime_projection_engine(self.app)
        self.assertEqual(self.calls, [('box', (1., 1., 1.)), ('project', self.shape, (0., 0., 1.))])
        one = [('root', 'User parameter:BaseApp/Preferences/Mod/TechDraw'),
               ('group', 'General'), ('group', 'debug')]
        self.assertEqual(self.preference_calls, one + one)
        self.assertTrue(drawing._projection_primed)

    def test_existing_native_values_and_handles_are_preserved_without_value_writes(self):
        general, debug = NS(HandleFaces=False), NS(allowCrazyEdge=True)
        self.preferences.update(General=general, debug=debug)
        drawing._prime_projection_engine(self.app)
        self.assertIs(self.preferences['General'], general)
        self.assertIs(self.preferences['debug'], debug)
        self.assertEqual(vars(general), {'HandleFaces': False})
        self.assertEqual(vars(debug), {'allowCrazyEdge': True})

    def test_primed_cube_does_not_skip_native_groups_or_swallow_later_failure(self):
        drawing._projection_primed = True
        def fail(name):
            raise RuntimeError('synthetic-native-parameter-detail')
        self.groups.GetGroup = fail
        with self.assertRaises(drawing.PftDrawingError) as caught:
            drawing._prime_projection_engine(self.app)
        self.assertEqual(caught.exception.code, 'DRAWING_PROJECTION_UNAVAILABLE')
        self.assertNotIn('synthetic-native-parameter-detail', str(caught.exception))
        self.assertEqual(self.calls, [])

    def test_missing_native_parameter_api_or_group_is_not_engine_readiness(self):
        for app in (NS(Vector=lambda *xyz: xyz),
                    NS(Vector=lambda *xyz: xyz, ParamGet=lambda path: NS(GetGroup=lambda name: None))):
            with self.subTest(app=app):
                with self.assertRaises(drawing.PftDrawingError) as caught:
                    drawing._prime_projection_engine(app)
                self.assertEqual(caught.exception.code, 'DRAWING_PROJECTION_UNAVAILABLE')
                self.assertFalse(drawing._projection_primed)
                self.assertEqual(self.calls, [])

    def test_invalid_projection_does_not_claim_engine_ready(self):
        self.projected[0] = NS(isNull=lambda: True, Edges=[])
        with self.assertRaises(drawing.PftDrawingError) as caught:
            drawing._prime_projection_engine(self.app)
        self.assertEqual(caught.exception.code, 'DRAWING_PROJECTION_UNAVAILABLE')
        self.assertFalse(drawing._projection_primed)

    def test_native_failure_is_redacted_and_does_not_claim_ready(self):
        with patch.dict('sys.modules', {'TechDraw': NS(project=lambda *args: (_ for _ in ()).throw(RuntimeError('synthetic-native-detail')))}):
            with self.assertRaises(drawing.PftDrawingError) as caught:
                drawing._prime_projection_engine(self.app)
        self.assertNotIn('synthetic-native-detail', str(caught.exception))
        self.assertFalse(drawing._projection_primed)


if __name__ == '__main__':
    unittest.main()
