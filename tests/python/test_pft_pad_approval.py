# SPDX-License-Identifier: Apache-2.0
"""Pad operation routing and human-only grant using independent module doubles."""
from copy import deepcopy
import json
import types
import unittest
from unittest.mock import patch

import test_pft_part_approval as approval_fixtures
import test_pft_pad_length as native_fixtures
from test_pft_business import business, Error


class PadError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__("当前有界 Pad 设计条件不满足。")


def design_result():
    shape = {"volume_mm3": 2400.0, "dimensions_mm": [20.0, 10.0, 12.0], "solids": 1}
    return {"kind": "pad_length", "body": "Body", "pad": "Pad", "length_mm": 12.0,
            "recompute_count": 2, "checked_objects": 12, "geometry": {"pad": deepcopy(shape), "body": deepcopy(shape)},
            "validation": "native_valid_one_solid_and_length_scaled_volume"}


class PadApprovalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = approval_fixtures.PartApprovalTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.part.Name = "Pad"
        self.calls = []
        self.version = 0
        self.blocked = False
        self.failure = None
        self.pad_module = types.SimpleNamespace(CAPABILITIES=dict(business.PAD_LENGTH_CAPABILITIES),
            PftPadLengthError=PadError, prepare_plan=self.prepare_model, verify_plan=self.verify_model, apply_plan=self.apply_model)
        self.patches = patch.object(business, "_pad", return_value=self.pad_module)
        self.patches.start()
        self.addCleanup(self.patches.stop)

    def prepare_model(self, app, gui, document, body, pad, length, controller):
        self.calls.append(("prepare", body, pad.Name, length))
        if self.blocked:
            raise PadError("PAD_UNSUPPORTED")
        return {"version": self.version}, [{"object": pad.Name, "field": "Length (mm)", "old": 8.0,
            "new": length, "status": "change", "source": "native cached Length", "note": "Synthetic closed native graph"}]

    def verify_model(self, app, gui, document, plan, controller):
        self.calls.append(("verify",))
        if plan["version"] != self.version:
            raise PadError("PAD_PLAN_STALE")

    def apply_model(self, app, gui, document, plan, controller):
        self.verify_model(app, gui, document, plan, controller)
        self.calls.append(("apply",))
        if self.failure:
            raise PadError(self.failure)
        return design_result()

    def call(self, method, params):
        return business.perform(method, params, self.fixture.app, self.fixture.gui, Error)

    def prepare(self, **changes):
        params = {"docName": self.fixture.document.Name, "objectNames": ["Pad"], "operation": "set_pad_length",
                  "bodyName": "Body", "lengthMm": 12.0}
        params.update(changes)
        return self.call("prepare_part_changes", params)

    def status(self, token):
        return business.perform("get_part_change_status", {"token": token}, approval_fixtures.ForbiddenNative(), approval_fixtures.ForbiddenNative(), Error)

    def assert_error(self, code, action):
        with self.assertRaises(Error) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_strict_design_inputs_reject_before_model_or_native_calls(self):
        for changes in ({"objectNames": ["Pad", "Other"]}, {"objectNames": []}, {"bodyName": None},
                        {"bodyName": ""}, {"values": {}}, {"confirmed": True},
                        *({"lengthMm": value} for value in (True, False, None, "12", 0, -1, 0.009, 10000.001,
                                                            float("nan"), float("inf")))):
            self.assert_error("INVALID_PARAMS", lambda: self.prepare(**changes))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.fixture.document.events, [])
        self.assertEqual(self.fixture.presenters, [])
        for operation in ("normalize_names", "sync_link_labels", "initialize_properties", "recommend_properties", "set_properties"):
            self.assert_error("INVALID_PARAMS", lambda: self.prepare(operation=operation))

    def test_design_preview_uses_dedicated_snapshot_and_binds_all_public_context(self):
        model, _ = business._mvc()
        with patch.object(model, "_snapshot", side_effect=AssertionError("BOM is not a design snapshot")):
            preview = self.prepare()
        self.assertEqual(preview["changed_count"], 1)
        self.assertEqual(preview["design_request"], {"body": "Body", "pad": "Pad", "length_mm": 12.0})
        self.assertEqual(preview["rows"][0]["body"], "Body")
        self.assertEqual(preview["rows"][0]["unit"], "mm")
        self.assertEqual(preview["rows"][0]["recompute_roots"], ["Body"])
        self.assertEqual(business._plans[preview["token"]]["snapshots"], [])
        self.assertEqual(self.fixture.document.events, [])
        self.assertEqual(self.fixture.presenters, [])

    def test_full_apply_never_bypasses_human_confirmation_or_consumes_prepared_token(self):
        preview = self.prepare(); token = preview["token"]
        self.assert_error("PAD_NATIVE_CONFIRMATION_REQUIRED", lambda: self.call("apply_part_changes", {"token": token}))
        self.assertEqual(self.status(token)["state"], "prepared")
        self.assertNotIn(("apply",), self.calls)
        pending = self.call("request_part_change_approval", {"token": token})
        self.assertEqual(pending["state"], "awaiting_confirmation")
        self.assert_error("PAD_NATIVE_CONFIRMATION_REQUIRED", lambda: self.call("apply_part_changes", {"token": token}))
        self.assertEqual(self.status(token)["state"], "awaiting_confirmation")
        final = self.fixture.presenters[0].confirm()
        self.assertEqual(final["state"], "applied")
        self.assertIs(final["native_confirmation"], True)
        self.assertEqual(final["result"], {"changed_count": 1, "saves_document": False, "design_result": design_result()})
        self.assertEqual(self.calls.count(("apply",)), 1)
        self.assert_error("PAD_NATIVE_CONFIRMATION_REQUIRED", lambda: self.call("apply_part_changes", {"token": token}))
        self.assertEqual(self.fixture.presenters[0].confirm(), final)
        self.assertEqual(self.calls.count(("apply",)), 1)
        self.assertFalse(approval_fixtures.PartApprovalTests.PRIVATE_KEYS & set(business._plans[token]))

    def test_cancel_and_stop_invalidate_pad_grants_without_an_apply(self):
        for stop in (False, True):
            preview = self.prepare(); token = preview["token"]
            self.call("request_part_change_approval", {"token": token})
            handle = self.fixture.presenters[-1]
            if stop:
                business.cancel_pending_approvals()
            else:
                handle.cancel()
            self.assertEqual(handle.confirm()["state"], "cancelled")
            self.assertFalse(self.status(token)["native_confirmation"])
            self.assertNotIn(("apply",), self.calls)
            business.begin_approval_lifecycle()

    def test_rebound_design_plan_is_rejected_before_show_and_after_waiting(self):
        for phase in ("before_window", "while_waiting"):
            preview = self.prepare(); token = preview["token"]
            if phase == "before_window":
                self.version += 1
                result = self.call("request_part_change_approval", {"token": token})
            else:
                self.call("request_part_change_approval", {"token": token})
                self.version += 1
                result = self.fixture.presenters[-1].confirm()
            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error_code"], "PAD_PLAN_STALE")
            self.assertNotIn(("apply",), self.calls)

    def test_native_failure_is_fixed_terminal_and_status_does_not_read_model_again(self):
        for code in ("PAD_PARAMETER_VERIFY_FAILED", "PAD_GEOMETRY_VERIFY_FAILED", "PAD_RECOMPUTE_UNVERIFIED"):
            preview = self.prepare(); token = preview["token"]
            self.call("request_part_change_approval", {"token": token})
            self.failure = code
            result = self.fixture.presenters[-1].confirm()
            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error_code"], code)
            self.assertIs(result["native_confirmation"], True)
            before = list(self.calls)
            self.assertEqual(self.status(token), result)
            self.assertEqual(self.calls, before)
            self.assertFalse(approval_fixtures.PartApprovalTests.PRIVATE_KEYS & set(business._plans[token]))

    def test_blocked_prepare_never_creates_window_or_write_grant(self):
        self.blocked = True
        preview = self.prepare()
        self.assertFalse(preview["can_apply"])
        self.assertEqual(preview["rows"][0]["status"], "blocked")
        result = self.call("request_part_change_approval", {"token": preview["token"]})
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error_code"], "PART_PREVIEW_BLOCKED")
        self.assertEqual(self.fixture.presenters, [])
        self.assertNotIn(("apply",), self.calls)

    def test_applied_result_is_detached_bounded_and_no_private_native_references_remain(self):
        preview = self.prepare(); token = preview["token"]
        self.call("request_part_change_approval", {"token": token})
        first = self.fixture.presenters[-1].confirm()
        first["result"]["design_result"]["geometry"]["pad"]["dimensions_mm"][0] = 999
        second = self.status(token)
        self.assertEqual(second["result"]["design_result"], design_result())
        self.assertLessEqual(len(json.dumps(second, ensure_ascii=True, separators=(",", ":")).encode()), 4096)
        self.assertFalse(approval_fixtures.PartApprovalTests.PRIVATE_KEYS & set(business._plans[token]))


