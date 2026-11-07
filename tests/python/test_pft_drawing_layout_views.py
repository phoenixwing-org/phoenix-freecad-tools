# SPDX-License-Identifier: Apache-2.0
"""Detached layout DTO binding and injected Qt; no CAD or user configuration."""
from copy import deepcopy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools"
_saved = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_drawing_layout_views as views
finally:
    sys.path[:] = _saved


def source(five=False):
    rows = [{"edge": "Edge0", "kind": "DistanceX", "placement": "auto",
             "geometry": {"curve": "line", "start_mm": [80, 100], "end_mm": [120, 100]},
             "text": "40.00", "label_size_mm": [14, 5]}]
    if five:
        rows = [
            {"edge": "Edge0", "kind": "Distance", "placement": "auto",
             "geometry": {"curve": "line", "start_mm": [80, 100], "end_mm": [120, 100]},
             "text": "40.00", "label_size_mm": [14, 5]},
            {"edge": "Edge0", "kind": "DistanceX", "placement": "auto",
             "geometry": {"curve": "line", "start_mm": [80, 100], "end_mm": [120, 100]},
             "text": "40.00", "label_size_mm": [14, 5]},
            {"edge": "Edge1", "kind": "DistanceY", "placement": "auto",
             "geometry": {"curve": "line", "start_mm": [80, 100], "end_mm": [80, 140]},
             "text": "40.00", "label_size_mm": [14, 5]},
            {"edge": "Edge2", "kind": "Diameter", "placement": "auto",
             "geometry": {"curve": "circle", "center_mm": [100, 120], "radius_mm": 5},
             "text": "⌀10.00", "label_size_mm": [18, 5]},
            {"edge": "Edge2", "kind": "Radius", "placement": "auto",
             "geometry": {"curve": "circle", "center_mm": [100, 120], "radius_mm": 5},
             "text": "R5.00", "label_size_mm": [14, 5]},
        ]
    return {"version": 1,
            "paper": {"width_mm": 297, "height_mm": 210, "margin_mm": 10,
                      "reserved_rects": [[10, 10, 287, 50]]},
            "view": {"name": "TopView", "origin_mm": [100, 120], "bounds_mm": [80, 100, 120, 140]},
            "obstacles": {"rects": [
                {"name": "FrontView", "kind": "view", "bounds_mm": [160, 100, 200, 140]},
                {"name": "SideView", "kind": "view", "bounds_mm": [215, 100, 255, 140]},
                {"name": "FixedDimension", "kind": "dimension", "bounds_mm": [170, 153, 188, 158]}],
                "segments": [[[165, 145], [195, 145]]]},
            "spacing": {"clearance_mm": 1, "offset_mm": 6, "lane_mm": 8}, "dimensions": rows}


def graphics(value=None):
    value = source() if value is None else value
    return {"version": 1, "input": value, "result": views._contract().suggest_layout(value)}


class Signal:
    def connect(self, callback): self.callback = callback
    def emit(self): self.callback(False)


class Rect:
    def __init__(self, width, height): self.w, self.h = width, height
    def width(self): return self.w
    def height(self): return self.h


class Widget:
    def __init__(self, parent=None):
        self.parent, self.children, self.name, self.enabled = parent, [], "", True
        self.w, self.h, self.updates = 640, 400, 0
        if parent is not None: parent.children.append(self)
    def setObjectName(self, value): self.name = value
    def setMinimumSize(self, *value): self.minimum = value
    def setMinimumWidth(self, value): self.minimum_width = value
    def setEnabled(self, value): self.enabled = value
    def resize(self, width, height): self.w, self.h = width, height
    def width(self): return self.w
    def height(self): return self.h
    def rect(self): return Rect(self.w, self.h)
    contentsRect = rect
    def installEventFilter(self, owner): self.filter = owner
    def update(self): self.updates += 1
    def eventFilter(self, watched, event): return False
    def closeEvent(self, event): self.closed = True


class Label(Widget):
    def setTextFormat(self, value): self.text_format = value
    def setWordWrap(self, value): self.word_wrap = value
    def setText(self, value): self.text = value
    def setToolTip(self, value): self.tooltip = value


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
    def addLayout(self, value): self.items.append(value)


