# SPDX-License-Identifier: Apache-2.0
"""Read-only, detached drawing-layout plan; no CAD objects or approval actions."""
import importlib.util
import math
from html import escape
from pathlib import Path


_CONTRACT = None
_PREFIX = "PhoenixTools_DrawingLayout"
_BASE_PIXELS_PER_MM = 96.0 / 25.4
_MAX_CANVAS_SIDE = 8192
_MARGIN = 16
_NOTICE = "仅为布局建议；标签框与线条为估算，非原生尺寸渲染。投影未验证，需人工复核。"
_EMPTY = "尚无尺寸布局计划。"
_READY = "布局建议已冻结；请结合完整预览表复核。"
_BLOCKED = "布局被阻断；未生成部分写入坐标。"
_LEGEND = "灰：视图范围／已有固定标注；橙：保留区；紫：参考边；蓝：本次估算框与线。"
TRANSLATABLE_STRINGS = (
    _NOTICE, _EMPTY, _READY, _BLOCKED, _LEGEND, "适合窗口", "放大", "缩小",
    "纸外", "保留区冲突", "标签冲突", "线条冲突", "没有可用位置", "搜索预算已用尽",
    "已有固定标注", "视图范围", "自动建议", "显式位置", "纸面毫米；尺寸X/Y为相对视图的偏移。",
)
_REASONS = {"OUTSIDE_PAPER": "纸外", "RESERVED_REGION": "保留区冲突",
            "LABEL_COLLISION": "标签冲突", "LINE_COLLISION": "线条冲突",
            "NO_SPACE": "没有可用位置", "SEARCH_BUDGET_EXHAUSTED": "搜索预算已用尽"}


def _contract():
    global _CONTRACT
    expected = Path(__file__).resolve().with_name("pft_drawing_layout.py")
    if _CONTRACT is None:
        spec = importlib.util.spec_from_file_location("_pft_layout_views_contract", expected)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if Path(module.__file__).resolve() != expected:
            raise ValueError("DRAWING_LAYOUT_PREVIEW_INVALID")
        _CONTRACT = module
    if Path(_CONTRACT.__file__).resolve() != expected:
        raise ValueError("DRAWING_LAYOUT_PREVIEW_INVALID")
    return _CONTRACT


def validate_graphics(value):
    """Validate and detach the private input/result envelope, without Qt."""
    try:
        if (type(value) is not dict or set(value) != {"version", "input", "result"}
                or type(value["version"]) is not int or value["version"] != 1):
            raise ValueError()
        contract = _contract()
        source = contract.validate_input(value["input"])
        result = contract.validate_result(value["result"], expected=source)
        return {"version": 1, "input": source, "result": result}
    except Exception:
        raise ValueError("DRAWING_LAYOUT_PREVIEW_INVALID") from None


def _qt():
    from PySide import QtCore, QtGui
    try:
        from PySide import QtWidgets
    except ImportError:
        QtWidgets = QtGui
    return QtCore, QtGui, QtWidgets


def _enum(owner, group, name):
    value = getattr(owner, name, None)
    if value is None:
        value = getattr(getattr(owner, group, None), name, None)
    if value is None:
        raise RuntimeError("DRAWING_LAYOUT_UI_UNAVAILABLE")
    return value


def _screen_point(point, paper_height, scale, origin):
    return (origin[0] + point[0] * scale, origin[1] + (paper_height - point[1]) * scale)


def _screen_rect(box, paper_height, scale, origin):
    x, y = _screen_point((box[0], box[3]), paper_height, scale, origin)
    return x, y, (box[2] - box[0]) * scale, (box[3] - box[1]) * scale


def _display_position(value):
    text = format(value, ".3f").rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _fit_label_font(painter, text, width, height, flags, core, gui):
    """Fit display text using the same device, rectangle and drawText flags.

    This changes display typography only, never the frozen estimated box. A
    physically subpixel box has no readable complete label; omit its text
    rather than painting a misleading prefix. The full text remains in the
    row tooltip and becomes visible in the diagram after zooming in.
    """
    if width <= 0 or height <= 0:
        return None
    rectangle = core.QRectF(0, 0, width, height)

    def candidate(size):
        font = gui.QFont(painter.font())
        font.setPixelSize(size)
        measured = gui.QFontMetricsF(font, painter.device()).boundingRect(rectangle, flags, text)
        left, top, right, bottom = measured.left(), measured.top(), measured.right(), measured.bottom()
        if (not all(math.isfinite(value) for value in (left, top, right, bottom))
                or left < -1e-6 or top < -1e-6 or right > width + 1e-6 or bottom > height + 1e-6):
            return None
        return font

    best = candidate(1)
    if best is None:
        return None
    low, high = 2, 512
    while low <= high:
        middle = (low + high) // 2
        font = candidate(middle)
        if font is None:
            high = middle - 1
        else:
            best, low = font, middle + 1
    return best


