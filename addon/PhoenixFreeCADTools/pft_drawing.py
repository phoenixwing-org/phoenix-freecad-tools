# SPDX-License-Identifier: Apache-2.0
"""Finite, additive TechDraw drafts behind the native confirmation lifecycle.

Previews read cached native geometry and a fixed installed template only. The
write creates five nodes in one owned transaction, recomputes only those nodes,
and never pumps GUI events, waits for projection, exports, or saves a document.
Native callbacks are not isolated; source preservation and transaction closure
are checked, while rendered projection remains explicitly unverified.
"""
import hashlib
import math
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET

CAPABILITIES = {
    'version': 1, 'operation': 'create_drawing', 'confirmation': 'native_gui_only',
    'preview_method': 'prepare_part_changes', 'request_method': 'request_part_change_approval',
    'status_method': 'get_part_change_status', 'source_scope': 'single_native_solid', 'paper': 'A4_landscape',
    'projection': 'first_angle', 'views': ['front', 'top', 'axonometric'],
    'saves_document': False, 'projection_verification': 'not_checked',
}
_MESSAGES = {
    'DRAWING_UNSUPPORTED': '当前原生对象、缓存几何、文档状态或内置 A4 模板不在图纸草稿的支持范围内。',
    'DRAWING_PLAN_STALE': '文档、来源几何、Body Tip、内置模板或预定对象名在预览后发生变化，请重新预览。',
    'DRAWING_TEMPLATE_UNAVAILABLE': '当前安装未提供可验证的内置 A4 横向模板。',
    'DRAWING_CREATE_FAILED': '图纸节点、来源引用、布局或源对象保护未通过核对，请检查原生事务状态。',
    'DRAWING_RECOMPUTE_UNVERIFIED': '新建图纸节点的受限重算返回值无法确认；未验证投影，不会自动重试。',
    'DRAWING_PROJECTION_UNAVAILABLE': '原生精确投影引擎初始化未通过核对，未创建图纸节点。',
    'DRAWING_NATIVE_CONFIRMATION_REQUIRED': '图纸草稿创建只能由原生确认窗口的人工确认回调执行。',
    'DRAWING_RESULT_INVALID': '图纸草稿结果格式或内容未通过核对。',
}
_TEMPLATE_CHOICES = ('ISO/A4_Landscape_ISO5457_advanced.svg',
                     'A4_Landscape_ISO5457_advanced.svg',
                     'Default_Template_A4_Landscape.svg')
_SCALES = (1., .5, .2, .1, .05, .02, .01, .005, .002, .001)
_MAX_PARENT_DEPTH = 16
# Reserve the lower 80 mm for the installed A4 title block. Conservative
# view boxes stay within x=30..270, y=80..195, with a gap between projections.
_SPECS = (('front', (0., -1., 0.), (1., 0., 0.), 85., 160., 110., 60.),
          ('top', (0., 0., 1.), (1., 0., 0.), 85., 100., 110., 40.),
          ('axonometric', (1., -1., 1.), (1., 1., 0.), 215., 150., 110., 90.))


class PftDrawingError(RuntimeError):
    def __init__(self, code, message=None):
        self.code = code if code in _MESSAGES else 'DRAWING_UNSUPPORTED'
        self.message = _MESSAGES[self.code]
        super().__init__(self.message)


def _fail(code='DRAWING_UNSUPPORTED'):
    raise PftDrawingError(code)


def _number(value):
    value = value.Value if hasattr(value, 'Value') else value
    if type(value) not in (int, float) or not math.isfinite(value):
        _fail()
    return float(value)


def _sequence(value, maximum=256):
    if type(value) not in (list, tuple) or len(value) > maximum:
        _fail()
    return tuple(value)


def _name(value, maximum=64, document=False):
    return (type(value) is str and 0 < len(value) <= maximum
            and not any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in value)
            and (document or value.strip() == value and value not in ('.', '..')
                 and not any(c in value for c in '\\/:')))


def _placement_value(placement):
    quaternion = _sequence(placement.Rotation.Q, 4)
    if len(quaternion) != 4:
        _fail()
    return (tuple(_number(getattr(placement.Base, k)) for k in ('x', 'y', 'z')),
            tuple(_number(v) for v in quaternion))


def _placement(obj):
    return _placement_value(obj.Placement)


