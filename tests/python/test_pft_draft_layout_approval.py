# SPDX-License-Identifier: Apache-2.0
"""New-draft layout binding and native-only approval with neutral native mocks."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import test_pft_drawing_approval as legacy
from test_pft_part_approval import ForbiddenNative

business, Error = legacy.business, legacy.Error


class DraftLayoutApprovalTests(unittest.TestCase):
    open_view = legacy.DrawingApprovalTests.open_view
    call = legacy.DrawingApprovalTests.call
    request = legacy.DrawingApprovalTests.request
    status = legacy.DrawingApprovalTests.status
    rejects = legacy.DrawingApprovalTests.rejects

    def setUp(self):
        legacy.DrawingApprovalTests.setUp(self)
        self.layout = business._draft_layout()

    def make_plan(self, app, gui, document, source, controller, *, draft_layout=False):
        plan, rows = legacy.DrawingApprovalTests.make_plan(self, app, gui, document, source, controller)
        if draft_layout:
            names = tuple(row['object'] for row in rows)
            dto = business._draft_layout().plan(source.Name, names[0], names[2:], [60., 40., 8.])
            plan.update(names=names, scale=dto['scale'], draft_layout=dto)
        return plan, rows

    def make_result(self, app, gui, document, plan, controller):
        result = legacy.DrawingApprovalTests.make_result(self, app, gui, document, plan, controller)
        if 'draft_layout' in plan:
            result['scale'] = plan['draft_layout']['scale']
        return result

    def prepare(self, **changes):
        return legacy.DrawingApprovalTests.prepare(self, draftLayoutVersion=1, **changes)

    def test_new_preview_is_detached_bound_and_only_native_confirmation_writes_once(self):
        preview = self.prepare(); token = preview['token']
        self.assertTrue(preview['can_apply']); self.assertIsNone(preview['draft_layout_reason'])
        self.assertEqual(preview['draft_layout']['scale'], .5)
        self.assertEqual(preview['draft_layout']['views'][0]['center_mm'][0],
                         preview['draft_layout']['views'][1]['center_mm'][0])
        self.drawing.prepare_plan.assert_called_once()
        self.assertEqual(self.drawing.prepare_plan.call_args.kwargs, {'draft_layout': True})
        original = deepcopy(preview['draft_layout'])
        preview['draft_layout']['views'][0]['center_mm'][0] += 1
        self.assertEqual(business._plans[token]['public_preview']['draft_layout'], original)
        self.rejects('DRAWING_NATIVE_CONFIRMATION_REQUIRED', 'apply_part_changes', {'token': token})
        self.assertEqual(self.status(token)['state'], 'prepared')
        self.assertEqual(self.request(token)['state'], 'awaiting_confirmation')
        self.assertEqual(self.views[-1].preview['draft_layout'], original)
        self.drawing.apply_plan.assert_not_called()
        state = self.views[-1].confirm()
        self.assertEqual(state['state'], 'applied')
        self.assertEqual(state['result']['design_result']['scale'], .5)
        self.views[-1].confirm(); self.drawing.apply_plan.assert_called_once()
        self.assertFalse({'plan', 'public_preview', 'objects'} & set(business._plans[token]))
        self.assertEqual(self.doc.events, ['synthetic_drawing_write'])
        with patch.object(business, '_mvc', side_effect=AssertionError('status must stay memory-only')):
            self.assertEqual(business.perform('get_part_change_status', {'token': token},
                ForbiddenNative(), ForbiddenNative(), Error)['state'], 'applied')

    def test_missing_version_preserves_original_preview_and_native_arguments(self):
        preview = legacy.DrawingApprovalTests.prepare(self)
        self.assertNotIn('draft_layout', preview); self.assertNotIn('draft_layout_reason', preview)
        self.assertEqual(self.drawing.prepare_plan.call_args.kwargs, {})

    def test_version_requires_exact_integer_one_and_drawing_operation(self):
        for value in (True, False, 0, 2, 1., None, '1'):
            with self.subTest(value=value):
                self.rejects('INVALID_PARAMS', 'prepare_part_changes', {'docName': self.doc.Name,
                    'objectNames': [self.part.Name], 'operation': 'create_drawing', 'draftLayoutVersion': value})
        self.rejects('INVALID_PARAMS', 'prepare_part_changes', {'docName': self.doc.Name,
            'objectNames': [self.part.Name], 'operation': 'normalize_names', 'draftLayoutVersion': 1})
        self.drawing.prepare_plan.assert_not_called()

    def test_complete_no_space_blocks_without_placeholder_coordinates_or_native_window(self):
        self.drawing.prepare_plan.side_effect = legacy.DrawingError('DRAWING_DRAFT_LAYOUT_NO_SPACE')
        preview = self.prepare()
        self.assertFalse(preview['can_apply']); self.assertIsNone(preview['design_request']['page'])
        self.assertIsNone(preview['draft_layout'])
        self.assertEqual(preview['draft_layout_reason'], 'DRAWING_DRAFT_LAYOUT_NO_SPACE')
        self.assertEqual((preview['changed_count'], len(preview['rows'])), (0, 1))
        self.assertEqual(self.request(preview['token'])['error_code'], 'PART_PREVIEW_BLOCKED')
        self.assertEqual(self.views, []); self.drawing.apply_plan.assert_not_called()

    def test_bad_native_public_binding_refuses_token(self):
        original = self.drawing.prepare_plan.side_effect
        def swapped(*args, **kwargs):
            plan, rows = original(*args, **kwargs)
            plan['draft_layout']['views'][0]['center_mm'][0] += 1
            return plan, rows
        self.drawing.prepare_plan.side_effect = swapped
        self.rejects('DRAWING_RESULT_INVALID', 'prepare_part_changes', {'docName': self.doc.Name,
            'objectNames': [self.part.Name], 'operation': 'create_drawing', 'draftLayoutVersion': 1})
        self.assertEqual(len(business._plans), 0); self.drawing.apply_plan.assert_not_called()

    def test_last_native_read_cannot_change_shown_rows_or_downgrade_layout(self):
        for change in ('rows', 'policy', 'geometry'):
            with self.subTest(change=change):
                self.drawing.verify_plan.side_effect = None
                preview = self.prepare(); token = preview['token']; record = business._plans[token]
                def mutate(*args):
                    if change == 'rows': record['public_preview']['rows'][2]['note'] = 'Altered after native read.'
                    elif change == 'policy': record['draft_layout_required'] = False
                    else: record['plan']['drawing_plan']['draft_layout']['views'][0]['center_mm'][0] += 1e-10
                self.drawing.verify_plan.side_effect = mutate
                state = self.request(token)
                self.assertEqual((state['state'], state['error_code']), ('failed', 'DRAWING_DRAFT_LAYOUT_STALE'))
                self.drawing.apply_plan.assert_not_called()
        self.assertEqual(self.views, []); self.assertEqual(self.doc.events, [])

    def test_cancel_expiry_and_server_stop_keep_late_confirmation_without_write(self):
        for action in ('cancel', 'expiry', 'stop'):
            with self.subTest(action=action):
                business.begin_approval_lifecycle()
                with patch.object(business.time, 'monotonic', return_value=100.):
                    preview = self.prepare(); self.request(preview['token'])
                view = self.views[-1]
                with patch.object(business.time, 'monotonic', return_value=401. if action == 'expiry' else 101.):
                    if action == 'cancel': view.cancel()
                    if action == 'stop': business.cancel_pending_approvals()
                    state = view.confirm()
                self.assertEqual(state['state'], 'expired' if action == 'expiry' else 'cancelled')
        self.drawing.apply_plan.assert_not_called(); self.assertEqual(self.doc.events, [])

    def test_mismatched_applied_scale_is_failed_and_never_retried(self):
        preview = self.prepare(); self.request(preview['token'])
        wrong = legacy.drawing_result(self.part.Name)
        wrong['scale'] = .2
        self.drawing.apply_plan.side_effect = lambda *args: wrong
        self.assertEqual(self.views[-1].confirm()['error_code'], 'DRAWING_RESULT_INVALID')
        self.views[-1].confirm(); self.drawing.apply_plan.assert_called_once()


if __name__ == '__main__':
    unittest.main()
