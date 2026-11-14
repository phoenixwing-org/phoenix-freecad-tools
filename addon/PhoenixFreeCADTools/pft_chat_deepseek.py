# SPDX-License-Identifier: Apache-2.0
"""In-memory DeepSeek text chat. Transport is injected, with no Codex or CAD dependency."""

import json
import re
from collections import deque
from datetime import datetime
import time


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
_ROUTES = {"models": DEEPSEEK_BASE_URL + "/models", "chat": DEEPSEEK_BASE_URL + "/chat/completions"}
_KEY = re.compile(r"[!-~]{8,512}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_MAX_INPUT = 16 * 1024
_MAX_HISTORY = 96 * 1024
_MAX_BODY = 192 * 1024
_MAX_TOOL_RESULT = 48 * 1024
_MAX_TOOL_ARGS = 16 * 1024
_MAX_TOOL_CALLS = 8
_TOOL_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_APPROVAL_TOOLS = frozenset(("request_part_change_approval", "request_drawing_export_approval"))
_CONTINUING = object()
_ERRORS = {
    "MCP": "FreeCAD MCP 未通过核验或连接已失效，请检查本窗口服务后重新连接。",
    "TOOL_LIMIT": "本轮工具调用超出限制，请分步操作。",
    "KEY_SAVE": "DeepSeek 已连接，但无法记住 Key。",
    "KEY": "请输入有效的 DeepSeek API Key。",
    "AUTH": "DeepSeek 拒绝了此 API Key，请核对后重新连接。",
    "BALANCE": "DeepSeek 账户余额不足，请核对 API 账户。",
    "LIMIT": "DeepSeek 请求过多，请稍后手动重试。",
    "HTTP": "DeepSeek 未完成请求，请核对模型或服务状态。",
    "NETWORK": "无法连接 DeepSeek，请检查网络或系统代理。",
    "TIMEOUT": "等待 DeepSeek 超时，可手动重试。",
    "RESPONSE": "DeepSeek 返回的响应无法验证。",
    "SIZE": "本次聊天内容超出限制，请缩短消息或重新连接。",
    "INPUT": "请输入有效的聊天消息，请勿在聊天中发送 API Key。",
    "PARTIAL": "DeepSeek 未完整回复，本次内容未加入后续上下文。",
}


class PftDeepSeekChat:
    """One active HTTPS request per dock. A stopped turn never updates later state."""

    def __init__(self, transport, changed):
        self._transport, self._changed = transport, changed
        self._generation = self._request_id = 0
        self._mcp = None
        self._reset()

    def _reset(self):
        self._key = self._selected = self._actual_model = None
        self._tools, self._tool_calls = {}, []
        self._turn = None
        self._models, self._messages, self._rows, self._times = [], [], [], []
        self._phase, self._error = "disconnected", None
        self._started = self._finished = self._first_response = None

    @property
    def snapshot(self):
        timing = None
        if self._started is not None:
            end = self._finished if self._finished is not None else time.monotonic()
            timing = {"elapsed_seconds": max(0, end - self._started), "finished": self._finished is not None,
                      "first_response_seconds": self._first_response}
        ready = self._phase == "ready"
        configurable = ready and not self._messages
        return {"provider": "deepseek", "phase": self._phase, "connection_generation": self._generation,
                "mcp_enabled": self._mcp is not None,
                "tool_calls": [dict(call) for call in self._tool_calls],
                "transcript": [dict(row) for row in self._rows], "message_times": list(self._times),
                "error": dict(self._error) if self._error else None, "turn_timing": timing,
                "is_busy": self._phase in ("connecting", "sending"), "can_send": ready and bool(self._models),
                "models": [dict(row) for row in self._models], "selected_model": self._selected,
                "models_status": "loading" if self._phase == "connecting" else "ready" if self._models else "idle",
                "reported_model": self._actual_model,
                "model": self._actual_model or (self._model() if self._models else None),
                "can_select_model": configurable, "can_refresh_models": configurable}

    def _publish(self, error=None):
        self._error = {"code": "CHAT_DEEPSEEK_" + error, "message": _ERRORS[error]} if error else None
        self._changed(self.snapshot)

    def _model(self):
        return self._selected or next(row["model"] for row in self._models if row["is_default"])

    def _append(self, role, text, status="completed"):
        self._rows.append({"role": role, "text": text, "status": status})
        self._times.append(datetime.now().strftime("%H:%M"))
        if len(self._rows) > 80:
            self._rows.pop(0)
            self._times.pop(0)

    def _request(self, route, payload, success):
        self._request_id += 1
        current = self._request_id

        def completed(status, raw, fault=None):
            if current != self._request_id:
                return
            self._request_id += 1
            error = fault if fault in ("NETWORK", "TIMEOUT", "SIZE", "RESPONSE") else None
            if error is None and status != 200:
                error = {401: "AUTH", 403: "AUTH", 402: "BALANCE", 429: "LIMIT"}.get(status, "HTTP")
            warning = None
            if error is None:
                try:
                    if type(raw) is not bytes or len(raw) > _MAX_BODY:
                        raise ValueError()
                    value = json.loads(raw.decode("utf-8"))
                    if type(value) is not dict or "error" in value:
                        raise ValueError()
                    warning = success(value)
                except (ValueError, UnicodeError, RecursionError, TypeError):
                    error = "RESPONSE"
            if error is not None:
                if self._phase == "sending":
                    self._finished = time.monotonic()
                if self._phase == "connecting" or error == "AUTH":
                    self._phase, self._key = "error", None
                    self._close_mcp()
                else:
                    self._phase = "ready"
                self._publish(error)
            elif warning is not _CONTINUING:
                self._publish(warning)

        try:
            self._transport.request(_ROUTES[route], self._key, payload, completed)
        except Exception:
            completed(0, b"", "NETWORK")

    def connect(self, key, *, remember_key=None, mcp=None):
        if self._phase not in ("disconnected", "error"):
            return
        self._close_mcp()
        self._generation += 1
        self._reset()
        if type(key) is not str or _KEY.fullmatch(key) is None:
            self._phase = "error"
            self._publish("KEY")
            return
        self._key = key
        self._mcp = mcp
        if mcp is None:
            self._load_models(remember_key)
            return

        generation = self._generation
        self._phase = "connecting"
        self._publish()

        def ready(tools):
            if generation != self._generation or self._phase != "connecting":
                return
            if not tools:
                self._mcp_failed()
                return
            self._tools = {tool["function"]["name"]: tool for tool in tools}
            self._load_models(remember_key)

        mcp.start(ready)

    def _load_models(self, remember_key=None):
        self._phase = "connecting"
        self._publish()

        def receive(value):
            data = value.get("data")
            if value.get("object") != "list" or type(data) is not list or not 1 <= len(data) <= 128:
                raise ValueError()
            ids = []
            for row in data:
                model = row.get("id") if type(row) is dict else None
                if type(model) is not str or _MODEL.fullmatch(model) is None or self._key in model or model in ids:
                    raise ValueError()
                ids.append(model)
            default = "deepseek-flash" if "deepseek-flash" in ids else ids[0]
            self._models = [{"model": model, "display_name": model, "is_default": model == default} for model in ids]
            if self._selected not in ids:
                self._selected = None
            self._phase = "ready"
            if remember_key is not None:
                try:
                    remember_key(self._key)
                except (OSError, ValueError):
                    return "KEY_SAVE"

        self._request("models", None, receive)

    def refresh_models(self):
        if self.snapshot["can_refresh_models"]:
            self._load_models()

    def select_model(self, value):
        if self.snapshot["can_select_model"] and (value is None or any(row["model"] == value for row in self._models)):
            self._selected = value
            self._publish()

    def _close_mcp(self):
        mcp, self._mcp = self._mcp, None
        if mcp is not None:
            mcp.stop()

    def _mcp_failed(self):
        self._close_mcp()
        self._phase, self._key = "error", None
        if self._started is not None:
            self._finished = time.monotonic()
        self._publish("MCP")

    def send(self, text):
        if self._phase != "ready":
            return
        try:
            if type(text) is not str or not text.strip() or "\0" in text or self._key in text:
                self._publish("INPUT")
                return
            if len(text.encode("utf-8")) > _MAX_INPUT:
                self._publish("SIZE")
                return
        except UnicodeError:
            self._publish("INPUT")
            return
        if self._mcp is not None and not self._mcp.ready:
            self._mcp_failed()
            return
        user = {"role": "user", "content": text}
        messages = [*self._messages, user]
        instructions = "Text chat only. You have no FreeCAD tools in this session."
        if self._tools:
            from pft_chat_controller import _developer_instructions
            instructions = _developer_instructions({})
        instructions = (
            "The configured provider for this conversation is the official DeepSeek API at api.deepseek.com. "
            "For identity questions, report that configured service. Do not guess the underlying model version "
            "or another provider; the client displays the returned model metadata. " + instructions)
        system = {"role": "system", "content": instructions}
        model = self._model()
        turn = {"messages": messages, "ids": set(), "requests": set(), "paused": False, "waiting_on_mcp": False}
        self._turn = turn
        self._tool_calls = []
        self._append("user", text)
        self._phase = "sending"
        self._started, self._finished, self._first_response = time.monotonic(), None, None
        self._publish()

        def request():
            payload = {"model": model, "messages": [system, *messages], "stream": False,
                       "max_tokens": 4096, "thinking": {"type": "disabled"}}
            if self._tools:
                payload.update(tools=list(self._tools.values()), tool_choice="none" if turn["paused"] else "auto")
            if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > _MAX_HISTORY:
                self._phase, self._finished = "ready", time.monotonic()
                self._publish("SIZE")
                return
            self._request("chat", payload, receive)

        def receive(value):
            actual = value.get("model")
            choices = value.get("choices")
            if (type(actual) is not str or _MODEL.fullmatch(actual) is None or self._key in actual
                    or type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict):
                raise ValueError()
            choice = choices[0]
            message, finish = choice.get("message"), choice.get("finish_reason")
            if type(message) is not dict or message.get("role") != "assistant":
                raise ValueError()
            answer, calls = message.get("content"), message.get("tool_calls")
            if answer is not None and (type(answer) is not str or self._key in answer or "\0" in answer):
                raise ValueError()
            self._actual_model = actual
            if self._first_response is None:
                self._first_response = time.monotonic() - self._started
            if finish == "tool_calls":
                if not self._tools or turn["paused"]:
                    raise ValueError()
                if type(calls) is list and len(turn["ids"]) + len(calls) > _MAX_TOOL_CALLS:
                    self._phase, self._finished = "ready", time.monotonic()
                    return "TOOL_LIMIT"
                self._run_tools(turn, calls, answer, request)
                return _CONTINUING
            if (not answer or not answer.strip() or len(answer.encode("utf-8")) > _MAX_BODY
                    or calls not in (None, []) or finish not in ("stop", "length")):
                raise ValueError()
            self._phase, self._finished = "ready", time.monotonic()
            self._append("assistant", answer, "completed" if finish == "stop" else "error")
            if finish == "stop":
                self._messages = [*messages, {"role": "assistant", "content": answer}]
            return "PARTIAL" if finish == "length" else None

        request()

    def _contains_key(self, value):
        if isinstance(value, str):
            return self._key in value
        if isinstance(value, dict):
            return any(self._contains_key(key) or self._contains_key(item) for key, item in value.items())
        if isinstance(value, list):
            return any(self._contains_key(item) for item in value)
        return False

    def _run_tools(self, turn, calls, answer, resume):
        from pft_chat_qprocess import _decode_line

        if type(calls) is not list or not 1 <= len(calls) <= _MAX_TOOL_CALLS:
            raise ValueError()
        pending, normalized = deque(), []
        ids = turn["ids"]
        for call in calls:
            if type(call) is not dict or call.get("type") != "function" or type(call.get("function")) is not dict:
                raise ValueError()
            ident, function = call.get("id"), call["function"]
            name, raw = function.get("name"), function.get("arguments")
            if (type(ident) is not str or not _TOOL_ID.fullmatch(ident) or ident in ids or self._key in ident
                    or type(name) is not str or name not in self._tools or type(raw) is not str
                    or len(raw.encode("utf-8")) > _MAX_TOOL_ARGS or self._key in raw
                    or (name in _APPROVAL_TOOLS and len(calls) != 1)):
                raise ValueError()
            arguments = _decode_line(raw.encode("utf-8"))
            canonical = json.dumps(arguments, ensure_ascii=False, allow_nan=False, sort_keys=True)
            signature = (name, canonical)
            if self._contains_key(arguments) or signature in turn["requests"]:
                raise ValueError()
            ids.add(ident)
            turn["requests"].add(signature)
            normalized.append({"id": ident, "type": "function", "function": {"name": name, "arguments": canonical}})
            pending.append((ident, name, arguments))

        turn["messages"].append({"role": "assistant", "content": answer, "tool_calls": normalized})
        if answer and answer.strip():
            self._append("assistant", answer)

        def next_tool():
            if self._phase != "sending" or self._turn is not turn:
                return
            if not pending:
                resume()
                return
            ident, name, arguments = pending.popleft()
            row = {"name": name, "status": "in_progress"}
            self._tool_calls.append(row)
            turn["waiting_on_mcp"] = True
            self._publish()

            def completed(result):
                if self._phase != "sending" or self._turn is not turn or not turn["waiting_on_mcp"]:
                    return
                turn["waiting_on_mcp"] = False
                if result is None:
                    row["status"] = "failed"
                    self._mcp_failed()
                    return
                content = json.dumps(result, ensure_ascii=False, allow_nan=False)
                if len(content.encode("utf-8")) > _MAX_TOOL_RESULT or self._contains_key(result):
                    result = {"ok": False, "code": "MCP_TOOL_OUTPUT_WITHHELD"}
                    content = json.dumps(result)
                row["status"] = "completed" if result.get("ok") is True else "failed"
                turn["messages"].append({"role": "tool", "tool_call_id": ident, "content": content})
                if name in _APPROVAL_TOOLS:
                    turn["paused"] = True
                self._publish()
                next_tool()

            self._mcp.call(name, arguments, completed)

        next_tool()

    def interrupt(self):
        if self._phase == "sending":
            self._request_id += 1
            self._transport.cancel()
            self._phase, self._finished = "ready", time.monotonic()
            self._append("assistant", "", "interrupted")
            if self._turn is not None and self._turn["waiting_on_mcp"]:
                self._mcp_failed()
            else:
                self._publish()
            self._turn = None

    def disconnect(self):
        self._request_id += 1
        self._generation += 1
        self._close_mcp()
        self._reset()
        self._transport.cancel()
        self._publish()
