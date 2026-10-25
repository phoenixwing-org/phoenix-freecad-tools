# SPDX-License-Identifier: Apache-2.0
"""Explicit GUI scripts and bounded model capture; no server or startup side effects."""
import base64
import hashlib
from itertools import islice
import io
import json
import uuid
import math
import re
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

METHODS = {"execute_script", "get_script_status", "capture_view"}
MAX_IMAGE_BYTES = 600 * 1024
_lock = threading.RLock()
_scripts = OrderedDict()
_seen = {}
SESSION_ID = str(uuid.uuid4())


def validate(method, params, Error):
    if method not in METHODS:
        return False
    keys = {"code", "requestId"} if method == "execute_script" else {"requestId"} if method == "get_script_status" else {"docName", "width", "height"}
    if set(params) - keys:
        raise Error("INVALID_PARAMS", "参数包含未支持的字段。")
    if method in {"execute_script", "get_script_status"}:
        value = params.get("requestId")
        if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value) is None:
            raise Error("INVALID_PARAMS", "requestId 必须是 1 至 64 字符的字母数字、连字符或下划线标识。")
    if method == "execute_script":
        code = params.get("code")
        if not isinstance(code, str) or not 1 <= len(code) <= 32768 or "\x00" in code or any(0xD800 <= ord(c) <= 0xDFFF for c in code):
            raise Error("INVALID_PARAMS", "code 必须是 1 至 32768 字符的 Python 脚本，不能含空字符。")
    if method == "capture_view":
        if "docName" in params:
            name = params["docName"]
            if not isinstance(name, str) or not name or len(name) > 256 or any(ord(c) < 32 for c in name):
                raise Error("INVALID_PARAMS", "docName 必须是文档内部名称。")
        for key in ("width", "height"):
            if key in params and (type(params[key]) is not int or not 128 <= params[key] <= 1024):
                raise Error("INVALID_PARAMS", "图像宽高必须为 128 至 1024 的整数。")
    return True


def _snapshot(record):
    return {k: v for k, v in record.items() if k != "digest"}


def claim(params, Error):
    digest = hashlib.sha256(params["code"].encode("utf-8")).hexdigest()
    with _lock:
        found = _scripts.get(params["requestId"])
        if found:
            if found["digest"] != digest:
                raise Error("REQUEST_ID_CONFLICT", "同一 requestId 已用于其他脚本。")
            return False, _snapshot(found)
        if params["requestId"] in _seen:
            if _seen[params["requestId"]] != digest:
                raise Error("REQUEST_ID_CONFLICT", "同一 requestId 已用于其他脚本。")
            raise Error("SCRIPT_RESULT_EXPIRED", "结果已过期，此请求不会再次执行；请核对模型状态。")
        if len(_seen) >= 4096:
            raise Error("SCRIPT_HISTORY_FULL", "当前服务会话脚本记录已满，请先核对任务状态。")
        completed = [key for key, value in _scripts.items() if value["state"] in {"done", "failed", "cancelled"}]
        while len(completed) >= 20:
            _scripts.pop(completed.pop(0))
        if len(_scripts) >= 64:
            raise Error("SCRIPT_QUEUE_FULL", "脚本队列已满，请等待现有任务完成。")
        record = {"request_id": params["requestId"], "digest": digest, "state": "queued", "created_at": time.time(), "started_at": None, "finished_at": None}
        _seen[params["requestId"]] = digest
        _scripts[params["requestId"]] = record
        return True, _snapshot(record)


def status(request_id, Error):
    with _lock:
        if request_id not in _scripts:
            raise Error("SCRIPT_NOT_FOUND", "未找到此脚本请求；状态仅保留于当前桥接模块内存。")
        return _snapshot(_scripts[request_id])


def dispatch_error(request_id, error):
    with _lock:
        record = _scripts.get(request_id)
        if record and record["state"] == "queued":
            if error.code in {"GUI_QUEUE_TIMEOUT", "SERVER_STOPPING", "SERVER_NOT_RUNNING"}:
                record.update(state="cancelled", finished_at=time.time(), error={"code": error.code, "message": error.message})
                completed = sorted((key for key, value in _scripts.items() if value["state"] in {"done", "failed", "cancelled"}), key=lambda key: _scripts[key]["finished_at"] or 0)
                while len(completed) > 20:
                    _scripts.pop(completed.pop(0))
            else:
                record.update(state="starting", dispatch_error={"code": error.code, "message": error.message})


