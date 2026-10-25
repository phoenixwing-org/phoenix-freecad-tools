# SPDX-License-Identifier: Apache-2.0
"""Nonmodal native approval view. CAD access belongs to injected callbacks only."""
from collections import OrderedDict
import json
from html import escape
import importlib.util
from pathlib import Path


_RESULT_CONTRACT = None


_HANDLES = OrderedDict()
_MAX_HANDLES = 8
_STATES = frozenset(("prepared", "awaiting_confirmation", "applying", "applied", "cancelled", "failed", "expired"))
_ERROR_CODES = frozenset((
    "PART_CHANGE_FAILED", "PART_APPROVAL_UI_UNAVAILABLE", "PART_APPROVAL_NO_CHANGES", "PART_PREVIEW_BLOCKED",
    "PART_PLAN_STALE", "DOCUMENT_NOT_FOUND", "DOCUMENT_NOT_ACTIVE", "GUI_THREAD_REQUIRED",
    "PART_TRANSACTION_STATE_UNAVAILABLE", "PART_TRANSACTION_OPEN_UNVERIFIED", "PART_TRANSACTION_CHANGED",
    "PART_COMMIT_UNVERIFIED", "PART_ROLLBACK_UNVERIFIED", "PART_PROPERTY_VERIFY_FAILED",
    "PART_PREFERENCE_RESTORE_UNVERIFIED", "PART_ROLLBACK_AND_PREFERENCE_UNVERIFIED",
    "PAD_UNSUPPORTED", "PAD_PLAN_STALE", "PAD_PARAMETER_VERIFY_FAILED", "PAD_GEOMETRY_VERIFY_FAILED",
    "PAD_RECOMPUTE_UNVERIFIED", "PAD_NATIVE_CONFIRMATION_REQUIRED",
))
TRANSLATABLE_STRINGS = (
    "确认零件修改", "文档：%s", "操作：%s", "原预览到期时间：%s", "仅点击“确认修改”会修改文档；可撤销，不会自动保存。",
    "对象", "字段", "原值", "新值", "状态", "详情", "确认修改", "取消", "关闭",
    "等待原生确认；请检查全部预览行。", "正在修改，请等待结果。", "预览尚未进入确认状态。",
    "修改结果未确认（%s）。请检查文档和撤销记录；本窗口不会重试。",
)


def _qt():
    from PySide import QtCore
    try:
        from PySide import QtWidgets
    except ImportError:
        from PySide import QtGui as QtWidgets
    return QtCore, QtWidgets


def _enum(owner, group, name):
    value = getattr(owner, name, None)
    if value is None:
        value = getattr(getattr(owner, group, None), name, None)
    if value is None:
        raise RuntimeError("PART_APPROVAL_UI_UNAVAILABLE")
    return value


def _value(value):
    return value if type(value) is str else json.dumps(value, ensure_ascii=False, allow_nan=False)


