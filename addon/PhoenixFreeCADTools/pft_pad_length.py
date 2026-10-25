# SPDX-License-Identifier: Apache-2.0
"""Finite first-Pad Length edits; all preview observations are cached reads.

Initial scope: unattached Sketch and identity Body/Sketch/Pad placements, +Z
extrusion. No Content, XML, property dump, extension enumeration, shape creation,
solve, event processing, selection, save, or isolation of unknown observers.
"""
import importlib.util
import math
from pathlib import Path
import sys
import unicodedata

CAPABILITIES = {
    'version': 1, 'operation': 'set_pad_length', 'confirmation': 'native_gui_only',
    'preview_method': 'prepare_part_changes', 'request_method': 'request_part_change_approval',
    'status_method': 'get_part_change_status', 'length_unit': 'mm', 'minimum_mm': 0.01,
    'maximum_mm': 10000, 'model_scope': 'single_document_first_pad',
    'max_open_documents': 1, 'saves_document': False,
}
_MESSAGES = {
    'PAD_UNSUPPORTED': '当前模型、参数或原生读取状态不在首个 Pad 长度修改的有限支持范围内。',
    'PAD_PLAN_STALE': '文档、草图、依赖、参数或可写状态在预览后发生变化，请重新预览。',
    'PAD_PARAMETER_VERIFY_FAILED': '原生参数或关联配置与本次预览不一致，本次修改未通过反验。',
    'PAD_GEOMETRY_VERIFY_FAILED': 'Pad 或 Body 的原生单实体几何、尺寸或长度体积比例未通过验证。',
    'PAD_RECOMPUTE_UNVERIFIED': '原生重算计数、对象完成状态或上下文无法确认，不会自动重试。',
    'PAD_NATIVE_CONFIRMATION_REQUIRED': 'Pad 长度修改只能由原生确认窗口的人工确认回调执行。',
}
_TYPES = frozenset(('PartDesign::Body', 'PartDesign::Pad', 'Sketcher::SketchObject',
                    'App::Origin', 'App::Line', 'App::Plane', 'App::Point'))
_CONSTRAINTS = frozenset(('Coincident', 'Horizontal', 'Vertical', 'Parallel', 'Tangent',
    'Distance', 'DistanceX', 'DistanceY', 'Angle', 'Perpendicular', 'Radius', 'Equal',
    'PointOnObject', 'Symmetric', 'Block', 'Diameter'))
_STATUS_NAMES = frozenset(('Immutable', 'ReadOnly', 'Hidden', 'Transient', 'MaterialEdit',
    'NoMaterialListEdit', 'Output', 'LockDynamic', 'NoModify', 'PartialTrigger',
    'NoRecompute', 'CopyOnChange', 'UserEdit', 'AllowPartial'))
_STATUS_BITS = frozenset((12, 13, 14, 15, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31))
_IDENTITY = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))
_LENGTH = (1, 0, 0, 0, 0, 0, 0, 0)
_ANGLE = (0, 0, 0, 0, 0, 0, 0, 1)


class PftPadLengthError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in _MESSAGES else 'PAD_UNSUPPORTED'
        super().__init__(_MESSAGES[self.code])


def _fail(code='PAD_UNSUPPORTED'):
    raise PftPadLengthError(code)


def _number(value, positive=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or positive and value <= 0):
        _fail()
    return float(value)


def _text(value, maximum=512, name=False):
    if (type(value) is not str or len(value) > maximum
            or any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in value)
            or name and (not value or value.strip() != value or value in ('.', '..')
                         or any(c in value for c in '\\/:'))):
        _fail()
    return value


def _seq(value, maximum):
    if type(value) not in (list, tuple) or len(value) > maximum:
        _fail()
    return value


def _bool(value):
    if type(value) is not bool:
        _fail()
    return value


def _vector(value):
    return tuple(_number(getattr(value, k)) for k in ('x', 'y', 'z'))


def _rotation(value):
    q = _seq(value.Q, 4)
    if len(q) != 4:
        _fail()
    return tuple(_number(v) for v in q)


def _placement(value):
    return (_vector(value.Base), _rotation(value.Rotation))


