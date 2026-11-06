# SPDX-License-Identifier: Apache-2.0
"""Add explicit cached-projection dimensions through native approval only.

No event pumping or projection-ready inference. Freeze source geometry, native
view settings and selected projected edges; check actual associative values after
one bounded recompute. The original model is never recomputed or saved here.
"""
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
_MESSAGES = {'DIMENSION_UNSUPPORTED': '当前原生图纸、来源、投影引用或测量类型不支持本次关联尺寸。', 'DIMENSION_PLAN_STALE': '图纸、来源、视图、投影引用或预定名称已变化，请重新预览。', 'DIMENSION_CREATE_FAILED': '关联尺寸节点、引用、位置或原对象保护未通过核对。', 'DIMENSION_RECOMPUTE_UNVERIFIED': '关联尺寸局部重算未通过核对，不会自动重试。', 'DIMENSION_VALUE_UNVERIFIED': '原生关联尺寸量值与预览引用不一致，请检查投影及撤销记录。', 'DIMENSION_NATIVE_CONFIRMATION_REQUIRED': '关联尺寸只能通过原生确认窗口的人工确认回调创建。', 'DIMENSION_RESULT_INVALID': '关联尺寸结果格式未通过核对。'}

class PftDrawingDimensionsError(RuntimeError):

    def __init__(self, code):
        self.code = code if code in _MESSAGES else 'DIMENSION_UNSUPPORTED'
        super().__init__(_MESSAGES[self.code])

def _fail(code='DIMENSION_UNSUPPORTED'):
    raise PftDrawingDimensionsError(code)

