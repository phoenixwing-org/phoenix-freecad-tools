# SPDX-License-Identifier: Apache-2.0
"""Bounded text-only history projection for the verified Codex protocols.

Pure Python: no Qt, filesystem, RPC, credentials, clock reads or live-turn
callbacks. The caller binds responses to its connection generation and exact
request/cursor, requests turns descending with itemsView=notLoaded, then items
ascending for each returned turn. This reducer never fetches or replays work.
"""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import unicodedata

PFT_HISTORY_PAGE_ITEMS = 20
PFT_HISTORY_MAX_TURNS = 100
PFT_HISTORY_MAX_ROWS = 256
PFT_HISTORY_MAX_TEXT_BYTES = 256 * 1024
PFT_HISTORY_MAX_PAGES = 20
PFT_HISTORY_MAX_INPUT_BYTES = 1280 * 1024
_PFT_MAX_VALUES = 50000
_PFT_MAX_DEPTH = 64
_TERMINAL = frozenset(("completed", "failed", "interrupted"))
_MESSAGES = {
    "CHAT_HISTORY_INVALID": "聊天历史格式不兼容，未应用这一页。",
    "CHAT_HISTORY_SCOPE": "聊天历史目标不一致，未应用这一页。",
    "CHAT_HISTORY_CONFLICT": "聊天历史快照冲突，未覆盖已有内容；请重新检查。",
    "CHAT_HISTORY_LIMIT": "聊天历史超过读取上限，未应用这一页。",
    "CHAT_HISTORY_STATE_INVALID": "聊天历史读取已停止；请重新检查会话。",
}


class PftChatHistoryError(RuntimeError):
    def __init__(self, code):
        self.code, self.message = code, _MESSAGES[code]
        super().__init__(self.message)


def _fail(suffix):
    raise PftChatHistoryError("CHAT_HISTORY_" + suffix) from None


def _string(value, maximum, *, identifier=False):
    if type(value) is not str or len(value) > maximum:
        _fail("INVALID")
    try:
        raw = value.encode("utf-8", errors="strict")
    except UnicodeError:
        _fail("INVALID")
    if len(raw) > maximum:
        _fail("INVALID")
    if identifier and (not value or value != value.strip() or
            any(unicodedata.category(c) in ("Cc", "Cf") for c in value)):
        _fail("INVALID")
    return value


def _bounded_wire(value):
    """Bound plain JSON input without serializing or retaining tool payloads."""
    stack, nodes, size = [(value, 0)], 0, 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > _PFT_MAX_VALUES or depth > _PFT_MAX_DEPTH:
            _fail("LIMIT")
        kind = type(current)
        if kind is str:
            if len(current) > PFT_HISTORY_MAX_INPUT_BYTES - size:
                _fail("LIMIT")
            try:
                size += 2 + len(current.encode("utf-8", errors="strict"))
                size += sum(1 if c in '"\\\b\f\n\r\t' else 5 if ord(c) < 32 else 0 for c in current)
            except UnicodeError:
                _fail("INVALID")
        elif kind is dict:
            if 2 * len(current) > _PFT_MAX_VALUES - nodes - len(stack):
                _fail("LIMIT")
            size += 2 + 2 * len(current)
            for key, item in current.items():
                if type(key) is not str:
                    _fail("INVALID")
                stack.extend(((key, depth + 1), (item, depth + 1)))
        elif kind is list:
            if len(current) > _PFT_MAX_VALUES - nodes - len(stack):
                _fail("LIMIT")
            size += 2 + len(current)
            stack.extend((item, depth + 1) for item in current)
        elif current is None or kind is bool:
            size += 5
        elif kind is int:
            if current.bit_length() > 4096:
                _fail("LIMIT")
            size += (current.bit_length() * 30103 // 100000) + 3
        elif kind is float:
            if not math.isfinite(current):
                _fail("INVALID")
            size += 32
        else:
            _fail("INVALID")
        if size > PFT_HISTORY_MAX_INPUT_BYTES:
            _fail("LIMIT")


def _cursor(value):
    return None if value is None else _string(value, 1024, identifier=True)


def _page(response):
    _bounded_wire(response)
    if type(response) is not dict or type(response.get("data")) is not list:
        _fail("INVALID")
    data, following = response["data"], _cursor(response.get("nextCursor"))
    _cursor(response.get("backwardsCursor"))
    if not data and following is not None:
        _fail("INVALID")
    return data, following


def _timestamp(value):
    if value is None:
        return None
    if type(value) is not int or not -(2 ** 63) <= value <= 2 ** 63 - 1:
        _fail("INVALID")
    try:
        # Integer arithmetic keeps the recorded millisecond value exact.
        datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=value)
    except (OverflowError, ValueError):
        _fail("INVALID")
    return value


