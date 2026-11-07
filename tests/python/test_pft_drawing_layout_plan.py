# SPDX-License-Identifier: Apache-2.0
"""Layout freeze orchestration with real pure contracts and injected native reads."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

PATH = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools/pft_drawing_layout_plan.py"
spec = importlib.util.spec_from_file_location("neutral_layout_plan_test", PATH)
plans = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plans)


def source():
    return {"version": 1, "paper": {"width_mm": 297, "height_mm": 210, "margin_mm": 10,
                                   "reserved_rects": [[10, 10, 287, 80]]},
            "view": {"name": "NeutralView", "origin_mm": [120, 130], "bounds_mm": [100, 110, 140, 140]},
            "obstacles": {"rects": [], "segments": []},
            "spacing": {"clearance_mm": 1, "offset_mm": 6, "lane_mm": 8},
            "dimensions": [{"edge": "Edge0", "kind": "DistanceX", "placement": "auto",
                            "geometry": {"curve": "line", "start_mm": [100, 120], "end_mm": [140, 120]},
                            "text": "40.00", "label_size_mm": [14, 5]}]}


def intent():
    return {"page": "NeutralPage", "view": "NeutralView",
            "dimensions": [{"edge": "Edge0", "kind": "DistanceX", "placement": "auto"}]}


class LayoutPlanTests(unittest.TestCase):
    def setUp(self):
        self.source = source()
        self.context = {"input": deepcopy(self.source), "native_marker": object()}
        self.native = types.SimpleNamespace(snapshot=Mock(return_value=self.context), verify=Mock(), verify_defaults=Mock())
        sibling = plans._sibling
        self.core = sibling("pft_drawing_layout")
        def load(name):
            return self.native if name == "pft_drawing_layout_profile" else sibling(name)
        self.patch = patch.object(plans, "_sibling", side_effect=load)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.app, self.gui, self.document, self.controller = (object() for _ in range(4))
        self.page, self.view = types.SimpleNamespace(Name="NeutralPage"), types.SimpleNamespace(Name="NeutralView")

    def prepare(self, value=None):
        return plans.prepare(self.app, self.gui, self.document, self.page, self.view,
                             intent() if value is None else value, self.controller)

    def verify(self, plan, **kwargs):
        return plans.verify(self.app, self.gui, plan, self.controller, **kwargs)

    def test_one_complete_suggestion_is_detached_and_never_reflowed_during_verification(self):
        value = intent()
        with patch.object(self.core, "suggest_layout", wraps=self.core.suggest_layout) as solver:
            plan = self.prepare(value)
            solver.assert_called_once()
            self.assertTrue(plan["graphics"]["result"]["can_apply"])
            self.assertEqual(plan["request"]["dimensions"][0]["edge"], "Edge0")
            value["dimensions"][0]["edge"] = "Edge99"
            self.assertEqual(plan["intent"]["dimensions"][0]["edge"], "Edge0")
            self.verify(plan)
            self.verify(plan, defaults_only=True)
            solver.assert_called_once()
        self.assertIs(plan["native_context"], self.context)
        self.assertIsNot(plan["graphics"]["input"], self.context["input"])
        self.native.snapshot.assert_called_once()
        self.assertEqual(self.native.verify.call_count, 2)
        self.native.verify_defaults.assert_called_once_with(self.app, self.gui, self.context, self.controller)

    def test_explicit_only_invalid_or_mismatched_owner_never_calls_native_adapter(self):
        explicit = intent(); explicit["dimensions"][0] = {"edge": "Edge0", "kind": "DistanceX", "x_mm": 0, "y_mm": 30}
        malformed = intent(); malformed["dimensions"][0]["x_mm"] = 1
        different = intent(); different["page"] = "DifferentPage"
        for value in (explicit, malformed, different):
            with self.assertRaises(plans.PftDrawingLayoutPlanError): self.prepare(value)
        self.native.snapshot.assert_not_called()
        self.native.verify.assert_not_called()

    def test_adapter_cannot_change_requested_edge_kind_mode_or_explicit_coordinates(self):
        for mode in ("view", "edge", "kind", "placement", "explicit"):
            self.context["input"] = source()
            value = intent()
            row = self.context["input"]["dimensions"][0]
            if mode == "view": self.context["input"]["view"]["name"] = "DifferentView"
            elif mode == "edge": row["edge"] = "Edge1"
            elif mode == "kind": row["kind"] = "Distance"
            elif mode == "placement": row.update(placement="explicit", x_mm=0, y_mm=30)
            else:
                value["dimensions"].append({"edge": "Edge1", "kind": "DistanceX", "x_mm": 0, "y_mm": 30})
                self.context["input"]["dimensions"].append({**deepcopy(row), "edge": "Edge1", "placement": "explicit", "x_mm": 0, "y_mm": 31})
            with self.subTest(mode=mode), self.assertRaises(plans.PftDrawingLayoutPlanError): self.prepare(value)
        self.native.verify.assert_not_called()

    def test_no_space_retains_complete_read_only_graphics_but_has_no_resolved_request(self):
        self.context["input"]["paper"]["reserved_rects"] = [[0, 0, 297, 210]]
        plan = self.prepare()
        self.assertIsNone(plan["request"])
        self.assertFalse(plan["graphics"]["result"]["can_apply"])
        self.assertEqual(plan["graphics"]["result"]["dimensions"], [])
        self.assertEqual(len(plan["graphics"]["input"]["dimensions"]), 1)
        self.verify(plan)

    def test_any_changed_frozen_payload_or_context_input_is_stale_before_native_read(self):
        original = self.prepare()
        for mode in ("intent", "request", "input", "result", "version", "fingerprint", "marker", "extra", "context"):
            plan = deepcopy(original)
            if mode == "intent": plan["intent"]["page"] = "DifferentPage"
            elif mode == "request": plan["request"]["dimensions"][0]["x_mm"] += .5
            elif mode == "input": plan["graphics"]["input"]["paper"]["margin_mm"] += .5
            elif mode == "result": plan["graphics"]["result"]["dimensions"][0]["text"] = "different"
            elif mode == "version": plan["graphics"]["version"] = 2
            elif mode == "fingerprint": plan["fingerprint"] = "0" * 64
            elif mode == "marker": plan["_plan"] = "different"
            elif mode == "extra": plan["external_confirmation"] = True
            else: plan["native_context"]["input"]["spacing"]["lane_mm"] += .5
            before = self.native.verify.call_count
            with self.subTest(mode=mode), self.assertRaises(plans.PftDrawingLayoutPlanError) as failure:
                self.verify(plan)
            self.assertEqual(failure.exception.code, "DIMENSION_LAYOUT_STALE")
            self.assertEqual(self.native.verify.call_count, before)

    def test_native_revalidation_failure_and_unknown_cleanup_are_not_retried(self):
        class Unknown(RuntimeError): cleanup_state = "unknown"
        self.native.verify.side_effect = Unknown("not exposed")
        with self.assertRaises(plans.PftDrawingLayoutPlanError) as failure: self.prepare()
        self.assertEqual(failure.exception.cleanup_state, "unknown")
        self.assertEqual(self.native.verify.call_count, 1)
        self.native.verify.side_effect = None
        plan = self.prepare()
        self.native.verify_defaults.side_effect = Unknown("not exposed")
        with self.assertRaises(plans.PftDrawingLayoutPlanError) as failure: self.verify(plan, defaults_only=True)
        self.assertEqual(failure.exception.code, "DIMENSION_LAYOUT_STALE")
        self.assertEqual(failure.exception.cleanup_state, "unknown")
        self.native.verify_defaults.assert_called_once()

    def test_boolean_envelope_version_and_numeric_request_values_are_not_equivalent(self):
        plan = self.prepare()
        changed = deepcopy(plan); changed["graphics"]["version"] = True
        with self.assertRaises(plans.PftDrawingLayoutPlanError): self.verify(changed)
        # The generated X is exactly zero here, so equality alone would confuse
        # False with an accepted numeric coordinate in the resolved DTO.
        self.assertEqual(plan["request"]["dimensions"][0]["x_mm"], 0)
        changed = deepcopy(plan); changed["request"]["dimensions"][0]["x_mm"] = False
        with self.assertRaises(plans.PftDrawingLayoutPlanError): self.verify(changed)

    def test_native_callback_cannot_change_frozen_input_after_the_solver(self):
        def mutate(*args): self.context["input"]["spacing"]["offset_mm"] += 1
        self.native.verify.side_effect = mutate
        with self.assertRaises(plans.PftDrawingLayoutPlanError): self.prepare()
        self.native.snapshot.assert_called_once()
        self.native.verify.assert_called_once()

    def test_invalid_write_mode_never_enters_a_native_verifier(self):
        plan = self.prepare()
        before = self.native.verify.call_count
        for mode in (0, 1, None, "true"):
            with self.assertRaises(plans.PftDrawingLayoutPlanError): self.verify(plan, defaults_only=mode)
        self.assertEqual(self.native.verify.call_count, before)
        self.native.verify_defaults.assert_not_called()

    def test_verify_callbacks_cannot_mutate_or_replace_the_frozen_plan(self):
        for mode in (False, True):
            for target in ("input", "request", "context", "intent", "result"):
                plan = self.prepare()
                callback = self.native.verify_defaults if mode else self.native.verify
                def mutate(*args):
                    if target == "input": plan["native_context"]["input"]["spacing"]["lane_mm"] += 1
                    elif target == "request": plan["request"]["dimensions"][0]["x_mm"] += 1
                    elif target == "context": plan["native_context"] = dict(plan["native_context"])
                    elif target == "intent": plan["intent"]["page"] = "DifferentPage"
                    else: plan["graphics"]["result"]["dimensions"][0]["text"] = "different"
                before = callback.call_count
                callback.side_effect = mutate
                with self.subTest(defaults_only=mode, target=target), self.assertRaises(plans.PftDrawingLayoutPlanError):
                    self.verify(plan, defaults_only=mode)
                self.assertEqual(callback.call_count, before + 1)
                callback.side_effect = None
                self.context["input"] = source()

    def test_prepare_preserves_only_fixed_actionable_reasons_without_raw_error_text(self):
        class NativeError(RuntimeError): pass
        for code in plans._REASONS:
            failure = NativeError("untrusted diagnostic text must not be shown")
            failure.code = code
            self.native.snapshot.side_effect = failure
            with self.subTest(code=code), self.assertRaises(plans.PftDrawingLayoutPlanError) as caught:
                self.prepare()
            self.assertEqual(caught.exception.reason_code, code)
            self.assertEqual(str(caught.exception), plans._REASONS[code])
        for code in (None, True, "UNRECOGNIZED_DIAGNOSTIC", "DRAWING_LAYOUT_PAGE_OPEN_REQUIRED extra"):
            failure = NativeError("untrusted diagnostic text must not be shown")
            failure.code = code
            self.native.snapshot.side_effect = failure
            with self.assertRaises(plans.PftDrawingLayoutPlanError) as caught: self.prepare()
            self.assertIsNone(caught.exception.reason_code)
            self.assertNotIn("untrusted", str(caught.exception))

    def test_verify_keeps_stale_state_and_fixed_reason_with_unknown_cleanup(self):
        plan = self.prepare()
        class NativeError(RuntimeError):
            code = "DRAWING_LAYOUT_STYLE_UNSUPPORTED"
            cleanup_state = "unknown"
        self.native.verify.side_effect = NativeError("not exposed")
        with self.assertRaises(plans.PftDrawingLayoutPlanError) as caught: self.verify(plan)
        self.assertEqual(caught.exception.code, "DIMENSION_LAYOUT_STALE")
        self.assertEqual(caught.exception.reason_code, "DRAWING_LAYOUT_STYLE_UNSUPPORTED")
        self.assertEqual(caught.exception.cleanup_state, "unknown")
        self.assertEqual(str(caught.exception), "尺寸布局输入已变化，请重新预览。")

    def test_public_preview_binds_full_resolved_request_and_is_detached(self):
        plan = self.prepare()
        public = plans.preview(plan)
        self.assertEqual(public, plans.validate_preview(public, plan["request"], can_apply=True))
        public["graphics"]["input"]["view"]["origin_mm"][0] += 1
        self.assertEqual(plan["graphics"]["input"]["view"]["origin_mm"], [120, 130])
        with self.assertRaises(ValueError): plans.validate_preview(public, plan["request"], can_apply=True)

    def test_blocked_public_preview_never_has_partial_or_placeholder_coordinates(self):
        self.context["input"]["paper"]["reserved_rects"] = [[0, 0, 297, 210]]
        plan = self.prepare(); public = plans.preview(plan)
        self.assertIsNone(public["reason_code"])
        self.assertEqual(public["graphics"]["result"]["dimensions"], [])
        self.assertEqual(plans.validate_preview(public, None, can_apply=False), public)
        for request, apply in ((None, True), ({"page": "NeutralPage", "view": "NeutralView",
                "dimensions": [{"edge": "Edge0", "kind": "DistanceX", "x_mm": 0, "y_mm": 0}]}, False)):
            with self.assertRaises(ValueError): plans.validate_preview(public, request, can_apply=apply)

    def test_unavailable_public_preview_has_only_known_reason_and_no_native_diagnostic(self):
        failure = RuntimeError("raw native diagnostic is not returned")
        public = plans.unavailable_preview(intent(), failure)
        self.assertIsNone(public["graphics"])
        self.assertEqual(public["reason_code"], "DRAWING_LAYOUT_INVALID_RESULT")
        self.assertNotIn("diagnostic", str(public))
        for reason in plans._REASONS:
            public["reason_code"] = reason
            self.assertEqual(plans.validate_preview(public, None, can_apply=False), public)
        public["reason_code"] = None
        with self.assertRaises(ValueError): plans.validate_preview(public, None, can_apply=False)

    def test_public_preview_rejects_manual_only_unknown_fields_bad_types_and_mismatched_suggestion(self):
        plan = self.prepare(); original = plans.preview(plan)
        for mode in ("version", "verification", "extra", "reason", "auto", "edge", "text", "request", "apply"):
            public, request, apply = deepcopy(original), deepcopy(plan["request"]), True
            if mode == "version": public["version"] = True
            elif mode == "verification": public["layout_verification"] = "native_verified"
            elif mode == "extra": public["approved"] = True
            elif mode == "reason": public["reason_code"] = "DRAWING_LAYOUT_INVALID_ARGUMENT"
            elif mode == "auto": public["intent"] = deepcopy(request)
            elif mode == "edge": public["intent"]["dimensions"][0]["edge"] = "Edge8"
            elif mode == "text": public["graphics"]["result"]["dimensions"][0]["text"] = "wrong"
            elif mode == "request": request["dimensions"][0]["x_mm"] = False
            else: apply = 1
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                plans.validate_preview(public, request, can_apply=apply)

    def test_new_native_style_verification_is_bound_before_and_after_callback(self):
        self.native.verify_created_style = Mock(return_value=True)
        plan = self.prepare(); created = [object()]
        self.assertTrue(plans.verify_created(self.app, self.gui, plan, created, self.controller))
        self.native.verify_created_style.assert_called_once_with(self.app, self.gui, self.context, created, self.controller)
        def mutate(*args): plan["request"]["dimensions"][0]["y_mm"] += 1
        self.native.verify_created_style.side_effect = mutate
        with self.assertRaises(plans.PftDrawingLayoutPlanError) as caught:
            plans.verify_created(self.app, self.gui, plan, created, self.controller)
        self.assertEqual(caught.exception.code, "DIMENSION_LAYOUT_STYLE_UNVERIFIED")
        self.assertNotIn("mutate", str(caught.exception))

    def test_new_native_style_failure_preserves_unknown_cleanup_without_retry(self):
        class NativeError(RuntimeError): cleanup_state = "unknown"
        self.native.verify_created_style = Mock(side_effect=NativeError("hidden"))
        plan = self.prepare()
        with self.assertRaises(plans.PftDrawingLayoutPlanError) as caught:
            plans.verify_created(self.app, self.gui, plan, [], self.controller)
        self.assertEqual(caught.exception.code, "DIMENSION_LAYOUT_STYLE_UNVERIFIED")
        self.assertEqual(caught.exception.cleanup_state, "unknown")
        self.assertNotIn("hidden", str(caught.exception))
        self.native.verify_created_style.assert_called_once()


if __name__ == "__main__": unittest.main()
