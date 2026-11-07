# SPDX-License-Identifier: Apache-2.0
"""Independent paper-mm examples; no CAD, Qt, files or service fixtures."""
from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[2] / 'addon/PhoenixFreeCADTools/pft_drawing_layout.py'
spec = importlib.util.spec_from_file_location('drawing_layout_fixture', PATH)
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)


def line(edge='Edge0', kind='DistanceX', start=None, end=None):
    return {'edge': edge, 'kind': kind, 'placement': 'auto',
            'geometry': {'curve': 'line', 'start_mm': [80, 100] if start is None else start,
                         'end_mm': [120, 100] if end is None else end},
            'text': '40.00', 'label_size_mm': [14, 5]}


def circle(edge='Edge2', kind='Radius', center=None):
    return {'edge': edge, 'kind': kind, 'placement': 'auto',
            'geometry': {'curve': 'circle', 'center_mm': [100, 120] if center is None else center,
                         'radius_mm': 5},
            'text': 'R5.00' if kind == 'Radius' else '⌀10.00', 'label_size_mm': [18, 5]}


def source(five=False):
    rows = [line()]
    if five:
        rows += [line(kind='Distance'), line('Edge1', 'DistanceY', [80, 100], [80, 140]),
                 circle(), circle(kind='Diameter')]
    return {'version': 1,
            'paper': {'width_mm': 297, 'height_mm': 210, 'margin_mm': 10,
                      'reserved_rects': [[10, 10, 287, 50]]},
            'view': {'name': 'TopView', 'origin_mm': [100, 120], 'bounds_mm': [80, 100, 120, 140]},
            'obstacles': {'rects': [], 'segments': []},
            'spacing': {'clearance_mm': 1, 'offset_mm': 6, 'lane_mm': 8}, 'dimensions': rows}


def explicit(row, x, y):
    row = deepcopy(row)
    row.update(placement='explicit', x_mm=x, y_mm=y)
    return row


def rectangle_sheet(scale):
    """A neutral 70 by 50 rectangle, a circle, and another fixed view."""
    def point(x, y): return [125 + x * scale, 145 + y * scale]
    value = source()
    value['view'].update(origin_mm=[125, 145], bounds_mm=point(-35, -25) + point(35, 25))
    value['paper']['reserved_rects'] = [[10, 10, 287, 80]]
    value['obstacles']['rects'] = [{'name': 'OtherView', 'kind': 'view', 'bounds_mm': [220, 120, 265, 175]}]
    value['spacing']['lane_mm'] = 9
    value['dimensions'] = [line(kind=kind, start=point(-35, -25), end=point(35, -25))
                           for kind in ('Distance', 'DistanceX')]
    value['dimensions'].append(line('Edge1', 'DistanceY', point(-35, -25), point(-35, 25)))
    for kind in ('Diameter', 'Radius'):
        value['dimensions'].append(circle(kind=kind, center=point(0, 0)))
        value['dimensions'][-1]['geometry']['radius_mm'] = 6 * scale
    for row in value['dimensions']:
        row['label_size_mm'] = [19 if row['kind'] == 'Diameter' else 15, 6]
    return value