def _quantity(value, signature):
    powers = _seq(value.Unit.Signature, 8)
    if len(powers) != 8 or any(type(v) is not int for v in powers) or tuple(powers) != signature:
        _fail()
    return _number(value.Value)


def _empty_link(value):
    return (value is None or type(value) in (list, tuple)
            and (not value or len(value) == 2 and value[0] is None
                 and type(value[1]) in (list, tuple) and not value[1]))


def _ref(value, registry):
    if value.Document is not registry['document']:
        _fail()
    name = _text(value.Name, 64, True)
    if registry['by_name'].get(name) is not value:
        _fail()
    return name


def _refs(values, registry):
    # Native edges are per-property: Group and Tip can both reference Pad.
    # Preserve order and multiplicity in the stale-plan snapshot.
    return tuple(_ref(v, registry) for v in _seq(values, 256))


def _context(app, gui, document, controller, owned=None):
    controller._ensure_main_thread()
    if owned is None:
        controller._ensure_idle(app, gui, document)
    else:
        owned()
        if (app.ActiveDocument is not document or gui.getDocument(document.Name).getInEdit() is not None
                or document.HasPendingTransaction is not True or type(document.UndoMode) is not int
                or document.UndoMode <= 0):
            _fail()
    opened = app.listDocuments()
    if (type(opened) is not dict or len(opened) != 1 or opened.get(document.Name) is not document
            or app.getDocument(document.Name) is not document or app.ActiveDocument is not document
            or document.RecomputesFrozen is not False):
        _fail()
    if owned is not None:
        owned()


def _registry(document, body_name, pad):
    objects = _seq(document.Objects, 64)
    if not objects:
        _fail()
    by_name = {}
    for obj in objects:
        name = _text(obj.Name, 64, True)
        if (name in by_name or obj.Document is not document or document.getObject(name) is not obj
                or type(obj.TypeId) is not str or obj.TypeId not in _TYPES):
            _fail()
        by_name[name] = obj
        props = _seq(obj.PropertiesList, 256)
        if (any(type(v) is not str for v in props) or len(set(props)) != len(props) or 'Proxy' in props
                or any(v.startswith(('BaseAttacher', 'BaseMap')) or v == 'BaseAttachment' for v in props)):
            _fail()
        try:
            getattr(obj, 'Proxy')
        except AttributeError:
            pass
        else:
            _fail()
        if _seq(obj.ExpressionEngine, 200):
            _fail()
    body = by_name.get(_text(body_name, 64, True))
    if (body is None or body.TypeId != 'PartDesign::Body' or by_name.get(pad.Name) is not pad
            or pad.TypeId != 'PartDesign::Pad'):
        _fail()
    group = _seq(body.Group, 2)
    if len(group) != 2 or group[1] is not pad or group[0].TypeId != 'Sketcher::SketchObject' or body.Tip is not pad:
        _fail()
    sketch, origin = group[0], body.Origin
    if origin.TypeId != 'App::Origin' or by_name.get(origin.Name) is not origin:
        _fail()
    features = _seq(origin.OriginFeatures, 7)
    if len(features) != 7 or sorted(v.TypeId for v in features) != ['App::Line'] * 3 + ['App::Plane'] * 3 + ['App::Point']:
        _fail()
    expected = (body, sketch, pad, origin, *features)
    if (len(objects) != len(expected) or len({id(v) for v in expected}) != len(expected)
            or {id(v) for v in objects} != {id(v) for v in expected}):
        _fail()
    if pad._Body is not body or not _empty_link(pad.BaseFeature) or not _empty_link(body.BaseFeature):
        _fail()
    return {'document': document, 'objects': tuple(objects), 'by_name': by_name,
            'body': body, 'sketch': sketch, 'pad': pad, 'origin': origin}


