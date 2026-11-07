# SPDX-License-Identifier: Apache-2.0
"""Real pure layout plans plus injected native reads, through business approval."""
from copy import deepcopy
import types
import unittest
from unittest.mock import Mock, patch

import test_pft_drawing_dimensions_approval as dimension_fixtures
from test_pft_drawing_layout_plan import source as neutral_source
from test_pft_part_approval import ForbiddenNative
import test_pft_business as fixtures

business, Error = fixtures.business, fixtures.Error


class LayoutApprovalTests(unittest.TestCase):
    make_plan = dimension_fixtures.DimensionApprovalTests.make_plan
    make_result = dimension_fixtures.DimensionApprovalTests.make_result
    open_view = dimension_fixtures.DimensionApprovalTests.open_view
    request = dimension_fixtures.DimensionApprovalTests.request
    status = dimension_fixtures.DimensionApprovalTests.status
    assert_error = dimension_fixtures.DimensionApprovalTests.assert_error

    def setUp(self):
        dimension_fixtures.DimensionApprovalTests.setUp(self)
        self.layout = business._layout()
        self.source = neutral_source()
        self.source['view']['name'] = self.front.Name
        self.context = {'input': self.source}
        self.native = types.SimpleNamespace(snapshot=Mock(return_value=self.context), verify=Mock(), verify_defaults=Mock())
        original = self.layout._sibling
        context = patch.object(self.layout, '_sibling', side_effect=lambda name:
            self.native if name == 'pft_drawing_layout_profile' else original(name))
        context.start(); self.addCleanup(context.stop)
        self.dimensions.prepare_plan.side_effect = self.native_plan
        self.dimensions.apply_plan.side_effect = self.native_apply

    def native_plan(self, *args):
        plan, rows = self.make_plan(*args)
        for row in rows: row['new'] = row['value_mm'] = 40.
        plan['names'] = tuple(row['object'] for row in rows)
        plan['values'] = tuple(row['value_mm'] for row in rows)
        return plan, rows

    def native_apply(self, app, gui, document, plan, controller, *, layout_plan=None):
        self.assertIsNotNone(layout_plan)
        self.layout.verify(app, gui, layout_plan, controller)
        document.events.append('synthetic_dimension_write')
        return dimension_fixtures.dimension_result(plan['request'], plan['rows'])

    def prepare(self, **changes):
        params = {'docName': self.doc.Name, 'objectNames': [self.page.Name], 'viewName': self.front.Name,
            'operation': 'add_drawing_dimensions', 'dimensions': [{'edge': 'Edge0', 'kind': 'DistanceX', 'placement': 'auto'}]}
        params.update(changes)
        return business.perform('prepare_part_changes', params, self.app, self.gui, Error)

    def test_prepare_native_confirmation_applies_exact_frozen_resolution_once(self):
        preview = self.prepare(); token = preview['token']
        self.assertTrue(preview['can_apply'])
        self.assertEqual(preview['layout']['intent']['dimensions'][0]['placement'], 'auto')
        self.assertEqual(preview['design_request']['dimensions'][0]['x_mm'], 0)
        stored = business._plans[token]['plan']['layout_plan']
        self.assert_error('DIMENSION_NATIVE_CONFIRMATION_REQUIRED', lambda:
            business.perform('apply_part_changes', {'token': token}, self.app, self.gui, Error))
        self.assertEqual(self.request(token)['state'], 'awaiting_confirmation')
        self.assertEqual(self.views[-1].preview, preview)
        self.dimensions.apply_plan.assert_not_called()
        self.assertEqual(self.doc.events, [])
        state = self.views[-1].confirm()
        self.assertEqual(state['state'], 'applied')
        self.assertEqual(state['result']['design_result']['dimensions'][0]['y_mm'], preview['design_request']['dimensions'][0]['y_mm'])
        self.assertIs(self.dimensions.apply_plan.call_args.kwargs['layout_plan'], stored)
        self.views[-1].confirm()
        self.dimensions.apply_plan.assert_called_once()
        self.assertFalse({'plan', 'public_preview', 'objects'} & set(business._plans[token]))
        self.assertEqual(self.status(token)['state'], 'applied')

    def test_mixed_intent_keeps_explicit_position_while_resolving_all_rows(self):
        extra = deepcopy(self.source['dimensions'][0]); extra.update(kind='Distance', placement='explicit', x_mm=0, y_mm=50)
        self.source['dimensions'].append(extra)
        preview = self.prepare(dimensions=[{'edge': 'Edge0', 'kind': 'DistanceX', 'placement': 'auto'},
            {'edge': 'Edge0', 'kind': 'Distance', 'x_mm': 0, 'y_mm': 50}])
        self.assertTrue(preview['can_apply'])
        self.assertEqual(preview['design_request']['dimensions'][1], {'edge': 'Edge0', 'kind': 'Distance', 'x_mm': 0, 'y_mm': 50})
        self.request(preview['token']); self.assertEqual(self.views[-1].confirm()['state'], 'applied')

    def test_solver_no_space_has_complete_conflict_graphics_but_never_native_write_plan(self):
        self.source['paper']['reserved_rects'] = [[0, 0, 297, 210]]
        preview = self.prepare()
        self.assertFalse(preview['can_apply']); self.assertIsNone(preview['design_request'])
        self.assertEqual(preview['layout']['graphics']['result']['dimensions'], [])
        self.assertTrue(preview['layout']['graphics']['result']['blocked'])
        self.assertEqual(self.request(preview['token'])['error_code'], 'PART_PREVIEW_BLOCKED')
        self.assertEqual(self.views, [])
        self.dimensions.prepare_plan.assert_not_called(); self.dimensions.apply_plan.assert_not_called()
        self.assertEqual(self.doc.events, [])

    def test_unavailable_native_profile_is_fixed_blocked_without_fabricated_inputs(self):
        class ProfileFailure(RuntimeError): code = 'DRAWING_LAYOUT_PAGE_OPEN_REQUIRED'
        self.native.snapshot.side_effect = ProfileFailure('raw diagnostic')
        preview = self.prepare()
        self.assertFalse(preview['can_apply']); self.assertIsNone(preview['design_request'])
        self.assertIsNone(preview['layout']['graphics'])
        self.assertEqual(preview['layout']['reason_code'], ProfileFailure.code)
        self.assertNotIn('diagnostic', str(preview))
        self.assertEqual(self.request(preview['token'])['error_code'], 'PART_PREVIEW_BLOCKED')
        self.assertEqual(self.views, [])

    def test_unknown_native_cleanup_refuses_token_and_is_not_generic_blocked(self):
        class ProfileFailure(RuntimeError): cleanup_state = 'unknown'
        self.native.snapshot.side_effect = ProfileFailure('hidden')
        self.assert_error('DIMENSION_LAYOUT_CLEANUP_UNVERIFIED', self.prepare)
        self.assertEqual(len(business._plans), 0)
        self.dimensions.prepare_plan.assert_not_called()

    def test_native_plan_reader_cannot_change_solver_resolution(self):
        def replace(*args):
            plan, rows = self.native_plan(*args)
            plan['request']['dimensions'][0]['y_mm'] += 1
            return plan, rows
        self.dimensions.prepare_plan.side_effect = replace
        self.assert_error('DIMENSION_RESULT_INVALID', self.prepare)
        self.assertEqual(len(business._plans), 0)

    def test_prepare_catches_callback_mutation_after_native_dimension_read(self):
        def replace(*args):
            result = self.native_plan(*args)
            self.context['input']['spacing']['lane_mm'] += 1
            return result
        self.dimensions.prepare_plan.side_effect = replace
        preview = self.prepare()
        self.assertFalse(preview['can_apply']); self.assertIsNone(preview['design_request'])
        self.assertIsNone(preview['layout']['graphics'])
        self.dimensions.apply_plan.assert_not_called()

    def test_stale_public_or_private_layout_never_opens_native_approval(self):
        for mode in ('public', 'resolved', 'missing', 'native_request', 'rows'):
            preview = self.prepare(); record = business._plans[preview['token']]
            if mode == 'public': record['public_preview']['layout']['graphics']['input']['paper']['margin_mm'] += 1
            elif mode == 'resolved': record['plan']['dimensions_input']['dimensions'][0]['y_mm'] += 1
            elif mode == 'missing': del record['plan']['layout_plan']
            elif mode == 'native_request': record['plan']['dimensions_plan']['request']['dimensions'][0]['y_mm'] += 1
            else: record['public_preview']['rows'][0]['y_mm'] += 1
            with self.subTest(mode=mode):
                state = self.request(preview['token'])
                self.assertEqual((state['state'], state['error_code']), ('failed', 'DIMENSION_LAYOUT_STALE'))
            self.assertEqual(self.views, [])
        self.dimensions.apply_plan.assert_not_called()

    def test_dimension_verify_callback_cannot_replace_public_diagram(self):
        preview = self.prepare(); record = business._plans[preview['token']]
        def mutate(*args): record['public_preview']['layout']['graphics']['input']['spacing']['lane_mm'] += 1
        self.dimensions.verify_plan.side_effect = mutate
        self.assertEqual(self.request(preview['token'])['error_code'], 'DIMENSION_LAYOUT_STALE')
        self.assertEqual(self.views, [])

    def test_late_unknown_cleanup_is_terminal_and_status_does_not_touch_native(self):
        preview = self.prepare(); self.request(preview['token'])
        failure = dimension_fixtures.DimensionError('DIMENSION_LAYOUT_STYLE_UNVERIFIED'); failure.cleanup_state = 'unknown'
        self.dimensions.apply_plan.side_effect = failure
        failed = self.views[-1].confirm()
        self.assertEqual((failed['state'], failed['error_code']), ('failed', 'DIMENSION_LAYOUT_CLEANUP_UNVERIFIED'))
        count = self.dimensions.apply_plan.call_count
        self.views[-1].confirm(); self.assertEqual(self.dimensions.apply_plan.call_count, count)
        with patch.object(business, '_mvc', side_effect=AssertionError('status reads native')):
            self.assertEqual(self.status(preview['token'])['error_code'], 'DIMENSION_LAYOUT_CLEANUP_UNVERIFIED')

    def test_cancelled_layout_drops_private_context_and_never_applies(self):
        preview = self.prepare(); self.request(preview['token']); self.views[-1].cancel()
        self.assertEqual(self.views[-1].confirm()['state'], 'cancelled')
        self.assertFalse({'plan', 'public_preview'} & set(business._plans[preview['token']]))
        self.dimensions.apply_plan.assert_not_called()

    def test_complete_layout_preview_budget_refuses_before_creating_token(self):
        with patch.object(business, 'MAX_PREVIEW_BYTES', 1024):
            self.assert_error('PART_PREVIEW_TOO_LARGE', self.prepare)
        self.assertEqual(len(business._plans), 0)
        self.assertEqual(self.doc.events, [])

    def test_last_native_callback_cannot_downgrade_layout_or_replace_shown_measurement(self):
        for target in ('policy', 'public_rows', 'native_names', 'native_values'):
            self.native.verify.side_effect = None
            preview = self.prepare(); self.request(preview['token'])
            record = business._plans[preview['token']]; calls = []
            def mutate(*args):
                calls.append(True)
                if len(calls) != 2: return
                if target == 'policy': record['layout_required'] = False
                elif target == 'public_rows':
                    row = record['public_preview']['rows'][0]; row['new'] = row['value_mm'] = 41.
                elif target == 'native_names': record['plan']['dimensions_plan']['names'] = ('OtherDimension',)
                else: record['plan']['dimensions_plan']['values'] = (41.,)
            self.native.verify.side_effect = mutate
            with self.subTest(target=target):
                state = self.views[-1].confirm()
                self.assertEqual((state['state'], state['error_code']), ('failed', 'DIMENSION_LAYOUT_STALE'))
            self.assertEqual(self.dimensions.apply_plan.call_count, 0)

    def test_transaction_error_unknown_cleanup_remains_explicit_in_status_and_rpc(self):
        preview = self.prepare(); self.request(preview['token'])
        _, controller = business._mvc()
        failure = controller.PftWriteError('PART_ROLLBACK_UNVERIFIED', 'Synthetic transaction failure')
        failure.cleanup_state = 'unknown'
        self.dimensions.apply_plan.side_effect = failure
        state = self.views[-1].confirm()
        self.assertEqual(state['error_code'], 'DIMENSION_LAYOUT_CLEANUP_UNVERIFIED')
        self.assertEqual(self.status(preview['token'])['error_code'], 'DIMENSION_LAYOUT_CLEANUP_UNVERIFIED')


if __name__ == '__main__': unittest.main()
