# SPDX-License-Identifier: Apache-2.0
import http.client
import importlib.util
import json
from pathlib import Path
import queue
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

BRIDGE_PATH = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools" / "pft_bridge.py"
spec = importlib.util.spec_from_file_location("pft_bridge_test", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class ProtocolTests(unittest.TestCase):
    def test_development_port_is_isolated_and_bad_development_configuration_is_rejected(self):
        self.assertEqual(bridge._default_port({}), 9876)
        self.assertEqual(bridge._default_port({"PFT_DEVELOPMENT_PORT": "9877"}), 9876)
        self.assertEqual(bridge._default_port({"PFT_DEVELOPMENT": "1"}), 9877)
        self.assertEqual(bridge._default_port({"PFT_DEVELOPMENT": "1", "PFT_DEVELOPMENT_PORT": "9878"}), 9878)
        for value in ("", "-1", "0", "65536", "9877;other", "９８７７"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                bridge._default_port({"PFT_DEVELOPMENT": "1", "PFT_DEVELOPMENT_PORT": value})

    def test_ping_and_status_without_freecad_or_qt(self):
        self.assertEqual(bridge.process_request({"method": "ping"}), {"ok": True, "data": {"pong": True}})
        status = bridge.process_request({"method": "get_status", "params": {}})
        self.assertTrue(status["ok"])
        self.assertEqual(status["data"]["protocol_version"], 1)
        self.assertEqual(status["data"]["addon_version"], "0.4.1")
        self.assertFalse(status["data"]["running"])

    def test_drawing_capability_is_exact_detached_and_input_dispatch_remains_strict(self):
        expected = {"version": 1, "operation": "create_drawing", "confirmation": "native_gui_only",
            "preview_method": "prepare_part_changes", "request_method": "request_part_change_approval",
            "status_method": "get_part_change_status", "paper": "A4_landscape", "projection": "first_angle",
            "views": ["front", "top", "axonometric"], "source_scope": "single_native_solid",
            "saves_document": False, "projection_verification": "not_checked"}
        status = bridge.get_status()
        self.assertEqual(status["drawing_create"], expected)
        status["drawing_create"]["views"].clear()
        self.assertEqual(bridge.get_status()["drawing_create"], expected)
        with patch.object(bridge, "_dispatch_gui", return_value={"synthetic": True}) as dispatch:
            params = {"docName": "ExampleDoc", "objectNames": ["Source"], "operation": "create_drawing"}
            self.assertTrue(bridge.process_request({"method": "prepare_part_changes", "params": params})["ok"])
            dispatch.assert_called_once_with("prepare_part_changes", params)
            dispatch.reset_mock()
            for fields in ({"values": {}}, {"bodyName": "Body"}, {"lengthMm": 10}, {"confirmed": True},
                           {"objectNames": ["Source", "Other"]}):
                bad = dict(params); bad.update(fields)
                self.assertFalse(bridge.process_request({"method": "prepare_part_changes", "params": bad})["ok"])
            dispatch.assert_not_called()

    def test_design_capability_and_strict_dispatch_preserve_read_only_route(self):
        status = bridge.get_status()
        self.assertIn("inspect_design", status["supported_methods"])
        self.assertIn("inspect_design", status["read_provenance"]["methods"])
        self.assertEqual(status["design_inspection"]["constraint_index_base"], 0)
        self.assertTrue(status["design_inspection"]["read_only"])
        with patch.object(bridge, "_dispatch_gui", return_value={"public": True}) as dispatch:
            good = {"method": "inspect_design", "params": {"docName": "Public", "objectName": "Sketch", "constraintOffset": 0, "constraintLimit": 100}}
            self.assertTrue(bridge.process_request(good)["ok"])
            dispatch.assert_called_once_with("inspect_design", good["params"])
        with patch.object(bridge, "_dispatch_gui") as dispatch:
            for params in ({"objectName": "Sketch", "solve": True}, {"objectName": "Sketch", "constraintLimit": 101},
                           {"objectName": "Sketch", "constraintOffset": True}, {"objectName": "Sketch", "expectedRevision": 1}, {}):
                self.assertFalse(bridge.process_request({"method": "inspect_design", "params": params})["ok"])
            dispatch.assert_not_called()

    def test_rejects_code_unknown_fields_and_wrong_types(self):
        cases = [
            {"method": "execute_code", "params": {"code": "print(1)"}},
            {"method": "view_fit", "params": {"save": True}},
            {"method": "view_axonometric", "params": {"fit": "false"}},
            {"method": "view_fit", "params": {"docName": ""}},
            {"method": "ping", "params": []},
            {"method": "ping", "code": "noop"},
            [],
        ]
        with patch.object(bridge, "_dispatch_gui") as dispatch:
            for payload in cases:
                self.assertFalse(bridge.process_request(payload)["ok"])
            dispatch.assert_not_called()

    def test_dispatches_only_valid_fixed_method(self):
        with patch.object(bridge, "_dispatch_gui", return_value={"view": "axonometric"}) as dispatch:
            result = bridge.process_request({"method": "view_axonometric", "params": {"docName": "Doc", "fit": False}})
            self.assertTrue(result["ok"])
            dispatch.assert_called_once_with("view_axonometric", {"docName": "Doc", "fit": False})

    def test_script_replay_queries_cached_result_without_dispatch(self):
        params = {"requestId":"bridge-replay", "code":"App.counter += 1\nresult = App.counter"}
        app = types.SimpleNamespace(counter=0)
        with patch.object(bridge,"_dispatch_gui",side_effect=lambda method,args:bridge._runtime.execute(args,app,types.SimpleNamespace(),bridge.PftBridgeError)) as dispatch:
            first = bridge.process_request({"method":"execute_script","params":params})
            again = bridge.process_request({"method":"execute_script","params":params})
            queried = bridge.process_request({"method":"get_script_status","params":{"requestId":"bridge-replay"}})
        self.assertTrue(first["ok"])
        self.assertEqual(first,again)
        self.assertEqual(first,queried)
        self.assertEqual(app.counter,1)
        dispatch.assert_called_once()

    def test_script_queued_and_started_timeouts_have_distinct_states(self):
        for code, expected in [("GUI_QUEUE_TIMEOUT","cancelled"),("GUI_TIMEOUT","starting")]:
            request = "timeout-" + code
            with patch.object(bridge,"_dispatch_gui",side_effect=bridge.PftBridgeError(code,"timeout")):
                result = bridge.process_request({"method":"execute_script","params":{"requestId":request,"code":"pass"}})
            self.assertFalse(result["ok"])
            queried = bridge.process_request({"method":"get_script_status","params":{"requestId":request}})
            self.assertEqual(queried["data"]["state"],expected)

    def test_business_error_retains_machine_code(self):
        with patch.object(bridge, "_dispatch_gui", side_effect=bridge.PftBridgeError("GUI_QUEUE_TIMEOUT", "timeout")):
            self.assertEqual(bridge.process_request({"method": "view_fit"}), {"ok": False, "error": {"code": "GUI_QUEUE_TIMEOUT", "message": "timeout"}})

    def test_json_rejects_duplicate_and_nonfinite_values(self):
        for body in [b'{"method":"ping","method":"view_fit"}', b'{"x":NaN}', b'{"x":Infinity}', b'\xff']:
            with self.assertRaises((ValueError, UnicodeError)):
                bridge._decode_json(body)


class DrawingExportProtocolTests(unittest.TestCase):
    """Synthetic protocol orchestration, with no CAD, Qt, files or publishing."""
    def setUp(self):
        self.params = {"docName": "Example", "pageName": "Page", "format": "pdf", "requestId": "export-example"}
        self.preparing = {"token": "e" * 32, "request_id": "export-example", "state": "preparing"}

    def test_capability_is_exact_detached_and_does_not_probe_runtime(self):
        expected = {"version": 1, "formats": ["pdf"], "confirmation": "native_capture_and_publish",
                    "prepare_method": "prepare_drawing_export", "request_method": "request_drawing_export_approval",
                    "status_method": "get_drawing_export_status", "storage": "memory_only", "saves_document": False,
                    "max_pdf_bytes": 8388608, "preview_backend": "macos_coregraphics", "projection_verification": "not_checked"}
        with patch.object(bridge, "_dispatch_gui", side_effect=AssertionError("GUI access")), \
             patch.object(Path, "stat", side_effect=AssertionError("Runtime file probe")):
            first = bridge.get_status()
            self.assertEqual(first["drawing_export"], expected)
            self.assertTrue(set(bridge._EXPORT_METHODS).issubset(first["supported_methods"]))
            first["drawing_export"]["formats"].clear()
            self.assertEqual(bridge.get_status()["drawing_export"], expected)

    def test_prepare_claim_precedes_only_dispatch_and_replay_is_memory_only(self):
        prepared = {**self.preparing, "state": "prepared"}
        events = []
        def claim(params, Error):
            events.append("claim")
            self.assertEqual(params, self.params)
            return len(events) == 1, self.preparing if len(events) == 1 else prepared
        def dispatch(method, params):
            events.append("dispatch")
            self.assertEqual(events, ["claim", "dispatch"])
            self.assertEqual((method, params), ("prepare_drawing_export", self.params))
            return prepared
        with patch.object(bridge._drawing_export, "claim_prepare", side_effect=claim), \
             patch.object(bridge, "_dispatch_gui", side_effect=dispatch) as native:
            first = bridge.process_request({"method": "prepare_drawing_export", "params": self.params})
            second = bridge.process_request({"method": "prepare_drawing_export", "params": self.params})
        self.assertEqual(first, second)
        self.assertEqual(first, {"ok": True, "data": prepared})
        native.assert_called_once()

    def test_preparing_and_terminal_replays_never_dispatch_or_retry(self):
        for state in ("preparing", "prepared", "awaiting_confirmation", "publishing", "published", "cancelled", "failed", "expired", "unknown"):
            snapshot = {**self.preparing, "state": state}
            with self.subTest(state=state), patch.object(bridge._drawing_export, "claim_prepare", return_value=(False, snapshot)), \
                 patch.object(bridge, "_dispatch_gui") as dispatch:
                reply = bridge.process_request({"method": "prepare_drawing_export", "params": self.params})
            self.assertEqual(reply, {"ok": True, "data": snapshot})
            dispatch.assert_not_called()

    def test_prepare_dispatch_errors_are_forwarded_without_erasing_claim(self):
        for code in ("GUI_QUEUE_TIMEOUT", "GUI_TIMEOUT", "SERVER_STOPPING", "SERVER_NOT_RUNNING", "INSTANCE_TARGET_MISMATCH"):
            error = bridge.PftBridgeError(code, "fixed synthetic failure")
            with self.subTest(code=code), patch.object(bridge._drawing_export, "claim_prepare", return_value=(True, self.preparing)), \
                 patch.object(bridge._drawing_export, "dispatch_error") as record_error, \
                 patch.object(bridge, "_dispatch_gui", side_effect=error):
                reply = bridge.process_request({"method": "prepare_drawing_export", "params": self.params})
            self.assertEqual(reply["error"]["code"], code)
            record_error.assert_called_once_with(self.params["requestId"], error)

    def test_status_uses_only_export_memory_route(self):
        params = {"token": self.preparing["token"]}
        with patch.object(bridge._drawing_export, "perform", return_value=self.preparing) as perform, \
             patch.object(bridge, "_dispatch_gui", side_effect=AssertionError("GUI queue")), \
             patch.object(bridge, "_perform_gui", side_effect=AssertionError("Native imports")):
            reply = bridge.process_request({"method": "get_drawing_export_status", "params": params})
        self.assertEqual(reply, {"ok": True, "data": self.preparing})
        perform.assert_called_once_with("get_drawing_export_status", params, None, None, bridge.PftBridgeError)

    def test_external_path_confirmation_overwrite_and_unknown_fields_never_claim(self):
        with patch.object(bridge._drawing_export, "claim_prepare") as claim, patch.object(bridge, "_dispatch_gui") as dispatch:
            for extra in ({"path": "/synthetic/export.pdf"}, {"confirmed": True}, {"overwrite": True}, {"instance": "another"}, {"format": "svg"}):
                with self.subTest(extra=extra):
                    self.assertFalse(bridge.process_request({"method": "prepare_drawing_export", "params": {**self.params, **extra}})["ok"])
            for method in ("request_drawing_export_approval", "get_drawing_export_status"):
                self.assertFalse(bridge.process_request({"method": method, "params": {"token": self.preparing["token"], "confirmed": True}})["ok"])
        claim.assert_not_called(); dispatch.assert_not_called()

    def test_export_perform_is_given_its_own_epoch_and_gui_objects(self):
        app, gui = object(), object()
        with patch.dict(sys.modules, {"FreeCAD": app, "FreeCADGui": gui}), \
             patch.object(bridge._drawing_export, "perform", return_value=self.preparing) as perform:
            reply = bridge._perform_gui("prepare_drawing_export", self.params, _approval_epoch=73)
        self.assertEqual(reply, self.preparing)
        perform.assert_called_once_with("prepare_drawing_export", self.params, app, gui, bridge.PftBridgeError, _approval_epoch=73)


class DrawingExportClaimIntegrationTests(unittest.TestCase):
    """Real bounded export registry with a synthetic queue; no native modules."""
    def setUp(self):
        export = bridge._drawing_export
        for obj, key, value in ((export, "_records", type(export._records)()),
                                (export, "_requests", {}), (export, "_enabled", True), (export, "_epoch", 7),
                                (bridge, "_service_identity", None), (bridge, "_dispatcher", None), (bridge, "_server", None)):
            context = patch.object(obj, key, value); context.start(); self.addCleanup(context.stop)

    def test_real_claim_survives_known_and_unknown_queue_outcomes_without_recapture(self):
        for code, expected in (("GUI_QUEUE_TIMEOUT", "cancelled"), ("GUI_TIMEOUT", "preparing")):
            params = {"docName": "Example", "pageName": "Page", "format": "pdf", "requestId": "real-export-" + code}
            with self.subTest(code=code), patch.dict(sys.modules, {"FreeCAD": None, "FreeCADGui": None, "PySide": None}), \
                 patch.object(bridge, "_dispatch_gui", side_effect=bridge.PftBridgeError(code, "synthetic queue outcome")) as dispatch:
                first = bridge.process_request({"method": "prepare_drawing_export", "params": params})
                self.assertEqual(first["error"]["code"], code)
                replay = bridge.process_request({"method": "prepare_drawing_export", "params": params})
                token = replay["data"]["token"]
                status = bridge.process_request({"method": "get_drawing_export_status", "params": {"token": token}})
            self.assertEqual(replay, status)
            self.assertEqual(replay["data"]["state"], expected)
            self.assertEqual(replay["data"]["publication_state"], "not_published")
            dispatch.assert_called_once()

    def test_real_request_conflict_is_rejected_without_dispatch_or_native_imports(self):
        params = {"docName": "Example", "pageName": "Page", "format": "pdf", "requestId": "real-conflict"}
        with patch.object(bridge, "_dispatch_gui", side_effect=bridge.PftBridgeError("GUI_TIMEOUT", "unknown")) as dispatch:
            bridge.process_request({"method": "prepare_drawing_export", "params": params})
            conflict = bridge.process_request({"method": "prepare_drawing_export", "params": {**params, "pageName": "OtherPage"}})
        self.assertEqual(conflict["error"]["code"], "DRAWING_EXPORT_REQUEST_CONFLICT")
        self.assertEqual(len(bridge._drawing_export._requests), 1)
        dispatch.assert_called_once()




class InstanceAdmissionTests(unittest.TestCase):
    """Synthetic identity checks never read CAD, registries or configuration."""
    def setUp(self):
        self.identity = {"version": 1, "instance_id": "1"*32, "service_id": "2"*32,
                         "pid": 1234, "guard_required": True}
        self.dispatcher = object()
        for name, value in (("_service_identity", self.identity), ("_dispatcher", self.dispatcher),
                            ("_server", object()), ("_request_origins", {}), ("_token_origins", {})):
            context = patch.object(bridge, name, value); context.start(); self.addCleanup(context.stop)
        self.target = {key: self.identity[key] for key in ("instance_id", "service_id")}
        self.admission = (self.identity, self.dispatcher)

    def request(self, method, params=None, target=True, listener=None):
        payload = {"method": method, "params": params or {}}
        if target is not False: payload["target"] = self.target if target is True else target
        return bridge.process_request(payload, _listener=listener)

    def test_discovery_guards_every_fixed_operation_before_dispatch_or_claim(self):
        with patch.object(bridge, "_dispatch_gui") as dispatch, patch.object(bridge._runtime, "claim") as claim:
            for method, params in (("list_documents", {}), ("execute_script", {"requestId":"guard-script", "code":"result = 1"}),
                                   ("get_script_status", {"requestId":"guard-script"}),
                                   ("get_part_change_status", {"token":"a"*32})):
                reply = self.request(method, params, target=False)
                self.assertEqual(reply["error"]["code"], "INSTANCE_TARGET_REQUIRED")
            dispatch.assert_not_called(); claim.assert_not_called()

    def test_unguarded_initial_handshake_allowed_but_wrong_target_ping_rejected(self):
        self.assertTrue(self.request("ping", target=False)["ok"])
        for method in ("get_status", "ping"):
            self.assertEqual(self.request(method, target={**self.target,"service_id":"3"*32})["error"]["code"], "INSTANCE_TARGET_MISMATCH")

    def test_malformed_targets_rejected_before_any_work(self):
        for target in (None, {}, [], {**self.target,"pid":1234}, {**self.target,"service_id":"A"*32},
                       {**self.target,"instance_id":True}):
            with self.subTest(target=target):
                self.assertEqual(self.request("ping",target=target)["error"]["code"], "INVALID_TARGET")

    def test_status_is_detached_and_has_no_registry_or_cad_reads(self):
        with patch.object(bridge, "_server", types.SimpleNamespace(server_address=("127.0.0.1",12345))), \
             patch.object(bridge, "_server_thread", types.SimpleNamespace(is_alive=lambda:True)), \
             patch.object(bridge._instance_registry, "resolve_instance_directory", side_effect=AssertionError("registry access")):
            result = self.request("get_status")["data"]
            self.assertEqual(result["instance_routing"], self.identity)
            result["instance_routing"]["service_id"] = "4"*32
            self.assertEqual(bridge.get_status()["instance_routing"], self.identity)

    def test_frozen_dispatcher_and_service_rechecked_before_gui_dispatch(self):
        with patch.object(bridge, "_dispatch_gui", return_value=[]) as dispatch:
            self.assertTrue(self.request("list_documents")["ok"])
            dispatch.assert_called_once_with("list_documents", {}, self.admission)
        for name, replacement in (("_service_identity", dict(self.identity)), ("_dispatcher", object()), ("_server", None)):
            with self.subTest(name=name), patch.object(bridge,name,replacement), patch.object(bridge, "_dispatch_gui") as dispatch:
                result = self.request("list_documents", listener=self.admission)
                self.assertEqual(result["error"]["code"], "INSTANCE_TARGET_MISMATCH")
                dispatch.assert_not_called()

    def test_script_origin_cannot_cross_service_even_new_guard_matches(self):
        params={"requestId":"routed-script", "code":"result = 1"}
        with patch.object(bridge._runtime,"claim", return_value=(False,{"state":"completed"})) as claim:
            self.assertTrue(self.request("execute_script",params)["ok"])
            bridge._service_identity = {**self.identity,"service_id":"3"*32}
            self.target["service_id"] = "3"*32
            for method in ("execute_script", "get_script_status"):
                result = self.request(method,params if method == "execute_script" else {"requestId":params["requestId"]})
                self.assertEqual(result["error"]["code"], "INSTANCE_AFFINITY_MISMATCH")
            self.assertEqual(claim.call_count,1)

    def test_unknown_guarded_token_rejected_before_business_query(self):
        with patch.object(bridge._business,"perform") as perform, patch.object(bridge,"_dispatch_gui") as dispatch:
            for method in ("get_part_change_status","request_part_change_approval","apply_part_changes"):
                self.assertEqual(self.request(method,{"token":"a"*32})["error"]["code"],"INSTANCE_AFFINITY_UNKNOWN")
            perform.assert_not_called(); dispatch.assert_not_called()

    def test_preview_token_stays_with_producer_and_cannot_move_after_restart(self):
        params={"docName":"Example", "objectNames":["Source"], "operation":"create_drawing"}
        with patch.object(bridge,"_dispatch_gui",return_value={"token":"b"*32}):
            self.assertTrue(self.request("prepare_part_changes",params)["ok"])
        with patch.object(bridge._business,"perform",return_value={"state":"prepared"}) as perform:
            self.assertTrue(self.request("get_part_change_status",{"token":"b"*32})["ok"])
            bridge._service_identity = {**self.identity,"service_id":"3"*32}; self.target["service_id"]="3"*32
            self.assertEqual(self.request("get_part_change_status",{"token":"b"*32})["error"]["code"],"INSTANCE_AFFINITY_MISMATCH")
            self.assertEqual(perform.call_count,1)

    def test_target_change_before_gui_marks_script_cancelled_not_unknown_running(self):
        params={"requestId":"stale-before-gui", "code":"result = 1"}
        with patch.object(bridge,"_dispatch_gui",side_effect=bridge.PftBridgeError("INSTANCE_TARGET_MISMATCH","changed")):
            reply=self.request("execute_script",params)
        self.assertEqual(reply["error"]["code"],"INSTANCE_TARGET_MISMATCH")
        self.assertEqual(bridge._runtime.status(params["requestId"],bridge.PftBridgeError)["state"],"cancelled")

    def test_affinity_bound_is_retained_on_unknown_dispatch_and_no_eviction(self):
        params={"requestId":"unknown-dispatch", "code":"result = 1"}
        with patch.object(bridge._runtime,"claim",return_value=(True,{})), \
             patch.object(bridge._runtime,"dispatch_error"), \
             patch.object(bridge,"_dispatch_gui",side_effect=bridge.PftBridgeError("GUI_TIMEOUT","unknown")):
            self.assertEqual(self.request("execute_script",params)["error"]["code"],"GUI_TIMEOUT")
        self.assertEqual(bridge._request_origins[params["requestId"]],("1"*32,"2"*32))
        with patch.object(bridge,"_MAX_ORIGINS",1), patch.object(bridge._runtime,"claim") as claim:
            self.assertEqual(self.request("execute_script",{**params,"requestId":"another"})["error"]["code"],"INSTANCE_AFFINITY_LIMIT")
            claim.assert_not_called()
            self.assertIn(params["requestId"],bridge._request_origins)

    def test_legacy_no_target_still_dispatches_on_fixed_listener(self):
        bridge._service_identity = {**self.identity,"guard_required":False}
        with patch.object(bridge,"_dispatch_gui",return_value=[]) as dispatch:
            self.assertTrue(self.request("list_documents",target=False)["ok"])
            self.assertEqual(dispatch.call_args.args[2],(bridge._service_identity,self.dispatcher))

    def test_export_guard_and_unknown_token_reject_before_claim_or_query(self):
        params = {"docName": "Example", "pageName": "Page", "format": "pdf", "requestId": "routed-export"}
        with patch.object(bridge._drawing_export, "claim_prepare") as claim, \
             patch.object(bridge._drawing_export, "perform") as perform, patch.object(bridge, "_dispatch_gui") as dispatch:
            self.assertEqual(self.request("prepare_drawing_export", params, target=False)["error"]["code"], "INSTANCE_TARGET_REQUIRED")
            for method in ("request_drawing_export_approval", "get_drawing_export_status"):
                self.assertEqual(self.request(method, {"token": "e" * 32})["error"]["code"], "INSTANCE_AFFINITY_UNKNOWN")
        claim.assert_not_called(); perform.assert_not_called(); dispatch.assert_not_called()

    def test_export_preparing_token_and_request_are_bound_before_gui_and_survive_timeout(self):
        params = {"docName": "Example", "pageName": "Page", "format": "pdf", "requestId": "routed-export"}
        snapshot = {"token": "e" * 32, "state": "preparing"}
        def dispatch(method, actual, admission):
            self.assertEqual((method, actual, admission), ("prepare_drawing_export", params, self.admission))
            self.assertEqual(bridge._request_origins[params["requestId"]], ("1" * 32, "2" * 32))
            self.assertEqual(bridge._token_origins[snapshot["token"]], ("1" * 32, "2" * 32))
            raise bridge.PftBridgeError("GUI_TIMEOUT", "operation may still run")
        with patch.object(bridge._drawing_export, "claim_prepare", side_effect=[(True, snapshot), (False, snapshot)]), \
             patch.object(bridge._drawing_export, "dispatch_error") as record_error, \
             patch.object(bridge, "_dispatch_gui", side_effect=dispatch) as native:
            self.assertEqual(self.request("prepare_drawing_export", params)["error"]["code"], "GUI_TIMEOUT")
            self.assertEqual(self.request("prepare_drawing_export", params), {"ok": True, "data": snapshot})
        native.assert_called_once(); self.assertEqual(record_error.call_count, 1)

    def test_export_request_and_token_never_migrate_to_restarted_service(self):
        params = {"docName": "Example", "pageName": "Page", "format": "pdf", "requestId": "origin-export"}
        snapshot = {"token": "e" * 32, "state": "preparing"}
        with patch.object(bridge._drawing_export, "claim_prepare", return_value=(False, snapshot)) as claim, \
             patch.object(bridge._drawing_export, "perform") as perform, patch.object(bridge, "_dispatch_gui") as dispatch:
            self.assertTrue(self.request("prepare_drawing_export", params)["ok"])
            bridge._service_identity = {**self.identity, "service_id": "3" * 32}; self.target["service_id"] = "3" * 32
            self.assertEqual(self.request("prepare_drawing_export", params)["error"]["code"], "INSTANCE_AFFINITY_MISMATCH")
            for method in ("request_drawing_export_approval", "get_drawing_export_status"):
                self.assertEqual(self.request(method, {"token": snapshot["token"]})["error"]["code"], "INSTANCE_AFFINITY_MISMATCH")
        claim.assert_called_once(); perform.assert_not_called(); dispatch.assert_not_called()

    def test_full_token_origin_table_still_allows_known_prepare_replay(self):
        params = {"docName": "Example", "pageName": "Page", "format": "pdf", "requestId": "known-export"}
        snapshot = {"token": "e" * 32, "state": "prepared"}
        bridge._token_origins[snapshot["token"]] = ("1" * 32, "2" * 32)
        with patch.object(bridge, "_MAX_ORIGINS", 1), \
             patch.object(bridge._drawing_export, "claim_prepare", return_value=(False, snapshot)), \
             patch.object(bridge, "_dispatch_gui") as dispatch:
            self.assertEqual(self.request("prepare_drawing_export", params), {"ok": True, "data": snapshot})
        dispatch.assert_not_called()

    def test_failed_preparing_token_reservation_never_dispatches_or_drops_dedup(self):
        params = {"docName": "Example", "pageName": "Page", "format": "pdf", "requestId": "capacity-export"}
        snapshot = {"token": "e" * 32, "state": "preparing"}
        bridge._token_origins["f" * 32] = ("1" * 32, "2" * 32)
        with patch.object(bridge, "_MAX_ORIGINS", 1), \
             patch.object(bridge._drawing_export, "claim_prepare", return_value=(True, snapshot)), \
             patch.object(bridge._drawing_export, "dispatch_error") as record_error, \
             patch.object(bridge, "_dispatch_gui") as dispatch:
            self.assertEqual(self.request("prepare_drawing_export", params)["error"]["code"], "INSTANCE_AFFINITY_LIMIT")
        self.assertEqual(record_error.call_args.args[0], params["requestId"])
        self.assertEqual(record_error.call_args.args[1].code, "INSTANCE_AFFINITY_LIMIT")
        self.assertEqual(bridge._request_origins[params["requestId"]], ("1" * 32, "2" * 32))
        self.assertNotIn(snapshot["token"], bridge._token_origins)
        dispatch.assert_not_called()


class SharedConfigurationIntegrationTests(unittest.TestCase):
    """Every startup dependency is synthetic; no listener/thread/config file."""
    def setUp(self):
        self.events = []
        self.servers, self.dispatchers, self.threads = [], [], []
        self.record = {"source": "user_config", "settings": {"schema_version": 1,
                       "bridge": {"preferred_port": 12345}},
                       "config_path": "/synthetic/PhoenixTools/mcp/config.json", "etag": "a"*64}
        owner = self
        class Dispatcher:
            def __init__(self):
                self.approval_epoch = None
                self.export_approval_epoch = None
                owner.dispatchers.append(self)
                owner.events.append("dispatcher")
            def close(self): owner.events.append("dispatcher-close")
        class Server:
            def __init__(self, address, handler):
                self.server_address = address[0], address[1] or 12349
                owner.servers.append(self)
                owner.events.append(("listen", address))
            def serve_forever(self, **kwargs): raise AssertionError("No real server")
            def server_close(self): owner.events.append("server-close")
        class Thread:
            def __init__(self, **kwargs):
                self.alive = False
                owner.threads.append(self)
            def start(self): self.alive = True; owner.events.append("thread-start")
            def is_alive(self): return self.alive
        self.patches = [
            patch.dict(bridge.os.environ, {}, clear=True),
            patch.object(bridge, "_server", None),
            patch.object(bridge, "_server_thread", None),
            patch.object(bridge, "_dispatcher", None),
            patch.object(bridge, "_stop_thread", None),
            patch.object(bridge, "_service_identity", None),
            patch.object(bridge, "_registration", None),
            patch.object(bridge, "_registration_error", None),
            patch.object(bridge, "_listener_configuration", {"source": "default", "config_path": None,
                                                           "preferred_port": 9876, "effective_port": 9876}),
            patch.object(bridge, "_create_gui_dispatcher", Dispatcher),
            patch.object(bridge, "_PftBridgeHttpServer", Server),
            patch.object(bridge.threading, "Thread", Thread),
            patch.object(bridge._business, "cancel_pending_approvals",
                         side_effect=lambda: self.events.append("cancel-approval")),
            patch.object(bridge._business, "begin_approval_lifecycle", return_value=42),
            patch.object(bridge._drawing_export, "cancel_pending_approvals",
                         side_effect=lambda: self.events.append("cancel-export-approval")),
            patch.object(bridge._drawing_export, "begin_approval_lifecycle",
                         side_effect=lambda: self.events.append("begin-export-approval") or 73),
        ]
        for context in self.patches:
            context.start()
            self.addCleanup(context.stop)
        self.read_patch = patch.object(bridge._mcp_settings, "read_config",
                                       side_effect=AssertionError("Unit test read real configuration"))
        self.read = self.read_patch.start()
        self.addCleanup(self.read_patch.stop)

    def test_export_lifecycle_opens_before_serving_and_stops_independently(self):
        bridge.start_server(12340)
        self.assertEqual(self.dispatchers[-1].approval_epoch, 42)
        self.assertEqual(self.dispatchers[-1].export_approval_epoch, 73)
        self.assertLess(self.events.index("cancel-export-approval"), self.events.index("dispatcher"))
        self.assertLess(self.events.index("begin-export-approval"), self.events.index("thread-start"))
        bridge.stop_server()
        self.assertEqual(self.events.count("cancel-export-approval"), 2)
        self.assertEqual(self.events.count("cancel-approval"), 2)

    def test_export_lifecycle_start_failure_closes_both_grants_and_owned_listener(self):
        with patch.object(bridge._drawing_export, "begin_approval_lifecycle", side_effect=RuntimeError("synthetic initialization failure")):
            with self.assertRaises(bridge.PftBridgeError) as raised:
                bridge.start_server(12340)
        self.assertEqual(raised.exception.code, "START_FAILED")
        self.assertEqual(self.events.count("cancel-export-approval"), 2)
        self.assertEqual(self.events.count("cancel-approval"), 2)
        self.assertIn("server-close", self.events); self.assertIn("dispatcher-close", self.events)
        self.assertNotIn("thread-start", self.events)
        self.assertIsNone(bridge._service_identity)

    def test_discovery_uses_dynamic_port_publishes_only_after_bind_and_cleans_exact_handle(self):
        self.record["settings"]={"schema_version":2,"bridge":{"preferred_port":12345},"discovery":{"enabled":True}}
        self.read.side_effect=None; self.read.return_value=self.record
        registration=types.SimpleNamespace(remove=lambda:self.events.append("registration-remove"))
        def publish(record, **kwargs):
            self.assertEqual(record["url"],"http://127.0.0.1:12349/")
            self.assertEqual(record["instance_id"],bridge._INSTANCE_ID)
            self.assertIn(("listen",("127.0.0.1",0)),self.events)
            self.assertNotIn("thread-start",self.events)
            self.events.append("publish")
            return registration
        with patch.object(bridge._instance_registry,"resolve_instance_directory",return_value=Path("/synthetic/instances")), \
             patch.object(bridge._instance_registry,"publish_instance",side_effect=publish) as writer:
            first=bridge.start_server()
        self.assertTrue(first["instance_routing"]["guard_required"])
        self.assertEqual(first["listener_configuration"]["preferred_port"],12345)
        self.assertEqual(writer.call_count,1)
        before=first["instance_routing"]
        bridge.stop_server()
        self.assertIsNone(bridge.get_status()["instance_routing"])
        self.assertIn("registration-remove",self.events)
        self.assertIsNone(bridge._registration)
        self.assertEqual(before["instance_id"],bridge._INSTANCE_ID)

    def test_uncertain_registration_cleanup_is_reported_and_never_retried_by_stop(self):
        remove=unittest.mock.Mock(side_effect=bridge._instance_registry.PftInstanceRegistryError("REMOVE_UNVERIFIED",uncertain=True))
        bridge._registration=types.SimpleNamespace(remove=remove)
        first=bridge.stop_server()
        self.assertEqual(first["instance_registration_error"],{"code":"INSTANCE_REGISTRY_REMOVE_UNVERIFIED","uncertain":True})
        self.assertFalse(first["running"])
        self.assertEqual(bridge.stop_server()["instance_registration_error"],first["instance_registration_error"])
        self.assertEqual(remove.call_count,1)
        with self.assertRaises(bridge.PftBridgeError) as raised:bridge.start_server(12340)
        self.assertEqual(raised.exception.code,"INSTANCE_REGISTRY_CLEANUP_REQUIRED")

    def test_registry_publish_failure_closes_only_own_unstarted_listener(self):
        with patch.object(bridge._instance_registry,"publish_instance",side_effect=bridge._instance_registry.PftInstanceRegistryError("INSTANCE_REGISTRY_INVALID_RECORD")):
            with self.assertRaises(bridge.PftBridgeError) as raised:
                bridge.start_server(0,discovery_registration_directory=Path("/synthetic/instances"))
        self.assertEqual(raised.exception.code,"INSTANCE_REGISTRY_INVALID_RECORD")
        self.assertIn("server-close",self.events); self.assertIn("dispatcher-close",self.events)
        self.assertNotIn("thread-start",self.events)
        self.assertFalse(bridge.get_status()["running"])
        self.assertIsNone(bridge.get_status()["instance_routing"])
        self.read.assert_not_called()

    def test_old_http_handler_never_adopts_a_new_service_or_unregistered_dispatcher(self):
        bridge.start_server(12340)
        handler=self.servers[-1].pft_process_payload
        first=dict(bridge._service_identity)
        self.assertTrue(handler({"method":"ping"})["ok"])
        bridge._server=bridge._server_thread=bridge._dispatcher=None
        bridge.start_server(12340)
        self.assertEqual(first["instance_id"],bridge._service_identity["instance_id"])
        self.assertNotEqual(first["service_id"],bridge._service_identity["service_id"])
        self.assertEqual(handler({"method":"ping"})["error"]["code"],"INSTANCE_TARGET_MISMATCH")

    def test_import_and_status_are_disk_free(self):
        fresh_spec = importlib.util.spec_from_file_location("pft_bridge_disk_free_test", BRIDGE_PATH)
        fresh = importlib.util.module_from_spec(fresh_spec)
        with patch.object(bridge.os, "open", side_effect=AssertionError("Configuration disk I/O")), \
             patch.object(Path, "home", side_effect=AssertionError("User home resolution")):
            fresh_spec.loader.exec_module(fresh)
            status = fresh.get_status()
            reply = fresh.process_request({"method": "get_status", "params": {}})
        self.assertFalse(status["running"])
        self.assertTrue(reply["ok"])
        self.assertEqual(status["listener_configuration"]["source"], "default")
        self.read.assert_not_called()
        self.assertEqual(self.events, [])

    def test_explicit_port_and_development_configuration_skip_shared_file(self):
        for explicit, environ, expected, source in (
            (12340, {}, 12340, "explicit"),
            (0, {}, 12349, "explicit"),
            (None, {"PFT_DEVELOPMENT": "1"}, 9877, "development"),
            (None, {"PFT_DEVELOPMENT": "1", "PFT_DEVELOPMENT_PORT": "12341"}, 12341, "development"),
            (12342, {"PFT_DEVELOPMENT": "1", "PFT_DEVELOPMENT_PORT": "invalid"}, 12342, "explicit"),
        ):
            with self.subTest(explicit=explicit, source=source), patch.dict(bridge.os.environ, environ, clear=True):
                bridge._server = bridge._server_thread = bridge._dispatcher = None
                status = bridge.start_server(explicit)
                self.assertTrue(status["running"])
                self.assertEqual(status["port"], expected)
                self.assertEqual(status["listener_configuration"]["source"], source)
        self.read.assert_not_called()

    def test_regular_start_reads_selected_record_before_lifecycle_changes(self):
        for source, port in (("default", 9876), ("user_config", 12345)):
            with self.subTest(source=source):
                bridge._server = bridge._server_thread = bridge._dispatcher = None
                self.events.clear()
                record = {**self.record, "source": source,
                          "settings": {"schema_version": 1, "bridge": {"preferred_port": port}}}
                def observe_read():
                    self.assertEqual(self.events, [])
                    return record
                self.read.side_effect = observe_read
                status = bridge.start_server()
                self.assertEqual(status["port"], port)
                self.assertEqual(status["listener_configuration"], {"source": source,
                    "config_path": record["config_path"], "preferred_port": port, "effective_port": port})
                self.assertEqual(self.events[0], "cancel-approval")
                self.assertEqual(self.dispatchers[-1].approval_epoch, 42)
        self.assertEqual(self.read.call_count, 2)

    def test_invalid_shared_configuration_does_not_cancel_approval_or_build_dispatcher(self):
        self.read.side_effect = bridge._mcp_settings.PftMcpSettingsError("MCP_SETTINGS_INVALID")
        before = bridge.get_status()
        with self.assertRaises(bridge.PftBridgeError) as raised:
            bridge.start_server()
        self.assertEqual(raised.exception.code, "MCP_SETTINGS_INVALID")
        self.assertEqual(bridge.get_status(), before)
        self.assertEqual(self.events, [])
        self.assertEqual(self.servers, [])
        self.assertEqual(self.dispatchers, [])
        self.assertEqual(self.threads, [])

    def test_existing_running_server_does_not_re_read_or_switch_ports(self):
        self.read.side_effect = None
        self.read.return_value = self.record
        first = bridge.start_server()
        self.assertEqual(self.read.call_count, 1)
        events = list(self.events)
        self.read.side_effect = AssertionError("Running service re-read configuration")
        for requested in (None, 12347):
            again = bridge.start_server(requested)
            self.assertEqual(again, first)
        self.assertEqual(self.events, events)
        self.assertEqual(self.read.call_count, 1)
        self.assertEqual(len(self.servers), 1)

    def test_stopping_or_invalid_explicit_start_never_reads_configuration(self):
        with patch.object(bridge, "_stop_thread", types.SimpleNamespace(is_alive=lambda: True)):
            with self.assertRaises(bridge.PftBridgeError) as raised:
                bridge.start_server()
        self.assertEqual(raised.exception.code, "SERVER_STOPPING")
        for port in (True, False, -1, 65536, 9876.0, "9876"):
            with self.subTest(port=port), self.assertRaises(bridge.PftBridgeError) as raised:
                bridge.start_server(port)
            self.assertEqual(raised.exception.code, "INVALID_PORT")
        self.read.assert_not_called()
        self.assertEqual(self.events, [])

    def test_failed_config_source_remains_read_only_in_stopped_status(self):
        self.read.side_effect = bridge._mcp_settings.PftMcpSettingsError("MCP_SETTINGS_UNSAFE")
        with self.assertRaises(bridge.PftBridgeError):
            bridge.start_server()
        self.read.side_effect = AssertionError("Status must not repeat disk access")
        detached = bridge.get_status()
        detached["listener_configuration"]["effective_port"] = 12348
        self.assertEqual(bridge.get_status()["port"], 9876)
        self.assertEqual(self.read.call_count, 1)



class WorkbenchLoaderTests(unittest.TestCase):
    def test_standard_and_explicit_file_loader_register_without_starting_server(self):
        init_gui = BRIDGE_PATH.with_name("InitGui.py")
        registered = []
        commands, manipulators = {}, []
        preferences = types.SimpleNamespace(GetString=lambda key, default="": default)
        app = types.SimpleNamespace(ParamGet=lambda path: preferences)
        gui = types.SimpleNamespace(
            addWorkbench=registered.append,
            addCommand=lambda identifier, command: commands.setdefault(identifier, command),
            Command=types.SimpleNamespace(get=lambda identifier: commands.get(identifier)),
            addWorkbenchManipulator=manipulators.append,
            removeWorkbenchManipulator=manipulators.remove)
        modules = {"FreeCAD": app, "FreeCADGui": gui, "pft_bridge": bridge}
        initial_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), initial_path))
        with patch.dict(sys.modules, modules), patch.object(bridge, "start_server") as start:
            for include_file in [True, False]:
                namespace = {"__name__": "PhoenixFreeCADTools_LoaderTest", "Workbench": type("Workbench", (), {})}
                if include_file:
                    namespace["__file__"] = str(init_gui)
                exec(compile(init_gui.read_text(), str(init_gui), "exec"), namespace)
                self.assertEqual(sys.modules["pft_workbench_gui"]._addon_dir, str(BRIDGE_PATH.parent))
            start.assert_not_called()
        self.assertEqual(len(registered), 1)
        self.assertEqual(len(manipulators), 1)
        self.assertEqual(registered[0].MenuText, "Phoenix Tools")


class GuiOperationTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.document = types.SimpleNamespace(Name="Doc")
        self.other = types.SimpleNamespace(Name="Other")
        self.view = types.SimpleNamespace(
            fitAll=lambda: self.calls.append("fit"),
            viewAxonometric=lambda: self.calls.append("axo"),
        )
        self.app = types.SimpleNamespace(
            ActiveDocument=self.document,
            listDocuments=lambda: {"Doc": self.document, "Other": self.other},
            getDocument=lambda name: {"Doc": self.document, "Other": self.other}[name],
        )
        self.gui = types.SimpleNamespace(getDocument=lambda name: types.SimpleNamespace(activeView=lambda: self.view))
        self.modules = patch.dict(sys.modules, {"FreeCAD": self.app, "FreeCADGui": self.gui})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_design_route_delegates_to_own_module_with_injected_app_gui(self):
        with patch.object(bridge._design, "perform", return_value={"public": True}) as perform:
            params = {"objectName": "Sketch"}
            self.assertEqual(bridge._perform_gui("inspect_design", params), {"public": True})
            perform.assert_called_once_with("inspect_design", params, self.app, self.gui, bridge.PftBridgeError)
        self.assertEqual(self.calls, [])

    def test_list_documents_does_not_change_view(self):
        self.assertEqual(bridge._perform_gui("list_documents", {}), ["Doc", "Other"])
        self.assertEqual(self.calls, [])

    def test_fit_and_axonometric_default_does_not_fit(self):
        self.assertEqual(bridge._perform_gui("view_fit", {})["document"], "Doc")
        result = bridge._perform_gui("view_axonometric", {})
        self.assertFalse(result["fit"])
        self.assertEqual(self.calls, ["fit", "axo"])

    def test_legacy_camera_methods_resolve_existing_3d_view_behind_drawing(self):
        self.gui.getDocument = lambda name: types.SimpleNamespace(activeView=lambda: object(), mdiViewsOfType=lambda kind: [self.view])
        bridge._perform_gui("view_axonometric", {"fit": True})
        self.assertEqual(self.calls, ["axo", "fit"])

    def test_explicit_document_and_optional_fit(self):
        result = bridge._perform_gui("view_axonometric", {"docName": "Other", "fit": True})
        self.assertEqual(result["document"], "Other")
        self.assertEqual(self.calls, ["axo", "fit"])
        self.assertIs(self.app.ActiveDocument, self.document)

    def test_no_active_and_missing_document(self):
        self.app.ActiveDocument = None
        with self.assertRaises(bridge.PftBridgeError) as missing_active:
            bridge._perform_gui("view_fit", {})
        self.assertEqual(missing_active.exception.code, "NO_ACTIVE_DOCUMENT")
        with self.assertRaises(bridge.PftBridgeError) as missing_document:
            bridge._perform_gui("view_fit", {"docName": "Missing"})
        self.assertEqual(missing_document.exception.code, "DOCUMENT_NOT_FOUND")

    def test_unsupported_view_has_no_camera_effect(self):
        self.gui.getDocument = lambda name: types.SimpleNamespace(activeView=lambda: object())
        with self.assertRaises(bridge.PftBridgeError) as failure:
            bridge._perform_gui("view_axonometric", {})
        self.assertEqual(failure.exception.code, "VIEW_UNAVAILABLE")
        self.assertEqual(self.calls, [])


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = bridge._PftBridgeHttpServer(("127.0.0.1", 0), bridge._PftRequestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, body=b'{"method":"ping","params":{}}', headers=None, method="POST", path="/rpc"):
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=2)
        self.addCleanup(connection.close)
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    def test_ping_over_http(self):
        status, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "data": {"pong": True}})

    def test_rejects_browser_origin_and_fetch_headers(self):
        for headers in [{"Origin": "https://example.com"}, {"Origin": ""}, {"Sec-Fetch-Site": "cross-site"}]:
            status, body = self.request(headers=headers)
            self.assertEqual(status, 403)
            self.assertEqual(body["error"]["code"], "BROWSER_REQUEST_REJECTED")

    def test_rejects_dns_rebinding_host_and_wrong_content_type(self):
        self.assertEqual(self.request(headers={"Host": "untrusted.example:9876"})[0], 403)
        self.assertEqual(self.request(headers={"Content-Type": "text/plain"})[0], 415)

    def test_payload_limit_invalid_json_and_fixed_path(self):
        self.assertEqual(self.request(b"x" * (bridge.MAX_PAYLOAD_BYTES + 1))[0], 413)
        self.assertEqual(self.request(b"not json")[0], 400)
        self.assertEqual(self.request(path="/other")[0], 404)
        self.assertEqual(self.request(method="OPTIONS")[0], 403)


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.main_thread = threading.get_ident()
        self.events = queue.Queue()
        events = self.events

        class Signal:
            def __init__(self, *args):
                self.callbacks = {}

            def __get__(self, instance, owner):
                if instance is None:
                    return self
                if instance not in self.callbacks:
                    self.callbacks[instance] = types.SimpleNamespace(
                        connect=lambda callback, mode: setattr(instance, "_test_callback", callback),
                        emit=lambda job: events.put((instance._test_callback, job)),
                    )
                return self.callbacks[instance]

        class QObject:
            def __init__(self, parent=None):
                pass

            def deleteLater(self):
                pass

        main_thread = self.main_thread
        app = types.SimpleNamespace(thread=lambda: main_thread)
        core = types.SimpleNamespace(
            QObject=QObject, Signal=Signal, Slot=lambda *args: lambda function: function,
            Qt=types.SimpleNamespace(QueuedConnection=1),
            QThread=types.SimpleNamespace(currentThread=threading.get_ident),
        )
        widgets = types.SimpleNamespace(QApplication=types.SimpleNamespace(instance=lambda: app))
        self.modules = patch.dict(sys.modules, {"PySide": types.SimpleNamespace(QtCore=core, QtWidgets=widgets)})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.dispatcher = bridge._create_gui_dispatcher()
        self.addCleanup(self.dispatcher.close)

    def worker(self, timeout):
        outcome = {}

        def run():
            try:
                outcome["data"] = self.dispatcher.dispatch("view_fit", {}, timeout)
            except bridge.PftBridgeError as error:
                outcome["error"] = error.code

        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(thread.join, 2)
        return thread, outcome

    def pump_one(self):
        callback, job = self.events.get(timeout=1)
        callback(job)

    def test_approval_dispatch_preserves_own_lifecycle_epoch_for_both_routes(self):
        self.dispatcher.approval_epoch = 41
        calls = []
        def perform(method, params, **kwargs):
            calls.append((method, params, kwargs))
            return {"state": "awaiting_confirmation"}
        with patch.object(bridge, "_perform_gui", side_effect=perform):
            direct = self.dispatcher.dispatch("request_part_change_approval", {"token": "synthetic-token"}, 1)
            job = bridge._PftGuiJob("request_part_change_approval", {"token": "synthetic-token"})
            self.dispatcher.run(job)
        self.assertEqual(direct, {"state": "awaiting_confirmation"})
        self.assertEqual(job.result, direct)
        self.assertEqual(calls, [("request_part_change_approval", {"token": "synthetic-token"}, {"_approval_epoch": 41})] * 2)

    def test_export_dispatch_keeps_distinct_epoch_for_direct_and_queued_prepare_and_request(self):
        self.dispatcher.approval_epoch = 41
        self.dispatcher.export_approval_epoch = 73
        for method, params in (("prepare_drawing_export", {"requestId": "synthetic-export"}),
                               ("request_drawing_export_approval", {"token": "synthetic-token"})):
            calls = []
            def perform(actual, values, **kwargs):
                calls.append((actual, values, kwargs))
                return {"state": "preparing" if actual == "prepare_drawing_export" else "awaiting_confirmation"}
            with self.subTest(method=method), patch.object(bridge, "_perform_gui", side_effect=perform):
                direct = self.dispatcher.dispatch(method, params, 1)
                job = bridge._PftGuiJob(method, params)
                self.dispatcher.run(job)
            self.assertEqual(job.result, direct)
            self.assertEqual(calls, [(method, params, {"_approval_epoch": 73})] * 2)
        self.assertEqual(self.dispatcher.approval_epoch, 41)

    def test_guarded_queued_job_is_rechecked_before_touching_gui(self):
        identity={"version":1,"instance_id":"1"*32,"service_id":"2"*32,"pid":1234,"guard_required":True}
        admission=(identity,self.dispatcher)
        job=bridge._PftGuiJob("view_fit",{},admission)
        with patch.object(bridge,"_service_identity",{**identity,"service_id":"3"*32}), \
             patch.object(bridge,"_dispatcher",self.dispatcher), patch.object(bridge,"_server",object()), \
             patch.object(bridge,"_perform_gui") as perform:
            self.dispatcher.run(job)
        perform.assert_not_called()
        self.assertEqual(job.error.code,"INSTANCE_TARGET_MISMATCH")
        self.assertTrue(job.event.is_set())

    def test_worker_executes_on_gui_thread(self):
        with patch.object(bridge, "_perform_gui", side_effect=lambda method, params: threading.get_ident()):
            thread, outcome = self.worker(1)
            self.pump_one()
            thread.join(timeout=1)
        self.assertEqual(outcome, {"data": self.main_thread})

    def test_queued_timeout_cancels_before_any_gui_effect(self):
        with patch.object(bridge, "_perform_gui") as operation:
            thread, outcome = self.worker(0.01)
            thread.join(timeout=1)
            self.pump_one()
            operation.assert_not_called()
        self.assertEqual(outcome, {"error": "GUI_QUEUE_TIMEOUT"})

    def test_started_timeout_reports_uncancelled_operation(self):
        states = []

        def operation(method, params):
            time.sleep(0.06)
            states.append(bridge._health_snapshot()["state"])
            return "finished"

        with patch.object(bridge, "_perform_gui", side_effect=operation):
            thread, outcome = self.worker(0.02)
            self.pump_one()
            thread.join(timeout=1)
        self.assertEqual(outcome, {"error": "GUI_TIMEOUT"})
        self.assertEqual(states, ["timed_out"])
        self.assertEqual(bridge._health_snapshot()["state"], "idle")

    def test_reentrant_delivery_waits_until_current_callback_finishes(self):
        calls = []
        nested = bridge._PftGuiJob("view_axonometric", {})

        def operation(method, params):
            calls.append(method + ":start")
            if method == "view_fit":
                self.dispatcher.run(nested)
            calls.append(method + ":end")

        with patch.object(bridge, "_perform_gui", side_effect=operation):
            self.dispatcher.run(bridge._PftGuiJob("view_fit", {}))
        self.assertEqual(calls, ["view_fit:start", "view_fit:end", "view_axonometric:start", "view_axonometric:end"])


if __name__ == "__main__":
    unittest.main()
