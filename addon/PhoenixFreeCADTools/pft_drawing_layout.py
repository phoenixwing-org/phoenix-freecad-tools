# SPDX-License-Identifier: Apache-2.0
"""Detached, bounded paper-mm position suggestions, without native operations.

The caller supplies measured label sizes and frozen projected geometry. Lines
and label rectangles below are estimates, not native dimension rendering or a
claim of fresh projection. All geometry uses absolute paper mm, positive Y up;
resolved dimension coordinates remain relative to the supplied view origin.
"""
import json
import math
import re
import unicodedata

KINDS = ('Distance', 'DistanceX', 'DistanceY', 'Diameter', 'Radius')
ALGORITHM = 'paper_lanes_v1'
MAX_INPUT_BYTES = 65536
MAX_RESULT_BYTES = 32768
MAX_CANDIDATES = 64
MAX_SEARCH = 8192
MAX_GEOMETRY_CHECKS = 131072
MAX_PAIR_CACHE = 8192
REASONS = frozenset(('OUTSIDE_PAPER', 'RESERVED_REGION', 'LABEL_COLLISION',
                     'LINE_COLLISION', 'NO_SPACE', 'SEARCH_BUDGET_EXHAUSTED'))
_EPS = 1e-8
_EDGE = re.compile(r'Edge(?:0|[1-9][0-9]{0,3})\Z')
_ERRORS = {'DRAWING_LAYOUT_INVALID_ARGUMENT': '尺寸布局输入格式无效。',
           'DRAWING_LAYOUT_INVALID_RESULT': '尺寸布局结果无法验证。',
           'DRAWING_LAYOUT_TOO_LARGE': '尺寸布局数据超过有限预算。'}


class PftDrawingLayoutError(ValueError):
    def __init__(self, code='DRAWING_LAYOUT_INVALID_ARGUMENT'):
        self.code = code if code in _ERRORS else 'DRAWING_LAYOUT_INVALID_ARGUMENT'
        self.message = _ERRORS[self.code]
        super().__init__(self.message)


def _fail(code='DRAWING_LAYOUT_INVALID_ARGUMENT'):
    raise PftDrawingLayoutError(code)


class _SearchLimit(Exception):
    pass


class _Budget:
    def __init__(self):
        self.checks = 0

    def tick(self):
        if self.checks >= MAX_GEOMETRY_CHECKS:
            raise _SearchLimit()
        self.checks += 1


def _mapping(value, keys):
    if (type(value) is not dict or len(value) != len(keys)
            or any(type(k) is not str for k in value) or set(value) != set(keys)):
        _fail()
    return value


def _list(value, minimum, maximum):
    if type(value) is not list or not minimum <= len(value) <= maximum:
        _fail()
    return value


def _number(value, low=-1e6, high=1e6, positive=False):
    if (type(value) not in (int, float) or value < low or value > high
            or not math.isfinite(value) or positive and value <= 0):
        _fail()
    return value


def _name(value):
    if (type(value) is not str or not 1 <= len(value) <= 64
            or value != value.strip() or value in ('.', '..')
            or any(c in '/\\:' or unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in value)):
        _fail()
    return value


def _text(value):
    if (type(value) is not str or not 1 <= len(value) <= 256 or value.count('\n') > 3
            or any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') and c != '\n' for c in value)):
        _fail()
    return value


def _point(value):
    return [_number(x) for x in _list(value, 2, 2)]


def _rect(value):
    result = [_number(x) for x in _list(value, 4, 4)]
    if result[0] >= result[2] or result[1] >= result[3]:
        _fail()
    return result


def _segment(value):
    result = [_point(x) for x in _list(value, 2, 2)]
    if result[0] == result[1]:
        _fail()
    return result


def _edge_kind(row):
    edge, kind = row['edge'], row['kind']
    if (type(edge) is not str or _EDGE.fullmatch(edge) is None or int(edge[4:]) > 2047
            or type(kind) is not str or kind not in KINDS):
        _fail()
    return edge, kind


def _bounded(value, limit):
    try:
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False,
                              separators=(',', ':')).encode('utf-8'))
    except Exception:
        _fail()
    if size > limit:
        _fail('DRAWING_LAYOUT_TOO_LARGE')
    return value


