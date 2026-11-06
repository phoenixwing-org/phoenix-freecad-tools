# SPDX-License-Identifier: Apache-2.0
"""Exact drawing-draft data contracts; no CAD, Qt, filesystem or service access.

These validators check detached reported data, not rendered projection. A valid
result always retains its explicit unverified/manual-review boundary.
"""
import math
import unicodedata

_ERROR = "图纸草稿结果格式无效。"
_RESULT_KEYS = frozenset(("kind", "page", "template", "source", "views", "scale", "projection",
                          "paper", "projection_verification", "manual_review_required"))
_REQUEST_KEYS = frozenset(("source", "page"))
_VIEW_KEYS = frozenset(("name", "kind"))
_VIEW_KINDS = ("front", "top", "axonometric")


def _reject():
    raise ValueError(_ERROR)


def _mapping(value, keys):
    if (type(value) is not dict or len(value) != len(keys)
            or any(type(key) is not str for key in value) or frozenset(value) != keys):
        _reject()
    return value


def _name(value):
    if (type(value) is not str or not 1 <= len(value) <= 64
            or value != value.strip() or value in (".", "..")
            or any(char in "/\\:" or unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value)):
        _reject()
    return value


def validate_design_request(value, allow_blocked=False):
    value = _mapping(value, _REQUEST_KEYS)
    source = _name(value["source"])
    page = None if allow_blocked is True and value["page"] is None else _name(value["page"])
    if page == source:
        _reject()
    return {"source": source, "page": page}


def validate_design_result(value, expected=None):
    value = _mapping(value, _RESULT_KEYS)
    literals = {"kind": "drawing_draft", "projection": "first_angle", "paper": "A4_landscape",
                "projection_verification": "not_checked"}
    if any(type(value[key]) is not str or value[key] != literal for key, literal in literals.items()):
        _reject()
    if value["manual_review_required"] is not True:
        _reject()
    source, page, template = (_name(value[key]) for key in ("source", "page", "template"))
    scale = value["scale"]
    if type(scale) not in (int, float):
        _reject()
    try:
        if not math.isfinite(scale) or scale <= 0:
            _reject()
    except OverflowError:
        _reject()
    views = value["views"]
    if type(views) is not list or len(views) != 3:
        _reject()
    detached_views = []
    for view, kind in zip(views, _VIEW_KINDS):
        view = _mapping(view, _VIEW_KEYS)
        if type(view["kind"]) is not str or view["kind"] != kind:
            _reject()
        detached_views.append({"name": _name(view["name"]), "kind": kind})
    names = [page, template] + [view["name"] for view in detached_views]
    if len(set(names)) != 5 or source in names:
        _reject()
    if expected is not None:
        expected = validate_design_request(expected)
        if source != expected["source"] or page != expected["page"]:
            _reject()
    return {"kind": "drawing_draft", "page": page, "template": template, "source": source,
            "views": detached_views, "scale": scale, "projection": "first_angle", "paper": "A4_landscape",
            "projection_verification": "not_checked", "manual_review_required": True}
