# SPDX-License-Identifier: Apache-2.0
"""Neutral frozen draft DTOs and injected Qt; no FreeCAD or user state."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import patch

import test_pft_drawing_layout_views as doubles

ADDON = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools"
spec = importlib.util.spec_from_file_location("neutral_draft_layout_views", ADDON / "pft_draft_layout_views.py")
views = importlib.util.module_from_spec(spec)
spec.loader.exec_module(views)


def layout():
    return views._contract().plan("NeutralSource", "NeutralPage", ["Front", "Top", "Iso"], [70., 50., 8.])


class DraftDtoTests(unittest.TestCase):
    def test_pure_contract_is_fixed_sibling_and_returns_detached_complete_plan(self):
        value = layout(); copied = views.validate_layout(value)
        self.assertEqual(Path(views._CONTRACT.__file__).resolve(), ADDON / "pft_draft_layout.py")
        self.assertEqual(copied, value)
        value["views"][0]["center_mm"][0] += 1
        self.assertNotEqual(value, copied)
        self.assertEqual(len(copied["views"]), 3)

    def test_wrong_cached_contract_or_malformed_complete_plan_is_rejected(self):
        with patch.object(views, "_CONTRACT", types.SimpleNamespace(__file__=str(ADDON / "unknown.py"))):
            with self.assertRaisesRegex(ValueError, "^DRAWING_DRAFT_LAYOUT_PREVIEW_INVALID$"):
                views.validate_layout({})
        for mode in ("center", "reserve", "annotation", "order", "span", "extra", "missing"):
            value = layout()
            if mode == "center": value["views"][0]["center_mm"][0] += 1
            elif mode == "reserve": value["paper"]["reserved_rects_mm"][0][3] = 79
            elif mode == "annotation": value["annotation"]["margin_mm"] = 19
            elif mode == "order": value["views"].reverse()
            elif mode == "span": value["source_spans_mm"][0] += 1
            elif mode == "extra": value["confirm"] = True
            else: value["views"].pop()
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "^DRAWING_DRAFT_LAYOUT_PREVIEW_INVALID$"):
                views.validate_layout(value)

    def test_display_is_short_with_negative_zero_normalized_and_y_up_conversion(self):
        self.assertEqual(views._display(-.0001), "0")
        self.assertEqual(views._display(1.2345678901), "1.235")
        self.assertEqual(views._screen_rect([10, 20, 30, 50], 210, 2, (3, 4)), (23, 324, 40, 60))


class DraftWidgetTests(unittest.TestCase):
    def setUp(self):
        core = types.SimpleNamespace(Qt=types.SimpleNamespace(TextFormat=types.SimpleNamespace(PlainText=0),
            PenStyle=types.SimpleNamespace(DashLine=2), AlignmentFlag=types.SimpleNamespace(AlignLeft=1)),
            QEvent=types.SimpleNamespace(Type=types.SimpleNamespace(Resize=14)),
            QRectF=doubles.Rectangle, QPointF=lambda *point: point)
        gui = types.SimpleNamespace(QColor=lambda value: value, QPen=doubles.Pen, QFont=doubles.Font,
                                    QPainter=doubles.Painter)
        widgets = types.SimpleNamespace(QWidget=doubles.Widget, QLabel=doubles.Label, QToolButton=doubles.Button,
            QVBoxLayout=doubles.Layout, QHBoxLayout=doubles.Layout, QScrollArea=doubles.Scroll)
        handle = patch.object(views, "_qt", return_value=(core, gui, widgets))
        handle.start(); self.addCleanup(handle.stop)
        self.parent = doubles.Widget()
        self.widget = views.create_draft_layout_widget(self.parent)

    def named(self, suffix):
        pending = [self.widget]
        while pending:
            item = pending.pop()
            if item.name == "PhoenixTools_DraftLayout" + suffix: return item
            pending.extend(item.children)
        self.fail("missing draft object " + suffix)

    def resize(self, width, height):
        port = self.named("Scroll").viewport(); port.resize(width, height)
        self.widget.eventFilter(port, types.SimpleNamespace(type=lambda: 14))

    def test_construct_is_inert_with_visible_scope_and_three_display_buttons(self):
        self.assertIs(self.widget.parent, self.parent)
        self.assertIsNone(self.widget._plan)
        self.assertFalse(self.named("Fit").enabled)
        self.assertIn("全局包围盒", self.named("Notice").text)
        self.assertIn("尚无尺寸", self.named("Notice").text)
        self.assertIn("投影未验证", self.named("Notice").text)
        self.assertEqual(len([x for x in self.widget.children if isinstance(x, doubles.Button)]), 3)

    def test_fit_zoom_resize_only_display_frozen_data_and_bound_canvas(self):
        value = layout(); self.widget.set_plan(value); copied = deepcopy(self.widget._plan)
        value["views"][0]["center_mm"][0] += 1
        with patch.object(views, "validate_layout", side_effect=AssertionError("must not revalidate or solve")):
            self.resize(180, 150)
            self.assertEqual((self.named("Canvas").width(), self.named("Canvas").height()), (180, 150))
            self.assertLessEqual(297 * self.widget._scale, 148)
            self.named("ZoomIn").clicked.emit(); before = self.widget._scale
            self.resize(140, 100); self.assertEqual(before, self.widget._scale)
            for _ in range(70): self.named("ZoomIn").clicked.emit()
            self.assertLessEqual(max(self.named("Canvas").width(), self.named("Canvas").height()), 8192)
            self.named("Fit").clicked.emit()
            self.assertTrue(self.named("Fit").checked)
        self.assertEqual(self.widget._plan, copied)

    def test_paint_has_paper_reserve_three_geometry_and_three_annotation_boxes(self):
        self.widget.set_plan(layout()); self.named("Canvas").paintEvent(None)
        painter = doubles.Painter.calls[-1]
        self.assertTrue(painter.ended)
        self.assertEqual(sum(x[0] == "rect" and x[2] == "#535d69" for x in painter.operations), 3)
        self.assertEqual(sum(x[0] == "rect" and x[2] == "#4084c4" for x in painter.operations), 3)
        self.assertEqual(sum(x[0] == "rect" and x[2] == "#c28a20" for x in painter.operations), 1)
        self.assertEqual([x[1][-1] for x in painter.operations if x[0] == "text"], ["#1", "#2", "#3"])

    def test_three_rows_and_exact_escaped_tooltip_are_retained(self):
        value = layout(); self.widget.set_plan(value)
        details = self.named("Details")
        self.assertEqual(details.text.count("X="), 3)
        self.assertIn("20 mm", details.text)
        self.assertTrue(details.tooltip.startswith("<pre>"))
        for row in value["views"]:
            self.assertIn("X=%s, Y=%s mm" % tuple(row["center_mm"]), details.tooltip)
            self.assertIn(str(row["annotation_envelope_mm"]), details.tooltip)
        self.widget.clear_plan()
        self.assertEqual((details.text, details.tooltip), ("", ""))
        self.assertFalse(self.named("ZoomIn").enabled)

    def test_invalid_replacement_and_close_clear_stale_geometry(self):
        self.widget.set_plan(layout())
        with self.assertRaisesRegex(ValueError, "^DRAWING_DRAFT_LAYOUT_PREVIEW_INVALID$"):
            self.widget.set_plan({})
        self.assertIsNone(self.widget._plan)
        self.widget.set_plan(layout()); self.widget.closeEvent(None)
        self.assertIsNone(self.widget._plan); self.assertTrue(self.widget.closed)

    def test_injected_translation_is_plain_text(self):
        widget = views.create_draft_layout_widget(translate=lambda text: "T:" + text)
        labels = [x for x in widget.children if isinstance(x, doubles.Label)]
        self.assertTrue(all(x.text_format == 0 for x in labels))
        self.assertTrue(labels[0].text.startswith("T:"))


if __name__ == "__main__": unittest.main()