def _inside(outer, inner, budget=None):
    if budget is not None:
        budget.tick()
    return (inner[0] >= outer[0] - _EPS and inner[1] >= outer[1] - _EPS
            and inner[2] <= outer[2] + _EPS and inner[3] <= outer[3] + _EPS)


def validate_input(value):
    """Validate exact JSON-like builtins and return detached ordinary data."""
    value = _mapping(value, ('version', 'paper', 'view', 'obstacles', 'spacing', 'dimensions'))
    if type(value['version']) is not int or value['version'] != 1:
        _fail()
    paper = _mapping(value['paper'], ('width_mm', 'height_mm', 'margin_mm', 'reserved_rects'))
    width = _number(paper['width_mm'], 0, 10000, True)
    height = _number(paper['height_mm'], 0, 10000, True)
    margin = _number(paper['margin_mm'], 0, 5000)
    if margin * 2 >= min(width, height):
        _fail()
    paper = {'width_mm': width, 'height_mm': height, 'margin_mm': margin,
             'reserved_rects': [_rect(r) for r in _list(paper['reserved_rects'], 0, 32)]}
    view = _mapping(value['view'], ('name', 'origin_mm', 'bounds_mm'))
    view = {'name': _name(view['name']), 'origin_mm': _point(view['origin_mm']),
            'bounds_mm': _rect(view['bounds_mm'])}
    obstacles = _mapping(value['obstacles'], ('rects', 'segments'))
    rects, names = [], {view['name']}
    for item in _list(obstacles['rects'], 0, 128):
        item = _mapping(item, ('name', 'kind', 'bounds_mm'))
        name = _name(item['name'])
        if name in names or type(item['kind']) is not str or item['kind'] not in ('view', 'dimension'):
            _fail()
        names.add(name)
        rects.append({'name': name, 'kind': item['kind'], 'bounds_mm': _rect(item['bounds_mm'])})
    obstacles = {'rects': rects,
                 'segments': [_segment(s) for s in _list(obstacles['segments'], 0, 512)]}
    spacing = _mapping(value['spacing'], ('clearance_mm', 'offset_mm', 'lane_mm'))
    spacing = {'clearance_mm': _number(spacing['clearance_mm'], 0, 20),
               'offset_mm': _number(spacing['offset_mm'], 0, 100, True),
               'lane_mm': _number(spacing['lane_mm'], 0, 100, True)}
    rows, seen = [], set()
    for item in _list(value['dimensions'], 1, 8):
        if type(item) is not dict or any(type(k) is not str for k in item):
            _fail()
        mode = item.get('placement')
        keys = ('edge', 'kind', 'placement', 'geometry', 'text', 'label_size_mm')
        if type(mode) is not str or mode not in ('auto', 'explicit'):
            _fail()
        _mapping(item, keys + (('x_mm', 'y_mm') if mode == 'explicit' else ()))
        edge, kind = _edge_kind(item)
        if (edge, kind) in seen:
            _fail()
        seen.add((edge, kind))
        geometry = item['geometry']
        if type(geometry) is not dict:
            _fail()
        if kind in ('Radius', 'Diameter'):
            _mapping(geometry, ('curve', 'center_mm', 'radius_mm'))
            if type(geometry['curve']) is not str or geometry['curve'] != 'circle':
                _fail()
            center, radius = _point(geometry['center_mm']), _number(geometry['radius_mm'], 0, 1e6, True)
            geometry = {'curve': 'circle', 'center_mm': center, 'radius_mm': radius}
            bounds = [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius]
        else:
            _mapping(geometry, ('curve', 'start_mm', 'end_mm'))
            if type(geometry['curve']) is not str or geometry['curve'] != 'line':
                _fail()
            start, end = _point(geometry['start_mm']), _point(geometry['end_mm'])
            if (start == end or kind == 'DistanceX' and start[0] == end[0]
                    or kind == 'DistanceY' and start[1] == end[1]):
                _fail()
            geometry = {'curve': 'line', 'start_mm': start, 'end_mm': end}
            bounds = [min(start[0], end[0]), min(start[1], end[1]),
                      max(start[0], end[0]), max(start[1], end[1])]
        if not _inside(view['bounds_mm'], bounds):
            _fail()
        size = [_number(n, 0, 1000, True) for n in _list(item['label_size_mm'], 2, 2)]
        row = {'edge': edge, 'kind': kind, 'placement': mode, 'geometry': geometry,
               'text': _text(item['text']), 'label_size_mm': size}
        if mode == 'explicit':
            row.update(x_mm=_number(item['x_mm'], -1000, 1000),
                       y_mm=_number(item['y_mm'], -1000, 1000))
        rows.append(row)
    return _bounded({'version': 1, 'paper': paper, 'view': view, 'obstacles': obstacles,
                     'spacing': spacing, 'dimensions': rows}, MAX_INPUT_BYTES)


