# SPDX-License-Identifier: Apache-2.0
"""Tool-mode lifecycle and trust boundaries. No real credentials or cloud I/O."""

import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from test_pft_chat_controller import chat as codex
from test_pft_chat_deepseek import DeepSeekFixture, PftDeepSeekChat
from test_pft_chat_qprocess import transport_module

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
SPEC = importlib.util.spec_from_file_location("pft_chat_mcp_test", ADDON / "pft_chat_mcp.py")
mcp_module = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"pft_chat_controller": codex}):
    SPEC.loader.exec_module(mcp_module)


def bridge(service="b"):
    return {"running": True, "stopping": False, "port": 11111, "basic_part_create": {"operation": "create_basic_part"},
            "instance_routing": {"version": 1, "instance_id": "a" * 32, "service_id": service * 32}}


def success(data):
    return {"structuredContent": {"ok": True, "data": data}}


def provider_call(name, arguments="{}", ident="neutral-call"):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": arguments}}


class Stdio:
    def __init__(self):
        self.sent = []
        self.starts = []
        self.stops = 0

    def start(self, program, args, env, cwd, receive, lost, failed):
        self.starts.append((program, args, env, cwd))
        self.receive = receive

    def arm_timeout(self, delay, failed):
        self.timeout = failed

    def clear_timeout(self):
        self.timeout = None

    def write(self, data):
        self.sent.append(json.loads(data))

    def reply(self, value):
        self.receive({"jsonrpc": "2.0", "id": self.sent[-1]["id"], "result": value})

    def stop(self):
        self.stops += 1


class DirectChatToolTests(unittest.TestCase):
    def setUp(self):
        modules = patch.dict(sys.modules, {"pft_chat_controller": codex, "pft_chat_qprocess": transport_module})
        modules.start()
        self.addCleanup(modules.stop)
        self.status = bridge()
        self.stdio, self.http = Stdio(), DeepSeekFixture()
        self.chat = PftDeepSeekChat(self.http, lambda _: None)
        self.mcp = mcp_module.PftChatMcp(self.stdio, {"node_executable": "/fixture/node",
            "cli_entry": "/fixture/dist/cli.js", "bridge_url": "http://127.0.0.1:11111/"}, lambda: self.status)
        self.key = 'synthetic-"quoted"-key'

    def inventory(self, names=codex._PFT_CHAT_TOOLS):
        self.stdio.reply({"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "phoenix-freecad-mcp"}})
        self.assertEqual([row["method"] for row in self.stdio.sent], ["initialize", "notifications/initialized", "tools/list"])
        self.stdio.reply({"tools": [{"name": name, "description": "Synthetic tool",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}
            for name in names]})

    def connect(self):
        self.chat.connect(self.key, mcp=self.mcp)
        self.assertFalse(self.http.requests)
        self.inventory()
        self.stdio.reply(success(self.status))
        self.http.models()
        self.assertTrue(self.chat.snapshot["can_send"])
        self.assertNotIn(self.key, str(self.stdio.starts))
        self.assertEqual(self.stdio.starts[0][2]["PHOENIX_FREECAD_TOOL_SCOPE"], "chat")

    def calls(self, *tools):
        self.http.reply({"model": "deepseek-flash", "choices": [{"finish_reason": "tool_calls",
            "message": {"role": "assistant", "content": None, "tool_calls": list(tools)}}]})

    def test_bad_inventory_or_wrong_instance_never_contacts_provider(self):
        self.chat.connect(self.key, mcp=self.mcp)
        self.inventory([*codex._PFT_CHAT_TOOLS[:-1], "execute_script"])
        self.assertEqual(self.chat.snapshot["phase"], "error")
        self.assertFalse(self.http.requests)
        self.stdio.sent.clear()
        self.chat.connect(self.key, mcp=self.mcp)
        self.inventory()
        self.stdio.reply(success(bridge("c")))
        self.assertEqual(self.chat.snapshot["phase"], "error")
        self.assertFalse(self.http.requests)

    def test_native_approval_response_pauses_tool_calls_until_user_followup(self):
        self.connect()
        self.chat.send("Make a neutral fixture")
        payload = self.http.requests[-1][2]
        self.assertNotIn("apply_part_changes", [row["function"]["name"] for row in payload["tools"]])
        self.calls(provider_call("request_part_change_approval", '{"token":"fixture-token"}'))
        self.assertEqual(self.stdio.sent[-1]["params"]["name"], "request_part_change_approval")
        self.stdio.reply(success({"state": "awaiting_confirmation"}))
        next_payload = self.http.requests[-1][2]
        self.assertEqual(next_payload["tool_choice"], "none")
        self.assertEqual(next_payload["messages"][-1]["role"], "tool")
        self.assertEqual(next_payload["messages"][-1]["tool_call_id"], "neutral-call")
        count = len(self.stdio.sent)
        self.calls(provider_call("get_part_change_status", '{"token":"fixture-token"}'))
        self.assertEqual(len(self.stdio.sent), count)
        self.assertEqual(self.chat.snapshot["error"]["code"], "CHAT_DEEPSEEK_RESPONSE")

    def test_drops_invalid_argument_batches_and_verifies_identity_before_each_dispatch(self):
        self.connect()
        self.chat.send("Fixture read")
        count = len(self.stdio.sent)
        self.calls(provider_call("list_documents"), provider_call("apply_part_changes", ident="invalid"))
        self.assertEqual(len(self.stdio.sent), count)
        self.chat.send("Fixture read")
        self.calls(provider_call("list_documents", '{"x":0,"x":1}'))
        self.assertEqual(len(self.stdio.sent), count)
        self.chat.send("Fixture read")
        self.calls(provider_call("list_documents"), provider_call("list_documents", ident="duplicate"))
        self.assertEqual(len(self.stdio.sent), count)
        self.chat.send("Fixture read")
        self.calls(provider_call("list_documents", json.dumps({"x": self.key})))
        self.assertEqual(len(self.stdio.sent), count)
        self.chat.send("Fixture read")
        self.status = bridge("c")
        self.calls(provider_call("list_documents"))
        self.assertEqual(len(self.stdio.sent), count)
        self.assertEqual(self.chat.snapshot["phase"], "error")
        self.assertIsNone(self.chat._key)

    def test_tool_timeout_ignores_late_reply_and_does_not_retry(self):
        self.connect()
        self.chat.send("Fixture read")
        self.calls(provider_call("list_documents"))
        count = len(self.http.requests)
        self.stdio.timeout()
        self.stdio.reply(success(["LateFixture"]))
        self.assertEqual(self.chat.snapshot["phase"], "error")
        self.assertEqual(len(self.http.requests), count)
        self.assertNotIn("LateFixture", str(self.chat.snapshot))
