# SPDX-License-Identifier: Apache-2.0
"""Independent nonmodal UI state tests; no capture, native child or file writes."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from test_pft_part_approval_views import QtDouble, Signal, Event

ADDON = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools"
_old = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_drawing_export_views as views
finally:
    sys.path[:] = _old

DATA = b"%PDF-independent-frozen-export-fixture"
DIGEST = hashlib.sha256(DATA).hexdigest()


class Renderer:
    instances = []
    def __init__(self, core, parent):
        self.has_active_session = False
        self.starts, self.cancels = [], 0
        self.instances.append(self)
    def start(self, runtime, data, width, height, completed, failed):
        self.starts.append((deepcopy(runtime), data, width, height))
        self.completed, self.failed, self.has_active_session = completed, failed, True
    def cancel(self): self.cancels += int(self.has_active_session)
    def success(self):
        self.has_active_session = False
        self.completed({"input_sha256": DIGEST, "byte_count": len(DATA), "width": 1000, "height": 1000})
    def finish_cancel(self):
        self.has_active_session = False
        self.failed(views.PftPdfError("PDF_CANCELLED"))


class DrawingExportViewTests(unittest.TestCase):
    def setUp(self):
        self.qt = QtDouble()
        base = self.qt.widgets.QLabel.__mro__[1]
        class Policy:
            Ignored = 0
            def setHorizontalPolicy(self, value): self.horizontal = value
        base.sizePolicy = lambda widget: Policy()
        base.setSizePolicy = lambda widget, value: setattr(widget, "policy", value)
        self.qt.widgets.QVBoxLayout.addWidget = lambda layout, item, *_: layout.children.append(item)
        self.qt.widgets.QHBoxLayout.addWidget = lambda layout, item, *_: layout.children.append(item)
        parent_dialog = self.qt.widgets.QDialog
        class FileDialog(parent_dialog):
            AcceptSave, AnyFile, DontConfirmOverwrite = 1, 2, 4
            def __init__(self, parent):
                super().__init__(parent)
                self.fileSelected, self.rejected = Signal(), Signal()
                self.opens, self.options = 0, {}
            def setAcceptMode(self, value): self.accept_mode = value
            def setFileMode(self, value): self.file_mode = value
            def setOption(self, key, value): self.options[key] = value
            def setNameFilter(self, value): self.name_filter = value
            def setDirectory(self, value): self.directory = value
            def selectFile(self, value): self.filename = value
            def open(self): self.opens += 1; self.visible = True
            def reject(self): self.visible = False; self.rejected.emit()
        self.qt.widgets.QFileDialog = FileDialog
        class Preview(base):
            def __init__(self, parent): super().__init__(parent); self.replies, self.clears = [], 0
            def set_preview(self, reply): self.check(); self.replies.append(deepcopy(reply))
            def clear_preview(self): self.check(); self.replies.clear(); self.clears += 1
        Renderer.instances = []
        for name, value in (
                ("_qt", lambda: (self.qt.core, self.qt.widgets)),
                ("_process_module", lambda: types.SimpleNamespace(PftPdfPreviewProcess=Renderer)),
                ("_preview_module", lambda: types.SimpleNamespace(create_pdf_preview_widget=Preview))):
            handle = patch.object(views, name, value); handle.start(); self.addCleanup(handle.stop)
        self.summary = {"document": "SyntheticExport", "page": "Page", "source": "<b>LiteralSource</b>",
                        "expires_at": "2099-01-01T00:00:00Z"}
        self.current = {"document": self.summary["document"], "page": "Page", "format": "pdf",
            "request_id": "neutral-request", "token": "neutral-token", "state": "awaiting_capture",
            "expires_at": self.summary["expires_at"], "storage": "memory_only", "saves_document": False,
            "requires_confirmation": True, "native_capture_confirmation": False,
            "native_publication_confirmation": False, "capture_state": "not_captured",
            "publication_state": "not_published", "cleanup_state": "complete",
            "cad_protection_state": "not_checked", "summary": deepcopy(self.summary),
            "byte_count": None, "sha256": None, "target": None, "target_exists": None, "error": None}
        self.calls = {key: [] for key in views._CALLBACKS}
        self.bundle = {"bytes": DATA, "runtime": {"program": "/neutral/python", "helper": "/neutral/helper.py"},
                       "default_path": "/neutral/export/SyntheticExport-Page.pdf"}
        self.callbacks = {"capture": self.capture, "rendered": self.rendered, "failed": self.failed,
                          "choose_target": self.choose_target, "publish": self.publish,
                          "cancel": self.cancel, "status": self.status}
        self.handle = self.open()
        self.renderer = Renderer.instances[-1]

    def open(self):
        return views.open_drawing_export_approval(self.qt.gui, self.summary, self.callbacks, translate=lambda text: text)
    def status(self): self.calls["status"].append(True); return deepcopy(self.current)
    def capture(self, ready, failed):
        self.calls["capture"].append(True)
        self.current["state"] = "capturing"
        self.ready, self.capture_failed = ready, failed
    def finish_capture(self, bundle=None):
        if self.current["state"] != "cancelling": self.current["state"] = "rendering"
        self.current.update(native_capture_confirmation=True, capture_state="captured",
                            cad_protection_state="verified", byte_count=len(DATA), sha256=DIGEST)
        self.ready(self.bundle if bundle is None else bundle)
    def rendered(self, reply):
        self.calls["rendered"].append(deepcopy(reply)); self.current["state"] = "preview_ready"
        return deepcopy(self.current)
    def failed(self, code):
        self.calls["failed"].append(code)
        if self.current["state"] not in views._TERMINAL:
            self.current["state"] = "cancelled" if self.current["state"] == "cancelling" and code == "PDF_CANCELLED" else "failed"
            self.current["error"] = {"code": code, "message": "Fixed neutral failure"}
        return deepcopy(self.current)
    def choose_target(self, path):
        self.calls["choose_target"].append(path)
        self.current.update(state="target_ready", target=path, target_exists=False)
        return deepcopy(self.current)
    def publish(self):
        self.calls["publish"].append(True)
        self.current.update(state="published", publication_state="published", native_publication_confirmation=True)
        return deepcopy(self.current)
    def cancel(self):
        self.calls["cancel"].append(True)
        if self.current["state"] not in views._TERMINAL:
            self.current["state"] = "cancelling" if self.handle._waiting() else "cancelled"
        return deepcopy(self.current)
    def click(self, name): getattr(self.handle, name + "_button").clicked.emit(False)
    def preview_ready(self):
        self.click("capture"); self.finish_capture(); self.renderer.success()
    def target_ready(self):
        self.preview_ready(); self.click("choose")
        self.handle.file_dialog.fileSelected.emit("/neutral/export/Selected.pdf")

    def test_construction_is_inert_nonmodal_plaintext_and_cancel_is_default(self):
        self.assertIs(self.handle.dialog.parent, self.qt.window)
        self.assertFalse(self.handle.dialog.modal)
        self.assertEqual(self.calls["capture"], [])
        self.assertEqual(self.calls["publish"], [])
        self.assertEqual(self.renderer.starts, [])
        self.assertTrue(self.handle.cancel_button.default)
        for button in (self.handle.capture_button, self.handle.choose_button, self.handle.publish_button):
            self.assertFalse(button.default); self.assertFalse(button.autodefault)
        labels = [item for item in self.qt.objects if item.name.startswith("pftDrawingExport") and hasattr(item, "format")]
        self.assertTrue(all(item.format == 0 and item.minimum_width == 0 for item in labels))
        self.assertTrue(any("<b>LiteralSource</b>" in item.text for item in labels))
        self.assertTrue(self.handle.capture_button.enabled)
        self.assertFalse(self.handle.choose_button.enabled)
        self.assertFalse(self.handle.publish_button.enabled)

    def test_async_capture_is_explicit_one_shot_and_duplicate_late_ready_is_ignored(self):
        self.click("capture"); self.click("capture")
        self.assertEqual(len(self.calls["capture"]), 1)
        self.assertEqual(self.renderer.starts, [])
        self.finish_capture(); self.ready(self.bundle); self.capture_failed("PDF_RENDER_FAILED")
        self.assertEqual(len(self.renderer.starts), 1)
        self.assertEqual(self.renderer.starts[0][1:], (DATA, 1000, 1000))
        self.assertEqual(self.calls["publish"], [])
        self.renderer.success()
        self.assertEqual(len(self.calls["rendered"]), 1)
        self.assertTrue(self.handle.choose_button.enabled)
        self.assertFalse(self.handle.publish_button.enabled)

    def test_synchronous_ready_is_deferred_until_capture_callback_returns(self):
        events = []
        def capture(ready, failed):
            self.capture(ready, failed); events.append("begin")
            self.finish_capture(); events.append("returned-ready")
            self.assertEqual(self.renderer.starts, [])
            events.append("end")
        self.handle.callbacks["capture"] = capture
        self.click("capture")
        self.assertEqual(events, ["begin", "returned-ready", "end"])
        self.assertEqual(len(self.renderer.starts), 1)

    def test_close_during_pending_capture_never_starts_a_late_helper(self):
        self.click("capture"); self.handle.close()
        self.assertFalse(self.handle._closed)
        self.assertEqual(len(self.calls["cancel"]), 1)
        self.finish_capture()
        self.assertEqual(self.renderer.starts, [])
        self.assertTrue(self.handle._closed)
        self.assertEqual(self.current["state"], "cancelled")
        self.assertEqual(self.calls["publish"], [])

    def test_close_during_render_waits_for_actual_terminal_and_reports_once(self):
        self.click("capture"); self.finish_capture()
        event = self.handle.dialog.close()
        self.assertTrue(event.ignored)
        self.assertFalse(self.handle._closed)
        self.assertTrue(self.renderer.has_active_session)
        self.assertEqual(self.calls["failed"], [])
        self.handle.close()
        self.assertEqual(self.renderer.cancels, 1)
        self.renderer.finish_cancel()
        self.assertTrue(self.handle._closed)
        self.assertEqual(self.calls["failed"], ["PDF_CANCELLED"])
        self.assertEqual(self.current["state"], "cancelled")

    def test_release_hides_ui_but_does_not_claim_the_child_has_exited(self):
        self.click("capture"); self.finish_capture()
        self.handle.release()
        self.assertFalse(self.handle.dialog.visible)
        self.assertFalse(self.handle._closed)
        self.assertTrue(self.renderer.has_active_session)
        self.renderer.finish_cancel()
        self.assertTrue(self.handle._closed)
        self.assertEqual(self.calls["publish"], [])

    def test_parent_destruction_cancels_only_own_renderer_and_avoids_deleted_widgets(self):
        self.click("capture"); self.finish_capture()
        self.handle.dialog.deleteLater()
        self.assertTrue(self.handle._destroyed_flag)
        self.assertFalse(self.handle._closed)
        self.assertTrue(self.renderer.has_active_session)
        self.renderer.finish_cancel()
        self.assertTrue(self.handle._closed)
        self.assertEqual(self.calls["failed"], ["PDF_CANCELLED"])

    def test_external_expiry_cancels_render_and_late_success_cannot_show_or_publish(self):
        self.click("capture"); self.finish_capture()
        self.current["state"] = "expired"
        self.handle.timer.timeout.emit()
        self.assertEqual(self.renderer.cancels, 1)
        self.renderer.success()
        self.assertEqual(self.calls["rendered"], [])
        self.assertEqual(self.handle.preview.replies, [])
        self.assertFalse(self.handle.publish_button.enabled)
        self.assertEqual(self.current["state"], "expired")

    def test_external_cancelling_state_stops_child_without_overwriting_cancel_reason(self):
        self.click("capture"); self.finish_capture()
        self.current["state"] = "cancelling"
        self.handle.timer.timeout.emit()
        self.assertGreaterEqual(self.renderer.cancels, 1)
        self.assertFalse(self.handle._closed)
        self.handle.close()
        self.assertEqual(self.calls["cancel"], [])
        self.renderer.finish_cancel()
        self.assertTrue(self.handle._closed)
        self.assertEqual(self.calls["failed"], ["PDF_CANCELLED"])

    def test_file_dialog_is_async_prefilled_and_file_selection_never_publishes(self):
        self.preview_ready(); self.click("choose")
        dialog = self.handle.file_dialog
        self.assertEqual(dialog.opens, 1)
        self.assertFalse(dialog.modal)
        self.assertEqual(dialog.directory, "/neutral/export")
        self.assertEqual(dialog.filename, "SyntheticExport-Page.pdf")
        self.assertTrue(dialog.options[dialog.DontConfirmOverwrite])
        dialog.fileSelected.emit("/neutral/export/Selected.pdf")
        dialog.fileSelected.emit("/neutral/export/Late.pdf")
        self.assertEqual(self.calls["choose_target"], ["/neutral/export/Selected.pdf"])
        self.assertEqual(self.calls["publish"], [])
        self.assertTrue(self.handle.publish_button.enabled)
        self.assertIn("Selected.pdf", self.handle.target_label.text)
        self.assertIn(DIGEST, self.handle.digest_label.text)
        self.assertIn(str(len(DATA)), self.handle.bytes_label.text)

    def test_file_dialog_cancel_keeps_frozen_preview_and_expiry_blocks_late_selection(self):
        self.preview_ready(); self.click("choose")
        self.handle.file_dialog.reject()
        self.assertEqual(self.calls["cancel"], [])
        self.assertEqual(len(self.handle.preview.replies), 1)
        self.click("choose"); old = self.handle.file_dialog
        self.current["state"] = "expired"; self.handle.timer.timeout.emit()
        old.fileSelected.emit("/neutral/export/Late.pdf")
        self.assertEqual(self.calls["choose_target"], [])
        self.assertEqual(self.calls["publish"], [])

    def test_return_escape_accept_and_reject_are_cancellation_not_approval(self):
        for method in (lambda h: h.dialog.keyPressEvent(Event(10)), lambda h: h.dialog.keyPressEvent(Event(13)),
                       lambda h: h.dialog.keyPressEvent(Event(27)), lambda h: h.dialog.accept(), lambda h: h.dialog.reject()):
            self.current["state"] = "awaiting_capture"
            handle = self.open(); method(handle)
            self.assertTrue(handle._closed)
        self.assertEqual(self.calls["capture"], [])
        self.assertEqual(self.calls["publish"], [])

    def test_final_publish_is_one_shot_even_with_reentry_and_close_during_callback(self):
        self.target_ready()
        def publish():
            self.handle._publish()
            self.handle.close()
            return self.publish()
        self.handle.callbacks["publish"] = publish
        self.click("publish"); self.handle._publish()
        self.assertEqual(len(self.calls["publish"]), 1)
        self.assertEqual(self.calls["cancel"], [])
        self.assertEqual(self.calls["failed"], [])
        self.assertTrue(self.handle._closed)

    def test_publish_exception_is_unknown_and_never_downgraded_or_retried(self):
        self.target_ready()
        def publish():
            self.calls["publish"].append(True)
            self.current.update(state="unknown", publication_state="unknown", native_publication_confirmation=True)
            raise RuntimeError("private-untrusted-publication-diagnostic")
        self.handle.callbacks["publish"] = publish
        self.click("publish"); self.handle.timer.timeout.emit(); self.handle._publish()
        self.assertEqual(len(self.calls["publish"]), 1)
        self.assertEqual(self.calls["failed"], [])
        self.assertIn("不确定", self.handle.status_label.text)
        self.assertNotIn("private-untrusted", self.handle.status_label.text)
        self.assertFalse(self.handle.publish_button.enabled)
        self.handle.close(); self.assertEqual(self.calls["cancel"], [])

    def test_bad_status_after_publication_exception_cannot_claim_a_normal_failure(self):
        self.target_ready()
        def publish():
            self.calls["publish"].append(True)
            raise RuntimeError("neutral-publication-unknown")
        self.handle.callbacks["publish"] = publish
        self.click("publish")
        self.handle.callbacks["status"] = lambda: {}
        self.handle.timer.timeout.emit(); self.handle._publish(); self.handle.close()
        self.assertEqual(self.calls["failed"], [])
        self.assertEqual(self.calls["cancel"], [])
        self.assertEqual(len(self.calls["publish"]), 1)
        self.assertTrue(self.handle._publish_uncertain)
        self.assertIn("不确定", self.handle.status_label.text)

    def test_cancel_callback_exception_never_fabricates_a_cancelled_receipt(self):
        def cancel():
            self.calls["cancel"].append(True)
            raise RuntimeError("neutral-cancel-failure")
        self.handle.callbacks["cancel"] = cancel
        self.handle.close()
        self.assertNotEqual(self.handle._last["state"], "cancelled")
        self.assertEqual(self.calls["failed"], ["PDF_PROTOCOL_INVALID"])
        self.assertTrue(self.handle._closed)

    def test_cancel_callback_exception_keeps_render_ownership_until_actual_terminal(self):
        self.click("capture"); self.finish_capture()
        def cancel(): raise RuntimeError("neutral-cancel-failure")
        self.handle.callbacks["cancel"] = cancel
        self.handle.close()
        self.assertFalse(self.handle._closed)
        self.assertEqual(self.calls["failed"], [])
        self.assertTrue(self.renderer.has_active_session)
        self.renderer.finish_cancel()
        self.assertEqual(self.calls["failed"], ["PDF_PROTOCOL_INVALID"])
        self.assertTrue(self.handle._closed)

    def test_cancel_callback_exception_waits_for_pending_capture_delivery(self):
        self.click("capture")
        def cancel(): raise RuntimeError("neutral-cancel-failure")
        self.handle.callbacks["cancel"] = cancel
        self.handle.close()
        self.assertFalse(self.handle._closed)
        self.assertEqual(self.calls["failed"], [])
        self.finish_capture()
        self.assertEqual(self.renderer.starts, [])
        self.assertTrue(self.handle._closed)
        self.assertEqual(self.calls["failed"], ["PDF_CANCELLED"])
        self.assertEqual(self.current["state"], "failed")

    def test_published_cleanup_and_cad_warnings_are_shown_independently(self):
        self.target_ready()
        def publish():
            self.publish(); self.current.update(cleanup_state="unknown", cad_protection_state="unverified")
            return deepcopy(self.current)
        self.handle.callbacks["publish"] = publish
        self.click("publish")
        self.assertIn("暂存清理未确认", self.handle.status_label.text)
        self.assertIn("CAD 状态保持未确认", self.handle.status_label.text)
        self.assertFalse(self.handle.publish_button.enabled)

    def test_invalid_bundle_and_capture_exception_cannot_start_a_helper(self):
        self.click("capture")
        bad = deepcopy(self.bundle); bad["default_path"] = "relative/unsupported.pdf"
        self.finish_capture(bad)
        self.assertEqual(self.renderer.starts, [])
        self.assertEqual(self.calls["failed"], ["PDF_PROTOCOL_INVALID"])
        self.assertFalse(self.handle.publish_button.enabled)

    def test_fixed_publisher_codes_are_preserved_without_raw_messages(self):
        self.current["state"] = "failed"
        for code in sorted(views._PUBLISH_ERROR_CODES):
            with self.subTest(code=code):
                self.current["error"] = {"code": code, "message": "neutral-raw-diagnostic-never-display"}
                self.handle.timer.timeout.emit()
                self.assertIn(code, self.handle.status_label.text)
                self.assertNotIn("neutral-raw-diagnostic", self.handle.status_label.text)
        self.current["error"] = {"code": "EXPORT_UNRECOGNIZED_NEUTRAL", "message": "neutral-raw-diagnostic-never-display"}
        self.handle.timer.timeout.emit()
        self.assertIn("PDF_RENDER_FAILED", self.handle.status_label.text)
        self.assertNotIn("EXPORT_UNRECOGNIZED", self.handle.status_label.text)
        self.assertEqual(self.calls["publish"], [])

    def test_changed_status_identity_or_frozen_digest_blocks_actions(self):
        self.preview_ready()
        self.current["sha256"] = "b" * 64
        self.handle.timer.timeout.emit(); self.handle._choose(); self.handle._publish()
        self.assertEqual(self.calls["choose_target"], [])
        self.assertEqual(self.calls["publish"], [])
        self.assertEqual(self.calls["failed"], ["PDF_PROTOCOL_INVALID"])
        self.assertFalse(self.handle.publish_button.enabled)

    def test_invalid_inputs_are_rejected_before_qt_construction(self):
        for summary in ({}, {**self.summary, "source": object()}, {**self.summary, "format": "svg"}):
            with self.assertRaises(views.PftPdfError):
                views.open_drawing_export_approval(self.qt.gui, summary, self.callbacks)
        for callbacks in ({}, {**self.callbacks, "extra": lambda: None}, {**self.callbacks, "publish": None}):
            with self.assertRaises(views.PftPdfError):
                views.open_drawing_export_approval(self.qt.gui, self.summary, callbacks)

    def test_unsaved_document_default_basename_does_not_invent_a_directory(self):
        self.bundle["default_path"] = "Unsaved-Page.pdf"
        self.preview_ready(); self.click("choose")
        dialog = self.handle.file_dialog
        self.assertEqual(dialog.filename, "Unsaved-Page.pdf")
        self.assertFalse(hasattr(dialog, "directory"))

    def test_authoritative_cancelled_snapshot_ends_a_queued_capture_without_a_child(self):
        self.click("capture")
        self.current["state"] = "cancelled"
        self.handle.close()
        self.assertTrue(self.handle._closed)
        self.ready(self.bundle)
        self.assertEqual(self.renderer.starts, [])
        self.assertEqual(self.calls["publish"], [])

    def test_exception_after_renderer_start_waits_for_real_terminal_before_failed_callback(self):
        start = self.renderer.start
        def broken(*args):
            start(*args)
            raise RuntimeError("private-untrusted-after-start")
        self.renderer.start = broken
        self.click("capture")
        self.finish_capture()  # Delivery must not throw back to the controller.
        self.assertTrue(self.renderer.has_active_session)
        self.assertTrue(self.handle._render_pending)
        self.assertEqual(self.calls["failed"], [])
        self.assertEqual(self.renderer.cancels, 1)
        self.renderer.finish_cancel()
        self.assertEqual(self.calls["failed"], ["PDF_RENDER_FAILED"])
        self.assertFalse(self.handle._render_pending)
        self.assertEqual(self.calls["rendered"], [])

    def test_close_from_capture_status_callback_prevents_renderer_start(self):
        self.click("capture")
        status = self.handle.callbacks["status"]
        def read():
            if self.current["state"] == "rendering": self.handle.close()
            return status()
        self.handle.callbacks["status"] = read
        self.finish_capture()
        self.assertEqual(self.renderer.starts, [])
        self.assertTrue(self.handle._closed)
        self.assertEqual(self.current["state"], "cancelled")

    def test_present_reuses_the_same_window_and_cannot_capture(self):
        dialog = self.handle.dialog
        self.handle.present(); self.handle.present()
        self.assertIs(self.handle.dialog, dialog)
        self.assertEqual(self.calls["capture"], [])
        self.assertEqual(self.renderer.starts, [])

    def test_present_cannot_reopen_a_window_closed_from_status_callback(self):
        def status():
            self.handle.close()
            return deepcopy(self.current)
        self.handle.callbacks["status"] = status
        self.handle.present()
        self.assertTrue(self.handle._closed)
        self.assertFalse(self.handle.dialog.visible)

    def test_foreign_token_or_request_id_never_authorizes_capture_or_publish(self):
        self.current["token"] = "another-neutral-token"
        self.handle.timer.timeout.emit()
        self.click("capture"); self.handle._publish()
        self.assertEqual(self.calls["capture"], [])
        self.assertEqual(self.calls["publish"], [])
        self.assertEqual(self.calls["failed"], ["PDF_PROTOCOL_INVALID"])


if __name__ == "__main__":
    unittest.main()