def _inflate(rect, distance):
    return [rect[0] - distance, rect[1] - distance, rect[2] + distance, rect[3] + distance]


def _rect_overlap(a, b, gap=0, budget=None):
    if budget is not None:
        budget.tick()
    return not (a[2] + gap < b[0] - _EPS or b[2] + gap < a[0] - _EPS
                or a[3] + gap < b[1] - _EPS or b[3] + gap < a[1] - _EPS)


def _clip_interval(segment, rect, budget=None):
    """Parameter interval inside an inclusive axis-aligned rectangle."""
    if budget is not None:
        budget.tick()
    a, b = segment
    low, high = 0., 1.
    for coordinate in (0, 1):
        delta = b[coordinate] - a[coordinate]
        if delta == 0:
            if a[coordinate] < rect[coordinate] or a[coordinate] > rect[coordinate + 2]:
                return None
        else:
            first = (rect[coordinate] - a[coordinate]) / delta
            last = (rect[coordinate + 2] - a[coordinate]) / delta
            low, high = max(low, min(first, last)), min(high, max(first, last))
            if low > high:
                return None
    return low, high


def _at(segment, t):
    return [segment[0][i] + (segment[1][i] - segment[0][i]) * t for i in (0, 1)]


def _trim(segment, box):
    interval = _clip_interval(segment, box)
    if interval is None:
        return [segment]
    result = []
    if interval[0] > 0:
        result.append([segment[0], _at(segment, interval[0])])
    if interval[1] < 1:
        result.append([_at(segment, interval[1]), segment[1]])
    return result


def _point_distance(point, segment):
    a, b = segment
    delta = [b[0] - a[0], b[1] - a[1]]
    length = math.hypot(*delta)
    if length == 0:
        return math.dist(point, a)
    direction = [x / length for x in delta]
    distance = max(0., min(length, sum((point[i] - a[i]) * direction[i] for i in (0, 1))))
    return math.hypot(point[0] - a[0] - distance * direction[0],
                      point[1] - a[1] - distance * direction[1])


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_near(a, b, gap, budget=None):
    if budget is not None:
        budget.tick()
    c1, c2 = _cross(a[0], a[1], b[0]), _cross(a[0], a[1], b[1])
    c3, c4 = _cross(b[0], b[1], a[0]), _cross(b[0], b[1], a[1])
    if ((c1 > 0 > c2 or c2 > 0 > c1) and (c3 > 0 > c4 or c4 > 0 > c3)):
        return True
    return min(_point_distance(a[0], b), _point_distance(a[1], b),
               _point_distance(b[0], a), _point_distance(b[1], a)) <= gap + _EPS


