# SPDX-License-Identifier: Apache-2.0
"""Thumbnail presentation and explicit export contract, without native GUI/CAD."""
import importlib.util
from pathlib import Path
import struct
import sys
import types
import unittest
from unittest.mock import Mock, patch
import zlib

from test_pft_document_tools_views import PftWidgets

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
_old_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_workbench_i18n as i18n
    import pft_thumbnail_views as views
finally:
    sys.path[:] = _old_path


def png(width=512, height=512):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    raw = (b"\0" + bytes((42, 84, 126)) * width) * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class ForbiddenNative:
    def __getattr__(self, name):
        raise AssertionError("native access forbidden: " + name)


class PftThumbnailViewTests(unittest.TestCase):
    def setUp(self):
        self.old_language = i18n.get_language(); i18n.set_language("zh")
        self.addCleanup(i18n.set_language, self.old_language)
        self.widgets = PftWidgets()
        def clear(label): label.text, label.pixmap = "", None
        def set_pixmap(label, pixmap): label.text, label.pixmap = "", pixmap
        self.widgets.QLabel.clear = clear
        self.widgets.QLabel.setPixmap = set_pixmap
        self.widgets.QLabel.setAlignment = lambda label, value: setattr(label, "alignment", value)
        class Pixmap:
            def loadFromData(self, data, format):
                self.data = data
                if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) < 24: return False
                self.dimensions = struct.unpack(">II", data[16:24]); return True
            def width(self): return self.dimensions[0]
            def height(self): return self.dimensions[1]
            def scaled(self, *args): self.scale_args = args; return self
        self.core = types.SimpleNamespace(Qt=types.SimpleNamespace(PlainText=0, AlignCenter=1,
            KeepAspectRatio=2, SmoothTransformation=3))
        qt_patch = patch.object(views, "_qt", return_value=(self.core,
            types.SimpleNamespace(QPixmap=Pixmap), self.widgets))
        qt_patch.start(); self.addCleanup(qt_patch.stop)
        class CaptureError(RuntimeError):
            def __init__(self, code, message): super().__init__(message); self.code, self.message = code, message
        class ExportError(RuntimeError):
            def __init__(self, code, message): super().__init__(message); self.code, self.message = code, message
        self.data = png()
        self.capture_value = {"png": self.data, "document": "PublicFixture", "width": 512,
                              "height": 512, "captured_at": "2026-01-01T12:00:00Z"}
        self.native = types.SimpleNamespace(PftThumbnailCaptureError=CaptureError,
            capture_thumbnail=Mock(side_effect=lambda *args: dict(self.capture_value)))
        self.exists = False
        self.exporter = types.SimpleNamespace(PftThumbnailError=ExportError,
            prepare_png_export=Mock(side_effect=lambda path, data: {"path": path, "exists": self.exists, "data": data}),
            write_png_export=Mock(return_value="/absolute/path/to/preview.png"))
        self.native_loader = Mock(return_value=self.native)
        self.export_loader = Mock(return_value=self.exporter)
        for name, loader in (("_capture_module", self.native_loader), ("_export_module", self.export_loader)):
            handle = patch.object(views, name, loader); handle.start(); self.addCleanup(handle.stop)
        self.app, self.gui = ForbiddenNative(), ForbiddenNative()
        self.target = "PublicFixture"
        self.get_target = Mock(side_effect=lambda: self.target)
        self.widget = views.create_thumbnail_widget(self.app, self.gui, self.widgets.QWidget(), self.get_target)

    def named(self, suffix): return self.widgets.named("PhoenixTools_Thumbnail" + suffix)
    def click(self, suffix): self.widgets.click("PhoenixTools_Thumbnail" + suffix)
    def generate(self): self.widget.set_document(self.target, active=True); self.click("CaptureAction")

    def test_construction_and_scope_update_are_inert_with_bounded_default_options(self):
        self.native_loader.assert_not_called(); self.export_loader.assert_not_called(); self.get_target.assert_not_called()
        self.assertFalse(self.named("CaptureAction").enabled); self.assertFalse(self.named("ExportAction").enabled)
        self.assertEqual(self.named("Size").rows, [("256 × 256", 256), ("512 × 512", 512), ("1024 × 1024", 1024)])
        self.assertEqual(self.named("Size").currentData(), 512)
        self.assertEqual(self.named("Background").currentData(), "white")
        self.assertTrue(self.named("AxonometricFit").isChecked())
        self.widget.set_document("PublicFixture", active=True)
        self.assertTrue(self.named("CaptureAction").enabled); self.assertFalse(self.named("ExportAction").enabled)
        self.native_loader.assert_not_called(); self.export_loader.assert_not_called(); self.get_target.assert_not_called()
        self.assertEqual((self.widgets.clipboard_calls, self.widgets.save_calls, self.widgets.exec_calls), (0, 0, 0))

    def test_reactivation_clears_stale_prompt_and_enables_capture_without_capturing(self):
        self.widget.set_document(self.target, active=False)
        self.assertIn("请先手动激活", self.named("Status").text)
        self.assertFalse(self.named("CaptureAction").enabled)
        self.widget.set_document(self.target, active=True)
        self.assertEqual(self.named("Status").text, "尚未生成缩略图。")
        self.assertEqual(self.named("Preview").text, "尚未生成缩略图。")
        self.assertTrue(self.named("CaptureAction").enabled)
        self.native_loader.assert_not_called()
        self.get_target.assert_not_called()
        self.assertEqual((self.widgets.exec_calls, self.widgets.save_calls), (0, 0))

    def test_activation_button_is_explicit_and_only_available_for_inactive_target(self):
        activate = Mock(return_value=True)
        self.widget = views.create_thumbnail_widget(self.app, self.gui, self.widgets.QWidget(),
                                                    self.get_target, on_activate=activate)
        self.widget.set_document(self.target, active=False)
        self.assertTrue(self.named("ActivateAction").visible)
        self.assertTrue(self.named("ActivateAction").enabled)
        activate.assert_not_called()
        self.click("ActivateAction")
        activate.assert_called_once_with(self.target)
        self.assertFalse(self.named("ActivateAction").visible)
        self.assertTrue(self.named("CaptureAction").enabled)
        self.assertEqual(self.named("Status").text, "尚未生成缩略图。")
        self.native_loader.assert_not_called()
        self.export_loader.assert_not_called()
        self.assertEqual((self.widgets.exec_calls, self.widgets.save_calls), (0, 0))

    def test_failed_or_stale_activation_does_not_capture_or_enable_capture(self):
        activate = Mock(side_effect=RuntimeError("private native details"))
        self.widget = views.create_thumbnail_widget(self.app, self.gui, self.widgets.QWidget(),
                                                    self.get_target, on_activate=activate)
        self.widget.set_document(self.target, active=False)
        self.click("ActivateAction")
        self.assertEqual(self.named("Status").text, "无法激活此文档，请先在 FreeCAD 中切换。")
        self.assertFalse(self.named("CaptureAction").enabled)
        self.assertTrue(self.named("ActivateAction").visible)
        # An unchanged follow poll must preserve the explicit activation error.
        self.widget.set_document(self.target, active=False)
        self.assertEqual(self.named("Status").text, "无法激活此文档，请先在 FreeCAD 中切换。")
        activate.reset_mock()
        self.target = "DifferentTarget"
        self.click("ActivateAction")
        activate.assert_not_called()
        self.native_loader.assert_not_called()

    def test_export_default_filename_comes_only_from_frozen_preview_target(self):
        filename = Mock(return_value="/fixture/CAD Part.png")
        self.widget = views.create_thumbnail_widget(self.app, self.gui, self.widgets.QWidget(),
                                                    self.get_target, get_export_filename=filename)
        self.generate()
        filename.assert_not_called()
        self.click("ExportAction")
        filename.assert_called_once_with(self.target)
        self.assertEqual(self.widgets.save_arguments[2], "/fixture/CAD Part.png")
        self.exporter.prepare_png_export.assert_not_called()
        self.assertEqual(self.native.capture_thumbnail.call_count, 1)

    def test_generation_is_explicit_and_uses_selected_options_for_whole_view(self):
        self.widget.set_document(self.target, active=True)
        self.named("Background").setCurrentIndex(2)
        self.named("AxonometricFit").setChecked(False)
        self.native.capture_thumbnail.assert_not_called()
        self.click("CaptureAction")
        self.native.capture_thumbnail.assert_called_once_with(self.app, self.gui, self.target,
                                                             512, 512, "transparent", False)
        self.assertEqual(self.named("Preview").pixmap.data, self.data)
        self.assertEqual(self.named("Preview").pixmap.scale_args, (320, 320, 2, 3))
        self.assertIn("PublicFixture", self.named("Status").text)
        self.assertIn("2026-01-01T12:00:00Z", self.named("Status").text)
        self.assertTrue(self.named("ExportAction").enabled)
        self.export_loader.assert_not_called()

    def test_narrowing_thumbnail_only_rescales_the_frozen_preview(self):
        self.generate()
        preview = self.named("Preview")
        self.assertEqual(preview.sizePolicy().horizontalPolicy(), self.widgets.QSizePolicy.Ignored)
        self.assertEqual(self.named("Status").sizePolicy().horizontalPolicy(), self.widgets.QSizePolicy.Ignored)
        preview.resize(240, 180)
        self.assertEqual(preview.pixmap.scale_args, (240, 180, 2, 3))
        self.assertEqual(preview.pixmap.data, self.data)
        preview.resize(400, 320)
        self.assertEqual(preview.pixmap.scale_args, (320, 320, 2, 3))
        self.assertEqual(self.native.capture_thumbnail.call_count, 1)
        self.assertTrue(self.named("ExportAction").enabled)
        self.export_loader.assert_not_called()
        self.widget.set_document(None, active=False)
        preview.resize(200, 180)
        self.assertIsNone(preview.pixmap)
        self.assertFalse(self.named("ExportAction").enabled)

    def test_same_document_preserves_preview_but_changed_invalid_or_inactive_scope_clears_it(self):
        self.generate()
        self.widget.set_document(self.target, active=True)
        self.assertTrue(self.named("ExportAction").enabled)
        for name, active in (("AnotherFixture", True), (None, False), ("PublicFixture", False)):
            self.widget.set_document(name, active=active)
            self.assertFalse(self.named("ExportAction").enabled)
            self.assertIsNone(self.named("Preview").pixmap)
        self.assertFalse(self.named("CaptureAction").enabled)
        self.assertEqual(self.native.capture_thumbnail.call_count, 1)

    def test_stale_target_and_capture_result_cannot_produce_exportable_preview(self):
        self.widget.set_document(self.target, active=True)
        self.target = "AnotherFixture"
        self.click("CaptureAction")
        self.native.capture_thumbnail.assert_not_called()
        self.assertFalse(self.named("ExportAction").enabled)
        self.target = "PublicFixture"
        self.capture_value["document"] = "AnotherFixture"
        self.click("CaptureAction")
        self.assertFalse(self.named("ExportAction").enabled)
        self.assertIn("预览数据无效", self.named("Status").text)

    def test_capture_detects_reentrant_generation_and_scope_change_during_capture(self):
        self.widget.set_document(self.target, active=True)
        def capture(*args):
            self.click("CaptureAction")
            self.widget.set_document("AnotherFixture", active=False)
            return dict(self.capture_value)
        self.native.capture_thumbnail.side_effect = capture
        self.click("CaptureAction")
        self.assertEqual(self.native.capture_thumbnail.call_count, 1)
        self.assertFalse(self.named("ExportAction").enabled)

    def test_invalid_dimensions_or_image_data_never_enable_export(self):
        for changes in ({"width": 256}, {"png": b"broken"}, {"captured_at": None}):
            self.capture_value.update(changes)
            self.generate()
            self.assertFalse(self.named("ExportAction").enabled)
            self.capture_value = {"png": self.data, "document": "PublicFixture", "width": 512,
                                  "height": 512, "captured_at": "2026-01-01T12:00:00Z"}

    def test_export_requires_preview_and_file_dialog_cancel_does_not_prepare_or_recapture(self):
        self.click("ExportAction")
        self.assertEqual(self.widgets.save_calls, 0)
        self.generate(); self.widgets.save_choice = ""
        self.click("ExportAction")
        self.export_loader.assert_not_called()
        self.assertEqual(self.native.capture_thumbnail.call_count, 1)
        self.assertEqual(self.widgets.save_options, {"options": 4})

    def test_confirmed_export_uses_frozen_visible_png_even_after_option_changes(self):
        self.generate()
        self.named("Size").setCurrentIndex(2); self.named("Background").setCurrentIndex(1)
        self.widgets.save_choice, self.widgets.preview_result = "/absolute/path/to/preview", 1
        self.click("ExportAction")
        self.exporter.prepare_png_export.assert_called_once_with("/absolute/path/to/preview.png", self.data)
        plan = self.exporter.write_png_export.call_args.args[0]
        self.assertEqual(plan["data"], self.named("Preview").pixmap.data)
        self.exporter.write_png_export.assert_called_once_with(plan, overwrite=False)
        self.assertEqual(self.native.capture_thumbnail.call_count, 1)
        self.assertEqual(self.named("Status").text, "PNG 已导出；CAD 文档未保存。")
        self.assertEqual(self.widgets.clipboard_calls, 0)

    def test_existing_export_defaults_to_cancel_and_confirmation_passes_fingerprint_plan_unchanged(self):
        self.generate(); self.exists = True
        self.widgets.save_choice, self.widgets.preview_result = "/absolute/path/to/preview.png", 0
        self.click("ExportAction")
        self.exporter.write_png_export.assert_not_called()
        boxes = [item for item in self.widgets.objects if isinstance(item, self.widgets.QDialogButtonBox)]
        self.assertTrue(boxes[-1].button(2).default); self.assertFalse(boxes[-1].button(1).default)
        plan = {"path": self.widgets.save_choice, "exists": True, "data": self.data, "fingerprint": object()}
        self.exporter.prepare_png_export.side_effect = None; self.exporter.prepare_png_export.return_value = plan
        self.widgets.preview_result = 1
        self.click("ExportAction")
        self.assertIs(self.exporter.write_png_export.call_args.args[0], plan)
        self.exporter.write_png_export.assert_called_once_with(plan, overwrite=True)
        self.assertEqual(self.native.capture_thumbnail.call_count, 1)

    def test_export_stops_if_preview_changes_during_confirmation_or_plan_changes_bytes(self):
        self.generate(); self.widgets.save_choice, self.widgets.preview_result = "/absolute/path/to/preview.png", 1
        self.exporter.prepare_png_export.side_effect = lambda path, data: {"path": path, "exists": False, "data": b"different"}
        self.click("ExportAction")
        self.exporter.write_png_export.assert_not_called(); self.assertEqual(self.widgets.exec_calls, 0)
        self.exporter.prepare_png_export.side_effect = lambda path, data: {"path": path, "exists": False, "data": data}
        self.widgets.on_preview = lambda: self.widget.set_document(None, active=False)
        self.click("ExportAction")
        self.exporter.write_png_export.assert_not_called()

    def test_only_known_capture_warnings_are_translated_and_displayed(self):
        i18n.set_language("en")
        warning = "窗口隐藏、最小化或未完成渲染时，预览可能为空白。"
        self.native.TRANSLATABLE_STRINGS = (warning,)
        self.capture_value["warnings"] = [warning, "private/path/untrusted detail"]
        self.generate()
        self.assertIn("The preview may be blank", self.named("Status").text)
        self.assertNotIn("private/path", self.named("Status").text)
        self.assertTrue(self.named("ExportAction").enabled)

    def test_errors_are_safe_and_fixed_messages_translate_without_native_calls(self):
        i18n.set_language("en")
        self.native.capture_thumbnail.side_effect = RuntimeError("private/path/credential detail")
        self.generate()
        self.assertEqual(self.named("Status").text, "Thumbnail capture failed. The CAD document was not saved.")
        self.assertNotIn("private", self.named("Status").text)
        for key in views.TRANSLATABLE_STRINGS:
            if any("\u4e00" <= char <= "\u9fff" for char in key):
                self.assertNotEqual(i18n.tr(key), key)


if __name__ == "__main__":
    unittest.main()
