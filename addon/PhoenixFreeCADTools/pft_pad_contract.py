# SPDX-License-Identifier: Apache-2.0
"""Strict, detached Pad result data shared by native adapters and approval views.

This module imports no FreeCAD or Qt API, performs no IO, and does not inspect
native objects. Validation describes the reported data; it does not run geometry
checks or establish that an operation completed.
"""
import math
import unicodedata


_ERROR = "Pad 设计结果格式无效。"
_SAFE_INTEGER = 9007199254740991
_RESULT_KEYS = frozenset(("kind", "body", "pad", "length_mm", "recompute_count",
                          "checked_objects", "geometry", "validation"))
_GEOMETRY_KEYS = frozenset(("volume_mm3", "dimensions_mm", "solids"))
_EXPECTED_KEYS = frozenset(("body", "pad", "length_mm"))


def _reject():
    raise ValueError(_ERROR)


def _mapping(value, keys):
    if type(value) is not dict or len(value) != len(keys):
        _reject()
    # Test key types before hashing/comparing against the trusted key set.
    if any(type(key) is not str for key in value) or frozenset(value) != keys:
        _reject()
    return value


def _name(value):
    if type(value) is not str or not 1 <= len(value) <= 64:
        _reject()
    if value != value.strip() or value in (".", ".."):
        _reject()
    if any(char in "/\\:" or unicodedata.category(char) in ("Cc", "Cf", "Cs")
           for char in value):
        _reject()
    return value


def _finite(value):
    if type(value) is not int and type(value) is not float:
        _reject()
    try:
        finite = math.isfinite(value)
    except OverflowError:
        _reject()
    if not finite:
        _reject()
    return value


def _length(value):
    value = _finite(value)
    if not 0.01 <= value <= 10000:
        _reject()
    return value


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        _reject()
    return value


def _geometry(value):
    value = _mapping(value, _GEOMETRY_KEYS)
    volume = _finite(value["volume_mm3"])
    if volume <= 0:
        _reject()
    dimensions = value["dimensions_mm"]
    if type(dimensions) is not list or len(dimensions) != 3:
        _reject()
    detached_dimensions = []
    for dimension in dimensions:
        dimension = _finite(dimension)
        if dimension <= 0:
            _reject()
        detached_dimensions.append(dimension)
    _integer(value["solids"], 1, 1)
    return {"volume_mm3": volume, "dimensions_mm": detached_dimensions, "solids": 1}


def validate_design_result(value, expected=None):
    """Return an exact detached DTO, optionally bound to body, pad and length.

    Only ordinary dictionaries, lists, strings and numeric primitives are read.
    All failures have the same local message and never include provider values.
    ``expected`` must be None or an exact three-field dictionary. Numeric length
    binding compares values, so 10 and 10.0 represent the same requested length.
    """
    value = _mapping(value, _RESULT_KEYS)
    if type(value["kind"]) is not str or value["kind"] != "pad_length":
        _reject()
    if (type(value["validation"]) is not str
            or value["validation"] != "native_valid_one_solid_and_length_scaled_volume"):
        _reject()
    body = _name(value["body"])
    pad = _name(value["pad"])
    length = _length(value["length_mm"])
    recompute_count = _integer(value["recompute_count"], 0, _SAFE_INTEGER)
    checked_objects = _integer(value["checked_objects"], 1, 64)
    geometry = _mapping(value["geometry"], frozenset(("pad", "body")))
    detached_geometry = {"pad": _geometry(geometry["pad"]),
                         "body": _geometry(geometry["body"])}
    if expected is not None:
        expected = _mapping(expected, _EXPECTED_KEYS)
        expected_body = _name(expected["body"])
        expected_pad = _name(expected["pad"])
        expected_length = _length(expected["length_mm"])
        if body != expected_body or pad != expected_pad or length != expected_length:
            _reject()
    return {"kind": "pad_length", "body": body, "pad": pad,
            "length_mm": length, "recompute_count": recompute_count,
            "checked_objects": checked_objects, "geometry": detached_geometry,
            "validation": "native_valid_one_solid_and_length_scaled_volume"}