def _candidate(row, center, origin, clearance, resolved=None):
    if row['placement'] == 'explicit':
        x, y = row['x_mm'], row['y_mm']
    elif resolved is not None:
        x, y = resolved
    else:
        x, y = center[0] - origin[0], center[1] - origin[1]
    # Canonicalise through the exact output offsets, so non-binary centres
    # reproduce the same rectangles after a JSON round trip and revalidation.
    center = [origin[0] + x, origin[1] + y]
    width, height = row['label_size_mm']
    box = [center[0] - width / 2, center[1] - height / 2,
           center[0] + width / 2, center[1] + height / 2]
    raw, geometry = [], row['geometry']
    if geometry['curve'] == 'line':
        p, q = geometry['start_mm'], geometry['end_mm']
        if row['kind'] == 'DistanceX':
            direction = [1., 0.]
        elif row['kind'] == 'DistanceY':
            direction = [0., 1.]
        else:
            length = math.hypot(q[0] - p[0], q[1] - p[1])
            direction = [(q[0] - p[0]) / length, (q[1] - p[1]) / length]
        # Project the reference endpoints onto the dimension line through the
        # suggested label centre. Extension overlap at a shared reference is
        # handled explicitly below, rather than ignoring all line collisions.
        feet = []
        for point in (p, q):
            distance = sum((point[i] - center[i]) * direction[i] for i in (0, 1))
            foot = [center[i] + direction[i] * distance for i in (0, 1)]
            feet.append(foot)
            raw.append(('extension', [point, foot], tuple(point)))
        distances = [sum((point[i] - center[i]) * direction[i] for i in (0, 1)) for point in feet]
        lo, hi = min(0., *distances), max(0., *distances)
        raw.append(('dimension', [[center[i] + direction[i] * lo for i in (0, 1)],
                                  [center[i] + direction[i] * hi for i in (0, 1)]], None))
        anchors = (tuple(p), tuple(q))
    else:
        point, radius = geometry['center_mm'], geometry['radius_mm']
        distance = math.hypot(center[0] - point[0], center[1] - point[1])
        direction = [(center[i] - point[i]) / distance for i in (0, 1)] if distance else [1., 0.]
        start = ([point[i] - direction[i] * radius for i in (0, 1)]
                 if row['kind'] == 'Diameter' else point[:])
        raw.append(('leader', [start, center[:]], None))
        anchors = ()
    segments, records = [], []
    for role, segment, anchor in raw:
        if math.dist(*segment) <= _EPS:
            continue
        for piece in _trim(segment, _inflate(box, clearance / 2)):
            if math.dist(*piece) > _EPS:
                segments.append(piece)
                records.append((role, piece, anchor))
    result = {'edge': row['edge'], 'kind': row['kind'], 'placement': row['placement'],
              'x_mm': x, 'y_mm': y, 'text': row['text'], 'label_box_mm': box,
              'segments_mm': segments}
    circle = ((row['edge'], tuple(geometry['center_mm']), geometry['radius_mm'])
              if geometry['curve'] == 'circle' else None)
    return {'result': result, 'records': records, 'anchors': anchors, 'circle': circle}


def _static_conflict(candidate, value, budget=None):
    result = candidate['result']
    box = result['label_box_mm']
    if box[0] >= box[2] or box[1] >= box[3]:
        # Positive subnormal sizes may round to zero extent at the supplied
        # paper position. Such a box cannot support a checkable suggestion.
        return 'NO_SPACE'
    if not -1000 <= result['x_mm'] <= 1000 or not -1000 <= result['y_mm'] <= 1000:
        return 'OUTSIDE_PAPER'
    paper, gap = value['paper'], value['spacing']['clearance_mm']
    margin = paper['margin_mm']
    usable = [margin, margin, paper['width_mm'] - margin, paper['height_mm'] - margin]
    if not _inside(usable, result['label_box_mm'], budget):
        return 'OUTSIDE_PAPER'
    for segment in result['segments_mm']:
        if not all(usable[0] - _EPS <= p[0] <= usable[2] + _EPS
                   and usable[1] - _EPS <= p[1] <= usable[3] + _EPS for p in segment):
            return 'OUTSIDE_PAPER'
    for rect in paper['reserved_rects']:
        if (_rect_overlap(result['label_box_mm'], rect, gap, budget)
                or any(_clip_interval(s, _inflate(rect, gap), budget) is not None for s in result['segments_mm'])):
            return 'RESERVED_REGION'
    if _rect_overlap(result['label_box_mm'], value['view']['bounds_mm'], gap, budget):
        return 'LABEL_COLLISION'
    for obstacle in value['obstacles']['rects']:
        rect = obstacle['bounds_mm']
        if _rect_overlap(result['label_box_mm'], rect, gap, budget):
            return 'LABEL_COLLISION'
        if any(_clip_interval(s, _inflate(rect, gap), budget) is not None for s in result['segments_mm']):
            return 'LINE_COLLISION'
    for segment in value['obstacles']['segments']:
        if _clip_interval(segment, _inflate(result['label_box_mm'], gap), budget) is not None:
            return 'LABEL_COLLISION'
        if any(_segments_near(segment, s, gap, budget) for s in result['segments_mm']):
            return 'LINE_COLLISION'
    return None