class _Output(io.StringIO):
    def __init__(self):
        super().__init__()
        self.truncated = False

    def write(self, text):
        available = max(0, 8192 - self.tell())
        if len(text) > available:
            self.truncated = True
        super().write(text[:available])
        return len(text)


def _json_value(value, depth=0, budget=None):
    if budget is None:
        budget = [512]
    budget[0] -= 1
    if budget[0] < 0 or depth > 5:
        return {"truncated": True}
    if value is None or type(value) in (bool, int):
        return value if type(value) is not int or abs(value) < 10 ** 100 else {"type": "large_integer"}
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:8192]
    if isinstance(value, (list, tuple)):
        return [_json_value(v, depth + 1, budget) for v in value[:100]]
    if isinstance(value, dict):
        return {k[:256]: _json_value(v, depth + 1, budget) for k, v in islice(value.items(), 100) if isinstance(k, str)}
    return {"type": type(value).__name__, "serialized": False}


def execute(params, App, Gui, Error):
    request_id = params["requestId"]
    with _lock:
        record = _scripts[request_id]
        record.update(state="running", started_at=time.time())
    out, err = _Output(), _Output()
    namespace = {"__name__": "__phoenix_script__", "App": App, "FreeCAD": App, "Gui": Gui, "FreeCADGui": Gui}
    failure = None
    try:
        with redirect_stdout(out), redirect_stderr(err):
            exec(compile(params["code"], "<phoenix-mcp-script>", "exec"), namespace, namespace)
        result = _json_value(namespace.get("result"))
        if len(json.dumps(result, ensure_ascii=True).encode("ascii")) > 131072:
            result = {"truncated": True, "reason": "result exceeds 128 KiB"}
    except BaseException as error:
        try:
            message = str(error)
        except BaseException:
            message = "exception message unavailable"
        failure = {"code": "SCRIPT_FAILED", "message": (type(error).__name__ + ": " + message)[:1000]}
        result = None
    with _lock:
        record.update(state="failed" if failure else "done", finished_at=time.time(), result=result, stdout=out.getvalue(), stderr=err.getvalue(), output_truncated=out.truncated or err.truncated)
        if failure:
            record["error"] = failure
        snapshot = _snapshot(record)
        completed = sorted((key for key, value in _scripts.items() if value["state"] in {"done", "failed", "cancelled"}), key=lambda key: _scripts[key]["finished_at"] or 0)
        while len(completed) > 20:
            _scripts.pop(completed.pop(0))
    if failure:
        raise Error(failure["code"], failure["message"])
    return snapshot


def capture(params, App, Gui, Error):
    name = params.get("docName")
    try:
        document = App.getDocument(name) if name else App.ActiveDocument
    except Exception:
        document = None
    if document is None:
        raise Error("DOCUMENT_NOT_FOUND" if name else "NO_ACTIVE_DOCUMENT", "指定文档未打开。" if name else "FreeCAD 没有活动文档。")
    try:
        gui_document = Gui.activeDocument()
        active_document = getattr(gui_document, "Document", None)
        target_is_active = getattr(active_document, "Name", None) == document.Name
        view = gui_document.activeView() if target_is_active else None
    except Exception:
        view = None
    if view is None or not callable(getattr(view, "saveImage", None)):
        raise Error("VIEW_UNAVAILABLE", "请先在 FreeCAD 中激活目标文档的三维视图；不截图后台文档或图纸页，也不自动切换或创建视图。")
    width, height = params.get("width", 800), params.get("height", 600)
    with tempfile.TemporaryDirectory(prefix="phoenix-freecad-capture-") as folder:
        output = Path(folder) / "view.png"
        if callable(getattr(view, "redraw", None)):
            view.redraw()
        view.saveImage(str(output), width, height, "Current")
        if not output.is_file() or not 0 < output.stat().st_size <= MAX_IMAGE_BYTES:
            raise Error("IMAGE_UNAVAILABLE", "图像未生成或超过 600 KiB；请降低分辨率。")
        data = output.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise Error("IMAGE_UNAVAILABLE", "FreeCAD 没有生成有效的 PNG 图像。")
    return {"document": document.Name, "mime_type": "image/png", "data": base64.b64encode(data).decode("ascii"), "width": width, "height": height}
