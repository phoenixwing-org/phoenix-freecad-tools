# SPDX-License-Identifier: Apache-2.0
"""Bounded native cached TechDraw edge references, without document mutations.

TechDraw Edge names are zero based. Native Selection/Index getters return
unscaled Y-up values; copied points are normalized once to the public Qt Y-down
coordinate contract. No second scale or placement is applied. Availability
describes this cached edge, not HLR completion, freshness,
source topology identity, visibility, or a production drawing verification.
"""
import json
import math
import re

CAPABILITIES = {
    'version': 1, 'method': 'inspect_drawing_page', 'index_base': 0,
    'max_index': 2047, 'max_items': 50, 'source': 'native_cached_projection',
    'projection_verification': 'not_checked',
}
MAX_INDEX = 2047
MAX_ITEMS = 50
DEFAULT_LIMIT = 20
MAX_EDGE_BYTES = 2048
_MESSAGES = {
    'DRAWING_REFERENCES_INVALID_ARGUMENT': '投影边引用或索引窗口无效。',
    'DRAWING_REFERENCES_UNSUPPORTED': '目标不是同页受支持的原生 TechDraw 视图。',
    'DRAWING_REFERENCES_READ_FAILED': '投影边缓存或视图身份无法完整读取。',
    'DRAWING_REFERENCES_STALE': '读取期间图页、视图或成员身份发生变化。',
    'DRAWING_REFERENCES_TOO_LARGE': '投影边缓存结果超过有限读取预算。',
}
_KEYS = frozenset(('edge', 'status', 'curve', 'start', 'end', 'length_mm',
                   'radius_mm', 'center', 'closed', 'supported_kinds'))
_LINE_KINDS = ('Distance', 'DistanceX', 'DistanceY')
_CIRCLE_KINDS = ('Radius', 'Diameter')


class PftDrawingReferencesError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in _MESSAGES else 'DRAWING_REFERENCES_READ_FAILED'
        self.message = _MESSAGES[self.code]
        super().__init__(self.message)


def _fail(code='DRAWING_REFERENCES_READ_FAILED'):
    raise PftDrawingReferencesError(code)


def edge_index(edge):
    if type(edge) is not str or re.fullmatch(r'Edge(?:0|[1-9][0-9]{0,3})', edge) is None:
        _fail('DRAWING_REFERENCES_INVALID_ARGUMENT')
    index = int(edge[4:])
    if index > MAX_INDEX:
        _fail('DRAWING_REFERENCES_INVALID_ARGUMENT')
    return index


def validate_window(offset=0, limit=DEFAULT_LIMIT):
    if (type(offset) is not int or type(limit) is not int or not 0 <= offset <= MAX_INDEX
            or not 1 <= limit <= MAX_ITEMS or offset + limit - 1 > MAX_INDEX):
        _fail('DRAWING_REFERENCES_INVALID_ARGUMENT')
    return offset, limit


def _native_view(view):
    if view.TypeId != 'TechDraw::DrawViewPart' or type(view.TypeId) is not str:
        _fail('DRAWING_REFERENCES_UNSUPPORTED')
    properties = view.PropertiesList
    if (type(properties) is not list or len(properties) > 4096
            or any(type(value) is not str for value in properties) or 'Proxy' in properties):
        _fail('DRAWING_REFERENCES_UNSUPPORTED')
    try:
        getattr(view, 'Proxy')
    except AttributeError:
        pass
    else:
        _fail('DRAWING_REFERENCES_UNSUPPORTED')


def _number(value, positive=False):
    if type(value) is not float or not math.isfinite(value) or positive and value <= 0:
        _fail()
    return value


def _point(value, vector_type):
    if type(value) is not vector_type:
        _fail()
    # FreeCAD 1.1.4 DrawViewPartPyImp mirrors the cached display geometry in
    # both native getters. Normalize that Y-up result to our declared Qt frame
    # on the detached DTO only; the native Vector and edge remain untouched.
    return [_number(value.x), -_number(value.y), _number(value.z)]


def _empty(edge, status, curve=None):
    return {'edge': edge, 'status': status, 'curve': curve, 'start': None, 'end': None,
            'length_mm': None, 'radius_mm': None, 'center': None, 'closed': None,
            'supported_kinds': []}