class RealPadCoreApprovalTests(unittest.TestCase):
    """Real business grant and Pad controller; only host/GUI presenter are fakes."""

    def test_real_core_prepare_native_grant_apply_and_memory_only_status(self):
        with business._lock:
            business._plans.clear()
        business.begin_approval_lifecycle()
        self.addCleanup(business.cancel_pending_approvals)
        document = native_fixtures.NativeDocument('arc')
        opened = {document.Name: document}
        app = types.SimpleNamespace(ActiveDocument=document, getDocument=opened.get,
            listDocuments=lambda: dict(opened), getActiveTransaction=lambda: document.active_transaction)
        gui = types.SimpleNamespace(getDocument=lambda name: types.SimpleNamespace(getInEdit=lambda: None))
        presenters = []
        def present(*args):
            handle = approval_fixtures.Presenter(*args)
            presenters.append(handle)
            return handle
        def perform(method, params):
            return business.perform(method, params, app, gui, Error)
        def status(token):
            # Neither current model reads nor lazy native/helper imports are
            # allowed during the completed memory-only polling path.
            with patch.object(business, '_mvc', side_effect=AssertionError('status reads no model')):
                return business.perform('get_part_change_status', {'token': token},
                    approval_fixtures.ForbiddenNative(), approval_fixtures.ForbiddenNative(), Error)
        real_core = business._pad()
        with patch.object(business, '_open_approval', side_effect=present), \
                patch.object(real_core, 'apply_plan', wraps=real_core.apply_plan) as actual_apply:
            preview = perform('prepare_part_changes', {'docName': document.Name, 'objectNames': ['Pad'],
                'operation': 'set_pad_length', 'bodyName': 'Body', 'lengthMm': 8.0})
            token = preview['token']
            self.assertTrue(preview['can_apply'])
            self.assertEqual(preview['rows'][0]['source'], 'cached_native_typed_snapshot')
            self.assertEqual(document.events, [])
            with self.assertRaises(Error) as rejected:
                perform('apply_part_changes', {'token': token})
            self.assertEqual(rejected.exception.code, 'PAD_NATIVE_CONFIRMATION_REQUIRED')
            self.assertEqual(status(token)['state'], 'prepared')
            pending = perform('request_part_change_approval', {'token': token})
            self.assertEqual(pending['state'], 'awaiting_confirmation')
            self.assertFalse(pending['native_confirmation'])
            self.assertEqual(presenters[0].preview, preview)
            self.assertEqual(document.events, [])
            self.assertEqual(document.pad.writes, [])
            actual_apply.assert_not_called()
            with self.assertRaises(Error):
                perform('apply_part_changes', {'token': token})
            # Invokes the service's captured private grant callback. This is a
            # synthetic presenter mechanism test, not actual human acceptance.
            final = presenters[0].confirm()
            actual_apply.assert_called_once()
            self.assertEqual(final['state'], 'applied')
            self.assertTrue(final['native_confirmation'])
            result = final['result']['design_result']
            self.assertEqual(result['body'], 'Body')
            self.assertEqual(result['pad'], 'Pad')
            self.assertEqual(result['length_mm'], 8.0)
            self.assertEqual(result['checked_objects'], 11)
            self.assertEqual(result['geometry']['pad']['volume_mm3'], 48.0)
            self.assertEqual(result['geometry']['body']['dimensions_mm'], [2.0, 3.0, 8.0])
            self.assertEqual(document.events, ['open', 'commit'])
            self.assertEqual(document.pad.writes, [8.0])
            self.assertEqual(document.recompute_calls, [([document.body], False, True)])
            self.assertEqual(status(token), final)
            self.assertEqual(presenters[0].confirm(), final)
            actual_apply.assert_called_once()
            self.assertFalse(approval_fixtures.PartApprovalTests.PRIVATE_KEYS & set(business._plans[token]))
            public_text = json.dumps(final, ensure_ascii=True, separators=(',', ':'))
            self.assertNotIn('synthetic-material', public_text)
            self.assertNotIn('cached_native_typed_snapshot', public_text)
            self.assertLessEqual(len(public_text.encode()), 4096)


if __name__ == "__main__":
    unittest.main()
