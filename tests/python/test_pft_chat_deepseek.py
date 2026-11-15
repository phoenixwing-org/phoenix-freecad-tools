# SPDX-License-Identifier: Apache-2.0
"""Direct DeepSeek protocol tests. All keys and responses are synthetic."""

import importlib.util
import json
from pathlib import Path
import unittest

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
spec = importlib.util.spec_from_file_location("pft_chat_deepseek_test", ADDON / "pft_chat_deepseek.py")
deepseek_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deepseek_module)
PftDeepSeekChat = deepseek_module.PftDeepSeekChat


class DeepSeekFixture:
    def __init__(self):
        self.requests = []
        self.cancellations = 0

    def request(self, url, key, payload, complete):
        self.requests.append((url, key, payload, complete))

    def cancel(self):
        self.cancellations += 1

    def reply(self, value, status=200, fault=None):
        self.requests[-1][3](status, json.dumps(value).encode(), fault)

    def models(self):
        self.reply({"object": "list", "data": [{"id": "deepseek-v4-pro"}, {"id": "deepseek-flash"}]})

    def answer(self, content, finish="stop"):
        self.reply({"model": "deepseek-flash", "choices": [{"finish_reason": finish,
            "message": {"role": "assistant", "content": content}}]})


class DirectChatTests(unittest.TestCase):
    def setUp(self):
        self.wire = DeepSeekFixture()
        self.snapshots = []
        self.chat = PftDeepSeekChat(self.wire, self.snapshots.append)
        self.key = "synthetic-test-key"

    def connect(self):
        self.chat.connect(self.key)
        self.wire.models()

    def test_connection_only_requests_models_then_conversation_uses_chosen_model_and_completed_turns(self):
        self.chat.connect(self.key)
        self.assertEqual(self.wire.requests[-1][:3], ("https://api.deepseek.com/models", self.key, None))
        self.assertEqual(self.chat.snapshot["phase"], "connecting")
        self.wire.models()
        self.assertTrue(self.chat.snapshot["can_send"])
        self.assertEqual(self.chat.snapshot["model"], "deepseek-flash")
        self.chat.select_model("deepseek-v4-pro")
        self.chat.send("first neutral prompt")
        url, key, payload, _ = self.wire.requests[-1]
        self.assertEqual((url, key), ("https://api.deepseek.com/chat/completions", self.key))
        self.assertEqual(payload["model"], "deepseek-v4-pro")
        self.assertNotIn(self.key, json.dumps(payload))
        self.wire.answer("first neutral reply")
        self.assertFalse(self.chat.snapshot["can_select_model"])
        self.chat.send("second neutral prompt")
        self.assertEqual(self.wire.requests[-1][2]["messages"][1:], [
            {"role": "user", "content": "first neutral prompt"},
            {"role": "assistant", "content": "first neutral reply"},
            {"role": "user", "content": "second neutral prompt"},
        ])
        self.assertNotIn(self.key, json.dumps(self.snapshots))
        self.assertEqual(len(self.wire.requests), 3)

    def test_stop_ignores_late_reply_even_after_reconnect_and_drops_interrupted_context(self):
        self.connect()
        self.chat.send("interrupted")
        late = self.wire.requests[-1][3]
        self.chat.interrupt()
        self.assertEqual(self.wire.cancellations, 1)
        self.assertTrue(self.chat.snapshot["can_send"])
        self.assertEqual(self.chat.snapshot["transcript"][-1]["status"], "interrupted")
        self.chat.send("next")
        self.assertEqual([m["content"] for m in self.wire.requests[-1][2]["messages"][1:]], ["next"])
        self.chat.disconnect()
        self.assertFalse(self.chat.snapshot["transcript"])
        self.assertIsNone(self.chat._key)
        self.chat.connect("other-synthetic-key")
        self.wire.models()
        old_state = self.chat.snapshot
        late(200, b'{"error":"late response"}')
        self.assertEqual(old_state, self.chat.snapshot)

    def test_bad_key_timeout_quota_and_echo_are_redacted_without_retry(self):
        self.chat.connect("header\r\ninjection")
        self.assertEqual(self.chat.snapshot["error"]["code"], "CHAT_DEEPSEEK_KEY")
        self.assertFalse(self.wire.requests)
        self.chat.connect(self.key)
        self.wire.reply({"error": self.key}, status=401)
        self.assertEqual(self.chat.snapshot["error"]["code"], "CHAT_DEEPSEEK_AUTH")
        self.assertIsNone(self.chat._key)
        self.chat.connect(self.key)
        self.wire.reply({}, status=0, fault="TIMEOUT")
        self.assertEqual(self.chat.snapshot["error"]["code"], "CHAT_DEEPSEEK_TIMEOUT")
        self.connect()
        self.chat.send("do not share " + self.key)
        self.assertEqual(self.chat.snapshot["error"]["code"], "CHAT_DEEPSEEK_INPUT")
        self.chat.send("neutral")
        self.wire.answer("echo " + self.key)
        self.assertEqual(self.chat.snapshot["error"]["code"], "CHAT_DEEPSEEK_RESPONSE")
        self.chat.send("neutral again")
        self.wire.reply({"error": self.key}, status=402)
        self.assertEqual(self.chat.snapshot["error"]["code"], "CHAT_DEEPSEEK_BALANCE")
        self.assertNotIn(self.key, json.dumps(self.snapshots))
        self.assertEqual(len(self.wire.requests), 5)

    def test_partial_answer_remains_visible_but_is_omitted_from_later_requests(self):
        self.connect()
        self.chat.send("first")
        self.wire.answer("incomplete", "length")
        self.assertEqual(self.chat.snapshot["error"]["code"], "CHAT_DEEPSEEK_PARTIAL")
        self.assertEqual(self.chat.snapshot["transcript"][-1]["text"], "incomplete")
        self.chat.send("second")
        self.assertEqual([m["content"] for m in self.wire.requests[-1][2]["messages"][1:]], ["second"])
