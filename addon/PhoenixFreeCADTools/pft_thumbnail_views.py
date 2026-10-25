# SPDX-License-Identifier: Apache-2.0
"""Explicit thumbnail preview/export UI; construction never reads CAD or files."""
from pathlib import Path

from pft_workbench_i18n import tr

TRANSLATABLE_STRINGS = (
    "缩略图", "整个当前可见三维视图，不按勾选过滤；不保存 CAD。", "尺寸", "背景", "白色", "当前背景", "透明背景",
    "轴测并适合窗口", "生成预览", "导出 PNG…", "尚未生成缩略图。", "生成中…",
    "激活此文档", "无法激活此文档，请先在 FreeCAD 中切换。",
    "仅支持当前活动文档；请先手动激活该文档。", "缩略图目标已变化，请重新生成预览。",
    "缩略图预览数据无效，请重新生成。", "已捕获 %s：%s × %s，时间 %s。",
    "缩略图生成失败；未保存 CAD 文档。", "PNG 文件 (*.png)", "缩略图导出确认", "目标：%s",
    "目标已存在，确认将覆盖该 PNG 文件。", "确认导出 PNG；不会保存 CAD 文档。", "确认覆盖并导出", "确认导出", "取消",
    "PNG 已导出；CAD 文档未保存。", "PNG 导出失败；未保存 CAD 文档。", "预览 PNG 与导出计划不一致，请重新生成。",
)


def _qt():
    from PySide import QtCore, QtGui
    try:
        from PySide import QtWidgets
    except ImportError:
        QtWidgets = QtGui
    return QtCore, QtGui, QtWidgets


def _capture_module():
    import pft_thumbnail_capture
    return pft_thumbnail_capture


def _export_module():
    import pft_thumbnail
    return pft_thumbnail


def _enum(owner, group, name):
    value = getattr(owner, name, None)
    return value if value is not None else getattr(getattr(owner, group, None), name, None)


def _shrink_horizontal(widget):
    widget.setMinimumWidth(0)
    policy = widget.sizePolicy()
    ignored = _enum(type(policy), "Policy", "Ignored")
    if ignored is not None:
        policy.setHorizontalPolicy(ignored)
        widget.setSizePolicy(policy)
    return widget


def _plain(label, core):
    mode = _enum(core.Qt, "TextFormat", "PlainText")
    if mode is not None:
        label.setTextFormat(mode)
    _shrink_horizontal(label)
    label.setWordWrap(True)
    return label


def _confirm_export(core, widgets, parent, plan):
    dialog = widgets.QDialog(parent)
    dialog.setObjectName("PhoenixTools_ThumbnailExportConfirmation")
    dialog.setWindowTitle(tr("缩略图导出确认"))
    layout = widgets.QVBoxLayout(dialog)
    layout.addWidget(_plain(widgets.QLabel(tr("目标：%s") % plan["path"]), core))
    message = "目标已存在，确认将覆盖该 PNG 文件。" if plan["exists"] else "确认导出 PNG；不会保存 CAD 文档。"
    layout.addWidget(_plain(widgets.QLabel(tr(message)), core))
    ok = _enum(widgets.QDialogButtonBox, "StandardButton", "Ok")
    cancel = _enum(widgets.QDialogButtonBox, "StandardButton", "Cancel")
    buttons = widgets.QDialogButtonBox(ok | cancel)
    buttons.button(ok).setText(tr("确认覆盖并导出") if plan["exists"] else tr("确认导出"))
    buttons.button(ok).setDefault(False)
    buttons.button(cancel).setText(tr("取消"))
    buttons.button(cancel).setDefault(True)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    execute = getattr(dialog, "exec", None) or dialog.exec_
    return execute() == _enum(widgets.QDialog, "DialogCode", "Accepted")


