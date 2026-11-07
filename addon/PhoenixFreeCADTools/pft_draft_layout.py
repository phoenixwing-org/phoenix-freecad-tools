# SPDX-License-Identifier: Apache-2.0
"""Detached, bounded A4 annotation slots for a newly approved drawing only.

The geometry is a conservative global AABB estimate. No CAD, Qt, filesystem,
projection, font, preference or service access is performed by this module.
"""
from copy import deepcopy
import json
import math
import unicodedata

MAX_INPUT_BYTES = 4096
MAX_RESULT_BYTES = 16384
SCALES = (1., .5, .2, .1, .05, .02, .01, .005, .002, .001)
CAPABILITIES = {'version': 1, 'operation': 'create_drawing', 'algorithm': 'annotation_slots_v1',
    'profile': 'freecad_1_1_4_a4_mm_v1', 'confirmation': 'native_gui_only',
    'preview_field': 'draft_layout', 'max_input_bytes': MAX_INPUT_BYTES,
    'max_result_bytes': MAX_RESULT_BYTES, 'saves_document': False,
    'layout_verification': 'estimated_geometry_only'}
ERROR_CODES = frozenset(('DRAWING_DRAFT_LAYOUT_UNAVAILABLE', 'DRAWING_DRAFT_LAYOUT_NO_SPACE',
                         'DRAWING_DRAFT_LAYOUT_STALE'))
_DIRECTIONS = (('front', [0., -1., 0.], [1., 0., 0.]),
               ('top', [0., 0., 1.], [1., 0., 0.]),
               ('axonometric', [1., -1., 1.], [1., 1., 0.]))
_ROW_KEYS = frozenset(('object', 'field', 'old', 'new', 'status', 'reason', 'source', 'note'))
_BLOCKED_KEYS = frozenset(('object', 'field', 'old', 'new', 'status', 'reason'))


class PftDraftLayoutError(ValueError):
    def __init__(self, code='DRAWING_DRAFT_LAYOUT_UNAVAILABLE'):
        self.code = code if type(code) is str and code in ERROR_CODES else 'DRAWING_DRAFT_LAYOUT_UNAVAILABLE'
        super().__init__('New drawing annotation slots could not be completely verified.' if self.code != 'DRAWING_DRAFT_LAYOUT_NO_SPACE'
                         else 'The complete three-view annotation layout does not fit the supported paper area.')


def _reject(code='DRAWING_DRAFT_LAYOUT_UNAVAILABLE'):
    raise PftDraftLayoutError(code)


def _keys(value, keys):
    return (type(value) is dict and all(type(k) is str for k in value)
            and set(value) == keys)


def _name(value):
    if (type(value) is not str or not 1 <= len(value) <= 64 or value != value.strip()
            or value in ('.', '..') or any(c in '/\\:' or unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in value)):
        _reject()
    return value


def _number(value):
    if type(value) not in (int, float) or not 0 < value <= 1e12 or not math.isfinite(value):
        _reject()
    return float(value)


def _budget(value, maximum):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf8')
    except Exception:
        _reject()
    if len(encoded) > maximum:
        _reject()


def _box(center, span, pad=0.):
    result = [center[0] - span[0] / 2. - pad, center[1] - span[1] / 2. - pad,
              center[0] + span[0] / 2. + pad, center[1] + span[1] / 2. + pad]
    if (any(not math.isfinite(v) for v in result) or not result[0] < result[2] or not result[1] < result[3]):
        _reject()
    return result