def _shared_extension(a, b, left, right):
    role_a, segment_a, anchor_a = a
    role_b, segment_b, anchor_b = b
    if role_a == role_b == 'extension' and anchor_a == anchor_b:
        return True
    if (role_a == role_b == 'leader' and left['circle'] is not None
            and left['circle'] == right['circle']):
        center = left['circle'][1]
        cross = ((segment_a[1][0] - segment_a[0][0]) * (segment_b[1][1] - segment_b[0][1])
                 - (segment_a[1][1] - segment_a[0][1]) * (segment_b[1][0] - segment_b[0][0]))
        # Same-circle radius/diameter leaders necessarily meet at the common
        # centre. Only that transverse meeting is exempt, never shared exterior
        # leader runs or a nearby circle's independent annotation.
        return (abs(cross) > _EPS * math.dist(*segment_a) * math.dist(*segment_b)
                and _point_distance(center, segment_a) <= _EPS
                and _point_distance(center, segment_b) <= _EPS)
    # A cascade legitimately joins another witness at the shared reference's
    # projection onto the dimension line. When the label is outside a short
    # edge, the extended line can put this join inside the finite line segment.
    if role_b == 'extension' and role_a == 'dimension':
        return _shared_extension(b, a, right, left)
    if role_a == 'extension' and role_b == 'dimension' and anchor_a in right['anchors']:
        length_a, length_b = math.dist(*segment_a), math.dist(*segment_b)
        da = [segment_a[1][i] - segment_a[0][i] for i in (0, 1)]
        db = [segment_b[1][i] - segment_b[0][i] for i in (0, 1)]
        if (length_a <= _EPS or length_b <= _EPS
                or abs(sum(da[i] * db[i] for i in (0, 1))) > _EPS * length_a * length_b):
            return False
        unit = [n / length_b for n in db]
        distance = sum((anchor_a[i] - segment_b[0][i]) * unit[i] for i in (0, 1))
        projection = [segment_b[0][i] + unit[i] * distance for i in (0, 1)]
        return (_point_distance(projection, segment_a) <= _EPS
                and _point_distance(projection, segment_b) <= _EPS)
    return False


def _pair_conflict(left, right, gap, budget=None):
    a, b = left['result'], right['result']
    if _rect_overlap(a['label_box_mm'], b['label_box_mm'], gap, budget):
        return 'LABEL_COLLISION'
    if (any(_clip_interval(s, _inflate(a['label_box_mm'], gap), budget) is not None for s in b['segments_mm'])
            or any(_clip_interval(s, _inflate(b['label_box_mm'], gap), budget) is not None for s in a['segments_mm'])):
        return 'LABEL_COLLISION'
    for sa in left['records']:
        for sb in right['records']:
            if _segments_near(sa[1], sb[1], gap, budget) and not _shared_extension(sa, sb, left, right):
                return 'LINE_COLLISION'
    return None


def _ray_exit(point, direction, rect):
    distances = []
    for i in (0, 1):
        if direction[i] > 0:
            distances.append((rect[i + 2] - point[i]) / direction[i])
        elif direction[i] < 0:
            distances.append((rect[i] - point[i]) / direction[i])
    return max(0., min(distances))