def _pad_result(value, expected):
    global _RESULT_CONTRACT
    if _RESULT_CONTRACT is None:
        filename = Path(__file__).resolve().with_name("pft_pad_contract.py")
        spec = importlib.util.spec_from_file_location("_pft_approval_pad_contract", filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _RESULT_CONTRACT = module
    return _RESULT_CONTRACT.validate_design_result(value, expected)


def _preview_copy(preview):
    try:
        result = json.loads(json.dumps(preview, ensure_ascii=False, allow_nan=False))
        if (type(result) is not dict or any(type(result.get(key)) is not str or not result[key]
                                           for key in ("document", "operation", "token", "expires_at"))
                or type(result.get("can_apply")) is not bool
                or type(result.get("changed_count")) is not int or result["changed_count"] < 0
                or type(result.get("rows")) is not list
                or any(type(row) is not dict or not {"object", "field", "old", "new", "status"}.issubset(row)
                       for row in result["rows"])):
            raise ValueError()
        if result["operation"] == "set_pad_length":
            request = result.get("design_request")
            if (type(request) is not dict or set(request) != {"body", "pad", "length_mm"}
                    or any(type(request[key]) is not str or not request[key] for key in ("body", "pad"))
                    or type(request["length_mm"]) not in (int, float)
                    or not 0.01 <= request["length_mm"] <= 10000
                    or result["changed_count"] > 1 or len(result["rows"]) != 1
                    or result["rows"][0]["object"] != request["pad"]
                    or result["rows"][0].get("body") != request["body"]
                    or result["rows"][0].get("unit") != "mm"):
                raise ValueError()
        return result
    except Exception:
        raise ValueError("PART_APPROVAL_PREVIEW_INVALID") from None


class _ApprovalHandle:
    def __init__(self, gui, preview, on_confirm, on_cancel, read_status, translate, core, widgets):
        self.preview = preview
        self._on_confirm, self._on_cancel, self._read_status = on_confirm, on_cancel, read_status
        self._tr, self._core, self._widgets = translate, core, widgets
        self._closed = self._cancel_called = self._confirming = self._attempted = False
        self._close_requested = self._native_destroyed = self._polling = False
        self._state = "prepared"
        self.dialog = self.timer = None
        owner = self

        class ApprovalDialog(widgets.QDialog):
            # QDialog.accept()/accepted are deliberately never an approval grant.
            def accept(self): owner.close()
            def reject(self): owner.close()
            def closeEvent(self, event):
                owner.close()
                if owner._closed:
                    event.accept()
                else:
                    event.ignore()
            def keyPressEvent(self, event):
                keys = tuple(_enum(core.Qt, "Key", key) for key in ("Key_Return", "Key_Enter", "Key_Escape"))
                if event.key() in keys:
                    owner.close()
                    event.accept()
                else:
                    super().keyPressEvent(event)

        try:
            self.dialog = ApprovalDialog(gui.getMainWindow())
            self.dialog.setObjectName("pftPartApprovalDialog")
            self.dialog.setWindowTitle(translate("确认零件修改"))
            self.dialog.setModal(False)
            self.dialog.setWindowModality(_enum(core.Qt, "WindowModality", "NonModal"))
            self.dialog.setMinimumWidth(0)
            self.dialog.resize(720, 420)
            layout = widgets.QVBoxLayout(self.dialog)
            layout.setContentsMargins(12, 12, 12, 12)
            for key, value in (("文档：%s", preview["document"]), ("操作：%s", preview["operation"]),
                               ("原预览到期时间：%s", preview["expires_at"])):
                layout.addWidget(self._label(translate(key) % value))
            layout.addWidget(self._label(translate("仅点击“确认修改”会修改文档；可撤销，不会自动保存。")))
            self.table = widgets.QTableWidget(len(preview["rows"]), 6, self.dialog)
            self.table.setObjectName("pftPartApprovalRows")
            self.table.setMinimumWidth(0)
            self.table.setHorizontalHeaderLabels([translate(text) for text in ("对象", "字段", "原值", "新值", "状态", "详情")])
            self.table.setEditTriggers(_enum(widgets.QAbstractItemView, "EditTrigger", "NoEditTriggers"))
            self.table.setWordWrap(False)
            self.table.setTextElideMode(_enum(core.Qt, "TextElideMode", "ElideNone"))
            self.table.setHorizontalScrollBarPolicy(_enum(core.Qt, "ScrollBarPolicy", "ScrollBarAsNeeded"))
            flags = (_enum(core.Qt, "ItemFlag", "ItemIsEnabled") | _enum(core.Qt, "ItemFlag", "ItemIsSelectable"))
            columns = ("object", "field", "old", "new", "status")
            for index, row in enumerate(preview["rows"]):
                detail = {key: value for key, value in row.items() if key not in columns}
                values = [row[key] for key in columns] + [detail if detail else ""]
                for column, value in enumerate(values):
                    text = _value(value)
                    item = widgets.QTableWidgetItem(text)
                    item.setFlags(flags)
                    item.setToolTip("<pre>" + escape(text) + "</pre>")
                    self.table.setItem(index, column, item)
            # Full cell text determines column widths; scroll within the compact dialog.
            self.table.resizeColumnsToContents()
            layout.addWidget(self.table)
            self.status_label = self._label(translate("预览尚未进入确认状态。"))
            self.status_label.setObjectName("pftPartApprovalStatus")
            layout.addWidget(self.status_label)
            buttons = widgets.QHBoxLayout()
            buttons.addStretch(1)
            self.confirm_button = widgets.QPushButton(translate("确认修改"), self.dialog)
            self.confirm_button.setObjectName("pftPartApprovalConfirm")
            self.confirm_button.setAutoDefault(False)
            self.confirm_button.setDefault(False)
            self.confirm_button.setEnabled(False)
            self.cancel_button = widgets.QPushButton(translate("取消"), self.dialog)
            self.cancel_button.setObjectName("pftPartApprovalCancel")
            self.cancel_button.setAutoDefault(True)
            self.cancel_button.setDefault(True)
            self.cancel_button.setFocus()
            buttons.addWidget(self.confirm_button)
            buttons.addWidget(self.cancel_button)
            layout.addLayout(buttons)
            self.confirm_button.clicked.connect(self._confirm)
            self.cancel_button.clicked.connect(self.close)
            self.dialog.finished.connect(self.close)
            self.dialog.destroyed.connect(self._destroyed)
            self.timer = core.QTimer(self.dialog)
            self.timer.timeout.connect(self._poll)
        except Exception:
            self._finish(cancel=True)
            raise RuntimeError("PART_APPROVAL_UI_UNAVAILABLE") from None

    def _label(self, text):
        label = self._widgets.QLabel(text, self.dialog)
        label.setTextFormat(_enum(self._core.Qt, "TextFormat", "PlainText"))
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setToolTip("<pre>" + escape(text) + "</pre>")
        return label

    def _status(self, status):
        if (type(status) is not dict or status.get("state") not in _STATES
                or any(status.get(key) != self.preview[key] for key in ("document", "operation", "token"))
                or type(status.get("changed_count")) is not int
                or status["changed_count"] < 0
                or (status["state"] != "applied" and status["changed_count"] != self.preview["changed_count"])
                or status.get("requires_confirmation") is not True or status.get("saves_document") is not False
                or status.get("storage") != "memory_only" or type(status.get("native_confirmation")) is not bool
                or (status["state"] in {"prepared", "awaiting_confirmation", "cancelled", "expired"}
                    and status["native_confirmation"])):
            raise ValueError("PART_APPROVAL_STATUS_INVALID")
        if status["state"] == "applied":
            result = status.get("result")
            if (status["native_confirmation"] is not True or type(result) is not dict
                    or type(result.get("changed_count")) is not int
                    or result["changed_count"] != status["changed_count"] or result["changed_count"] > 500
                    or result.get("saves_document") is not False):
                raise ValueError("PART_APPROVAL_STATUS_INVALID")
        if self.preview["operation"] == "set_pad_length":
            if status["changed_count"] > 1 or (status["state"] in {"applying", "applied"}
                    and (status["native_confirmation"] is not True or status["changed_count"] != 1)):
                raise ValueError("PART_APPROVAL_STATUS_INVALID")
            if status["state"] == "applied":
                if set(status["result"]) != {"changed_count", "saves_document", "design_result"}:
                    raise ValueError("PART_APPROVAL_STATUS_INVALID")
                _pad_result(status["result"]["design_result"], self.preview.get("design_request"))
        return status

    def _consume(self, status):
        if self._closed:
            return
        status = self._status(status)
        state = self._state = status["state"]
        if state == "applied":
            self._finish(cancel=False)
        elif state in ("cancelled", "expired"):
            self._finish(cancel=False)
        elif state == "failed":
            self._fail(status.get("error_code"))
        elif state == "applying":
            self.table.setEnabled(False)
            self.confirm_button.setEnabled(False)
            self.cancel_button.setEnabled(False)
            self.status_label.setText(self._tr("正在修改，请等待结果。"))
        elif self._attempted:
            # No second write after an ambiguous callback outcome.
            self._fail("PART_CHANGE_FAILED")
        elif self._close_requested:
            self._finish(cancel=True)
        else:
            self.confirm_button.setEnabled(state == "awaiting_confirmation" and self.preview["can_apply"] is True
                                           and self.preview["changed_count"] > 0)
            self.cancel_button.setEnabled(True)
            text = "等待原生确认；请检查全部预览行。" if state == "awaiting_confirmation" else "预览尚未进入确认状态。"
            self.status_label.setText(self._tr(text))

    def _fail(self, code):
        if self._closed:
            return
        self._state = "failed"
        if self._close_requested:
            self._finish(cancel=True)
            return
        self.timer.stop()
        code = code if type(code) is str and code in _ERROR_CODES else "PART_CHANGE_FAILED"
        self.status_label.setText(self._tr("修改结果未确认（%s）。请检查文档和撤销记录；本窗口不会重试。") % code)
        self.confirm_button.setEnabled(False)
        self.table.setEnabled(False)
        self.cancel_button.setText(self._tr("关闭"))
        self.cancel_button.setEnabled(True)
        self.cancel_button.setFocus()

    def _poll(self, *_):
        if self._closed or self._confirming or self._polling or self._state == "failed":
            return
        self._polling = True
        try:
            self._consume(self._read_status())
        except Exception:
            self._finish(cancel=True)
        finally:
            self._polling = False

    def _confirm(self, *_):
        if self._closed or self._confirming or self._polling or self._attempted or self._state != "awaiting_confirmation":
            return
        if not self.preview["can_apply"] or self.preview["changed_count"] <= 0:
            return
        # Guard the status precheck too: an injected callback cannot reenter approval.
        self._confirming = True
        try:
            try:
                self._consume(self._read_status())
            except Exception:
                self._finish(cancel=True)
                return
            if self._closed or self._state != "awaiting_confirmation":
                return
            self._attempted = True
            self._state = "applying"
            self.timer.stop()
            self.confirm_button.setEnabled(False)
            self.cancel_button.setEnabled(False)
            self.table.setEnabled(False)
            self.status_label.setText(self._tr("正在修改，请等待结果。"))
            try:
                # Synchronous only: no exec(), processEvents() or nested event loop.
                status = self._on_confirm()
            except Exception:
                status = None
        except Exception:
            self._finish(cancel=True)
            return
        finally:
            self._confirming = False
        if self._closed:
            return
        try:
            self._consume(status)
            if not self._closed and self._state == "applying":
                self.timer.start(1000)
        except Exception:
            self._fail("PART_CHANGE_FAILED")

    def present(self):
        if self._closed or self._confirming:
            return self
        try:
            self._poll()
            if not self._closed:
                self.dialog.show()
                self.dialog.raise_()
                self.dialog.activateWindow()
                if self._state != "failed":
                    self.timer.start(1000)
        except Exception:
            self._finish(cancel=True)
        return self

    def close(self, *_):
        if self._closed:
            return
        if self._confirming or self._state == "applying":
            self._close_requested = True
            return
        self._finish(cancel=True)

    def _destroyed(self, *_):
        self._native_destroyed = True
        self._finish(cancel=True)

    def _finish(self, cancel):
        if self._closed:
            return
        self._closed = True
        _HANDLES.pop(id(self), None)
        if self.timer is not None:
            try:
                self.timer.stop()
            except Exception:
                pass
        callback = self._on_cancel if cancel and not self._cancel_called else None
        self._cancel_called = True
        self._on_confirm = self._on_cancel = self._read_status = None
        if callback is not None:
            try:
                callback()
            except Exception:
                pass
        if self.dialog is not None and not self._native_destroyed:
            try:
                self.dialog.hide()
                self.dialog.deleteLater()
            except Exception:
                pass


def open_part_approval(gui, preview, on_confirm, on_cancel, read_status, translate=None):
    """Present an owned nonmodal view; only its explicit button invokes on_confirm."""
    if not all(callable(callback) for callback in (on_confirm, on_cancel, read_status)) or (translate is not None and not callable(translate)):
        raise ValueError("PART_APPROVAL_CALLBACK_INVALID")
    snapshot = _preview_copy(preview)
    if len(_HANDLES) >= _MAX_HANDLES:
        raise RuntimeError("PART_APPROVAL_UI_UNAVAILABLE")
    try:
        core, widgets = _qt()
    except Exception:
        raise RuntimeError("PART_APPROVAL_UI_UNAVAILABLE") from None
    handle = _ApprovalHandle(gui, snapshot, on_confirm, on_cancel, read_status, translate if translate is not None else (lambda text: text), core, widgets)
    if not handle._closed:
        _HANDLES[id(handle)] = handle
    return handle.present()
