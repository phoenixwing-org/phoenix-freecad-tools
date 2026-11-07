# SPDX-License-Identifier: Apache-2.0
"""Display detached PDF preview pixels only; no capture, files, CAD or publishing."""
from pft_pdf_contract import PftPdfError, response_pixels, validate_pdf_response
from pft_workbench_i18n import tr

TRANSLATABLE_STRINGS = (
    "尚未生成 PDF 预览。", "适合窗口", "100%", "按实际像素显示，可滚动检查细节。",
)


def _qt():
    from PySide import QtCore, QtGui
    try:
        from PySide import QtWidgets
    except ImportError:
        QtWidgets = QtGui
    return QtCore, QtGui, QtWidgets


def _enum(owner, group, name):
    value = getattr(owner, name, None)
    return value if value is not None else getattr(getattr(owner, group, None), name, None)


def create_pdf_preview_widget(parent=None):
    """Create an inert widget with set_preview(reply) and clear_preview().

    The caller binds helper callbacks to its own request generation and widget
    lifetime. This component never starts work or captures a replacement image.
    """
    core, gui, widgets = _qt()

    class PdfPreview(widgets.QWidget):
        def __init__(self):
            super().__init__(parent)
            self.setObjectName("PhoenixTools_PdfPreviewWidget")
            self.setMinimumSize(0, 0)
            self._source_image = self._source_pixmap = None
            self._fit, self._updating = True, False
            layout = widgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            actions = widgets.QHBoxLayout()
            self._fit_button = widgets.QToolButton(self)
            self._fit_button.setObjectName("PhoenixTools_PdfPreviewFit")
            self._fit_button.setText(tr("适合窗口"))
            self._fit_button.setToolTip(tr("适合窗口"))
            self._fit_button.setCheckable(True)
            self._actual_button = widgets.QToolButton(self)
            self._actual_button.setObjectName("PhoenixTools_PdfPreviewActualSize")
            self._actual_button.setText(tr("100%"))
            self._actual_button.setToolTip(tr("按实际像素显示，可滚动检查细节。"))
            self._actual_button.setCheckable(True)
            self._fit_button.clicked.connect(self._select_fit)
            self._actual_button.clicked.connect(self._select_actual)
            actions.addWidget(self._fit_button)
            actions.addWidget(self._actual_button)
            self._status = widgets.QLabel(self)
            self._status.setObjectName("PhoenixTools_PdfPreviewStatus")
            plain = _enum(core.Qt, "TextFormat", "PlainText")
            if plain is not None:
                self._status.setTextFormat(plain)
            self._status.setMinimumWidth(0)
            self._status.setWordWrap(True)
            actions.addWidget(self._status, 1)
            layout.addLayout(actions)
            self._scroll = widgets.QScrollArea(self)
            self._scroll.setObjectName("PhoenixTools_PdfPreviewScroll")
            self._scroll.setMinimumSize(0, 0)
            self._scroll.setWidgetResizable(False)
            self._image = widgets.QLabel(self._scroll)
            self._image.setObjectName("PhoenixTools_PdfPreviewImage")
            self._image.setMinimumSize(0, 0)
            ignored = _enum(widgets.QSizePolicy, "Policy", "Ignored")
            if ignored is not None:
                self._image.setSizePolicy(ignored, ignored)
            center = _enum(core.Qt, "AlignmentFlag", "AlignCenter")
            if center is not None:
                self._image.setAlignment(center)
                self._scroll.setAlignment(center)
            self._scroll.setWidget(self._image)
            self._scroll.viewport().installEventFilter(self)
            layout.addWidget(self._scroll, 1)
            self.clear_preview()

        def _buttons(self):
            available = self._source_pixmap is not None
            self._fit_button.setEnabled(available)
            self._actual_button.setEnabled(available)
            self._fit_button.setChecked(self._fit)
            self._actual_button.setChecked(not self._fit)

        def _select_fit(self, *_):
            self._fit = True
            self._buttons()
            self._update_image()

        def _select_actual(self, *_):
            self._fit = False
            self._buttons()
            self._update_image()

        def _update_image(self):
            if self._updating:
                return
            self._updating = True
            try:
                source = self._source_pixmap
                if source is None or self._fit:
                    # Removing the 100% scrollbars can synchronously enlarge
                    # the viewport. Settle that geometry before scaling, even
                    # when its nested Resize event hit the reentrancy guard.
                    for _ in range(3):
                        rectangle = self._scroll.viewport().contentsRect()
                        width, height = max(1, rectangle.width()), max(1, rectangle.height())
                        self._image.resize(width, height)
                        current = self._scroll.viewport().contentsRect()
                        if (max(1, current.width()), max(1, current.height())) == (width, height):
                            break
                if source is None:
                    return
                if self._fit:
                    aspect = _enum(core.Qt, "AspectRatioMode", "KeepAspectRatio")
                    smoothing = _enum(core.Qt, "TransformationMode", "SmoothTransformation")
                    if aspect is None or smoothing is None:
                        raise PftPdfError("PDF_BACKEND_UNAVAILABLE")
                    self._image.setPixmap(source.scaled(width, height, aspect, smoothing))
                else:
                    self._image.setPixmap(source)
                    self._image.resize(source.width(), source.height())
            finally:
                self._updating = False

        def eventFilter(self, watched, event):
            if (watched is self._scroll.viewport()
                    and event.type() == _enum(core.QEvent, "Type", "Resize")):
                self._update_image()
            return super().eventFilter(watched, event)

        def clear_preview(self):
            self._source_image = self._source_pixmap = None
            self._fit = True
            self._image.clear()
            self._status.setText(tr("尚未生成 PDF 预览。"))
            self._buttons()
            self._update_image()

        def set_preview(self, reply):
            """Self-validate and own a QImage copy before handing it to QPixmap."""
            self.clear_preview()
            try:
                if type(reply) is not dict:
                    raise PftPdfError("PDF_INVALID_RESPONSE")
                value = validate_pdf_response(reply, input_sha256=reply.get("input_sha256"),
                                              byte_count=reply.get("byte_count"),
                                              width=reply.get("width"), height=reply.get("height"))
                pixels = response_pixels(value)
                image_format = _enum(gui.QImage, "Format", "Format_RGBA8888_Premultiplied")
                if image_format is None:
                    raise PftPdfError("PDF_BACKEND_UNAVAILABLE")
                image = gui.QImage(pixels, value["width"], value["height"],
                                   value["stride"], image_format).copy()
                if image.isNull():
                    raise PftPdfError("PDF_RENDER_FAILED")
                pixmap = gui.QPixmap.fromImage(image)
                if pixmap.isNull():
                    raise PftPdfError("PDF_RENDER_FAILED")
                self._source_image, self._source_pixmap = image, pixmap
                self._status.setText("")
                self._buttons()
                self._update_image()
            except Exception as error:
                self.clear_preview()
                if isinstance(error, PftPdfError):
                    raise
                raise PftPdfError("PDF_RENDER_FAILED") from None

        def closeEvent(self, event):
            self.clear_preview()
            super().closeEvent(event)

    return PdfPreview()
