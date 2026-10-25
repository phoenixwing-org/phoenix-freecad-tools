# SPDX-License-Identifier: Apache-2.0
"""Capture an explicit active 3D view, restoring its original camera in place."""

from datetime import datetime, timezone
import math
from pathlib import Path
import tempfile
import threading
import unicodedata

from pft_thumbnail import sanitize_png

PFT_MAX_THUMBNAIL_BYTES = 4 * 1024 * 1024
PFT_MAX_THUMBNAIL_PIXELS = 1024 * 1024
_BACKGROUNDS = {"white": "White", "current": "Current", "transparent": "Transparent"}
# FreeCAD Gui/Camera.cpp Camera::isometric(), assigned directly without animation.
_ISOMETRIC = (0.424708, 0.17592, 0.339851, 0.820473)
_COMMON_FIELDS = {"viewportMapping": "SFEnum", "position": "SFVec3f", "orientation": "SFRotation",
                  "nearDistance": "SFFloat", "farDistance": "SFFloat", "aspectRatio": "SFFloat",
                  "focalDistance": "SFFloat"}
TRANSLATABLE_STRINGS = (
    "请在 FreeCAD 主界面捕获缩略图。", "请明确指定当前活动文档。",
    "缩略图尺寸必须为1到1024的整数，且总像素不超过1048576。", "缩略图背景或视角选项无效。",
    "当前活动文档或三维视图已变化，请刷新后重试。", "无法确认文档事务或编辑状态，未捕获缩略图。",
    "当前文档正在编辑或已有未完成事务，请先完成或取消。",
    "当前视图没有可完整读取的正交或透视相机，未捕获缩略图。",
    "缩略图 PNG 超过4 MiB限制，请降低尺寸。", "缩略图捕获失败；未保存 CAD。",
    "相机恢复无法确认，请检查当前视图；未保存 CAD。",
    "缩略图包含当前三维视图所有可见对象，不按目标勾选过滤。",
    "缩略图不包含图纸或整个桌面；请检查预览后再导出或分享。",
    "窗口隐藏、最小化或未完成渲染时，预览可能为空白。",
)


class PftThumbnailCaptureError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _error(code, message):
    return PftThumbnailCaptureError(code, message)


def _context(app, gui, document_name):
    try:
        document = app.getDocument(document_name)
        gui_document = gui.getDocument(document_name)
        if (document is None or app.ActiveDocument is not document or document.Name != document_name
                or gui_document is None or gui.activeDocument() is not gui_document):
            raise _error("THUMBNAIL_DOCUMENT_CHANGED", "当前活动文档或三维视图已变化，请刷新后重试。")
        transaction = getattr(app, "getActiveTransaction", None)
        edit = getattr(gui_document, "getInEdit", None)
        if not callable(transaction) or not callable(edit):
            raise _error("THUMBNAIL_STATE_UNKNOWN", "无法确认文档事务或编辑状态，未捕获缩略图。")
        if transaction() or edit():
            raise _error("THUMBNAIL_DOCUMENT_BUSY", "当前文档正在编辑或已有未完成事务，请先完成或取消。")
        view = gui_document.activeView()
        if view is None or not all(callable(getattr(view, name, None)) for name in ("getCameraNode", "getSceneGraph", "saveImage")):
            raise _error("THUMBNAIL_CAMERA_UNSUPPORTED", "当前视图没有可完整读取的正交或透视相机，未捕获缩略图。")
        return document, gui_document, view
    except PftThumbnailCaptureError:
        raise
    except Exception:
        raise _error("THUMBNAIL_STATE_UNKNOWN", "无法确认文档事务或编辑状态，未捕获缩略图。") from None


def _camera_snapshot(node):
    """All fields, types, default/ignored flags, and exact native float values."""
    try:
        kind = str(node.getTypeId().getName())
        if kind not in ("OrthographicCamera", "PerspectiveCamera"):
            raise ValueError()
        expected = {**_COMMON_FIELDS, "height" if kind == "OrthographicCamera" else "heightAngle": "SFFloat"}
        data = node.getFieldData()
        count = data.getNumFields()
        if type(count) is not int or count != len(expected):
            raise ValueError()
        fields = {}
        for index in range(count):
            name = str(data.getFieldName(index))
            field = node.getField(name)
            field_kind = str(field.getTypeId().getName())
            if name in fields or expected.get(name) != field_kind or field.isConnected():
                raise ValueError()
            value = field.getValue()
            if field_kind in ("SFVec3f", "SFRotation"):
                value = tuple(value.getValue())
                if len(value) != (3 if field_kind == "SFVec3f" else 4):
                    raise ValueError()
            if field_kind == "SFEnum":
                if type(value) is not int:
                    raise ValueError()
            elif any(type(number) not in (int, float) or not math.isfinite(number)
                     for number in (value if isinstance(value, tuple) else (value,))):
                raise ValueError()
            fields[name] = {"type": field_kind, "value": value, "default": bool(field.isDefault()),
                            "ignored": bool(field.isIgnored())}
        if set(fields) != set(expected):
            raise ValueError()
        return {"type": kind, "fields": fields}
    except Exception:
        raise _error("THUMBNAIL_CAMERA_UNSUPPORTED", "当前视图没有可完整读取的正交或透视相机，未捕获缩略图。") from None