class Scroll(Widget):
    def __init__(self, parent=None): super().__init__(parent); self.port = Widget(self)
    def viewport(self): return self.port
    def setWidgetResizable(self, value): self.widget_resizable = value
    def setWidget(self, value): self.widget = value


class Pen:
    def __init__(self, color): self.color = color
    def setWidthF(self, value): self.width = value
    def setStyle(self, value): self.style = value


class Font:
    def __init__(self, other=None): self.size = 10
    def setPixelSize(self, value): self.size = value


class Rectangle:
    def __init__(self, x, y, width, height): self.x, self.y, self.w, self.h = x, y, width, height
    def left(self): return self.x
    def top(self): return self.y
    def right(self): return self.x + self.w
    def bottom(self): return self.y + self.h


class FontMetrics:
    calls = []
    def __init__(self, font, device): self.font, self.device = font, device
    def boundingRect(self, rectangle, flags, text):
        self.calls.append((self.font.size, self.device, flags, text))
        width = max(sum(1.1 if ch == "⌀" else 0.65 for ch in line)
                    for line in text.split("\n")) * self.font.size
        height = len(text.split("\n")) * self.font.size * 1.2
        return Rectangle((rectangle.w - width) / 2, (rectangle.h - height) / 2, width, height)


class Painter:
    Antialiasing = 1
    calls = []
    def __init__(self, target):
        self.target, self.operations, self.ended, self.current_font = target, [], False, Font()
        self.calls.append(self)
    def fillRect(self, *args): self.operations.append(("fill", args))
    def setRenderHint(self, *args): pass
    def setPen(self, value): self.pen = value
    def setBrush(self, value): self.brush = value
    def font(self): return self.current_font
    def device(self): return self.target
    def setFont(self, value): self.current_font = value
    def drawRect(self, value): self.operations.append(("rect", value, self.pen.color))
    def drawEllipse(self, value): self.operations.append(("ellipse", value, self.pen.color))
    def drawLine(self, *args): self.operations.append(("line", args, self.pen.color))
    def drawText(self, *args): self.operations.append(("text", args, self.pen.color))
    def end(self): self.ended = True


