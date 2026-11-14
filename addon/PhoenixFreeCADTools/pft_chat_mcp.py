# SPDX-License-Identifier: Apache-2.0
"""Local MCP stdio session for direct chat providers, with no provider credentials."""

import json
import os
from pathlib import Path
import re

from pft_chat_controller import _PFT_CHAT_TOOLS, _mcp_environment


_PROTOCOL = "2025-11-25"
_HEX = re.compile(r"[0-9a-f]{32}\Z")
_ERROR = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_SCOPE = frozenset(_PFT_CHAT_TOOLS)
_MODEL_SCOPE = _SCOPE - {"get_capabilities", "get_script_status"}
_MAX_TOOL_SCHEMA = 64 * 1024


def pft_chat_bridge_identity(status):
    if type(status) is not dict or status.get("running") is not True or status.get("stopping") is not False:
        return None
    identity = status.get("instance_routing")
    port = status.get("port")
    if (type(identity) is not dict or identity.get("version") != 1
            or type(port) is not int or not 1 <= port <= 65535):
        return None
    values = identity.get("instance_id"), identity.get("service_id")
    return (port, *values) if all(type(v) is str and _HEX.fullmatch(v) for v in values) else None


def _inventory(result):
    if type(result) is not dict or set(result) != {"tools"} or type(result["tools"]) is not list:
        raise ValueError()
    tools = result["tools"]
    if len(tools) != len(_SCOPE):
        raise ValueError()
    definitions, names = [], set()
    for row in tools:
        if type(row) is not dict:
            raise ValueError()
        name, description, schema = row.get("name"), row.get("description"), row.get("inputSchema")
        if (type(name) is not str or name not in _SCOPE or name in names or type(description) is not str
                or not 1 <= len(description) <= 8192 or type(schema) is not dict or schema.get("type") != "object"):
            raise ValueError()
        names.add(name)
        # The DeepSeek API describes tool calls; the local MCP still validates
        # the actual arguments, including refinements absent from JSON Schema.
        parameters = {key: value for key, value in schema.items() if key != "$schema"}
        if name not in _MODEL_SCOPE:
            continue
        definitions.append({"type": "function", "function": {"name": name, "description": description,
                                                               "parameters": parameters}})
    if len(json.dumps(definitions, ensure_ascii=False, allow_nan=False).encode("utf-8")) > _MAX_TOOL_SCHEMA:
        raise ValueError()
    return definitions


def _tool_output(name, value):
    if type(value) is not dict or type(value.get("structuredContent")) is not dict:
        return {"ok": False, "code": "MCP_RESPONSE_UNVERIFIED"}
    data = value["structuredContent"]
    if value.get("isError") is True:
        error = data.get("error")
        code = error.get("code") if type(error) is dict else None
        return {"ok": False, "code": code if type(code) is str and _ERROR.fullmatch(code) else "MCP_TOOL_FAILED"}
    if value.get("isError") not in (False, None) or data.get("ok") is not True:
        return {"ok": False, "code": "MCP_RESPONSE_UNVERIFIED"}
    if name == "get_connection_status":
        status = data.get("data")
        fields = ("running", "stopping", "protocol_version", "addon_version", "supported_methods",
                  "basic_part_create", "part_approvals", "pad_length_write", "drawing_create",
                  "drawing_dimensions", "drawing_dimension_layout", "drawing_export")
        if type(status) is not dict:
            return {"ok": False, "code": "MCP_RESPONSE_UNVERIFIED"}
        return {"ok": True, "data": {key: status[key] for key in fields if key in status}}
    return data