def _parent(obj):
    """Confirm the native geometric parent, not arbitrary incoming references."""
    reader = getattr(obj, 'getParentGeoFeatureGroup', None)
    if not callable(reader):
        _fail()
    parent = reader()
    candidates = []
    for incoming in _sequence(obj.InList, 10000):
        extension = getattr(incoming, 'hasExtension', None)
        if not callable(extension):
            _fail()
        geometric = extension('App::GeoFeatureGroupExtension')
        if type(geometric) is not bool:
            _fail()
        if geometric:
            members = _sequence(incoming.Group, 10000)
            if any(member is obj for member in members):
                candidates.append(incoming)
    if (len(candidates) != (0 if parent is None else 1)
            or parent is not None and candidates[0] is not parent):
        _fail()
    return parent


def _parent_context(document, source):
    """Freeze a bounded native App::Part chain and each exact membership."""
    current, seen, parents, states = source, {id(source)}, [], []
    for depth in range(_MAX_PARENT_DEPTH + 1):
        parent = _parent(current)
        if parent is None:
            break
        if depth == _MAX_PARENT_DEPTH or id(parent) in seen or parent.TypeId != 'App::Part':
            _fail()
        seen.add(id(parent))
        state = _native(parent, document)
        members = _sequence(parent.Group, 10000)
        if (len({id(member) for member in members}) != len(members)
                or sum(member is current for member in members) != 1
                or any(member.Document is not document or document.getObject(member.Name) is not member
                       for member in members)):
            _fail()
        global_reader = getattr(parent, 'getGlobalPlacement', None)
        if not callable(global_reader):
            _fail()
        parents.append((parent, current, members))
        states.append((state, _placement_value(global_reader())))
        current = parent
    global_reader = getattr(source, 'getGlobalPlacement', None)
    if not callable(global_reader):
        _fail()
    global_placement = global_reader()
    signature = _placement_value(global_placement)
    if not parents and signature != _placement(source):
        _fail()
    return tuple(parents), tuple(states), global_placement, signature


def _same_parents(current, expected):
    return (len(current) == len(expected) and all(
        parent is old_parent and child is old_child and len(members) == len(old_members)
        and all(member is old_member for member, old_member in zip(members, old_members))
        for (parent, child, members), (old_parent, old_child, old_members) in zip(current, expected)))


def _native(obj, document):
    if (obj is None or not _name(obj.Name)
            or obj.Document is not document or document.getObject(obj.Name) is not obj
            or type(obj.TypeId) is not str or obj.TypeId.endswith('Python')):
        _fail()
    properties = _sequence(obj.PropertiesList)
    if any(type(p) is not str for p in properties) or 'Proxy' in properties:
        _fail()
    try:
        getattr(obj, 'Proxy')
    except AttributeError:
        pass
    else:
        _fail()
    states = _sequence(obj.State, 16)
    if (not states or set(states) - {'Up-to-date', 'Expanded'}
            or 'Up-to-date' not in states or obj.isValid() is not True):
        _fail()
    return (obj.Name, obj.TypeId, tuple(states), _placement(obj))


def _shape(shape):
    if (shape.isNull() is not False or shape.isValid() is not True
            or len(_sequence(shape.Solids, 2)) != 1):
        _fail()
    bounds = tuple(_number(getattr(shape.BoundBox, k)) for k in
                   ('XMin', 'YMin', 'ZMin', 'XMax', 'YMax', 'ZMax'))
    spans = tuple(bounds[i + 3] - bounds[i] for i in range(3))
    volume, area = _number(shape.Volume), _number(shape.Area)
    if volume <= 0 or area <= 0 or any(v <= 0 or not math.isfinite(v) for v in spans):
        _fail()
    return (volume, area, bounds, spans)


def _context(app, gui, document, controller, owned=None, written=False):
    controller._ensure_main_thread()
    if (app.ActiveDocument is not document or app.getDocument(document.Name) is not document
            or not _name(document.Name, 256, True)
            or document.RecomputesFrozen is not False):
        _fail()
    if owned is None:
        controller._ensure_idle(app, gui, document)
    else:
        owned()
        # FreeCAD allocates the document undo record lazily: a positive
        # application transaction ID can be owned before the first addObject.
        # Once any draft node exists, the undo record must remain pending.
        pending = document.HasPendingTransaction
        if (gui.getDocument(document.Name).getInEdit() is not None
                or type(pending) is not bool or written and not pending
                or type(document.UndoMode) is not int or document.UndoMode <= 0):
            _fail()
        owned()


