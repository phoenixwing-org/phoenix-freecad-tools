# SPDX-License-Identifier: Apache-2.0
"""Independent finite render inputs and owned-stage capture, no CAD process."""
import importlib.util
import gc
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
import weakref
from unittest.mock import patch

import test_pft_drawing_dimensions as f

PATH = Path(__file__).resolve().parents[2] / 'addon/PhoenixFreeCADTools/pft_drawing_export_profile.py'
spec = importlib.util.spec_from_file_location('export_profile_fixture', PATH)
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)
NS = types.SimpleNamespace


class Quantity:
    __module__ = 'Base'
    def __init__(self, value):
        self.Value, self.Unit = float(value), NS(Signature=(1, 0, 0, 0, 0, 0, 0, 0))


class Shape(f.fixtures.Shape):
    __module__ = 'Part'
    def __init__(self):
        super().__init__()
        self.brep = 'neutral complete topology A'
    def exportBrepToString(self): return self.brep


class ProjectedEdge(f.Edge):
    __module__ = 'Part'
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.Orientation = 'Forward'
        self.brep = repr((args, kwargs))
        self.BoundBox = NS(XMin=0., YMin=0., ZMin=0., XMax=10., YMax=10., ZMax=0.)
    def isNull(self): return False
    def isValid(self): return True
    def exportBrepToString(self): return self.brep


class Parameters:
    def __init__(self): self.data = {}
    def __getattr__(self, name):
        if name.startswith('Get'):
            kind = name[3:]
            return lambda key, default: self.data.get((kind, key), default)
        if name.startswith('Rem'):
            return lambda key: self.data.pop((name[3:], key), None)
        if name.startswith('Set'):
            return lambda key,value: self.data.__setitem__((name[3:],key),value)
        raise AttributeError(name)


def provider(obj):
    value = NS(Object=obj, TypeId='Gui::NativeViewProvider', PropertiesList=['Visibility'], Visibility=True)
    value.getTypeIdOfProperty = lambda name: 'App::PropertyBool' if name == 'Visibility' else 'App::PropertyFont'
    return value


def properties(obj, values):
    obj.PropertiesList = list(values)
    obj.getTypeIdOfProperty = values.__getitem__