def _graph(registry):
    body, sketch, pad, origin = (registry[k] for k in ('body', 'sketch', 'pad', 'origin'))
    features = tuple(origin.OriginFeatures)
    allowed = {id(body): {id(sketch), id(pad), id(origin)}, id(sketch): set(),
               id(pad): {id(sketch), id(body)}, id(origin): {id(v) for v in features}}
    allowed.update({id(v): set() for v in features})
    incoming_allowed = {id(v): {source for source, targets in allowed.items() if id(v) in targets}
                        for v in registry['objects']}
    result = []
    for obj in registry['objects']:
        out, incoming = _refs(obj.OutList, registry), _refs(obj.InList, registry)
        if (any(id(registry['by_name'][name]) not in allowed[id(obj)] for name in out)
                or any(id(registry['by_name'][name]) not in incoming_allowed[id(obj)] for name in incoming)):
            _fail()
        # Hidden/output links may have different native graph flags. Both lists
        # are independently closed and bound; no guessed reciprocal equality.
        result.append((obj.Name, obj.TypeId, _text(obj.Label), tuple(obj.PropertiesList), out, incoming))
    return tuple(result)


def _writable_length(pad):
    if pad.getTypeIdOfProperty('Length') != 'App::PropertyLength' or 'Length' not in pad.PropertiesList:
        _fail()
    statuses, modes = pad.getPropertyStatus('Length'), pad.getEditorMode('Length')
    if (type(statuses) is not list or len(statuses) > 64
            or any(not (type(v) is str and v in _STATUS_NAMES or type(v) is int and v in _STATUS_BITS) for v in statuses)
            or len(set(statuses)) != len(statuses) or type(modes) is not list or len(modes) > 2
            or any(type(v) is not str or v not in ('ReadOnly', 'Hidden') for v in modes)
            or len(set(modes)) != len(modes) or 24 in statuses
            or any(v in statuses for v in ('ReadOnly', 'Immutable', 'LockDynamic'))
            or 'ReadOnly' in modes or _seq(pad.ExpressionEngine, 200)):
        _fail()
    return (tuple(statuses), tuple(modes))


def _circle(geometry):
    if geometry.TypeId != 'Part::GeomCircle':
        _fail()
    return (tuple(_vector(getattr(geometry, k)) for k in ('Location', 'Axis', 'XAxis', 'YAxis')),
            _number(geometry.AngleXU), _number(geometry.Radius, True), _rotation(geometry.Rotation))


def _geometry(sketch):
    geometries = _seq(sketch.Geometry, 100)
    if type(sketch.GeometryCount) is not int or sketch.GeometryCount != len(geometries) or not geometries:
        _fail()
    result = []
    for index, geometry in enumerate(geometries):
        # Check before getConstruction's facade can lazily create an extension.
        if geometry.hasExtensionOfType('Sketcher::SketchGeometryExtension') is not True:
            _fail()
        construction = _bool(sketch.getConstruction(index))
        kind = geometry.TypeId
        parameters = (_number(geometry.FirstParameter), _number(geometry.LastParameter))
        if parameters[0] >= parameters[1]:
            _fail()
        if kind == 'Part::GeomLineSegment':
            data = (_vector(geometry.StartPoint), _vector(geometry.EndPoint))
        elif kind == 'Part::GeomCircle':
            data = _circle(geometry)
        elif kind == 'Part::GeomArcOfCircle':
            # Arc is a trimmed curve, not a conic: no arc.Rotation read.
            data = _circle(geometry.Circle)
        else:
            _fail()
        result.append((kind, data, parameters, construction))
    constraints = []
    for constraint in _seq(sketch.Constraints, 200):
        kind = constraint.Type
        if type(kind) is not str or kind not in _CONSTRAINTS:
            _fail()
        indices = []
        for field in ('First', 'FirstPos', 'Second', 'SecondPos', 'Third', 'ThirdPos'):
            value = getattr(constraint, field)
            if type(value) is not int or (field.endswith('Pos') and not 0 <= value <= 3):
                _fail()
            if not field.endswith('Pos') and value not in (-2000, -1, -2) and not 0 <= value < len(geometries):
                _fail()
            indices.append(value)
        constraints.append((kind, tuple(indices), _number(constraint.Value), _text(constraint.Name),
            _bool(constraint.Driving), _bool(constraint.InVirtualSpace), _bool(constraint.IsActive),
            _number(constraint.LabelDistance), _number(constraint.LabelPosition)))
    return (tuple(result), tuple(constraints))