def _boundary_centers(row, value):
    """Four fixed seeds in the paper bands on both sides of a linear reference.

    The normal lane offset is a placement preference. A narrow free band may
    fit a tangentially displaced label even when it cannot fit that offset.
    These extra seeds still pass the same hard paper/clearance/collision tests.
    Each seed visits at most the 32 validated reserved rectangles once.
    """
    geometry = row['geometry']
    if geometry['curve'] != 'line':
        return []
    p, q = geometry['start_mm'], geometry['end_mm']
    if row['kind'] in ('DistanceX', 'DistanceY'):
        axis = 0 if row['kind'] == 'DistanceX' else 1
        step = row['label_size_mm'][axis]
    else:
        # Sampling a paper band does not change the reference's actual tangent
        # or the dimension construction, including for an oblique Distance.
        axis = 0 if abs(q[0] - p[0]) >= abs(q[1] - p[1]) else 1
        step = max(row['label_size_mm'])
    other = 1 - axis
    paper, bounds = value['paper'], value['view']['bounds_mm']
    gap = value['spacing']['clearance_mm']
    step += 2 * gap
    mid = (p[axis] + q[axis]) / 2
    tangent_half, normal_half = (row['label_size_mm'][i] / 2 for i in (axis, other))
    extent = paper['width_mm'] if other == 0 else paper['height_mm']
    points = []
    for sign in (-1, 1):
        for shift in (-2, 2):
            tangent = mid + shift * step
            normal = (paper['margin_mm'] + normal_half if sign < 0
                      else extent - paper['margin_mm'] - normal_half)
            for rect in paper['reserved_rects']:
                if (tangent + tangent_half + gap >= rect[axis]
                        and tangent - tangent_half - gap <= rect[axis + 2]):
                    if sign < 0 and rect[other + 2] < bounds[other]:
                        normal = max(normal, rect[other + 2] + gap + _EPS * 4 + normal_half)
                    elif sign > 0 and rect[other] > bounds[other + 2]:
                        normal = min(normal, rect[other] - gap - _EPS * 4 - normal_half)
            point = [0., 0.]
            point[axis], point[other] = tangent, normal
            points.append(point)
    return points


def _centers(row, value):
    bounds, spacing = value['view']['bounds_mm'], value['spacing']
    width, height = row['label_size_mm']
    offset, lane = spacing['offset_mm'], spacing['lane_mm']
    geometry = row['geometry']
    if row['placement'] == 'explicit':
        origin = value['view']['origin_mm']
        return [[origin[0] + row['x_mm'], origin[1] + row['y_mm']]]
    candidates = []
    if row['kind'] in ('DistanceX', 'DistanceY'):
        axis = 0 if row['kind'] == 'DistanceX' else 1
        other = 1 - axis
        mid = (geometry['start_mm'][axis] + geometry['end_mm'][axis]) / 2
        step = row['label_size_mm'][axis] + spacing['clearance_mm'] * 2
        for level in range(6):
            for sign in (1, -1):
                position = bounds[other + (2 if sign > 0 else 0)] + sign * (
                    row['label_size_mm'][other] / 2 + offset + level * lane)
                for shift in (0, -1, 1, -2, 2):
                    point = [0., 0.]
                    point[axis], point[other] = mid + shift * step, position
                    candidates.append(point)
    else:
        if geometry['curve'] == 'line':
            p, q = geometry['start_mm'], geometry['end_mm']
            center = [(p[i] + q[i]) / 2 for i in (0, 1)]
            length = math.dist(p, q)
            tangent = [(q[i] - p[i]) / length for i in (0, 1)]
            normals = [[-tangent[1], tangent[0]], [tangent[1], -tangent[0]]]
            step = max(width, height) + spacing['clearance_mm'] * 2
            shifts = (0, -1, 1, -2, 2)
        else:
            center = geometry['center_mm']
            tangent, step, shifts = [0., 0.], 0., (0,)
            diagonal = math.sqrt(.5)
            normals = [[1., 0.], [diagonal, diagonal], [0., 1.], [-diagonal, diagonal],
                       [-1., 0.], [-diagonal, -diagonal], [0., -1.], [diagonal, -diagonal]]
        expanded = [bounds[0] - width / 2, bounds[1] - height / 2,
                    bounds[2] + width / 2, bounds[3] + height / 2]
        for level in range(6):
            for direction in normals:
                distance = _ray_exit(center, direction, expanded) + offset + level * lane
                for shift in shifts:
                    candidates.append([center[i] + direction[i] * distance + tangent[i] * step * shift
                                       for i in (0, 1)])
    candidates.extend(_boundary_centers(row, value))
    # Keep finite deterministic candidate sets; never silently truncate a new
    # strategy whose size no longer fits the reviewed bound.
    if len(candidates) > MAX_CANDIDATES:
        _fail()
    return candidates