def create_thumbnail_widget(app, gui, parent, get_document_name, on_activate=None, get_export_filename=None):
    """Create an inert widget; set_document updates scope without CAD access.

    Capture is explicit and only available for the panel's active document.
    A stored preview is a frozen PNG, independent of later option/model changes.
    """
    core, qtgui, widgets = _qt()
    content = widgets.QWidget(parent)
    content.setObjectName("PhoenixTools_ThumbnailWidget")
    layout = widgets.QVBoxLayout(content)
    layout.addWidget(_plain(widgets.QLabel(tr("整个当前可见三维视图，不按勾选过滤；不保存 CAD。")), core))
    options = widgets.QHBoxLayout()
    options.addWidget(widgets.QLabel(tr("尺寸")))
    size = widgets.QComboBox(content)
    size.setObjectName("PhoenixTools_ThumbnailSize")
    for value in (256, 512, 1024):
        size.addItem(str(value) + " × " + str(value), value)
    size.setCurrentIndex(1)
    _shrink_horizontal(size)
    options.addWidget(size, 1)
    layout.addLayout(options)
    options = widgets.QHBoxLayout()
    options.addWidget(widgets.QLabel(tr("背景")))
    background = widgets.QComboBox(content)
    background.setObjectName("PhoenixTools_ThumbnailBackground")
    for title, value in (("白色", "white"), ("当前背景", "current"), ("透明背景", "transparent")):
        background.addItem(tr(title), value)
    background.setCurrentIndex(0)
    _shrink_horizontal(background)
    options.addWidget(background, 1)
    layout.addLayout(options)
    adjust = widgets.QCheckBox(tr("轴测并适合窗口"), content)
    adjust.setObjectName("PhoenixTools_ThumbnailAxonometricFit")
    adjust.setChecked(True)
    layout.addWidget(adjust)
    class ThumbnailPreview(widgets.QLabel):
        """Scale the frozen preview to its viewport without recapturing CAD."""
        def __init__(self):
            super().__init__(tr("尚未生成缩略图。"), content)
            self._source_pixmap = None

        def clear(self):
            self._source_pixmap = None
            super().clear()

        def show_pixmap(self, pixmap):
            self._source_pixmap = pixmap
            self._fit_pixmap()

        def _fit_pixmap(self):
            if self._source_pixmap is None:
                return
            rectangle = self.contentsRect()
            aspect = _enum(core.Qt, "AspectRatioMode", "KeepAspectRatio")
            smoothing = _enum(core.Qt, "TransformationMode", "SmoothTransformation")
            pixmap = self._source_pixmap
            if aspect is not None and smoothing is not None:
                pixmap = pixmap.scaled(max(1, min(320, rectangle.width())),
                                       max(1, min(320, rectangle.height())), aspect, smoothing)
            self.setPixmap(pixmap)

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self._fit_pixmap()

    preview = _plain(ThumbnailPreview(), core)
    preview.setObjectName("PhoenixTools_ThumbnailPreview")
    preview.setMinimumHeight(180)
    preview.setMaximumHeight(320)
    alignment = _enum(core.Qt, "AlignmentFlag", "AlignCenter")
    if alignment is not None:
        preview.setAlignment(alignment)
    layout.addWidget(preview, 1)
    status = _plain(widgets.QLabel(tr("尚未生成缩略图。"), content), core)
    status.setObjectName("PhoenixTools_ThumbnailStatus")
    layout.addWidget(status)
    actions = widgets.QHBoxLayout()
    activate = widgets.QPushButton(tr("激活此文档"), content)
    activate.setObjectName("PhoenixTools_ThumbnailActivateAction")
    layout.addWidget(activate)
    capture = widgets.QPushButton(tr("生成预览"), content)
    capture.setObjectName("PhoenixTools_ThumbnailCaptureAction")
    export = widgets.QPushButton(tr("导出 PNG…"), content)
    export.setObjectName("PhoenixTools_ThumbnailExportAction")
    actions.addWidget(capture)
    actions.addWidget(export)
    layout.addLayout(actions)
    state = {"document": None, "active": False, "preview": None, "busy": False}

    def available():
        activate.setVisible(bool(state["document"] and not state["active"] and callable(on_activate)))
        activate.setEnabled(bool(state["document"] and not state["active"] and not state["busy"] and callable(on_activate)))
        capture.setEnabled(bool(state["document"] and state["active"] and not state["busy"]))
        export.setEnabled(state["preview"] is not None and not state["busy"])

    def clear_preview(message="尚未生成缩略图。"):
        state["preview"] = None
        preview.clear()
        preview.setText(tr(message))
        status.setText(tr(message))
        available()

    def set_document(document_name, active=False):
        name = document_name if isinstance(document_name, str) and document_name else None
        changed = name != state["document"]
        active = bool(active and name)
        active_changed = active != state["active"]
        state["document"], state["active"] = name, active
        if changed or active_changed:
            clear_preview("尚未生成缩略图。" if state["active"] or name is None else "仅支持当前活动文档；请先手动激活该文档。")
        available()

    def target_unchanged():
        current = get_document_name()
        return current is None or current == state["document"]

    def activate_document():
        if state["busy"] or not state["document"] or state["active"] or not callable(on_activate):
            return
        document_name = state["document"]
        state["busy"] = True
        available()
        try:
            if not target_unchanged():
                clear_preview("缩略图目标已变化，请重新生成预览。")
                return
            confirmed = on_activate(document_name)
            if state["document"] != document_name:
                return
            if confirmed is not True:
                raise RuntimeError()
            set_document(document_name, active=True)
        except Exception:
            status.setText(tr("无法激活此文档，请先在 FreeCAD 中切换。"))
        finally:
            state["busy"] = False
            available()

    def generate():
        if state["busy"] or not state["document"] or not state["active"]:
            return
        module = None
        state["busy"] = True
        available()
        try:
            if not target_unchanged():
                clear_preview("缩略图目标已变化，请重新生成预览。")
                return
            clear_preview("生成中…")
            module = _capture_module()
            dimension = size.currentData()
            document_name = state["document"]
            value = module.capture_thumbnail(app, gui, document_name, dimension, dimension,
                                             background.currentData(), adjust.isChecked())
            if (not isinstance(value, dict) or type(value.get("png")) is not bytes or not value["png"]
                    or value.get("document") != document_name or not target_unchanged()
                    or state["document"] != document_name or not state["active"]
                    or type(value.get("width")) is not int or type(value.get("height")) is not int
                    or value["width"] != dimension or value["height"] != dimension
                    or not isinstance(value.get("captured_at"), str) or not value["captured_at"]):
                clear_preview("缩略图预览数据无效，请重新生成。")
                return
            pixmap = qtgui.QPixmap()
            if (not pixmap.loadFromData(value["png"], "PNG") or pixmap.width() != value["width"]
                    or pixmap.height() != value["height"]):
                clear_preview("缩略图预览数据无效，请重新生成。")
                return
            preview.show_pixmap(pixmap)
            state["preview"] = {key: value[key] for key in ("png", "document", "width", "height", "captured_at")}
            caption = tr("已捕获 %s：%s × %s，时间 %s。") % (
                value["document"], value["width"], value["height"], value["captured_at"])
            known_messages = getattr(module, "TRANSLATABLE_STRINGS", ())
            warnings = value.get("warnings", ())
            warnings = warnings if isinstance(warnings, (tuple, list)) else ()
            visible_warnings = [tr(message) for message in warnings if isinstance(message, str)
                                and message in known_messages][:3]
            status.setText("\n".join([caption] + visible_warnings))
        except Exception as error:
            message = error.message if isinstance(error, getattr(module, "PftThumbnailCaptureError", ())) else "缩略图生成失败；未保存 CAD 文档。"
            clear_preview(message)
        finally:
            state["busy"] = False
            available()

    def export_png():
        if state["busy"] or state["preview"] is None:
            return
        image = state["preview"]
        module = None
        try:
            options_flag = _enum(widgets.QFileDialog, "Option", "DontConfirmOverwrite")
            options = {} if options_flag is None else {"options": options_flag}
            default_filename = get_export_filename(image["document"]) if callable(get_export_filename) else ""
            choice = widgets.QFileDialog.getSaveFileName(content, tr("导出 PNG…"), default_filename, tr("PNG 文件 (*.png)"), **options)
            filename = choice[0] if isinstance(choice, (tuple, list)) else choice
            if not filename:
                return
            if state["preview"] is not image:
                return
            if not Path(str(filename)).suffix:
                filename = str(filename) + ".png"
            module = _export_module()
            plan = module.prepare_png_export(str(filename), image["png"])
            if plan is None:
                return
            if plan.get("data") != image["png"]:
                status.setText(tr("预览 PNG 与导出计划不一致，请重新生成。"))
                return
            if not _confirm_export(core, widgets, content, plan):
                return
            if state["preview"] is not image:
                return
            module.write_png_export(plan, overwrite=plan["exists"])
            status.setText(tr("PNG 已导出；CAD 文档未保存。"))
        except Exception as error:
            message = error.message if isinstance(error, getattr(module, "PftThumbnailError", ())) else "PNG 导出失败；未保存 CAD 文档。"
            status.setText(tr(message))

    activate.clicked.connect(lambda _checked=False: activate_document())
    capture.clicked.connect(lambda _checked=False: generate())
    export.clicked.connect(lambda _checked=False: export_png())
    content.set_document = set_document
    available()
    return content