def _material(material):
    # Public typed values; excludes library paths, persistence and native dumps.
    strings = tuple(_text(getattr(material, k), 4096) for k in
                    ('UUID', 'Name', 'Description', 'URL', 'Reference', 'Parent', 'Author', 'License'))
    lists = tuple(tuple(_text(v, 512) for v in _seq(getattr(material, k), 100))
                  for k in ('PhysicalModels', 'AppearanceModels', 'Tags'))
    maps = []
    for field in ('PhysicalProperties', 'AppearanceProperties', 'LegacyProperties'):
        values = getattr(material, field)
        if type(values) is not dict or len(values) > 200:
            _fail()
        maps.append(tuple(sorted((_text(k, 512), _text(v, 4096)) for k, v in values.items())))
    return (strings, lists, tuple(maps))


def _attachment(sketch):
    # The standard engine enum/class relationship is defined by AttachExtension.
    # BaseAttacher*/BaseAttachment/BaseMap* dynamic properties are rejected by
    # the registry, before a getter could materialize base attachment state.
    engines = {'Attacher::AttachEngine3D': 'Engine 3D',
               'Attacher::AttachEnginePlane': 'Engine Plane',
               'Attacher::AttachEngineLine': 'Engine Line',
               'Attacher::AttachEnginePoint': 'Engine Point'}
    engine_type = _text(sketch.AttacherType)
    engine = _text(sketch.AttacherEngine)
    if engines.get(engine_type) != engine:
        _fail()
    if sketch.MapMode != 'Deactivated' or not _empty_link(sketch.AttachmentSupport):
        _fail()
    return (engine_type, engine, _bool(sketch.MapReversed),
            _number(sketch.MapPathParameter), _placement(sketch.AttachmentOffset))


def _configuration(registry):
    body, sketch, pad = (registry[k] for k in ('body', 'sketch', 'pad'))
    placements = tuple(_placement(v.Placement) for v in (body, sketch, pad))
    if any(v != _IDENTITY for v in placements) or _vector(pad.Direction) != (0.0, 0.0, 1.0):
        _fail()
    attachment = _attachment(sketch)
    if any(_seq(getattr(sketch, k), 100) for k in ('ExternalGeometry', 'ExternalTypes', 'Exports')):
        _fail()
    profile = pad.Profile
    if (type(profile) not in (tuple, list) or len(profile) != 2 or profile[0] is not sketch
            or type(profile[1]) not in (tuple, list) or profile[1]):
        _fail()
    strings = (pad.Type, pad.Type2, pad.SideType)
    if strings != ('Length', 'Length', 'One side'):
        _fail()
    flags = tuple(_bool(getattr(pad, k)) for k in
                  ('UseCustomVector', 'AlongSketchNormal', 'Reversed', 'Midplane', 'AllowMultiFace', 'Suppressed', 'Refine'))
    if flags[0] or not flags[1] or flags[2] or flags[3] or flags[5]:
        _fail()
    if any(not _empty_link(getattr(pad, k)) for k in ('ReferenceAxis', 'UpToFace', 'UpToFace2', 'UpToShape', 'UpToShape2')):
        _fail()
    zeros = tuple(_quantity(getattr(pad, k), _LENGTH) for k in ('Offset', 'Offset2'))
    zeros += tuple(_quantity(getattr(pad, k), _ANGLE) for k in ('TaperAngle', 'TaperAngle2'))
    if any(v != 0 for v in zeros):
        _fail()
    materials = tuple(_material(v.ShapeMaterial) for v in (body, pad))
    # Same UUID prevents copyMaterial's native setter branch; it does not by
    # itself prove complete material equality. Bind public material fields too.
    if materials[0][0][0] != materials[1][0][0] or pad.SuppressedShape.isNull() is not True:
        _fail()
    return (placements, strings, flags, zeros, _quantity(pad.Length2, _LENGTH),
            materials, _geometry(sketch), _writable_length(pad), _bool(body.AllowCompound), attachment)


def _completion(registry):
    for obj in registry['objects']:
        states = _seq(obj.State, 9)
        if (not states or any(type(v) is not str for v in states) or len(set(states)) != len(states)
                or set(states) - {'Up-to-date', 'Expanded'} or 'Up-to-date' not in states or obj.isValid() is not True):
            _fail('PAD_RECOMPUTE_UNVERIFIED')