def _paint_plan(painter, graphics, scale, origin, core, gui, tr):
    """Paint already validated paper geometry, without deriving new positions."""
    source, result = graphics["input"], graphics["result"]
    paper = source["paper"]
    width, height = paper["width_mm"], paper["height_mm"]
    solid = _enum(core.Qt, "PenStyle", "SolidLine")
    dashed = _enum(core.Qt, "PenStyle", "DashLine")
    no_brush = _enum(core.Qt, "BrushStyle", "NoBrush")
    center = _enum(core.Qt, "AlignmentFlag", "AlignCenter")
    wrap = _enum(core.Qt, "TextFlag", "TextWordWrap")

    def point(value):
        return core.QPointF(*_screen_point(value, height, scale, origin))

    def rectangle(value):
        return core.QRectF(*_screen_rect(value, height, scale, origin))

    def pen(color, style=solid, weight=1.0):
        value = gui.QPen(gui.QColor(color))
        value.setWidthF(weight)
        value.setStyle(style)
        painter.setPen(value)
        painter.setBrush(no_brush)

    def box(value, color, fill=None, style=solid):
        pen(color, style)
        if fill is not None:
            painter.setBrush(gui.QColor(fill))
        painter.drawRect(rectangle(value))

    def caption(value, text, color="#5c626b"):
        pen(color)
        font = gui.QFont(painter.font())
        font.setPixelSize(max(1, min(256, round(3.5 * scale))))
        painter.setFont(font)
        x, y = _screen_point(value, height, scale, origin)
        painter.drawText(core.QPointF(x + max(1, scale / 2), y - max(1, scale)), text)

    box((0, 0, width, height), "#3a3e44", "#ffffff")
    margin = paper["margin_mm"]
    box((margin, margin, width - margin, height - margin), "#c8cbd0", style=dashed)
    for value in paper["reserved_rects"]:
        box(value, "#b77d13", "#fff2cf", dashed)
    target = source["view"]
    box(target["bounds_mm"], "#707680", style=dashed)
    caption((target["bounds_mm"][0], target["bounds_mm"][3]), target["name"] + " · " + tr("视图范围"))
    for obstacle in source["obstacles"]["rects"]:
        is_view = obstacle["kind"] == "view"
        box(obstacle["bounds_mm"], "#9399a2", None if is_view else "#f0f1f3", dashed)
        caption((obstacle["bounds_mm"][0], obstacle["bounds_mm"][3]),
                obstacle["name"] + " · " + tr("视图范围" if is_view else "已有固定标注"))
    pen("#9399a2")
    for start, end in source["obstacles"]["segments"]:
        painter.drawLine(point(start), point(end))
    references = {}
    for index, row in enumerate(source["dimensions"]):
        geometry = row["geometry"]
        key = ((row["edge"], "line", tuple(geometry["start_mm"]), tuple(geometry["end_mm"]))
               if geometry["curve"] == "line" else
               (row["edge"], "circle", tuple(geometry["center_mm"]), geometry["radius_mm"]))
        if key not in references:
            references[key] = {"geometry": geometry, "indices": [], "edge": row["edge"]}
        references[key]["indices"].append(index + 1)
    for reference in references.values():
        geometry = reference["geometry"]
        pen("#8a3eaa", weight=1.5)
        if geometry["curve"] == "line":
            painter.drawLine(point(geometry["start_mm"]), point(geometry["end_mm"]))
            anchor = geometry["start_mm"]
        else:
            x, y = geometry["center_mm"]
            radius = geometry["radius_mm"]
            painter.drawEllipse(rectangle((x - radius, y - radius, x + radius, y + radius)))
            anchor = (x, y + radius)
        caption(anchor, "/".join("#%d" % index for index in reference["indices"]) +
                " " + reference["edge"], "#8a3eaa")
    for index, row in enumerate(result["dimensions"]):
        pen("#2466b3", weight=1.2)
        for start, end in row["segments_mm"]:
            painter.drawLine(point(start), point(end))
        bounds = row["label_box_mm"]
        box(bounds, "#2466b3", "#e8f1fc", dashed)
        caption((bounds[0], bounds[3]), "#%d" % (index + 1), "#2466b3")
        pen("#153b6b")
        x, y, text_width, text_height = _screen_rect(bounds, height, scale, origin)
        padding = 0.75  # Keep glyph ink off the fixed estimate's border.
        text_width, text_height = text_width - 2 * padding, text_height - 2 * padding
        font = _fit_label_font(painter, row["text"], text_width, text_height, center | wrap, core, gui)
        if font is not None:
            painter.setFont(font)
            painter.drawText(core.QRectF(x + padding, y + padding, text_width, text_height),
                             center | wrap, row["text"])


