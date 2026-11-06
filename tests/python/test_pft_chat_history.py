# SPDX-License-Identifier: Apache-2.0
"""Hand-built neutral history pages; no actual account, chat, process or CAD."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
_previous_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_chat_history as history
finally:
    sys.path[:] = _previous_path


def turn(identifier="turn-neutral", status="completed", **extra):
    return {"id": identifier, "status": status, "items": [], "itemsView": "notLoaded", **extra}


def page(data=(), following=None, **extra):
    return {"data": list(data), "nextCursor": following, **extra}


def entry(turn_id="turn-neutral", identifier="item-neutral", kind="agentMessage", text="Neutral answer", **extra):
    item = {"id": identifier, "type": kind}
    if kind == "userMessage":
        item["content"] = [{"type": "text", "text": text}]
    elif kind == "agentMessage":
        item["text"] = text
    return {"turnId": turn_id, "item": item, **extra}


class ChatHistoryTests(unittest.TestCase):
    def setUp(self):
        self.history = history.PftChatHistory("thread-neutral")

    def load_turn(self, status="completed"):
        self.history.add_turn_page(page([turn(status=status)]))

    def error(self, suffix, operation):
        with self.assertRaises(history.PftChatHistoryError) as caught:
            operation()
        self.assertEqual(caught.exception.code, "CHAT_HISTORY_" + suffix)
        self.assertNotIn("synthetic suppressed", caught.exception.message)
        return caught.exception

    def test_empty_history_is_incomplete_until_explicit_terminal_turn_page(self):
        snapshot = self.history.snapshot()
        self.assertEqual(snapshot["transcript"], [])
        self.assertFalse(snapshot["complete"])
        self.assertTrue(snapshot["read_only"])
        self.assertIsNone(self.history.add_turn_page(page()))
        snapshot = self.history.snapshot()
        self.assertTrue(snapshot["complete"])
        self.assertFalse(snapshot["read_only"])
        self.assertEqual(snapshot["message_times"], [])

    def test_fixed_schema_optional_next_cursor_may_be_omitted_on_terminal_pages(self):
        # Codex 0.160.0 stable list responses require only data; nextCursor is
        # optional string|null for both turn and item responses.
        self.assertIsNone(self.history.add_turn_page({"data": [turn()]}))
        self.assertIsNone(self.history.add_item_page("turn-neutral", {"data": [entry()]}))
        self.assertTrue(self.history.snapshot()["complete"])

    def test_desc_turns_and_per_turn_asc_items_become_chronological_messages(self):
        self.assertEqual(self.history.add_turn_page(page([turn("turn-new")], "older-turn-page")), "older-turn-page")
        self.history.add_turn_page(page([turn("turn-old")]))
        self.history.add_item_page("turn-new", page([entry("turn-new", "new-user", "userMessage", "New request"),
                                                   entry("turn-new", "new-ai", text="New answer")]))
        self.assertEqual(self.history.add_item_page("turn-old", page([entry("turn-old", "old-user", "userMessage", "Old request")], "older-item-page")), "older-item-page")
        self.history.add_item_page("turn-old", page([entry("turn-old", "old-ai", text="Old answer")]))
        snapshot = self.history.snapshot()
        self.assertEqual([row["text"] for row in snapshot["transcript"]], ["Old request", "Old answer", "New request", "New answer"])
        self.assertEqual([row["id"] for row in snapshot["turns"]], ["turn-old", "turn-new"])
        self.assertTrue(all(row["history"] for row in snapshot["transcript"]))
        self.assertEqual(snapshot["message_times"], [""] * 4)
        self.assertTrue(snapshot["complete"])
        self.assertFalse(snapshot["read_only"])

    def test_identical_text_with_distinct_ids_or_turns_is_never_deduplicated(self):
        self.history.add_turn_page(page([turn("turn-new"), turn("turn-old")]))
        self.history.add_item_page("turn-old", page([entry("turn-old", "same-id", text="Repeated text"),
                                                   entry("turn-old", "another-id", text="Repeated text")]))
        self.history.add_item_page("turn-new", page([entry("turn-new", "same-id", text="Repeated text")]))
        self.assertEqual([row["text"] for row in self.history.snapshot()["transcript"]], ["Repeated text"] * 3)

    def test_identical_pages_are_idempotent_and_overlapping_anchor_items_do_not_repeat(self):
        turn_page = page([turn()])
        self.history.add_turn_page(turn_page)
        self.history.add_turn_page(copy.deepcopy(turn_page))
        first = page([entry(identifier="first")], "more")
        self.history.add_item_page("turn-neutral", first)
        before = self.history.snapshot()
        self.assertEqual(self.history.add_item_page("turn-neutral", copy.deepcopy(first)), "more")
        self.assertEqual(self.history.snapshot(), before)
        self.history.add_item_page("turn-neutral", page([entry(identifier="first"), entry(identifier="second")]))
        self.assertEqual([row["item_id"] for row in self.history.snapshot()["transcript"]], ["first", "second"])

    def test_text_projection_never_copies_tool_arguments_results_paths_or_unknown_fields(self):
        self.load_turn()
        user = entry(identifier="user", kind="userMessage", text="Neutral request")
        user["item"]["content"] += [{"type": "image", "url": "https://example.invalid/synthetic-suppressed-image"},
                                      {"type": "localImage", "path": "/fixture/synthetic-suppressed-image"},
                                      {"type": "skill", "name": "synthetic suppressed skill", "path": "/fixture/suppressed"}]
        user["item"]["clientId"] = "synthetic suppressed client"
        user["item"]["content"][0]["text_elements"] = [{"placeholder": "synthetic suppressed placeholder"}]
        assistant = entry(identifier="ai", text="Neutral answer")
        assistant["item"].update(questions=[{"text": "synthetic suppressed question"}], memoryCitation={"text": "synthetic suppressed citation"})
        hidden = []
        for index, kind in enumerate(("mcpToolCall", "functionCallOutput", "dynamicToolCall", "commandExecution", "fileChange", "reasoning", "imageView", "imageGeneration", "futureKind")):
            value = entry(identifier="hidden-" + str(index), kind=kind)
            value["item"].update(arguments={"path": "/fixture/synthetic-suppressed-argument"},
                result={"content": [{"type": "image", "data": "synthetic suppressed image"}],
                        "structuredContent": {"secret": "synthetic suppressed result"}},
                error={"message": "synthetic suppressed error"}, output="synthetic suppressed output", path="/fixture/suppressed", tool="synthetic suppressed tool")
            hidden.append(value)
        self.history.add_item_page("turn-neutral", page([user, assistant, *hidden]))
        snapshot = self.history.snapshot()
        self.assertEqual([row["text"] for row in snapshot["transcript"]], ["Neutral request", "Neutral answer"])
        self.assertEqual(snapshot["suppressed_items"], len(hidden))
        self.assertEqual(snapshot["suppressed_inputs"], 3)
        self.assertEqual(snapshot["tool_calls"], [])
        public = json.dumps(snapshot)
        self.assertNotIn("synthetic suppressed", public)
        self.assertNotIn("synthetic-suppressed", public)
        self.assertNotIn("/fixture", public)
        self.assertNotIn("example.invalid", public)

    def test_user_message_without_text_is_suppressed_and_empty_text_remains_valid(self):
        self.load_turn()
        hidden = entry(identifier="only-image", kind="userMessage")
        hidden["item"]["content"] = [{"type": "image", "fileId": "neutral-file"}]
        empty = entry(identifier="empty", kind="userMessage", text="")
        self.history.add_item_page("turn-neutral", page([hidden, empty]))
        snapshot = self.history.snapshot()
        self.assertEqual(len(snapshot["transcript"]), 1)
        self.assertEqual(snapshot["transcript"][0]["text"], "")
        self.assertEqual(snapshot["suppressed_items"], 1)

    def test_multiple_text_segments_preserve_boundaries_and_control_cleanup_is_flagged(self):
        self.load_turn()
        value = entry(kind="userMessage")
        value["item"]["content"] = [{"type": "text", "text": "A\x00B\u202e"}, {"type": "text", "text": "C\n\tD"}]
        self.history.add_item_page("turn-neutral", page([value]))
        row = self.history.snapshot()["transcript"][0]
        self.assertEqual(row["text"], "A B \nC\n\tD")
        self.assertTrue(row["sanitized"])

    def test_untrusted_markup_is_plain_text_to_be_escaped_by_the_renderer(self):
        self.load_turn()
        text = '<img src="file:///fixture/not-read"> **neutral** <script>neutral</script>'
        self.history.add_item_page("turn-neutral", page([entry(text=text)]))
        self.assertEqual(self.history.snapshot()["transcript"][0]["text"], text)

    def test_failed_and_interrupted_turn_outcomes_mark_only_the_last_assistant_and_never_raise_live_errors(self):
        for status, expected in (("failed", "error"), ("interrupted", "interrupted")):
            with self.subTest(status=status):
                self.setUp()
                self.load_turn(status)
                self.history.add_item_page("turn-neutral", page([entry(identifier="user", kind="userMessage"),
                    entry(identifier="commentary", text="Checking neutral data"), entry(identifier="answer", text="Partial neutral answer")]))
                snapshot = self.history.snapshot()
                self.assertEqual([row["status"] for row in snapshot["transcript"]], ["completed", "completed", expected])
                self.assertIsNone(snapshot["error"])
                self.assertTrue(snapshot["complete"])
                self.assertFalse(snapshot["read_only"])

    def test_failed_turn_without_assistant_text_keeps_outcome_without_fabricating_reply(self):
        self.load_turn("failed")
        self.history.add_item_page("turn-neutral", page([entry(kind="userMessage", text="Neutral request")]))
        snapshot = self.history.snapshot()
        self.assertEqual(len(snapshot["transcript"]), 1)
        self.assertEqual(snapshot["transcript"][0]["role"], "user")
        self.assertEqual(snapshot["turns"][0]["status"], "failed")

    def test_in_progress_and_unknown_status_are_read_only_without_claiming_stopped_or_completed(self):
        for status in ("inProgress", "future-status"):
            with self.subTest(status=status):
                self.setUp()
                self.load_turn(status)
                self.history.add_item_page("turn-neutral", page([entry()]))
                snapshot = self.history.snapshot()
                self.assertTrue(snapshot["read_only"])
                self.assertEqual(snapshot["transcript"][0]["status"], "unknown")
                self.assertEqual(snapshot["has_in_progress"], status == "inProgress")
                self.assertEqual(snapshot["has_unknown"], status != "inProgress")

    def test_real_item_millisecond_times_are_explicit_utc_and_no_turn_or_thread_time_is_invented(self):
        self.history.add_turn_page(page([turn(startedAt=1730000000, completedAt=1730001000, durationMs=1000000)]))
        self.history.add_item_page("turn-neutral", page([
            entry(identifier="first", startedAtMs=0, completedAtMs=1000),
            entry(identifier="second", startedAtMs=None, completedAtMs=3600000),
            entry(identifier="missing"), entry(identifier="null", startedAtMs=None, completedAtMs=None)]))
        snapshot = self.history.snapshot()
        self.assertEqual(snapshot["message_times"], ["00:00 UTC", "01:00 UTC", "", ""])
        self.assertEqual([row["time_source"] for row in snapshot["transcript"]], ["startedAtMs", "completedAtMs", None, None])

    def test_bad_item_timestamps_are_protocol_errors_not_replaced_with_current_time(self):
        for value in (True, 1.0, "1000", 2 ** 63, 2 ** 63 - 1):
            with self.subTest(value_type=type(value).__name__):
                self.setUp(); self.load_turn()
                self.error("INVALID", lambda: self.history.add_item_page("turn-neutral", page([entry(startedAtMs=value)])))
                self.assertEqual(self.history.snapshot()["message_times"], [])
        self.setUp(); self.load_turn()
        self.error("INVALID", lambda: self.history.add_item_page("turn-neutral", page([entry(startedAtMs=20, completedAtMs=10)])))

    def test_wrong_turn_entry_unknown_turn_and_bare_item_entries_are_rejected_atomically(self):
        self.load_turn()
        for value, suffix in ((entry(turn_id="other-turn"), "SCOPE"), (entry()["item"], "INVALID")):
            with self.subTest(suffix=suffix):
                self.setUp(); self.load_turn()
                self.error(suffix, lambda: self.history.add_item_page("turn-neutral", page([entry(identifier="valid-first"), value])))
                self.assertEqual(self.history.snapshot()["transcript"], [])
        self.setUp(); self.load_turn()
        self.error("SCOPE", lambda: self.history.add_item_page("not-loaded", page()))

    def test_changed_body_kind_phase_or_recorded_time_conflicts_without_overwriting(self):
        for mutate in (lambda e: e["item"].update(text="Contradicting neutral answer"),
                       lambda e: e["item"].update(type="futureKind"),
                       lambda e: e["item"].update(phase="final_answer"),
                       lambda e: e.update(startedAtMs=2000)):
            self.setUp(); self.load_turn()
            original = entry(startedAtMs=1000)
            self.history.add_item_page("turn-neutral", page([original]))
            before = self.history.snapshot()["transcript"]
            changed = copy.deepcopy(original); mutate(changed)
            self.error("CONFLICT", lambda: self.history.add_item_page("turn-neutral", page([changed])))
            snapshot = self.history.snapshot()
            self.assertEqual(snapshot["transcript"], before)
            self.assertTrue(snapshot["read_only"])
            self.assertFalse(snapshot["complete"])
            self.error("STATE_INVALID", lambda: self.history.add_turn_page(page()))

    def test_cleaned_identical_text_does_not_hide_contradicting_raw_snapshots(self):
        self.load_turn()
        self.history.add_item_page("turn-neutral", page([entry(text="A\x00B")]))
        self.error("CONFLICT", lambda: self.history.add_item_page("turn-neutral", page([entry(text="A\x01B")])))
        self.assertEqual(self.history.snapshot()["transcript"][0]["text"], "A B")

    def test_terminal_status_cannot_regress_or_change_terminal_kind(self):
        for status in ("inProgress", "failed", "interrupted"):
            with self.subTest(status=status):
                self.setUp(); self.load_turn()
                self.error("CONFLICT", lambda: self.history.add_turn_page(page([turn(status=status)])))
                self.assertEqual(self.history.snapshot()["turns"][0]["status"], "completed")
        self.setUp(); self.load_turn("inProgress")
        self.history.add_turn_page(page([turn(status="completed")]))
        self.assertFalse(self.history.snapshot()["has_in_progress"])

    def test_active_item_snapshots_cannot_become_final_via_metadata_only_transition(self):
        for status in ("completed", "failed", "interrupted"):
            for values in ([], [entry(text="Partial neutral text")]):
                with self.subTest(status=status, empty=not values):
                    self.setUp(); self.load_turn("inProgress")
                    self.history.add_item_page("turn-neutral", page(values))
                    before = self.history.snapshot()
                    self.assertTrue(before["complete"])
                    self.assertTrue(before["read_only"])
                    self.error("CONFLICT", lambda: self.history.add_turn_page(page([turn(status=status)])))
                    after = self.history.snapshot()
                    self.assertEqual(after["turns"], before["turns"])
                    self.assertEqual(after["transcript"], before["transcript"])
                    self.assertTrue(after["read_only"])
                    self.assertFalse(after["complete"])

    def test_previously_cached_in_progress_page_cannot_hide_a_terminal_regression(self):
        self.load_turn("inProgress")
        self.history.add_turn_page(page([turn(status="completed")]))
        self.error("CONFLICT", lambda: self.history.add_turn_page(page([turn(status="inProgress")])))
        self.assertEqual(self.history.snapshot()["turns"][0]["status"], "completed")

    def test_completion_markers_cannot_reopen_a_finished_page_stream(self):
        self.load_turn()
        self.error("CONFLICT", lambda: self.history.add_turn_page(page([turn()], "new-cursor")))
        self.setUp(); self.load_turn()
        self.history.add_item_page("turn-neutral", page([entry()]))
        self.error("CONFLICT", lambda: self.history.add_item_page("turn-neutral", page([entry()], "new-cursor")))

    def test_snapshot_time_metadata_may_fill_unknown_but_never_erase_known_values(self):
        self.load_turn()
        self.history.add_item_page("turn-neutral", page([entry()]))
        self.history.add_item_page("turn-neutral", page([entry(startedAtMs=0)]))
        self.history.add_item_page("turn-neutral", page([entry()]))
        self.assertEqual(self.history.snapshot()["message_times"], ["00:00 UTC"])

    def test_completed_pagination_cannot_silently_accept_new_items_or_turns(self):
        self.load_turn()
        self.history.add_item_page("turn-neutral", page([entry(identifier="first")]))
        self.error("CONFLICT", lambda: self.history.add_item_page("turn-neutral", page([entry(identifier="new")])) )
        self.setUp(); self.load_turn()
        self.error("CONFLICT", lambda: self.history.add_turn_page(page([turn("new-turn")])))

    def test_page_order_contradictions_are_not_sorted_using_ids_or_timestamps(self):
        self.history.add_turn_page(page([turn("turn-c"), turn("turn-b")], "next"))
        self.error("CONFLICT", lambda: self.history.add_turn_page(page([turn("turn-b"), turn("turn-c")])) )
        self.setUp(); self.load_turn()
        self.history.add_item_page("turn-neutral", page([entry(identifier="old")], "next"))
        self.error("CONFLICT", lambda: self.history.add_item_page("turn-neutral", page([entry(identifier="new"), entry(identifier="old")])) )

    def test_page_cursor_shapes_empty_continuation_and_invalid_turn_items_view_are_rejected(self):
        for response in (page([], "next"), page([], True), page([], {"type": "item", "itemId": "neutral"}),
                         {"data": (), "nextCursor": None}, page([turn(itemsView="future")]),
                         page([turn(items=[entry()["item"]])])):
            self.setUp()
            self.error("INVALID", lambda: self.history.add_turn_page(response))

    def test_page_size_and_total_page_bounds_stop_without_a_partial_page(self):
        self.history.add_turn_page(page([turn()], "more"))
        self.history.add_turn_page(page([turn("turn-" + str(i)) for i in range(21)]))
        snapshot = self.history.snapshot()
        self.assertEqual([row["id"] for row in snapshot["turns"]], ["turn-neutral"])
        self.assertTrue(snapshot["truncated"])
        self.assertEqual(snapshot["limit_reason"], "page_size")
        self.setUp()
        with patch.object(history, "PFT_HISTORY_MAX_PAGES", 2):
            self.load_turn()
            self.history.add_item_page("turn-neutral", page([entry(identifier="first")], "more"))
            self.history.add_item_page("turn-neutral", page([entry(identifier="second")]))
        snapshot = self.history.snapshot()
        self.assertEqual([row["item_id"] for row in snapshot["transcript"]], ["first"])
        self.assertEqual(snapshot["limit_reason"], "page_limit")
        self.assertTrue(snapshot["read_only"])

    def test_turn_row_and_utf8_budgets_reject_overflowing_whole_page(self):
        with patch.object(history, "PFT_HISTORY_MAX_TURNS", 1):
            self.history.add_turn_page(page([turn("turn-a")], "more"))
            self.history.add_turn_page(page([turn("turn-b")]))
        self.assertEqual(len(self.history.snapshot()["turns"]), 1)
        self.assertEqual(self.history.snapshot()["limit_reason"], "turn_limit")
        self.setUp(); self.load_turn()
        with patch.object(history, "PFT_HISTORY_MAX_ROWS", 1):
            self.history.add_item_page("turn-neutral", page([entry(identifier="first")], "more"))
            self.history.add_item_page("turn-neutral", page([entry(identifier="second"), entry(identifier="third")]))
        self.assertEqual([r["item_id"] for r in self.history.snapshot()["transcript"]], ["first"])
        self.assertEqual(self.history.snapshot()["limit_reason"], "row_limit")
        self.setUp(); self.load_turn()
        with patch.object(history, "PFT_HISTORY_MAX_TEXT_BYTES", 6):
            self.history.add_item_page("turn-neutral", page([entry(identifier="first", text="示示")], "more"))
            self.history.add_item_page("turn-neutral", page([entry(identifier="second", text="a")]))
        self.assertEqual([r["text"] for r in self.history.snapshot()["transcript"]], ["示示"])
        self.assertEqual(self.history.snapshot()["limit_reason"], "utf8_budget")

    def test_actual_hundred_turn_limit_keeps_all_previous_turns(self):
        for offset in range(0, 100, 20):
            self.history.add_turn_page(page([turn("turn-%03d" % index) for index in range(offset, offset + 20)], "page-%d" % offset))
        self.assertEqual(len(self.history.snapshot()["turns"]), 100)
        self.history.add_turn_page(page([turn("turn-overflow")]))
        snapshot = self.history.snapshot()
        self.assertEqual(len(snapshot["turns"]), 100)
        self.assertEqual(snapshot["limit_reason"], "turn_limit")

    def test_actual_row_and_utf8_limits_accept_the_boundary_then_stop_next_page(self):
        self.load_turn()
        for offset in range(0, 256, 20):
            self.history.add_item_page("turn-neutral", page([
                entry(identifier="row-%03d" % index, text="") for index in range(offset, min(offset + 20, 256))], "page-%d" % offset))
        self.assertEqual(len(self.history.snapshot()["transcript"]), 256)
        self.history.add_item_page("turn-neutral", page([entry(identifier="overflow-row", text="")]))
        self.assertEqual(len(self.history.snapshot()["transcript"]), 256)
        self.assertEqual(self.history.snapshot()["limit_reason"], "row_limit")
        self.setUp(); self.load_turn()
        exact = "示" * 87381 + "x"
        self.assertEqual(len(exact.encode("utf-8")), 256 * 1024)
        self.history.add_item_page("turn-neutral", page([entry(identifier="exact", text=exact)], "more"))
        self.history.add_item_page("turn-neutral", page([entry(identifier="extra", text="y")]))
        snapshot = self.history.snapshot()
        self.assertEqual([row["text"] for row in snapshot["transcript"]], [exact])
        self.assertEqual(snapshot["limit_reason"], "utf8_budget")

    def test_actual_twenty_page_limit_is_shared_between_turn_and_item_pages(self):
        self.load_turn()
        for index in range(19):
            self.history.add_item_page("turn-neutral", page([entry(identifier="row-%d" % index)], "cursor-%d" % index))
        self.assertEqual(self.history.snapshot()["pages"], 20)
        self.history.add_item_page("turn-neutral", page([entry(identifier="overflow")]))
        snapshot = self.history.snapshot()
        self.assertEqual(snapshot["limit_reason"], "page_limit")
        self.assertEqual(len(snapshot["transcript"]), 19)

    def test_one_oversized_text_stops_before_publishing_any_prefix(self):
        self.load_turn()
        self.history.add_item_page("turn-neutral", page([entry(text="x" * (history.PFT_HISTORY_MAX_TEXT_BYTES + 1))]))
        snapshot = self.history.snapshot()
        self.assertEqual(snapshot["transcript"], [])
        self.assertTrue(snapshot["truncated"])
        self.assertEqual(snapshot["limit_reason"], "utf8_budget")

    def test_raw_input_limits_reject_nested_huge_or_invalid_objects_without_stringifying(self):
        class Opaque:
            def __str__(self): raise AssertionError("must not stringify")
            def __repr__(self): raise AssertionError("must not represent")
        nested = []
        for _ in range(70): nested = [nested]
        for bad, suffix in ((page([turn()], ignored=Opaque()), "INVALID"),
                            (page([turn()], ignored=nested), "LIMIT"),
                            (page([turn()], ignored=[None] * 50001), "LIMIT"),
                            (page([turn()], ignored="x" * (history.PFT_HISTORY_MAX_INPUT_BYTES + 1)), "LIMIT"),
                            (page([turn()], ignored=float("nan")), "INVALID"),
                            (page([turn()], ignored="\ud800"), "INVALID")):
            self.setUp()
            self.error(suffix, lambda: self.history.add_turn_page(bad))
            self.assertEqual(self.history.snapshot()["turns"], [])

    def test_raw_string_budget_counts_json_escapes_without_serializing_payload(self):
        with patch.object(history, "PFT_HISTORY_MAX_INPUT_BYTES", 100):
            self.error("LIMIT", lambda: self.history.add_turn_page({"data": [], "ignored": "\\" * 60}))
        self.assertEqual(self.history.snapshot()["turns"], [])

    def test_custom_containers_and_invalid_identifiers_are_not_interpreted(self):
        class CustomDict(dict):
            def items(self): raise AssertionError("must not iterate custom dict")
        class CustomList(list):
            def __iter__(self): raise AssertionError("must not iterate custom list")
        for response in (CustomDict(data=[]), {"data": CustomList()}, page([turn(identifier="\u202e")]), page([turn(identifier="x" * 257)])):
            self.setUp()
            self.error("INVALID", lambda: self.history.add_turn_page(response))
        for identifier in (None, True, "", " neutral ", "x" * 257):
            self.error("INVALID", lambda: history.PftChatHistory(identifier))
        with self.assertRaises(AttributeError):
            self.history.thread_id = "other-thread"

    def test_frozen_output_does_not_alias_input_or_prior_snapshots(self):
        self.load_turn()
        response = page([entry(text="Original neutral answer")])
        self.history.add_item_page("turn-neutral", response)
        before = self.history.snapshot()
        response["data"][0]["item"]["text"] = "Input changed"
        before["transcript"][0]["text"] = "Output changed"
        before["turns"][0]["status"] = "failed"
        current = self.history.snapshot()
        self.assertEqual(current["transcript"][0]["text"], "Original neutral answer")
        self.assertEqual(current["turns"][0]["status"], "completed")
        json.dumps(current, allow_nan=False)

    def test_projection_never_opens_files_launches_processes_uses_clock_or_calls_rpc(self):
        self.load_turn()
        with patch("builtins.open", side_effect=AssertionError("no filesystem")), \
             patch("time.time", side_effect=AssertionError("no now")), \
             patch("time.monotonic", side_effect=AssertionError("no elapsed clock")):
            self.history.add_item_page("turn-neutral", page([entry(startedAtMs=0)]))
            self.assertEqual(self.history.snapshot()["message_times"], ["00:00 UTC"])


if __name__ == "__main__":
    unittest.main()
