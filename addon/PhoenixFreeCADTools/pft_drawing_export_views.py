# SPDX-License-Identifier: Apache-2.0
"""Nonmodal human PDF export approval; all business actions are injected."""
import hashlib
import os
from pathlib import Path
import re

from pft_pdf_contract import MAX_PDF_BYTES, PDF_ERROR_CODES, PftPdfError
from pft_workbench_i18n import tr

_CALLBACKS = frozenset(("capture", "rendered", "failed", "choose_target", "publish", "cancel", "status"))
_STATES = frozenset(("prepared", "awaiting_capture", "capturing", "rendering", "preview_ready", "target_ready",
                     "publishing", "published", "cancelling", "cancelled", "expired", "failed", "unknown"))
_TERMINAL = frozenset(("published", "cancelled", "expired", "failed", "unknown"))
# These are the publisher's fixed public codes, never exception text.
_PUBLISH_ERROR_CODES = frozenset((
    "INVALID_EXPORT_POLICY", "INVALID_EXPORT_PATH", "INVALID_EXPORT_DIRECTORY", "INVALID_EXPORT_TARGET",
    "INVALID_EXPORT_DATA", "EXPORT_TARGET_TOO_LARGE", "EXPORT_PLAN_INVALID", "EXPORT_TARGET_CHANGED",
    "OVERWRITE_REQUIRED", "EXPORT_DIRECTORY_FD_REQUIRED", "EXPORT_CLEANUP_FAILED", "EXPORT_FAILED",
))
TRANSLATABLE_STRINGS = (
    "确认 PDF 导出", "文档：%s", "图页：%s", "来源：%s", "原预览到期时间：%s",
    "原生捕获会打开并刷新本页视图和图框，可能改变显示、选择或对象状态；不会保存 CAD。",
    "打开并捕获 PDF 预览", "选择目标…", "尚未选择 PDF 目标。", "目标：%s", "SHA-256：%s",
    "大小：%s 字节", "确认发布 PDF", "此目标已存在；确认发布将覆盖文件。",
    "此目标不存在；确认发布将创建文件。", "请检查完整 PDF 预览，再选择目标并确认发布。",
    "正在捕获 PDF…", "正在生成 PDF 预览…", "正在发布 PDF…", "取消中，正在等待预览进程退出。",
    "PDF 导出已取消。", "PDF 导出计划已过期。", "PDF 已发布；CAD 文档未保存。",
    "PDF 发布结果不确定；请核对目标，不要重复导出。", "PDF 已发布，但暂存清理未确认；请核对结果。",
    "PDF 已发布，但 CAD 状态保持未确认；请检查文档。",
    "PDF 导出失败（%s）。", "选择 PDF 导出目标", "PDF 文件 (*.pdf)", "取消", "关闭",
)


def _qt():
    from PySide import QtCore
    try:
        from PySide import QtWidgets
    except ImportError:
        from PySide import QtGui as QtWidgets
    return QtCore, QtWidgets


def _process_module():
    import pft_pdf_qprocess
    return pft_pdf_qprocess


def _preview_module():
    import pft_pdf_preview_views
    return pft_pdf_preview_views


def _enum(owner, group, name):
    value = getattr(owner, name, None)
    if value is None:
        value = getattr(getattr(owner, group, None), name, None)
    if value is None:
        raise PftPdfError("PDF_BACKEND_UNAVAILABLE")
    return value


def _summary(value):
    keys = ("document", "page", "source", "expires_at")
    if (type(value) is not dict or any(type(value.get(key)) is not str
            or not 1 <= len(value[key]) <= 4096 for key in keys)
            or value.get("format", "pdf") != "pdf"):
        raise PftPdfError("PDF_PROTOCOL_INVALID")
    return {key: value[key] for key in keys}


