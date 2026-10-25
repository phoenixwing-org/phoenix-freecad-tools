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

BRIDGE_PATH = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADMCP" / "phoenix_freecad_bridge.py"
spec = importlib.util.spec_from_file_location("phoenix_freecad_bridge_test", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class ProtocolTests(unittest.TestCase):
    def test_ping_and_status_without_freecad_or_qt(self):
        self.assertEqual(bridge.process_request({"method": "ping"}), {"ok": True, "data": {"pong": True}})
        status = bridge.process_request({"method": "get_status", "params": {}})
        self.assertTrue(status["ok"])
        self.assertEqual(status["data"]["protocol_version"], 1)
        self.assertEqual(status["data"]["addon_version"], "0.2.1")
        self.assertFalse(status["data"]["running"])

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
        with patch.object(bridge,"_dispatch_gui",side_effect=lambda method,args:bridge._runtime.execute(args,app,types.SimpleNamespace(),bridge.BridgeError)) as dispatch:
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
            with patch.object(bridge,"_dispatch_gui",side_effect=bridge.BridgeError(code,"timeout")):
                result = bridge.process_request({"method":"execute_script","params":{"requestId":request,"code":"pass"}})
            self.assertFalse(result["ok"])
            queried = bridge.process_request({"method":"get_script_status","params":{"requestId":request}})
            self.assertEqual(queried["data"]["state"],expected)

    def test_business_error_retains_machine_code(self):
        with patch.object(bridge, "_dispatch_gui", side_effect=bridge.BridgeError("GUI_QUEUE_TIMEOUT", "timeout")):
            self.assertEqual(bridge.process_request({"method": "view_fit"}), {"ok": False, "error": {"code": "GUI_QUEUE_TIMEOUT", "message": "timeout"}})

    def test_json_rejects_duplicate_and_nonfinite_values(self):
        for body in [b'{"method":"ping","method":"view_fit"}', b'{"x":NaN}', b'{"x":Infinity}', b'\xff']:
            with self.assertRaises((ValueError, UnicodeError)):
                bridge._decode_json(body)


class WorkbenchLoaderTests(unittest.TestCase):
    def test_standard_and_explicit_file_loader_register_without_starting_server(self):
        init_gui = BRIDGE_PATH.with_name("InitGui.py")
        registered = []
        app = types.SimpleNamespace()
        gui = types.SimpleNamespace(addWorkbench=registered.append)
        modules = {"FreeCAD": app, "FreeCADGui": gui, "phoenix_freecad_bridge": bridge}
        initial_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), initial_path))
        with patch.dict(sys.modules, modules), patch.object(bridge, "start_server") as start:
            for include_file in [True, False]:
                namespace = {"__name__": "PhoenixFreeCADMCP_LoaderTest", "Workbench": type("Workbench", (), {})}
                if include_file:
                    namespace["__file__"] = str(init_gui)
                exec(compile(init_gui.read_text(), str(init_gui), "exec"), namespace)
                self.assertEqual(sys.modules["phoenix_workbench_gui"]._addon_dir, str(BRIDGE_PATH.parent))
            start.assert_not_called()
        self.assertEqual(len(registered), 2)
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
        with self.assertRaises(bridge.BridgeError) as missing_active:
            bridge._perform_gui("view_fit", {})
        self.assertEqual(missing_active.exception.code, "NO_ACTIVE_DOCUMENT")
        with self.assertRaises(bridge.BridgeError) as missing_document:
            bridge._perform_gui("view_fit", {"docName": "Missing"})
        self.assertEqual(missing_document.exception.code, "DOCUMENT_NOT_FOUND")

    def test_unsupported_view_has_no_camera_effect(self):
        self.gui.getDocument = lambda name: types.SimpleNamespace(activeView=lambda: object())
        with self.assertRaises(bridge.BridgeError) as failure:
            bridge._perform_gui("view_axonometric", {})
        self.assertEqual(failure.exception.code, "VIEW_UNAVAILABLE")
        self.assertEqual(self.calls, [])


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = bridge._BridgeHttpServer(("127.0.0.1", 0), bridge._RequestHandler)
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
            except bridge.BridgeError as error:
                outcome["error"] = error.code

        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(thread.join, 2)
        return thread, outcome

    def pump_one(self):
        callback, job = self.events.get(timeout=1)
        callback(job)

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
        nested = bridge._GuiJob("view_axonometric", {})

        def operation(method, params):
            calls.append(method + ":start")
            if method == "view_fit":
                self.dispatcher.run(nested)
            calls.append(method + ":end")

        with patch.object(bridge, "_perform_gui", side_effect=operation):
            self.dispatcher.run(bridge._GuiJob("view_fit", {}))
        self.assertEqual(calls, ["view_fit:start", "view_fit:end", "view_axonometric:start", "view_axonometric:end"])


if __name__ == "__main__":
    unittest.main()
