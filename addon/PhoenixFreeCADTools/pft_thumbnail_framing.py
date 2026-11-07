# SPDX-License-Identifier: Apache-2.0
"""Bounded perspective framing rules, without FreeCAD, Coin, or GUI imports.

The camera and scene bounds must use the same coordinate system.  Values sent
to Coin are rounded to binary32 here; the caller must verify native readback.
"""

import itertools
import math
import struct

PADDING = 1.1
_FLOAT32_MAX = (2.0 - 2.0 ** -23) * 2.0 ** 127
_ROUNDING_RESERVE = 1.0 + 8.0 * 2.0 ** -23
_CAMERA_KEYS = frozenset(("position", "orientation", "nearDistance", "farDistance",
                          "focalDistance", "heightAngle", "aspectRatio", "viewportMapping",
                          "width", "height"))


class PftThumbnailFramingError(ValueError):
    """The finite native camera cannot safely represent this framing."""


def _number(value):
    if type(value) not in (int, float):
        raise PftThumbnailFramingError()
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise PftThumbnailFramingError() from None
    if not math.isfinite(result) or abs(result) > _FLOAT32_MAX:
        raise PftThumbnailFramingError()
    return result


def _float32(value):
    value = _number(value)
    result = struct.unpack("!f", struct.pack("!f", value))[0]
    if not math.isfinite(result) or (value != 0.0 and result == 0.0):
        raise PftThumbnailFramingError()
    return result


def _vector(value, size):
    if type(value) not in (tuple, list) or len(value) != size:
        raise PftThumbnailFramingError()
    return tuple(_number(number) for number in value)


def _rotate(point, quaternion, inverse=False):
    # Native SbRotation is unit length to binary32 precision. Normalize only
    # the mathematical copy, never change the native orientation field.
    norm = math.hypot(*quaternion)
    if not math.isfinite(norm) or abs(norm - 1.0) > 0.00001:
        raise PftThumbnailFramingError()
    x, y, z, w = (number / norm for number in quaternion)
    if inverse:
        x, y, z = -x, -y, -z
    a, b, c = point
    tx, ty, tz = 2.0 * (y*c-z*b), 2.0 * (z*a-x*c), 2.0 * (x*b-y*a)
    return (a+w*tx+y*tz-z*ty, b+w*ty+z*tx-x*tz, c+w*tz+x*ty-y*tx)


def _camera(value):
    if type(value) is not dict or set(value) != _CAMERA_KEYS:
        raise PftThumbnailFramingError()
    result = {key: _number(value[key]) for key in
              ("nearDistance", "farDistance", "focalDistance", "heightAngle", "aspectRatio")}
    for number in result.values():
        _float32(number)
    result["position"] = _vector(value["position"], 3)
    result["orientation"] = _vector(value["orientation"], 4)
    _rotate((0.0, 0.0, 1.0), result["orientation"])
    mapping = value["viewportMapping"]
    if type(mapping) is not int or not 0 <= mapping <= 4:
        raise PftThumbnailFramingError()
    for key in ("width", "height"):
        if type(value[key]) is not int or not 1 <= value[key] <= 1024:
            raise PftThumbnailFramingError()
        result[key] = value[key]
    if (not 0.0 < result["heightAngle"] < math.pi or result["aspectRatio"] <= 0.0
            or result["focalDistance"] <= 0.0):
        raise PftThumbnailFramingError()
    result["viewportMapping"] = mapping
    return result


def _half_angle(camera):
    # ADJUST_CAMERA expands the view volume for a portrait output. Other
    # mappings retain the camera aspect (including the three crop modes).
    tangent = math.tan(camera["heightAngle"] / 2.0)
    if camera["viewportMapping"] != 3:
        tangent *= min(camera["aspectRatio"], 1.0)
    angle = math.atan(tangent)
    sine = math.sin(angle)
    if not math.isfinite(angle) or sine <= 0.0 or not math.isfinite(1.0 / sine):
        raise PftThumbnailFramingError()
    return angle


