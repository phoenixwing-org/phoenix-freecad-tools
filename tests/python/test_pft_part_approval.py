# SPDX-License-Identifier: Apache-2.0
"""Native approval grants and memory-only status, using independent Python fixtures."""
from contextlib import ExitStack
from copy import deepcopy
import json
import threading
import types
import unittest
from unittest.mock import patch

import test_pft_business as fixtures
import test_pft_bridge as bridge_fixtures

business = fixtures.business
Error = fixtures.Error


class Presenter:
    def __init__(self, gui, preview, confirm, cancel, status):
        self.gui, self.preview = gui, preview
        self.confirm, self.cancel, self.status = confirm, cancel, status
        self.presentations, self.closures = 1, 0
    def present(self): self.presentations += 1
    def close(self): self.closures += 1


class ForbiddenNative:
    def __getattribute__(self, name):
        raise AssertionError("status must not read native state")


class PartApprovalTests(unittest.TestCase):
    DTO_KEYS = {"document", "operation", "token", "state", "changed_count", "requires_confirmation",
                "saves_document", "storage", "native_confirmation", "result", "error_code"}
    PRIVATE_KEYS = {"document_ref", "objects", "snapshots", "plan", "link_states", "public_preview",
                    "approval_grant", "approval_view", "approval_ready", "approval_epoch"}

    def setUp(self):
        with business._lock:
            business._plans.clear()
        business.begin_approval_lifecycle()  # No server, Qt or native configuration.
        self.part = fixtures.Object("Part", "Old example", PartNumber="5120", PartVersion="001", PartName="Example part")
        self.other = fixtures.Object("Other", "Other example")
        self.document = fixtures.Document("ExampleDoc", [self.part, self.other])
        self.documents = {self.document.Name: self.document}
        self.preferences = fixtures.Preferences()
        self.app = types.SimpleNamespace(ActiveDocument=self.document, getDocument=self.documents.get,
            listDocuments=lambda: self.documents, getActiveTransaction=lambda: self.document.active_transaction,
            ParamGet=lambda path: self.preferences)
        self.gui_document = types.SimpleNamespace(getInEdit=lambda: None)
        self.gui = types.SimpleNamespace(getDocument=lambda name: self.gui_document)
        self.presenters = []
        self.open = patch.object(business, "_open_approval", side_effect=self.open_presenter)
        self.open.start()
        self.addCleanup(self.open.stop)

    def open_presenter(self, *args):
        presenter = Presenter(*args)
        self.presenters.append(presenter)
        return presenter

    def call(self, method, params):
        return business.perform(method, params, self.app, self.gui, Error)

    def prepare(self, **updates):
        params = {"docName": self.document.Name, "objectNames": [self.part.Name], "operation": "normalize_names"}
        params.update(updates)
        return self.call("prepare_part_changes", params)

    def request(self, token):
        result = self.call("request_part_change_approval", {"token": token})
        self.assert_dto(result)
        return result

    def status(self, token):
        result = business.perform("get_part_change_status", {"token": token}, None, None, Error)
        self.assert_dto(result)
        return result

    def assert_dto(self, result):
        self.assertEqual(set(result), self.DTO_KEYS)
        self.assertIs(result["requires_confirmation"], True)
        self.assertIs(result["saves_document"], False)
        self.assertEqual(result["storage"], "memory_only")
        self.assertIs(type(result["native_confirmation"]), bool)
        self.assertIs(type(result["changed_count"]), int)
        self.assertTrue(0 <= result["changed_count"] <= 500)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode()), 4096)
        if result["state"] == "applied":
            self.assertEqual(result["result"], {"changed_count": result["changed_count"], "saves_document": False})
        else:
            self.assertIsNone(result["result"])
        if result["state"] == "failed":
            self.assertIn(result["error_code"], business._FIXED_ERRORS)
        else:
            self.assertIsNone(result["error_code"])

    def assert_no_write(self):
        self.assertEqual(self.document.events, [])
        self.assertEqual(self.preferences.events, [])
        self.assertEqual(self.part.Label, "Old example")
        self.assertEqual(self.other.Label, "Other example")

    def assert_terminal_cleanup(self, token):
        self.assertFalse(self.PRIVATE_KEYS & set(business._plans[token]))

    def assert_error(self, code, callback):
        with self.assertRaises(Error) as failure:
            callback()
        self.assertEqual(failure.exception.code, code)

    def test_request_is_read_only_and_native_view_gets_complete_copied_preview(self):
        preview = self.prepare()
        original = deepcopy(preview)
        record = business._plans[preview["token"]]
        self.assertEqual(record["public_preview"], preview)
        preview["rows"][0]["new"] = "Changed caller copy"
        state = self.request(original["token"])
        self.assertEqual(state["state"], "awaiting_confirmation")
        self.assertFalse(state["native_confirmation"])
        self.assertEqual(self.presenters[0].preview, original)
        self.presenters[0].preview["rows"][0]["new"] = "Changed view copy"
        self.assertEqual(record["public_preview"], original)
        self.assert_no_write()

    def test_same_token_request_presents_existing_view_without_extending_deadline(self):
        preview = self.prepare()
        deadline = business._plans[preview["token"]]["deadline"]
        first = self.request(preview["token"])
        second = self.request(preview["token"])
        self.assertEqual(first, second)
        self.assertEqual(len(self.presenters), 1)
        self.assertEqual(self.presenters[0].presentations, 2)
        self.assertEqual(business._plans[preview["token"]]["deadline"], deadline)
        self.assert_no_write()

    def test_maximum_one_pending_view_does_not_consume_another_prepared_token(self):
        first, second = self.prepare(), self.prepare()
        self.request(first["token"])
        self.assert_error("PART_APPROVAL_BUSY", lambda: self.request(second["token"]))
        self.assertEqual(self.status(second["token"])["state"], "prepared")
        self.presenters[0].cancel()
        self.assertEqual(self.request(second["token"])["state"], "awaiting_confirmation")
        self.assertEqual(len(self.presenters), 2)
        self.assert_no_write()

    def test_rpc_flags_and_modified_targets_cannot_forge_confirmation(self):
        preview = self.prepare()
        for method in ("request_part_change_approval", "get_part_change_status", "apply_part_changes"):
            for name, value in (("confirmed", True), ("approved", True), ("native_confirmation", True),
                                ("grant", "example"), ("operation", "set_properties"), ("values", {})):
                with self.subTest(method=method, field=name):
                    self.assert_error("INVALID_PARAMS", lambda: self.call(method, {"token": preview["token"], name: value}))
            for token in (None, True, 42, "", "a" * 15, "a" * 129, "example token"):
                with self.subTest(method=method, token=token):
                    self.assert_error("INVALID_PARAMS", lambda: self.call(method, {"token": token}))
        self.assertEqual(self.presenters, [])
        self.assert_no_write()

    def test_external_apply_pending_is_blocked_and_real_view_remains_usable(self):
        preview = self.prepare()
        self.request(preview["token"])
        self.assert_error("PART_APPROVAL_PENDING", lambda: self.call("apply_part_changes", {"token": preview["token"]}))
        self.assertEqual(self.status(preview["token"])["state"], "awaiting_confirmation")
        self.assert_no_write()
        confirmed = self.presenters[0].confirm()
        self.assert_dto(confirmed)
        self.assertEqual(confirmed["state"], "applied")
        self.assertTrue(confirmed["native_confirmation"])
        self.assertEqual(self.part.Label, "5120.001-Example part")
        self.assertEqual(self.document.events, ["open", "commit"])
        self.assert_terminal_cleanup(preview["token"])
        self.assertEqual(self.presenters[0].confirm(), confirmed)
        self.assertEqual(self.presenters[0].cancel(), confirmed)
        self.assertEqual(self.request(preview["token"]), confirmed)
        replay = self.call("apply_part_changes", {"token": preview["token"]})
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.document.events, ["open", "commit"])

    def test_advanced_external_apply_keeps_native_confirmation_false(self):
        preview = self.prepare()
        self.call("apply_part_changes", {"token": preview["token"]})
        state = self.status(preview["token"])
        self.assertEqual(state["state"], "applied")
        self.assertFalse(state["native_confirmation"])
        self.assertEqual(self.presenters, [])
        self.assert_terminal_cleanup(preview["token"])

    def test_cancel_late_confirm_and_duplicates_never_write(self):
        preview = self.prepare()
        self.request(preview["token"])
        cancelled = self.presenters[0].cancel()
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertFalse(cancelled["native_confirmation"])
        self.assertEqual(self.presenters[0].confirm(), cancelled)
        self.assertEqual(self.presenters[0].cancel(), cancelled)
        self.assertEqual(self.request(preview["token"]), cancelled)
        self.assert_terminal_cleanup(preview["token"])
        self.assert_no_write()

    def test_pending_expiry_and_status_never_call_view_or_native_state(self):
        with patch.object(business.time, "monotonic", return_value=100):
            preview = self.prepare()
            self.request(preview["token"])
        view = self.presenters[0]
        with patch.object(business.time, "monotonic", return_value=400), \
             patch.object(business, "_mvc", side_effect=AssertionError("status imported native MVC")):
            state = business.perform("get_part_change_status", {"token": preview["token"]}, ForbiddenNative(), ForbiddenNative(), Error)
            self.assertEqual(state["state"], "expired")
            self.assertEqual(view.confirm(), state)
        self.assertEqual((view.presentations, view.closures), (1, 0))
        self.assert_terminal_cleanup(preview["token"])
        self.assert_no_write()

    def test_request_does_not_refresh_original_preview_ttl(self):
        with patch.object(business.time, "monotonic", return_value=100):
            preview = self.prepare()
        with patch.object(business.time, "monotonic", return_value=399):
            self.assertEqual(self.request(preview["token"])["state"], "awaiting_confirmation")
        with patch.object(business.time, "monotonic", return_value=400):
            self.assertEqual(self.presenters[0].confirm()["state"], "expired")
        self.assert_no_write()

    def test_status_route_is_lock_only_and_safe_from_worker_thread(self):
        preview = self.prepare()
        self.request(preview["token"])
        states = []
        with patch.object(business, "_mvc", side_effect=AssertionError("status imported MVC")):
            worker = threading.Thread(target=lambda: states.append(business.perform("get_part_change_status",
                {"token": preview["token"]}, ForbiddenNative(), ForbiddenNative(), Error)))
            worker.start()
            worker.join()
        self.assertEqual(states[0]["state"], "awaiting_confirmation")
        states[0]["state"] = "caller changed copy"
        self.assertEqual(self.status(preview["token"])["state"], "awaiting_confirmation")
        self.assert_no_write()

    def test_stale_binding_at_request_fails_before_open_and_drops_native_references(self):
        preview = self.prepare()
        self.part.PartName = "Externally changed"
        state = self.request(preview["token"])
        self.assertEqual((state["state"], state["error_code"]), ("failed", "PART_PLAN_STALE"))
        self.assertFalse(state["native_confirmation"])
        self.assertEqual(self.presenters, [])
        self.assertEqual(self.document.events, [])
        self.assert_terminal_cleanup(preview["token"])

    def test_confirm_rechecks_active_document_object_edit_and_transaction_states(self):
        for change in ("active", "object", "edit", "pending", "label"):
            with self.subTest(change=change):
                self.setUp()
                preview = self.prepare()
                self.request(preview["token"])
                if change == "active": self.app.ActiveDocument = None
                if change == "object": self.document.Objects[0] = fixtures.Object("Part", "Replacement")
                if change == "edit": self.gui_document.getInEdit = lambda: False
                if change == "pending": self.document.HasPendingTransaction = True
                if change == "label": self.part.Label = "Changed externally"
                state = self.presenters[0].confirm()
                self.assertEqual(state["state"], "failed")
                self.assertTrue(state["native_confirmation"])
                self.assertIn(state["error_code"], {"DOCUMENT_NOT_ACTIVE", "PART_PLAN_STALE"})
                self.assertEqual(self.document.events, [])
                self.assert_terminal_cleanup(preview["token"])
                self.assertEqual(self.presenters[0].confirm(), state)

    def test_native_failure_status_has_only_fixed_codes_and_no_raw_messages(self):
        _, controller = business._mvc()
        for error, code in ((RuntimeError("synthetic native details"), "PART_CHANGE_FAILED"),
                            (controller.PftWriteError("PART_TRANSACTION_CHANGED", "synthetic native details"), "PART_TRANSACTION_CHANGED")):
            with self.subTest(code=code):
                self.setUp()
                preview = self.prepare()
                self.request(preview["token"])
                with patch.object(controller, "apply_rename", side_effect=error):
                    state = self.presenters[0].confirm()
                self.assertEqual((state["state"], state["error_code"]), ("failed", code))
                self.assertNotIn("synthetic native details", json.dumps(state))
                self.assert_terminal_cleanup(preview["token"])
                self.assert_no_write()

    def test_ui_open_failure_invalidates_even_half_constructed_callbacks(self):
        preview = self.prepare()
        captured = []
        def fail_open(*args):
            captured.append(Presenter(*args))
            raise RuntimeError("synthetic presenter details")
        with patch.object(business, "_open_approval", side_effect=fail_open):
            state = self.request(preview["token"])
        self.assertEqual((state["state"], state["error_code"]), ("failed", "PART_APPROVAL_UI_UNAVAILABLE"))
        self.assertFalse(state["native_confirmation"])
        self.assertNotIn("synthetic presenter details", json.dumps(state))
        self.assertEqual(captured[0].confirm(), state)
        self.assert_terminal_cleanup(preview["token"])
        self.assert_no_write()

    def test_presenter_must_return_own_handle_and_opening_callback_cannot_confirm(self):
        preview = self.prepare()
        early = []
        def open_with_early_confirm(*args):
            view = self.open_presenter(*args)
            early.append(view.confirm())
            return view
        with patch.object(business, "_open_approval", side_effect=open_with_early_confirm):
            state = self.request(preview["token"])
        self.assertEqual(state["state"], "awaiting_confirmation")
        self.assertFalse(early[0]["native_confirmation"])
        self.assert_no_write()
        self.presenters[0].cancel()
        preview = self.prepare()
        with patch.object(business, "_open_approval", return_value=object()):
            state = self.request(preview["token"])
        self.assertEqual(state["error_code"], "PART_APPROVAL_UI_UNAVAILABLE")
        self.assert_no_write()

    def test_represent_cannot_synchronously_invoke_confirmation(self):
        preview = self.prepare()
        self.request(preview["token"])
        early = []
        with patch.object(self.presenters[0], "present", side_effect=lambda: early.append(self.presenters[0].confirm())):
            state = self.request(preview["token"])
        self.assertEqual(state["state"], "awaiting_confirmation")
        self.assertFalse(early[0]["native_confirmation"])
        self.assert_no_write()
        self.assertEqual(self.presenters[0].confirm()["state"], "applied")

    def test_repeated_request_rechecks_binding_before_presenting_same_view(self):
        preview = self.prepare()
        self.request(preview["token"])
        self.document.HasPendingTransaction = True
        state = self.request(preview["token"])
        self.assertEqual((state["state"], state["error_code"]), ("failed", "PART_PLAN_STALE"))
        self.assertEqual(self.presenters[0].presentations, 1)
        self.assertFalse(state["native_confirmation"])
        self.assert_no_write()

    def test_unknown_callback_failure_is_fixed_and_invalidates_grant(self):
        preview = self.prepare()
        self.request(preview["token"])
        with patch.object(business, "_apply", side_effect=RuntimeError("synthetic callback details")):
            state = self.presenters[0].confirm()
        self.assertEqual((state["state"], state["error_code"]), ("failed", "PART_CHANGE_FAILED"))
        self.assertNotIn("synthetic callback details", json.dumps(state))
        self.assert_terminal_cleanup(preview["token"])
        self.assert_no_write()

    def test_failing_represent_marks_ui_failure_without_native_write(self):
        preview = self.prepare()
        self.request(preview["token"])
        with patch.object(self.presenters[0], "present", side_effect=RuntimeError("synthetic view details")):
            state = self.request(preview["token"])
        self.assertEqual(state["error_code"], "PART_APPROVAL_UI_UNAVAILABLE")
        self.assert_terminal_cleanup(preview["token"])
        self.assert_no_write()

    def test_wrong_thread_callback_never_grants_native_confirmation(self):
        preview = self.prepare()
        self.request(preview["token"])
        states = []
        worker = threading.Thread(target=lambda: states.append(self.presenters[0].confirm()))
        worker.start()
        worker.join()
        self.assertEqual((states[0]["state"], states[0]["error_code"]), ("failed", "GUI_THREAD_REQUIRED"))
        self.assertFalse(states[0]["native_confirmation"])
        self.assert_no_write()

    def test_stop_invalidates_pending_without_gui_access_even_when_server_is_absent(self):
        preview = self.prepare()
        self.request(preview["token"])
        view = self.presenters[0]
        bridge = bridge_fixtures.bridge
        with patch.object(bridge, "_business", business), patch.object(bridge, "_server", None), \
             patch.object(bridge, "_dispatcher", None), patch.object(bridge, "_stop_thread", None):
            bridge.stop_server()
        self.assertEqual(self.status(preview["token"])["state"], "cancelled")
        self.assertEqual(view.confirm()["state"], "cancelled")
        self.assertEqual((view.presentations, view.closures), (1, 0))
        self.assert_terminal_cleanup(preview["token"])
        self.assert_no_write()

    def test_stop_during_apply_does_not_abort_owned_or_foreign_transactions(self):
        preview = self.prepare()
        self.request(preview["token"])
        _, controller = business._mvc()
        original = controller.apply_rename
        def apply_with_stop(*args):
            self.assertEqual(self.status(preview["token"])["state"], "applying")
            business.cancel_pending_approvals()
            self.assertEqual(self.status(preview["token"])["state"], "applying")
            return original(*args)
        with patch.object(controller, "apply_rename", side_effect=apply_with_stop):
            state = self.presenters[0].confirm()
        self.assertEqual(state["state"], "applied")
        self.assertEqual(self.document.events, ["open", "commit"])
        self.document.active_transaction = ("Foreign fixture operation", 900)
        business.cancel_pending_approvals()
        self.assertEqual(self.document.active_transaction, ("Foreign fixture operation", 900))
        self.assertEqual(self.document.events, ["open", "commit"])

    def test_stop_while_presenter_opens_prevents_late_confirmation(self):
        preview = self.prepare()
        def stop_on_open(*args):
            view = self.open_presenter(*args)
            business.cancel_pending_approvals()
            return view
        with patch.object(business, "_open_approval", side_effect=stop_on_open):
            state = self.request(preview["token"])
        self.assertEqual(state["state"], "cancelled")
        self.assertEqual(self.presenters[0].confirm(), state)
        self.assert_no_write()

    def test_apply_crossing_deadline_still_reports_applied_and_terminal_summary_persists(self):
        clock = [100]
        _, controller = business._mvc()
        original = controller.apply_rename
        def apply_across_deadline(*args):
            clock[0] = 500
            self.assertEqual(self.status(preview["token"])["state"], "applying")
            return original(*args)
        with patch.object(business.time, "monotonic", side_effect=lambda: clock[0]):
            preview = self.prepare()
            self.request(preview["token"])
            with patch.object(controller, "apply_rename", side_effect=apply_across_deadline):
                state = self.presenters[0].confirm()
            self.assertEqual(state["state"], "applied")
            clock[0] = 10000
            self.assertEqual(self.status(preview["token"]), state)
            self.assertEqual(self.presenters[0].confirm(), state)
        self.assert_terminal_cleanup(preview["token"])
        self.assertEqual(self.document.events, ["open", "commit"])

    def test_terminal_capacity_eviction_never_resurrects_callback_or_allows_rewrite(self):
        preview = self.prepare()
        self.request(preview["token"])
        view = self.presenters[0]
        state = view.confirm()
        self.assertEqual(state["state"], "applied")
        for _ in range(32): self.prepare()
        self.assertEqual(len(business._plans), 32)
        self.assertNotIn(preview["token"], business._plans)
        self.assert_error("PART_TOKEN_NOT_FOUND", lambda: self.status(preview["token"]))
        self.assert_error("PART_TOKEN_NOT_FOUND", lambda: self.call("apply_part_changes", {"token": preview["token"]}))
        self.assertEqual(view.confirm(), state)
        self.assertEqual(self.document.events, ["open", "commit"])

    def test_blocked_and_unchanged_requests_do_not_open_confirmation(self):
        self.other.Label = "5120.001-Example part"
        blocked = self.prepare()
        state = self.request(blocked["token"])
        self.assertEqual(state["error_code"], "PART_PREVIEW_BLOCKED")
        self.assertFalse(state["native_confirmation"])
        self.other.Label = "Other example"
        self.part.Label = "5120.001-Example part"
        unchanged = self.prepare()
        state = self.request(unchanged["token"])
        self.assertEqual(state["error_code"], "PART_APPROVAL_NO_CHANGES")
        self.assertEqual(self.presenters, [])
        self.assertEqual(self.document.events, [])

    def test_legacy_non_geometric_operations_use_the_same_native_approval_grant(self):
        for operation in ("normalize_names", "sync_link_labels", "initialize_properties", "recommend_properties", "set_properties"):
            with self.subTest(operation=operation):
                self.setUp()
                updates = {"operation": operation}
                if operation == "recommend_properties":
                    self.part.Label = "5120.001-Example part"
                    self.part.PartName = ""
                if operation == "sync_link_labels":
                    link = fixtures.Object("Instance", "Old instance")
                    link.TypeId, link.LinkedObject, link.Document = "App::Link", self.part, self.document
                    self.document.Objects.append(link)
                    updates["objectNames"] = [link.Name]
                if operation == "set_properties":
                    updates["values"] = {self.part.Name: {"PartName": "New example"}}
                preview = self.prepare(**updates)
                self.assertTrue(preview["can_apply"])
                self.assertGreater(preview["changed_count"], 0)
                self.request(preview["token"])
                self.assertEqual(self.document.events, [])
                state = self.presenters[0].confirm()
                self.assertEqual(state["state"], "applied")
                self.assertTrue(state["native_confirmation"])
                self.assertEqual(self.document.events, ["open", "commit"])

    def test_bridge_capability_and_status_bypass_gui_dispatch(self):
        preview = self.prepare()
        bridge = bridge_fixtures.bridge
        expected = {"version": 1, "request_method": "request_part_change_approval", "status_method": "get_part_change_status",
                    "confirmation": "native_gui_only", "max_pending": 1, "storage": "memory_only",
                    "ttl_seconds": 300, "max_status_bytes": 4096}
        with patch.object(bridge, "_business", business), \
             patch.object(bridge, "_dispatch_gui", side_effect=AssertionError("status dispatched GUI")), \
             patch.object(business, "_mvc", side_effect=AssertionError("status imported MVC")):
            self.assertEqual(bridge.get_status()["part_approvals"], expected)
            result = bridge.process_request({"method": "get_part_change_status", "params": {"token": preview["token"]}})
            self.assertTrue(result["ok"])
            self.assertEqual(result["data"]["state"], "prepared")
        with patch.object(bridge, "_dispatch_gui", return_value={"native": "request"}) as dispatch:
            result = bridge.process_request({"method": "request_part_change_approval", "params": {"token": preview["token"]}})
            self.assertTrue(result["ok"])
            dispatch.assert_called_once_with("request_part_change_approval", {"token": preview["token"]})

    def fake_bridge_lifecycle(self):
        """Replace every start/stop side effect; fake HTTP threads never serve."""
        bridge = bridge_fixtures.bridge
        state = types.SimpleNamespace(servers=[], dispatchers=[], threads=[])
        class Server:
            server_address = ("127.0.0.1", 12345)
            def __init__(self, *args): self.closed = False; state.servers.append(self)
            def serve_forever(self, **kwargs): raise AssertionError("fake server must not serve")
            def shutdown(self): self.stopped = True
            def server_close(self): self.closed = True
        class Dispatcher:
            def __init__(self): self.closed = False; self.approval_epoch = 0; state.dispatchers.append(self)
            def close(self): self.closed = True
        class Thread:
            def __init__(self, target, kwargs=None, name="", **ignored):
                self.target, self.kwargs, self.name, self.alive = target, kwargs or {}, name, False
                self.start_calls = 0
                state.threads.append(self)
            def start(self):
                self.start_calls += 1
                if self.name.endswith("-Stop"):
                    self.target(**self.kwargs)
                else:
                    self.alive = True
            def is_alive(self): return self.alive
        stack = ExitStack()
        for name, value in (("_business", business), ("_server", None), ("_server_thread", None),
                            ("_dispatcher", None), ("_stop_thread", None),
                            ("_create_gui_dispatcher", Dispatcher), ("_PftBridgeHttpServer", Server)):
            stack.enter_context(patch.object(bridge, name, value))
        stack.enter_context(patch.object(bridge.threading, "Thread", Thread))
        self.addCleanup(stack.close)
        return bridge, state, Thread

    def test_stop_without_server_closes_gate_until_successful_new_start(self):
        bridge, state, _ = self.fake_bridge_lifecycle()
        preview = self.prepare()
        bridge.stop_server()
        self.assertFalse(business._approvals_enabled)
        with patch.object(business, "_mvc", side_effect=AssertionError("closed gate must not access MVC")):
            self.assert_error("SERVER_NOT_RUNNING", lambda: self.request(preview["token"]))
        self.assertEqual(self.presenters, [])
        self.assertTrue(bridge.start_server()["running"])
        self.assertTrue(business._approvals_enabled)
        self.assertEqual(state.dispatchers[-1].approval_epoch, business._approval_epoch)
        self.assertEqual(self.request(preview["token"])["state"], "awaiting_confirmation")
        self.assert_no_write()

    def test_stop_cancel_then_dispatch_close_barrier_rejects_new_grant(self):
        RealThread = threading.Thread
        bridge, _, _ = self.fake_bridge_lifecycle()
        bridge.start_server()
        preview = self.prepare()
        cancelled, resume = threading.Event(), threading.Event()
        original = business.cancel_pending_approvals
        failures = []
        def paused_cancel():
            original()
            cancelled.set()
            if not resume.wait(3): raise AssertionError("fake stop barrier timed out")
        def stop():
            try: bridge.stop_server()
            except BaseException as error: failures.append(type(error).__name__)
        with patch.object(business, "cancel_pending_approvals", side_effect=paused_cancel):
            worker = RealThread(target=stop)
            worker.start()
            try:
                self.assertTrue(cancelled.wait(3))
                self.assert_error("SERVER_NOT_RUNNING", lambda: self.request(preview["token"]))
            finally:
                resume.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(self.presenters, [])
        self.assertFalse(business._approvals_enabled)
        self.assert_no_write()

    def test_already_started_request_crossing_stop_cannot_take_new_epoch(self):
        RealThread = threading.Thread
        bridge, _, _ = self.fake_bridge_lifecycle()
        bridge.start_server()
        preview = self.prepare()
        reached_mvc, stopped = threading.Event(), threading.Event()
        original = business._mvc
        failures = []
        def pause_mvc():
            reached_mvc.set()
            if not stopped.wait(3): raise AssertionError("fake request barrier timed out")
            return original()
        def stop():
            try:
                if not reached_mvc.wait(3): raise AssertionError("request not started")
                bridge.stop_server()
            except BaseException as error: failures.append(type(error).__name__)
            finally: stopped.set()
        worker = RealThread(target=stop)
        worker.start()
        try:
            with patch.object(business, "_mvc", side_effect=pause_mvc):
                self.assert_error("SERVER_STOPPING", lambda: self.request(preview["token"]))
        finally:
            stopped.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(self.presenters, [])
        self.assert_no_write()

    def test_old_dispatch_epoch_cannot_enter_after_stop_and_successful_restart(self):
        bridge, state, _ = self.fake_bridge_lifecycle()
        bridge.start_server()
        old_epoch = state.dispatchers[-1].approval_epoch
        preview = self.prepare()
        bridge.stop_server()
        bridge.start_server()
        new_epoch = state.dispatchers[-1].approval_epoch
        self.assertNotEqual(old_epoch, new_epoch)
        with patch.object(business, "_mvc", side_effect=AssertionError("old job must not access MVC")):
            self.assert_error("SERVER_STOPPING", lambda: business.perform("request_part_change_approval",
                {"token": preview["token"]}, self.app, self.gui, Error, _approval_epoch=old_epoch))
        self.assertEqual(self.presenters, [])
        self.assertEqual(business.perform("request_part_change_approval", {"token": preview["token"]},
            self.app, self.gui, Error, _approval_epoch=new_epoch)["state"], "awaiting_confirmation")
        self.assert_no_write()

    def test_start_and_stop_serialize_gate_transitions(self):
        RealThread = threading.Thread
        bridge, state, _ = self.fake_bridge_lifecycle()
        bridge.start_server()
        old_dispatcher = state.dispatchers[-1]
        cancelled, resume, start_called, start_done = (threading.Event() for _ in range(4))
        original = business.cancel_pending_approvals
        failures = []
        def paused_cancel():
            original()
            if threading.current_thread().name == "synthetic-stop":
                cancelled.set()
                if not resume.wait(3): raise AssertionError("serial stop barrier timed out")
        def stop():
            try: bridge.stop_server()
            except BaseException as error: failures.append(type(error).__name__)
        def start():
            start_called.set()
            try: bridge.start_server()
            except BaseException as error: failures.append(type(error).__name__)
            finally: start_done.set()
        with patch.object(business, "cancel_pending_approvals", side_effect=paused_cancel):
            stopper = RealThread(target=stop, name="synthetic-stop")
            starter = RealThread(target=start, name="synthetic-start")
            stopper.start()
            try:
                self.assertTrue(cancelled.wait(3))
                starter.start()
                self.assertTrue(start_called.wait(3))
                self.assertFalse(start_done.wait(0.05))
            finally:
                resume.set()
                stopper.join(3)
                if starter.ident is not None: starter.join(3)
        self.assertFalse(stopper.is_alive())
        self.assertFalse(starter.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(old_dispatcher.closed)
        self.assertIs(bridge._dispatcher, state.dispatchers[-1])
        self.assertIsNot(bridge._dispatcher, old_dispatcher)
        self.assertTrue(business._approvals_enabled)
        preview = self.prepare()
        self.assertEqual(self.request(preview["token"])["state"], "awaiting_confirmation")
        self.assert_no_write()

    def test_failed_start_keeps_gate_closed_and_cleans_partial_components(self):
        bridge, state, Thread = self.fake_bridge_lifecycle()
        preview = self.prepare()
        failures = (("dispatcher", "START_FAILED"), ("server", "PORT_UNAVAILABLE"),
                    ("thread_constructor", "START_FAILED"), ("lifecycle", "START_FAILED"), ("thread_start", "START_FAILED"))
        for mode, code in failures:
            with self.subTest(mode=mode):
                business.begin_approval_lifecycle()
                with ExitStack() as stack:
                    if mode == "dispatcher":
                        stack.enter_context(patch.object(bridge, "_create_gui_dispatcher",
                            side_effect=bridge.PftBridgeError("START_FAILED", "synthetic dispatcher failure")))
                    elif mode == "server":
                        stack.enter_context(patch.object(bridge, "_PftBridgeHttpServer", side_effect=OSError("synthetic port busy")))
                    elif mode == "thread_constructor":
                        stack.enter_context(patch.object(bridge.threading, "Thread", side_effect=RuntimeError("synthetic thread failure")))
                    elif mode == "lifecycle":
                        original_begin = business.begin_approval_lifecycle
                        def incomplete_begin():
                            original_begin()
                            raise RuntimeError("synthetic lifecycle initialization failure")
                        stack.enter_context(patch.object(business, "begin_approval_lifecycle", side_effect=incomplete_begin))
                    else:
                        stack.enter_context(patch.object(Thread, "start", side_effect=RuntimeError("synthetic thread failure")))
                    with self.assertRaises(bridge.PftBridgeError) as raised: bridge.start_server()
                self.assertEqual(raised.exception.code, code)
                self.assertFalse(business._approvals_enabled)
                self.assertIsNone(bridge._server)
                self.assertIsNone(bridge._dispatcher)
                if mode != "dispatcher": self.assertTrue(state.dispatchers[-1].closed)
                if mode in {"thread_constructor", "lifecycle", "thread_start"}: self.assertTrue(state.servers[-1].closed)
                if mode == "lifecycle":
                    self.assertEqual(state.threads[-1].start_calls, 0)
                    self.assertFalse(state.threads[-1].alive)
                self.assert_error("SERVER_NOT_RUNNING", lambda: self.request(preview["token"]))
                self.assert_no_write()
        self.assertTrue(bridge.start_server()["running"])
        self.assertTrue(business._approvals_enabled)
        self.assertEqual(self.request(preview["token"])["state"], "awaiting_confirmation")

    def test_bridge_stop_during_apply_preserves_owned_transaction_and_late_callbacks(self):
        bridge, _, _ = self.fake_bridge_lifecycle()
        bridge.start_server()
        preview = self.prepare()
        self.request(preview["token"])
        _, controller = business._mvc()
        original = controller.apply_rename
        def apply_then_stop(*args):
            self.assertEqual(self.status(preview["token"])["state"], "applying")
            bridge.stop_server()
            self.assertEqual(self.status(preview["token"])["state"], "applying")
            return original(*args)
        with patch.object(controller, "apply_rename", side_effect=apply_then_stop):
            state = self.presenters[0].confirm()
        self.assertEqual(state["state"], "applied")
        self.assertEqual(self.document.events, ["open", "commit"])
        self.assertFalse(business._approvals_enabled)
        self.assertEqual(self.presenters[0].confirm(), state)
        self.assertEqual(self.document.events, ["open", "commit"])

    def test_strict_sibling_origin_rejects_another_installation_presenter(self):
        preview = self.prepare()
        foreign = types.SimpleNamespace(__file__=str(fixtures.ADDON / "different-installation" / "pft_part_approval_views.py"))
        with patch.dict("sys.modules", {"pft_part_approval_views": foreign}), \
             patch.object(business, "_open_approval", wraps=self.open.temp_original):
            state = self.request(preview["token"])
        self.assertEqual(state["error_code"], "PART_APPROVAL_UI_UNAVAILABLE")
        self.assert_no_write()


if __name__ == "__main__":
    unittest.main()