def _bounded(record):
    try:
        size = len(json.dumps(record, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    except Exception:
        _fail()
    if size > MAX_EDGE_BYTES:
        _fail('DRAWING_REFERENCES_TOO_LARGE')
    return record


def read_edge(view, edge):
    """Return a frozen plain DTO for exactly one native EdgeN; never enumerate.

    Only exact Part.Edge/Part.Line/Part.Circle and Base.Vector values are read.
    Unsupported curve objects are not traversed. A circle must be closed. Native
    getter ValueError is the fixed API's missing-index result; other exceptions
    are read_failed, never an empty/valid edge or a forged zero measurement.
    """
    index = edge_index(edge)
    try:
        _native_view(view)
    except PftDrawingReferencesError:
        return _empty(edge, 'unsupported', 'unsupported')
    except Exception:
        return _empty(edge, 'read_failed')
    try:
        import FreeCAD
        import Part
        getter = getattr(view, 'getEdgeBySelection', None)
        try:
            native = getter(edge) if callable(getter) else view.getEdgeByIndex(index)
        except ValueError:
            return _empty(edge, 'missing')
        if type(native) is not Part.Edge:
            return _empty(edge, 'unsupported', 'unsupported')
        curve = native.Curve
        if type(curve) is Part.Line:
            vertices = native.Vertexes
            if (type(vertices) is not list or len(vertices) != 2
                    or any(type(vertex) is not Part.Vertex for vertex in vertices)):
                _fail()
            start = _point(vertices[0].Point, FreeCAD.Vector)
            end = _point(vertices[1].Point, FreeCAD.Vector)
            closed = native.isClosed()
            if type(closed) is not bool or closed or start == end:
                _fail()
            record = {'edge': edge, 'status': 'available', 'curve': 'line',
                      'start': start, 'end': end, 'length_mm': _number(native.Length, True),
                      'radius_mm': None, 'center': None, 'closed': False,
                      'supported_kinds': list(_LINE_KINDS)}
        elif type(curve) is Part.Circle:
            closed = native.isClosed()
            if type(closed) is not bool:
                _fail()
            if not closed:
                return _empty(edge, 'unsupported', 'unsupported')
            record = {'edge': edge, 'status': 'available', 'curve': 'circle',
                      'start': None, 'end': None, 'length_mm': _number(native.Length, True),
                      'radius_mm': _number(curve.Radius, True),
                      'center': _point(curve.Center, FreeCAD.Vector), 'closed': True,
                      'supported_kinds': list(_CIRCLE_KINDS)}
        else:
            return _empty(edge, 'unsupported', 'unsupported')
        return _bounded(record)
    except Exception:
        return _empty(edge, 'read_failed')


def _measurement(status='unknown', kind=None, value=None):
    return {'status': status, 'kind': kind,
            'value_mm': value if status == 'available' else None,
            'unit': 'mm', 'source': 'native_cached_dimension',
            'geometry_freshness': 'not_verified'}


def _registered_reference(document, obj):
    try:
        return (obj.Document is document and type(obj.Name) is str and 0 < len(obj.Name) <= 256
                and document.getObject(obj.Name) is obj)
    except Exception:
        return False


def _dimension_reference_scope(dimension):
    """Check reference identities only; never traverse source Shape or geometry."""
    try:
        document = dimension.Document
        if not _registered_reference(document, dimension):
            return False
        for field in ('References2D', 'References3D'):
            references = getattr(dimension, field)
            if (type(references) is not list or len(references) > 8
                    or field == 'References2D' and not references):
                return False
            for entry in references:
                if type(entry) is not tuple or len(entry) != 2:
                    return False
                owner, names = entry
                if (not _registered_reference(document, owner) or type(names) not in (list, tuple)
                        or not names or len(names) > 8):
                    return False
                if field == 'References2D':
                    _native_view(owner)
                    pattern = r'(?:Edge|Vertex)(?:0|[1-9][0-9]{0,3})'
                else:
                    pattern = r'(?:Face|Edge|Vertex)[1-9][0-9]{0,3}'
                for name in names:
                    if type(name) is not str or re.fullmatch(pattern, name) is None:
                        return False
                    native_index = int(re.search(r'[0-9]+\Z', name).group(0))
                    if native_index > (MAX_INDEX if field == 'References2D' else MAX_INDEX + 1):
                        return False
        return True
    except Exception:
        return False


def read_dimension(dimension):
    """Read one supported native cached projected dimension, never label text.

    Zero is also FreeCAD's missing/incomplete-geometry result, so it cannot prove
    a valid measurement. This does not recompute, wait for HLR, validate the 3D
    source, or claim that the cached value matches the currently rendered label.
    """
    kind = None
    try:
        if type(dimension.TypeId) is not str or dimension.TypeId != 'TechDraw::DrawViewDimension':
            return _measurement('unsupported')
        properties = dimension.PropertiesList
        if (type(properties) is not list or len(properties) > 4096
                or any(type(value) is not str for value in properties) or 'Proxy' in properties):
            return _measurement('unsupported')
        try:
            getattr(dimension, 'Proxy')
        except AttributeError:
            pass
        else:
            return _measurement('unsupported')
        native_kind = dimension.Type
        if type(native_kind) is not str or native_kind not in _LINE_KINDS + _CIRCLE_KINDS:
            return _measurement('unsupported')
        kind = native_kind
        measure_type = dimension.MeasureType
        if type(measure_type) is not str or measure_type != 'Projected' or dimension.Arbitrary is not False:
            return _measurement('unsupported', kind)
        states = dimension.State
        if (type(states) is not list or not states or len(states) > 16
                or any(type(state) is not str for state in states)
                or 'Up-to-date' not in states or set(states) - {'Up-to-date', 'Expanded'}):
            return _measurement('unknown', kind)
        if not _dimension_reference_scope(dimension):
            return _measurement('unsupported', kind)
        getter = getattr(dimension, 'getRawValue', None)
        if not callable(getter):
            return _measurement('unsupported', kind)
        value = getter()
        if type(value) is not float or not math.isfinite(value) or not 0. < value <= 1.e9:
            return _measurement('unknown', kind)
        return _measurement('available', kind, value)
    except Exception:
        return _measurement('read_failed', kind)


def edge_signature(record):
    """Immutable comparison of the complete public DTO, not native isSame()."""
    if type(record) is not dict or set(record) != _KEYS:
        _fail()
    edge_index(record['edge'])
    if (record['status'] not in ('available', 'missing', 'read_failed', 'unsupported')
            or record['curve'] not in (None, 'line', 'circle', 'unsupported')
            or type(record['supported_kinds']) is not list
            or any(type(kind) is not str for kind in record['supported_kinds'])):
        _fail()
    for field in ('start', 'end', 'center'):
        value = record[field]
        if value is not None:
            if type(value) is not list or len(value) != 3:
                _fail()
            for number in value:
                _number(number)
    for field in ('length_mm', 'radius_mm'):
        if record[field] is not None:
            _number(record[field], True)
    if record['closed'] is not None and type(record['closed']) is not bool:
        _fail()
    if record['status'] == 'available':
        if record['curve'] == 'line':
            if (record['start'] is None or record['end'] is None or record['start'] == record['end']
                    or record['length_mm'] is None or record['radius_mm'] is not None
                    or record['center'] is not None or record['closed'] is not False
                    or record['supported_kinds'] != list(_LINE_KINDS)):
                _fail()
        elif record['curve'] == 'circle':
            if (record['start'] is not None or record['end'] is not None
                    or record['length_mm'] is None or record['radius_mm'] is None
                    or record['center'] is None or record['closed'] is not True
                    or record['supported_kinds'] != list(_CIRCLE_KINDS)):
                _fail()
        else:
            _fail()
    elif (any(record[field] is not None for field in ('start', 'end', 'length_mm', 'radius_mm', 'center', 'closed'))
          or record['supported_kinds']
          or record['curve'] != ('unsupported' if record['status'] == 'unsupported' else None)):
        _fail()
    _bounded(record)
    return tuple(tuple(record[key]) if type(record[key]) is list else record[key] for key in sorted(_KEYS))


def _identity(document, page, view):
    if (page.Document is not document or document.getObject(page.Name) is not page
            or page.TypeId != 'TechDraw::DrawPage' or view.Document is not document
            or document.getObject(view.Name) is not view):
        _fail('DRAWING_REFERENCES_STALE')
    members = page.Views
    if (type(members) is not list or len(members) > 10000
            or sum(member is view for member in members) != 1):
        _fail('DRAWING_REFERENCES_UNSUPPORTED')
    return tuple(members)


def read_window(document, page, view, offset=0, limit=DEFAULT_LIMIT):
    """Query exactly a finite index window; missing entries do not imply a total."""
    offset, limit = validate_window(offset, limit)
    result = {'view': view.Name, 'status': 'available', 'offset': offset, 'limit': limit,
              'index_base': 0, 'max_index': MAX_INDEX, 'items': [], 'total_count': None,
              'enumeration_complete': False, 'length_unit': 'mm',
              'coordinate_system': 'view_local_unscaled_qt_y_down',
              'source': 'native_cached_projection', 'projection_verification': 'not_checked',
              'geometry_freshness': 'not_verified'}
    try:
        members = _identity(document, page, view)
        _native_view(view)
    except PftDrawingReferencesError as error:
        if error.code == 'DRAWING_REFERENCES_STALE':
            raise
        result['status'] = 'unsupported'
        return result
    except Exception:
        result['status'] = 'read_failed'
        return result
    result['items'] = [read_edge(view, 'Edge' + str(index)) for index in range(offset, offset + limit)]
    try:
        after = _identity(document, page, view)
        _native_view(view)
    except Exception:
        _fail('DRAWING_REFERENCES_STALE')
    if len(after) != len(members) or any(old is not new for old, new in zip(members, after)):
        _fail('DRAWING_REFERENCES_STALE')
    return result