class LayoutDtoTests(unittest.TestCase):
    def test_validated_envelope_is_detached_and_fixed_sibling_is_used(self):
        value = graphics()
        with patch.dict(sys.modules, {"pft_drawing_layout": types.SimpleNamespace(
                validate_input=lambda value: self.fail("ambient contract import"))}):
            old = views._CONTRACT
            try:
                views._CONTRACT = None
                copied = views.validate_graphics(value)
                self.assertEqual(Path(views._CONTRACT.__file__).resolve(), ADDON / "pft_drawing_layout.py")
            finally:
                views._CONTRACT = old
        value["input"]["view"]["origin_mm"][0] = -999
        value["result"]["dimensions"][0]["text"] = "changed"
        self.assertEqual(copied["input"]["view"]["origin_mm"], [100, 120])
        self.assertEqual(copied["result"]["dimensions"][0]["text"], "40.00")

    def test_wrong_cached_contract_origin_is_rejected(self):
        with patch.object(views, "_CONTRACT", types.SimpleNamespace(__file__=str(ADDON / "unknown.py"))):
            with self.assertRaisesRegex(ValueError, "^DRAWING_LAYOUT_PREVIEW_INVALID$"):
                views.validate_graphics({"version": 1, "input": {}, "result": {}})

    def test_result_must_bind_each_input_identity_text_size_and_offsets(self):
        valid = graphics(source(True))
        self.assertTrue(valid["result"]["can_apply"])
        changes = (
            lambda row: row.update(edge="Edge999"),
            lambda row: row.update(kind="DistanceY"),
            lambda row: row.update(text="different label"),
            lambda row: row.update(x_mm=row["x_mm"] + 0.5),
            lambda row: row.update(y_mm=row["y_mm"] + 0.5),
            lambda row: row["label_box_mm"].__setitem__(2, row["label_box_mm"][2] + 0.5),
            lambda row: row["label_box_mm"].__setitem__(0, row["label_box_mm"][0] + 0.5),
            lambda row: row["segments_mm"][0][0].__setitem__(0, row["segments_mm"][0][0][0] + 0.5),
        )
        for index, change in enumerate(changes):
            value = deepcopy(valid)
            change(value["result"]["dimensions"][0])
            with self.subTest(change=index), self.assertRaisesRegex(ValueError, "^DRAWING_LAYOUT_PREVIEW_INVALID$"):
                views.validate_graphics(value)
        value = deepcopy(valid)
        value["result"]["dimensions"][:2] = reversed(value["result"]["dimensions"][:2])
        with self.assertRaises(ValueError): views.validate_graphics(value)

    def test_wrong_explicit_position_and_partial_blocked_rows_are_rejected(self):
        value = source()
        value["dimensions"][0].update(placement="explicit", x_mm=0, y_mm=-30)
        valid = graphics(value)
        self.assertTrue(valid["result"]["can_apply"])
        valid["input"]["dimensions"][0]["x_mm"] = 2
        with self.assertRaises(ValueError): views.validate_graphics(valid)
        value = source()
        value["paper"]["reserved_rects"] = [[0, 0, 297, 210]]
        blocked = graphics(value)
        self.assertFalse(blocked["result"]["can_apply"])
        self.assertEqual(blocked["result"]["dimensions"], [])
        blocked["result"]["dimensions"] = graphics()["result"]["dimensions"]
        with self.assertRaises(ValueError): views.validate_graphics(blocked)

    def test_result_cannot_follow_changed_input_metrics_origin_or_reference(self):
        valid = graphics()
        changes = (
            lambda data: data["dimensions"][0]["label_size_mm"].__setitem__(0, 15),
            lambda data: data["view"]["origin_mm"].__setitem__(0, 101),
            lambda data: data["dimensions"][0]["geometry"]["end_mm"].__setitem__(0, 119),
            lambda data: data["dimensions"][0].update(text="different input label"),
        )
        for index, change in enumerate(changes):
            value = deepcopy(valid)
            change(value["input"])
            with self.subTest(change=index), self.assertRaisesRegex(ValueError, "^DRAWING_LAYOUT_PREVIEW_INVALID$"):
                views.validate_graphics(value)

    def test_envelope_is_strict_and_does_not_echo_invalid_data(self):
        for changed in (None, {"version": True, "input": {}, "result": {}},
                        {"version": 1, "input": {}, "result": {}, "path": "synthetic-not-a-path"}):
            with self.subTest(value_type=type(changed).__name__), self.assertRaisesRegex(ValueError, "^DRAWING_LAYOUT_PREVIEW_INVALID$"):
                views.validate_graphics(changed)

    def test_paper_mm_uses_y_up_and_does_not_apply_model_scale_again(self):
        self.assertEqual(views._screen_point((20, 30), 210, 2, (4, 6)), (44, 366))
        self.assertEqual(views._screen_rect((20, 30, 60, 50), 210, 2, (4, 6)), (44, 326, 80, 40))

    def test_display_positions_round_only_the_text_and_normalize_negative_zero(self):
        for value, expected in ((0, "0"), (-0.0, "0"), (-0.0004, "0"), (0.0004, "0"),
                                (1.23456789, "1.235"), (-26.74264068711929, "-26.743"),
                                (10.5000, "10.5"), (1000, "1000")):
            with self.subTest(value=value): self.assertEqual(views._display_position(value), expected)