def _restore_camera(node, snapshot, coin):
    # Retain the original Pivy wrapper; never replace the camera or its scene node.
    failed = False
    for name, state in snapshot["fields"].items():
        try:
            field = node.getField(name)
        except Exception:
            failed = True
            continue
        try:
            value = state["value"]
            if state["type"] == "SFRotation":
                field.setValue(coin.SbRotation(*value))
            elif state["type"] == "SFVec3f":
                field.setValue(*value)
            else:
                field.setValue(value)
        except Exception:
            failed = True
        # Flags and remaining fields can still be restored after a setter fails.
        for method, value in (("setDefault", state["default"]), ("setIgnored", state["ignored"])):
            try:
                getattr(field, method)(value)
            except Exception:
                failed = True
    if failed:
        raise RuntimeError("Camera restoration could not be completed")


def capture_thumbnail(app, gui, document_name, width=512, height=512, background="white", axonometric_fit=True):
    """Return sanitized PNG bytes only after the original camera is verified."""
    if threading.current_thread() is not threading.main_thread():
        raise _error("MAIN_THREAD_REQUIRED", "请在 FreeCAD 主界面捕获缩略图。")
    if (not isinstance(document_name, str) or not 0 < len(document_name) <= 256
            or any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in document_name)):
        raise _error("DOCUMENT_REQUIRED", "请明确指定当前活动文档。")
    if (type(width) is not int or type(height) is not int or not 1 <= width <= 1024 or not 1 <= height <= 1024
            or width * height > PFT_MAX_THUMBNAIL_PIXELS):
        raise _error("THUMBNAIL_SIZE_INVALID", "缩略图尺寸必须为1到1024的整数，且总像素不超过1048576。")
    if not isinstance(background, str) or background not in _BACKGROUNDS or type(axonometric_fit) is not bool:
        raise _error("THUMBNAIL_OPTIONS_INVALID", "缩略图背景或视角选项无效。")
    document, gui_document, view = _context(app, gui, document_name)
    try:
        from pivy import coin
        node = view.getCameraNode()
        before = _camera_snapshot(node)
    except PftThumbnailCaptureError:
        raise
    except Exception:
        raise _error("THUMBNAIL_CAMERA_UNSUPPORTED", "当前视图没有可完整读取的正交或透视相机，未捕获缩略图。") from None
    try:
        if axonometric_fit:
            node.getField("orientation").setValue(coin.SbRotation(*_ISOMETRIC))
            node.viewAll(view.getSceneGraph(), coin.SbViewportRegion(width, height), 1.1)
        with tempfile.TemporaryDirectory(prefix="pft-thumbnail-") as temporary:
            path = Path(temporary) / "view.png"
            view.saveImage(str(path), width, height, _BACKGROUNDS[background])
            if path.is_symlink():
                raise ValueError()
            with path.open("rb") as stream:
                png = stream.read(PFT_MAX_THUMBNAIL_BYTES + 1)
            if len(png) > PFT_MAX_THUMBNAIL_BYTES:
                raise _error("THUMBNAIL_PNG_TOO_LARGE", "缩略图 PNG 超过4 MiB限制，请降低尺寸。")
            png = sanitize_png(png, width, height)
        current_document, current_gui, current_view = _context(app, gui, document_name)
        if current_document is not document or current_gui is not gui_document or current_view is not view:
            raise _error("THUMBNAIL_DOCUMENT_CHANGED", "当前活动文档或三维视图已变化，请刷新后重试。")
    except PftThumbnailCaptureError:
        raise
    except Exception:
        raise _error("THUMBNAIL_CAPTURE_FAILED", "缩略图捕获失败；未保存 CAD。") from None
    finally:
        try:
            _restore_camera(node, before, coin)
            if view.getCameraNode() != node or _camera_snapshot(node) != before:
                raise ValueError()
        except Exception:
            raise _error("THUMBNAIL_CAMERA_RESTORE_FAILED", "相机恢复无法确认，请检查当前视图；未保存 CAD。") from None
    return {"png": png, "document": document_name, "width": width, "height": height,
            "captured_at": datetime.now(timezone.utc).isoformat(), "source_kind": "live_freecad_gui",
            "camera_restored": True, "saved_cad": False,
            "warnings": ["缩略图包含当前三维视图所有可见对象，不按目标勾选过滤。",
                         "缩略图不包含图纸或整个桌面；请检查预览后再导出或分享。",
                         "窗口隐藏、最小化或未完成渲染时，预览可能为空白。"]}
