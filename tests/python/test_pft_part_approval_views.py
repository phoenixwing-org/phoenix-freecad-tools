# SPDX-License-Identifier: Apache-2.0
"""Approval lifecycle with an injected Qt double; no Qt, CAD or service launch."""
from copy import deepcopy
from html import escape
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ADDON = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools"
_saved_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_part_approval_views as views
    import pft_workbench_i18n as i18n
finally:
    sys.path[:] = _saved_path


class Signal:
    def __init__(self): self.slots = []
    def connect(self, slot): self.slots.append(slot)
    def emit(self, *args):
        for slot in list(self.slots): slot(*args)


class Event:
    def __init__(self, key=None): self.code, self.accepted, self.ignored = key, False, False
    def key(self): return self.code
    def accept(self): self.accepted = True
    def ignore(self): self.ignored = True


class QtDouble:
    def __init__(self):
        owner = self
        self.objects = []
        class Base:
            def __init__(self, parent=None):
                self.parent, self.name, self.deleted, self.visible, self.enabled = parent, "", False, False, True
                self.destroyed = Signal()
                self.delete_calls = 0
                owner.objects.append(self)
            def check(self):
                if self.deleted: raise RuntimeError("synthetic deleted Qt wrapper")
            def setObjectName(self, value): self.check(); self.name = value
            def setWindowTitle(self, value): self.check(); self.title = value
            def setMinimumWidth(self, value): self.check(); self.minimum_width = value
            def setEnabled(self, value): self.check(); self.enabled = value
            def resize(self, *size): self.check(); self.size = size
            def show(self): self.check(); self.visible = True
            def hide(self): self.check(); self.visible = False
            def raise_(self): self.check()
            def activateWindow(self): self.check()
            def deleteLater(self):
                self.check(); self.delete_calls += 1; self.deleted = True; self.visible = False
                for item in owner.objects:
                    if item.parent is self: item.deleted = True
                self.destroyed.emit(self)
        class Dialog(Base):
            def __init__(self, parent=None):
                super().__init__(parent); self.accepted, self.finished = Signal(), Signal()
            def setModal(self, value): self.modal = value
            def setWindowModality(self, value): self.modality = value
            def close(self):
                event = Event(); self.closeEvent(event); return event
            def done(self, result):
                self.visible = False
                if result == 1: self.accepted.emit()
                self.finished.emit(result)
            def keyPressEvent(self, event): self.forwarded_key = event.code
            def exec(self): raise AssertionError("A nonmodal view must not run a nested event loop")
            exec_ = exec
        class Label(Base):
            def __init__(self, text, parent): super().__init__(parent); self.text = text
            def setText(self, value): self.check(); self.text = value
            def setTextFormat(self, value): self.format = value
            def setWordWrap(self, value): self.wordwrap = value
            def setToolTip(self, value): self.tooltip = value
        class Button(Label):
            def __init__(self, text, parent): super().__init__(text, parent); self.clicked = Signal()
            def setAutoDefault(self, value): self.autodefault = value
            def setDefault(self, value): self.default = value
            def setFocus(self): self.check(); self.focused = True
        class Layout:
            def __init__(self, *args): self.children = []
            def setContentsMargins(self, *args): self.margins = args
            def addWidget(self, item): self.children.append(item)
            def addLayout(self, item): self.children.append(item)
            def addStretch(self, *args): pass
        class Table(Base):
            def __init__(self, rows, columns, parent):
                super().__init__(parent); self.rows, self.columns, self.cells = rows, columns, {}
            def setHorizontalHeaderLabels(self, value): self.headers = value
            def setEditTriggers(self, value): self.edit_triggers = value
            def setWordWrap(self, value): self.wordwrap = value
            def setTextElideMode(self, value): self.elide = value
            def setHorizontalScrollBarPolicy(self, value): self.scroll_policy = value
            def resizeColumnsToContents(self): self.complete_widths = True
            def setItem(self, row, col, item): self.cells[row, col] = item
        class Item:
            def __init__(self, text): self.text = text
            def setFlags(self, value): self.flags = value
            def setToolTip(self, value): self.tooltip = value
        class Timer(Base):
            def __init__(self, parent): super().__init__(parent); self.timeout, self.active = Signal(), False
            def start(self, interval): self.check(); self.active, self.interval = True, interval
            def stop(self): self.check(); self.active = False
        self.widgets = types.SimpleNamespace(QDialog=Dialog, QLabel=Label, QPushButton=Button,
            QVBoxLayout=Layout, QHBoxLayout=Layout, QTableWidget=Table, QTableWidgetItem=Item,
            QAbstractItemView=types.SimpleNamespace(EditTrigger=types.SimpleNamespace(NoEditTriggers=0)))
        self.core = types.SimpleNamespace(QTimer=Timer, Qt=types.SimpleNamespace(
            WindowModality=types.SimpleNamespace(NonModal=0), TextFormat=types.SimpleNamespace(PlainText=0),
            TextElideMode=types.SimpleNamespace(ElideNone=3), ScrollBarPolicy=types.SimpleNamespace(ScrollBarAsNeeded=0),
            ItemFlag=types.SimpleNamespace(ItemIsEnabled=2, ItemIsSelectable=4),
            Key=types.SimpleNamespace(Key_Return=10, Key_Enter=13, Key_Escape=27)))
        self.window = Base()
        self.gui = types.SimpleNamespace(getMainWindow=lambda: self.window)