class DrawingExportProfileTests(unittest.TestCase):
    def setUp(self):
        self.base = f.DrawingDimensionsTests('test_preview_reads_real_projected_reference_without_writes_or_recompute')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.app, self.gui, self.doc = self.base.app, self.base.gui, self.base.doc
        self.source, self.page, self.view, self.template = self.base.source, self.base.page, self.base.view, self.base.template
        self.source.Shape = Shape()
        self.base.part.Edge = ProjectedEdge
        self.view.edges = {"Edge" + str(i): ProjectedEdge() for i in range(4)}
        self.view.edges['Edge4'] = ProjectedEdge(radius=4.)
        self.projected = list(self.view.edges.values())
        self.hidden_projected = []
        self.projection_reads = []
        def visible(conventional):
            self.projection_reads.append(('visible', conventional))
            return self.projected[:]
        def hidden(conventional):
            self.projection_reads.append(('hidden', conventional))
            return self.hidden_projected[:]
        def indexed(index):
            if index >= len(self.projected) + len(self.hidden_projected): raise ValueError('missing')
            return (self.projected + self.hidden_projected)[index]
        self.view.getVisibleEdges, self.view.getHiddenEdges, self.view.getEdgeByIndex = visible, hidden, indexed
        material=NS(DiffuseColor=(.2,.3,.4,1.),AmbientColor=(.2,.2,.2,1.),SpecularColor=(0.,0.,0.,1.),EmissiveColor=(0.,0.,0.,1.),Shininess=0.5,Transparency=0.)
        self.source.ViewObject = NS(Visibility=True, ShapeColor=(.2, .3, .4), LineColor=(0., 0., 0.),
            PointColor=(0., 0., 0.), Transparency=0, LineWidth=2., PointSize=2., DisplayMode='Flat Lines',
            PropertiesList=['ShapeAppearance','Transparency'],ShapeAppearance=[material],getTypeIdOfProperty=lambda key:'App::PropertyMaterialList')
        self.doc.FileName = ''
        self.gui_doc = NS(getInEdit=lambda: None, Modified=True)
        self.gui.getDocument = lambda name: self.gui_doc
        self.selections = []
        self.gui.Selection = NS(getSelectionEx=lambda: self.selections,
                                getPreselection=lambda: NS(DocumentName='', ObjectName='', SubElementNames=['']))
        self.temp = tempfile.TemporaryDirectory(prefix='pft-export-profile-test-', dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        resource = self.root / 'Mod' / 'TechDraw' / 'LineGroup'
        resource.mkdir(parents=True)
        for stem in ('A', 'B'):
            for suffix in ('LineDef', 'ElementDef'):
                (resource / (stem + '.' + suffix + '.csv')).write_text('neutral,line,definition\n')
        svg = self.root / 'neutral.svg'
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="297mm" height="210mm"><text>Neutral</text></svg>')
        self.template.Template = self.template.PageResult = str(svg)
        self.template.Width, self.template.Height = Quantity(297), Quantity(210)
        self.groups = {}
        self.app.ParamGet = lambda name: self.groups.setdefault(name, Parameters())
        self.app.getResourceDir = lambda: str(self.root)
        self.app.Version = lambda: ('1', '1', '4')
        self.page.Visibility = True
        properties(self.page, {'Label':'App::PropertyString', 'Visibility':'App::PropertyBool', 'Template':'App::PropertyLink',
            'Views':'App::PropertyLinkList', 'Scale':'App::PropertyFloatConstraint',
            'ProjectionType':'App::PropertyEnumeration'})
        properties(self.template, {'Label':'App::PropertyString', 'Template':'App::PropertyFile',
            'PageResult':'App::PropertyFileIncluded', 'EditableTexts':'App::PropertyMap',
            'Width':'App::PropertyLength', 'Height':'App::PropertyLength', 'Orientation':'App::PropertyEnumeration',
            'ExpressionEngine':'App::PropertyExpressionEngine'})
        self.view.XSource, self.view.CosmeticVertexes, self.view.GeomFormats = [], [], []
        properties(self.view, {'Label':'App::PropertyString', 'Source':'App::PropertyLinkList',
            'XSource':'App::PropertyXLinkList', 'Direction':'App::PropertyVector', 'XDirection':'App::PropertyVector',
            'CoarseView':'App::PropertyBool', 'Perspective':'App::PropertyBool',
            'CosmeticEdges':'TechDraw::PropertyCosmeticEdgeList', 'CenterLines':'TechDraw::PropertyCenterLineList',
            'CosmeticVertexes':'TechDraw::PropertyCosmeticVertexList', 'GeomFormats':'TechDraw::PropertyGeomFormatList'})
        self.dims = []
        for index, kind in enumerate(('Distance', 'DistanceX', 'DistanceY', 'Diameter', 'Radius')):
            dim = f.Dimension('NeutralDimension' + str(index))
            dim.Document, dim.State = self.doc, ['Up-to-date']
            dim.Type, dim.MeasureType, dim.Arbitrary = kind, 'Projected', False
            dim.FormatSpec = '%.2f'
            dim.References2D = [(self.view, ('Edge4' if kind in ('Diameter', 'Radius') else 'Edge0',))]
            properties(dim, {'Type':'App::PropertyEnumeration', 'MeasureType':'App::PropertyEnumeration',
                'Arbitrary':'App::PropertyBool', 'References2D':'App::PropertyLinkSubList',
                'References3D':'App::PropertyLinkSubList', 'FormatSpec':'App::PropertyString'})
            self.doc.Objects.append(dim)
            self.page.Views.append(dim)
            self.dims.append(dim)
        for obj in (self.page, self.template, self.view, *self.dims): obj.ViewObject = provider(obj)
        self.page.OutList, self.view.OutList = [self.template, *self.page.Views], [self.source]
        for dim in self.dims: dim.OutList = [self.view]
        qt_patch=patch.object(profile,'_qt',return_value=(NS(),NS(),NS()));qt_patch.start();self.addCleanup(qt_patch.stop)
        fonts_patch=patch.object(profile,'_svg_fonts',return_value=('neutral font fingerprint',));fonts_patch.start();self.addCleanup(fonts_patch.stop)
        self.scene_object = object()
        self.scene_value = ('neutral complete scene',)
        self.scene_patch = patch.object(profile, '_scene', side_effect=lambda page, gui: (self.scene_object, self.scene_value))
        self.scene_patch.start(); self.addCleanup(self.scene_patch.stop)
        self.native = NS(exportPageAsPdf=self.export)
        self.module_patch = patch.dict('sys.modules', {'TechDrawGui': self.native})
        self.module_patch.start(); self.addCleanup(self.module_patch.stop)
        self.export_calls = 0
        self.export_hook = None
        self.guard_calls = 0
        self.paper_paints = []
        self.render_hook = None
        def render_paper(scene, paper, *, guard):
            guard(); self.paper_paints.append((scene, paper))
            if self.render_hook is not None: self.render_hook()
            guard()
        paper_patch = patch.object(profile, '_render_paper', side_effect=render_paper)
        paper_patch.start(); self.addCleanup(paper_patch.stop)

    def export(self, page, path):
        self.export_calls += 1
        Path(path).write_bytes(b'%PDF-neutral-frozen-capture')
        if self.export_hook: self.export_hook(Path(path))

    def guard(self): self.guard_calls += 1

    def snapshot(self): return profile.snapshot(self.app, self.gui, self.doc, self.page, f.controller)
    def verify(self, context, **kwargs): return profile.verify(self.app, self.gui, context, f.controller, **kwargs)
    def capture(self, context, **kwargs):
        return profile.capture_pdf(self.app, self.gui, context, f.controller, guard=kwargs.pop('guard', self.guard), **kwargs)
    def reject(self, callback, code=None):
        with self.assertRaises(profile.PftDrawingExportProfileError) as error: callback()
        if code: self.assertEqual(error.exception.code, code)
        self.assertNotIn('private-untrusted', str(error.exception))
        return error.exception

    def test_prepare_reads_full_five_kind_profile_without_rendering_or_cad_writes(self):
        context = self.snapshot()
        self.assertEqual(context['public_summary']['dimension_count'], 5)
        self.assertEqual(context['public_summary']['view_count'], 1)
        self.assertTrue(context['public_summary']['manual_review_required'])
        self.assertEqual(context['public_summary']['projection_verification'], 'not_checked')
        self.assertEqual(context['public_summary']['paper_mm'], [297., 210.])
        self.assertEqual(self.export_calls, 0)
        self.assertEqual(self.doc.events, [])
        self.assertEqual(self.doc.recompute_calls, [])

    def test_global_source_selection_is_frozen_and_preserved_not_a_blanket_refusal(self):
        self.selections.append(NS(DocumentName=self.doc.Name, ObjectName=self.source.Name, SubElementNames=[]))
        context = self.snapshot()
        self.verify(context)
        self.capture(context)
        self.assertEqual(self.selections[0].ObjectName, self.source.Name)
        self.assertEqual(self.export_calls, 1)

    def test_geometry_with_same_bbox_and_metrics_but_different_brep_is_stale(self):
        context = self.snapshot()
        self.source.Shape.brep = 'neutral complete topology B'
        self.reject(lambda: self.verify(context), 'DRAWING_EXPORT_STALE')
        self.assertEqual(self.export_calls, 0)

    def test_style_font_format_resource_preference_or_selection_changes_are_stale(self):
        for mutation in (
                lambda: setattr(self.dims[0], 'FormatSpec', '%.4f'),
                lambda: setattr(self.template, 'EditableTexts', {'title':'Changed neutral title'}),
                lambda: setattr(self.dims[0].ViewObject, 'Visibility', False),
                lambda: self.groups['User parameter:BaseApp/Preferences/Mod/TechDraw/Colors'].data.update({('Unsigned','PageColor'):123}),
                lambda: self.selections.append(NS(DocumentName=self.doc.Name,ObjectName=self.source.Name,SubElementNames=[])),
                lambda: Path(self.template.PageResult).write_text('<svg xmlns="http://www.w3.org/2000/svg"><path/></svg>')):
            with self.subTest(mutation=mutation):
                self.setUp(); context = self.snapshot(); mutation()
                self.reject(lambda: self.verify(context), 'DRAWING_EXPORT_STALE')

    def test_unknown_property_proxy_external_reference_and_extra_page_type_fail_closed(self):
        for mutation in (
                lambda: setattr(self.view, 'Proxy', object()),
                lambda: self.view.PropertiesList.append('Proxy'),
                lambda: setattr(self.view, 'XSource', [self.source]),
                lambda: setattr(self.view, 'GeomFormats', [object()]),
                lambda: setattr(self.dims[0], 'Arbitrary', True),
                lambda: setattr(self.dims[0], 'References3D', [(self.source, ('Edge1',))]),
                lambda: setattr(self.page, 'TypeId', 'TechDraw::DrawPagePython'),
                lambda: setattr(self.template, 'ExpressionEngine', [('Width','1')])):
            with self.subTest(mutation=mutation):
                self.setUp(); mutation(); self.reject(self.snapshot)

    def test_source_other_dirty_or_invalid_target_cache_flags_are_not_export_exempt(self):
        for obj, flags in ((self.source, ['Touched']), (self.view, ['Touched']),
                           (self.template, ['Touched','Error']), (self.dims[0], ['Invalid'])):
            old = obj.State;obj.State = flags
            self.reject(self.snapshot)
            obj.State = old

    def test_post_capture_allows_only_target_cache_flags_not_source_or_style_changes(self):
        context = self.snapshot()
        self.view.State = self.dims[0].State = ['Touched']
        self.reject(lambda: self.verify(context), 'DRAWING_EXPORT_STALE')
        self.reject(lambda: self.verify(context, after_capture=True), 'DRAWING_EXPORT_STALE')
        context['_capture_phase'] = 'native'
        self.verify(context, after_capture=True)
        self.dims[0].FormatSpec = 'changed'
        self.reject(lambda: self.verify(context, after_capture=True), 'DRAWING_EXPORT_STALE')

    def test_touched_page_and_dimensions_are_frozen_until_native_capture(self):
        cached = (self.page, *self.dims)
        for obj in cached: obj.State = ['Touched']
        context = self.snapshot()
        self.verify(context)
        self.assertEqual(self.doc.recompute_calls, [])
        self.assertEqual(self.export_calls, 0)
        self.page.State = ['Up-to-date']
        self.reject(lambda: self.verify(context), 'DRAWING_EXPORT_STALE')
        self.page.State = ['Touched']
        def refresh(path):
            for obj in cached: obj.State = ['Up-to-date']
        self.export_hook = refresh
        result = self.capture(context)
        self.assertEqual(result['capture_state'], 'captured')
        self.assertEqual(self.export_calls, 1)
        self.assertEqual(self.doc.recompute_calls, [])

    def test_touched_cache_exception_does_not_cover_unrelated_native_nodes(self):
        other = f.fixtures.NativeObject('UnrelatedNative')
        other.Document, other.State = self.doc, ['Touched']
        self.doc.Objects.append(other)
        self.page.State = self.dims[0].State = ['Touched']
        self.reject(self.snapshot, 'DRAWING_EXPORT_UNSUPPORTED')
        self.assertEqual(self.export_calls, 0)

    def test_export_rejects_foreign_proxy_or_expression_source_dependencies(self):
        for mode in ('foreign', 'proxy', 'expression'):
            with self.subTest(mode=mode):
                dependency = f.fixtures.NativeObject('NativeDependency')
                dependency.Document = self.doc if mode != 'foreign' else object()
                if mode == 'proxy': dependency.Proxy = None
                if mode == 'expression': dependency.ExpressionEngine = [('Length', '1 + 1')]
                if mode != 'foreign': self.doc.Objects.append(dependency)
                self.source.OutList = [dependency]
                self.reject(self.snapshot, 'DRAWING_EXPORT_UNSUPPORTED')
                self.source.OutList = []
                if mode != 'foreign': self.doc.Objects.remove(dependency)
        self.assertEqual(self.export_calls, 0)
        self.assertEqual(self.doc.recompute_calls, [])

    def test_changed_dependency_edges_are_stale_before_and_during_capture(self):
        before, after = (f.fixtures.NativeObject(name) for name in ('DependencyA', 'DependencyB'))
        for obj in (before, after):
            obj.Document = self.doc
            self.doc.Objects.append(obj)
        self.source.OutList = [before]
        context = self.snapshot()
        self.source.OutList = [after]
        self.reject(lambda: self.verify(context), 'DRAWING_EXPORT_STALE')
        self.assertEqual(self.export_calls, 0)
        self.source.OutList = [before]
        self.export_hook = lambda path: setattr(self.source, 'OutList', [after])
        error = self.reject(lambda: self.capture(context), 'DRAWING_EXPORT_STALE')
        self.assertEqual(error.capture_state, 'unknown')
        self.assertEqual(error.cleanup_state, 'complete')
        self.assertEqual(self.export_calls, 1)

    def test_bound_drawing_nodes_cannot_add_unplanned_native_dependencies(self):
        for obj in (self.page, self.template, self.view, self.dims[0]):
            with self.subTest(node=obj.Name):
                before = obj.OutList
                obj.OutList = [*before, self.source]
                self.reject(self.snapshot, 'DRAWING_EXPORT_UNSUPPORTED')
                obj.OutList = before
        self.assertEqual(self.export_calls, 0)

    def test_unexpected_dependency_target_is_never_inspected(self):
        class Unbound:
            def __getattribute__(self, key):
                raise AssertionError('unbound native dependency must not be inspected')
        self.template.OutList = [Unbound()]
        self.reject(self.snapshot, 'DRAWING_EXPORT_UNSUPPORTED')
        self.assertEqual(self.export_calls, 0)

    def test_bound_dependency_order_is_frozen_even_with_the_same_members(self):
        context = self.snapshot()
        self.page.OutList.reverse()
        self.reject(lambda: self.verify(context), 'DRAWING_EXPORT_STALE')
        self.assertEqual(self.export_calls, 0)

    def test_capture_returns_exact_once_frozen_bytes_and_owned_stage_is_removed(self):
        context = self.snapshot(); paths = []
        self.export_hook = lambda path: paths.append(path)
        result = self.capture(context)
        self.assertEqual(result['bytes'], b'%PDF-neutral-frozen-capture')
        self.assertEqual(result['capture_state'], 'captured')
        self.assertEqual(result['cleanup_state'], 'complete')
        self.assertTrue(self.guard_calls >= 4)
        self.assertFalse(paths[0].exists()); self.assertFalse(paths[0].parent.exists())
        self.reject(lambda: self.capture(context), 'DRAWING_EXPORT_STALE')
        self.assertEqual(self.export_calls, 1)
        self.assertEqual(self.doc.events, [])

    def test_full_projected_cache_geometry_orientation_order_and_fuzz_are_frozen(self):
        changes = (
            lambda: setattr(self.projected[0], 'brep', 'changed curve with the same bounding box'),
            lambda: setattr(self.projected[0], 'Orientation', 'Reversed'),
            lambda: self.projected.reverse(),
            lambda: setattr(self.projected[0].BoundBox, 'XMax', 11.),
            lambda: self.groups['User parameter:BaseApp/Preferences/Mod/TechDraw/General'].data.__setitem__(('Float','EdgeFuzz'), 11.),
        )
        for change in changes:
            with self.subTest(change=change):
                self.setUp(); context = self.snapshot()
                self.assertTrue(self.projection_reads)
                self.assertTrue(all(flag is False for _, flag in self.projection_reads))
                change()
                self.reject(lambda: self.verify(context), 'DRAWING_EXPORT_STALE')
                self.assertEqual(self.export_calls, 0)
                self.assertEqual(self.doc.events, [])

    def test_projection_cache_rejects_unknown_type_excess_count_and_missing_index(self):
        for replacement in (lambda: setattr(self.view, 'getVisibleEdges', lambda flag: tuple(self.projected)),
                            lambda: setattr(self.view, 'getVisibleEdges', lambda flag: [self.projected[0]] * (profile.MAX_PROJECTED_EDGES+1)),
                            lambda: setattr(self.view, 'getEdgeByIndex', lambda index: (_ for _ in ()).throw(ValueError('missing')))):
            with self.subTest(replacement=replacement):
                self.setUp(); replacement()
                self.reject(self.snapshot)
                self.assertEqual(self.doc.events, [])

    def test_verification_preserves_unknown_clone_cleanup_in_fixed_stale_error(self):
        context = self.snapshot()
        failure = profile.PftDrawingExportProfileError('DRAWING_EXPORT_UNSUPPORTED', cleanup_state='unknown')
        with patch.object(profile, '_snapshot', side_effect=failure):
            error = self.reject(lambda: self.verify(context), 'DRAWING_EXPORT_STALE')
        self.assertEqual(error.cleanup_state, 'unknown')

    def test_capture_keeps_unknown_clone_cleanup_and_uses_actual_capture_phase(self):
        for fail_after_native in (False, True):
            with self.subTest(fail_after_native=fail_after_native):
                self.setUp(); context = self.snapshot(); calls = [0]
                original = profile.verify
                def verify(*args, **kwargs):
                    calls[0] += 1
                    if (fail_after_native and self.export_calls) or (not fail_after_native and calls[0] == 2):
                        raise profile.PftDrawingExportProfileError('DRAWING_EXPORT_STALE', cleanup_state='unknown')
                    return original(*args, **kwargs)
                with patch.object(profile, 'verify', side_effect=verify):
                    error = self.reject(lambda: self.capture(context), 'DRAWING_EXPORT_STALE')
                self.assertEqual(error.cleanup_state, 'unknown')
                self.assertEqual(error.capture_state, 'unknown' if fail_after_native else 'not_captured')
                self.assertEqual(self.export_calls, 1 if fail_after_native else 0)

    def test_native_guard_required_and_invalid_guard_never_enters_native(self):
        context = self.snapshot()
        for guard in (None, False, 'private-untrusted'):
            self.reject(lambda: self.capture(context, guard=guard), 'DRAWING_EXPORT_NATIVE_CONFIRMATION_REQUIRED')
        self.reject(lambda: self.capture(context, guard=lambda: False), 'DRAWING_EXPORT_STALE')
        self.assertEqual(self.export_calls, 0)

    def test_native_capture_geometry_or_scene_change_is_unknown_and_not_retried(self):
        for hook in (lambda path: setattr(self.source.Shape, 'brep', 'changed geometry'),
                     lambda path: setattr(self, 'scene_value', ('changed scene',))):
            with self.subTest(hook=hook):
                self.setUp(); context = self.snapshot(); self.export_hook = hook
                error = self.reject(lambda: self.capture(context), 'DRAWING_EXPORT_STALE')
                self.assertEqual(error.capture_state, 'unknown')
                self.assertEqual(error.cleanup_state, 'complete')
                self.reject(lambda: self.capture(context))
                self.assertEqual(self.export_calls, 1)

    def test_partial_bad_or_oversize_native_output_does_not_become_capture_success(self):
        for data in (b'', b'not-a-pdf', b'%PDF-' + b'0' * profile.MAX_PDF_BYTES):
            with self.subTest(size=len(data)):
                self.setUp(); context = self.snapshot(); self.export_hook = lambda path: path.write_bytes(data)
                error = self.reject(lambda: self.capture(context))
                self.assertEqual(error.capture_state, 'unknown')
                self.assertEqual(self.export_calls, 1)

    def test_unknown_stage_content_is_preserved_and_cleanup_is_separate(self):
        context = self.snapshot(); folders = []
        def extra(path):
            folders.append(path.parent)
            (path.parent/'unknown-content').write_text('preserve')
        self.export_hook = extra
        result = self.capture(context)
        self.assertEqual(result['capture_state'], 'captured')
        self.assertEqual(result['cleanup_state'], 'unknown')
        self.assertTrue((folders[0]/'unknown-content').exists())
        (folders[0]/'unknown-content').unlink();folders[0].rmdir()

    def test_foreign_replacement_on_native_failure_is_not_deleted_or_adopted(self):
        context = self.snapshot(); paths = []
        def replace_then_fail(path):
            paths.append(path)
            foreign = path.parent / 'foreign-stage'
            foreign.write_bytes(b'%PDF-foreign-bytes')
            foreign.replace(path)
            raise RuntimeError('private-untrusted')
        self.export_hook = replace_then_fail
        error = self.reject(lambda: self.capture(context), 'DRAWING_EXPORT_CAPTURE_UNVERIFIED')
        self.assertEqual(error.capture_state, 'unknown')
        self.assertEqual(error.cleanup_state, 'unknown')
        self.assertEqual(paths[0].read_bytes(), b'%PDF-foreign-bytes')
        paths[0].unlink(); paths[0].parent.rmdir()

    def test_owned_stage_rename_or_external_hardlink_is_not_reported_cleaned(self):
        for operation in ('rename', 'hardlink'):
            with self.subTest(operation=operation):
                self.setUp(); context = self.snapshot(); paths = []
                retained = self.root / 'retained-native.pdf'
                def move_or_link_then_fail(path):
                    paths.append(path)
                    if operation == 'rename':
                        path.rename(retained)
                    else:
                        os.link(path, retained)
                    raise RuntimeError('private-untrusted-native-failure')
                self.export_hook = move_or_link_then_fail
                error = self.reject(lambda: self.capture(context), 'DRAWING_EXPORT_CAPTURE_UNVERIFIED')
                self.assertEqual(error.capture_state, 'unknown')
                self.assertEqual(error.cleanup_state, 'unknown')
                self.assertEqual(retained.read_bytes(), b'%PDF-neutral-frozen-capture')
                self.assertTrue(paths[0].parent.exists())
                if operation == 'hardlink':
                    self.assertTrue(paths[0].exists())
                    self.assertEqual(paths[0].stat().st_nlink, 2)
                    paths[0].unlink()
                retained.unlink(); paths[0].parent.rmdir()

    def test_reserved_handle_close_failure_before_effect_is_unknown_without_reclosing(self):
        context = self.snapshot(); held = []; real_close = os.close
        stage = self.root / 'reserved-stage'; stage.mkdir(mode=0o700)
        def close_once(fd):
            if not held:
                held.append(fd)
                raise RuntimeError('private-untrusted-close-error')
            real_close(fd)
        try:
            with patch.object(profile.tempfile, 'mkdtemp', return_value=str(stage)), \
                    patch.object(profile.os, 'close', side_effect=close_once):
                error = self.reject(lambda: self.capture(context), 'DRAWING_EXPORT_CAPTURE_FAILED')
            self.assertEqual(error.capture_state, 'not_captured')
            self.assertEqual(error.cleanup_state, 'unknown')
            self.assertEqual(self.export_calls, 0)
            os.fstat(held[0])  # Our injected failed close really retained it.
            self.assertTrue(stage.exists())
            self.assertEqual(list(stage.iterdir()), [])
        finally:
            if held: real_close(held[0])
            if stage.exists(): stage.rmdir()

    def test_parent_handle_close_failure_is_unknown_without_double_close_or_raw_error(self):
        context = self.snapshot(); paths = []; armed = [False]; close_calls = []
        real_close = os.close
        def hook(path):
            paths.append(path)
            armed[0] = True
        def close_once(fd):
            real_close(fd)
            if armed[0]:
                close_calls.append(fd)
                raise RuntimeError('private-untrusted-close-error')
        self.export_hook = hook
        with patch.object(profile.os, 'close', side_effect=close_once):
            result = self.capture(context)
        self.assertEqual(result['capture_state'], 'captured')
        self.assertEqual(result['cleanup_state'], 'unknown')
        self.assertEqual(len(close_calls), 1)
        self.assertFalse(paths[0].exists())
        paths[0].parent.rmdir()

    def test_native_opens_the_exact_reserved_private_file_before_rendering(self):
        context = self.snapshot(); observed = []
        original = self.native.exportPageAsPdf
        def export(page, path):
            info = Path(path).stat()
            observed.append((info.st_ino, info.st_mode & 0o777, info.st_size))
            original(page, path)
            self.assertEqual(Path(path).stat().st_ino, observed[0][0])
        self.native.exportPageAsPdf = export
        self.capture(context)
        self.assertEqual(observed[0][1:], (0o600, 0))

    def test_closed_page_requires_explicit_open_then_deferred_scene_binding(self):
        self.scene_value = None
        self.page.Visibility = self.page.ViewObject.Visibility = False
        def show():
            self.page.Visibility = self.page.ViewObject.Visibility = True
        self.page.ViewObject.show = show
        context = self.snapshot()
        self.assertFalse(context['public_summary']['page_open'])
        self.reject(lambda: self.capture(context), 'DRAWING_EXPORT_PAGE_OPEN_REQUIRED')
        opened = profile.open_page(self.app,self.gui,context,f.controller,guard=self.guard)
        self.assertTrue(opened['pending_open'])
        self.scene_value = ('opened bound scene',)
        bound = profile.bind_open_scene(self.app,self.gui,opened,f.controller,guard=self.guard)
        self.assertTrue(bound['public_summary']['page_open'])
        result = self.capture(bound)
        self.assertEqual(result['capture_state'], 'captured')

    def test_closed_page_native_bool_pair_only_is_authorized_during_exact_show(self):
        self.scene_value = None
        self.page.Visibility = self.page.ViewObject.Visibility = False
        before = self.snapshot()
        before_protected = before['data']['protected']
        self.page.ViewObject.show = lambda: (setattr(self.page, 'Visibility', True),
                                           setattr(self.page.ViewObject, 'Visibility', True))
        opened = profile.open_page(self.app, self.gui, before, f.controller, guard=self.guard)
        self.assertEqual(before['data']['protected'], before_protected)
        self.assertFalse(profile._page_visibility(before['data'], id(self.page))[-1])
        self.assertTrue(profile._page_visibility(opened['data'], id(self.page))[-1])
        self.assertFalse(opened['_opened_was_open'])
        self.assertEqual(self.export_calls, 0)

    def test_closed_show_single_sided_or_extra_mutation_is_not_authorized(self):
        for mutation in (
                lambda: setattr(self.page, 'Visibility', True),
                lambda: setattr(self.page.ViewObject, 'Visibility', True),
                lambda: (setattr(self.page, 'Visibility', True), setattr(self.page.ViewObject, 'Visibility', True),
                         setattr(self.source.Shape, 'brep', 'changed source')),
                lambda: (setattr(self.page, 'Visibility', True), setattr(self.page.ViewObject, 'Visibility', True),
                         setattr(self.dims[0].ViewObject, 'Visibility', False)),
                lambda: (setattr(self.page, 'Visibility', True), setattr(self.page.ViewObject, 'Visibility', True),
                         self.selections.append(NS(DocumentName=self.doc.Name,ObjectName=self.source.Name,SubElementNames=[])))):
            with self.subTest(mutation=mutation):
                self.setUp(); self.scene_value = None
                self.page.Visibility = self.page.ViewObject.Visibility = False
                context = self.snapshot(); self.page.ViewObject.show = mutation
                self.reject(lambda: profile.open_page(self.app, self.gui, context, f.controller, guard=self.guard),
                            'DRAWING_EXPORT_STALE')
                self.assertEqual(self.export_calls, 0); self.assertEqual(self.paper_paints, [])

    def test_page_visibility_exception_is_not_available_after_show_freeze(self):
        self.scene_value = None
        self.page.Visibility = self.page.ViewObject.Visibility = False
        self.page.ViewObject.show = lambda: (setattr(self.page, 'Visibility', True), setattr(self.page.ViewObject, 'Visibility', True))
        opened = profile.open_page(self.app, self.gui, self.snapshot(), f.controller, guard=self.guard)
        self.page.Visibility = self.page.ViewObject.Visibility = False
        self.scene_value = ('native opened scene',)
        self.reject(lambda: profile.bind_open_scene(self.app, self.gui, opened, f.controller, guard=self.guard),
                    'DRAWING_EXPORT_STALE')
        self.assertEqual(self.paper_paints, [])

    def test_paired_visibility_before_show_must_be_consistent(self):
        self.scene_value = None; self.page.Visibility = False
        calls = []; self.page.ViewObject.show = lambda: calls.append('show')
        self.reject(lambda: profile.open_page(self.app, self.gui, self.snapshot(), f.controller, guard=self.guard),
                    'DRAWING_EXPORT_STALE')
        self.assertEqual(calls, [])

    def test_deferred_open_binding_rechecks_source_and_global_selection(self):
        self.scene_value = None;self.page.ViewObject.show = lambda: None
        opened = profile.open_page(self.app,self.gui,self.snapshot(),f.controller,guard=self.guard)
        self.scene_value = ('opened scene',)
        self.selections.append(NS(DocumentName=self.doc.Name,ObjectName=self.source.Name,SubElementNames=[]))
        self.reject(lambda: profile.bind_open_scene(self.app,self.gui,opened,f.controller,guard=self.guard), 'DRAWING_EXPORT_STALE')
        self.assertEqual(self.export_calls, 0)

    def test_already_open_page_is_shown_and_whole_paper_refrozen_before_export(self):
        calls = []
        self.page.ViewObject.show = lambda: calls.append('bound-show')
        old = self.snapshot()
        opened = profile.open_page(self.app, self.gui, old, f.controller, guard=self.guard)
        self.assertEqual(calls, ['bound-show'])
        self.assertTrue(opened['pending_open']); self.assertTrue(opened['_opened_was_open'])
        self.render_hook = lambda: setattr(self, 'scene_value', ('new complete scene',))
        bound = profile.bind_open_scene(self.app, self.gui, opened, f.controller, guard=self.guard)
        self.assertEqual(bound['scene'], ('new complete scene',))
        self.assertEqual(len(self.paper_paints), 1)
        self.assertIs(self.paper_paints[0][0], self.scene_object)
        self.assertEqual(self.paper_paints[0][1], (297., 210.))
        self.assertEqual(self.export_calls, 0)
        self.capture(bound)
        self.assertEqual(self.export_calls, 1)
        self.assertEqual(self.doc.recompute_calls, [])

    def test_show_source_mutation_is_not_refrozen_as_an_allowed_refresh(self):
        self.page.ViewObject.show = lambda: setattr(self.source.Shape, 'brep', 'changed neutral topology')
        error = self.reject(lambda: profile.open_page(self.app, self.gui, self.snapshot(), f.controller, guard=self.guard),
                            'DRAWING_EXPORT_STALE')
        self.assertEqual(error.cad_protection_state, 'unverified')
        self.assertEqual(error.capture_state, 'not_captured')
        self.assertEqual(self.paper_paints, [])

    def test_refresh_rejects_source_or_existing_scene_replacement_before_paint(self):
        self.page.ViewObject.show = lambda: None
        opened = profile.open_page(self.app, self.gui, self.snapshot(), f.controller, guard=self.guard)
        self.scene_object = object()
        self.reject(lambda: profile.bind_open_scene(self.app, self.gui, opened, f.controller, guard=self.guard),
                    'DRAWING_EXPORT_STALE')
        self.assertEqual(self.paper_paints, [])

    def test_paint_source_or_scene_change_is_detected_before_native_export(self):
        for mutation in (lambda: setattr(self.source.Shape, 'brep', 'changed during paint'),
                         lambda: setattr(self, 'scene_object', object())):
            with self.subTest(mutation=mutation):
                self.setUp(); self.page.ViewObject.show = lambda: None
                opened = profile.open_page(self.app, self.gui, self.snapshot(), f.controller, guard=self.guard)
                self.render_hook = mutation
                error = self.reject(lambda: profile.bind_open_scene(self.app, self.gui, opened, f.controller, guard=self.guard),
                                    'DRAWING_EXPORT_STALE')
                self.assertEqual(error.capture_state, 'not_captured')
                self.assertEqual(error.cad_protection_state, 'unverified')
                self.assertEqual(self.export_calls, 0)

    def test_refreshed_scene_is_still_strict_after_the_one_native_capture(self):
        self.page.ViewObject.show = lambda: None
        opened = profile.open_page(self.app, self.gui, self.snapshot(), f.controller, guard=self.guard)
        self.render_hook = lambda: setattr(self, 'scene_value', ('refreshed before freeze',))
        bound = profile.bind_open_scene(self.app, self.gui, opened, f.controller, guard=self.guard)
        self.export_hook = lambda path: setattr(self, 'scene_value', ('changed after freeze',))
        self.reject(lambda: self.capture(bound), 'DRAWING_EXPORT_STALE')
        self.assertEqual(self.export_calls, 1)

    def test_bind_carries_render_cleanup_unknown_and_ends_before_native_capture(self):
        self.page.ViewObject.show = lambda: None
        opened = profile.open_page(self.app, self.gui, self.snapshot(), f.controller, guard=self.guard)
        failure = profile.PftDrawingExportProfileError('DRAWING_EXPORT_STALE', cleanup_state='unknown')
        with patch.object(profile, '_render_paper', side_effect=failure):
            error = self.reject(lambda: profile.bind_open_scene(self.app, self.gui, opened, f.controller, guard=self.guard))
        self.assertIs(error, failure)
        self.assertEqual(error.cleanup_state, 'unknown'); self.assertEqual(error.capture_state, 'not_captured')
        self.assertEqual(self.export_calls, 0)

    def test_scene_support_failure_is_checked_before_any_paint(self):
        self.page.ViewObject.show = lambda: None
        opened = profile.open_page(self.app, self.gui, self.snapshot(), f.controller, guard=self.guard)
        failure = profile.PftDrawingExportProfileError('DRAWING_EXPORT_UNSUPPORTED', cleanup_state='unknown')
        with patch.object(profile, '_scene', side_effect=failure):
            error = self.reject(lambda: profile.bind_open_scene(self.app, self.gui, opened, f.controller, guard=self.guard))
        self.assertIs(error, failure)
        self.assertEqual(self.paper_paints, []); self.assertEqual(self.export_calls, 0)
        self.assertEqual(error.cleanup_state, 'unknown')

    def test_bind_failure_trace_releases_local_borrowed_scene_and_current_snapshots(self):
        self.page.ViewObject.show = lambda: None
        opened = profile.open_page(self.app, self.gui, self.snapshot(), f.controller, guard=self.guard)
        self.render_hook = lambda: setattr(self.source.Shape, 'brep', 'changed during paint')
        error = None
        try:
            profile.bind_open_scene(self.app, self.gui, opened, f.controller, guard=self.guard)
        except profile.PftDrawingExportProfileError as failure:
            error = failure
        self.assertIsNotNone(error)
        trace = error.__traceback__; inspected = False
        while trace is not None:
            if trace.tb_frame.f_code.co_name == 'bind_open_scene':
                inspected = True
                for name in ('scene', 'semantic', 'data', 'current', 'context', 'opened_context'):
                    self.assertIsNone(trace.tb_frame.f_locals[name])
            trace = trace.tb_next
        self.assertTrue(inspected)

    def test_svg_rejects_active_or_external_content_but_preserves_license_metadata(self):
        for body in ('<script/>', '<image href="file:///neutral.png"/>',
                     '<path style="fill:url(https://invalid.example/texture)"/>',
                     '<style>@import "neutral.css";</style>'):
            self.reject(lambda: profile._svg(('<svg xmlns="http://www.w3.org/2000/svg">'+body+'</svg>').encode()))
        profile._svg(b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:rdf="urn:rdf"><metadata><rdf:license rdf:resource="https://example.invalid/license"/></metadata></svg>')

    def test_limits_and_error_messages_are_fixed_without_stringifying_unknown_values(self):
        for value in (True, float('nan'), 'private-untrusted'):
            self.reject(lambda: profile._num(value))
        self.assertEqual(profile.PftDrawingExportProfileError('private-untrusted').code, 'DRAWING_EXPORT_UNSUPPORTED')
        self.assertNotIn('private-untrusted', str(profile.PftDrawingExportProfileError('private-untrusted')))



    def test_material_changes_preserving_shape_color_are_stale(self):
        context=self.snapshot()
        self.source.ViewObject.ShapeAppearance[0].Shininess=.9
        self.reject(lambda:self.verify(context),'DRAWING_EXPORT_STALE')

    def test_native_exception_and_stop_still_remove_only_the_own_false_parameter(self):
        for stopped in (False,True):
            with self.subTest(stopped=stopped):
                self.setUp();context=self.snapshot();active=[True];paths=[]
                colors=self.groups['User parameter:BaseApp/Preferences/Mod/TechDraw/Colors']
                def hook(path):
                    paths.append(path)
                    colors.data[('Bool','LightOnDark')]=False
                    if stopped:active[0]=False
                    else:raise RuntimeError('private-untrusted-native-failure')
                self.export_hook=hook
                error=self.reject(lambda:self.capture(context,guard=lambda:active[0]))
                self.assertEqual(error.capture_state,'unknown')
                self.assertEqual(error.cleanup_state,'complete')
                self.assertNotIn(('Bool','LightOnDark'),colors.data)
                self.assertFalse(paths[0].parent.exists())

    def test_foreign_parameter_change_is_preserved_and_cleanup_is_unverified(self):
        context=self.snapshot()
        colors=self.groups['User parameter:BaseApp/Preferences/Mod/TechDraw/Colors']
        paths=[]
        def hook(path):
            paths.append(path)
            colors.data[('Bool','LightOnDark')]=True
            raise RuntimeError('private-untrusted-native-failure')
        self.export_hook=hook
        error=self.reject(lambda:self.capture(context))
        self.assertTrue(colors.data[('Bool','LightOnDark')])
        self.assertEqual(error.cleanup_state,'unknown')
        # The native PDF itself remains an exactly owned stage, even when
        # parameter cleanup is uncertain.
        if paths[0].exists():paths[0].unlink()
        if paths[0].parent.exists():paths[0].parent.rmdir()

    def test_open_failure_reports_no_pdf_but_unknown_native_page_protection(self):
        self.scene_value=None;context=self.snapshot()
        def show():raise RuntimeError('private-untrusted-open')
        self.page.ViewObject.show=show
        error=self.reject(lambda:profile.open_page(self.app,self.gui,context,f.controller,guard=self.guard))
        self.assertEqual(error.capture_state,'not_captured')
        self.assertEqual(error.cad_protection_state,'unverified')
        self.assertEqual(error.page_open_state,'unknown')
        self.assertEqual(self.export_calls,0)


class Point:
    def x(self): return 0.
    def y(self): return 0.


class Rect(Point):
    def width(self): return 20.
    def height(self): return 10.


class Transform:
    def __getattr__(self, name):
        if name.startswith('m') and len(name) == 3:
            return lambda: 1. if name[1] == name[2] else 0.
        raise AttributeError(name)


class PathValue:
    def fillRule(self): return 0
    def elementCount(self): return 2
    def elementAt(self, index): return NS(type=0 if index == 0 else 1, x=float(index), y=float(index))


class Item:
    __module__ = 'PySide6.QtWidgets'
    def __init__(self, kind, name=None, children=()): self.kind,self.name,self.children=kind,name,list(children)
    def type(self): return self.kind
    def parentItem(self): raise AssertionError('unsafe parentItem must never be called')
    def childItems(self): return self.children[:]
    def data(self, key): return ('QGIV' if key == 0 else self.name) if self.name else None
    def graphicsEffect(self): return None
    def isVisible(self): return True
    def pos(self): return Point()
    def transformOriginPoint(self): return Point()
    def transform(self): return Transform()
    def sceneTransform(self): return Transform()
    def rotation(self): return 0.
    def scale(self): return 1.
    def opacity(self): return 1.
    def zValue(self): return 0.
    def boundingRect(self): return Rect()
    def flags(self): return 0


class PathItem(Item):
    __module__ = 'PySide6.QtWidgets'
    def path(self): return PathValue()
    def shape(self): return PathValue()
    def pen(self): return NS()
    def brush(self): return NS()


class EmptyTextItem(Item):
    __module__ = 'PySide6.QtWidgets'
    def toPlainText(self): return ''
    def font(self): return NS()
    def document(self): raise AssertionError('empty native labels must not get a new document')
    def defaultTextColor(self): raise AssertionError('empty labels need no text color control')


class GraphicsView:
    def __init__(self, scene): self.owner = scene
    def scene(self): return self.owner


class GraphicsScene:
    def __init__(self, items): self.roots,self.selected=items,[]
    def views(self): return [GraphicsView(self)]
    def selectedItems(self): return self.selected
    def items(self):
        result=[]
        def walk(item):
            result.append(item)
            for child in item.childItems(): walk(child)
        for root in self.roots: walk(root)
        return result
    def sceneRect(self): return Rect()


class SceneSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.part=NS(Name='NeutralView',TypeId='TechDraw::DrawViewPart')
        self.dimension=NS(Name='NeutralDimension',TypeId='TechDraw::DrawViewDimension')
        self.page=NS(Views=[self.part,self.dimension])
        self.label=EmptyTextItem(65539)
        self.root=Item(65577,'NeutralView',[PathItem(65555),Item(65575,'NeutralDimension',[self.label])])
        self.scene=GraphicsScene([self.root,Item(65566)])
        locale=NS(name=lambda:'en',decimalPoint=lambda:'.',groupSeparator=lambda:',',numberOptions=lambda:0)
        widgets=NS(QGraphicsScene=GraphicsScene,QGraphicsView=GraphicsView,
                   QGraphicsTextItem=EmptyTextItem,QGraphicsPixmapItem=type('Other',(),{}),
                   QGraphicsColorizeEffect=type('Effect',(),{}))
        self.qt=patch.object(profile,'_qt',return_value=(NS(QLocale=lambda:locale),NS(),widgets))
        self.qt.start();self.addCleanup(self.qt.stop)
        for name in ('_font', '_pen', '_brush'):
            reader = patch.object(profile, name, return_value=('neutral native style',))
            reader.start(); self.addCleanup(reader.stop)
        self.native=patch.dict('sys.modules',{'TechDrawGui':NS(getSceneForPage=lambda page:self.scene)})
        self.native.start();self.addCleanup(self.native.stop)

    def test_forest_uses_only_borrowed_child_lists_and_skips_empty_document_getter(self):
        before=self.scene.items()
        scene,first=profile._scene(self.page,None)
        _,second=profile._scene(self.page,None)
        self.assertIs(scene,self.scene)
        self.assertEqual(first,second)
        self.assertEqual(self.scene.items(),before)

    def test_visible_native_edge_keeps_complete_outline_when_path_wrapper_disappears(self):
        edge = self.root.children[0]
        _, before = profile._scene(self.page, None)
        edge.path = None
        _, generic_wrapper = profile._scene(self.page, None)
        self.assertEqual(before, generic_wrapper)
        changed = PathValue()
        changed.elementAt = lambda index: NS(type=0 if index == 0 else 1, x=float(index), y=2. if index == 1 else 0.)
        edge.shape = lambda: changed
        _, different_geometry = profile._scene(self.page, None)
        self.assertNotEqual(before, different_geometry)

    def test_hidden_native_vertex_wrapper_cache_is_not_a_render_input_but_visibility_is(self):
        vertex = PathItem(65569)
        vertex.isVisible = lambda: False
        self.root.children.append(vertex)
        _, before = profile._scene(self.page, None)
        # Same native kind, geometry bounds and transforms; only the Python
        # base wrapper's optional cache getters differ after native redraw.
        vertex.path = None
        _, after = profile._scene(self.page, None)
        self.assertEqual(before, after)
        vertex.isVisible = lambda: True
        _, visible = profile._scene(self.page, None)
        self.assertNotEqual(before, visible)
        changed_shape = PathValue()
        changed_shape.elementAt = lambda index: NS(type=0 if index == 0 else 1, x=float(index), y=2. if index else 0.)
        vertex.shape = lambda: changed_shape
        _, visible_path = profile._scene(self.page, None)
        self.assertNotEqual(visible, visible_path)

    def test_recursive_snapshot_releases_own_reference_containers_without_cyclic_gc(self):
        gc.collect()
        enabled = gc.isenabled()
        gc.disable()
        try:
            profile._scene(self.page, None)
            count = sum(isinstance(value, types.FunctionType) and value.__name__ == 'item_snapshot'
                        for value in gc.get_objects())
            self.assertEqual(count, 0, 'visitor must not keep borrowed Qt wrappers in a self-cycle')
            self.label.font = None
            visited = False
            try:
                profile._scene(self.page, None)
            except profile.PftDrawingExportProfileError as failure:
                trace = failure.__traceback__
                while trace is not None:
                    frame = trace.tb_frame
                    if frame.f_code.co_name == 'item_snapshot':
                        visited = True
                        self.assertIsNone(frame.f_locals.get('item'))
                        self.assertIsNone(frame.f_locals.get('child'))
                        self.assertEqual(frame.f_locals['children_by_id'], {})
                        self.assertEqual(frame.f_locals['font_cache'][0], [])
                        self.assertIsNone(frame.f_locals['item_snapshot'])
                    trace = trace.tb_next
            self.assertTrue(visited)
        finally:
            if enabled: gc.enable()

    def test_native_group_never_dispatches_spurious_nonvirtual_wrapper_getters(self):
        def forbidden(): raise AssertionError('not a compatible native C++ base getter')
        for name in ('path','pen','brush','rect','font','text','toPlainText','polygon','line','pixmap','document'):
            setattr(self.root, name, forbidden)
        profile._scene(self.page, None)
        # A QLabel-derived native ID still requires text and font; an
        # incomplete generic wrapper is rejected, not silently under-frozen.
        self.label.font = None
        with self.assertRaises(profile.PftDrawingExportProfileError):
            profile._scene(self.page, None)

    def test_unknown_native_class_and_missing_required_primpath_style_are_rejected(self):
        self.root.children.append(Item(65537))
        with self.assertRaises(profile.PftDrawingExportProfileError):
            profile._scene(self.page, None)
        self.root.children.pop()
        self.root.children.append(PathItem(65545))
        profile._scene(self.page, None)
        self.root.children[-1].pen = None
        with self.assertRaises(profile.PftDrawingExportProfileError):
            profile._scene(self.page, None)

    def test_actual_scene_selection_unknown_root_or_missing_dimension_is_rejected(self):
        self.scene.selected=[self.root]
        with self.assertRaises(profile.PftDrawingExportProfileError):profile._scene(self.page,None)
        self.scene.selected=[]
        self.scene.roots.append(Item(65566))
        with self.assertRaises(profile.PftDrawingExportProfileError):profile._scene(self.page,None)
        self.scene.roots.pop()
        self.root.children.pop()
        with self.assertRaises(profile.PftDrawingExportProfileError):profile._scene(self.page,None)


class OpenedVisibilityContractTests(unittest.TestCase):
    def data(self, target=False, other=False):
        def row(identity, visible):
            props = (('Label', 'App::PropertyString', identity[1]), ('Visibility', 'App::PropertyBool', visible))
            return identity, props, identity[0] + 100, 'Gui::NativePageViewProvider', (('Visibility', 'App::PropertyBool', visible),)
        protected = [() for _ in range(15)]
        protected[0] = ('unchanged source',)
        protected[6] = (row((11, 'Page', 'TechDraw::DrawPage'), target),
                        row((12, 'OtherPage', 'TechDraw::DrawPage'), other))
        return {'protected': tuple(protected)}

    def replace(self, data, *, row_index=0, part=1, value=None):
        protected = list(data['protected']); rows = list(protected[6]); row = list(rows[row_index])
        row[part] = value; rows[row_index] = tuple(row); protected[6] = tuple(rows)
        return {'protected': tuple(protected)}

    def test_exact_pair_only_during_closed_show_and_no_snapshot_mutation(self):
        before, after = self.data(False), self.data(True)
        frozen = before['protected']
        self.assertTrue(profile._opened_app_matches(before, after, 11, False))
        self.assertEqual(before['protected'], frozen)
        self.assertFalse(profile._opened_app_matches(before, after, 11, True))
        self.assertFalse(profile._opened_app_matches(self.data(True), self.data(False), 11, False))
        self.assertTrue(profile._opened_app_matches(self.data(True), self.data(True), 11, True))

    def test_other_page_source_extra_property_name_type_or_provider_identity_is_exact(self):
        before, after = self.data(False), self.data(True)
        source_changed = list(after['protected']); source_changed[0] = ('changed source',)
        variants = (
            self.data(True, True), {'protected': tuple(source_changed)},
            self.replace(after, part=0, value=(11, 'RenamedPage', 'TechDraw::DrawPage')),
            self.replace(after, part=0, value=(11, 'Page', 'TechDraw::DrawPagePython')),
            self.replace(after, part=2, value=999),
            self.replace(after, part=3, value='Gui::DifferentPageViewProvider'),
            self.replace(after, part=1, value=after['protected'][6][0][1] + (('Extra', 'App::PropertyBool', True),)),
        )
        for value in variants:
            with self.subTest(value=value):
                try: matches = profile._opened_app_matches(before, value, 11, False)
                except profile.PftDrawingExportProfileError: matches = False
                self.assertFalse(matches)

    def test_missing_duplicate_nonbool_wrong_kind_and_mixed_pairs_are_rejected(self):
        before, after = self.data(False), self.data(True)
        visibility = ('Visibility', 'App::PropertyBool', True)
        variants = (
            self.replace(after, part=4, value=()),
            self.replace(after, part=4, value=(visibility, visibility)),
            self.replace(after, part=4, value=(visibility, ('Visibility', 'App::PropertyBool'))),
            self.replace(after, part=4, value=(('Visibility', 'App::PropertyBool', 1),)),
            self.replace(after, part=4, value=(('Visibility', 'App::PropertyInteger', 1),)),
            self.replace(after, part=4, value=(('Visibility', 'App::PropertyBool', False),)),
        )
        for value in variants:
            with self.subTest(value=value):
                with self.assertRaises(profile.PftDrawingExportProfileError):
                    profile._opened_app_matches(before, value, 11, False)


class WholePaperPaintTests(unittest.TestCase):
    def setUp(self):
        owner = self
        self.events, self.images, self.painters = [], [], []
        self.active = True
        self.begin_result, self.end_result = True, True
        self.begin_hook = self.render_hook = self.end_hook = None
        self.null_image = False
        class Rectangle:
            def __init__(self, *values): self.values = values
            def x(self): return self.values[0]
            def y(self): return self.values[1]
            def width(self): return self.values[2]
            def height(self): return self.values[3]
        class Image:
            Format_RGBA8888_Premultiplied = 5
            def __init__(self, width, height, format):
                self.w, self.h, self.format, self.ratio = width, height, format, 2.
                owner.images.append(weakref.ref(self))
                owner.events.append(('image', width, height, format))
            def isNull(self): return owner.null_image
            def setDevicePixelRatio(self, ratio): self.ratio = ratio
            def devicePixelRatio(self): return self.ratio
            def fill(self, color): owner.events.append(('fill', color))
            def width(self): return self.w
            def height(self): return self.h
            def bytesPerLine(self): return self.w * 4
            def sizeInBytes(self): return self.w * self.h * 4
        class Painter:
            def __init__(self):
                self.image = None
                owner.painters.append(weakref.ref(self))
            def begin(self, image):
                owner.events.append('begin'); self.image = image
                if owner.begin_hook: owner.begin_hook()
                return owner.begin_result
            def isActive(self): return self.image is not None
            def end(self):
                owner.events.append('end'); self.image = None
                if owner.end_hook: owner.end_hook()
                return owner.end_result
        class Scene:
            def __init__(self): self.rect = (-2970., -4200., 8910., 6300.)
            def sceneRect(self): return Rectangle(*self.rect)
            def render(self, painter, target, source, aspect):
                owner.events.append(('render', target.values, source.values, aspect))
                if owner.render_hook: owner.render_hook()
                return None  # The real Qt API is void, not a success boolean.
        self.Scene, self.Image, self.Painter = Scene, Image, Painter
        self.scene = Scene()
        core = NS(QRectF=Rectangle, Qt=NS(white='opaque-white', KeepAspectRatio='keep-aspect'))
        qt = patch.object(profile, '_qt', return_value=(core, NS(QImage=Image, QPainter=Painter), NS(QGraphicsScene=Scene)))
        qt.start(); self.addCleanup(qt.stop)

    def guard(self): return self.active
    def render(self): return profile._render_paper(self.scene, (297., 210.), guard=self.guard)
    def reject(self, callback=None):
        with self.assertRaises(profile.PftDrawingExportProfileError) as error:
            (callback or self.render)()
        self.assertNotIn('private-untrusted', str(error.exception))
        return error.exception

    def test_whole_paper_render_has_fixed_memory_not_viewport_or_navigation_extent(self):
        self.assertIsNone(self.render())
        self.assertEqual(self.events, [('image', 1000, 1000, 5), ('fill', 'opaque-white'), 'begin',
            ('render', (0., 0., 1000., 1000.), (0., -2100., 2970., 2100.), 'keep-aspect'), 'end'])
        self.assertTrue(all(ref() is None for ref in self.images + self.painters))

    def test_paper_geometry_uses_actual_common_factor_without_default_ten_assumption(self):
        for factor in (2., 10., 25.):
            with self.subTest(factor=factor):
                width, height = 297. * factor, 210. * factor
                self.scene.rect = (-width, -2. * height, 3. * width, 3. * height)
                self.render()
                rendered = next(row for row in reversed(self.events) if type(row) is tuple and row[0] == 'render')
                self.assertEqual(rendered[2], (0., -height, width, height))

    def test_invalid_extents_and_scale_overflow_reject_before_image_allocation(self):
        for rect in ((0., 0., 2970., 2100.), (-2970., -4200., 8910., 6000.),
                     (-2970., -4200., -8910., 6300.), (float('nan'), -4200., 8910., 6300.),
                     (-1e-320, -2e-320, 3e-320, 3e-320),
                     (-1e308, -1e308, 1e308, 1e308)):
            with self.subTest(rect=rect):
                self.scene.rect = rect; self.reject()
                self.assertEqual(self.images, [])
        self.reject(lambda: profile._paper_rect((-1., -2., 3., 3.), (True, 1.)))

    def test_guard_denial_or_null_image_never_begins_or_renders(self):
        self.active = False; self.reject(); self.assertEqual(self.events, [])
        self.active = True; self.null_image = True; self.reject()
        self.assertNotIn('begin', self.events)

    def test_begin_callback_cancellation_is_rechecked_before_scene_render(self):
        self.begin_hook = lambda: setattr(self, 'active', False)
        error = self.reject()
        self.assertEqual(error.code, 'DRAWING_EXPORT_STALE')
        self.assertEqual(self.events.count('end'), 1)
        self.assertFalse(any(type(row) is tuple and row[0] == 'render' for row in self.events))

    def test_begin_failure_is_unknown_without_retry_or_end(self):
        self.begin_result = False
        error = self.reject()
        self.assertEqual(error.cleanup_state, 'unknown')
        self.assertEqual(error.capture_state, 'not_captured')
        self.assertNotIn('end', self.events)

    def test_fixed_render_error_is_preserved_when_end_also_fails(self):
        for raised_end in (False, True):
            with self.subTest(raised_end=raised_end):
                failure = profile.PftDrawingExportProfileError('DRAWING_EXPORT_STALE')
                def render_error(): raise failure
                def end_error(): raise RuntimeError('private-untrusted-end')
                self.events.clear(); self.render_hook = render_error
                self.end_result = False; self.end_hook = end_error if raised_end else None
                error = self.reject()
                self.assertIs(error, failure)
                self.assertEqual(error.cleanup_state, 'unknown')
                self.assertEqual(error.cad_protection_state, 'unverified')
                self.assertEqual(self.events.count('end'), 1)

    def test_end_callback_stop_is_not_missed_after_native_cleanup(self):
        self.end_hook = lambda: setattr(self, 'active', False)
        error = self.reject()
        self.assertEqual(error.code, 'DRAWING_EXPORT_STALE')
        self.assertEqual(self.events.count('end'), 1)

    def test_owned_paint_objects_release_with_gc_disabled_and_retained_failure_trace(self):
        enabled = gc.isenabled(); gc.disable()
        try:
            self.render()
            self.assertTrue(all(ref() is None for ref in self.images + self.painters))
            def failure(): raise RuntimeError('private-untrusted-render')
            self.render_hook = failure
            error = None
            try:
                self.render()
            except profile.PftDrawingExportProfileError as failure:
                error = failure
            self.assertIsNotNone(error)
            self.assertTrue(all(ref() is None for ref in self.images + self.painters))
            trace = error.__traceback__; inspected = False
            while trace is not None:
                if trace.tb_frame.f_code.co_name == '_render_paper':
                    inspected = True
                    for name in ('image', 'painter', 'source', 'target', 'scene', 'paper_mm'):
                        self.assertIsNone(trace.tb_frame.f_locals[name])
                trace = trace.tb_next
            self.assertTrue(inspected)
        finally:
            if enabled: gc.enable()


class FontResourceTests(unittest.TestCase):
    class Raw:
        def __init__(self, data): self.data, self.reads = data, 0
        def isValid(self): return True
        def familyName(self): return 'Neutral'
        def styleName(self): return 'Regular'
        def pixelSize(self): return 12.
        def unitsPerEm(self): return 1000.
        def style(self): return 0
        def weight(self): return 400
        def fontTable(self, tag):
            self.reads += 1
            return self.data if tag == 'cmap' else b''

    def test_glyph_fallback_uses_native_utf16_range_and_queues_own_clone_cleanup(self):
        raw = self.Raw(b'neutral fallback font')
        calls, deleted = [], []
        invalid = NS(isValid=lambda: False)
        def glyphs(*args):
            calls.append(args)
            return [NS(rawFont=lambda: raw)]
        # A + non-BMP character is three UTF-16 units plus the paragraph
        # separator. Native block.length is authoritative, not Python len.
        block = NS(isValid=lambda: True, length=lambda: 4,
                   layout=lambda: NS(glyphRuns=glyphs), next=lambda: invalid)
        detached = NS(documentLayout=lambda: NS(documentSize=lambda: None),
                      begin=lambda: block, deleteLater=lambda: deleted.append(True))
        document = NS(clone=lambda: detached)
        fonts = profile._text_fonts(document, NS(QTextDocument=NS), [[], set(), 0])
        self.assertEqual(calls, [(0, 3)])
        self.assertEqual(len(fonts), 1)
        self.assertEqual(deleted, [True])

    def test_empty_block_skips_layout_and_exception_still_queues_only_own_clone(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                deleted = []
                invalid = NS(isValid=lambda: False)
                def layout(): raise RuntimeError('private-untrusted-layout')
                block = NS(isValid=lambda: True, length=lambda: 2 if fail else 1,
                           layout=layout, next=lambda: invalid)
                detached = NS(documentLayout=lambda: NS(documentSize=lambda: None),
                              begin=lambda: block, deleteLater=lambda: deleted.append(True))
                source = NS(clone=lambda: detached)
                if fail:
                    with self.assertRaises(RuntimeError):
                        profile._text_fonts(source, NS(QTextDocument=NS), [[], set(), 0])
                else:
                    self.assertEqual(profile._text_fonts(source, NS(QTextDocument=NS), [[], set(), 0]), ())
                self.assertEqual(deleted, [True])

    def test_wrong_native_clone_wrapper_is_rejected_without_casting_or_deleting_it(self):
        class GraphicsRectItem:
            def __getattr__(self, name): raise AssertionError('unknown native wrapper must not be accessed')
        wrong = GraphicsRectItem()
        source = NS(clone=lambda: wrong)
        with self.assertRaises(profile.PftDrawingExportProfileError) as failure:
            profile._text_fonts(source, NS(QTextDocument=NS), [[], set(), 0])
        self.assertEqual(failure.exception.code, 'DRAWING_EXPORT_UNSUPPORTED')
        self.assertEqual(failure.exception.cleanup_state, 'unknown')
        self.assertNotIn('GraphicsRectItem', str(failure.exception))

    def test_equal_metadata_does_not_alias_different_native_font_data(self):
        cache = [[], set(), 0]
        one, two = self.Raw(b'first font data'), self.Raw(b'different font data')
        first = profile._font_tables(one, cache)
        second = profile._font_tables(two, cache)
        self.assertEqual(first[0], second[0])
        self.assertNotEqual(first[1], second[1])
        self.assertGreater(two.reads, 0)
        previous_reads = one.reads
        self.assertEqual(profile._font_tables(one, cache), first)
        self.assertEqual(one.reads, previous_reads)

    def test_content_budget_counts_exact_table_bytes_once_but_rejects_overflow(self):
        cache = [[], set(), 0]
        with patch.object(profile, 'MAX_FONT_BYTES', 10), patch.object(profile, 'MAX_FONT_TABLE_BYTES', 8):
            profile._font_tables(self.Raw(b'12345678'), cache)
            profile._font_tables(self.Raw(b'12345678'), cache)
            self.assertEqual(cache[2], 8)
            with self.assertRaises(profile.PftDrawingExportProfileError):
                profile._font_tables(self.Raw(b'abc'), cache)
        with patch.object(profile, 'MAX_FONT_TABLE_BYTES', 2):
            with self.assertRaises(profile.PftDrawingExportProfileError):
                profile._font_tables(self.Raw(b'abc'), [[], set(), 0])


if __name__ == '__main__':
    unittest.main()