class LayoutWidgetTests(unittest.TestCase):
    def setUp(self):
        Painter.calls, FontMetrics.calls = [], []
        qt = types.SimpleNamespace(PlainText=0, SolidLine=1, DashLine=2, NoBrush=0,
                                   AlignCenter=4, TextWordWrap=8)
        core = types.SimpleNamespace(Qt=qt, QEvent=types.SimpleNamespace(Resize=14),
                                     QPointF=lambda *value: value, QRectF=Rectangle)
        gui = types.SimpleNamespace(QColor=lambda value: value, QPen=Pen, QFont=Font,
                                    QFontMetricsF=FontMetrics, QPainter=Painter)
        self.core, self.gui = core, gui
        widgets = types.SimpleNamespace(QWidget=Widget, QLabel=Label, QToolButton=Button,
                                        QVBoxLayout=Layout, QHBoxLayout=Layout, QScrollArea=Scroll)
        handle = patch.object(views, "_qt", return_value=(core, gui, widgets))
        handle.start(); self.addCleanup(handle.stop)
        self.parent = Widget()
        self.widget = views.create_drawing_layout_widget(self.parent)

    def named(self, suffix):
        pending = [self.widget]
        while pending:
            item = pending.pop()
            if item.name == "PhoenixTools_DrawingLayout" + suffix: return item
            pending.extend(item.children)
        self.fail("missing layout object " + suffix)

    def resize(self, width, height):
        port = self.named("Scroll").viewport()
        port.resize(width, height)
        self.widget.eventFilter(port, types.SimpleNamespace(type=lambda: 14))

    def test_construct_is_inert_with_fixed_warning_and_no_confirmation_control(self):
        self.assertIs(self.widget.parent, self.parent)
        self.assertEqual(self.widget.minimum, (0, 0))
        self.assertIsNone(self.widget._plan)
        self.assertFalse(self.named("Fit").enabled)
        self.assertIn("非原生尺寸渲染", self.named("Notice").text)
        self.assertFalse(self.named("Scroll").widget_resizable)
        buttons = [item.name for item in self.widget.children if isinstance(item, Button)]
        self.assertEqual(set(buttons), {"PhoenixTools_DrawingLayout" + name for name in ("Fit", "ZoomIn", "ZoomOut")})

    def test_fit_narrow_zoom_and_clear_do_not_revalidate_or_relayout(self):
        value = graphics()
        self.widget.set_plan(value)
        frozen = deepcopy(self.widget._plan)
        value["input"]["view"]["origin_mm"][0] = -999
        with patch.object(views, "validate_graphics", side_effect=AssertionError("must not re-read plan")):
            self.resize(180, 150)
            self.assertEqual((self.named("Canvas").w, self.named("Canvas").h), (180, 150))
            self.assertLessEqual(297 * self.widget._scale, 148)
            self.named("ZoomIn").clicked.emit()
            self.assertFalse(self.named("Fit").checked)
            before = self.widget._scale
            self.resize(120, 100)
            self.assertEqual(self.widget._scale, before)
            for _ in range(70): self.named("ZoomIn").clicked.emit()
            self.assertLessEqual(max(self.named("Canvas").w, self.named("Canvas").h), 8192)
            self.named("Fit").clicked.emit()
            self.assertTrue(self.named("Fit").checked)
            self.assertEqual((self.named("Canvas").w, self.named("Canvas").h), (120, 100))
        self.assertEqual(self.widget._plan, frozen)
        self.widget.clear_plan()
        self.assertIsNone(self.widget._plan)
        self.assertEqual(self.named("Details").text, "")
        self.assertFalse(self.named("ZoomIn").enabled)

    def test_actual_paint_commands_include_all_five_kinds_and_fixed_input(self):
        value = graphics(source(True))
        self.assertTrue(value["result"]["can_apply"])
        self.widget.set_plan(value)
        self.named("Canvas").paintEvent(None)
        painter = Painter.calls[-1]
        self.assertTrue(painter.ended)
        self.assertEqual(sum(op[0] == "ellipse" for op in painter.operations), 1)
        texts = [op[1][-1] for op in painter.operations if op[0] == "text"]
        for text in ("⌀10.00", "R5.00", "40.00", "FrontView · 视图范围", "SideView · 视图范围", "FixedDimension · 已有固定标注"):
            self.assertIn(text, texts)
        self.assertIn("#1/#2 Edge0", texts)
        self.assertIn("#4/#5 Edge2", texts)
        self.assertEqual(self.named("Details").text.count("X="), 5)
        self.assertEqual(sum(op[0] == "rect" and op[2] == "#2466b3" for op in painter.operations), 5)

    def test_footer_uses_short_values_but_safe_tooltip_retains_exact_coordinates(self):
        value = graphics(source(True))
        frozen = deepcopy(value)
        self.widget.set_plan(value)
        details = self.named("Details")
        self.assertTrue(details.tooltip.startswith("<pre>"))
        self.assertTrue(details.tooltip.endswith("</pre>"))
        for row in value["result"]["dimensions"]:
            self.assertIn("X=%s, Y=%s mm" % (views._display_position(row["x_mm"]),
                                           views._display_position(row["y_mm"])), details.text)
            self.assertIn("X=%s, Y=%s mm" % (row["x_mm"], row["y_mm"]), details.tooltip)
            self.assertIn(row["text"], details.tooltip)
        self.assertEqual(self.widget._plan, frozen)
        self.assertEqual(value, frozen)
        self.widget.clear_plan()
        self.assertEqual(details.tooltip, "")

    def test_label_font_fits_width_and_height_with_same_device_and_flags(self):
        painter = Painter(self.widget)
        painter.current_font.size = 4096
        for text, width, height in (("⌀10.00", 15, 60), ("R5.00", 10, 100),
                                    ("60.00\n±0.1", 20, 12)):
            with self.subTest(text=text):
                font = views._fit_label_font(painter, text, width, height, 12, self.core, self.gui)
                self.assertIsNotNone(font)
                measured = FontMetrics(font, self.widget).boundingRect(Rectangle(0, 0, width, height), 12, text)
                self.assertGreaterEqual(measured.left(), -1e-6)
                self.assertGreaterEqual(measured.top(), -1e-6)
                self.assertLessEqual(measured.right(), width + 1e-6)
                self.assertLessEqual(measured.bottom(), height + 1e-6)
                self.assertLessEqual(font.size, 512)
        self.assertTrue(all(device is self.widget and flags == 12 for _, device, flags, _ in FontMetrics.calls))

    def test_subpixel_label_is_omitted_instead_of_showing_a_clipped_prefix(self):
        painter = Painter(self.widget)
        for width, height in ((0.1, 0.1), (0, 1), (3, 0), (1, 10)):
            with self.subTest(width=width, height=height):
                self.assertIsNone(views._fit_label_font(painter, "⌀10.00", width, height, 12, self.core, self.gui))

    def test_blocked_plan_keeps_paper_obstacles_references_and_no_partial_boxes(self):
        value = source(True)
        value["paper"]["reserved_rects"] = [[0, 0, 297, 210]]
        self.widget.set_plan(graphics(value))
        self.named("Canvas").paintEvent(None)
        operations = Painter.calls[-1].operations
        self.assertIn("未生成部分", self.named("Status").text)
        self.assertIn("#1", self.named("Details").text)
        for kind in ("Distance", "DistanceX", "DistanceY", "Diameter", "Radius"):
            self.assertIn(kind, self.named("Details").text)
        self.assertNotIn("X=", self.named("Details").text)
        self.assertTrue(any(op[0] == "line" and op[2] == "#8a3eaa" for op in operations))
        self.assertFalse(any(op[0] == "rect" and op[2] == "#2466b3" for op in operations))
        self.assertTrue(any(op[0] == "rect" and op[2] == "#b77d13" for op in operations))

    def test_invalid_replacement_clears_old_plan_and_close_disposes_dto(self):
        self.widget.set_plan(graphics())
        with self.assertRaisesRegex(ValueError, "^DRAWING_LAYOUT_PREVIEW_INVALID$"):
            self.widget.set_plan({"version": 1, "input": {}, "result": {}})
        self.assertIsNone(self.widget._plan)
        self.assertFalse(self.named("ZoomIn").enabled)
        self.widget.set_plan(graphics())
        self.widget.closeEvent(None)
        self.assertIsNone(self.widget._plan)
        self.assertTrue(self.widget.closed)

    def test_translation_is_injected_and_labels_use_plain_text(self):
        widget = views.create_drawing_layout_widget(translate=lambda text: "T:" + text)
        self.assertTrue(widget._notice.text.startswith("T:"))
        self.assertEqual(widget._notice.text_format, 0)
        self.assertEqual(widget._status.text_format, 0)


if __name__ == "__main__":
    unittest.main()
