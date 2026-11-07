# SPDX-License-Identifier: Apache-2.0
"""Owned-image and viewport tests with injected Qt; no native window or files."""
import base64
from copy import deepcopy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools"
_old_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_pdf_preview_views as views
    import pft_pdf_contract as contract
finally:
    sys.path[:] = _old_path


def success():
    return {"ok": True, "backend": "coregraphics", "input_sha256": "a" * 64,
            "byte_count": 100, "page_count": 1, "media_box_points": [0, 0, 200, 100],
            "rotation_degrees": 0, "user_unit": 1, "width": 1000, "height": 1000,
            "stride": 4000, "pixel_format": "rgba8888-premultiplied",
            "pixels_base64": base64.b64encode(b"\x12\x34\x56\xff" * 1_000_000).decode("ascii")}


class Signal:
    def connect(self, callback): self.callback = callback
    def emit(self): self.callback(False)


class Widget:
    def __init__(self, parent=None):
        self.parent, self.children, self.name = parent, [], ""
        self.width_value, self.height_value = 640, 400
        self.enabled = True
        if parent is not None: parent.children.append(self)
    def setObjectName(self, value): self.name = value
    def setMinimumSize(self, *args): self.minimum = args
    def setMinimumWidth(self, value): self.minimum_width = value
    def resize(self, width, height): self.width_value, self.height_value = width, height
    def setEnabled(self, value): self.enabled = value
    def contentsRect(self):
        return types.SimpleNamespace(width=lambda: self.width_value, height=lambda: self.height_value)
    def installEventFilter(self, owner): self.filter = owner
    def eventFilter(self, watched, event): return False
    def closeEvent(self, event): self.closed = True


class Label(Widget):
    def setTextFormat(self, value): self.text_format = value
    def setWordWrap(self, value): self.word_wrap = value
    def setSizePolicy(self, *args): self.policy = args
    def setAlignment(self, value): self.alignment = value
    def setText(self, value): self.text = value
    def setPixmap(self, value): self.pixmap = value
    def clear(self): self.pixmap, self.text = None, ""


class Button(Widget):
    def __init__(self, parent=None): super().__init__(parent); self.clicked = Signal()
    def setText(self, value): self.text = value
    def setToolTip(self, value): self.tooltip = value
    def setCheckable(self, value): self.checkable = value
    def setChecked(self, value): self.checked = value


class Layout:
    def __init__(self, *args): self.items = []
    def setContentsMargins(self, *args): self.margins = args
    def addWidget(self, *args): self.items.append(args)
    def addLayout(self, layout): self.items.append(layout)


class Scroll(Widget):
    def __init__(self, parent=None): super().__init__(parent); self.port = Widget(self)
    def viewport(self): return self.port
    def setWidgetResizable(self, value): self.widget_resizable = value
    def setAlignment(self, value): self.alignment = value
    def setWidget(self, value): self.widget = value


class Image:
    Format_RGBA8888_Premultiplied = 18
    calls, force_null, throw = [], False, False
    def __init__(self, pixels, width, height, stride, image_format):
        if self.throw: raise RuntimeError("private-untrusted-image-diagnostic")
        self.pixels, self.w, self.h, self.stride, self.image_format = pixels, width, height, stride, image_format
        self.owned = False
        self.calls.append(self)
    def copy(self):
        image = object.__new__(Image)
        image.pixels, image.w, image.h = bytes(bytearray(self.pixels)), self.w, self.h
        image.stride, image.image_format, image.owned = self.stride, self.image_format, True
        return image
    def isNull(self): return self.force_null


class Pixmap:
    force_null = False
    @classmethod
    def fromImage(cls, image):
        if not image.owned: raise AssertionError("QImage must own a copy before QPixmap")
        value = cls(); value.image, value.w, value.h, value.scales = image, image.w, image.h, []
        return value
    def isNull(self): return self.force_null
    def width(self): return self.w
    def height(self): return self.h
    def scaled(self, width, height, aspect, smoothing):
        self.scales.append((width, height, aspect, smoothing))
        value = Pixmap(); value.image, value.scales = self.image, self.scales
        factor = min(width / self.w, height / self.h)
        value.w, value.h = int(self.w * factor), int(self.h * factor)
        return value


