# SPDX-License-Identifier: Apache-2.0
"""Finite, read-only native inputs for paper-mm dimension suggestions.

No page opening, event pumping, CAD node creation or recompute is performed.
The full PDF profile protects prepare/approval. During an owned write only the
original GUI subtrees are compared, while every original view's complete cached
projection is still independently frozen. CAD node ownership remains the
dimension writer's responsibility; this module never authorizes a write.
"""
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import re
import struct
import sys
import xml.etree.ElementTree as ET

PROFILE = 'freecad_1_1_4_a4_mm_v1'
_MARKER = 'pft-drawing-layout-profile-v1'
_A4_SHA256 = '321b65bd1ea8a4799ada857126698df4773cfafaf2c033b39c35ac6837c63ebf'
_A4_RELATIVE = 'Mod/TechDraw/Templates/ISO/A4_Landscape_ISO5457_advanced.svg'
MAX_OBSTACLES = 128
_MESSAGES = {
    'DRAWING_LAYOUT_PROFILE_UNSUPPORTED': 'This page or native input is outside the finite dimension-layout profile.',
    'DRAWING_LAYOUT_PAGE_OPEN_REQUIRED': 'Open the target drawing page normally, then prepare dimension positions again.',
    'DRAWING_LAYOUT_TEMPLATE_UNSUPPORTED': 'Dimension suggestions require the verified A4 landscape frame and its reserved region.',
    'DRAWING_LAYOUT_STYLE_UNSUPPORTED': 'The current native units, font or dimension defaults are outside the supported layout profile.',
    'DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE': 'A complete existing native label or drawing obstacle could not be read.',
    'DRAWING_LAYOUT_PLAN_STALE': 'The native layout inputs changed; prepare a new complete layout plan.',
}
ERROR_CODES = frozenset(_MESSAGES)


class PftDrawingLayoutProfileError(RuntimeError):
    def __init__(self, code='DRAWING_LAYOUT_PROFILE_UNSUPPORTED', *, cleanup_state='complete'):
        self.code = code if code in _MESSAGES else 'DRAWING_LAYOUT_PROFILE_UNSUPPORTED'
        self.message = _MESSAGES[self.code]
        self.cleanup_state = 'complete' if type(cleanup_state) is str and cleanup_state == 'complete' else 'unknown'
        super().__init__(self.message)


def _fail(code='DRAWING_LAYOUT_PROFILE_UNSUPPORTED', *, cleanup_state='complete'):
    raise PftDrawingLayoutProfileError(code, cleanup_state=cleanup_state)


def _cleanup(error):
    try:
        state = getattr(error, 'cleanup_state', 'complete')
    except Exception:
        return 'unknown'
    return 'complete' if type(state) is str and state == 'complete' else 'unknown'


