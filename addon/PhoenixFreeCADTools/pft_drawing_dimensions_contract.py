# SPDX-License-Identifier: Apache-2.0
"""Exact detached DTOs for a limited associative TechDraw dimension operation.

Validation performs no CAD, Qt, filesystem or service access. Native reference
and value checks do not establish projection freshness or a finished drawing.
"""
import math
import re
import unicodedata

KINDS = ("Distance", "DistanceX", "DistanceY", "Diameter", "Radius")
CAPABILITIES = {
    "version": 1,
    "operation": "add_drawing_dimensions",
    "confirmation": "native_gui_only",
    "preview_method": "prepare_part_changes",
    "request_method": "request_part_change_approval",
    "status_method": "get_part_change_status",
    "kinds": list(KINDS),
    "max_dimensions": 8,
    "edge_index_base": 0,
    "maximum_edge_index": 2047,
    "position_scope": "view_relative_paper_mm",
    "measurement_unit": "mm",
    "saves_document": False,
    "projection_verification": "not_checked",
}

_ERROR = "图纸尺寸数据格式无效。"
_REQUEST_KEYS = frozenset(("page", "view", "dimensions"))
_REQUEST_ROW_KEYS = frozenset(("edge", "kind", "x_mm", "y_mm"))
_RESULT_KEYS = frozenset(("kind", "page", "view", "dimensions", "measurement_verification",
                          "projection_verification", "manual_review_required"))
_RESULT_ROW_KEYS = _REQUEST_ROW_KEYS | frozenset(("name", "value_mm"))
_EDGE = re.compile(r"Edge(?:0|[1-9][0-9]{0,3})\Z")


def _reject():
    raise ValueError(_ERROR)


def _mapping(value, keys):
    if (type(value) is not dict or len(value) != len(keys)
            or any(type(key) is not str for key in value) or frozenset(value) != keys):
        _reject()
    return value


def _name(value):
    # Match the established drawing-contract rules without requiring imports
    # from an ambient module path when this pure contract is loaded in isolation.
    if (type(value) is not str or not 1 <= len(value) <= 64
            or value != value.strip() or value in (".", "..")
            or any(char in "/\\:" or unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value)):
        _reject()
    return value


def _number(value, minimum, maximum, exclusive_minimum=False):
    if type(value) not in (int, float):
        _reject()
    # Bound before isfinite so arbitrarily large Python integers never overflow
    # during a conversion to float. No numeric/native/string coercion is used.
    if (value < minimum or value > maximum or exclusive_minimum and value == minimum
            or not math.isfinite(value)):
        _reject()
    return value


def _row(value, keys):
    value = _mapping(value, keys)
    edge, kind = value["edge"], value["kind"]
    if (type(edge) is not str or _EDGE.fullmatch(edge) is None or int(edge[4:]) > 2047
            or type(kind) is not str or kind not in KINDS):
        _reject()
    return {"edge": edge, "kind": kind,
            "x_mm": _number(value["x_mm"], -1000, 1000),
            "y_mm": _number(value["y_mm"], -1000, 1000)}


def _dimensions(value, keys):
    if type(value) is not list or not 1 <= len(value) <= 8:
        _reject()
    rows, seen = [], set()
    for entry in value:
        row = _row(entry, keys)
        identity = (row["edge"], row["kind"])
        if identity in seen:
            _reject()
        seen.add(identity)
        rows.append(row)
    return rows


def _owners(value):
    page, view = _name(value["page"]), _name(value["view"])
    if page == view:
        _reject()
    return page, view


def validate_request(value):
    value = _mapping(value, _REQUEST_KEYS)
    page, view = _owners(value)
    return {"page": page, "view": view,
            "dimensions": _dimensions(value["dimensions"], _REQUEST_ROW_KEYS)}


def validate_result(value, expected=None):
    value = _mapping(value, _RESULT_KEYS)
    literals = {"kind": "drawing_dimensions",
                "measurement_verification": "native_reference_and_value_checked",
                "projection_verification": "not_checked"}
    if any(type(value[key]) is not str or value[key] != literal for key, literal in literals.items()):
        _reject()
    if value["manual_review_required"] is not True:
        _reject()
    page, view = _owners(value)
    rows = _dimensions(value["dimensions"], _RESULT_ROW_KEYS)
    names = {page, view}
    for row, entry in zip(rows, value["dimensions"]):
        name = _name(entry["name"])
        if name in names:
            _reject()
        names.add(name)
        row["name"] = name
        row["value_mm"] = _number(entry["value_mm"], 0, 1e9, exclusive_minimum=True)
    if expected is not None:
        expected = validate_request(expected)
        if page != expected["page"] or view != expected["view"] or len(rows) != len(expected["dimensions"]):
            _reject()
        for row, request in zip(rows, expected["dimensions"]):
            if any(row[key] != request[key] for key in _REQUEST_ROW_KEYS):
                _reject()
    return {"kind": "drawing_dimensions", "page": page, "view": view, "dimensions": rows,
            "measurement_verification": "native_reference_and_value_checked",
            "projection_verification": "not_checked", "manual_review_required": True}