class PftChatMcp:
    def __init__(self, transport, config, bridge_status):
        if set(config) != {"node_executable", "cli_entry", "bridge_url"}:
            raise ValueError("invalid direct chat MCP configuration")
        self._transport, self._config, self._status = transport, config, bridge_status
        self._identity = pft_chat_bridge_identity(bridge_status())
        if self._identity is None or config["bridge_url"] != "http://127.0.0.1:%s/" % self._identity[0]:
            raise ValueError("unverified current window")
        self._generation = self._id = 0
        self._pending = self._starting = None
        self._ready = False

    @property
    def ready(self):
        return self._ready and self._same_window()

    def _same_window(self):
        try:
            return pft_chat_bridge_identity(self._status()) == self._identity
        except Exception:
            return False

    def start(self, ready):
        if self._ready or self._starting is not None:
            raise ValueError("MCP session already started")
        self._generation += 1
        generation = self._generation
        self._starting = ready

        def receive(message):
            if generation == self._generation:
                self._receive(message)

        def lost(*_args):
            if generation == self._generation:
                self._failed()

        if not self._same_window():
            self._failed()
            return
        # Process argv contains the verified local CLI path. Construct a new
        # environment from known fields, with neither provider data nor keys.
        environment = {key: os.environ[key] for key in ("SystemRoot", "SYSTEMROOT", "WINDIR") if key in os.environ}
        environment.update(_mcp_environment(self._config))
        cli = self._config["cli_entry"]
        try:
            self._transport.start(self._config["node_executable"], [cli], environment,
                                  str(Path(cli).parent), receive, lost, lost)
            if generation == self._generation:
                self._request("initialize", {"protocolVersion": _PROTOCOL, "capabilities": {},
                    "clientInfo": {"name": "phoenix_freecad_direct_chat", "version": "1"}}, self._initialized)
        except Exception:
            lost()

    def _write(self, value):
        self._transport.write((json.dumps({"jsonrpc": "2.0", **value}, ensure_ascii=False, allow_nan=False) + "\n").encode())

    def _request(self, method, params, callback):
        if self._pending is not None:
            raise ValueError("MCP request already pending")
        self._id += 1
        request_id = self._id
        self._pending = (request_id, callback)

        def timed_out():
            if self._pending is not None and self._pending[0] == request_id:
                self._failed()

        try:
            self._transport.arm_timeout(20000, timed_out)
            self._write({"id": request_id, "method": method, "params": params})
        except Exception:
            self._failed()

    def _receive(self, message):
        pending = self._pending
        try:
            if type(message) is not dict or message.get("jsonrpc") != "2.0":
                raise ValueError()
            if pending is None or type(message.get("id")) is not int or message["id"] != pending[0]:
                raise ValueError()
            if set(message) != {"jsonrpc", "id", "result"}:
                raise ValueError()
            self._transport.clear_timeout()
            self._pending = None
            pending[1](message["result"])
        except Exception:
            self._failed(pending)

    def _initialized(self, result):
        if (type(result) is not dict or result.get("protocolVersion") != _PROTOCOL
                or type(result.get("serverInfo")) is not dict or result["serverInfo"].get("name") != "phoenix-freecad-mcp"
                or type(result.get("capabilities")) is not dict or "tools" not in result["capabilities"]):
            raise ValueError()
        self._write({"method": "notifications/initialized"})
        self._request("tools/list", {}, self._listed)

    def _listed(self, result):
        definitions = _inventory(result)

        def checked(value):
            actual = value.get("structuredContent") if type(value) is dict else None
            status = actual.get("data") if type(actual) is dict and actual.get("ok") is True else None
            if pft_chat_bridge_identity(status) != self._identity or not self._same_window():
                raise ValueError()
            self._ready = True
            callback, self._starting = self._starting, None
            callback(definitions)

        self._request("tools/call", {"name": "get_connection_status", "arguments": {}}, checked)

    def call(self, name, arguments, completed):
        if (not self.ready or self._pending is not None or name not in _MODEL_SCOPE
                or type(arguments) is not dict):
            completed(None)
            return
        def received(value):
            completed(_tool_output(name, value) if value is not None and self.ready else None)
        self._request("tools/call", {"name": name, "arguments": arguments}, received)

    def _failed(self, consumed=None):
        pending, starting = self._pending or consumed, self._starting
        self.stop()
        if starting is not None:
            starting(None)
        elif pending is not None:
            pending[1](None)

    def stop(self):
        self._generation += 1
        self._pending = self._starting = None
        self._ready = False
        self._transport.stop()