def _result(rows, blocked, tested):
    return {'version': 1, 'algorithm': ALGORITHM, 'can_apply': not blocked,
            'dimensions': rows if not blocked else [], 'blocked': blocked,
            'candidates_tested': tested, 'layout_verification': 'suggested_only'}


def suggest_layout(value):
    """Resolve every row or return no writable rows; no caller data is changed."""
    value = validate_input(value)
    rows, origin, gap = value['dimensions'], value['view']['origin_mm'], value['spacing']['clearance_mm']
    selected, choices, tested, pairs = {}, {}, 0, {}
    budget, current_index, search = _Budget(), 0, None
    try:
        for index, row in enumerate(rows):
            current_index = index
            if row['placement'] != 'explicit':
                continue
            candidate = _candidate(row, _centers(row, value)[0], origin, gap)
            reason = _static_conflict(candidate, value, budget)
            if reason is None:
                for other in selected.values():
                    reason = _pair_conflict(candidate, other, gap, budget)
                    if reason is not None:
                        break
            if reason:
                return _bounded(_result([], [{'index': index, 'reason': reason}], tested), MAX_RESULT_BYTES)
            selected[index] = candidate
        for index, row in enumerate(rows):
            current_index = index
            if row['placement'] == 'explicit':
                continue
            candidates = []
            for center in _centers(row, value):
                tested += 1
                candidate = _candidate(row, center, origin, gap)
                if (_static_conflict(candidate, value, budget) is None
                        and not any(_pair_conflict(candidate, fixed, gap, budget) for fixed in selected.values())):
                    candidates.append(candidate)
            if not candidates:
                return _bounded(_result([], [{'index': index, 'reason': 'NO_SPACE'}], tested), MAX_RESULT_BYTES)
            choices[index] = candidates
        order = sorted(choices, key=lambda i: (len(choices[i]), i))
        exhausted = False

        def compatible(left, a, right, b):
            key = (left, a, right, b) if left < right else (right, b, left, a)
            if key in pairs:
                return pairs[key]
            allowed = _pair_conflict(choices[left][a], choices[right][b], gap, budget) is None
            if len(pairs) < MAX_PAIR_CACHE:
                pairs[key] = allowed
            return allowed

        def search(domains):
            nonlocal tested, exhausted, current_index
            if not domains:
                return True
            # Forward checking avoids exploring a whole dead branch only to
            # discover that another row no longer has a feasible candidate.
            index = current_index = min(domains, key=lambda i: (len(domains[i]), i))
            for choice in domains[index]:
                if tested >= MAX_SEARCH:
                    exhausted = True
                    return False
                tested += 1
                remaining = {}
                for other, domain in domains.items():
                    if other == index:
                        continue
                    allowed = [b for b in domain if compatible(index, choice, other, b)]
                    if not allowed:
                        break
                    remaining[other] = allowed
                else:
                    selected[index] = choices[index][choice]
                    if search(remaining):
                        return True
                    del selected[index]
                if exhausted:
                    return False
            return False

        if search({i: list(range(len(candidates))) for i, candidates in choices.items()}):
            result = _result([selected[i]['result'] for i in range(len(rows))], [], tested)
        else:
            result = _result([], [{'index': order[0], 'reason': 'SEARCH_BUDGET_EXHAUSTED' if exhausted else 'NO_SPACE'}], tested)
        return _bounded(result, MAX_RESULT_BYTES)
    except _SearchLimit:
        return _bounded(_result([], [{'index': current_index,
                                     'reason': 'SEARCH_BUDGET_EXHAUSTED'}], tested), MAX_RESULT_BYTES)
    finally:
        # The local recursive visitor must not retain its captured dictionaries
        # after return. It contains only plain data, but has no reason to survive.
        search = None
        pairs.clear()