def _time_text(started, completed):
    value = started if started is not None else completed
    if value is None:
        return "", None
    moment = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=value)
    return ("%02d:%02d UTC" % (moment.hour, moment.minute),
            "startedAtMs" if started is not None else "completedAtMs")


def _turn(raw):
    if type(raw) is not dict or type(raw.get("items")) is not list:
        _fail("INVALID")
    identifier = _string(raw.get("id"), 256, identifier=True)
    status = _string(raw.get("status"), 128, identifier=True)
    status = status if status in _TERMINAL or status == "inProgress" else "unknown"
    if raw.get("itemsView", "full") not in ("notLoaded", "summary", "full"):
        _fail("INVALID")
    if raw.get("itemsView") == "notLoaded" and raw["items"]:
        _fail("INVALID")
    # Seconds-based Turn times/error/items are deliberately not used as item
    # timestamps or text. Only the metadata contract is projected here.
    return {"id": identifier, "status": status}


def _clean_text(parts):
    digest, clean, sanitized = hashlib.sha256(), [], False
    for text in parts:
        _string(text, PFT_HISTORY_MAX_INPUT_BYTES)
        raw = text.encode("utf-8")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
        value = "".join(c if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cf") else " "
                        for c in text)
        sanitized = sanitized or value != text
        clean.append(value)
    return "\n".join(clean), digest.hexdigest(), sanitized


def _item(turn_id, entry):
    if type(entry) is not dict:
        _fail("INVALID")
    if _string(entry.get("turnId"), 256, identifier=True) != turn_id:
        _fail("SCOPE")
    raw = entry.get("item")
    if type(raw) is not dict:
        _fail("INVALID")
    identifier = _string(raw.get("id"), 256, identifier=True)
    kind = _string(raw.get("type"), 128, identifier=True)
    started, completed = _timestamp(entry.get("startedAtMs")), _timestamp(entry.get("completedAtMs"))
    if started is not None and completed is not None and completed < started:
        _fail("INVALID")
    result = {"id": identifier, "kind": kind, "role": None, "text": None,
              "digest": None, "sanitized": False, "phase": None,
              "started": started, "completed": completed, "suppressed_inputs": 0}
    if kind == "userMessage":
        content = raw.get("content")
        if type(content) is not list or len(content) > 256:
            _fail("INVALID")
        parts = []
        for piece in content:
            if type(piece) is not dict or type(piece.get("type")) is not str:
                _fail("INVALID")
            if piece["type"] == "text":
                if type(piece.get("text")) is not str:
                    _fail("INVALID")
                parts.append(piece["text"])
            else:
                result["suppressed_inputs"] += 1
        if parts:
            result["role"] = "user"
            result["text"], result["digest"], result["sanitized"] = _clean_text(parts)
    elif kind == "agentMessage":
        if raw.get("phase") not in (None, "commentary", "final_answer"):
            _fail("INVALID")
        result["role"], result["phase"] = "assistant", raw.get("phase")
        result["text"], result["digest"], result["sanitized"] = _clean_text([raw.get("text")])
    # Everything else, including tool arguments/result/error/URI/path/image,
    # remains outside the retained projection. No raw object is stored.
    return result


