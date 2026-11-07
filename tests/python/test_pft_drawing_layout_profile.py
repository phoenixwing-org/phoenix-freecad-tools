# SPDX-License-Identifier: Apache-2.0
"""Independent finite layout-input fixtures; no CAD or Qt runtime is required."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / 'addon/PhoenixFreeCADTools'


def load(name):
    spec = importlib.util.spec_from_file_location('neutral_' + name, ADDON / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Parameters:
    def __init__(self, values=None): self.values = values or {}
    def __getattr__(self, method):
        if method.startswith('Get'):
            return lambda key, default: self.values.get(key, default)
        raise AssertionError('No preference writes are permitted')


def cache(bounds=(-30., -20., 0., 30., 20., 0.), digest='neutral-brep'):
    state = ('Forward', bounds, (100, digest))
    return ((state,), (), ((0, state),))


def fields(kind, transform=None, bounds=(0., 0., 10., 20.), binding=(None, None), **extra):
    return {'type': kind, 'binding': binding, 'visible': True, 'bounds': bounds,
            'scene_transform': transform or (1., 0., 0., 0., 1., 0., 0., 0., 1.), **extra}


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = load('pft_drawing_layout_profile')
        self.pdf = self.profile._sibling('pft_drawing_export_profile')

    def test_paper_rectangle_uses_affine_native_scene_not_app_placeholder(self):
        value = fields(65551, (0., -1., 0., 1., 0., 0., 300., -500., 1.), (0., 0., 20., 10.))
        self.assertEqual(self.profile._paper_rect(value), [30., 50., 31., 52.])
        self.assertNotIn('getRect', Path(self.profile.__file__).read_text())

    def test_paper_transform_rejects_perspective(self):
        value = fields(65551, (1., 0., .1, 0., 1., 0., 0., 0., 1.))
        with self.assertRaises(self.profile.PftDrawingLayoutProfileError):
            self.profile._paper_rect(value)

    def test_indexed_geometry_is_already_y_up_and_scale_is_applied_once(self):
        view = types.SimpleNamespace(Name='Top', X=types.SimpleNamespace(Value=100.),
            Y=types.SimpleNamespace(Value=150.), Scale=.5, ScaleType='Custom', Rotation=90.)
        dto, scale = self.profile._view_geometry(view, types.SimpleNamespace(Scale=.5), cache((2., 3., 0., 8., 7., 0.)))
        self.assertEqual(dto, {'name': 'Top', 'origin_mm': [100., 150.], 'bounds_mm': [101., 151.5, 104., 153.5]})
        self.assertEqual(scale, .5)

    def test_four_scales_preserve_origin_and_physical_geometry(self):
        for scale in (1., .5, .1, .05):
            with self.subTest(scale=scale):
                view = types.SimpleNamespace(Name='Top', X=types.SimpleNamespace(Value=85.),
                    Y=types.SimpleNamespace(Value=100.), Scale=scale, ScaleType='Custom')
                dto, actual = self.profile._view_geometry(view, None, cache())
                self.assertEqual(dto['bounds_mm'], [85.-30.*scale, 100.-20.*scale, 85.+30.*scale, 100.+20.*scale])
                self.assertEqual(actual, scale)

    def test_page_scale_must_match_native_effective_property(self):
        view = types.SimpleNamespace(Name='Top', X=types.SimpleNamespace(Value=0.),
            Y=types.SimpleNamespace(Value=0.), Scale=.5, ScaleType='Page')
        with self.assertRaises(self.profile.PftDrawingLayoutProfileError):
            self.profile._view_geometry(view, types.SimpleNamespace(Scale=1.), cache())

    def test_known_frame_geometry_allows_text_only_not_path_or_attribute_changes(self):
        a = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0L1 1"/><text x="2">Neutral</text></svg>'
        b = a.replace(b'Neutral', b'Independent text')
        self.assertEqual(self.profile._svg_geometry(a), self.profile._svg_geometry(b))
        self.assertNotEqual(self.profile._svg_geometry(a), self.profile._svg_geometry(a.replace(b'L1 1', b'L2 2')))
        self.assertNotEqual(self.profile._svg_geometry(a), self.profile._svg_geometry(a.replace(b'x="2"', b'x="3"')))

    def test_known_frame_does_not_accept_external_resources(self):
        with self.assertRaises(Exception):
            self.profile._svg_geometry(b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.invalid/image"/></svg>')

    def test_default_style_reads_actual_line_group_csv_and_freezes_absence(self):
        with tempfile.TemporaryDirectory(prefix='pft-layout-defaults-') as directory:
            root = Path(directory)
            folder = root / 'Mod/TechDraw/LineGroup'; folder.mkdir(parents=True)
            (folder / 'LineGroup.csv').write_text('; Neutral finite records\n*one,.13,.18,.25,.5\n*two,.18,.25,.35,.7\n*three,.25,.35,.5,1\n*four,.35,.5,.7,1.4\n')
            app = types.SimpleNamespace(getResourceDir=lambda: str(root), ParamGet=lambda path: Parameters())
            locale = types.SimpleNamespace(decimalPoint=lambda: '.', name=lambda: 'C', numberOptions=lambda: 0)
            with patch.object(self.pdf, '_qt', return_value=(types.SimpleNamespace(QLocale=lambda: locale), None, None)), \
                 patch.object(self.profile, '_sibling', return_value=self.pdf):
                values, signature = self.profile._defaults(app)
            self.assertEqual(values['FontSize'], 5.)
            self.assertEqual(values['ArrowSize'], 3.5)
            self.assertEqual(values['LineWidth'], .35)
            self.assertTrue(all(row[3] is False for row in signature[0]))
            self.assertEqual(signature[1], hashlib.sha256((folder / 'LineGroup.csv').read_bytes()).hexdigest())

    def test_unknown_units_and_nondefault_arrow_profile_are_explicitly_rejected(self):
        for supplied in ({'UserSchema': 1}, {'ArrowStyle': 1}, {'Resolution': 12.}, {'GapISO': 1.}):
            with self.subTest(supplied=supplied):
                app = types.SimpleNamespace(getResourceDir=lambda: '/neutral', ParamGet=lambda path: Parameters(supplied))
                with self.assertRaises(self.profile.PftDrawingLayoutProfileError) as error:
                    self.profile._defaults(app)
                self.assertEqual(error.exception.code, 'DRAWING_LAYOUT_STYLE_UNSUPPORTED')


class GuiSubtreeTests(unittest.TestCase):
    def setUp(self):
        self.p = load('pft_drawing_layout_profile')
        self.pdf = self.p._sibling('pft_drawing_export_profile')
        self.doc = types.SimpleNamespace()
        self.view = types.SimpleNamespace(Name='Top', Document=self.doc)
        self.dim = types.SimpleNamespace(Name='OldDimension', TypeId='TechDraw::DrawViewDimension',
            Document=self.doc, References2D=[(self.view, ('Edge0',))], References3D=[])
        self.page = types.SimpleNamespace(Document=self.doc, Views=[self.view, self.dim])
        self.objects = {'Top': self.view, 'OldDimension': self.dim}
        self.doc.getObject = self.objects.get
        class Item:
            __module__ = 'PySide6.QtWidgets'
            def __init__(self, kind, name=None, children=()):
                self.kind, self.name, self.children, self.marker = kind, name, list(children), 'original'
            def type(self): return self.kind
            def data(self, role): return ('QGIV' if self.name else None) if role == 0 else self.name
            def childItems(self): return list(self.children)
        self.Item = Item
        self.old_label = Item(65551)
        self.old_root = Item(65575, 'OldDimension', [self.old_label])
        self.view_root = Item(65577, 'Top', [self.old_root])
        self.template = Item(65566)
        self.roots = [self.template, self.view_root]
        class Scene:
            def views(inner): return [object()]
            def selectedItems(inner): return []
            def items(inner):
                result = []
                def visit(item):
                    result.append(item)
                    for child in item.children: visit(child)
                for root in self.roots: visit(root)
                return result
        self.scene = Scene()
        patches = [patch.object(self.pdf, '_qt', return_value=(None, None, types.SimpleNamespace(QGraphicsScene=Scene))),
            patch.dict('sys.modules', {'TechDrawGui': types.SimpleNamespace(getSceneForPage=lambda page: self.scene)}),
            patch.object(self.p, '_fields', side_effect=lambda item, *args: {'type': item.kind, 'marker': item.marker}),
            patch.object(self.p._sibling('pft_drawing_dimensions'), '_node', return_value=None)]
        for patcher in patches:
            patcher.start(); self.addCleanup(patcher.stop)

    def read(self):
        return self.p._gui(self.page, None, (self.view,), (self.dim,))[1]

    def test_template_semantics_are_part_of_old_gui_snapshot(self):
        before = self.read()
        self.template.marker = 'changed-native-transform'
        self.assertNotEqual(before, self.read())
        self.assertEqual(before[0][0], '_template')

    def test_unknown_root_and_unregistered_dimension_are_rejected(self):
        for item in (self.Item(3), self.Item(65575, 'Unknown')):
            with self.subTest(kind=item.kind):
                self.roots.append(item)
                with self.assertRaises(self.p.PftDrawingLayoutProfileError): self.read()
                self.roots.pop()
        self.view_root.children.append(self.Item(65575, 'Unknown'))
        with self.assertRaises(self.p.PftDrawingLayoutProfileError): self.read()

    def test_only_registered_same_page_dimension_in_own_view_can_be_excluded(self):
        before = self.read()
        native = types.SimpleNamespace(Name='NewDimension', TypeId='TechDraw::DrawViewDimension',
            Document=self.doc, References2D=[(self.view, ('Edge0',))], References3D=[])
        self.objects[native.Name] = native
        root = self.Item(65575, native.Name, [self.Item(65551), self.Item(65553)])
        self.view_root.children.append(root)
        with self.assertRaises(self.p.PftDrawingLayoutProfileError): self.read()
        self.page.Views.append(native)
        self.assertEqual(before, self.read())
        native.References2D = [(object(), ('Edge0',))]
        with self.assertRaises(self.p.PftDrawingLayoutProfileError): self.read()

    def test_old_label_change_is_retained_even_when_new_native_subtree_is_ignored(self):
        before = self.read()
        self.old_label.marker = 'changed-old-label'
        self.assertNotEqual(before, self.read())


class TemplateFontTests(unittest.TestCase):
    def test_both_svg_resources_and_editable_text_font_bytes_are_frozen(self):
        p = load('pft_drawing_layout_profile'); pdf = p._sibling('pft_drawing_export_profile')
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><text x="2">Neutral</text></svg>'
        with tempfile.TemporaryDirectory(prefix='pft-layout-frame-') as directory:
            root = Path(directory); installed = root / p._A4_RELATIVE
            installed.parent.mkdir(parents=True); installed.write_bytes(svg)
            embedded = root / 'embedded.svg'; embedded.write_bytes(svg)
            template = types.SimpleNamespace(Template=str(installed), PageResult=str(embedded), Orientation='Landscape',
                Width=types.SimpleNamespace(Value=297.), Height=types.SimpleNamespace(Value=210.), EditableTexts={'Title': 'Neutral'})
            app = types.SimpleNamespace(getResourceDir=lambda: str(root)); page = types.SimpleNamespace(Template=template)
            tables = ['first-font-binary']
            with patch.object(p, '_A4_SHA256', hashlib.sha256(svg).hexdigest()), \
                 patch.object(pdf, '_qt', return_value=(None, object(), None)), \
                 patch.object(pdf, '_svg_fonts', side_effect=lambda data, texts, gui, cache: tuple(tables)) as fonts:
                before = p._template(app, page)
                self.assertEqual(fonts.call_count, 2)
                self.assertEqual([call.args[0] for call in fonts.call_args_list], [svg, svg])
                tables[0] = 'changed-font-binary-with-same-family-name'
                self.assertNotEqual(before, p._template(app, page))
                tables[0] = 'first-font-binary'
                template.EditableTexts['Title'] = 'Changed text with same font'
                self.assertNotEqual(before, p._template(app, page))

    def test_unknown_svg_font_cleanup_is_not_marked_complete(self):
        p = load('pft_drawing_layout_profile'); pdf = p._sibling('pft_drawing_export_profile')
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><text>Neutral</text></svg>'
        class UnknownCleanup(RuntimeError): cleanup_state = 'unknown'
        template = types.SimpleNamespace(Template='neutral-original', PageResult='neutral-embedded', Orientation='Landscape',
            Width=types.SimpleNamespace(Value=297.), Height=types.SimpleNamespace(Value=210.), EditableTexts={})
        with patch.object(p, '_A4_SHA256', hashlib.sha256(svg).hexdigest()), patch.object(pdf, '_resource', return_value=svg), \
             patch.object(pdf, '_qt', return_value=(None, object(), None)), patch.object(pdf, '_svg_fonts', side_effect=UnknownCleanup()):
            with self.assertRaises(UnknownCleanup):
                p._template(types.SimpleNamespace(getResourceDir=lambda: '/neutral'), types.SimpleNamespace(Template=template))


class TextResourceTests(unittest.TestCase):
    def test_changed_html_resource_is_rejected_before_glyph_layout(self):
        p = load('pft_drawing_layout_profile'); pdf = p._sibling('pft_drawing_export_profile')
        class Document:
            def toHtml(self): return self.html
        document = Document()
        point = types.SimpleNamespace(x=lambda: 0., y=lambda: 0.)
        item = types.SimpleNamespace(type=lambda: 65542, graphicsEffect=lambda: None, data=lambda index: None,
            isVisible=lambda: True, transform=lambda: None, sceneTransform=lambda: None, rotation=lambda: 0.,
            pos=lambda: point, transformOriginPoint=lambda: point, scale=lambda: 1., opacity=lambda: 1.,
            zValue=lambda: 0., flags=lambda: 0, boundingRect=lambda: None,
            toPlainText=lambda: 'Neutral label', font=lambda: None, document=lambda: document,
            defaultTextColor=lambda: None)
        with patch.object(pdf, '_qt', return_value=(None, types.SimpleNamespace(QTextDocument=Document), None)), \
             patch.object(pdf, '_transform', return_value=(1.,)*9), patch.object(pdf, '_rect', return_value=(0.,0.,1.,1.)), \
             patch.object(pdf, '_font', return_value=('font',)), patch.object(pdf, '_color', return_value='color'), \
             patch.object(pdf, '_text_fonts', side_effect=AssertionError('must not layout unknown resources')) as glyphs:
            for html in ('<img src="https://example.invalid/x">', '<object></object>', '<span style="background:url(x)">x</span>', '<span SRC = "x">x</span>'):
                with self.subTest(html=html):
                    document.html = html
                    with self.assertRaises(p.PftDrawingLayoutProfileError): p._fields(item, pdf, [0], [[],set(),0])
            glyphs.assert_not_called()


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.p = load('pft_drawing_layout_profile')
        self.pdf = self.p._sibling('pft_drawing_export_profile')
        self.ref = self.p._sibling('pft_drawing_references')
        self.doc = types.SimpleNamespace(Name='Example')
        self.view = types.SimpleNamespace(Name='Top', Document=self.doc, Scale=1., ScaleType='Custom',
            X=types.SimpleNamespace(Value=85.), Y=types.SimpleNamespace(Value=110.))
        self.other = types.SimpleNamespace(Name='Front', Document=self.doc, Scale=.5, ScaleType='Custom',
            X=types.SimpleNamespace(Value=210.), Y=types.SimpleNamespace(Value=160.))
        self.old = types.SimpleNamespace(Name='OldDimension', Document=self.doc)
        self.page = types.SimpleNamespace(Name='Page', Document=self.doc, Scale=1.)
        self.objects = {'Top': self.view, 'Front': self.other, 'OldDimension': self.old, 'Page': self.page}
        self.doc.getObject = self.objects.get
        self.app = types.SimpleNamespace(getDocument=lambda name: self.doc if name == 'Example' else None,
            Units=types.SimpleNamespace(Quantity=lambda value, unit: types.SimpleNamespace(getUserPreferred=lambda: ('native text', 1., 'mm'))))
        self.controller = types.SimpleNamespace(_ensure_main_thread=lambda: None)
        self.intent = {'page': 'Page', 'view': 'Top', 'dimensions': [
            {'edge': 'Edge0', 'kind': 'DistanceX', 'placement': 'auto'},
            {'edge': 'Edge1', 'kind': 'DistanceY', 'x_mm': -40., 'y_mm': 0.},
            {'edge': 'Edge0', 'kind': 'Distance', 'placement': 'auto'},
            {'edge': 'Edge2', 'kind': 'Radius', 'placement': 'auto'},
            {'edge': 'Edge2', 'kind': 'Diameter', 'placement': 'auto'}]}
        self.data = {'views': (self.view, self.other), 'dimensions': (self.old,)}
        self.scene = object()
        self.caches = {'Top': cache(), 'Front': cache()}
        self.states = (
            ('Top', (fields(65577, (1., 0., 0., 0., 1., 0., 850., -1100., 1.), binding=('QGIV', 'Top')), ())),
            ('Front', (fields(65577, (1., 0., 0., 0., 1., 0., 2100., -1600., 1.), binding=('QGIV', 'Front')), ())),
            ('OldDimension', (fields(65575), ((fields(65551, bounds=(10., -1200., 100., 50.)), ()),
                                            (fields(65553, bounds=(0., -1200., 200., 1.)), ()))))
        )
        self.style = {'LineWidth': .35, 'FontSize': 5., 'ArrowSize': 3.5, 'DecimalPoint': '.', 'ShowUnits': False}
        self.context_pdf = {'data': self.data}
        patches = [patch.object(self.p, '_template', return_value=('approved-template', 'embedded')),
            patch.object(self.p, '_defaults', return_value=(self.style, ('preferences',))),
            patch.object(self.p, '_gui', side_effect=lambda *args: (self.scene, self.states)),
            patch.object(self.p, '_measure', side_effect=lambda text, style: ([10., 6.], ('native-font', text))),
            patch.object(self.pdf, '_projected_cache', side_effect=lambda view: self.caches[view.Name]),
            patch.object(self.pdf, 'snapshot', return_value=self.context_pdf),
            patch.object(self.pdf, 'verify', return_value=self.context_pdf),
            patch.object(self.ref, 'read_edge', side_effect=self.edge)]
        for patcher in patches:
            patcher.start(); self.addCleanup(patcher.stop)

    def edge(self, view, name):
        if name == 'Edge2':
            return {'edge': name, 'status': 'available', 'curve': 'circle', 'start': None, 'end': None,
                'length_mm': 2*math.pi*5., 'radius_mm': 5., 'center': [0., 0., 0.],
                'closed': True, 'supported_kinds': ['Radius', 'Diameter']}
        start, end = ([-30., 0., 0.], [30., 0., 0.]) if name == 'Edge0' else ([0., -20., 0.], [0., 20., 0.])
        return {'edge': name, 'status': 'available', 'curve': 'line', 'start': start, 'end': end,
                'length_mm': 60. if name == 'Edge0' else 40., 'radius_mm': None, 'center': None,
                'closed': False, 'supported_kinds': ['Distance', 'DistanceX', 'DistanceY']}

    def snapshot(self):
        return self.p.snapshot(self.app, object(), self.doc, self.page, self.view, self.intent, self.controller)

    def test_full_five_kind_mixed_intent_and_fixed_old_obstacles(self):
        value = self.snapshot()['input']
        self.assertEqual([row['kind'] for row in value['dimensions']], ['DistanceX', 'DistanceY', 'Distance', 'Radius', 'Diameter'])
        self.assertEqual(value['dimensions'][1]['x_mm'], -40.)
        self.assertEqual(value['dimensions'][1]['placement'], 'explicit')
        self.assertEqual(value['dimensions'][3]['text'], 'R5.00')
        self.assertEqual(value['dimensions'][4]['text'], '⌀10.00')
        self.assertEqual(value['paper']['reserved_rects'], [[0., 0., 297., 80.]])
        self.assertEqual(len(value['obstacles']['rects']), 3)
        self.assertEqual(value['dimensions'][0]['geometry']['start_mm'], [55., 110.])

    def test_full_other_view_cache_change_is_stale_even_selected_edge_unchanged(self):
        context = self.snapshot()
        self.caches['Front'] = cache(digest='changed-unselected-native-edge')
        with self.assertRaises(self.p.PftDrawingLayoutProfileError) as error:
            self.p.verify_defaults(self.app, None, context, self.controller)
        self.assertEqual(error.exception.code, 'DRAWING_LAYOUT_PLAN_STALE')

    def test_old_gui_obstacle_change_cannot_be_ignored_during_write(self):
        context = self.snapshot()
        changed = deepcopy(self.states)
        changed[-1][1][1][0][0]['bounds'] = (10., -1200., 101., 50.)
        self.states = changed
        with self.assertRaises(self.p.PftDrawingLayoutProfileError):
            self.p.verify_defaults(self.app, None, context, self.controller)

    def test_visible_stroke_expands_target_and_other_view_collision_bounds(self):
        changed = deepcopy(self.states)
        changed = list(changed)
        for index, bounds in ((0, (500., -1320., 700., 440.)), (1, (1900., -1720., 400., 240.))):
            name, (root, children) = changed[index]
            changed[index] = (name, (root, children + ((fields(65555, bounds=bounds), ()),)))
        self.states = tuple(changed)
        value = self.snapshot()['input']
        self.assertEqual(value['view']['bounds_mm'], [50., 88., 120., 132.])
        other = next(row for row in value['obstacles']['rects'] if row['kind'] == 'view')
        self.assertEqual(other['bounds_mm'], [190., 148., 230., 172.])

    def test_new_cad_nodes_do_not_force_full_export_registry_validation_in_defaults(self):
        context = self.snapshot()
        self.objects['NewDimension'] = object()
        with patch.object(self.pdf, 'verify', side_effect=AssertionError('do not compare changed full CAD registry')):
            self.assertTrue(self.p.verify_defaults(self.app, None, context, self.controller))

    def test_full_verify_preserves_existing_pdf_source_gate(self):
        context = self.snapshot()
        with patch.object(self.pdf, 'verify', side_effect=RuntimeError('neutral-source-change')):
            with self.assertRaises(self.p.PftDrawingLayoutProfileError) as error:
                self.p.verify(self.app, None, context, self.controller)
        self.assertEqual(error.exception.code, 'DRAWING_LAYOUT_PLAN_STALE')

    def test_snapshot_input_is_detached_from_caller_intent(self):
        context = self.snapshot()
        original = deepcopy(context['input'])
        self.intent['dimensions'][1]['x_mm'] = 500.
        self.assertEqual(context['input'], original)
        self.assertEqual(context['intent']['dimensions'][1]['x_mm'], -40.)

    def test_non_mm_user_quantity_never_guesses_conversion(self):
        self.app.Units.Quantity = lambda value, unit: types.SimpleNamespace(getUserPreferred=lambda: ('0.5 in', 25.4, 'in'))
        with self.assertRaises(self.p.PftDrawingLayoutProfileError) as error:
            self.snapshot()
        self.assertEqual(error.exception.code, 'DRAWING_LAYOUT_STYLE_UNSUPPORTED')

    def test_offset_and_rotated_reference_dto_uses_declared_qt_frame_once(self):
        self.view.Scale = .5
        self.view.Y.Value = 145.
        self.caches['Top'] = cache((-40., -40., 0., 40., 40., 0.))
        self.states = (('Top', (fields(65577, (1.,0.,0.,0.,1.,0.,850.,-1450.,1.), binding=('QGIV','Top')), ())),) + self.states[1:]
        original = self.edge
        for center in ([-15., 10., 0.], [-7.990381056766584, 16.160254037844382, 0.]):
            with self.subTest(center=center):
                def offset(view, name):
                    record = original(view, name)
                    if record['curve'] == 'circle': record['center'] = center.copy()
                    elif name == 'Edge0': record['start'] = [-30., -12., 0.]; record['end'] = [30., -12., 0.]
                    return record
                with patch.object(self.ref, 'read_edge', side_effect=offset):
                    value = self.snapshot()['input']
                line = value['dimensions'][0]['geometry']
                self.assertEqual(line['start_mm'], [70.,151.])
                self.assertEqual(line['end_mm'], [100.,151.])
                circle = value['dimensions'][3]['geometry']
                self.assertEqual(circle['center_mm'], [85.+.5*center[0],145.-.5*center[1]])
                self.assertEqual(circle['radius_mm'], 2.5)

    def test_visible_scene_mapping_mismatch_is_not_silently_rescaled(self):
        changed = deepcopy(self.states)
        changed[0][1][0]['scene_transform'] = (1., 0., 0., 0., 1., 0., 851., -1100., 1.)
        self.states = changed
        with self.assertRaises(self.p.PftDrawingLayoutProfileError) as error:
            self.snapshot()
        self.assertEqual(error.exception.code, 'DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')

    def test_cleanup_unknown_survives_snapshot_defaults_and_full_verify_boundaries(self):
        class UnknownCleanup(RuntimeError):
            cleanup_state = 'unknown'
        context = self.snapshot()
        cases = (
            (lambda: self.snapshot(), patch.object(self.pdf, 'snapshot', side_effect=UnknownCleanup('private raw cause'))),
            (lambda: self.p.verify_defaults(self.app, None, context, self.controller),
             patch.object(self.p, '_gui', side_effect=UnknownCleanup('private raw cause'))),
            (lambda: self.p.verify(self.app, None, context, self.controller),
             patch.object(self.pdf, 'verify', side_effect=UnknownCleanup('private raw cause'))),
        )
        for operation, injected in cases:
            with self.subTest(operation=operation), injected:
                with self.assertRaises(self.p.PftDrawingLayoutProfileError) as error:
                    operation()
                self.assertEqual(error.exception.cleanup_state, 'unknown')
                self.assertNotIn('private raw cause', str(error.exception))

    def test_cleanup_state_rejects_noncontract_values(self):
        for value in (None, True, 1, 'unexpected'):
            self.assertEqual(self.p.PftDrawingLayoutProfileError(cleanup_state=value).cleanup_state, 'unknown')
        self.assertEqual(self.p.PftDrawingLayoutProfileError(cleanup_state='complete').cleanup_state, 'complete')


if __name__ == '__main__':
    unittest.main()