def _bounds(value):
    if value is None:
        return None
    if type(value) not in (tuple, list) or len(value) != 2:
        raise PftThumbnailFramingError()
    low, high = (_vector(point, 3) for point in value)
    if any(a > b for a, b in zip(low, high)):
        raise PftThumbnailFramingError()
    center = tuple(a + (b-a)/2.0 for a, b in zip(low, high))
    radius = math.hypot(*(b-a for a, b in zip(low, high))) / 2.0
    if not all(math.isfinite(number) for number in (*center, radius)):
        raise PftThumbnailFramingError()
    return low, high, center, radius


def plan_perspective(bounds, camera_values):
    """Return detached binary32 settings, or None for a flagged empty box.

    A zero-radius box uses the original positive focal distance as a point
    viewing reference. It does not acquire a fictitious object radius.
    """
    camera = _camera(camera_values)
    angle = _half_angle(camera)
    box = _bounds(bounds)
    if box is None:
        if not 0.0 < camera["nearDistance"] < camera["farDistance"]:
            raise PftThumbnailFramingError()
        return None
    low, high, center, radius = box
    distance = (PADDING * radius / math.sin(angle) if radius > 0.0
                else camera["focalDistance"])
    distance = _float32(distance * _ROUNDING_RESERVE)
    direction = _rotate((0.0, 0.0, 1.0), camera["orientation"])
    position = tuple(_float32(c + d*distance) for c, d in zip(center, direction))
    relative = tuple(c-p for c, p in zip(center, position))
    center_depth = -_rotate(relative, camera["orientation"], inverse=True)[2]
    if center_depth <= 0.0:
        raise PftThumbnailFramingError()
    near = _float32(center_depth - 1.05*radius if radius > 0.0 else center_depth/2.0)
    far = _float32(center_depth + 1.05*radius if radius > 0.0 else center_depth*1.5)
    settings = {"position": position, "nearDistance": near, "farDistance": far,
                "focalDistance": _float32(center_depth)}
    plan = {"bounds": (low, high), "center": center, "radius": radius,
            "camera": camera, "settings": settings}
    verify_perspective(plan, {**camera, **settings})
    return plan


def verify_perspective(plan, camera_values):
    """Check actual readback, padded sphere planes, clips, and all 8 corners."""
    if type(plan) is not dict or set(plan) != {"bounds", "center", "radius", "camera", "settings"}:
        raise PftThumbnailFramingError()
    camera = _camera(camera_values)
    expected = {**plan["camera"], **plan["settings"]}
    if camera != expected:
        raise PftThumbnailFramingError()
    box = _bounds(plan["bounds"])
    low, high, center, radius = box
    if center != plan["center"] or radius != plan["radius"]:
        raise PftThumbnailFramingError()
    near, far = camera["nearDistance"], camera["farDistance"]
    if not 0.0 < near < far:
        raise PftThumbnailFramingError()
    angle = _half_angle(camera)
    sine, cosine, tangent = math.sin(angle), math.cos(angle), math.tan(angle)
    relative = tuple(c-p for c, p in zip(center, camera["position"]))
    cx, cy, cz = _rotate(relative, camera["orientation"], inverse=True)
    depth = -cz
    if (depth <= 0.0 or depth-radius < near or depth+radius > far
            or depth*sine-abs(cx)*cosine < PADDING*radius
            or depth*sine-abs(cy)*cosine < PADDING*radius):
        raise PftThumbnailFramingError()
    for corner in itertools.product(*zip(low, high)):
        relative = tuple(c-p for c, p in zip(corner, camera["position"]))
        x, y, z = _rotate(relative, camera["orientation"], inverse=True)
        depth = -z
        if (not all(math.isfinite(number) for number in (x, y, depth))
                or not near <= depth <= far or abs(x) >= depth*tangent or abs(y) >= depth*tangent):
            raise PftThumbnailFramingError()
    return True
