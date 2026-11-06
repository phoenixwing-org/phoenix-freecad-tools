# SPDX-License-Identifier: Apache-2.0
"""Neutral dimension lifecycle doubles; no native host, files or account state."""
from copy import deepcopy
import json
import types
import unittest
from unittest.mock import Mock, patch

import test_pft_business as fixtures
from test_pft_part_approval import Presenter, ForbiddenNative

business, Error = fixtures.business, fixtures.Error


class DimensionError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__('Fixed synthetic dimension failure')


def dimension_result(request, rows):
    return {'kind': 'drawing_dimensions', 'page': request['page'], 'view': request['view'],
            'dimensions': [{'name': row['object'], **{key: row[key] for key in ('edge', 'kind', 'value_mm', 'x_mm', 'y_mm')}}
                           for row in rows],
            'measurement_verification': 'native_reference_and_value_checked',
            'projection_verification': 'not_checked', 'manual_review_required': True}


class DimensionApprovalTests(unittest.TestCase):
    def setUp(self):
        fixtures.BusinessTests.setUp(self)
        self.page, self.front = fixtures.Object('Page', 'Neutral page'), fixtures.Object('Front', 'Neutral front')
        for obj in (self.page, self.front):
            obj.Document = self.doc
            self.doc.Objects.append(obj)
        self.views = []
        self.dimensions = types.SimpleNamespace(CAPABILITIES=deepcopy(business.DRAWING_DIMENSIONS_CAPABILITIES),
            PftDrawingDimensionsError=DimensionError, prepare_plan=Mock(side_effect=self.make_plan),
            verify_plan=Mock(), apply_plan=Mock(side_effect=self.make_result))
        for target, kwargs in (('_dimensions', {'return_value': self.dimensions}),
                               ('_open_approval', {'side_effect': self.open_view})):
            context = patch.object(business, target, **kwargs)
            context.start(); self.addCleanup(context.stop)

    def make_plan(self, app, gui, document, page, view, dimensions, controller):
        request = {'page': page.Name, 'view': view.Name, 'dimensions': deepcopy(dimensions)}
        rows = [{'object': 'Dimension%d' % i, 'field': 'TechDraw dimension', 'old': None, 'new': 600., 'status': 'change',
                 **item, 'value_mm': 600., 'source': 'native_cached_projected_geometry',
                 'note': 'Native cached geometry in mm; view relative paper mm offsets; projection not checked; manual review.'}
                for i, item in enumerate(dimensions)]
        return {'request': request, 'rows': rows, 'native_ref': object()}, rows

    def make_result(self, app, gui, document, plan, controller):
        document.events.append('synthetic_dimension_write')
        return dimension_result(plan['request'], plan['rows'])

    def open_view(self, *args):
        view = Presenter(*args); self.views.append(view); return view

    def prepare(self, count=1, **changes):
        params = {'docName': self.doc.Name, 'objectNames': [self.page.Name], 'viewName': self.front.Name,
                  'operation': 'add_drawing_dimensions',
                  'dimensions': [{'edge': 'Edge%d' % i, 'kind': 'Distance', 'x_mm': 0, 'y_mm': 10} for i in range(count)]}
        params.update(changes)
        return business.perform('prepare_part_changes', params, self.app, self.gui, Error)

    def request(self, token):
        return business.perform('request_part_change_approval', {'token': token}, self.app, self.gui, Error)

    def status(self, token):
        return business.perform('get_part_change_status', {'token': token}, ForbiddenNative(), ForbiddenNative(), Error)

    def assert_error(self, code, callback):
        with self.assertRaises(Error) as caught: callback()
        self.assertEqual(caught.exception.code, code)

    def test_read_only_prepare_binding_and_detached_complete_preview_without_bom(self):
        model, _ = business._mvc()
        with patch.object(model, '_snapshot', side_effect=AssertionError('dimensions must not read BOM')):
            preview = self.prepare(8)
            self.assertEqual((preview['changed_count'], preview['can_apply'], len(preview['rows'])), (8, True, 8))
            self.assertEqual(preview['design_request']['page'], self.page.Name)
            self.request(preview['token'])
        self.assertEqual(self.views[0].preview, preview)
        self.views[0].preview['design_request']['dimensions'][0]['edge'] = 'Edge99'
        self.assertEqual(business._plans[preview['token']]['public_preview']['design_request']['dimensions'][0]['edge'], 'Edge0')
        self.dimensions.apply_plan.assert_not_called()
        self.assertEqual(self.doc.events, [])
        with patch.object(business, '_mvc', side_effect=AssertionError('status imported MVC')):
            self.assertFalse(self.status(preview['token'])['native_confirmation'])

    def test_native_plan_input_cannot_mutate_the_frozen_user_request(self):
        def mutate(app, gui, document, page, view, dimensions, controller):
            dimensions[0]["edge"] = "Edge99"
            return self.make_plan(app, gui, document, page, view, dimensions, controller)
        self.dimensions.prepare_plan.side_effect = mutate
        self.assert_error("DIMENSION_RESULT_INVALID", self.prepare)
        self.assertEqual(len(business._plans), 0)
        self.assertEqual(self.doc.events, [])

    def test_direct_apply_never_approves_or_replays_dimensions(self):
        preview = self.prepare(2); token = preview['token']
        for state in ('prepared', 'awaiting_confirmation', 'applied'):
            if state == 'awaiting_confirmation': self.request(token)
            if state == 'applied':
                applied = self.views[0].confirm()
                self.assertEqual(applied['result']['changed_count'], 2)
                self.assertEqual(applied['result']['design_result']['dimensions'][0]['edge'], 'Edge0')
            self.assert_error('DIMENSION_NATIVE_CONFIRMATION_REQUIRED', lambda: business.perform(
                'apply_part_changes', {'token': token}, self.app, self.gui, Error))
            self.assertEqual(self.status(token)['state'], state)
        self.views[0].confirm(); self.views[0].cancel(); self.request(token)
        self.dimensions.apply_plan.assert_called_once()
        self.assertEqual(self.doc.events, ['synthetic_dimension_write'])
        self.assertFalse({'plan', 'objects', 'approval_grant', 'public_preview'} & set(business._plans[token]))

    def test_strict_dimension_inputs_and_legacy_field_isolation(self):
        base = {'edge': 'Edge0', 'kind': 'Distance', 'x_mm': 0., 'y_mm': 0.}
        invalid = [{'dimensions': []}, {'dimensions': [dict(base)] * 9}, {'dimensions': [dict(base), dict(base)]},
                   {'objectNames': ['Page', 'Front']}, {'values': {}}, {'bodyName': 'Body'}, {'lengthMm': 4},
                   {'confirmed': True}, {'pageName': 'Page'}]
        for key, value in [('edge', 'Edge00'), ('edge', 'Edge2048'), ('kind', 'distance'), ('x_mm', True),
                           ('y_mm', float('inf')), ('x_mm', -1000.001), ('value_mm', 600), ('FormatSpec', 'override')]:
            invalid.append({'dimensions': [{**base, key: value}]})
        for changes in invalid:
            with self.subTest(changes=changes): self.assert_error('INVALID_PARAMS', lambda: self.prepare(**changes))
        self.dimensions.prepare_plan.assert_not_called()
        for operation in ('normalize_names', 'set_properties', 'create_drawing', 'set_pad_length'):
            self.assert_error('INVALID_PARAMS', lambda: self.prepare(operation=operation))
        self.assertEqual(len(business._plans), 0)

    def test_stale_fixed_failure_and_invalid_result_are_terminal_without_retry(self):
        preview = self.prepare(); self.request(preview['token'])
        self.dimensions.verify_plan.side_effect = DimensionError('DIMENSION_PLAN_STALE')
        failed = self.views[0].confirm()
        self.assertEqual((failed['state'], failed['error_code']), ('failed', 'DIMENSION_PLAN_STALE'))
        self.dimensions.apply_plan.assert_not_called()
        self.dimensions.verify_plan.side_effect = None
        for mode in ('native', 'result'):
            preview = self.prepare(); self.request(preview['token'])
            if mode == 'native': self.dimensions.apply_plan.side_effect = DimensionError('DIMENSION_VALUE_UNVERIFIED')
            else:
                plan, rows = self.make_plan(None, None, None, self.page, self.front, preview['design_request']['dimensions'], None)
                result = dimension_result(plan['request'], rows); result['dimensions'][0]['edge'] = 'Edge1'
                self.dimensions.apply_plan.side_effect = None; self.dimensions.apply_plan.return_value = result
            state = self.views[-1].confirm()
            self.assertEqual(state['error_code'], 'DIMENSION_VALUE_UNVERIFIED' if mode == 'native' else 'DIMENSION_RESULT_INVALID')
            self.assertIsNone(state['result'])
            before = self.dimensions.apply_plan.call_count; self.views[-1].confirm()
            self.assertEqual(self.dimensions.apply_plan.call_count, before)

    def test_cancel_expiry_and_stop_keep_late_confirmation_read_only(self):
        for ending in ('cancel', 'expiry', 'stop'):
            business.begin_approval_lifecycle()
            with patch.object(business.time, 'monotonic', return_value=100.):
                preview = self.prepare(); self.request(preview['token'])
            with patch.object(business.time, 'monotonic', return_value=401. if ending == 'expiry' else 101.):
                if ending == 'cancel': self.views[-1].cancel()
                if ending == 'stop': business.cancel_pending_approvals()
                state = self.views[-1].confirm()
                self.assertEqual(state['state'], 'expired' if ending == 'expiry' else 'cancelled')
                self.assertFalse(state['native_confirmation'])
        self.dimensions.apply_plan.assert_not_called()
        self.assertEqual(self.doc.events, [])

    def test_blocked_or_malformed_preview_never_opens_or_stores_partial_success(self):
        self.dimensions.prepare_plan.side_effect = DimensionError('DIMENSION_UNSUPPORTED')
        preview = self.prepare()
        self.assertEqual((preview['can_apply'], preview['changed_count'], len(preview['rows'])), (False, 0, 1))
        self.assertEqual(self.request(preview['token'])['error_code'], 'PART_PREVIEW_BLOCKED')
        self.assertEqual(self.views, [])
        for mode in ('target', 'row', 'extra', 'boolean', 'private'):
            plan, rows = self.make_plan(None, None, None, self.page, self.front,
                [{'edge': 'Edge0', 'kind': 'Distance', 'x_mm': 0, 'y_mm': 10}], None)
            if mode == 'target': plan['request']['view'] = 'OtherView'
            if mode == 'row': rows[0]['edge'] = 'Edge1'
            if mode == 'extra': rows[0]['override'] = 7
            if mode == 'boolean': rows[0]['new'] = True
            if mode == 'private': rows[0]['note'] = '/home/synthetic-private/hidden'
            self.dimensions.prepare_plan.side_effect = None; self.dimensions.prepare_plan.return_value = (plan, rows)
            before = len(business._plans)
            self.assert_error('DIMENSION_RESULT_INVALID', self.prepare)
            self.assertEqual(len(business._plans), before)
        self.assertEqual(self.doc.events, [])

    def test_terminal_status_budget_checked_before_token_and_accepts_exact_bound(self):
        request = {'page': 'Page', 'view': 'Front', 'dimensions': [{'edge': 'Edge0', 'kind': 'Distance', 'x_mm': 0, 'y_mm': 10}]}
        _, rows = self.make_plan(None, None, None, self.page, self.front, request['dimensions'], None)
        bound = business._dimension_status_budget(self.doc.Name, request, rows, Error)
        with patch.object(business, 'MAX_STATUS_BYTES', bound-1):
            self.assert_error('PART_STATUS_TOO_LARGE', self.prepare)
        self.assertEqual(len(business._plans), 0)
        with patch.object(business, 'MAX_STATUS_BYTES', bound):
            preview = self.prepare(); self.request(preview['token']); applied = self.views[-1].confirm()
            self.assertEqual(applied['state'], 'applied')
            self.assertLessEqual(len(json.dumps(self.status(preview['token']), ensure_ascii=True, separators=(',', ':')).encode()), bound)
        self.doc.Name = '😀'*256; self.documents.clear(); self.documents[self.doc.Name] = self.doc
        before = len(business._plans)
        self.assert_error('PART_STATUS_TOO_LARGE', lambda: self.prepare(8))
        self.assertEqual(len(business._plans), before)