def create_drawing_layout_widget(parent=None, translate=None):
    """Return an inert QWidget with set_plan(graphics) and clear_plan().

    The caller supplies its approval-scoped frozen DTO and owns its lifetime.
    This widget has no confirmation callback, event pumping or native CAD read.
    Zooming changes display scale only; it never reruns the layout algorithm.
    """
    core, gui, widgets = _qt()
    tr = translate if callable(translate) else lambda text: text

    class Canvas(widgets.QWidget):
        def __init__(self, owner):
            super().__init__(owner)
            self._owner = owner
            self.setObjectName(_PREFIX + "Canvas")
            self.setMinimumSize(0, 0)

        def paintEvent(self, event):
            painter = gui.QPainter(self)
            try:
                self._owner._paint(painter)
            finally:
                painter.end()

    class LayoutPreview(widgets.QWidget):
        def __init__(self):
            super().__init__(parent)
            self.setObjectName(_PREFIX + "Widget")
            self.setMinimumSize(0, 0)
            self._plan = None
            self._fit, self._updating, self._scale = True, False, _BASE_PIXELS_PER_MM
            layout = widgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            self._notice = self._label(_NOTICE, "Notice")
            layout.addWidget(self._notice)
            actions = widgets.QHBoxLayout()
            self._fit_button = self._button("适合窗口", "Fit", self._select_fit)
            self._fit_button.setCheckable(True)
            self._out_button = self._button("−", "ZoomOut", lambda *_: self._zoom(1 / 1.25))
            self._out_button.setToolTip(tr("缩小"))
            self._in_button = self._button("+", "ZoomIn", lambda *_: self._zoom(1.25))
            self._in_button.setToolTip(tr("放大"))
            self._scale_label = self._label("", "Scale")
            for control in (self._fit_button, self._out_button, self._in_button, self._scale_label):
                actions.addWidget(control)
            layout.addLayout(actions)
            self._status = self._label(_EMPTY, "Status")
            layout.addWidget(self._status)
            self._scroll = widgets.QScrollArea(self)
            self._scroll.setObjectName(_PREFIX + "Scroll")
            self._scroll.setMinimumSize(0, 0)
            self._scroll.setWidgetResizable(False)
            self._canvas = Canvas(self)
            self._scroll.setWidget(self._canvas)
            self._scroll.viewport().installEventFilter(self)
            layout.addWidget(self._scroll, 1)
            layout.addWidget(self._label(_LEGEND, "Legend"))
            layout.addWidget(self._label("纸面毫米；尺寸X/Y为相对视图的偏移。", "Coordinates"))
            self._details = self._label("", "Details")
            layout.addWidget(self._details)
            self.clear_plan()

        def _label(self, text, suffix):
            label = widgets.QLabel(self)
            label.setObjectName(_PREFIX + suffix)
            label.setTextFormat(_enum(core.Qt, "TextFormat", "PlainText"))
            label.setMinimumWidth(0)
            label.setWordWrap(True)
            label.setText(tr(text))
            return label

        def _button(self, text, suffix, callback):
            button = widgets.QToolButton(self)
            button.setObjectName(_PREFIX + suffix)
            button.setText(tr(text))
            button.clicked.connect(callback)
            return button

        def clear_plan(self):
            self._plan = None
            self._fit, self._scale = True, _BASE_PIXELS_PER_MM
            self._status.setText(tr(_EMPTY))
            self._details.setText("")
            self._details.setToolTip("")
            self._update_geometry()

        def set_plan(self, graphics):
            self.clear_plan()
            self._plan = validate_graphics(graphics)
            result = self._plan["result"]
            self._status.setText(tr(_READY if result["can_apply"] else _BLOCKED))
            rows = ["#%d %s %s · %s · X=%s, Y=%s mm" %
                    (index + 1, row["edge"], row["kind"],
                     tr("自动建议" if row["placement"] == "auto" else "显式位置"),
                     _display_position(row["x_mm"]), _display_position(row["y_mm"]))
                    for index, row in enumerate(result["dimensions"])]
            exact = ["#%d %s %s · X=%s, Y=%s mm\n%s" %
                     (index + 1, row["edge"], row["kind"], row["x_mm"], row["y_mm"], row["text"])
                     for index, row in enumerate(result["dimensions"])]
            if not result["can_apply"]:
                rows = ["#%d %s %s · %s" % (index + 1, row["edge"], row["kind"],
                        tr("自动建议" if row["placement"] == "auto" else "显式位置"))
                        for index, row in enumerate(self._plan["input"]["dimensions"])]
            rows.extend("#%d · %s" % (row["index"] + 1, tr(_REASONS[row["reason"]]))
                        for row in result["blocked"])
            self._details.setText("\n".join(rows))
            # Qt tooltips auto-detect rich text. Escape the entire literal
            # content so no caller-provided text can become markup/resources.
            self._details.setToolTip("<pre>" + escape("\n".join(exact)) + "</pre>" if exact else "")
            self._update_geometry()

        def _select_fit(self, *_):
            self._fit = True
            self._update_geometry()

        def _maximum_scale(self):
            width, height = self._paper_size()
            return min(8 * _BASE_PIXELS_PER_MM,
                       (_MAX_CANVAS_SIDE - 2 * _MARGIN) / max(width, height))

        def _paper_size(self):
            paper = self._plan["input"]["paper"]
            return paper["width_mm"], paper["height_mm"]

        def _zoom(self, multiplier):
            if self._plan is None:
                return
            self._fit = False
            self._scale = min(self._maximum_scale(), max(0.05 * _BASE_PIXELS_PER_MM,
                                                         self._scale * multiplier))
            self._update_geometry()

        def _update_geometry(self):
            if self._updating:
                return
            self._updating = True
            try:
                enabled = self._plan is not None
                self._fit_button.setEnabled(enabled)
                self._fit_button.setChecked(self._fit)
                self._in_button.setEnabled(enabled)
                self._out_button.setEnabled(enabled)
                if self._fit or not enabled:
                    for _ in range(3):
                        rectangle = self._scroll.viewport().contentsRect()
                        width, height = max(1, rectangle.width()), max(1, rectangle.height())
                        self._canvas.resize(width, height)
                        after = self._scroll.viewport().contentsRect()
                        if (max(1, after.width()), max(1, after.height())) == (width, height):
                            break
                    if enabled:
                        paper_width, paper_height = self._paper_size()
                        self._scale = min(self._maximum_scale(),
                                          max(1, width - 2 * _MARGIN) / paper_width,
                                          max(1, height - 2 * _MARGIN) / paper_height)
                else:
                    width, height = self._paper_size()
                    self._canvas.resize(math.ceil(width * self._scale + 2 * _MARGIN),
                                        math.ceil(height * self._scale + 2 * _MARGIN))
                self._scale_label.setText("%g%%" % round(self._scale / _BASE_PIXELS_PER_MM * 100, 1)
                                          if enabled else "")
                self._canvas.update()
            finally:
                self._updating = False

        def eventFilter(self, watched, event):
            if watched is self._scroll.viewport() and event.type() == _enum(core.QEvent, "Type", "Resize"):
                self._update_geometry()
            return super().eventFilter(watched, event)

        def _paint(self, painter):
            painter.fillRect(self._canvas.rect(), gui.QColor("#e8eaed"))
            if self._plan is None:
                return
            painter.setRenderHint(_enum(gui.QPainter, "RenderHint", "Antialiasing"), True)
            width, height = self._paper_size()
            origin = ((self._canvas.width() - width * self._scale) / 2,
                      (self._canvas.height() - height * self._scale) / 2)
            _paint_plan(painter, self._plan, self._scale, origin, core, gui, tr)

        def closeEvent(self, event):
            self.clear_plan()
            super().closeEvent(event)

    return LayoutPreview()
