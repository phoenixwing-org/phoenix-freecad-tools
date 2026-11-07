# SPDX-License-Identifier: Apache-2.0
"""Read-only new-draft geometry plan, without CAD or approval callbacks."""
import importlib.util
import math
from html import escape
from pathlib import Path

_CONTRACT = None
_PREFIX = "PhoenixTools_DraftLayout"
_BASE_SCALE = 96. / 25.4
_MAX_SIDE = 8192
_MARGIN = 16
_NOTICE = "仅为来源全局包围盒的几何估算；非原生图纸渲染，尚无尺寸。投影未验证，标注留白不保证尺寸一定有解。"
_EMPTY = "尚无新草稿布局计划。"
_READY = "新草稿布局已冻结；仅原确认按钮可创建五个节点。"
_LEGEND = "灰：估算几何框；蓝：固定纸面标注留白；橙：标题保留区。"
_KINDS = {"front": "主视图", "top": "俯视图", "axonometric": "轴测图"}
TRANSLATABLE_STRINGS = (_NOTICE, _EMPTY, _READY, _LEGEND, "适合窗口", "放大", "缩小",
    "主视图", "俯视图", "轴测图", "纸面毫米；三视图使用共同模型比例，标注留白不随比例缩小。",
    "比例 1:%s；每侧标注留白 %s mm。")


