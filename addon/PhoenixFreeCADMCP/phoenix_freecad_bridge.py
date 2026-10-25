# SPDX-License-Identifier: Apache-2.0
"""Original, fixed-command JSON bridge for the Phoenix FreeCAD MCP client.

No external Python dependencies. Importing this module does not start a server
or alter a document. Call start_server() from FreeCAD's Python console or menu.
"""

import itertools
import importlib.util
from pathlib import Path
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

def _load_helper(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_operations = _load_helper("phoenix_freecad_operations")
_runtime = _load_helper("phoenix_freecad_runtime")
_business = _load_helper("phoenix_freecad_business")
_OPERATION_METHODS = set(_operations.METHODS)
_BUSINESS_METHODS = set(_business.METHODS)
SUPPORTED_METHODS = sorted({"ping", "get_status", "list_documents", "view_fit", "view_axonometric"} | _OPERATION_METHODS | _runtime.METHODS | _BUSINESS_METHODS)
ADDON_VERSION = "0.3.0"
PROTOCOL_VERSION = 1
DEFAULT_PORT = 9876
MAX_PAYLOAD_BYTES = 262144
GUI_TIMEOUT_SECONDS = 15.0

_state_lock = threading.RLock()
_server = None
_server_thread = None
_stop_thread = None
_dispatcher = None
_job_ids = itertools.count(1)
_health = {"state": "idle", "job_id": 0, "operation": "", "started": 0.0}


class BridgeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _success(data):
    return {"ok": True, "data": data}


def _failure(code, message):
    return {"ok": False, "error": {"code": code, "message": message}}


def _health_snapshot():
    with _state_lock:
        result = {key: value for key, value in _health.items() if key != "started"}
        result["running_seconds"] = (
            round(max(0.0, time.monotonic() - _health["started"]), 3)
            if _health["started"] else 0.0
        )
        return result


def get_status():
    """Read server and dispatch state without accessing FreeCAD or Qt."""
    with _state_lock:
        running = _server is not None and _server_thread is not None and _server_thread.is_alive()
        port = _server.server_address[1] if _server is not None else DEFAULT_PORT
        return {
            "protocol_version": PROTOCOL_VERSION,
            "addon_name": "PhoenixFreeCADMCP",
            "addon_version": ADDON_VERSION,
            "supported_methods": SUPPORTED_METHODS,
            "script_session_id": _runtime.SESSION_ID,
            "part_previews": {"ttl_seconds": _business.TTL_SECONDS, "max_plans": _business.MAX_PLANS, "max_preview_bytes": _business.MAX_PREVIEW_BYTES, "storage": "memory_only"},
            "running": bool(running),
            "stopping": bool(_stop_thread is not None and _stop_thread.is_alive()),
            "host": "127.0.0.1",
            "port": port,
            "gui_dispatch": _health_snapshot(),
        }


def _validate_request(payload):
    if not isinstance(payload, dict) or set(payload) - {"method", "params"}:
        raise BridgeError("INVALID_REQUEST", "请求必须是仅包含 method 和 params 的 JSON 对象。")
    method = payload.get("method")
    allowed = set(SUPPORTED_METHODS)
    if not isinstance(method, str) or method not in allowed:
        raise BridgeError("METHOD_NOT_FOUND", "该方法未开放；请先检查 supported_methods。")
    params = payload.get("params", {})
    if not isinstance(params, dict):
        raise BridgeError("INVALID_PARAMS", "params 必须是 JSON 对象。")
    if (_operations.validate(method, params, BridgeError) or _runtime.validate(method, params, BridgeError)
            or _business.validate(method, params, BridgeError)):
        return method, params
    keys = {"docName"} if method == "view_fit" else {"docName", "fit"} if method == "view_axonometric" else set()
    if set(params) - keys:
        raise BridgeError("INVALID_PARAMS", "参数包含未支持的字段。")
    if "docName" in params:
        value = params["docName"]
        if not isinstance(value, str) or not value or len(value) > 512 or any(ord(char) < 32 for char in value):
            raise BridgeError("INVALID_PARAMS", "docName 必须是文档内部名称；省略时使用当前活动文档。")
    if "fit" in params and not isinstance(params["fit"], bool):
        raise BridgeError("INVALID_PARAMS", "fit 必须为布尔值。")
    return method, params


def _perform_gui(method, params):
    import FreeCAD as App
    import FreeCADGui as Gui

    if method in _OPERATION_METHODS:
        return _operations.perform(method, params, App, Gui, BridgeError)
    if method in _BUSINESS_METHODS:
        return _business.perform(method, params, App, Gui, BridgeError)
    if method == "execute_script":
        return _runtime.execute(params, App, Gui, BridgeError)
    if method == "capture_view":
        return _runtime.capture(params, App, Gui, BridgeError)
    if method == "list_documents":
        return list(App.listDocuments().keys())
    doc_name = params.get("docName")
    if doc_name:
        try:
            document = App.getDocument(doc_name)
        except Exception:
            raise BridgeError("DOCUMENT_NOT_FOUND", "指定文档未打开。")
        if document is None:
            raise BridgeError("DOCUMENT_NOT_FOUND", "指定文档未打开。")
    else:
        document = App.ActiveDocument
        if document is None:
            raise BridgeError("NO_ACTIVE_DOCUMENT", "FreeCAD 没有活动文档。")
    required = ("fitAll",) if method == "view_fit" else ("viewAxonometric",) + (("fitAll",) if params.get("fit", False) else ())
    view = _operations._view(document, Gui, BridgeError, required=required)
    if method == "view_fit":
        if view is None or not callable(getattr(view, "fitAll", None)):
            raise BridgeError("VIEW_UNAVAILABLE", "当前视图不支持适合窗口。")
        view.fitAll()
        return {"document": document.Name, "view": "fit", "fit": True}
    if method == "view_axonometric":
        if view is None or not callable(getattr(view, "viewAxonometric", None)):
            raise BridgeError("VIEW_UNAVAILABLE", "当前视图不支持轴测。")
        fit = params.get("fit", False)
        if fit and not callable(getattr(view, "fitAll", None)):
            raise BridgeError("VIEW_UNAVAILABLE", "当前视图不支持适合窗口。")
        view.viewAxonometric()
        if fit:
            view.fitAll()
        return {"document": document.Name, "view": "axonometric", "fit": fit}
    raise BridgeError("METHOD_NOT_FOUND", "该 GUI 方法未开放。")


class _GuiJob:
    def __init__(self, method, params):
        self.id = next(_job_ids)
        self.method = method
        self.params = params
        self.lock = threading.Lock()
        self.event = threading.Event()
        self.started = False
        self.cancelled = False
        self.result = None
        self.error = None


def _create_gui_dispatcher():
    from PySide import QtCore, QtWidgets

    application = QtWidgets.QApplication.instance()
    if application is None or QtCore.QThread.currentThread() != application.thread():
        raise BridgeError("GUI_THREAD_REQUIRED", "请从 FreeCAD 界面菜单或 Python 控制台启动服务。")

    class GuiDispatcher(QtCore.QObject):
        requested = QtCore.Signal(object)

        def __init__(self):
            super().__init__(application)
            self.lock = threading.Lock()
            self.jobs = {}
            self.closed = False
            self.pending = []
            self.processing = False
            self.requested.connect(self.run, QtCore.Qt.QueuedConnection)

        @QtCore.Slot(object)
        def run(self, job):
            # A view callback may pump Qt events. Do not allow a second request
            # to re-enter FreeCAD while the first callback is still executing.
            self.pending.append(job)
            if self.processing:
                return
            self.processing = True
            try:
                while self.pending:
                    self._execute(self.pending.pop(0))
            finally:
                self.processing = False

        def _execute(self, job):
            with job.lock:
                if job.cancelled:
                    return
                job.started = True
                with _state_lock:
                    _health.update(state="running", job_id=job.id, operation=job.method, started=time.monotonic())
            try:
                job.result = _perform_gui(job.method, job.params)
            except BridgeError as error:
                job.error = error
            except BaseException as error:
                job.error = BridgeError("GUI_OPERATION_FAILED", "FreeCAD 操作失败（%s）。" % type(error).__name__)
            finally:
                with _state_lock:
                    if _health["job_id"] == job.id:
                        _health.update(state="idle", job_id=0, operation="", started=0.0)
                with self.lock:
                    self.jobs.pop(job.id, None)
                job.event.set()

        def dispatch(self, method, params, timeout):
            # Console/menu calls already run on Qt's main thread.
            if QtCore.QThread.currentThread() == application.thread():
                return _perform_gui(method, params)
            job = _GuiJob(method, params)
            with self.lock:
                if self.closed:
                    raise BridgeError("SERVER_STOPPING", "服务正在停止。")
                self.jobs[job.id] = job
            self.requested.emit(job)
            if not job.event.wait(timeout):
                with job.lock:
                    if not job.started:
                        job.cancelled = True
                        with self.lock:
                            self.jobs.pop(job.id, None)
                        raise BridgeError("GUI_QUEUE_TIMEOUT", "等待 GUI 超时；尚未开始的请求已取消，不会自动重试。")
                    if not job.event.is_set():
                        with _state_lock:
                            if _health["job_id"] == job.id:
                                _health["state"] = "timed_out"
                        raise BridgeError("GUI_TIMEOUT", "GUI 操作超时，但已经开始的操作可能仍在执行；不会自动取消或重试。")
            if job.error is not None:
                raise job.error
            return job.result

        def close(self):
            with self.lock:
                self.closed = True
                pending = list(self.jobs.values())
            for job in pending:
                with job.lock:
                    if not job.started:
                        job.cancelled = True
                        job.error = BridgeError("SERVER_STOPPING", "服务已停止，排队请求已取消。")
                        job.event.set()
            self.deleteLater()

    return GuiDispatcher()


def _dispatch_gui(method, params):
    with _state_lock:
        dispatcher = _dispatcher
    if dispatcher is None:
        raise BridgeError("SERVER_NOT_RUNNING", "Phoenix FreeCAD MCP 服务尚未启动。")
    return dispatcher.dispatch(method, params, GUI_TIMEOUT_SECONDS)


def process_request(payload):
    """Validate the fixed protocol and return a JSON-serializable envelope."""
    try:
        method, params = _validate_request(payload)
        if method == "ping":
            return _success({"pong": True})
        if method == "get_status":
            return _success(get_status())
        if method == "get_script_status":
            return _success(_runtime.status(params["requestId"], BridgeError))
        if method == "execute_script":
            fresh, snapshot = _runtime.claim(params, BridgeError)
            if not fresh:
                if snapshot["state"] in {"failed", "cancelled"}:
                    error = snapshot["error"]
                    raise BridgeError(error["code"], error["message"])
                return _success(snapshot)
            try:
                return _success(_dispatch_gui(method, params))
            except BridgeError as error:
                _runtime.dispatch_error(params["requestId"], error)
                raise
        return _success(_dispatch_gui(method, params))
    except BridgeError as error:
        return _failure(error.code, error.message)
    except Exception as error:
        return _failure("INTERNAL_ERROR", "服务处理请求失败（%s）。" % type(error).__name__)


def _decode_json(body):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("non-finite JSON")

    return json.loads(body.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject_constant)


class _BridgeHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _RequestHandler(BaseHTTPRequestHandler):
    server_version = "PhoenixFreeCADMCP/0.3.0"
    sys_version = ""

    def log_message(self, format, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(5.0)

    def _reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _reject(self, status, code, message):
        self._reply(status, _failure(code, message))

    def do_POST(self):
        if self.path != "/rpc":
            return self._reject(404, "NOT_FOUND", "仅开放 /rpc。")
        if self.headers.get("Origin") is not None or any(key.lower().startswith("sec-fetch-") for key in self.headers.keys()):
            return self._reject(403, "BROWSER_REQUEST_REJECTED", "不接受浏览器网页请求。")
        try:
            parsed_host = urlsplit("//" + (self.headers.get("Host") or ""))
            valid_host = parsed_host.hostname in {"127.0.0.1", "localhost", "::1"} and not parsed_host.username and not parsed_host.password and not parsed_host.path and not parsed_host.query and not parsed_host.fragment
        except ValueError:
            valid_host = False
        if not valid_host:
            return self._reject(403, "HOST_REJECTED", "仅接受发送到本机回环地址的请求。")
        if (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
            return self._reject(415, "UNSUPPORTED_MEDIA_TYPE", "请求必须使用 application/json。")
        if self.headers.get("Transfer-Encoding") is not None:
            return self._reject(400, "INVALID_REQUEST", "不支持分块请求。")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit():
            return self._reject(411, "CONTENT_LENGTH_REQUIRED", "请求必须包含有效的 Content-Length。")
        if len(lengths[0]) > 10:
            return self._reject(413, "PAYLOAD_TOO_LARGE", "请求超过大小限制。")
        length = int(lengths[0])
        if length > MAX_PAYLOAD_BYTES:
            return self._reject(413, "PAYLOAD_TOO_LARGE", "请求超过大小限制。")
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("incomplete body")
            payload = _decode_json(body)
        except (ValueError, UnicodeError, TimeoutError, OSError, RecursionError):
            return self._reject(400, "INVALID_JSON", "请求不是有效的 UTF-8 JSON。")
        result = process_request(payload)
        return self._reply(200, result)

    def do_GET(self):
        self._reject(405, "METHOD_NOT_ALLOWED", "仅接受 POST /rpc。")

    def do_OPTIONS(self):
        self._reject(403, "BROWSER_REQUEST_REJECTED", "不开放浏览器跨域请求。")


def start_server(port=DEFAULT_PORT):
    """Start on the GUI thread; never close, save, reload or edit a document."""
    global _server, _server_thread, _dispatcher
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise BridgeError("INVALID_PORT", "端口必须是 0 到 65535 的整数。")
    with _state_lock:
        if _server is not None:
            return get_status()
        if _stop_thread is not None and _stop_thread.is_alive():
            raise BridgeError("SERVER_STOPPING", "服务仍在停止，请稍后重新启动。")
        dispatcher = _create_gui_dispatcher()
        try:
            server = _BridgeHttpServer(("127.0.0.1", port), _RequestHandler)
        except OSError:
            dispatcher.close()
            raise BridgeError("PORT_UNAVAILABLE", "端口不可用；没有停止或替换其他服务。")
        _dispatcher = dispatcher
        _server = server
        _server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, name="PhoenixFreeCADMCP-HTTP", daemon=True)
        try:
            _server_thread.start()
        except Exception:
            _server = None
            _server_thread = None
            _dispatcher = None
            server.server_close()
            dispatcher.close()
            raise BridgeError("START_FAILED", "服务线程启动失败。")
        return get_status()


def stop_server():
    """Stop accepting requests without blocking Qt or cancelling a running view operation."""
    global _server, _server_thread, _dispatcher, _stop_thread
    with _state_lock:
        server = _server
        dispatcher = _dispatcher
        if server is None:
            return get_status()
        _server = None
        _server_thread = None
        _dispatcher = None

        def shutdown():
            try:
                server.shutdown()
            finally:
                server.server_close()

        _stop_thread = threading.Thread(target=shutdown, name="PhoenixFreeCADMCP-Stop", daemon=True)
        _stop_thread.start()
    # Never acquire a job lock while holding the global state lock: a request
    # reaching its deadline may hold that job lock while updating health.
    if dispatcher is not None:
        dispatcher.close()
    return get_status()


def fit_view(doc_name=None):
    """GUI-console/menu helper. This only changes the view framing."""
    params = {} if doc_name is None else {"docName": doc_name}
    _validate_request({"method": "view_fit", "params": params})
    # Require the same GUI-thread check even when the HTTP service is stopped.
    dispatcher = _create_gui_dispatcher()
    try:
        return dispatcher.dispatch("view_fit", params, GUI_TIMEOUT_SECONDS)
    finally:
        dispatcher.close()


def axonometric_view(doc_name=None, fit=False):
    """GUI-console/menu helper. fit defaults to False."""
    params = {"fit": fit}
    if doc_name is not None:
        params["docName"] = doc_name
    _validate_request({"method": "view_axonometric", "params": params})
    dispatcher = _create_gui_dispatcher()
    try:
        return dispatcher.dispatch("view_axonometric", params, GUI_TIMEOUT_SECONDS)
    finally:
        dispatcher.close()