def _registry(document, created=(), allowed_touched=()):
    objects = _sequence(document.Objects, 10000)
    if (type(allowed_touched) is not tuple or len(allowed_touched) > 8
            or len({id(obj) for obj in allowed_touched}) != len(allowed_touched)):
        _fail()
    for obj in allowed_touched:
        if (obj is None or type(obj.TypeId) is not str or obj.TypeId != 'TechDraw::DrawSVGTemplate'
                or obj.Document is not document or not _name(obj.Name)
                or document.getObject(obj.Name) is not obj or not any(obj is member for member in objects)
                or obj.isValid() is not True):
            _fail()
        properties = _sequence(obj.PropertiesList)
        if any(type(value) is not str for value in properties) or 'Proxy' in properties:
            _fail()
        owners = _sequence(obj.InList, 8)
        if (len(owners) != 1 or type(owners[0].TypeId) is not str
                or owners[0].TypeId != 'TechDraw::DrawPage' or owners[0].Document is not document
                or not _name(owners[0].Name) or document.getObject(owners[0].Name) is not owners[0]
                or not any(owners[0] is member for member in objects) or owners[0].Template is not obj
                or _sequence(obj.ExpressionEngine, 100)):
            _fail()
        try:
            getattr(obj, 'Proxy')
        except AttributeError:
            pass
        else:
            _fail()
    names = []
    for obj in objects:
        if (type(obj.Name) is not str or obj.Document is not document
                or document.getObject(obj.Name) is not obj):
            _fail()
        states = _sequence(obj.State, 16)
        template_touched = any(obj is allowed for allowed in allowed_touched)
        if (any(type(v) is not str for v in states)
                or template_touched and (not states or set(states) - {'Up-to-date', 'Expanded', 'Touched'})
                or not any(obj is new for new in created)
                and any(v in states for v in ('Invalid', 'Error', 'Recompute')
                        + (() if template_touched else ('Touched',)))):
            _fail()
        names.append(obj.Name)
    if len(set(names)) != len(names):
        _fail()
    return objects


def _template(app):
    root = (Path(app.getResourceDir()) / 'Mod' / 'TechDraw' / 'Templates').resolve()
    for relative in _TEMPLATE_CHOICES:
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            continue
        if not 0 < candidate.stat().st_size <= 262144:
            _fail('DRAWING_TEMPLATE_UNAVAILABLE')
        data = candidate.read_bytes()
        if not 0 < len(data) <= 262144:
            _fail('DRAWING_TEMPLATE_UNAVAILABLE')
        try:
            svg = ET.fromstring(data)
        except ET.ParseError:
            _fail('DRAWING_TEMPLATE_UNAVAILABLE')
        def millimetres(field):
            match = re.fullmatch(r'\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?\s*', svg.attrib.get(field, ''))
            return float(match.group(1)) if match else None
        if (svg.tag != '{http://www.w3.org/2000/svg}svg'
                or millimetres('width') != 297. or millimetres('height') != 210.):
            _fail('DRAWING_TEMPLATE_UNAVAILABLE')
        return candidate, hashlib.sha256(data).hexdigest()
    _fail('DRAWING_TEMPLATE_UNAVAILABLE')


def _capture(app, gui, document, source, controller, owned=None, created=(), allowed_touched=()):
    _context(app, gui, document, controller, owned, bool(created))
    objects = _registry(document, created, allowed_touched)
    state = _native(source, document)
    if source.TypeId not in ('Part::Feature', 'PartDesign::Body'):
        _fail()
    shape = source.Shape
    geometry = _shape(shape)
    tip, tip_state, tip_shape, tip_geometry = None, None, None, None
    if source.TypeId == 'PartDesign::Body':
        tip = source.Tip
        tip_state = _native(tip, document)
        if (tip is source or not tip.TypeId.startswith(('PartDesign::', 'Part::'))
                or not any(member is tip for member in _sequence(source.Group, 10000))):
            _fail()
        tip_shape, tip_geometry = tip.Shape, _shape(tip.Shape)
    parents, parent_states, global_placement, global_state = _parent_context(document, source)
    projection_geometry = geometry
    if parents:
        # Native Part::Feature Shape already carries its local Placement.
        # TechDraw replaces that placement with globalPlacement(), rather than
        # multiplying it a second time. Work only on an independent Shape copy.
        located = shape.copy()
        if located is shape:
            _fail()
        located.Placement = global_placement
        projection_geometry = _shape(located)
    _context(app, gui, document, controller, owned, bool(created))
    return (objects, (state, geometry, tip_state, tip_geometry, global_state, parent_states, projection_geometry),
            (shape, tip_shape), tip, parents)


def _names(source):
    base = 'PhoenixDrawing_' + hashlib.sha256(source.Name.encode('utf-8')).hexdigest()[:16]
    return (base + '_Page', base + '_Template', base + '_Front', base + '_Top', base + '_Iso')