def _sibling(name):
    filename = Path(__file__).resolve().with_name(name + '.py')
    key = '_pft_dimensions_' + name
    module = sys.modules.get(key)
    if module is not None:
        if Path(module.__file__).resolve() != filename:
            _fail()
        return module
    spec = importlib.util.spec_from_file_location(key, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(key, None)
        raise
    return module
CAPABILITIES = _sibling('pft_drawing_dimensions_contract').CAPABILITIES

def _number(value):
    if hasattr(value, 'Value'):
        value = value.Value
    if type(value) not in (int, float) or not math.isfinite(value):
        _fail()
    return float(value)

def _sequence(value, maximum=10000):
    if type(value) not in (list, tuple) or len(value) > maximum:
        _fail()
    return tuple(value)

def _vector(value):
    return tuple((_number(getattr(value, axis)) for axis in ('x', 'y', 'z')))

def _node(document, obj, kind, mutable=False):
    d = _sibling('pft_drawing')
    if obj is None or obj.TypeId != kind or (not d._name(obj.Name)) or (obj.Document is not document) or (document.getObject(obj.Name) is not obj):
        _fail()
    properties = _sequence(obj.PropertiesList, 256)
    if any((type(p) is not str for p in properties)) or 'Proxy' in properties:
        _fail()
    try:
        getattr(obj, 'Proxy')
    except AttributeError:
        pass
    else:
        _fail()
    states = _sequence(obj.State, 16)
    allowed = {'Up-to-date', 'Expanded'} | ({'Touched'} if mutable else set())
    if not states or any((type(s) is not str for s in states)) or set(states) - allowed or (not mutable and 'Up-to-date' not in states) or (obj.isValid() is not True):
        _fail()
    return (obj.Name, kind)

def _view_state(document, view, source):
    _node(document, view, 'TechDraw::DrawViewPart')
    if tuple(view.Source) != (source,) or view.CoarseView is not False:
        _fail()
    # Native projection combines Source and XSource. This operation binds one
    # complete source; additional or partially selected sources are unsupported.
    extra_sources = _sequence(view.XSource, 100) if 'XSource' in view.PropertiesList else ()
    if extra_sources:
        _fail()
    # Cosmetic edges and center lines join the same indexed geometry array.
    # Empty native extension lists are required before calling a reference an
    # entity projection. Never inspect or stringify their Python objects.
    for field, native_type in (('CosmeticEdges', 'TechDraw::PropertyCosmeticEdgeList'),
                               ('CenterLines', 'TechDraw::PropertyCenterLineList')):
        if field not in view.PropertiesList or view.getTypeIdOfProperty(field) != native_type:
            _fail()
        items = getattr(view, field)
        if type(items) is not list or items:
            _fail()
    flags = []
    for name in ('Perspective', 'HardHidden', 'SmoothVisible', 'SmoothHidden', 'SeamVisible', 'SeamHidden', 'IsoVisible', 'IsoHidden'):
        if name in view.PropertiesList:
            value = getattr(view, name)
            if type(value) is not bool:
                _fail()
            if name == 'Perspective' and value:
                _fail()
            flags.append((name, value))
    return (_vector(view.Direction), _vector(view.XDirection), _number(view.Rotation), view.ScaleType, _number(view.Scale), _number(view.X), _number(view.Y), tuple(flags), extra_sources)

def _refs(value):
    refs = []
    for entry in _sequence(value, 8):
        if type(entry) not in (list, tuple) or len(entry) != 2:
            _fail()
        owner, names = entry
        if type(names) is str:
            names = (names,)
        names = _sequence(names, 8)
        if any((type(n) is not str for n in names)):
            _fail()
        refs.append((owner, names))
    return tuple(refs)

def _dimension_state(document, dimension, page, source):
    _node(document, dimension, 'TechDraw::DrawViewDimension')
    kind, measure_type, arbitrary = dimension.Type, dimension.MeasureType, dimension.Arbitrary
    references_2d, references_3d = _refs(dimension.References2D), _refs(dimension.References3D)
    if (type(kind) is not str or kind not in _sibling('pft_drawing_dimensions_contract').KINDS
            or type(measure_type) is not str or measure_type != 'Projected' or arbitrary is not False
            or references_3d or len(references_2d) != 1):
        _fail()
    owner, names = references_2d[0]
    if len(names) != 1 or sum(member is owner for member in _sequence(page.Views, 100)) != 1:
        _fail()
    # Resolve only a registered, same-page and same-source native view before
    # reading projected geometry or invoking the dimension's native measurement.
    _view_state(document, owner, source)
    helper = _sibling('pft_drawing_references')
    helper.edge_index(names[0])
    _value(helper.read_edge(owner, names[0]), kind)
    formatting = dimension.FormatSpec
    x, y = _number(dimension.X), _number(dimension.Y)
    return (kind, measure_type, arbitrary, references_2d, references_3d, formatting,
            x, y, _number(dimension.getRawValue()))

def _page_state(document, page, mutable=False, template_touched=False):
    _node(document, page, 'TechDraw::DrawPage', mutable)
    template = page.Template
    _node(document, template, 'TechDraw::DrawSVGTemplate', template_touched)
    properties = _sequence(template.PropertiesList, 256)
    owners = _sequence(template.InList, 8)
    expressions = _sequence(template.ExpressionEngine, 100)
    if (len(owners) != 1 or owners[0] is not page or page.Document is not document
            or document.getObject(page.Name) is not page or expressions):
        _fail()
    texts = template.EditableTexts
    if type(texts) is not dict or len(texts) > 100 or any((type(k) is not str or type(v) is not str or len(k) > 256 or (len(v) > 4096) for k, v in texts.items())):
        _fail()
    encoded = json.dumps(texts, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(encoded) > 128 * 1024:
        _fail()
    path = template.Template
    if type(path) is not str or len(path) > 4096:
        _fail()
    width, height = _number(template.Width), _number(template.Height)
    orientation = template.Orientation
    if width <= 0 or height <= 0 or type(orientation) is not str or orientation not in ('Portrait', 'Landscape'):
        _fail()
    # Inspect only the bounded native embedded-file registration after its
    # type is known. No resolve/stat/open is added, and no path enters public
    # rows/results.
    if ('PageResult' not in properties
            or template.getTypeIdOfProperty('PageResult') != 'App::PropertyFileIncluded'):
        _fail()
    page_result = template.PageResult
    if type(page_result) is not str or not 1 <= len(page_result) <= 4096:
        _fail()
    return (template, path, hashlib.sha256(encoded).hexdigest(), _number(page.Scale), page.ProjectionType,
            width, height, orientation, page_result, properties, owners, expressions,
            _sequence(template.State, 16))

def _capture(app, gui, document, page, view, controller, owned=None, created=()):
    drawing = _sibling('pft_drawing')
    if owned is not None:
        owned()
    _node(document, view, 'TechDraw::DrawViewPart')
    sources = _sequence(view.Source, 2)
    if len(sources) != 1:
        _fail()
    source = sources[0]
    # Opening a native SVG page can touch its template while extracting page
    # attributes. Validate and freeze this exact template before granting the
    # narrow read-only Touched exception; it is not a newly created node.
    page_state = _page_state(document, page, owned is not None, template_touched=True)
    captured = drawing._capture(app, gui, document, source, controller, owned,
                               (page, *created) if owned is not None and created else (),
                               allowed_touched=(page_state[0],))
    objects, snapshot, shapes, tip, parents = captured
    views = _sequence(page.Views, 100)
    original_views = tuple((v for v in views if not any((v is n for n in created))))
    if sum((v is view for v in original_views)) != 1 or len({id(v) for v in views}) != len(views):
        _fail()
    states = []
    for item in original_views:
        if item.TypeId == 'TechDraw::DrawViewPart':
            states.append(('view', _view_state(document, item, source)))
        elif item.TypeId == 'TechDraw::DrawViewDimension':
            states.append(('dimension', _dimension_state(document, item, page, source)))
        else:
            _fail()
    if owned is not None:
        owned()
    return {'objects': objects, 'source': source, 'source_snapshot': snapshot, 'shapes': shapes, 'tip': tip, 'parents': parents, 'page_state': page_state, 'views': original_views, 'view_states': tuple(states)}

def _value(record, kind):
    if record.get('status') != 'available' or kind not in record.get('supported_kinds', []):
        _fail()
    if kind in ('Diameter', 'Radius'):
        value = _number(record['radius_mm']) * (2 if kind == 'Diameter' else 1)
    else:
        start, end = (record['start'], record['end'])
        if kind == 'Distance':
            value = _number(record['length_mm'])
        else:
            axis = 0 if kind == 'DistanceX' else 1
            value = abs(_number(end[axis]) - _number(start[axis]))
    if not 0 < value <= 1000000000.0:
        _fail()
    return value

def _edges(view, dimensions):
    helper = _sibling('pft_drawing_references')
    records = tuple((helper.read_edge(view, item['edge']) for item in dimensions))
    values = tuple((_value(record, item['kind']) for record, item in zip(records, dimensions)))
    signatures = tuple((helper.edge_signature(record) for record in records))
    return (signatures, values)

def _same_capture(current, original, created, allow_template_cleaned=False):
    d = _sibling('pft_drawing')
    current_page, original_page = current['page_state'], original['page_state']
    page_matches = current_page == original_page
    if not page_matches and allow_template_cleaned is True:
        current_flags, original_flags = current_page[-1], original_page[-1]
        # Only a successful owned local recompute may clean this cache flag.
        # All template contents, identities and other flags stay frozen.
        page_matches = (
            current_page[:-1] == original_page[:-1]
            and original_flags.count('Touched') == 1
            and 'Up-to-date' not in original_flags
            and current_flags.count('Up-to-date') == 1
            and 'Touched' not in current_flags
            and tuple(flag for flag in original_flags if flag != 'Touched')
                == tuple(flag for flag in current_flags if flag != 'Up-to-date')
        )
    return len(current['objects']) == len(original['objects']) + len(created) and {id(o) for o in current['objects']} == {id(o) for o in (*original['objects'], *created)} and (current['source'] is original['source']) and (current['tip'] is original['tip']) and (current['source_snapshot'] == original['source_snapshot']) and (current['shapes'][0].isSame(original['shapes'][0]) is True) and (current['tip'] is None or current['shapes'][1].isSame(original['shapes'][1]) is True) and d._same_parents(current['parents'], original['parents']) and page_matches and (len(current['views']) == len(original['views'])) and all((a is b for a, b in zip(current['views'], original['views']))) and (current['view_states'] == original['view_states'])

def _verify(app, gui, document, plan, controller, owned=None, created=(), allow_template_cleaned=False):
    if plan['document'] is not document or document.getObject(plan['page'].Name) is not plan['page'] or document.getObject(plan['view'].Name) is not plan['view']:
        _fail('DIMENSION_PLAN_STALE')
    current = _capture(app, gui, document, plan['page'], plan['view'], controller, owned, created)
    if not _same_capture(current, plan['snapshot'], created, allow_template_cleaned):
        _fail('DIMENSION_PLAN_STALE')
    if _edges(plan['view'], plan['request']['dimensions']) != (plan['edges'], plan['values']):
        _fail('DIMENSION_PLAN_STALE')
    for i, name in enumerate(plan['names']):
        if document.getObject(name) is not (created[i] if i < len(created) else None):
            _fail('DIMENSION_PLAN_STALE')
    if owned is not None:
        owned()

def verify_plan(app, gui, document, plan, controller):
    try:
        _verify(app, gui, document, plan, controller)
    except Exception:
        _fail('DIMENSION_PLAN_STALE')

def prepare_plan(app, gui, document, page, view, dimensions, controller):
    try:
        contract = _sibling('pft_drawing_dimensions_contract')
        request = contract.validate_request({'page': page.Name, 'view': view.Name, 'dimensions': dimensions})
        snapshot = _capture(app, gui, document, page, view, controller)
        edges, values = _edges(view, request['dimensions'])
        names = []
        for item in request['dimensions']:
            digest = hashlib.sha256(json.dumps([page.Name, view.Name, item['edge'], item['kind']], ensure_ascii=False).encode()).hexdigest()[:16]
            name = 'PhoenixDimension_' + digest
            if document.getObject(name) is not None:
                _fail()
            names.append(name)
        plan = {'document': document, 'page': page, 'view': view, 'request': request, 'snapshot': snapshot, 'edges': edges, 'values': values, 'names': tuple(names)}
        rows = [{'object': name, 'field': 'TechDraw dimension', 'old': None, 'new': value, 'status': 'change', 'edge': item['edge'], 'kind': item['kind'], 'value_mm': value, 'x_mm': item['x_mm'], 'y_mm': item['y_mm'], 'source': 'native_cached_projected_geometry', 'note': '由当前投影边估算mm量值；位置为相对视图的纸面mm偏移。确认后核对原生关联尺寸；投影未验证，须人工复核，不保存CAD。'} for name, item, value in zip(names, request['dimensions'], values)]
        verify_plan(app, gui, document, plan, controller)
        return (plan, rows)
    except PftDrawingDimensionsError:
        raise
    except Exception:
        _fail()

def _format(kind):
    return '⌀%.2f' if kind == 'Diameter' else 'R%.2f' if kind == 'Radius' else '%.2f'

def _verify_created(app, gui, document, plan, controller, created, owned=None, allow_template_cleaned=False):
    _verify(app, gui, document, plan, controller, owned, created, allow_template_cleaned)
    views = tuple(plan['page'].Views)
    expected = plan['snapshot']['views'] + tuple(created)
    if len(views) != len(expected) or any((a is not b for a, b in zip(views, expected))):
        _fail('DIMENSION_CREATE_FAILED')
    readings = []
    for dim, item, value in zip(created, plan['request']['dimensions'], plan['values']):
        _node(document, dim, 'TechDraw::DrawViewDimension')
        try:
            actual = _dimension_state(document, dim, plan['page'], plan['snapshot']['source'])
        except PftDrawingDimensionsError:
            _fail('DIMENSION_CREATE_FAILED')
        if actual[0] != item['kind'] or actual[1] != 'Projected' or actual[2] is not False or (actual[3] != ((plan['view'], (item['edge'],)),)) or (actual[4] != ()) or (actual[5] != _format(item['kind'])) or (actual[6] != item['x_mm']) or (actual[7] != item['y_mm']):
            _fail('DIMENSION_CREATE_FAILED')
        if not 0 < actual[8] <= 1e9 or not math.isclose(actual[8], value, rel_tol=1e-07, abs_tol=1e-06):
            _fail('DIMENSION_VALUE_UNVERIFIED')
        readings.append(actual[8])
        if owned is not None:
            owned()
    _verify(app, gui, document, plan, controller, owned, created, allow_template_cleaned)
    return tuple(readings)

def apply_plan(app, gui, document, plan, controller):
    try:
        verify_plan(app, gui, document, plan, controller)
        nodes, measured = [], []
        recompute_verified = False

        def write(owned):
            nonlocal recompute_verified
            for name, item in zip(plan['names'], plan['request']['dimensions']):
                _verify(app, gui, document, plan, controller, owned, nodes)
                owned(before_write=True)
                dim = document.addObject('TechDraw::DrawViewDimension', name)
                nodes.append(dim)
                owned(require_pending=True)
                if dim.Name != name or dim.Document is not document or dim.TypeId != 'TechDraw::DrawViewDimension':
                    _fail('DIMENSION_CREATE_FAILED')
                for field, value in (('Type', item['kind']), ('MeasureType', 'Projected'), ('Arbitrary', False), ('References3D', []), ('References2D', [(plan['view'], (item['edge'],))]), ('FormatSpec', _format(item['kind']))):
                    owned()
                    setattr(dim, field, value)
                    owned(require_pending=True)
                owned()
                plan['page'].addView(dim)
                owned(require_pending=True)
                dim.X = item['x_mm']
                owned()
                dim.Y = item['y_mm']
                owned()
                _verify(app, gui, document, plan, controller, owned, nodes)
            try:
                count = document.recompute([plan['page'], *nodes], False, True)
            except Exception:
                owned()
                _fail('DIMENSION_RECOMPUTE_UNVERIFIED')
            owned()
            if type(count) is not int or not 0 <= count <= 9007199254740991:
                _fail('DIMENSION_RECOMPUTE_UNVERIFIED')
            recompute_verified = True
            _verify_created(app, gui, document, plan, controller, nodes, owned,
                            allow_template_cleaned=recompute_verified)

        def rollback():
            _verify(app, gui, document, plan, controller)

        def committed():
            measured[:] = _verify_created(app, gui, document, plan, controller, nodes,
                                          allow_template_cleaned=recompute_verified)
        controller._run_transaction(app, document, 'Phoenix: add projected drawing dimensions', write, rollback, committed)
        result = {'kind': 'drawing_dimensions', 'page': plan['page'].Name, 'view': plan['view'].Name, 'dimensions': [{'name': name, **item, 'value_mm': value} for name, item, value in zip(plan['names'], plan['request']['dimensions'], measured)], 'measurement_verification': 'native_reference_and_value_checked', 'projection_verification': 'not_checked', 'manual_review_required': True}
        return _sibling('pft_drawing_dimensions_contract').validate_result(result, plan['request'])
    except (PftDrawingDimensionsError, controller.PftWriteError):
        raise
    except Exception:
        _fail('DIMENSION_CREATE_FAILED')
