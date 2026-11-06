# SPDX-License-Identifier: Apache-2.0
"""Synthetic drawing approval integration; no Qt, CAD, service or user state."""
from copy import deepcopy
import types
import unittest
from unittest.mock import Mock, patch

import test_pft_business as fixtures
from test_pft_part_approval import Presenter, ForbiddenNative
from test_pft_drawing_contract import drawing_result

business, Error = fixtures.business, fixtures.Error


class DrawingError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__("Fixed synthetic drawing failure")


class DrawingApprovalTests(unittest.TestCase):
    def setUp(self):
        fixtures.BusinessTests.setUp(self)
        self.views = []
        self.drawing = types.SimpleNamespace(CAPABILITIES=deepcopy(business.DRAWING_CREATE_CAPABILITIES),
            PftDrawingError=DrawingError, prepare_plan=Mock(side_effect=self.make_plan),
            verify_plan=Mock(), apply_plan=Mock(side_effect=self.make_result))
        for target, kwargs in (("_drawing", {"return_value": self.drawing}),
                               ("_open_approval", {"side_effect": self.open_view})):
            context = patch.object(business, target, **kwargs)
            context.start(); self.addCleanup(context.stop)

    def open_view(self, *args):
        view = Presenter(*args); self.views.append(view); return view

    def make_plan(self, app, gui, document, source, controller):
        names = ("PageDraft", "TemplateDraft", "FrontDraft", "TopDraft", "AxonometricDraft")
        rows = [{"object": name, "field": "TechDraw draft node", "old": None,
                 "new": "Synthetic draft node", "status": "change", "reason": "",
                 "source": "cached_native_typed_snapshot", "note": "Projection not checked; manual review required."}
                for name in names]
        return {"request": {"source": source.Name, "page": names[0]}, "native_ref": object()}, rows

    def make_result(self, app, gui, document, plan, controller):
        self.doc.events.append("synthetic_drawing_write")
        return drawing_result(self.part.Name)

    def call(self, method, params):
        return business.perform(method, params, self.app, self.gui, Error)

    def prepare(self, **changes):
        params = {"docName": self.doc.Name, "objectNames": [self.part.Name], "operation": "create_drawing"}
        params.update(changes)
        return self.call("prepare_part_changes", params)

    def status(self, token):
        return business.perform("get_part_change_status", {"token": token}, ForbiddenNative(), ForbiddenNative(), Error)

    def request(self, token):
        return self.call("request_part_change_approval", {"token": token})

    def rejects(self, code, method, params):
        with self.assertRaises(Error) as failure: self.call(method, params)
        self.assertEqual(failure.exception.code, code)

    def test_prepare_and_request_are_read_only_complete_and_bypass_bom_snapshots(self):
        model, _ = business._mvc()
        with patch.object(model, "_snapshot", side_effect=AssertionError("drawing must not read BOM snapshot")):
            preview = self.prepare()
            self.assertEqual(preview["design_request"], {"source": "Part", "page": "PageDraft"})
            self.assertEqual((preview["changed_count"], preview["can_apply"], len(preview["rows"])), (5, True, 5))
            self.assertEqual(self.request(preview["token"])["state"], "awaiting_confirmation")
        self.assertEqual(self.views[0].preview, preview)
        self.views[0].preview["design_request"]["page"] = "ChangedCopy"
        self.assertEqual(business._plans[preview["token"]]["public_preview"]["design_request"]["page"], "PageDraft")
        self.assertEqual(self.doc.events, [])
        self.assertEqual(self.prefs.events, [])
        self.drawing.apply_plan.assert_not_called()
        with patch.object(business, "_mvc", side_effect=AssertionError("status must remain memory-only")):
            self.assertFalse(self.status(preview["token"])["native_confirmation"])

    def test_direct_apply_never_grants_drawing_even_after_applied_replay(self):
        preview = self.prepare(); token = preview["token"]
        for before in ("prepared", "awaiting_confirmation", "applied"):
            if before == "awaiting_confirmation": self.request(token)
            elif before == "applied":
                result = self.views[0].confirm()
                self.assertEqual(result["state"], "applied")
                self.assertTrue(result["native_confirmation"])
                self.assertEqual(result["result"], {"changed_count": 5, "saves_document": False,
                                                   "design_result": drawing_result("Part")})
            self.rejects("DRAWING_NATIVE_CONFIRMATION_REQUIRED", "apply_part_changes", {"token": token})
            self.assertEqual(self.status(token)["state"], before)
        self.drawing.apply_plan.assert_called_once()
        self.assertEqual(self.doc.events, ["synthetic_drawing_write"])
        self.views[0].confirm(); self.views[0].cancel(); self.request(token)
        self.drawing.apply_plan.assert_called_once()

    def test_cancel_expiry_and_stop_invalidate_late_drawing_confirmation(self):
        for ending in ("cancel", "expiry", "stop"):
            with self.subTest(ending=ending):
                business.begin_approval_lifecycle()
                with patch.object(business.time, "monotonic", return_value=100.0):
                    preview = self.prepare(); self.request(preview["token"])
                view = self.views[-1]
                with patch.object(business.time, "monotonic", return_value=401.0 if ending == "expiry" else 101.0):
                    if ending == "cancel": view.cancel()
                    elif ending == "stop": business.cancel_pending_approvals()
                    state = view.confirm()
                    self.assertEqual(state["state"], "expired" if ending == "expiry" else "cancelled")
                    self.assertFalse(state["native_confirmation"])
        self.drawing.apply_plan.assert_not_called()
        self.assertEqual(self.doc.events, [])

    def test_plan_staleness_is_fixed_terminal_and_never_calls_apply(self):
        preview = self.prepare(); self.request(preview["token"])
        self.drawing.verify_plan.side_effect = DrawingError("DRAWING_PLAN_STALE")
        state = self.views[0].confirm()
        self.assertEqual((state["state"], state["error_code"]), ("failed", "DRAWING_PLAN_STALE"))
        self.assertTrue(state["native_confirmation"])
        self.drawing.apply_plan.assert_not_called()
        self.views[0].confirm()
        self.drawing.apply_plan.assert_not_called()

    def test_fixed_create_error_and_invalid_result_never_retry_or_report_success(self):
        for failure in ("create", "result"):
            with self.subTest(failure=failure):
                preview = self.prepare(); self.request(preview["token"])
                if failure == "create": self.drawing.apply_plan.side_effect = DrawingError("DRAWING_CREATE_FAILED")
                else:
                    bad = drawing_result("Part", "WrongPage")
                    self.drawing.apply_plan.side_effect = None; self.drawing.apply_plan.return_value = bad
                state = self.views[-1].confirm()
                expected = "DRAWING_CREATE_FAILED" if failure == "create" else "DRAWING_RESULT_INVALID"
                self.assertEqual((state["state"], state["error_code"]), ("failed", expected))
                self.assertIsNone(state["result"])
                before = self.drawing.apply_plan.call_count
                self.views[-1].confirm()
                self.assertEqual(self.drawing.apply_plan.call_count, before)

    def test_blocked_preview_keeps_null_page_and_cannot_open_approval(self):
        self.drawing.prepare_plan.side_effect = DrawingError("DRAWING_TEMPLATE_UNAVAILABLE")
        preview = self.prepare()
        self.assertEqual(preview["design_request"], {"source": "Part", "page": None})
        self.assertEqual((preview["can_apply"], preview["changed_count"]), (False, 0))
        self.assertEqual(len(preview["rows"]), 1)
        self.assertIn("DRAWING_TEMPLATE_UNAVAILABLE", preview["rows"][0]["reason"])
        state = self.request(preview["token"])
        self.assertEqual((state["state"], state["error_code"]), ("failed", "PART_PREVIEW_BLOCKED"))
        self.assertEqual(self.views, [])
        self.drawing.apply_plan.assert_not_called()

    def test_drawing_rejects_extra_inputs_multiple_sources_and_unsafe_names_before_core(self):
        for changes in ({"values": {}}, {"bodyName": "Body"}, {"lengthMm": 10},
                        {"objectNames": ["Part", "Other"]}, {"objectNames": ["../Part"]},
                        {"objectNames": ["Part\u200b"]}, {"objectNames": ["字" * 65]}):
            with self.subTest(changes=changes):
                with self.assertRaises(Error) as failure: self.prepare(**changes)
                self.assertEqual(failure.exception.code, "INVALID_PARAMS")
        self.drawing.prepare_plan.assert_not_called()
        self.assertEqual(len(business._plans), 0)

    def test_unicode_document_and_source_names_reach_the_core_without_transliteration(self):
        self.doc.Name = "示例文档"; self.part.Name = "来源零件"
        self.documents.clear(); self.documents[self.doc.Name] = self.doc
        preview = self.prepare()
        self.assertEqual(preview["document"], "示例文档")
        self.assertEqual(preview["design_request"]["source"], "来源零件")
        self.drawing.prepare_plan.assert_called_once()

    def test_oversized_unicode_terminal_status_is_rejected_before_token_or_write(self):
        self.doc.Name = "😀" * 256; self.part.Name = "😀" * 64
        self.documents.clear(); self.documents[self.doc.Name] = self.doc
        with self.assertRaises(Error) as failure: self.prepare()
        self.assertEqual(failure.exception.code, "PART_STATUS_TOO_LARGE")
        self.assertEqual(len(business._plans), 0)
        self.assertEqual(self.views, [])
        self.assertEqual(self.doc.events, [])
        self.drawing.apply_plan.assert_not_called()

    def test_terminal_status_budget_is_checked_at_prepare_and_accepts_its_exact_bound(self):
        import json
        original_limit = business.MAX_STATUS_BYTES
        for limit in range(1, original_limit + 1):
            with patch.object(business, "MAX_STATUS_BYTES", limit):
                try:
                    business._drawing_status_budget("Demo", {"source": "Part", "page": "PageDraft"},
                                                  self.make_plan(None, None, None, self.part, None)[1], Error)
                except Error as error:
                    self.assertEqual(error.code, "PART_STATUS_TOO_LARGE")
                else:
                    minimum = limit
                    break
        with patch.object(business, "MAX_STATUS_BYTES", minimum - 1):
            with self.assertRaises(Error) as failure: self.prepare()
        self.assertEqual(failure.exception.code, "PART_STATUS_TOO_LARGE")
        self.assertEqual(len(business._plans), 0)
        with patch.object(business, "MAX_STATUS_BYTES", minimum):
            preview = self.prepare(); self.request(preview["token"])
            result = self.views[-1].confirm()
            self.assertEqual(result["state"], "applied")
            self.assertLessEqual(len(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode()), minimum)
            self.assertEqual(self.status(preview["token"])["state"], "applied")

    def test_malformed_core_preview_is_rejected_without_storing_a_token(self):
        for request, rows in (({"source": "Other", "page": "PageDraft"}, self.make_plan(None, None, None, self.part, None)[1]),
                              ({"source": "Part", "page": "PageDraft"}, [])):
            self.drawing.prepare_plan.side_effect = None
            self.drawing.prepare_plan.return_value = ({"request": request}, rows)
            with self.assertRaises(Error) as failure: self.prepare()
            self.assertEqual(failure.exception.code, "DRAWING_RESULT_INVALID")
            self.assertEqual(len(business._plans), 0)
        self.assertEqual(self.doc.events, [])


if __name__ == "__main__": unittest.main()