def _scale(geometry):
    x, y, z = geometry[3]
    spans = ((x, z), (x, y), ((x + y) / math.sqrt(2), (x + y + 2 * z) / math.sqrt(6)))
    for value in _SCALES:
        if all(w * value <= spec[5] and h * value <= spec[6] for (w, h), spec in zip(spans, _SPECS)):
            return value
    _fail()


def prepare_plan(app, gui, document, source, controller):
    try:
        objects, snapshot, shapes, tip, parents = _capture(app, gui, document, source, controller)
        template, digest = _template(app)
        names = _names(source)
        if any(document.getObject(name) is not None for name in names):
            _fail()
        scale = _scale(snapshot[6])
        plan = {'document': document, 'source': source, 'tip': tip, 'objects': objects,
                'snapshot': snapshot, 'shapes': shapes, 'parents': parents, 'template_path': template,
                'template_digest': digest, 'names': names, 'scale': scale,
                'request': {'source': source.Name, 'page': names[0]}}
        descriptions = ('A4 横向图纸草稿；第一角投影', '安装内置 A4 横向模板',
                        '主视图', '俯视图（位于主视图下方）', '轴测图')
        rows = [{'object': name, 'field': 'TechDraw draft node', 'old': None,
                 'new': text, 'status': 'change', 'reason': '',
                 'source': 'cached_native_typed_snapshot',
                 'note': ('来源 %s；比例 %g；确认后只创建图纸节点并受限重算；'
                          '无尺寸、无导出、不保存；投影尚未核验，须人工检查排版和内容。') % (source.Name, scale)}
                for name, text in zip(names, descriptions)]
        for row, spec in zip(rows[2:], _SPECS):
            row['note'] += ' 视图中心 X=%g mm，Y=%g mm；下部预留标题栏。' % (spec[3], spec[4])
        verify_plan(app, gui, document, plan, controller)
        return plan, rows
    except PftDrawingError:
        raise
    except Exception:
        _fail()


def _verify(app, gui, document, plan, controller, owned=None, created=()):
    if plan['document'] is not document or plan['names'] != _names(plan['source']):
        _fail('DRAWING_PLAN_STALE')
    objects, snapshot, shapes, tip, parents = _capture(app, gui, document, plan['source'], controller, owned, created)
    expected = plan['objects'] + tuple(created)
    if (len(objects) != len(expected) or {id(v) for v in objects} != {id(v) for v in expected}
            or snapshot != plan['snapshot'] or tip is not plan['tip'] or not _same_parents(parents, plan['parents'])
            or shapes[0].isSame(plan['shapes'][0]) is not True
            or tip is not None and shapes[1].isSame(plan['shapes'][1]) is not True):
        _fail('DRAWING_PLAN_STALE')
    if _template(app) != (plan['template_path'], plan['template_digest']):
        _fail('DRAWING_PLAN_STALE')
    if plan['scale'] != _scale(snapshot[6]):
        _fail('DRAWING_PLAN_STALE')
    for index, name in enumerate(plan['names']):
        if document.getObject(name) is not (created[index] if index < len(created) else None):
            _fail('DRAWING_PLAN_STALE')


def verify_plan(app, gui, document, plan, controller):
    try:
        _verify(app, gui, document, plan, controller)
    except PftDrawingError as error:
        if error.code == 'DRAWING_PLAN_STALE':
            raise
        _fail('DRAWING_PLAN_STALE')
    except Exception:
        _fail('DRAWING_PLAN_STALE')


def _result(plan):
    names = plan['names']
    return {'kind': 'drawing_draft', 'page': names[0], 'template': names[1],
            'source': plan['source'].Name,
            'views': [{'name': name, 'kind': spec[0]} for name, spec in zip(names[2:], _SPECS)],
            'scale': plan['scale'], 'projection': 'first_angle', 'paper': 'A4_landscape',
            'projection_verification': 'not_checked', 'manual_review_required': True}


def _verify_created(app, gui, document, plan, controller, nodes, owned=None):
    _verify(app, gui, document, plan, controller, owned, nodes)
    page, template, *views = nodes
    if (page.TypeId != 'TechDraw::DrawPage' or template.TypeId != 'TechDraw::DrawSVGTemplate'
            or page.Template is not template or template.Template != str(plan['template_path'])
            or page.ProjectionType != 'First angle'
            or tuple(page.Views) != tuple(views) or _number(page.Scale) != plan['scale']
            or any(type(v) is not str or v != '' for v in template.EditableTexts.values())
            or any(any(state in ('Invalid', 'Error') for state in _sequence(node.State, 16)) for node in nodes)):
        _fail('DRAWING_CREATE_FAILED')
    for view, spec in zip(views, _SPECS):
        if (view.TypeId != 'TechDraw::DrawViewPart' or tuple(view.Source) != (plan['source'],)
                or tuple(_number(getattr(view.Direction, k)) for k in ('x', 'y', 'z')) != spec[1]
                or tuple(_number(getattr(view.XDirection, k)) for k in ('x', 'y', 'z')) != spec[2]
                or view.ScaleType != 'Custom' or _number(view.Scale) != plan['scale'] or view.CoarseView is not False
                or _number(view.X) != spec[3] or _number(view.Y) != spec[4]):
            _fail('DRAWING_CREATE_FAILED')