def _path(value):
    return (type(value) is str and 0 < len(value) <= 4096 and os.path.isabs(value)
            and Path(value).suffix.lower() == ".pdf"
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _default_filename(value):
    return (_path(value) or (type(value) is str and 0 < len(value) <= 256
            and not any(char in "/\\" or ord(char) < 32 or ord(char) == 127 for char in value)
            and value not in (".", "..") and Path(value).suffix.lower() == ".pdf"))


def _fixed_code(value):
    if isinstance(value, PftPdfError):
        value = value.code
    return value if type(value) is str and value in PDF_ERROR_CODES else "PDF_RENDER_FAILED"


class _ExportHandle:
    def __init__(self, gui, summary, callbacks, translate, core, widgets):
        self.summary, self.callbacks = summary, callbacks
        self._tr, self.core, self.widgets = translate, core, widgets
        self.dialog = self.timer = self.file_dialog = self.renderer = self.preview = None
        self._closed = self._released = self._destroyed_flag = False
        self._calling = self._polling = self._deferred_close = False
        self._dismiss = self._cancel_called = self._cancel_requested = False
        self._capture_attempted = self._capture_pending = self._render_pending = False
        self._publish_attempted = self._publish_uncertain = self._failure_notified = False
        self._generation = self._selection_generation = 0
        self._queued_capture = self._expected = self._default_path = None
        self._identity = None
        self._pending_render_failure = None
        self._render_cancel_sent = False
        self._last = {"state": "prepared", "summary": dict(summary)}
        self._preview_verified = False
        owner = self

        class ExportDialog(widgets.QDialog):
            def accept(self): owner.close()
            def reject(self): owner.close()
            def closeEvent(self, event):
                owner.close()
                event.accept() if owner._closed else event.ignore()
            def keyPressEvent(self, event):
                if event.key() in tuple(_enum(core.Qt, "Key", name)
                                       for name in ("Key_Return", "Key_Enter", "Key_Escape")):
                    owner.close()
                    event.accept()
                else:
                    super().keyPressEvent(event)

        try:
            self.dialog = ExportDialog(gui.getMainWindow())
            self.dialog.setObjectName("pftDrawingExportDialog")
            self.dialog.setWindowTitle(translate("确认 PDF 导出"))
            self.dialog.setModal(False)
            self.dialog.setWindowModality(_enum(core.Qt, "WindowModality", "NonModal"))
            self.dialog.setMinimumWidth(0)
            self.dialog.resize(640, 700)
            layout = widgets.QVBoxLayout(self.dialog)
            for key, field, name in (("文档：%s", "document", "Document"), ("图页：%s", "page", "Page"),
                                      ("来源：%s", "source", "Source"),
                                      ("原预览到期时间：%s", "expires_at", "Expiry")):
                layout.addWidget(self._label(translate(key) % summary[field], name))
            layout.addWidget(self._label(translate("原生捕获会打开并刷新本页视图和图框，可能改变显示、选择或对象状态；不会保存 CAD。"), "Warning"))
            actions = widgets.QHBoxLayout()
            self.capture_button = self._button("打开并捕获 PDF 预览", "Capture", self._capture)
            self.choose_button = self._button("选择目标…", "Choose", self._choose)
            actions.addWidget(self.capture_button)
            actions.addWidget(self.choose_button)
            layout.addLayout(actions)
            self.preview = _preview_module().create_pdf_preview_widget(self.dialog)
            layout.addWidget(self.preview, 1)
            self.target_label = self._label(translate("尚未选择 PDF 目标。"), "Target")
            self.digest_label = self._label(translate("SHA-256：%s") % "—", "Digest")
            self.bytes_label = self._label(translate("大小：%s 字节") % "—", "Bytes")
            self.action_label = self._label("", "Action")
            self.status_label = self._label("", "Status")
            for label in (self.target_label, self.digest_label, self.bytes_label, self.action_label, self.status_label):
                layout.addWidget(label)
            buttons = widgets.QHBoxLayout()
            buttons.addStretch(1)
            self.publish_button = self._button("确认发布 PDF", "Publish", self._publish)
            self.cancel_button = self._button("取消", "Cancel", self.close)
            self.cancel_button.setAutoDefault(True)
            self.cancel_button.setDefault(True)
            self.cancel_button.setFocus()
            buttons.addWidget(self.publish_button)
            buttons.addWidget(self.cancel_button)
            layout.addLayout(buttons)
            self.renderer = _process_module().PftPdfPreviewProcess(core, self.dialog)
            self.dialog.finished.connect(self.close)
            self.dialog.destroyed.connect(self._destroyed)
            self.timer = core.QTimer(self.dialog)
            self.timer.timeout.connect(self._poll)
            self._display()
        except Exception:
            self.release()
            raise PftPdfError("PDF_BACKEND_UNAVAILABLE") from None

    def _label(self, text, suffix):
        value = self.widgets.QLabel(text, self.dialog)
        value.setObjectName("pftDrawingExport" + suffix)
        value.setTextFormat(_enum(self.core.Qt, "TextFormat", "PlainText"))
        value.setWordWrap(True)
        value.setMinimumWidth(0)
        policy = value.sizePolicy()
        ignored = _enum(type(policy), "Policy", "Ignored")
        policy.setHorizontalPolicy(ignored)
        value.setSizePolicy(policy)
        return value

    def _button(self, text, suffix, callback):
        value = self.widgets.QPushButton(self._tr(text), self.dialog)
        value.setObjectName("pftDrawingExport" + suffix)
        value.setAutoDefault(False)
        value.setDefault(False)
        value.clicked.connect(lambda *_: callback())
        return value

    def _call(self, name, *args):
        if self._calling:
            raise PftPdfError("PDF_BUSY")
        self._calling = True
        try:
            return self.callbacks[name](*args)
        finally:
            self._calling = False

    def _waiting(self):
        return self._capture_pending or (self.renderer is not None and self.renderer.has_active_session)

    def _cancel_renderer(self):
        if self.renderer is not None and self.renderer.has_active_session and not self._render_cancel_sent:
            self._render_cancel_sent = True
            try:
                self.renderer.cancel()
            except Exception:
                self._render_cancel_sent = False  # No claim about the child's exit.

    def _status(self, value):
        keys = {"document", "page", "format", "request_id", "token", "state", "expires_at", "storage",
                "saves_document", "requires_confirmation", "native_capture_confirmation",
                "native_publication_confirmation", "capture_state", "publication_state", "cleanup_state",
                "cad_protection_state", "summary", "byte_count", "sha256", "target", "target_exists", "error"}
        if (type(value) is not dict or value.get("state") not in _STATES
                or set(value) != keys or _summary(value.get("summary")) != self.summary
                or any(value.get(key) != self.summary[key] for key in ("document", "page", "expires_at"))
                or value.get("format") != "pdf" or value.get("storage") != "memory_only"
                or value.get("saves_document") is not False or value.get("requires_confirmation") is not True
                or any(type(value.get(key)) is not bool for key in
                       ("native_capture_confirmation", "native_publication_confirmation"))
                or value.get("capture_state") not in ("not_captured", "unknown", "captured")
                or value.get("cad_protection_state") not in ("not_checked", "verified", "unverified")
                or any(type(value.get(key)) is not str or not 1 <= len(value[key]) <= 256
                       for key in ("token", "request_id"))):
            raise PftPdfError("PDF_PROTOCOL_INVALID")
        identity = (value["token"], value["request_id"])
        if self._identity is not None and identity != self._identity:
            raise PftPdfError("PDF_PROTOCOL_INVALID")
        result = {"state": value["state"], "summary": dict(self.summary)}
        for key in ("byte_count", "sha256", "target", "target_exists", "publication_state", "cleanup_state", "error", "cad_protection_state"):
            result[key] = value.get(key)
        count, digest = result["byte_count"], result["sha256"]
        if ((count is not None and (type(count) is not int or not 1 <= count <= MAX_PDF_BYTES))
                or (digest is not None and (type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None))
                or (result["target"] is not None and not _path(result["target"]))
                or (result["target_exists"] is not None and type(result["target_exists"]) is not bool)
                or result["publication_state"] not in ("not_published", "published", "unknown")
                or result["cleanup_state"] not in ("complete", "unknown")):
            raise PftPdfError("PDF_PROTOCOL_INVALID")
        error = value["error"]
        if error is not None and (type(error) is not dict or set(error) != {"code", "message"}
                or type(error["code"]) is not str or not 1 <= len(error["code"]) <= 96
                or type(error["message"]) is not str or len(error["message"]) > 2048):
            raise PftPdfError("PDF_PROTOCOL_INVALID")
        if result["state"] in ("preview_ready", "target_ready", "publishing", "published"):
            if self._expected is None or (count, digest) != (self._expected["byte_count"], self._expected["input_sha256"]):
                raise PftPdfError("PDF_PROTOCOL_INVALID")
        if result["state"] == "target_ready" and (not _path(result["target"]) or type(result["target_exists"]) is not bool):
            raise PftPdfError("PDF_PROTOCOL_INVALID")
        self._identity = identity
        return result

    def _accept(self, value):
        self._last = self._status(value)
        if self._last["state"] == "cancelling":
            self._cancel_requested = True
            self._drop_file_dialog()
            self._cancel_renderer()
        if self._last["state"] in _TERMINAL:
            # An authoritative terminal business snapshot ends any queued
            # capture. A still-owned renderer is a separate lifetime below.
            self._capture_pending = False
            self._drop_file_dialog()
            self._cancel_renderer()
        self._display()
        self._settle()

    def _display(self):
        if self._released or self._destroyed_flag:
            return
        state = self._last["state"]
        waiting = self._waiting()
        active = not self._cancel_requested and not self._calling and self.file_dialog is None
        self.capture_button.setEnabled(active and not self._capture_attempted and state in ("prepared", "awaiting_capture"))
        self.choose_button.setEnabled(active and not waiting and self._preview_verified and not self._publish_attempted
                                      and state in ("preview_ready", "target_ready"))
        self.publish_button.setEnabled(active and not waiting and self._preview_verified and not self._publish_attempted
                                       and state == "target_ready")
        self.cancel_button.setText(self._tr("关闭" if state in _TERMINAL and not waiting else "取消"))
        self.cancel_button.setEnabled(not self._cancel_requested or not waiting)
        target, digest, count = (self._last.get(key) for key in ("target", "sha256", "byte_count"))
        self.target_label.setText(self._tr("目标：%s") % target if target is not None else self._tr("尚未选择 PDF 目标。"))
        self.digest_label.setText(self._tr("SHA-256：%s") % (digest if digest is not None else "—"))
        self.bytes_label.setText(self._tr("大小：%s 字节") % (count if count is not None else "—"))
        exists = self._last.get("target_exists")
        action = ("此目标已存在；确认发布将覆盖文件。" if exists is True else
                  "此目标不存在；确认发布将创建文件。" if exists is False else "")
        self.action_label.setText(self._tr(action) if action else "")
        if (self._cancel_requested or state in _TERMINAL) and waiting:
            message = "取消中，正在等待预览进程退出。"
        elif self._publish_uncertain and state != "published":
            message = "PDF 发布结果不确定；请核对目标，不要重复导出。"
        elif state == "published" and self._last.get("cleanup_state") == "unknown":
            message = "PDF 已发布，但暂存清理未确认；请核对结果。"
        else:
            message = {"capturing": "正在捕获 PDF…", "rendering": "正在生成 PDF 预览…",
                       "publishing": "正在发布 PDF…", "cancelling": "取消中，正在等待预览进程退出。",
                       "cancelled": "PDF 导出已取消。", "expired": "PDF 导出计划已过期。",
                       "published": "PDF 已发布；CAD 文档未保存。",
                       "unknown": "PDF 发布结果不确定；请核对目标，不要重复导出。",
                       "failed": "PDF 导出失败（%s）。"}.get(state, "请检查完整 PDF 预览，再选择目标并确认发布。")
        if message == "PDF 导出失败（%s）。":
            error = self._last.get("error")
            code = error.get("code") if type(error) is dict else error
            code = code if type(code) is str and (code in _PUBLISH_ERROR_CODES or
                re.fullmatch(r"(?:PDF|DRAWING_EXPORT)_[A-Z0-9_]{1,80}", code)) else "PDF_RENDER_FAILED"
            text = self._tr(message) % code
        else:
            text = self._tr(message)
        if state == "published" and self._last.get("cad_protection_state") == "unverified":
            warning = self._tr("PDF 已发布，但 CAD 状态保持未确认；请检查文档。")
            text += "\n" + warning
        self.status_label.setText(text)

    def _poll(self):
        if self._released or self._closed or self._calling or self._polling:
            return
        self._polling = True
        try:
            self._accept(self._call("status"))
        except Exception:
            self._fail("PDF_PROTOCOL_INVALID")
        finally:
            self._polling = False
            self._drain_close()

    def _fail(self, code):
        if self._publish_attempted:
            # A later UI/protocol failure cannot disprove a publication that
            # was already entered. Only an authoritative receipt can do that.
            if self._last["state"] not in _TERMINAL:
                self._publish_uncertain = True
            self._display()
            self._settle()
            return
        if self._failure_notified:
            return
        if self.renderer.has_active_session:
            self._pending_render_failure = _fixed_code(code)
            self._cancel_renderer()
            return  # The owned child's actual terminal callback reports failure.
        self._failure_notified = True
        if self._last["state"] not in _TERMINAL:
            try:
                self._accept(self._call("failed", _fixed_code(code)))
                return
            except Exception:
                self._last = {**self._last, "state": "failed", "error": "PDF_PROTOCOL_INVALID"}
        self._display()
        self._settle()

    def _capture(self):
        self._poll()
        if (self._released or self._calling or self._cancel_requested or self._capture_attempted
                or self._last["state"] not in ("prepared", "awaiting_capture")):
            return
        self._capture_attempted = self._capture_pending = True
        self._generation += 1
        generation = self._generation
        self._display()
        try:
            self._call("capture", lambda bundle: self._capture_delivery(generation, True, bundle),
                       lambda code: self._capture_delivery(generation, False, code))
        except Exception:
            self._queued_capture = (generation, False, "PDF_RENDER_FAILED")
        self._drain_close()
        queued, self._queued_capture = self._queued_capture, None
        if queued is not None:
            self._capture_delivery(*queued)
        self._poll()

    def _capture_delivery(self, generation, ready, value):
        # The controller must never mistake a UI exception after start() for
        # "no renderer started" and retire its render_active ownership early.
        try:
            self._capture_reply(generation, ready, value)
        except Exception:
            self._capture_pending = False
            try:
                if self.renderer.has_active_session:
                    self._render_pending = True
                    self._pending_render_failure = "PDF_RENDER_FAILED"
                    self._cancel_renderer()
                else:
                    self._fail("PDF_RENDER_FAILED")
            except Exception:
                pass  # Keep the transport's actual-terminal callback alive.

    def _capture_reply(self, generation, ready, value):
        if generation != self._generation or not self._capture_pending:
            return
        if self._calling:
            if self._queued_capture is None:
                self._queued_capture = (generation, ready, value)
            return
        self._capture_pending = False
        if self._released or self._cancel_requested or self._last["state"] in _TERMINAL:
            self._fail("PDF_CANCELLED")
            self._settle()
            return
        if not ready:
            self._fail(value)
            return
        try:
            if (type(value) is not dict or set(value) != {"bytes", "runtime", "default_path"}
                    or type(value["bytes"]) is not bytes or not 1 <= len(value["bytes"]) <= MAX_PDF_BYTES
                    or type(value["runtime"]) is not dict or not _default_filename(value["default_path"])):
                raise PftPdfError("PDF_PROTOCOL_INVALID")
            self._expected = {"input_sha256": hashlib.sha256(value["bytes"]).hexdigest(),
                              "byte_count": len(value["bytes"]), "width": 1000, "height": 1000}
            self._default_path = value["default_path"]
            self._accept(self._call("status"))
            self._drain_close()
            if self._released or self._cancel_requested or self._last["state"] in _TERMINAL:
                self._fail("PDF_CANCELLED")
                return
            if self._last["state"] != "rendering":
                raise PftPdfError("PDF_PROTOCOL_INVALID")
            self._render_pending = True
            self.renderer.start(value["runtime"], value["bytes"], 1000, 1000,
                                lambda reply: self._rendered(generation, reply),
                                lambda error: self._render_failed(generation, error))
        except Exception as error:
            if self.renderer.has_active_session:
                self._pending_render_failure = _fixed_code(error)
                self._cancel_renderer()
            else:
                self._render_pending = False
                self._fail(_fixed_code(error))
        self._display()

    def _rendered(self, generation, reply):
        if generation != self._generation or not self._render_pending:
            return
        self._render_pending = False
        if self._pending_render_failure is not None:
            self._fail(self._pending_render_failure)
            self._settle()
            return
        if self._released or self._cancel_requested or self._last["state"] in _TERMINAL:
            self._fail("PDF_CANCELLED")
            self._settle()
            return
        try:
            self._accept(self._call("rendered", reply))
            self._drain_close()
            if self._released or self._cancel_requested:
                return
            if self._last["state"] != "preview_ready":
                return
            self.preview.set_preview(reply)
            self._preview_verified = True
            self._display()
        except Exception as error:
            self._fail(_fixed_code(error))
        self._drain_close()

    def _render_failed(self, generation, error):
        if generation != self._generation or not self._render_pending:
            return
        self._render_pending = False
        self._fail(self._pending_render_failure or _fixed_code(error))
        self._settle()

    def _choose(self):
        self._poll()
        if (self._released or self._calling or self._cancel_requested or self.file_dialog is not None
                or not self._preview_verified or self._waiting() or self._publish_attempted
                or self._last["state"] not in ("preview_ready", "target_ready")):
            return
        try:
            dialog = self.widgets.QFileDialog(self.dialog)
            dialog.setObjectName("pftDrawingExportFileDialog")
            dialog.setWindowTitle(self._tr("选择 PDF 导出目标"))
            dialog.setAcceptMode(_enum(self.widgets.QFileDialog, "AcceptMode", "AcceptSave"))
            dialog.setFileMode(_enum(self.widgets.QFileDialog, "FileMode", "AnyFile"))
            dialog.setOption(_enum(self.widgets.QFileDialog, "Option", "DontConfirmOverwrite"), True)
            dialog.setNameFilter(self._tr("PDF 文件 (*.pdf)"))
            if os.path.isabs(self._default_path):
                dialog.setDirectory(str(Path(self._default_path).parent))
            dialog.selectFile(Path(self._default_path).name)
            dialog.setModal(False)
            self._selection_generation += 1
            generation = self._selection_generation
            self.file_dialog = dialog
            dialog.fileSelected.connect(lambda path: self._selected(dialog, generation, path))
            dialog.rejected.connect(lambda: self._selection_cancelled(dialog, generation))
            dialog.open()  # asynchronous; never exec()/getSaveFileName()
            self._display()
        except Exception:
            self._drop_file_dialog()
            self._fail("PDF_PROTOCOL_INVALID")

    def _selected(self, dialog, generation, path):
        if dialog is not self.file_dialog or generation != self._selection_generation:
            return
        self.file_dialog = None
        dialog.deleteLater()
        self._poll()
        if self._released or self._cancel_requested or self._last["state"] not in ("preview_ready", "target_ready"):
            return
        try:
            if not _path(path):
                raise PftPdfError("PDF_PROTOCOL_INVALID")
            self._accept(self._call("choose_target", path))
        except Exception as error:
            self._fail(_fixed_code(error))
        self._drain_close()

    def _selection_cancelled(self, dialog, generation):
        if dialog is self.file_dialog and generation == self._selection_generation:
            self.file_dialog = None
            dialog.deleteLater()
            self._display()

    def _drop_file_dialog(self):
        dialog, self.file_dialog = self.file_dialog, None
        self._selection_generation += 1
        if dialog is not None:
            try:
                dialog.reject()
                dialog.deleteLater()
            except RuntimeError:
                pass

    def _publish(self):
        self._poll()
        if (self._released or self._calling or self._cancel_requested or self._publish_attempted
                or not self._preview_verified or self._waiting() or self.file_dialog is not None
                or self._last["state"] != "target_ready"):
            return
        self._publish_attempted = True  # one-shot before any callback/native boundary
        self._display()
        try:
            value = self._call("publish")
            self._accept(value)
            if self._last["state"] not in _TERMINAL:
                self._publish_uncertain = True
        except Exception:
            # Publication may already have happened. Do not downgrade via failed()
            # or retry; only subsequent status can supply a confirmed receipt.
            self._publish_uncertain = True
        self._display()
        self._drain_close()

    def _cancel(self):
        if self._cancel_called:
            return
        self._cancel_called = self._cancel_requested = True
        self._drop_file_dialog()
        if self._last["state"] not in _TERMINAL | {"cancelling"} and not self._publish_uncertain:
            try:
                self._accept(self._call("cancel"))
            except Exception:
                # A failed callback is not a cancellation receipt. A queued
                # capture must still deliver; a child must actually finish.
                if self.renderer is not None and self.renderer.has_active_session:
                    self._pending_render_failure = "PDF_PROTOCOL_INVALID"
                elif not self._capture_pending:
                    self._fail("PDF_PROTOCOL_INVALID")
        self._cancel_renderer()
        self._display()
        self._settle()

    def _drain_close(self):
        if self._deferred_close and not self._calling:
            self._deferred_close = False
            self._cancel()
        self._settle()

    def _settle(self):
        if self._closed or not self._dismiss or self._calling or self._waiting():
            return
        self._closed = True
        if self.timer is not None:
            try: self.timer.stop()
            except RuntimeError: pass
        if not self._destroyed_flag and self.dialog is not None:
            try:
                if self.preview is not None: self.preview.clear_preview()
                self.dialog.hide()
                self.dialog.deleteLater()
            except RuntimeError:
                pass

    def _destroyed(self, *_):
        self._destroyed_flag = self._released = self._dismiss = True
        if self._calling:
            self._deferred_close = True
        else:
            self._cancel()

    def present(self):
        if self._closed or self._released:
            return
        self._poll()
        if self._closed or self._released or self._destroyed_flag:
            return
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        self.timer.start(250)

    def close(self, *_):
        if self._closed:
            return
        self._dismiss = True
        if self._calling:
            self._deferred_close = True
            return
        self._cancel()

    def release(self):
        if self._closed:
            return
        self._released = True
        if self.dialog is not None and not self._destroyed_flag:
            try:
                self.dialog.hide()
                if self.timer is not None: self.timer.stop()
            except RuntimeError:
                pass
        self.close()


def open_drawing_export_approval(gui, summary, callbacks, *, translate=tr):
    """Create the human-controlled view without capture, rendering or file writes."""
    summary = _summary(summary)
    if (type(callbacks) is not dict or set(callbacks) != _CALLBACKS
            or any(not callable(value) for value in callbacks.values()) or not callable(translate)):
        raise PftPdfError("PDF_PROTOCOL_INVALID")
    core, widgets = _qt()
    handle = _ExportHandle(gui, summary, dict(callbacks), translate, core, widgets)
    handle.present()
    return handle