def _sibling(name):
    filename = Path(__file__).resolve().with_name(name + '.py')
    key = '_pft_layout_profile_' + name
    existing = sys.modules.get(key)
    if existing is not None:
        if Path(existing.__file__).resolve() != filename:
            _fail()
        return existing
    spec = importlib.util.spec_from_file_location(key, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(key, None)
        raise
    return module


def _num(value, low=-1e6, high=1e6):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        _fail()
    return float(value)


def _svg_geometry(data):
    """Bind the known frame geometry; only text contents may be substituted."""
    _sibling('pft_drawing_export_profile')._svg(data)
    def node(element):
        local = element.tag.rsplit('}', 1)[-1]
        text = '' if local in ('text', 'tspan') else (element.text or '').strip()
        return element.tag, tuple(sorted(element.attrib.items())), text, tuple(node(child) for child in element)
    return node(ET.fromstring(data))


def _template(app, page):
    pdf = _sibling('pft_drawing_export_profile')
    template = page.Template
    original = pdf._resource(template.Template)
    installed = pdf._resource(str(Path(app.getResourceDir()) / _A4_RELATIVE))
    if (hashlib.sha256(original).hexdigest() != _A4_SHA256 or installed != original
            or _num(template.Width.Value) != 297. or _num(template.Height.Value) != 210.
            or template.Orientation != 'Landscape'):
        _fail('DRAWING_LAYOUT_TEMPLATE_UNSUPPORTED')
    embedded = pdf._resource(template.PageResult)
    if _svg_geometry(original) != _svg_geometry(embedded):
        _fail('DRAWING_LAYOUT_TEMPLATE_UNSUPPORTED')
    texts = template.EditableTexts
    if type(texts) is not dict or len(texts) > 128:
        _fail('DRAWING_LAYOUT_TEMPLATE_UNSUPPORTED')
    texts = {pdf._text(key, 1024): pdf._text(value, 8192) for key, value in texts.items()}
    if len(json.dumps(texts, ensure_ascii=False).encode('utf8')) > 131072:
        _fail('DRAWING_LAYOUT_TEMPLATE_UNSUPPORTED')
    cache = [[], set(), 0]
    try:
        QtGui = pdf._qt()[1]
        fonts = tuple(pdf._svg_fonts(data, texts, QtGui, cache) for data in (original, embedded))
        return (hashlib.sha256(original).hexdigest(), hashlib.sha256(embedded).hexdigest(),
                tuple(sorted(texts.items())), fonts)
    finally:
        cache[0].clear()


def _defaults(app):
    pdf = _sibling('pft_drawing_export_profile')
    rows, values = [], {}
    definitions = {
        'Labels': {'LabelFont': ('String', 'osifont')},
        'Dimensions': {'FontSize': ('Float', 5.), 'ArrowSize': ('Float', 3.5),
            'ArrowStyle': ('Int', 0), 'StandardAndStyle': ('Int', 0),
            'GapISO': ('Float', 0.), 'GapASME': ('Float', 0.),
            'LineSpacingFactorISO': ('Float', 2.), 'ShowUnits': ('Bool', False),
            'Color': ('Unsigned', 0x000000FF)},
        'Decorations': {'LineGroup': ('Int', 3)},
        'Files': {'LineGroupFile': ('String', str(Path(app.getResourceDir()) / 'Mod/TechDraw/LineGroup/LineGroup.csv'))},
        'Rez': {'Resolution': ('Float', 10.)},
    }
    for suffix, entries in definitions.items():
        group = app.ParamGet('User parameter:BaseApp/Preferences/Mod/TechDraw/' + suffix)
        for key, (kind, default) in entries.items():
            present, value = pdf._parameter(group, key, kind)
            rows.append((suffix, key, kind, present, value))
            values[key] = value if present else default
    units = app.ParamGet('User parameter:BaseApp/Preferences/Units')
    for key, default in (('UserSchema', 0), ('Decimals', 2)):
        present, value = pdf._parameter(units, key, 'Int')
        rows.append(('Units', key, 'Int', present, value))
        values[key] = value if present else default
    if (values['UserSchema'] != 0 or values['Resolution'] != 10.
            or values['ArrowStyle'] != 0 or values['StandardAndStyle'] != 0
            or values['GapISO'] != 0. or values['GapASME'] != 0.
            or values['LineSpacingFactorISO'] != 2.
            or type(values['ShowUnits']) is not bool
            or type(values['LabelFont']) is not str or not 1 <= len(values['LabelFont']) <= 256
            or type(values['Color']) is not int or not 0 <= values['Color'] <= 0xffffffff
            or not 1 <= _num(values['FontSize']) <= 10 or not 0 < _num(values['ArrowSize']) <= 10
            or type(values['LineGroup']) is not int or not 0 <= values['LineGroup'] <= 32):
        _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
    data = pdf._resource(values['LineGroupFile'])
    records = [line.split(',') for line in data.decode('utf-8').splitlines() if line.startswith('*')]
    index = values['LineGroup']
    if index >= len(records) or len(records[index]) != 5:
        _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
    width = _num(float(records[index][1]), .01, 2.)
    values['LineWidth'] = width
    QtCore, QtGui, _ = pdf._qt()
    locale = QtCore.QLocale()
    decimal = locale.decimalPoint()
    if type(decimal) is not str or len(decimal) != 1:
        _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
    values['DecimalPoint'] = decimal
    return values, (tuple(rows), hashlib.sha256(data).hexdigest(), locale.name(), decimal,
                    pdf._enum(locale.numberOptions()))


def _measure(text, style):
    """Native detached plain-text layout with fallback font tables and margin.

    QGIView::exactFontSize truncates sceneSize before multiplying its cap-height
    ratio, and truncates again on return. QGraphicsTextItem uses QTextDocument;
    a full pixel-size allowance bounds the datum label's additional empty unit
    and padding. This is a conservative estimate, not a native dimension node.
    """
    pdf = _sibling('pft_drawing_export_profile')
    _, QtGui, _ = pdf._qt()
    font = QtGui.QFont(style['LabelFont'])
    scene_size = 10. * style['FontSize']
    font.setPixelSize(int(scene_size))
    cap_height = _num(QtGui.QFontMetricsF(font).capHeight(), .01, 10000.)
    pixels = int(int(scene_size) * scene_size / cap_height)
    if not 1 <= pixels <= 2048:
        _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
    font.setPixelSize(pixels)
    document, cache = QtGui.QTextDocument(), [[], set(), 0]
    try:
        document.setDefaultFont(font)
        document.setPlainText(text)
        # QGraphicsTextItem has an unconstrained text width. adjustSize() can
        # choose a wrapped layout and underestimate a long native label.
        document.setTextWidth(-1.)
        size = document.documentLayout().documentSize()
        width = _num(size.width(), .01, 10000.) + pixels
        height = _num(size.height(), .01, 10000.) + pixels
        fingerprint = (pdf._font(font, QtGui, cache), pdf._text_fonts(document, QtGui, cache),
                       pixels, width, height)
        return [width / 10., height / 10.], fingerprint
    finally:
        cache[0].clear()
        document = None


def _fields(item, pdf, paths, cache, root_view=False):
    effect = document = image = None
    try:
        kind = pdf._integer(item.type())
        if kind not in pdf._SUPPORTED_ITEMS:
            _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
        effect = item.graphicsEffect()
        if effect is not None:
            _, _, widgets = pdf._qt()
            if type(effect) is not widgets.QGraphicsColorizeEffect:
                _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
            effect = (pdf._bool(effect.isEnabled()), pdf._color(effect.color()), pdf._num(effect.strength()))
        fields = {'type': kind, 'binding': (item.data(0), item.data(1)),
            'visible': pdf._bool(item.isVisible()), 'transform': pdf._transform(item.transform()),
            'scene_transform': pdf._transform(item.sceneTransform()), 'rotation': pdf._num(item.rotation()),
            'position': (pdf._num(item.pos().x()), pdf._num(item.pos().y())),
            'transform_origin': (pdf._num(item.transformOriginPoint().x()), pdf._num(item.transformOriginPoint().y())),
            'scale': pdf._num(item.scale()), 'opacity': pdf._num(item.opacity()), 'z': pdf._num(item.zValue()),
            'flags': pdf._enum(item.flags()), 'effect': effect}
        if not root_view:
            fields['bounds'] = pdf._rect(item.boundingRect())
        if kind in pdf._PRIM_PATH_ITEMS:
            fields['native_shape'] = pdf._path(item.shape(), paths)
        if kind == 2:
            fields['path'] = pdf._path(pdf._required_method(item, 'path')(), paths)
        if kind in pdf._RECT_ITEMS:
            fields['rect'] = pdf._rect(pdf._required_method(item, 'rect')())
        if ((kind in pdf._RECT_ITEMS or kind == 2 or kind in pdf._PRIM_PATH_ITEMS)
                and kind not in (65555, 65556) and not (kind == 65569 and fields['visible'] is False)):
            fields['pen'] = pdf._pen(pdf._required_method(item, 'pen')())
            fields['brush'] = pdf._brush(pdf._required_method(item, 'brush')())
        if kind in pdf._TEXT_ITEMS:
            text = pdf._text(pdf._required_method(item, 'toPlainText')(), 65536)
            fields.update(text=text, font=pdf._font(pdf._required_method(item, 'font')(), pdf._qt()[1], cache))
            if text:
                document = pdf._required_method(item, 'document')()
                if type(document) is not pdf._qt()[1].QTextDocument:
                    _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
                html = pdf._text(document.toHtml(), 131072)
                # Write-phase verification must reject changed resources before
                # any detached glyph layout can request them. Prepare's earlier
                # full PDF snapshot does not authorize a later changed document.
                if re.search(r'<(?:img|object)|(?:src\s*=|url\s*\()', html, re.I):
                    _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
                fields.update(html=html,
                    text_color=pdf._color(pdf._required_method(item, 'defaultTextColor')()),
                    glyph_fonts=pdf._text_fonts(document, pdf._qt()[1], cache))
        if kind == 65538:
            image = pdf._required_method(item, 'pixmap')().toImage()
            if image.width() * image.height() > 1_000_000:
                _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
            raw = bytes(image.constBits()[:image.sizeInBytes()]) if not image.isNull() else b''
            fields['pixmap'] = (image.width(), image.height(), pdf._enum(image.format()), hashlib.sha256(raw).hexdigest())
        return fields
    finally:
        item = effect = document = image = None


def _gui(page, gui, original_views, original_dimensions):
    pdf = _sibling('pft_drawing_export_profile')
    _, _, widgets = pdf._qt()
    scene = importlib.import_module('TechDrawGui').getSceneForPage(page)
    if scene is None or type(scene) is not widgets.QGraphicsScene or not scene.views():
        _fail('DRAWING_LAYOUT_PAGE_OPEN_REQUIRED')
    old = {obj.Name: obj for obj in (*original_views, *original_dimensions)}
    view_names = {obj.Name for obj in original_views}
    page_members = pdf._seq(page.Views, 100)
    all_items = roots = templates = ()
    children_by_id, selected, owners = {}, {}, {}
    cache, paths, visitor, exclude = [[], set(), 0], [0], None, None
    try:
        all_items = pdf._seq(scene.items(), pdf.MAX_SCENE_ITEMS)
        if scene.selectedItems():
            _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
        # Borrow childItems rather than parentItem: PySide can transfer native
        # top-level group ownership when wrapping that latter borrowed pointer.
        children_by_id = {id(item): pdf._seq(item.childItems(), pdf.MAX_SCENE_ITEMS) for item in all_items}
        identifiers = {id(item) for item in all_items}
        child_ids = [id(child) for children in children_by_id.values() for child in children]
        if len(identifiers) != len(all_items) or len(child_ids) != len(set(child_ids)) or set(child_ids) - identifiers:
            _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
        roots = [item for item in all_items if id(item) not in set(child_ids)]
        templates = [item for item in roots if item.type() == 65566]
        if (len(templates) != 1 or len(roots) != len(original_views) + 1
                or {item.data(1) for item in roots if item.data(0) == 'QGIV'} != view_names):
            _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
        for item in all_items:
            if item.data(0) == 'QGIV' and item.data(1) in old:
                name = item.data(1)
                expected = 65577 if name in view_names else 65575
                if name in selected or item.type() != expected:
                    _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
                selected[name] = item
        if set(selected) != set(old):
            _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
        seen = set()
        def mark(item, depth):
            if (depth > 32 or id(item) in seen or len(seen) >= pdf.MAX_SCENE_ITEMS
                    or type(item).__module__ != 'PySide6.QtWidgets' or item.type() not in pdf._SUPPORTED_ITEMS):
                _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
            seen.add(id(item))
        def exclude(item, depth):
            child = None
            try:
                mark(item, depth)
                if item.data(0) == 'QGIV':
                    _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
                for child in children_by_id[id(item)]:
                    exclude(child, depth + 1)
            finally:
                item = child = None
        def visitor(item, depth=0, owner_view=None):
            child = None
            try:
                mark(item, depth)
                kind, name = item.type(), item.data(1)
                if kind == 65575:
                    obj = page.Document.getObject(name) if type(name) is str else None
                    dims = _sibling('pft_drawing_dimensions')
                    if item.data(0) != 'QGIV' or owner_view is None or sum(member is obj for member in page_members) != 1:
                        _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
                    dims._node(page.Document, obj, 'TechDraw::DrawViewDimension', mutable=True)
                    references = dims._refs(obj.References2D)
                    if (len(references) != 1 or references[0][0] is not old[owner_view] or obj.References3D):
                        _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
                    owners[name] = owner_view
                    if name not in old:
                        # Only an exactly bound, registered new native dimension
                        # in a known view can retire its new GUI subtree.
                        for child in children_by_id[id(item)]:
                            exclude(child, depth + 1)
                    return None
                fields = _fields(item, pdf, paths, cache, name in view_names and item.data(0) == 'QGIV')
                children = []
                for child in children_by_id[id(item)]:
                    value = visitor(child, depth + 1, owner_view)
                    if value is not None: children.append(value)
                return fields, tuple(children)
            finally:
                item = child = None
        states = [('_template', visitor(templates[0]))]
        for obj in original_views:
            states.append((obj.Name, visitor(selected[obj.Name], owner_view=obj.Name)))
        for obj in original_dimensions:
            root = selected[obj.Name]
            if obj.Name not in owners:
                _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
            fields = _fields(root, pdf, paths, cache)
            fields['owner_view'] = owners[obj.Name]
            children = []
            for child in children_by_id[id(root)]:
                value = visitor(child, 1, owners[obj.Name])
                if value is not None: children.append(value)
            states.append((obj.Name, (fields, tuple(children))))
        if seen != identifiers or len(json.dumps(states, ensure_ascii=False).encode('utf8')) > pdf.MAX_SCENE_BYTES:
            _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
        return scene, tuple(states)
    finally:
        visitor = exclude = None
        selected.clear(); owners.clear(); children_by_id.clear(); cache[0].clear()
        all_items = roots = templates = ()
        item = child = root = None


def _paper_rect(fields):
    x, y, width, height = fields['bounds']
    matrix = fields['scene_transform']
    if len(matrix) != 9 or matrix[2] != 0. or matrix[5] != 0. or matrix[8] != 1.:
        _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
    points = [(matrix[0] * u + matrix[3] * v + matrix[6],
               matrix[1] * u + matrix[4] * v + matrix[7])
              for u, v in ((x, y), (x + width, y), (x, y + height), (x + width, y + height))]
    return [min(p[0] for p in points) / 10., -max(p[1] for p in points) / 10.,
            max(p[0] for p in points) / 10., -min(p[1] for p in points) / 10.]


def _walk(row):
    yield row[0]
    for child in row[1]:
        yield from _walk(child)


def _view_geometry(view, page, cache):
    scale_type = view.ScaleType
    scale = _num(view.Scale, .000001, 1000.)
    if scale_type == 'Page':
        if scale != _num(page.Scale, .000001, 1000.):
            _fail()
    elif scale_type != 'Custom':
        _fail()
    origin = [_num(view.X.Value), _num(view.Y.Value)]
    bounds = cache[2]
    points = []
    for _, (_, box, _) in bounds:
        # getEdgeByIndex mirrors the cached display edge before dividing by
        # Scale. Its result is already Y-up; only the QGraphics scene needs a
        # Y inversion. Reversing these indexed values again mirrors an offset
        # circle or a rotated edge away from its actual rendered location.
        points.extend([[origin[0] + scale * box[0], origin[1] + scale * box[1]],
                       [origin[0] + scale * box[3], origin[1] + scale * box[4]]])
    if not points:
        _fail()
    rect = [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]
    return {'name': view.Name, 'origin_mm': origin, 'bounds_mm': rect}, scale


def _frozen_inputs(app, gui, document, page, view, intent, controller, data):
    pdf = _sibling('pft_drawing_export_profile')
    views, dimensions = data['views'], data['dimensions']
    if not any(obj is view for obj in views):
        _fail()
    for obj in (*views, *dimensions):
        if obj.Document is not document or document.getObject(obj.Name) is not obj:
            _fail()
    template = _template(app, page)
    style, defaults = _defaults(app)
    scene, gui_state = _gui(page, gui, views, dimensions)
    geometry_cache = tuple((obj.Name, pdf._projected_cache(obj)) for obj in views)
    maps = {name: state for name, state in gui_state}
    caches = dict(geometry_cache)
    view_dtos = {}
    for obj in views:
        dto, scale = _view_geometry(obj, page, caches[obj.Name])
        expected = (1., 0., 0., 0., 1., 0., dto['origin_mm'][0] * 10., -dto['origin_mm'][1] * 10., 1.)
        if maps[obj.Name][0]['scene_transform'] != expected:
            _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
        # OCC bounds describe projected geometry, not its rendered stroke.
        # Freeze and include the actual visible native primitives as well;
        # thick lines and native hit-shape margins must not become free space.
        for fields in _walk(maps[obj.Name]):
            if fields['visible'] and fields['type'] in (pdf._PRIM_PATH_ITEMS | pdf._RECT_ITEMS | {2, 65538}):
                actual = _paper_rect(fields)
                if actual[0] < actual[2] and actual[1] < actual[3]:
                    old_bounds = dto['bounds_mm']
                    dto['bounds_mm'] = [min(old_bounds[0], actual[0]), min(old_bounds[1], actual[1]),
                                        max(old_bounds[2], actual[2]), max(old_bounds[3], actual[3])]
        view_dtos[obj.Name] = dto, scale
    obstacles = []
    def obstacle(name, rect):
        if rect[0] >= rect[2] or rect[1] >= rect[3]: return
        if len(obstacles) >= MAX_OBSTACLES:
            _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
        identifier = name[:40] + '_' + hashlib.sha256((name + str(len(obstacles))).encode()).hexdigest()[:12]
        obstacles.append({'name': identifier, 'kind': 'dimension', 'bounds_mm': rect})
    for obj in views:
        if obj is not view:
            obstacles.append({'name': obj.Name, 'kind': 'view', 'bounds_mm': view_dtos[obj.Name][0]['bounds_mm']})
        for fields in _walk(maps[obj.Name]):
            if fields['type'] in pdf._TEXT_ITEMS and fields.get('text') and fields['visible']:
                obstacle(obj.Name + '_caption', _paper_rect(fields))
    for obj in dimensions:
        fields_list = list(_walk(maps[obj.Name]))
        labels = [fields for fields in fields_list if fields['type'] == 65551]
        if len(labels) != 1:
            _fail('DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE')
        obstacle(obj.Name + '_label', _paper_rect(labels[0]))
        for fields in fields_list:
            if fields['type'] in pdf._PRIM_PATH_ITEMS or fields['type'] in (2, 3):
                obstacle(obj.Name + '_line', _paper_rect(fields))
    refs, dims = _sibling('pft_drawing_references'), _sibling('pft_drawing_dimensions')
    rows, measurements = [], []
    origin = view_dtos[view.Name][0]['origin_mm']; scale = view_dtos[view.Name][1]
    for item in intent['dimensions']:
        edge = refs.read_edge(view, item['edge'])
        value = dims._value(edge, item['kind'])
        quantity = app.Units.Quantity(value, 'mm').getUserPreferred()
        if type(quantity) is not tuple or len(quantity) != 3 or quantity[1] != 1. or quantity[2] != 'mm':
            _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
        text = format(value, '.2f').replace('.', style['DecimalPoint'])
        text = ('⌀' if item['kind'] == 'Diameter' else 'R' if item['kind'] == 'Radius' else '') + text
        if style['ShowUnits']: text += ' mm'
        size, font = _measure(text, style)
        if item['kind'] in ('Diameter', 'Radius', 'Distance'):
            diagonal = math.hypot(*size)
            size = [diagonal, diagonal]
        elif item['kind'] == 'DistanceY':
            size = [size[1], size[0]]
        if edge['curve'] == 'line':
            # The public reference DTO is normalized to Qt Y-down, unlike the
            # raw indexed BReps used by _view_geometry above.
            geometry = {'curve': 'line', 'start_mm': [origin[0] + scale * edge['start'][0], origin[1] - scale * edge['start'][1]],
                        'end_mm': [origin[0] + scale * edge['end'][0], origin[1] - scale * edge['end'][1]]}
        else:
            geometry = {'curve': 'circle', 'center_mm': [origin[0] + scale * edge['center'][0], origin[1] - scale * edge['center'][1]],
                        'radius_mm': scale * edge['radius_mm']}
        row = {'edge': item['edge'], 'kind': item['kind'], 'placement': 'auto' if item.get('placement') == 'auto' else 'explicit',
               'geometry': geometry, 'text': text, 'label_size_mm': size}
        if row['placement'] == 'explicit': row.update(x_mm=item['x_mm'], y_mm=item['y_mm'])
        rows.append(row); measurements.append((refs.edge_signature(edge), font))
    result = _sibling('pft_drawing_layout').validate_input({'version': 1,
        'paper': {'width_mm': 297., 'height_mm': 210., 'margin_mm': 10., 'reserved_rects': [[0., 0., 297., 80.]]},
        'view': view_dtos[view.Name][0], 'obstacles': {'rects': obstacles, 'segments': []},
        'spacing': {'clearance_mm': max(.5, style['LineWidth']),
                    'offset_mm': max(6., style['FontSize'] + style['ArrowSize']),
                    'lane_mm': max(4., style['FontSize'] + 1.)}, 'dimensions': rows})
    return result, (template, defaults, geometry_cache, gui_state, tuple(measurements)), scene


def snapshot(app, gui, document, page, view, intent, controller):
    try:
        contract = _sibling('pft_drawing_dimensions_contract')
        intent = contract.validate_intent(intent)
        if intent['page'] != page.Name or intent['view'] != view.Name:
            _fail()
        pdf_context = _sibling('pft_drawing_export_profile').snapshot(app, gui, document, page, controller)
        value, frozen, scene = _frozen_inputs(app, gui, document, page, view, intent, controller, pdf_context['data'])
        context = {'_profile': _MARKER, 'document': document, 'page': page, 'view': view,
            'intent': deepcopy(intent), 'export_context': pdf_context, 'input': value,
            'frozen': frozen, 'scene_object': scene}
        verify(app, gui, context, controller)
        return context
    except PftDrawingLayoutProfileError:
        raise
    except Exception as error:
        _fail(cleanup_state=_cleanup(error))


def verify_defaults(app, gui, context, controller):
    try:
        if type(context) is not dict or context.get('_profile') != _MARKER:
            _fail()
        controller._ensure_main_thread()
        document, page, view = context['document'], context['page'], context['view']
        if app.getDocument(document.Name) is not document or document.getObject(page.Name) is not page or document.getObject(view.Name) is not view:
            _fail()
        value, frozen, scene = _frozen_inputs(app, gui, document, page, view, context['intent'], controller, context['export_context']['data'])
        if scene is not context['scene_object'] or frozen != context['frozen'] or value != context['input']:
            _fail()
        return True
    except Exception as error:
        _fail('DRAWING_LAYOUT_PLAN_STALE', cleanup_state=_cleanup(error))


def verify(app, gui, context, controller):
    try:
        _sibling('pft_drawing_export_profile').verify(app, gui, context['export_context'], controller)
        return verify_defaults(app, gui, context, controller)
    except Exception as error:
        _fail('DRAWING_LAYOUT_PLAN_STALE', cleanup_state=_cleanup(error))


def verify_created_style(app, gui, context, created, controller):
    """Read actual newly created dimension styles; never repair preferences.

    The writer surrounds this check with its owned-transaction check and the
    existing scoped native-layout verification. A prefix is supported while a
    bounded batch is being built; every member must already belong to this page
    and have its final reference/format fields. No scene is opened or rendered.
    """
    try:
        if type(context) is not dict or context.get('_profile') != _MARKER:
            _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
        if type(created) not in (list, tuple) or not 1 <= len(created) <= 8:
            _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
        created = tuple(created)
        if len({id(dim) for dim in created}) != len(created):
            _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
        controller._ensure_main_thread()
        document, page, view = context['document'], context['page'], context['view']
        if (app.getDocument(document.Name) is not document or document.getObject(page.Name) is not page
                or document.getObject(view.Name) is not view):
            _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
        intent = context['intent']['dimensions']
        if len(created) > len(intent):
            _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
        style, defaults = _defaults(app)
        if defaults != context['frozen'][1]:
            _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
        pdf, dimensions = _sibling('pft_drawing_export_profile'), _sibling('pft_drawing_dimensions')
        members = pdf._seq(page.Views, 100)
        lengths = {'Fontsize': style['FontSize'], 'Arrowsize': style['ArrowSize'], 'LineWidth': style['LineWidth']}
        scalars = {'GapFactorISO': style['GapISO'], 'GapFactorASME': style['GapASME'],
                   'LineSpacingFactorISO': style['LineSpacingFactorISO']}
        # Base::Color::setPackedValue performs float32 channel/255 division;
        # PropertyColor::getPyObject exposes those exact floats as a tuple4.
        color = tuple(struct.unpack('f', struct.pack('f', ((style['Color'] >> shift) & 255) / 255.))[0]
                      for shift in (24, 16, 8, 0))
        native_identity = []
        provider = None
        try:
            # Re-read the complete bounded batch: a later native read or
            # observer must not silently change an earlier checked style.
            for reading in range(2):
                for index, (dim, item) in enumerate(zip(created, intent)):
                    dimensions._node(document, dim, 'TechDraw::DrawViewDimension', mutable=True)
                    if sum(member is dim for member in members) != 1:
                        _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
                    if (dim.Type != item['kind'] or dim.MeasureType != 'Projected' or dim.Arbitrary is not False
                            or dim.FormatSpec != dimensions._format(item['kind'])
                            or type(dim.ShowUnits) is not bool or dim.ShowUnits != style['ShowUnits']
                            or pdf._seq(dim.ExpressionEngine, 100)):
                        _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
                    for field, expected in (('ArbitraryTolerances', False), ('TheoreticalExact', False),
                                            ('EqualTolerance', True), ('Inverted', False), ('AngleOverride', False)):
                        if getattr(dim, field) is not expected:
                            _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
                    for field in ('OverTolerance', 'UnderTolerance', 'LineAngle', 'ExtensionAngle'):
                        if dimensions._number(getattr(dim, field)) != 0.:
                            _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
                    if (dimensions._refs(dim.References2D) != ((view, (item['edge'],)),)
                            or dimensions._refs(dim.References3D)):
                        _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
                    provider = dim.ViewObject
                    if (provider.Object is not dim or provider.TypeId != 'TechDrawGui::ViewProviderDimension'
                            or type(provider.TypeId) is not str or 'Proxy' in pdf._seq(provider.PropertiesList, 256)):
                        _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
                    try:
                        getattr(provider, 'Proxy')
                    except AttributeError:
                        pass
                    else:
                        _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
                    if (type(provider.Font) is not str or provider.Font != style['LabelFont']
                            or provider.ArrowStyle != 'Filled arrow' or provider.StandardAndStyle != 'ISO Oriented'
                            or provider.RenderingExtent != 'Normal' or provider.FlipArrowheads is not False):
                        _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
                    actual_color = provider.Color
                    if (type(actual_color) is not tuple or len(actual_color) != 4
                            or any(type(channel) is not float or not math.isfinite(channel) for channel in actual_color)
                            or actual_color != color):
                        _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
                    for field, expected in lengths.items():
                        value, signature = pdf._quantity(getattr(provider, field))
                        if value != expected or signature != (1, 0, 0, 0, 0, 0, 0, 0):
                            _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
                    for field, expected in scalars.items():
                        value = getattr(provider, field)
                        if type(value) is not float or not math.isfinite(value) or value != expected:
                            _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
                    if reading == 0:
                        native_identity.append((dim, provider))
                    elif native_identity[index][0] is not dim or native_identity[index][1] is not provider:
                        _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
            # These are the last native binding reads, after style getters that
            # can run an observer in a hostile or broken extension.
            for dim, provider in native_identity:
                if (document.getObject(dim.Name) is not dim or dim.Document is not document
                        or dim.ViewObject is not provider or provider.Object is not dim
                        or sum(member is dim for member in pdf._seq(page.Views, 100)) != 1):
                    _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED')
        finally:
            provider = None
            native_identity.clear()
        return True
    except Exception as error:
        _fail('DRAWING_LAYOUT_STYLE_UNSUPPORTED', cleanup_state=_cleanup(error))