class PdfPreviewViewTests(unittest.TestCase):
    def setUp(self):
        Image.calls, Image.force_null, Image.throw, Pixmap.force_null = [], False, False, False
        core = types.SimpleNamespace(Qt=types.SimpleNamespace(PlainText=0, AlignCenter=1,
                                     KeepAspectRatio=2, SmoothTransformation=3),
                                     QEvent=types.SimpleNamespace(Resize=14))
        gui = types.SimpleNamespace(QImage=Image, QPixmap=Pixmap)
        widgets = types.SimpleNamespace(QWidget=Widget, QLabel=Label, QToolButton=Button,
                                        QVBoxLayout=Layout, QHBoxLayout=Layout, QScrollArea=Scroll,
                                        QSizePolicy=types.SimpleNamespace(Ignored=0))
        handle = patch.object(views, "_qt", return_value=(core, gui, widgets))
        handle.start(); self.addCleanup(handle.stop)
        self.translation = patch.object(views, "tr", side_effect=lambda text: text)
        self.translation.start(); self.addCleanup(self.translation.stop)
        self.parent = Widget()
        self.widget = views.create_pdf_preview_widget(self.parent)

    def named(self, suffix):
        pending = [self.widget]
        while pending:
            value = pending.pop()
            if value.name == "PhoenixTools_PdfPreview" + suffix: return value
            pending.extend(value.children)
        self.fail("missing named object: " + suffix)

    def resize_viewport(self, width, height):
        viewport = self.named("Scroll").viewport()
        viewport.resize(width, height)
        self.widget.eventFilter(viewport, types.SimpleNamespace(type=lambda: 14))

    def test_construct_is_inert_and_small_with_disabled_controls(self):
        self.assertIs(self.widget.parent, self.parent)
        self.assertEqual(self.widget.minimum, (0, 0))
        self.assertEqual(Image.calls, [])
        self.assertFalse(self.named("Fit").enabled)
        self.assertFalse(self.named("ActualSize").enabled)
        self.assertEqual(self.named("Status").text, "尚未生成 PDF 预览。")
        self.assertEqual(self.named("Image").policy, (0, 0))
        self.assertFalse(self.named("Scroll").widget_resizable)

    def test_self_validated_pixels_become_owned_qimage_copy_before_pixmap(self):
        reply = success()
        self.widget.set_preview(reply)
        self.assertEqual(len(Image.calls), 1)
        borrowed = Image.calls[0]
        self.assertEqual((borrowed.w, borrowed.h, borrowed.stride, borrowed.image_format), (1000, 1000, 4000, 18))
        self.assertTrue(self.widget._source_image.owned)
        self.assertIsNot(self.widget._source_image, borrowed)
        self.assertIsNot(self.widget._source_image.pixels, borrowed.pixels)
        reply["pixels_base64"] = "changed-after-display"
        reply["media_box_points"][2] = 1
        self.assertEqual(self.widget._source_image.pixels[:4], b"\x12\x34\x56\xff")
        self.assertTrue(self.named("Fit").checked)
        self.assertFalse(self.named("ActualSize").checked)
        self.assertEqual(self.named("Status").text, "")

    def test_fit_tracks_viewport_without_redecoding_or_replacing_frozen_source(self):
        self.widget.set_preview(success())
        source = self.widget._source_pixmap
        self.assertEqual(source.scales[-1], (640, 400, 2, 3))
        self.assertEqual((self.named("Image").pixmap.w, self.named("Image").pixmap.h), (400, 400))
        self.resize_viewport(220, 180)
        self.assertEqual(source.scales[-1], (220, 180, 2, 3))
        self.assertEqual((self.named("Image").width_value, self.named("Image").height_value), (220, 180))
        self.assertIs(self.widget._source_pixmap, source)
        self.assertEqual(len(Image.calls), 1)

    def test_actual_size_is_scrollable_and_fit_can_be_restored_without_rendering(self):
        self.widget.set_preview(success())
        source = self.widget._source_pixmap
        self.named("ActualSize").clicked.emit()
        self.assertIs(self.named("Image").pixmap, source)
        self.assertEqual((self.named("Image").width_value, self.named("Image").height_value), (1000, 1000))
        self.assertTrue(self.named("ActualSize").checked)
        self.assertFalse(self.named("Fit").checked)
        self.resize_viewport(200, 150)
        self.assertIs(self.named("Image").pixmap, source)
        self.assertEqual((self.named("Image").width_value, self.named("Image").height_value), (1000, 1000))
        self.named("Fit").clicked.emit()
        self.assertEqual(source.scales[-1], (200, 150, 2, 3))
        self.assertEqual(len(Image.calls), 1)

    def test_clear_removes_all_pixels_and_resize_does_not_resurrect_them(self):
        self.widget.set_preview(success())
        self.named("ActualSize").clicked.emit()
        self.widget.clear_preview()
        self.assertIsNone(self.widget._source_image)
        self.assertIsNone(self.widget._source_pixmap)
        self.assertIsNone(self.named("Image").pixmap)
        self.assertFalse(self.named("Fit").enabled)
        self.assertFalse(self.named("ActualSize").enabled)
        self.assertTrue(self.named("Fit").checked)
        self.resize_viewport(160, 120)
        self.assertIsNone(self.named("Image").pixmap)

    def test_fit_settles_synchronous_viewport_resize_when_scrollbars_disappear(self):
        self.widget.set_preview(success())
        self.named("ActualSize").clicked.emit()
        image = self.named("Image")
        viewport = self.named("Scroll").viewport()
        viewport.resize(620, 380)
        original_resize, calls = image.resize, []
        def resize(width, height):
            calls.append((width, height))
            original_resize(width, height)
            if len(calls) == 1:
                viewport.resize(640, 400)
                self.widget.eventFilter(viewport, types.SimpleNamespace(type=lambda: 14))
        image.resize = resize
        self.named("Fit").clicked.emit()
        self.assertEqual(calls, [(620, 380), (640, 400)])
        self.assertEqual(self.widget._source_pixmap.scales[-1], (640, 400, 2, 3))

    def test_invalid_reply_clears_previous_image_before_fixed_error(self):
        for change in (lambda r: r.update(width=True), lambda r: r.update(user_unit=2),
                       lambda r: r.update(pixels_base64="private-untrusted"),
                       lambda r: r.update(extra="private-untrusted")):
            self.widget.set_preview(success())
            count = len(Image.calls)
            reply = success(); change(reply)
            with self.assertRaises(contract.PftPdfError) as failure: self.widget.set_preview(reply)
            self.assertEqual(failure.exception.code, "PDF_INVALID_RESPONSE")
            self.assertIsNone(self.named("Image").pixmap)
            self.assertEqual(len(Image.calls), count)
        for reply in (None, [], {"ok": False, "code": "PDF_ENCRYPTED"}):
            with self.assertRaises(contract.PftPdfError): self.widget.set_preview(reply)

    def test_qimage_null_pixmap_null_and_exception_clear_pixels_without_raw_error(self):
        for target, key in ((Image, "force_null"), (Image, "throw"), (Pixmap, "force_null")):
            setattr(target, key, True)
            with self.assertRaises(contract.PftPdfError) as failure: self.widget.set_preview(success())
            self.assertEqual(failure.exception.code, "PDF_RENDER_FAILED")
            self.assertNotIn("private-untrusted", str(failure.exception))
            self.assertIsNone(self.named("Image").pixmap)
            self.assertIsNone(self.widget._source_image)
            setattr(target, key, False)

    def test_close_clears_owned_image_and_no_deferred_work_exists(self):
        self.widget.set_preview(success())
        self.widget.closeEvent(object())
        self.assertTrue(self.widget.closed)
        self.assertIsNone(self.widget._source_image)
        self.assertIsNone(self.named("Image").pixmap)
        self.assertEqual(len(Image.calls), 1)

    def test_nested_qt_enum_groups_are_supported(self):
        owner = types.SimpleNamespace(Format=types.SimpleNamespace(Format_RGBA8888_Premultiplied=18))
        self.assertEqual(views._enum(owner, "Format", "Format_RGBA8888_Premultiplied"), 18)


if __name__ == "__main__":
    unittest.main()