class PartApprovalViewTests(unittest.TestCase):
    def setUp(self):
        self.qt = QtDouble()
        qt_patch = patch.object(views, "_qt", return_value=(self.qt.core, self.qt.widgets))
        qt_patch.start(); self.addCleanup(qt_patch.stop)
        self.addCleanup(self.clear_handles)
        self.preview = {"document": "SyntheticDocument", "operation": "set_properties", "token": "synthetic-token",
            "state": "prepared", "can_apply": True, "expires_at": "2099-01-01T01:02:03Z", "ttl_seconds": 300,
            "changed_count": 2, "rows": [
                {"object": "PartA", "field": "PartName", "old": "<b>literal old</b>", "new": "长值" * 1200,
                 "status": "change", "reason": "synthetic reason", "source": "synthetic source", "note": "note\nline 2"},
                {"object": "PartB", "field": "PartNumber", "old": None, "new": {"nested": [1, False, "literal"]}, "status": "change"},
                {"object": "PartC", "field": "Label", "old": "same", "new": "same", "status": "unchanged"}]}
        self.status = self.dto()
        self.confirm, self.cancel = Mock(side_effect=lambda: self.dto("applied")), Mock()
        self.read = Mock(side_effect=lambda: deepcopy(self.status))

    def clear_handles(self):
        for handle in list(views._HANDLES.values()):
            handle._state, handle._confirming = "failed", False
            handle.close()
        self.assertFalse(views._HANDLES)

    def dto(self, state="awaiting_confirmation", code=None):
        return {"document": self.preview["document"], "operation": self.preview["operation"], "token": self.preview["token"],
            "state": state, "changed_count": self.preview["changed_count"], "requires_confirmation": True,
            "saves_document": False, "storage": "memory_only", "native_confirmation": state == "applied",
            "result": {"changed_count": self.preview["changed_count"], "saves_document": False} if state == "applied" else None,
            "error_code": code}

    def open(self, translate=None):
        return views.open_part_approval(self.qt.gui, self.preview, self.confirm, self.cancel, self.read, translate)

    def pad_preview(self):
        self.preview.update(operation="set_pad_length", changed_count=1,
            design_request={"body": "Body", "pad": "Pad", "length_mm": 12.0},
            rows=[{"object": "Pad", "field": "Length (mm)", "old": 8.0, "new": 12.0,
                   "body": "Body", "unit": "mm", "recompute_roots": ["Body"], "status": "change"}])
        self.status = self.dto()

    def pad_applied(self):
        result = self.dto("applied")
        shape = {"volume_mm3": 2400.0, "dimensions_mm": [20.0, 10.0, 12.0], "solids": 1}
        result["result"]["design_result"] = {"kind": "pad_length", "body": "Body", "pad": "Pad", "length_mm": 12.0,
            "recompute_count": 2, "checked_objects": 12, "geometry": {"pad": deepcopy(shape), "body": deepcopy(shape)},
            "validation": "native_valid_one_solid_and_length_scaled_volume"}
        return result

    def test_pad_view_requires_bound_request_and_shows_scope_in_full_row_details(self):
        self.pad_preview()
        for request in (None, {}, {"body": "Body", "pad": "Other", "length_mm": 12},
                        {"body": "Body", "pad": "Pad", "length_mm": True}):
            bad = deepcopy(self.preview); bad["design_request"] = request
            with self.assertRaisesRegex(ValueError, "PART_APPROVAL_PREVIEW_INVALID"):
                views.open_part_approval(self.qt.gui, bad, self.confirm, self.cancel, self.read)
        handle = self.open()
        self.assertIn('"body": "Body"', handle.table.cells[0, 5].text)
        self.assertIn('"unit": "mm"', handle.table.cells[0, 5].text)
        self.assertIn('"recompute_roots": ["Body"]', handle.table.cells[0, 5].text)
        self.assertEqual(handle.table.cells[0, 2].text, "8.0")
        self.assertEqual(handle.table.cells[0, 3].text, "12.0")
        self.confirm.assert_not_called()

    def test_pad_view_rejects_unconfirmed_applying_and_unverified_geometry(self):
        self.pad_preview(); handle = self.open()
        applying = self.dto("applying")
        with self.assertRaisesRegex(ValueError, "PART_APPROVAL_STATUS_INVALID"):
            handle._status(applying)
        applying["native_confirmation"] = True
        self.assertEqual(handle._status(applying), applying)
        for change in ("missing", "target", "length", "invalid", "extra"):
            result = self.pad_applied()
            if change == "missing": result["result"].pop("design_result")
            elif change == "target": result["result"]["design_result"]["pad"] = "Other"
            elif change == "length": result["result"]["design_result"]["length_mm"] = 11
            elif change == "invalid": result["result"]["design_result"]["geometry"]["body"]["solids"] = 0
            else: result["result"]["untrusted_detail"] = "synthetic-hidden-detail"
            with self.subTest(change=change):
                with self.assertRaises(ValueError): handle._status(result)
        self.assertEqual(handle._status(self.pad_applied()), self.pad_applied())
        self.confirm.assert_not_called()

    def test_pad_confirm_closes_only_after_bound_geometry_result(self):
        self.pad_preview(); self.confirm.side_effect = self.pad_applied
        handle = self.open(); handle.confirm_button.clicked.emit(False)
        self.assertTrue(handle._closed)
        self.confirm.assert_called_once_with(); self.cancel.assert_not_called()

    def test_nonmodal_compact_parented_default_cancel_and_plain_labels(self):
        handle = self.open()
        self.assertIs(handle.dialog.parent, self.qt.window)
        self.assertFalse(handle.dialog.modal)
        self.assertEqual(handle.dialog.modality, 0)
        self.assertEqual(handle.dialog.size, (720, 420))
        self.assertEqual(handle.dialog.minimum_width, 0)
        self.assertTrue(handle.dialog.visible)
        self.assertTrue(handle.confirm_button.enabled)
        self.assertFalse(handle.confirm_button.default)
        self.assertFalse(handle.confirm_button.autodefault)
        self.assertTrue(handle.cancel_button.default)
        self.assertTrue(handle.cancel_button.focused)
        labels = [item for item in self.qt.objects if type(item) is self.qt.widgets.QLabel]
        self.assertTrue(all(item.format == 0 for item in labels))
        self.assertIn("原预览到期时间：2099-01-01T01:02:03Z", [item.text for item in labels])
        self.assertTrue(any("可撤销" in item.text and "不会自动保存" in item.text for item in labels))
        self.assertTrue(handle.timer.active)
        self.assertEqual(handle.timer.interval, 1000)
        self.assertIs(handle.present(), handle)
        self.confirm.assert_not_called(); self.cancel.assert_not_called()

    def test_all_rows_full_values_readonly_and_literal_tooltips(self):
        handle = self.open(); table = handle.table
        self.assertEqual((table.rows, table.columns, len(table.cells)), (3, 6, 18))
        self.assertEqual(table.edit_triggers, 0)
        self.assertEqual(table.elide, 3)
        self.assertEqual(table.scroll_policy, 0)
        self.assertEqual(table.minimum_width, 0)
        self.assertTrue(table.complete_widths)
        self.assertEqual(table.cells[0, 2].text, self.preview["rows"][0]["old"])
        self.assertEqual(table.cells[0, 3].text, self.preview["rows"][0]["new"])
        self.assertEqual(table.cells[1, 2].text, "null")
        self.assertIn('"nested": [1, false, "literal"]', table.cells[1, 3].text)
        for item in table.cells.values():
            self.assertEqual(item.flags, 6)
            self.assertEqual(item.tooltip, "<pre>" + escape(item.text) + "</pre>")
        for key in ("reason", "source", "note"):
            self.assertIn(key, table.cells[0, 5].text)
        self.preview["rows"][0]["new"] = "caller mutation"
        self.assertNotEqual(table.cells[0, 3].text, "caller mutation")

    def test_cancel_button_and_handle_close_cancel_once_and_late_callbacks_are_safe(self):
        handle = self.open(); timer = handle.timer
        handle.cancel_button.clicked.emit(False)
        handle.close(); handle.present(); timer.timeout.emit(); handle.confirm_button.clicked.emit(False)
        self.assertTrue(handle._closed)
        self.assertFalse(timer.active)
        self.assertEqual(handle.dialog.delete_calls, 1)
        self.assertNotIn(id(handle), views._HANDLES)
        self.cancel.assert_called_once_with(); self.confirm.assert_not_called()
        self.assertEqual(self.read.call_count, 1)

    def test_enter_return_escape_reject_and_window_close_never_confirm(self):
        for action in (10, 13, 27, "reject", "close", "accept", "done"):
            with self.subTest(action=action):
                self.cancel.reset_mock(); handle = self.open()
                if type(action) is int:
                    event = Event(action); handle.dialog.keyPressEvent(event); self.assertTrue(event.accepted)
                elif action == "done": handle.dialog.done(1)
                else: getattr(handle.dialog, action)()
                self.assertTrue(handle._closed)
                self.cancel.assert_called_once_with(); self.confirm.assert_not_called()

    def test_accepted_signal_has_no_approval_handler(self):
        handle = self.open(); handle.dialog.accepted.emit()
        self.confirm.assert_not_called(); self.cancel.assert_not_called()
        self.assertFalse(handle._closed)

    def test_only_explicit_confirm_calls_once_and_applied_does_not_cancel(self):
        handle = self.open(); handle.confirm_button.clicked.emit(False)
        self.assertTrue(handle._closed)
        self.confirm.assert_called_once_with(); self.cancel.assert_not_called()
        handle.confirm_button.clicked.emit(False); handle.timer.timeout.emit(); handle.close()
        self.confirm.assert_called_once_with(); self.cancel.assert_not_called()

    def test_applied_actual_count_may_differ_from_original_preview(self):
        handle = self.open()
        result = self.dto("applied")
        result["changed_count"] = result["result"]["changed_count"] = 0
        self.confirm.side_effect = lambda: result
        handle.confirm_button.clicked.emit(False)
        self.assertTrue(handle._closed)
        self.cancel.assert_not_called()
        self.assertEqual(handle.preview["changed_count"], 2)

    def test_construct_failure_deletes_partial_dialog_and_cancels_once(self):
        with patch.object(self.qt.widgets.QTableWidget, "setItem", side_effect=RuntimeError("synthetic construction error")):
            with self.assertRaisesRegex(RuntimeError, "^PART_APPROVAL_UI_UNAVAILABLE$"):
                self.open()
        dialog = next(item for item in self.qt.objects if isinstance(item, self.qt.widgets.QDialog))
        self.assertTrue(dialog.deleted)
        self.assertEqual(dialog.delete_calls, 1)
        self.cancel.assert_called_once_with()
        self.assertFalse(views._HANDLES)

    def test_present_failure_cleans_owned_timer_and_dialog(self):
        with patch.object(self.qt.widgets.QDialog, "show", side_effect=RuntimeError("synthetic show error")):
            handle = self.open()
        self.assertTrue(handle._closed)
        self.assertTrue(handle.dialog.deleted)
        self.assertFalse(handle.timer.active)
        self.cancel.assert_called_once_with()
        self.assertFalse(views._HANDLES)

    def test_qt_destruction_during_status_precheck_revokes_before_confirm(self):
        handle = self.open()
        def read():
            handle.dialog.deleteLater()
            return self.dto()
        self.read.side_effect = read
        handle.confirm_button.clicked.emit(False)
        self.assertTrue(handle._closed)
        self.cancel.assert_called_once_with(); self.confirm.assert_not_called()

    def test_confirm_reentrancy_controls_disabled_and_close_deferred_until_applied(self):
        handle = self.open()
        def callback():
            self.assertFalse(handle.confirm_button.enabled)
            self.assertFalse(handle.cancel_button.enabled)
            self.assertFalse(handle.table.enabled)
            handle.confirm_button.clicked.emit(False); handle.timer.timeout.emit(); handle.present(); handle.close()
            self.assertFalse(handle._closed)
            self.cancel.assert_not_called()
            return self.dto("applied")
        self.confirm.side_effect = callback
        handle.confirm_button.clicked.emit(False)
        self.assertTrue(handle._closed)
        self.confirm.assert_called_once_with(); self.cancel.assert_not_called()
        self.assertEqual(self.read.call_count, 2)

    def test_status_precheck_reentrancy_cannot_double_confirm(self):
        handle = self.open()
        def read():
            handle.confirm_button.clicked.emit(False)
            return self.dto()
        self.read.side_effect = read
        handle.confirm_button.clicked.emit(False)
        self.confirm.assert_called_once_with(); self.cancel.assert_not_called()

    def test_failed_result_only_close_enabled_fixed_code_and_no_retry(self):
        handle = self.open(); self.confirm.side_effect = lambda: self.dto("failed", "PART_PLAN_STALE")
        handle.confirm_button.clicked.emit(False)
        self.assertFalse(handle._closed)
        self.assertIn("PART_PLAN_STALE", handle.status_label.text)
        self.assertFalse(handle.confirm_button.enabled)
        self.assertFalse(handle.table.enabled)
        self.assertEqual(handle.cancel_button.text, "关闭")
        self.assertTrue(handle.cancel_button.enabled)
        self.assertFalse(handle.timer.active)
        handle.confirm_button.clicked.emit(False); handle.timer.timeout.emit(); handle.present()
        self.confirm.assert_called_once_with()
        handle.cancel_button.clicked.emit(False); self.cancel.assert_called_once_with()

    def test_confirm_exception_and_unrecognized_code_never_expose_exception_text(self):
        for outcome in (RuntimeError("synthetic sensitive exception"), self.dto("failed", "synthetic sensitive code"), None, self.dto()):
            with self.subTest(kind=type(outcome).__name__):
                self.confirm.reset_mock(); self.cancel.reset_mock(); handle = self.open()
                self.confirm.side_effect = outcome if isinstance(outcome, Exception) else lambda value=outcome: value
                handle.confirm_button.clicked.emit(False)
                self.assertIn("PART_CHANGE_FAILED", handle.status_label.text)
                self.assertNotIn("synthetic sensitive", handle.status_label.text)
                handle.confirm_button.clicked.emit(False)
                self.confirm.assert_called_once_with()
                handle.close()
        self.confirm.side_effect = lambda: self.dto("applied")

    def test_expiry_cancellation_and_apply_status_from_timer_close_without_write(self):
        for state in ("expired", "cancelled", "applied"):
            with self.subTest(state=state):
                handle = self.open(); self.status = self.dto(state); handle.timer.timeout.emit()
                self.assertTrue(handle._closed); self.confirm.assert_not_called(); self.cancel.assert_not_called()
                self.status = self.dto()

    def test_expired_precheck_closes_instead_of_applying(self):
        handle = self.open(); self.status = self.dto("expired"); handle.confirm_button.clicked.emit(False)
        self.assertTrue(handle._closed); self.confirm.assert_not_called(); self.cancel.assert_not_called()

    def test_timer_status_error_or_malformed_identity_cancels_safely(self):
        variants = [None, {}, self.dto(), self.dto(), self.dto(), self.dto(), self.dto("applied")]
        variants[2]["token"] = "other-token"
        variants[3]["changed_count"] = True
        variants[4]["native_confirmation"] = 1
        variants[5]["state"] = "unknown"
        variants[6]["native_confirmation"] = False
        for status in variants:
            with self.subTest(status=status):
                self.cancel.reset_mock(); handle = self.open()
                self.read.side_effect = lambda value=status: value
                handle.timer.timeout.emit()
                self.assertTrue(handle._closed); self.cancel.assert_called_once_with(); self.confirm.assert_not_called()
                self.read.side_effect = lambda: deepcopy(self.status)
        handle = self.open(); self.read.side_effect = RuntimeError("synthetic private read error")
        handle.timer.timeout.emit(); self.assertTrue(handle._closed)

    def test_prepared_or_blocked_or_no_changes_never_apply(self):
        for mode in ("prepared", "blocked", "zero"):
            with self.subTest(mode=mode):
                old_preview = deepcopy(self.preview)
                if mode == "prepared": self.status = self.dto("prepared")
                elif mode == "blocked": self.preview["can_apply"] = False
                else:
                    self.preview["changed_count"] = 0; self.status = self.dto()
                handle = self.open(); self.assertFalse(handle.confirm_button.enabled)
                handle.confirm_button.clicked.emit(False); self.confirm.assert_not_called()
                handle.close(); self.preview = old_preview; self.status = self.dto()

    def test_unconfirmed_states_cannot_claim_a_native_confirmation(self):
        for state in ("prepared", "awaiting_confirmation", "cancelled", "expired"):
            with self.subTest(state=state):
                self.cancel.reset_mock(); handle = self.open()
                value = self.dto(state); value["native_confirmation"] = True
                self.read.side_effect = lambda: value
                handle.timer.timeout.emit()
                self.assertTrue(handle._closed)
                self.cancel.assert_called_once_with(); self.confirm.assert_not_called()
                self.read.side_effect = lambda: deepcopy(self.status)

    def test_applying_timer_disables_controls_and_close_waits_for_terminal(self):
        handle = self.open(); self.status = self.dto("applying"); handle.timer.timeout.emit()
        self.assertFalse(handle.cancel_button.enabled); self.assertFalse(handle.confirm_button.enabled)
        handle.close(); self.assertFalse(handle._closed); self.cancel.assert_not_called()
        self.status = self.dto("applied"); handle.timer.timeout.emit()
        self.assertTrue(handle._closed); self.cancel.assert_not_called(); self.confirm.assert_not_called()

    def test_async_status_after_confirm_is_polled_and_cannot_reapply(self):
        handle = self.open(); self.confirm.side_effect = lambda: self.dto("applying")
        handle.confirm_button.clicked.emit(False)
        self.assertFalse(handle._closed); self.assertTrue(handle.timer.active)
        handle.confirm_button.clicked.emit(False); self.confirm.assert_called_once_with()
        self.status = self.dto("applied"); handle.timer.timeout.emit()
        self.assertTrue(handle._closed); self.cancel.assert_not_called()

    def test_external_qt_destruction_cancels_once_and_late_signals_touch_no_deleted_widgets(self):
        handle = self.open(); handle.dialog.deleteLater()
        handle.timer.timeout.emit(); handle.confirm_button.clicked.emit(False); handle.present(); handle.close()
        self.assertTrue(handle._closed); self.cancel.assert_called_once_with(); self.confirm.assert_not_called()

    def test_cancel_exception_is_suppressed_and_no_reentrancy(self):
        handle = self.open()
        def cancel():
            handle.close(); raise RuntimeError("synthetic private cancellation error")
        self.cancel.side_effect = cancel
        handle.close(); handle.close()
        self.cancel.assert_called_once_with(); self.assertTrue(handle._closed)

    def test_registry_is_bounded_and_terminal_handles_release(self):
        with patch.object(views, "_MAX_HANDLES", 2):
            first, second = self.open(), self.open()
            with self.assertRaisesRegex(RuntimeError, "^PART_APPROVAL_UI_UNAVAILABLE$"): self.open()
            self.assertEqual(len(views._HANDLES), 2)
            first.close(); third = self.open()
            self.assertEqual(len(views._HANDLES), 2)
            second.close(); third.close(); self.assertFalse(views._HANDLES)

    def test_invalid_preview_or_callbacks_fail_before_qt_construction(self):
        for preview in ({}, {**self.preview, "rows": [{"object": "PartA"}]}, {**self.preview, "changed_count": True},
                        {**self.preview, "rows": [{**self.preview["rows"][0], "new": float("nan")}]},
                        {**self.preview, "new": object()}):
            with self.subTest(keys=list(preview)):
                with self.assertRaisesRegex(ValueError, "^PART_APPROVAL_PREVIEW_INVALID$"):
                    views.open_part_approval(self.qt.gui, preview, self.confirm, self.cancel, self.read)
        with self.assertRaisesRegex(ValueError, "^PART_APPROVAL_CALLBACK_INVALID$"):
            views.open_part_approval(self.qt.gui, self.preview, None, self.cancel, self.read)
        self.assertEqual(len(self.qt.objects), 1)

    def test_prepared_transitions_to_awaiting_and_expiry_remains_original(self):
        self.status = self.dto("prepared")
        handle = self.open()
        self.assertFalse(handle.confirm_button.enabled)
        self.status = self.dto()
        handle.timer.timeout.emit()
        self.assertTrue(handle.confirm_button.enabled)
        self.assertEqual(handle.preview["expires_at"], "2099-01-01T01:02:03Z")
        self.assertEqual(handle.preview["ttl_seconds"], 300)
        self.confirm.assert_not_called()

    def test_callable_falsey_translator_is_still_used(self):
        class Translator:
            def __bool__(self): return False
            def __call__(self, text): return "translated: " + text
        handle = self.open(Translator())
        self.assertEqual(handle.dialog.title, "translated: 确认零件修改")

    def test_translation_is_injected_and_all_view_strings_have_english(self):
        old_language = i18n.get_language(); self.addCleanup(i18n.set_language, old_language)
        i18n.set_language("en")
        handle = self.open(i18n.tr)
        self.assertEqual(handle.confirm_button.text, "Confirm changes")
        self.assertEqual(handle.cancel_button.text, "Cancel")
        self.assertEqual(handle.dialog.title, "Confirm part changes")
        self.assertEqual(handle.table.headers[0], "Object")
        for source in views.TRANSLATABLE_STRINGS:
            self.assertIn(source, i18n.ENGLISH)


if __name__ == "__main__":
    unittest.main()