def narrow_band_sheet(transpose=False):
    """Independent sheet with generous labels and a narrow lower paper band."""
    value = source()
    value['paper'] = {'width_mm': 320, 'height_mm': 240, 'margin_mm': 12,
                      'reserved_rects': [[0, 0, 320, 90]]}
    value['view'].update(origin_mm=[140, 126], bounds_mm=[119, 112, 161, 140])
    value['obstacles']['rects'] = [
        {'name': 'ElevationView', 'kind': 'view', 'bounds_mm': [118, 198, 162, 207]},
        {'name': 'SideView', 'kind': 'view', 'bounds_mm': [235, 157, 280, 192]}]
    value['spacing'] = {'clearance_mm': 1, 'offset_mm': 10, 'lane_mm': 7}
    value['dimensions'] = [line(kind='DistanceX', start=[120, 113], end=[160, 113]),
                           line('Edge1', 'DistanceY', [120, 113], [120, 139]),
                           line(kind='Distance', start=[120, 113], end=[160, 113]),
                           circle('Edge5', 'Diameter', [130, 120]),
                           circle('Edge5', 'Radius', [130, 120])]
    for row, size in zip(value['dimensions'], ([24, 19], [19, 24], [31, 31], [34, 34], [32, 32])):
        row['label_size_mm'] = size
        if row['geometry']['curve'] == 'circle':
            row['geometry']['radius_mm'] = 3
    if transpose:
        def rect(r): return [r[1], r[0], r[3], r[2]]
        paper = value['paper']
        paper['width_mm'], paper['height_mm'] = paper['height_mm'], paper['width_mm']
        paper['reserved_rects'] = [rect(r) for r in paper['reserved_rects']]
        value['view']['bounds_mm'] = rect(value['view']['bounds_mm'])
        value['view']['origin_mm'].reverse()
        for obstacle in value['obstacles']['rects']:
            obstacle['bounds_mm'] = rect(obstacle['bounds_mm'])
        for row in value['dimensions']:
            row['kind'] = {'DistanceX': 'DistanceY', 'DistanceY': 'DistanceX'}.get(row['kind'], row['kind'])
            row['label_size_mm'].reverse()
            for name in ('start_mm', 'end_mm', 'center_mm'):
                if name in row['geometry']:
                    row['geometry'][name].reverse()
    return value