def _shape(shape):
    if shape.isNull() is not False or shape.isValid() is not True or len(_seq(shape.Solids, 2)) != 1:
        _fail('PAD_GEOMETRY_VERIFY_FAILED')
    volume = _number(shape.Volume, True)
    bounds = tuple(_number(getattr(shape.BoundBox, k)) for k in ('XMin', 'YMin', 'ZMin', 'XMax', 'YMax', 'ZMax'))
    dimensions = tuple(bounds[i + 3] - bounds[i] for i in range(3))
    if any(v <= 0 or not math.isfinite(v) for v in dimensions):
        _fail('PAD_GEOMETRY_VERIFY_FAILED')
    return (volume, bounds, dimensions)


def _capture(app, gui, document, body_name, pad, controller, owned=None, complete=True):
    _context(app, gui, document, controller, owned)
    registry = _registry(document, body_name, pad)
    graph, configuration = _graph(registry), _configuration(registry)
    length = _quantity(pad.Length, _LENGTH)
    if not 0.01 <= length <= 10000:
        _fail()
    if complete:
        _completion(registry)
    shapes = tuple(v.Shape for v in (pad, registry['body']))
    geometry = tuple(_shape(v) for v in shapes)
    _context(app, gui, document, controller, owned)
    fresh = _registry(document, body_name, pad)
    if (len(registry['objects']) != len(fresh['objects'])
            or any(a is not b for a, b in zip(registry['objects'], fresh['objects'])) or graph != _graph(fresh)):
        _fail()
    return registry, (graph, configuration, length, geometry), shapes


def _same_shapes(current, previous):
    return all(now.isSame(before) is True for now, before in zip(current, previous))


def prepare_plan(app, gui, document, body_name, pad, length_mm, controller):
    try:
        requested = _number(length_mm)
        if not 0.01 <= requested <= 10000:
            _fail()
        registry, snapshot, shapes = _capture(app, gui, document, body_name, pad, controller)
        if (snapshot[3][0] != snapshot[3][1]
                or not math.isclose(snapshot[3][0][2][2], snapshot[2], rel_tol=1e-8, abs_tol=1e-7)):
            _fail('PAD_GEOMETRY_VERIFY_FAILED')
        plan = {'document': document, 'body_name': body_name, 'body': registry['body'], 'pad': pad,
                'objects': registry['objects'], 'snapshot': snapshot, 'shapes': shapes, 'length_mm': requested}
        row = {'object': pad.Name, 'field': 'Length (mm)', 'old': snapshot[2], 'new': requested,
               'status': 'unchanged' if requested == snapshot[2] else 'change', 'reason': '',
               'source': 'cached_native_typed_snapshot',
               'note': ('首个且最终 Pad；%d 个闭图原生对象；仅线段、圆和圆弧的 typed 草图；'
                        '未附着草图、标准一致附着引擎（附着配置完整绑定），无动态 Base 附着；'
                        'Body/Sketch/Pad 为 identity Placement，方向 +Z；'
                        '确认后一次长度 setter、一次局部重算，反验单实体与长度体积比例；'
                        '不隔离未知原生回调，不自动保存。') % len(registry['objects'])}
        return plan, [row]
    except PftPadLengthError:
        raise
    except Exception:
        _fail()


def _verify(app, gui, document, plan, controller, owned=None, expected_length=None, complete=True):
    if plan['document'] is not document:
        _fail('PAD_PLAN_STALE')
    registry, current, shapes = _capture(app, gui, document, plan['body_name'], plan['pad'], controller, owned, complete)
    if (registry['body'] is not plan['body'] or len(registry['objects']) != len(plan['objects'])
            or any(a is not b for a, b in zip(registry['objects'], plan['objects']))):
        _fail('PAD_PLAN_STALE')
    before = plan['snapshot']
    if current[0:2] != before[0:2] or current[2] != (before[2] if expected_length is None else expected_length):
        _fail('PAD_PLAN_STALE' if expected_length is None else 'PAD_PARAMETER_VERIFY_FAILED')
    if expected_length is None and (current[3] != before[3] or not _same_shapes(shapes, plan['shapes'])):
        _fail('PAD_PLAN_STALE')
    return registry, current, shapes


def verify_plan(app, gui, document, plan, controller):
    try:
        _verify(app, gui, document, plan, controller)
    except PftPadLengthError:
        raise
    except Exception:
        _fail('PAD_PLAN_STALE')