def plan(source, page, view_names, source_spans_mm):
    source, page = _name(source), _name(page)
    if type(view_names) not in (list, tuple) or len(view_names) != 3:
        _reject()
    names = [_name(v) for v in view_names]
    if len(set([source, page, *names])) != 5:
        _reject()
    if type(source_spans_mm) not in (list, tuple) or len(source_spans_mm) != 3:
        _reject()
    spans = [_number(v) for v in source_spans_mm]
    _budget({'source': source, 'page': page, 'view_names': names, 'source_spans_mm': spans}, MAX_INPUT_BYTES)
    x, y, z = spans
    projections = ((x, z), (x, y), ((x + y) / math.sqrt(2.), (x + y + 2. * z) / math.sqrt(6.)))
    for scale in SCALES:
        scaled = [[w * scale, h * scale] for w, h in projections]
        (wf, hf), (wt, ht), (wi, hi) = scaled
        left_w, left_h = max(wf, wt) + 40., hf + ht + 88.
        right_w, right_h = wi + 40., hi + 40.
        total_w = left_w + 8. + right_w
        if total_w <= 277. and max(left_h, right_h) <= 119.:
            break
    else:
        _reject('DRAWING_DRAFT_LAYOUT_NO_SPACE')
    left = 10. + (277. - total_w) / 2.
    bottom = 81. + (119. - left_h) / 2.
    centers = ([left + left_w / 2., bottom + ht + 40. + 8. + (hf + 40.) / 2.],
               [left + left_w / 2., bottom + (ht + 40.) / 2.],
               [left + left_w + 8. + right_w / 2., 140.5])
    views = []
    for name, (kind, direction, x_direction), center, span in zip(names, _DIRECTIONS, centers, scaled):
        views.append({'name': name, 'kind': kind, 'direction': direction[:], 'x_direction': x_direction[:],
            'rotation_degrees': 0., 'center_mm': list(center), 'projected_span_mm': span,
            'estimated_geometry_bounds_mm': _box(center, span), 'annotation_envelope_mm': _box(center, span, 20.)})
    for row in views:
        b = row['annotation_envelope_mm']
        if b[0] < 10. - 1e-8 or b[1] < 81. - 1e-8 or b[2] > 287. + 1e-8 or b[3] > 200. + 1e-8:
            _reject()
    for index, row in enumerate(views):
        a = row['annotation_envelope_mm']
        for other in views[index + 1:]:
            b = other['annotation_envelope_mm']
            if not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]):
                _reject()
    result = {'version': 1, 'algorithm': 'annotation_slots_v1', 'source': source, 'page': page,
        'source_spans_mm': spans,
        'paper': {'width_mm': 297., 'height_mm': 210., 'margin_mm': 10.,
                  'reserved_rects_mm': [[0., 0., 297., 80.]], 'usable_bounds_mm': [10., 81., 287., 200.]},
        'annotation': {'margin_mm': 20., 'gutter_mm': 8., 'title_clearance_mm': 1.,
                       'unit': 'paper_mm', 'verification': 'reserved_space_only'},
        'scale': scale, 'views': views, 'geometry_source': 'source_global_aabb',
        'layout_verification': 'estimated_geometry_only', 'projection_verification': 'not_checked',
        'manual_review_required': True}
    _budget(result, MAX_RESULT_BYTES)
    return result


def _equal(actual, expected, *, key=None):
    kind = type(expected)
    if kind is dict:
        if (type(actual) is not dict or any(type(k) is not str for k in actual) or set(actual) != set(expected)):
            _reject()
        for field, value in expected.items():
            _equal(actual[field], value, key=field)
    elif kind is list:
        if type(actual) is not list or len(actual) != len(expected):
            _reject()
        for a, b in zip(actual, expected):
            _equal(a, b, key=key)
    elif kind in (int, float) and key != 'version':
        tolerance = 1e-8 if key in ('center_mm', 'projected_span_mm',
                                  'estimated_geometry_bounds_mm', 'annotation_envelope_mm') else 0.
        if (type(actual) not in (int, float) or not math.isfinite(actual)
                or abs(actual - expected) > tolerance):
            _reject()
    elif type(actual) is not kind or actual != expected:
        _reject()


def validate(value):
    try:
        if (type(value) is not dict or any(type(k) is not str for k in value)
                or type(value.get('views')) is not list or len(value['views']) != 3
                or any(type(row) is not dict or any(type(k) is not str for k in row) for row in value['views'])):
            _reject()
        expected = plan(value['source'], value['page'], [row['name'] for row in value['views']], value['source_spans_mm'])
        _equal(value, expected)
        _budget(value, MAX_RESULT_BYTES)
        return deepcopy(value)
    except PftDraftLayoutError:
        raise
    except Exception:
        _reject()


def validate_preview(value, request, rows, *, can_apply):
    try:
        if not _keys(value, {'draft_layout', 'draft_layout_reason'}) or type(can_apply) is not bool:
            _reject()
        if not _keys(request, {'source', 'page'}) or type(rows) is not list:
            _reject()
        source = _name(request['source'])
        if can_apply:
            dto = validate(value['draft_layout'])
            if value['draft_layout_reason'] is not None or source != dto['source'] or request['page'] != dto['page'] or len(rows) != 5:
                _reject()
            names = []
            for row in rows:
                if (not _keys(row, _ROW_KEYS) or row['field'] != 'TechDraw draft node'
                        or row['old'] is not None or row['status'] != 'change' or row['reason'] != ''
                        or row['source'] != 'cached_native_typed_snapshot'
                        or any(type(row[k]) is not str or not 1 <= len(row[k]) <= 2048 for k in ('new', 'note'))):
                    _reject()
                names.append(_name(row['object']))
            if (names[0] != dto['page'] or names[2:] != [row['name'] for row in dto['views']]
                    or len(set(names)) != 5 or source in names):
                _reject()
            return {'draft_layout': dto, 'draft_layout_reason': None}
        reason = value['draft_layout_reason']
        if (value['draft_layout'] is not None or type(reason) is not str or reason not in ERROR_CODES
                or request['page'] is not None or len(rows) != 1):
            _reject()
        row = rows[0]
        if (not _keys(row, _BLOCKED_KEYS) or row['object'] != source
                or row['field'] != 'TechDraw draft node' or row['old'] is not None or row['new'] is not None
                or row['status'] != 'blocked' or type(row['reason']) is not str or not 1 <= len(row['reason']) <= 2048):
            _reject()
        return {'draft_layout': None, 'draft_layout_reason': reason}
    except PftDraftLayoutError:
        raise
    except Exception:
        _reject()