class DrawingLayoutTests(unittest.TestCase):
    def check(self, value):
        before = deepcopy(value)
        result = layout.suggest_layout(value)
        self.assertEqual(value, before)
        self.assertEqual(layout.validate_result(result, expected=value), result)
        self.assertEqual(layout.validate_result(json.loads(json.dumps(result)), expected=value), result)
        return result

    def reject(self, value):
        with self.assertRaises(layout.PftDrawingLayoutError) as failure:
            layout.validate_input(value)
        self.assertIn(failure.exception.code,
                      ('DRAWING_LAYOUT_INVALID_ARGUMENT', 'DRAWING_LAYOUT_TOO_LARGE'))

    def test_five_kinds_are_deterministic_complete_detached_and_not_native_claims(self):
        value = source(True)
        result = self.check(value)
        self.assertTrue(result['can_apply'])
        self.assertEqual([r['kind'] for r in result['dimensions']],
                         ['DistanceX', 'Distance', 'DistanceY', 'Radius', 'Diameter'])
        self.assertEqual(result, layout.suggest_layout(deepcopy(value)))
        self.assertEqual(result['layout_verification'], 'suggested_only')
        self.assertEqual(result['blocked'], [])
        self.assertNotIn('projection_verification', result)
        self.assertLessEqual(result['candidates_tested'], layout.MAX_SEARCH)
        result['dimensions'][0]['text'] = 'changed detached result'
        self.assertEqual(value['dimensions'][0]['text'], '40.00')

    def test_narrow_reserved_band_resolves_all_five_without_smaller_labels_or_larger_budgets(self):
        for transpose in (False, True):
            with self.subTest(transpose=transpose):
                value = narrow_band_sheet(transpose)
                original = deepcopy(value)
                # The original normal lanes omit the tangential free band.
                with patch.object(layout, '_boundary_centers', return_value=[]):
                    old = self.check(value)
                self.assertFalse(old['can_apply'])
                self.assertEqual(old['dimensions'], [])
                self.assertEqual(old['blocked'][0]['reason'], 'NO_SPACE')
                result = self.check(value)
                self.assertTrue(result['can_apply'])
                self.assertEqual(len(result['dimensions']), 5)
                self.assertEqual(value, original)
                self.assertEqual(result, layout.suggest_layout(value))
                self.assertEqual([len(layout._centers(row, value)) for row in value['dimensions']],
                                 [64, 64, 64, 48, 48])
                self.assertEqual((layout.MAX_CANDIDATES, layout.MAX_SEARCH, layout.MAX_GEOMETRY_CHECKS),
                                 (64, 8192, 131072))

    def test_boundary_seeds_preserve_original_lanes_and_oblique_reference_construction(self):
        for kind, start, end in (('DistanceX', [80, 100], [120, 100]),
                                 ('DistanceY', [80, 100], [80, 140]),
                                 ('Distance', [80, 100], [120, 130]),
                                 ('Distance', [80, 100], [110, 140])):
            with self.subTest(kind=kind, end=end):
                value = source()
                row = line(kind=kind, start=start, end=end)
                value['dimensions'] = [row]
                with patch.object(layout, '_boundary_centers', return_value=[]):
                    original = layout._centers(row, value)
                current = layout._centers(row, value)
                self.assertEqual(len(original), 60)
                self.assertEqual(current[:60], original)
                self.assertEqual(current[60:], layout._boundary_centers(row, value))
                self.assertEqual(len(current), 64)
                result = self.check(value)
                self.assertTrue(result['can_apply'])
        value['dimensions'][0] = explicit(row, -40, -40)
        self.assertEqual(layout._centers(value['dimensions'][0], value), [[60, 80]])

    def test_multiple_reserved_boundaries_are_order_independent_and_keep_hard_clearance(self):
        value = source()
        value['paper']['reserved_rects'] = [[0, 0, 297, 40], [0, 40, 297, 50],
                                            [0, 185, 297, 210]]
        row = value['dimensions'][0]
        points = layout._boundary_centers(row, value)
        self.assertEqual(len(points), 4)
        lower, upper = points[:2], points[2:]
        for point in lower:
            self.assertAlmostEqual(point[1], 53.5 + layout._EPS * 4)
        for point in upper:
            self.assertAlmostEqual(point[1], 181.5 - layout._EPS * 4)
        value['paper']['reserved_rects'].reverse()
        self.assertEqual(layout._boundary_centers(row, value), points)
        for point in points:
            candidate = layout._candidate(row, point, value['view']['origin_mm'], 1)
            self.assertIsNone(layout._static_conflict(candidate, value))
        self.assertTrue(self.check(value)['can_apply'])

    def test_explicit_row_stays_exact_even_when_it_is_after_auto_in_request(self):
        value = source(True)
        old = self.check(value)['dimensions'][-1]
        value['dimensions'][-1] = explicit(value['dimensions'][-1], old['x_mm'], old['y_mm'])
        result = self.check(value)
        self.assertTrue(result['can_apply'])
        self.assertEqual(result['dimensions'][-1]['placement'], 'explicit')
        for key in ('x_mm', 'y_mm'):
            self.assertEqual(result['dimensions'][-1][key], old[key])

    def test_all_explicit_valid_plan_is_supported_without_moving_rows(self):
        value = source()
        value['dimensions'][0] = explicit(value['dimensions'][0], 0, 30)
        result = self.check(value)
        self.assertTrue(result['can_apply'])
        self.assertEqual(result['candidates_tested'], 0)
        self.assertIs(type(result['dimensions'][0]['x_mm']), int)

    def test_paper_margin_title_region_and_existing_view_are_obstacles(self):
        value = source()
        value['obstacles']['rects'] = [{'name': 'FrontView', 'kind': 'view',
                                         'bounds_mm': [70, 143, 130, 200]}]
        result = self.check(value)
        self.assertTrue(result['can_apply'])
        box = result['dimensions'][0]['label_box_mm']
        self.assertLess(box[3], 100)
        self.assertGreater(box[1], 50)
        for row in result['dimensions']:
            x0, y0, x1, y1 = row['label_box_mm']
            self.assertGreaterEqual(x0, 10)
            self.assertGreaterEqual(y0, 10)
            self.assertLessEqual(x1, 287)
            self.assertLessEqual(y1, 200)

    def test_old_label_and_old_line_force_another_candidate(self):
        value = source()
        value['obstacles']['rects'] = [{'name': 'ExistingDimension', 'kind': 'dimension',
                                         'bounds_mm': [90, 145, 110, 155]}]
        value['obstacles']['segments'] = [[[70, 156], [130, 156]]]
        result = self.check(value)
        self.assertTrue(result['can_apply'])
        self.assertNotEqual(result['dimensions'][0]['y_mm'], 28.5)
        self.assertEqual(value['obstacles']['rects'][0]['bounds_mm'], [90, 145, 110, 155])

    def test_reserved_whole_paper_has_no_partial_writable_result(self):
        value = source(True)
        value['paper']['reserved_rects'] = [[0, 0, 297, 210]]
        result = self.check(value)
        self.assertFalse(result['can_apply'])
        self.assertEqual(result['dimensions'], [])
        self.assertEqual(result['blocked'], [{'index': 0, 'reason': 'NO_SPACE'}])

    def test_explicit_outside_paper_or_reserved_or_model_is_not_repositioned(self):
        for x, y, reason in ((0, 200, 'OUTSIDE_PAPER'), (0, -80, 'RESERVED_REGION'),
                             (0, 0, 'LABEL_COLLISION')):
            value = source()
            value['dimensions'][0] = explicit(value['dimensions'][0], x, y)
            result = self.check(value)
            self.assertFalse(result['can_apply'])
            self.assertEqual(result['blocked'][0]['reason'], reason)
            self.assertEqual(result['dimensions'], [])

    def test_old_line_intersection_is_blocked_even_when_label_is_clear(self):
        value = source()
        value['dimensions'][0] = explicit(value['dimensions'][0], 0, 30)
        value['obstacles']['segments'] = [[[75, 145], [85, 145]]]
        result = self.check(value)
        self.assertEqual(result['blocked'][0]['reason'], 'LINE_COLLISION')

    def test_two_locked_labels_cannot_overlap(self):
        value = source()
        value['dimensions'] = [explicit(line(), 0, 30), explicit(line(kind='Distance'), 0, 30)]
        result = self.check(value)
        self.assertEqual(result['blocked'], [{'index': 1, 'reason': 'LABEL_COLLISION'}])

    def test_crossing_new_extensions_are_not_ignored_as_model_contacts(self):
        value = source()
        value['dimensions'] = [explicit(line(), 0, 30),
                               explicit(line('Edge1', 'DistanceY', [80, 100], [80, 140]), 45, 0)]
        result = self.check(value)
        self.assertEqual(result['blocked'], [{'index': 1, 'reason': 'LINE_COLLISION'}])

    def test_shared_linear_reference_allows_bounded_cascade_joins(self):
        value = source()
        value['dimensions'] += [line(kind='Distance')]
        result = self.check(value)
        self.assertTrue(result['can_apply'])
        self.assertNotEqual(result['dimensions'][0]['y_mm'], result['dimensions'][1]['y_mm'])

    def test_short_edge_outside_text_allows_exact_shared_reference_projection_join(self):
        value = rectangle_sheet(.1)
        value['dimensions'] = [explicit(value['dimensions'][0], -17, -11.5),
                               explicit(value['dimensions'][1], -17, -20.5)]
        result = self.check(value)
        self.assertTrue(result['can_apply'])
        # The join is inside the extended line, not at a segment endpoint.
        self.assertIn([[116.0, 133.5], [128.5, 133.5]], result['dimensions'][0]['segments_mm'])

    def test_reference_projection_exception_rejects_oblique_collinear_near_miss_and_other_anchor(self):
        left = {'anchors': ((0, 0), (0, 1)), 'circle': None}
        right = {'anchors': ((0, 0), (1, 0)), 'circle': None}
        extension = ('extension', [[0, 0], [0, -10]], (0, 0))
        dimension = ('dimension', [[-5, -5], [5, -5]], None)
        self.assertTrue(layout._shared_extension(extension, dimension, left, right))
        invalid = [
            (('extension', [[0, 0], [.2, -10]], (0, 0)), dimension),
            (('extension', [[-5, -5], [5, -5]], (0, 0)), dimension),
            (('extension', [[.001, 0], [.001, -10]], (0, 0)), dimension),
            (('extension', [[0, 0], [0, -10]], (1, 1)), dimension),
            (extension, ('dimension', [[1, -5], [5, -5]], None)),
            (('extension', [[0, 0], [0, -10]], (1, 0)), dimension),
        ]
        for a, b in invalid:
            self.assertFalse(layout._shared_extension(a, b, left, right))

    def test_same_circle_radius_diameter_can_meet_at_their_reference_centre(self):
        value = source()
        value['dimensions'] = [circle(), circle(kind='Diameter')]
        result = self.check(value)
        self.assertTrue(result['can_apply'])
        self.assertNotEqual(result['dimensions'][0]['label_box_mm'], result['dimensions'][1]['label_box_mm'])

    def test_different_edge_same_circle_has_no_shared_contact_exemption(self):
        value = source()
        value['dimensions'] = [explicit(circle(), 35, 0),
                               explicit(circle('Edge3', 'Diameter'), 0, 35)]
        result = self.check(value)
        self.assertEqual(result['blocked'], [{'index': 1, 'reason': 'LINE_COLLISION'}])

    def test_same_circle_external_overlap_is_still_rejected(self):
        value = source()
        value['dimensions'] = [explicit(circle(), 35, 0),
                               explicit(circle(kind='Diameter'), 60, 0)]
        result = self.check(value)
        self.assertFalse(result['can_apply'])
        self.assertIn(result['blocked'][0]['reason'], ('LABEL_COLLISION', 'LINE_COLLISION'))

    def test_diagonal_line_uses_its_normal_and_json_round_trip_remains_valid(self):
        value = source()
        value['view']['origin_mm'] = [100.1, 120.3]
        value['dimensions'] = [line(kind='Distance', start=[80.1, 100.3], end=[119.2, 138.4])]
        result = self.check(value)
        self.assertTrue(result['can_apply'])
        self.assertTrue(all(math.isfinite(r[k]) for r in result['dimensions'] for k in ('x_mm', 'y_mm')))

    def test_label_sizes_and_paper_coordinates_are_not_model_unit_values(self):
        for scale in (1, .5, .1, .05):
            value = source()
            value['view']['bounds_mm'] = [100 - 20 * scale, 120 - 20 * scale,
                                           100 + 20 * scale, 120 + 20 * scale]
            value['dimensions'] = [line(start=[100 - 20 * scale, 120 - 20 * scale],
                                        end=[100 + 20 * scale, 120 - 20 * scale])]
            result = self.check(value)
            self.assertTrue(result['can_apply'])
            row = result['dimensions'][0]
            self.assertAlmostEqual(row['label_box_mm'][2] - row['label_box_mm'][0], 14)
            self.assertAlmostEqual(row['y_mm'] - 20 * scale, 8.5)

    def test_full_five_kind_rectangle_with_title_region_and_other_view_at_four_scales(self):
        for scale in (1, .5, .1, .05):
            with self.subTest(scale=scale):
                value = rectangle_sheet(scale)
                result = self.check(value)
                self.assertTrue(result['can_apply'])
                self.assertEqual(len(result['dimensions']), 5)
                self.assertLess(result['candidates_tested'], layout.MAX_SEARCH)

    def test_one_and_eight_items_are_allowed_and_nine_are_not(self):
        value = source()
        self.assertTrue(self.check(value)['can_apply'])
        value['dimensions'] = [circle('Edge' + str(i)) for i in range(8)]
        self.assertEqual(len(layout.validate_input(value)['dimensions']), 8)
        value['dimensions'].append(circle('Edge8'))
        self.reject(value)

    def test_eight_requested_rows_can_use_distinct_lanes_in_one_complete_result(self):
        value = source()
        value['dimensions'] = [line('Edge' + str(i)) for i in range(8)]
        result = self.check(value)
        self.assertTrue(result['can_apply'])
        self.assertEqual(len(result['dimensions']), 8)
        self.assertEqual(len({(r['x_mm'], r['y_mm']) for r in result['dimensions']}), 8)
        self.assertEqual([r['edge'] for r in result['dimensions']], ['Edge' + str(i) for i in range(8)])

    def test_bad_or_duplicate_edges_kinds_and_zero_measurement_are_rejected(self):
        for edge in ('Edge00', 'Edge01', 'Edge2048', 'Edge1\n', 'Edge-1', 'Edge 1'):
            value = source(); value['dimensions'][0]['edge'] = edge; self.reject(value)
        for kind in ('Angle', 'distance', 'Radius', True):
            value = source(); value['dimensions'][0]['kind'] = kind; self.reject(value)
        value = source(); value['dimensions'].append(deepcopy(value['dimensions'][0])); self.reject(value)
        value = source(); value['dimensions'][0]['geometry']['end_mm'] = [80, 100]; self.reject(value)
        value = source(); value['dimensions'][0]['geometry']['end_mm'] = [80, 140]; self.reject(value)
        value = source(); value['dimensions'][0] = circle(); value['dimensions'][0]['geometry']['radius_mm'] = 0; self.reject(value)

    def test_exact_keys_lists_and_geometry_binding_are_required(self):
        original = source()
        paths = [(), ('paper',), ('view',), ('obstacles',), ('spacing',), ('dimensions', 0),
                 ('dimensions', 0, 'geometry')]
        for path in paths:
            target = original
            for part in path: target = target[part]
            for key in target:
                value = deepcopy(original); current = value
                for part in path: current = current[part]
                current.pop(key)
                self.reject(value)
            value = deepcopy(original); current = value
            for part in path: current = current[part]
            current['unknown'] = 1
            self.reject(value)
        value = source(); value['view']['origin_mm'] = (100, 120); self.reject(value)
        value = source(); value['dimensions'][0]['x_mm'] = 0; self.reject(value)
        value = source(); value['dimensions'][0]['geometry']['end_mm'] = [130, 100]; self.reject(value)

    def test_numbers_and_strings_are_not_coerced(self):
        class FloatSubclass(float): pass
        for invalid in (True, False, None, '1', float('nan'), float('inf'), 10 ** 1000, FloatSubclass(1)):
            value = source(); value['paper']['width_mm'] = invalid; self.reject(value)
            value = source(); value['dimensions'][0]['label_size_mm'][0] = invalid; self.reject(value)
        for invalid in ('', ' bad', 'bad/path', 'bad\n', 'bad\u200b'):
            value = source(); value['view']['name'] = invalid; self.reject(value)
        for invalid in ('', 'x\x00', 'x\u200b', '\n' * 4, 'x' * 257):
            value = source(); value['dimensions'][0]['text'] = invalid; self.reject(value)
        value = source(); value['dimensions'][0]['text'] = '⌀10.00\nmm'; layout.validate_input(value)

    def test_untrusted_scalars_never_receive_comparison_hash_or_attribute_calls(self):
        calls, state = [], {'armed': False}
        class Forbidden:
            def __eq__(self, other): calls.append('eq'); raise AssertionError('comparison')
            def __hash__(self):
                if state['armed']: calls.append('hash'); raise AssertionError('hash')
                return hash('placement')
            def __getattribute__(self, name): calls.append('attribute'); raise AssertionError('attribute')
        bad = Forbidden()
        paths = [('version',), ('paper', 'width_mm'), ('view', 'name'),
                 ('dimensions', 0, 'placement'), ('dimensions', 0, 'geometry', 'curve'),
                 ('dimensions', 0, 'text'), ('dimensions', 0, 'kind'), ('dimensions', 0, 'edge')]
        for path in paths:
            value = source(); current = value
            for part in path[:-1]: current = current[part]
            current[path[-1]] = bad
            self.reject(value)
        value = source(); value['dimensions'][0] = {bad: 'auto'}
        state['armed'] = True
        self.reject(value)
        self.assertEqual(calls, [])
        result = layout.suggest_layout(source())
        for key in ('layout_verification', 'algorithm', 'can_apply'):
            modified = deepcopy(result); modified[key] = bad
            with self.assertRaises(layout.PftDrawingLayoutError): layout.validate_result(modified)
        self.assertEqual(calls, [])

    def test_limits_on_obstacles_names_and_plain_builtins(self):
        value = source(); value['obstacles']['rects'] = [{'name': 'Same', 'kind': 'view', 'bounds_mm': [1, 1, 2, 2]}] * 2; self.reject(value)
        value = source(); value['obstacles']['rects'] = [{'name': 'TopView', 'kind': 'view', 'bounds_mm': [1, 1, 2, 2]}]; self.reject(value)
        value = source(); value['obstacles']['segments'] = [[[1, 1], [2, 2]]] * 513; self.reject(value)
        value = source(); value['paper']['reserved_rects'] *= 33; self.reject(value)
        class DictSubclass(dict): pass
        class ListSubclass(list): pass
        self.reject(DictSubclass(source()))
        value = source(); value['dimensions'] = ListSubclass(value['dimensions']); self.reject(value)

    def test_input_byte_budget_counts_actual_utf8_not_character_count(self):
        value = source()
        value['obstacles']['rects'] = [{'name': '中' * 60 + str(i), 'kind': 'view',
                                        'bounds_mm': [-999999, -999999, -999998, -999998]} for i in range(128)]
        value['obstacles']['segments'] = [[[.1234567890123456, .2345678901234567],
                                           [.3456789012345678, .4567890123456789]]] * 512
        with self.assertRaises(layout.PftDrawingLayoutError) as failure: layout.validate_input(value)
        self.assertEqual(failure.exception.code, 'DRAWING_LAYOUT_TOO_LARGE')

    def test_real_unsatisfiable_reference_contacts_backtrack_without_partial_results(self):
        value = source()
        value['dimensions'] = [circle('Edge0'), circle('Edge1')]
        result = self.check(value)
        self.assertFalse(result['can_apply'])
        self.assertEqual(result['dimensions'], [])
        self.assertEqual(result['blocked'][0]['reason'], 'NO_SPACE')
        # Both rows have real geometric candidates, but different-edge leaders
        # all meet at the centre. Forward checking rejects each first-row choice.
        self.assertGreater(result['candidates_tested'], 96)
        self.assertLess(result['candidates_tested'], layout.MAX_SEARCH)

    def test_forward_checking_prunes_three_incompatible_circle_rows(self):
        value = source()
        value['paper'].update(width_mm=500, height_mm=500, reserved_rects=[])
        value['view'].update(origin_mm=[250, 250], bounds_mm=[240, 240, 260, 260])
        value['dimensions'] = [circle('Edge0', center=[250, 250]),
                               circle('Edge0', 'Diameter', center=[250, 250]),
                               circle('Edge1', center=[250, 250])]
        result = self.check(value)
        self.assertEqual(result['dimensions'], [])
        self.assertLess(result['candidates_tested'], 512)
        self.assertEqual(result['blocked'][0]['reason'], 'NO_SPACE')

    def test_real_search_limit_is_reached_by_eight_rows_in_a_crowded_limited_strip(self):
        value = source()
        value['paper'].update(height_mm=196, reserved_rects=[[10, 10, 287, 95]])
        value['dimensions'] = [line('Edge' + str(i)) for i in range(8)]
        result = self.check(value)
        self.assertEqual(result['dimensions'], [])
        self.assertEqual(result['candidates_tested'], layout.MAX_SEARCH)
        self.assertEqual(result['blocked'][0]['reason'], 'SEARCH_BUDGET_EXHAUSTED')

    def test_real_geometry_budget_bounds_heavy_obstacles_and_eight_rows(self):
        value = source()
        value['dimensions'] = [circle('Edge' + str(i)) for i in range(8)]
        value['obstacles']['rects'] = [{'name': 'Obstacle' + str(i), 'kind': 'view',
                                        'bounds_mm': [1, 1, 2, 2]} for i in range(128)]
        value['obstacles']['segments'] = [[[1, 1], [2, 2]]] * 512
        budgets, original = [], layout._Budget
        class ObservedBudget(original):
            def __init__(self): super().__init__(); budgets.append(self)
        with patch.object(layout, '_Budget', ObservedBudget):
            result = self.check(value)
        self.assertEqual(budgets[0].checks, layout.MAX_GEOMETRY_CHECKS)
        self.assertLess(result['candidates_tested'], layout.MAX_SEARCH)
        self.assertEqual(result['blocked'][0]['reason'], 'SEARCH_BUDGET_EXHAUSTED')
        self.assertEqual(result['dimensions'], [])

    def test_result_cannot_change_intent_bounds_routes_or_explicit_coordinates(self):
        value = source(True)
        result = self.check(value)
        for key, changed in (('edge', 'Edge99'), ('kind', 'Radius'), ('placement', 'explicit'),
                             ('text', 'forged'), ('x_mm', result['dimensions'][0]['x_mm'] + 1), ('label_box_mm', [1, 1, 2, 2]),
                             ('segments_mm', [])):
            bad = deepcopy(result); bad['dimensions'][0][key] = changed
            with self.assertRaises(layout.PftDrawingLayoutError): layout.validate_result(bad, expected=value)
        bad = deepcopy(result); bad['dimensions'].reverse()
        with self.assertRaises(layout.PftDrawingLayoutError): layout.validate_result(bad, expected=value)
        value = source(); value['dimensions'][0] = explicit(value['dimensions'][0], 0, 30)
        bad = self.check(value); bad['dimensions'][0]['x_mm'] = 1
        with self.assertRaises(layout.PftDrawingLayoutError): layout.validate_result(bad, expected=value)

    def test_blocked_result_never_carries_partial_rows_or_unknown_reasons(self):
        good = self.check(source())
        for changes in ({'can_apply': False}, {'blocked': [{'index': 0, 'reason': 'NO_SPACE'}]},
                        {'layout_verification': 'collision_free'}, {'algorithm': 'unreviewed'},
                        {'candidates_tested': True}, {'candidates_tested': layout.MAX_SEARCH + 1}):
            bad = deepcopy(good); bad.update(changes)
            with self.assertRaises(layout.PftDrawingLayoutError): layout.validate_result(bad)
        value = source(); value['paper']['reserved_rects'] = [[0, 0, 297, 210]]
        blocked = self.check(value)
        for key, changed in (('reason', 'untrusted text'), ('index', 1), ('index', True)):
            bad = deepcopy(blocked); bad['blocked'][0][key] = changed
            with self.assertRaises(layout.PftDrawingLayoutError): layout.validate_result(bad, expected=value)

    def test_tiny_segments_and_near_endpoint_trimming_do_not_divide_by_zero(self):
        self.assertEqual(layout._point_distance([0, 1], [[0, 0], [1e-200, 0]]), 1)
        self.assertEqual(layout._point_distance([0, 1], [[0, 0], [0, 0]]), 1)
        value = source()
        value['obstacles']['segments'] = [[[1e-200, 1e-200], [2e-200, 2e-200]]]
        result = self.check(value)
        self.assertTrue(result['can_apply'])
        self.assertTrue(all(segment[0] != segment[1] for row in result['dimensions'] for segment in row['segments_mm']))

    def test_positive_label_size_that_rounds_to_zero_extent_is_conservatively_blocked(self):
        value = source()
        value['dimensions'][0]['label_size_mm'] = [5e-324, 5e-324]
        result = self.check(value)
        self.assertFalse(result['can_apply'])
        self.assertEqual(result['dimensions'], [])
        self.assertEqual(result['blocked'][0]['reason'], 'NO_SPACE')


if __name__ == '__main__':
    unittest.main()