def _contract():
    global _CONTRACT
    filename = Path(__file__).resolve().with_name("pft_draft_layout.py")
    if _CONTRACT is None:
        spec = importlib.util.spec_from_file_location("_pft_draft_layout_views_contract", filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if Path(getattr(module, "__file__", "")).resolve() != filename:
            raise ValueError("DRAWING_DRAFT_LAYOUT_PREVIEW_INVALID")
        _CONTRACT = module
    if Path(getattr(_CONTRACT, "__file__", "")).resolve() != filename:
        raise ValueError("DRAWING_DRAFT_LAYOUT_PREVIEW_INVALID")
    return _CONTRACT


def validate_layout(value):
    """Validate and detach a standalone exact draft DTO, with no native reads."""
    try:
        return _contract().validate(value)
    except Exception:
        raise ValueError("DRAWING_DRAFT_LAYOUT_PREVIEW_INVALID") from None


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
        raise RuntimeError("DRAWING_DRAFT_LAYOUT_UI_UNAVAILABLE")
    return value


def _display(value):
    text = ("%.3f" % value).rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _screen_rect(bounds, height, scale, origin):
    return (origin[0] + bounds[0] * scale, origin[1] + (height - bounds[3]) * scale,
            (bounds[2] - bounds[0]) * scale, (bounds[3] - bounds[1]) * scale)


def _paint_plan(painter, plan, scale, origin, core, gui):
    width, height = plan["paper"]["width_mm"], plan["paper"]["height_mm"]
    def rectangle(bounds, fill, stroke, dashed=False):
        pen = gui.QPen(gui.QColor(stroke))
        pen.setWidthF(1.)
        if dashed:
            pen.setStyle(_enum(core.Qt, "PenStyle", "DashLine"))
        painter.setPen(pen)
        painter.setBrush(gui.QColor(fill))
        painter.drawRect(core.QRectF(*_screen_rect(bounds, height, scale, origin)))
    rectangle([0, 0, width, height], "#ffffff", "#87909a")
    for reserved in plan["paper"]["reserved_rects_mm"]:
        rectangle(reserved, "#fff2cf", "#c28a20")
    rectangle(plan["paper"]["usable_bounds_mm"], "#00ffffff", "#a0a6ad", True)
    for index, view in enumerate(plan["views"]):
        rectangle(view["annotation_envelope_mm"], "#e8f1fc", "#4084c4", True)
        rectangle(view["estimated_geometry_bounds_mm"], "#dce1e6", "#535d69")
        center = view["center_mm"]
        x, y = origin[0] + center[0] * scale, origin[1] + (height - center[1]) * scale
        painter.setPen(gui.QPen(gui.QColor("#364251")))
        painter.drawLine(core.QPointF(x - 3, y), core.QPointF(x + 3, y))
        painter.drawLine(core.QPointF(x, y - 3), core.QPointF(x, y + 3))
        ax, ay, aw, ah = _screen_rect(view["annotation_envelope_mm"], height, scale, origin)
        band = plan["annotation"]["margin_mm"] * scale
        if band >= 9:
            font = gui.QFont(painter.font())
            font.setPixelSize(max(6, min(12, int(band * .65))))
            painter.setFont(font)
            painter.drawText(core.QRectF(ax + 2, ay + 1, max(1, aw - 4), max(1, band - 2)),
                             _enum(core.Qt, "AlignmentFlag", "AlignLeft"), "#%d" % (index + 1))


def create_draft_layout_widget(parent=None, translate=None):
    """Create an inert QWidget with set_plan(dto) and clear_plan().

    It renders only its frozen geometry DTO. Display controls never search,
    change CAD/selection, open a page or grant the surrounding approval.
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
                painter.fillRect(self.rect(), gui.QColor("#e8eaed"))
                plan = self._owner._plan
                if plan is not None:
                    painter.setRenderHint(_enum(gui.QPainter, "RenderHint", "Antialiasing"), True)
                    width, height = plan["paper"]["width_mm"], plan["paper"]["height_mm"]
                    scale = self._owner._scale
                    origin = ((self.width() - width * scale) / 2, (self.height() - height * scale) / 2)
                    _paint_plan(painter, plan, scale, origin, core, gui)
            finally:
                painter.end()

    class DraftPreview(widgets.QWidget):
        def __init__(self):
            super().__init__(parent)
            self.setObjectName(_PREFIX + "Widget")
            self.setMinimumSize(0, 0)
            self._plan = None
            self._fit, self._updating, self._scale = True, False, _BASE_SCALE
            layout = widgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self._label(_NOTICE, "Notice"))
            actions = widgets.QHBoxLayout()
            self._fit_button = self._button("适合窗口", "Fit", self._select_fit)
            self._fit_button.setCheckable(True)
            self._out_button = self._button("−", "ZoomOut", lambda *_: self._zoom(1 / 1.25))
            self._out_button.setToolTip(tr("缩小"))
            self._in_button = self._button("+", "ZoomIn", lambda *_: self._zoom(1.25))
            self._in_button.setToolTip(tr("放大"))
            self._scale_label = self._label("", "Scale")
            for item in (self._fit_button, self._out_button, self._in_button, self._scale_label):
                actions.addWidget(item)
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
            layout.addWidget(self._label("纸面毫米；三视图使用共同模型比例，标注留白不随比例缩小。", "Coordinates"))
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
            self._fit, self._scale = True, _BASE_SCALE
            self._status.setText(tr(_EMPTY))
            self._details.setText("")
            self._details.setToolTip("")
            self._update_geometry()

        def set_plan(self, value):
            self.clear_plan()
            self._plan = validate_layout(value)
            plan = self._plan
            self._status.setText(tr(_READY))
            rows = [tr("比例 1:%s；每侧标注留白 %s mm。") % (_display(1 / plan["scale"]), _display(plan["annotation"]["margin_mm"]))]
            exact = []
            for index, view in enumerate(plan["views"]):
                x, y = view["center_mm"]
                rows.append("#%d %s %s · X=%s, Y=%s mm" % (index + 1, tr(_KINDS[view["kind"]]),
                            view["name"], _display(x), _display(y)))
                exact.append("#%d %s %s\nX=%s, Y=%s mm\ngeometry=%s\nannotation=%s" % (
                    index + 1, view["kind"], view["name"], x, y,
                    view["estimated_geometry_bounds_mm"], view["annotation_envelope_mm"]))
            self._details.setText("\n".join(rows))
            self._details.setToolTip("<pre>" + escape("\n".join(exact)) + "</pre>")
            self._update_geometry()

        def _paper_size(self):
            return self._plan["paper"]["width_mm"], self._plan["paper"]["height_mm"]

        def _maximum_scale(self):
            return min(8 * _BASE_SCALE, (_MAX_SIDE - 2 * _MARGIN) / max(self._paper_size()))

        def _select_fit(self, *_):
            self._fit = True
            self._update_geometry()

        def _zoom(self, factor):
            if self._plan is not None:
                self._fit = False
                self._scale = min(self._maximum_scale(), max(.05 * _BASE_SCALE, self._scale * factor))
                self._update_geometry()

        def _update_geometry(self):
            if self._updating:
                return
            self._updating = True
            try:
                enabled = self._plan is not None
                self._fit_button.setEnabled(enabled)
                self._fit_button.setChecked(self._fit)
                self._out_button.setEnabled(enabled)
                self._in_button.setEnabled(enabled)
                if self._fit or not enabled:
                    for _ in range(3):
                        area = self._scroll.viewport().contentsRect()
                        width, height = max(1, area.width()), max(1, area.height())
                        self._canvas.resize(width, height)
                        after = self._scroll.viewport().contentsRect()
                        if (width, height) == (max(1, after.width()), max(1, after.height())):
                            break
                    if enabled:
                        pw, ph = self._paper_size()
                        self._scale = min(self._maximum_scale(), max(1, width - 2 * _MARGIN) / pw,
                                          max(1, height - 2 * _MARGIN) / ph)
                else:
                    pw, ph = self._paper_size()
                    self._canvas.resize(math.ceil(pw * self._scale + 2 * _MARGIN), math.ceil(ph * self._scale + 2 * _MARGIN))
                self._scale_label.setText("%g%%" % round(self._scale / _BASE_SCALE * 100, 1) if enabled else "")
                self._canvas.update()
            finally:
                self._updating = False

        def eventFilter(self, watched, event):
            if watched is self._scroll.viewport() and event.type() == _enum(core.QEvent, "Type", "Resize"):
                self._update_geometry()
            return super().eventFilter(watched, event)

        def closeEvent(self, event):
            self.clear_plan()
            super().closeEvent(event)

    return DraftPreview()