def _result_contract(value, expected):
    """Load the pure validator from this exact addon, never a second addon cache."""
    name = 'pft_pad_contract'
    filename = Path(__file__).resolve().with_name(name + '.py')
    module = sys.modules.get(name)
    if module is not None:
        if Path(getattr(module, '__file__', '')).resolve() != filename:
            _fail('PAD_PARAMETER_VERIFY_FAILED')
    else:
        spec = importlib.util.spec_from_file_location(name, filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            if sys.modules.get(name) is module:
                sys.modules.pop(name, None)
            _fail('PAD_PARAMETER_VERIFY_FAILED')
    try:
        return module.validate_design_result(value, expected)
    except Exception:
        _fail('PAD_PARAMETER_VERIFY_FAILED')


def _verified_result(plan, current, shapes, count):
    if type(count) is not int or not 0 <= count <= 9007199254740991:
        _fail('PAD_RECOMPUTE_UNVERIFIED')
    ratio = plan['length_mm'] / plan['snapshot'][2]
    for index, (observed, original) in enumerate(zip(current[3], plan['snapshot'][3])):
        expected_bounds = list(original[1])
        expected_bounds[5] += plan['length_mm'] - plan['snapshot'][2]
        if (not math.isclose(observed[0], original[0] * ratio, rel_tol=1e-8, abs_tol=1e-7)
                or any(not math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-7)
                       for a, b in zip(observed[1], expected_bounds))
                or shapes[index].isSame(plan['shapes'][index]) is not False):
            _fail('PAD_GEOMETRY_VERIFY_FAILED')
    if current[3][0] != current[3][1]:
        _fail('PAD_GEOMETRY_VERIFY_FAILED')
    def public(value):
        return {'volume_mm3': value[0], 'dimensions_mm': list(value[2]), 'solids': 1}
    payload = {'kind': 'pad_length', 'body': plan['body_name'], 'pad': plan['pad'].Name,
            'length_mm': plan['length_mm'], 'recompute_count': count, 'checked_objects': len(plan['objects']),
            'geometry': {'pad': public(current[3][0]), 'body': public(current[3][1])},
            'validation': 'native_valid_one_solid_and_length_scaled_volume'}
    return _result_contract(payload, {'body': plan['body_name'], 'pad': plan['pad'].Name,
                                      'length_mm': plan['length_mm']})


def _apply_plan(app, gui, document, plan, controller):
    # Business owns the private native-GUI grant and the one-use token.
    verify_plan(app, gui, document, plan, controller)
    if plan['length_mm'] == plan['snapshot'][2]:
        _fail()
    result = []
    def write(check_owner):
        try:
            _verify(app, gui, document, plan, controller, check_owner)
            check_owner()
            _writable_length(plan['pad'])
            check_owner()
            plan['pad'].Length = plan['length_mm']
            check_owner()
            _verify(app, gui, document, plan, controller, check_owner, plan['length_mm'], False)
            check_owner()
            try:
                count = document.recompute([plan['body']], False, True)
            except Exception:
                check_owner()
                _fail('PAD_RECOMPUTE_UNVERIFIED')
            check_owner()
            _, current, shapes = _verify(app, gui, document, plan, controller, check_owner, plan['length_mm'])
            result.append(_verified_result(plan, current, shapes, count))
            check_owner()
        except (PftPadLengthError, controller.PftWriteError):
            raise
        except Exception:
            _fail('PAD_PARAMETER_VERIFY_FAILED')
    def rollback():
        _verify(app, gui, document, plan, controller)
    def committed():
        _, current, shapes = _verify(app, gui, document, plan, controller, expected_length=plan['length_mm'])
        if _verified_result(plan, current, shapes, result[0]['recompute_count']) != result[0]:
            _fail('PAD_PARAMETER_VERIFY_FAILED')
    controller._run_transaction(app, document, 'Phoenix: set first Pad length', write, rollback, committed)
    return result[0]


def apply_plan(app, gui, document, plan, controller):
    try:
        return _apply_plan(app, gui, document, plan, controller)
    except (PftPadLengthError, controller.PftWriteError):
        raise
    except Exception:
        _fail('PAD_PARAMETER_VERIFY_FAILED')