def _merge_order(existing, identifiers):
    """Continue one fixed-direction stream; do not guess a new order from IDs."""
    result, positions, seen = list(existing), {value: i for i, value in enumerate(existing)}, set()
    new_seen, last = False, -1
    for identifier in identifiers:
        if identifier in seen:
            continue
        seen.add(identifier)
        if identifier in positions:
            if new_seen or positions[identifier] < last:
                _fail("CONFLICT")
            last = positions[identifier]
        else:
            new_seen = True
            result.append(identifier)
    return result


def _merge_item(previous, incoming):
    if any(previous[key] != incoming[key] for key in (
            "kind", "role", "digest", "phase", "suppressed_inputs")):
        _fail("CONFLICT")
    result = dict(previous)
    for key in ("started", "completed"):
        if result[key] is not None and incoming[key] is not None and result[key] != incoming[key]:
            _fail("CONFLICT")
        if result[key] is None:
            result[key] = incoming[key]
    if result["started"] is not None and result["completed"] is not None and result["completed"] < result["started"]:
        _fail("CONFLICT")
    return result


class PftChatHistory:
    """Text-only display state; it never grants permission to continue a thread.

    Duplicate identical snapshots are idempotent. Contradictions stop this
    reducer without replacing prior content. A display/page budget stops before
    applying the whole overflowing page; snapshot.truncated makes this explicit.
    Controller owns request generation, cursor-cycle checks and policy rechecks.
    """
    def __init__(self, thread_id):
        self._thread_id = _string(thread_id, 256, identifier=True)
        self._turns, self._order, self._items, self._item_orders = {}, [], {}, {}
        self._items_complete, self._turns_complete = {}, False
        self._pages, self._seen_pages = 0, set()
        self._truncated, self._limit_reason, self._error = False, None, None

    @property
    def thread_id(self):
        return self._thread_id

    def _active(self):
        if self._error is not None:
            _fail("STATE_INVALID")
        return not self._truncated

    def _failed(self, error):
        self._error = {"code": error.code, "message": error.message}
        if error.code == "CHAT_HISTORY_LIMIT":
            self._truncated, self._limit_reason = True, "input_budget"

    def _limit(self, reason):
        self._truncated, self._limit_reason = True, reason
        return None

    def _page_key(self, kind, turn_id, data, following):
        # Serialize only our bounded primitive projection, never a raw payload.
        body = {"kind": kind, "turn": turn_id, "data": data, "next": following}
        return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode("utf-8")).digest()

    def _within_display_budget(self, turns, items):
        if len(turns) > PFT_HISTORY_MAX_TURNS:
            return "turn_limit"
        rows, byte_count = 0, 0
        for turn_items in items.values():
            for item in turn_items.values():
                if item["text"] is not None:
                    rows += 1
                    byte_count += len(item["text"].encode("utf-8"))
        if rows > PFT_HISTORY_MAX_ROWS:
            return "row_limit"
        if byte_count > PFT_HISTORY_MAX_TEXT_BYTES:
            return "utf8_budget"
        return None

    def add_turn_page(self, response):
        """Consume one desc turn page; return its opaque nextCursor or None."""
        if not self._active():
            return None
        try:
            raw, following = _page(response)
            if len(raw) > PFT_HISTORY_PAGE_ITEMS:
                return self._limit("page_size")
            data = [_turn(value) for value in raw]
            key = self._page_key("turns", None, data, following)
            turns = copy.deepcopy(self._turns)
            for value in data:
                previous = turns.get(value["id"])
                if previous is not None and previous != value:
                    if previous["status"] != "inProgress" or value["status"] not in _TERMINAL:
                        _fail("CONFLICT")
                    # Item snapshots read while active cannot be promoted to
                    # final history by a later metadata-only status change.
                    if self._items.get(value["id"]) or self._items_complete.get(value["id"]):
                        _fail("CONFLICT")
                turns[value["id"]] = value
            if key in self._seen_pages:
                return following  # Revalidate status before accepting an old cached page.
            if self._pages >= PFT_HISTORY_MAX_PAGES:
                return self._limit("page_limit")
            order = _merge_order(self._order, [value["id"] for value in data])
            if self._turns_complete and following is not None:
                _fail("CONFLICT")
            if self._turns_complete and len(order) != len(self._order):
                _fail("CONFLICT")
            reason = self._within_display_budget(turns, self._items)
            if reason:
                return self._limit(reason)
            self._turns, self._order = turns, order
            for value in data:
                self._items.setdefault(value["id"], {})
                self._item_orders.setdefault(value["id"], [])
                self._items_complete.setdefault(value["id"], False)
            self._turns_complete = following is None
            self._seen_pages.add(key)
            self._pages += 1
            return following
        except PftChatHistoryError as error:
            self._failed(error)
            raise

    def add_item_page(self, turn_id, response):
        """Consume one asc page for an already-loaded exact turn id."""
        if not self._active():
            return None
        try:
            turn_id = _string(turn_id, 256, identifier=True)
            if turn_id not in self._turns:
                _fail("SCOPE")
            raw, following = _page(response)
            if len(raw) > PFT_HISTORY_PAGE_ITEMS:
                return self._limit("page_size")
            data = [_item(turn_id, entry) for entry in raw]
            key = self._page_key("items", turn_id, data, following)
            if key in self._seen_pages:
                return following
            if self._pages >= PFT_HISTORY_MAX_PAGES:
                return self._limit("page_limit")
            if self._items_complete[turn_id] and following is not None:
                _fail("CONFLICT")
            items = copy.deepcopy(self._items)
            for value in data:
                previous = items[turn_id].get(value["id"])
                items[turn_id][value["id"]] = value if previous is None else _merge_item(previous, value)
            order = _merge_order(self._item_orders[turn_id], [value["id"] for value in data])
            if self._items_complete[turn_id] and len(order) != len(self._item_orders[turn_id]):
                _fail("CONFLICT")
            reason = self._within_display_budget(self._turns, items)
            if reason:
                return self._limit(reason)
            self._items = items
            self._item_orders[turn_id] = order
            self._items_complete[turn_id] = following is None
            self._seen_pages.add(key)
            self._pages += 1
            return following
        except PftChatHistoryError as error:
            self._failed(error)
            raise

    def snapshot(self):
        rows, times, turns, suppressed, suppressed_inputs = [], [], [], 0, 0
        for turn_id in reversed(self._order):
            turn = self._turns[turn_id]
            turns.append({**turn, "items_complete": self._items_complete[turn_id]})
            assistant_indexes = []
            for identifier in self._item_orders[turn_id]:
                item = self._items[turn_id][identifier]
                suppressed_inputs += item["suppressed_inputs"]
                if item["text"] is None:
                    suppressed += 1
                    continue
                stamp, source = _time_text(item["started"], item["completed"])
                status = "completed" if item["role"] == "user" or turn["status"] in _TERMINAL else "unknown"
                rows.append({"role": item["role"], "text": item["text"], "status": status,
                             "history": True, "turn_id": turn_id, "item_id": identifier,
                             "time_source": source, "sanitized": item["sanitized"]})
                times.append(stamp)
                if item["role"] == "assistant":
                    assistant_indexes.append(len(rows) - 1)
            if assistant_indexes and turn["status"] in ("failed", "interrupted"):
                rows[assistant_indexes[-1]]["status"] = "error" if turn["status"] == "failed" else "interrupted"
        complete = (self._turns_complete and all(self._items_complete.values())
                    and not self._truncated and self._error is None)
        has_in_progress = any(turn["status"] == "inProgress" for turn in self._turns.values())
        has_unknown = any(turn["status"] == "unknown" for turn in self._turns.values())
        return {"thread_id": self.thread_id, "transcript": rows, "message_times": times,
                "tool_calls": [], "turns": turns, "complete": complete,
                "truncated": self._truncated, "limit_reason": self._limit_reason,
                "read_only": not complete or has_in_progress or has_unknown,
                "has_in_progress": has_in_progress, "has_unknown": has_unknown,
                "suppressed_items": suppressed, "suppressed_inputs": suppressed_inputs,
                "pages": self._pages, "error": copy.deepcopy(self._error)}