_projection_primed = False


def _prime_projection_engine(app):
    """Initialize exact HLR serially before starting three native view jobs.

    OCCT 7.8/7.9 BRepLib::Plane lazily initializes a shared handle without a
    lock. A fixed in-memory cube exercises that path on the GUI thread first.
    This neither changes document objects nor disables precise curved edges.
    It does not isolate unrelated native jobs or prove the final page layout.
    """
    global _projection_primed
    if _projection_primed:
        return
    try:
        import Part
        import TechDraw
        projected = TechDraw.project(Part.makeBox(1., 1., 1.), app.Vector(0., 0., 1.))
        if (type(projected) not in (list, tuple) or len(projected) != 4
                or projected[0].isNull() is not False or len(projected[0].Edges) != 4):
            raise ValueError()
    except Exception:
        _fail('DRAWING_PROJECTION_UNAVAILABLE')
    _projection_primed = True


def apply_plan(app, gui, document, plan, controller):
    try:
        verify_plan(app, gui, document, plan, controller)
        _prime_projection_engine(app)
        verify_plan(app, gui, document, plan, controller)
        nodes = []
        def write(owned):
            _verify(app, gui, document, plan, controller, owned)
            for kind, name in zip(('TechDraw::DrawPage', 'TechDraw::DrawSVGTemplate',
                                   'TechDraw::DrawViewPart', 'TechDraw::DrawViewPart',
                                   'TechDraw::DrawViewPart'), plan['names']):
                owned()
                if document.getObject(name) is not None:
                    _fail('DRAWING_PLAN_STALE')
                owned(before_write=True)
                obj = document.addObject(kind, name)
                owned(require_pending=True)
                if obj.Name != name or obj.TypeId != kind or obj.Document is not document:
                    _fail('DRAWING_CREATE_FAILED')
                nodes.append(obj)
                _verify(app, gui, document, plan, controller, owned, nodes)
            page, template, *views = nodes
            owned()
            template.Template = str(plan['template_path'])
            owned()
            texts = dict(template.EditableTexts)
            template.EditableTexts = {key: '' for key in texts}
            owned()
            page.Template = template
            owned()
            page.Scale = plan['scale']
            owned()
            page.ProjectionType = 'First angle'
            owned()
            for view, spec in zip(views, _SPECS):
                owned()
                view.Source = [plan['source']]
                owned()
                view.CoarseView = False
                owned()
                view.Direction = app.Vector(*spec[1])
                owned()
                view.XDirection = app.Vector(*spec[2])
                owned()
                view.ScaleType = 'Custom'
                owned()
                view.Scale = plan['scale']
                owned()
                page.addView(view)
                owned()
                view.X = spec[3]
                owned()
                view.Y = spec[4]
                owned()
            _verify_created(app, gui, document, plan, controller, nodes, owned)
            owned()
            try:
                count = document.recompute(list(nodes), False, True)
            except Exception:
                owned()
                _fail('DRAWING_RECOMPUTE_UNVERIFIED')
            owned()
            if type(count) is not int or not 0 <= count <= 9007199254740991:
                _fail('DRAWING_RECOMPUTE_UNVERIFIED')
            # No GUI activation or event pumping. Native asynchronous HLR may
            # still be pending; restore/check positions without claiming pixels.
            for view, spec in zip(views, _SPECS):
                owned()
                if _number(view.X) != spec[3]:
                    view.X = spec[3]
                owned()
                if _number(view.Y) != spec[4]:
                    view.Y = spec[4]
            _verify_created(app, gui, document, plan, controller, nodes, owned)
            owned()
        def rollback():
            _verify(app, gui, document, plan, controller)
        def committed():
            _verify_created(app, gui, document, plan, controller, nodes)
        controller._run_transaction(app, document, 'Phoenix: create A4 drawing draft', write, rollback, committed)
        return _result(plan)
    except (PftDrawingError, controller.PftWriteError):
        raise
    except Exception:
        _fail('DRAWING_CREATE_FAILED')