def validate_result(value, expected=None):
    """Validate a detached result and, when supplied, its complete input binding."""
    try:
        value = _mapping(value, ('version', 'algorithm', 'can_apply', 'dimensions', 'blocked',
                                 'candidates_tested', 'layout_verification'))
        if (type(value['version']) is not int or value['version'] != 1
                or type(value['algorithm']) is not str or value['algorithm'] != ALGORITHM
                or type(value['can_apply']) is not bool
                or type(value['layout_verification']) is not str
                or value['layout_verification'] != 'suggested_only'
                or type(value['candidates_tested']) is not int or not 0 <= value['candidates_tested'] <= MAX_SEARCH):
            _fail()
        rows, seen = [], set()
        for item in _list(value['dimensions'], 1 if value['can_apply'] else 0, 8 if value['can_apply'] else 0):
            _mapping(item, ('edge', 'kind', 'placement', 'x_mm', 'y_mm', 'text', 'label_box_mm', 'segments_mm'))
            edge, kind = _edge_kind(item)
            if ((edge, kind) in seen or type(item['placement']) is not str
                    or item['placement'] not in ('auto', 'explicit')):
                _fail()
            seen.add((edge, kind))
            rows.append({'edge': edge, 'kind': kind, 'placement': item['placement'],
                         'x_mm': _number(item['x_mm'], -1000, 1000), 'y_mm': _number(item['y_mm'], -1000, 1000),
                         'text': _text(item['text']), 'label_box_mm': _rect(item['label_box_mm']),
                         'segments_mm': [_segment(s) for s in _list(item['segments_mm'], 0, 12)]})
        blocked, indices = [], set()
        for item in _list(value['blocked'], 0 if value['can_apply'] else 1, 0 if value['can_apply'] else 8):
            _mapping(item, ('index', 'reason'))
            index, reason = item['index'], item['reason']
            if (type(index) is not int or not 0 <= index <= 7 or index in indices
                    or type(reason) is not str or reason not in REASONS):
                _fail()
            indices.add(index)
            blocked.append({'index': index, 'reason': reason})
        result = _bounded(_result(rows, blocked, value['candidates_tested']), MAX_RESULT_BYTES)
        if expected is not None:
            expected = validate_input(expected)
            if any(x['index'] >= len(expected['dimensions']) for x in blocked):
                _fail()
            if value['can_apply']:
                if len(rows) != len(expected['dimensions']):
                    _fail()
                candidates, origin = [], expected['view']['origin_mm']
                gap = expected['spacing']['clearance_mm']
                for row, request in zip(rows, expected['dimensions']):
                    if (any(row[k] != request[k] for k in ('edge', 'kind', 'placement', 'text'))
                            or request['placement'] == 'explicit' and any(row[k] != request[k] for k in ('x_mm', 'y_mm'))):
                        _fail()
                    center = [origin[0] + row['x_mm'], origin[1] + row['y_mm']]
                    candidate = _candidate(request, center, origin, gap,
                                           resolved=(row['x_mm'], row['y_mm']))
                    if (candidate['result'] != row or _static_conflict(candidate, expected)
                            or any(_pair_conflict(candidate, other, gap) for other in candidates)):
                        _fail()
                    candidates.append(candidate)
        return result
    except PftDrawingLayoutError:
        raise PftDrawingLayoutError('DRAWING_LAYOUT_INVALID_RESULT') from None
    except Exception:
        raise PftDrawingLayoutError('DRAWING_LAYOUT_INVALID_RESULT') from None
