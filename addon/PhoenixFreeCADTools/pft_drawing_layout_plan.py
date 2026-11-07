# SPDX-License-Identifier: Apache-2.0
"""Freeze a layout intent, its native inputs and one complete suggestion.

This component does not register a tool, display a dialog, authorize a change,
create a CAD object or save a file. Existing dimension approval owns those steps.
Verification checks the frozen result; it never requests another arrangement.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

_MARKER = "drawing_layout_plan_v1"
_KEYS = frozenset(("_plan", "intent", "request", "graphics", "native_context", "fingerprint"))
CAPABILITIES = {"version": 1, "operation": "add_drawing_dimensions", "algorithm": "paper_lanes_v1",
    "profile": "freecad_1_1_4_a4_mm_v1", "confirmation": "native_gui_only", "max_dimensions": 8,
    "max_input_bytes": 65536, "max_result_bytes": 32768, "max_plan_bytes": 114688,
    "max_candidates_per_dimension": 64, "max_search_attempts": 8192, "max_geometry_checks": 131072,
    "layout_verification": "suggested_only", "saves_document": False}
_REASONS = {
    "DRAWING_LAYOUT_PROFILE_UNSUPPORTED": "图页或来源超出当前尺寸布局范围。",
    "DRAWING_LAYOUT_PAGE_OPEN_REQUIRED": "请先打开目标图页，再重新生成尺寸布局预览。",
    "DRAWING_LAYOUT_TEMPLATE_UNSUPPORTED": "尺寸布局需要已核验的内置A4横向图框。",
    "DRAWING_LAYOUT_STYLE_UNSUPPORTED": "当前单位、字体或尺寸样式暂不支持布局建议。",
    "DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE": "已有标注或图形障碍无法完整读取。",
    "DRAWING_LAYOUT_PLAN_STALE": "原生布局输入已变化，请重新预览。",
    "DRAWING_LAYOUT_INVALID_ARGUMENT": "尺寸布局输入格式无效。",
    "DRAWING_LAYOUT_INVALID_RESULT": "尺寸布局建议无法完整验证。",
    "DRAWING_LAYOUT_TOO_LARGE": "尺寸布局数据超过有限预算，请减少本批项目。",
}


class PftDrawingLayoutPlanError(RuntimeError):
    def __init__(self, stale=False, cleanup_state="complete", reason_code=None):
        self.code = "DIMENSION_LAYOUT_STALE" if stale else "DIMENSION_LAYOUT_UNAVAILABLE"
        self.cleanup_state = "complete" if type(cleanup_state) is str and cleanup_state == "complete" else "unknown"
        self.reason_code = reason_code if type(reason_code) is str and reason_code in _REASONS else None
        default = "尺寸布局输入已变化，请重新预览。" if stale else "尺寸布局输入或建议无法完整验证。"
        super().__init__(_REASONS[self.reason_code] if not stale and self.reason_code else default)


def _failure(error, *, stale=False):
    reason, cleanup = None, "complete"
    for name in ("reason_code", "code"):
        try:
            value = getattr(error, name, None)
        except Exception:
            continue
        if type(value) is str and value in _REASONS:
            reason = value
            break
    try:
        state = getattr(error, "cleanup_state", "complete")
        cleanup = "complete" if type(state) is str and state == "complete" else "unknown"
    except Exception:
        cleanup = "unknown"
    return PftDrawingLayoutPlanError(stale=stale, cleanup_state=cleanup, reason_code=reason)


def _sibling(name):
    filename = Path(__file__).resolve().with_name(name + ".py")
    key = "_pft_layout_plan_" + name
    module = sys.modules.get(key)
    if module is not None:
        if Path(getattr(module, "__file__", "")).resolve() != filename:
            raise PftDrawingLayoutPlanError()
        return module
    spec = importlib.util.spec_from_file_location(key, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(key, None)
        raise
    return module


def _bind_input(intent, source):
    if source["view"]["name"] != intent["view"] or len(source["dimensions"]) != len(intent["dimensions"]):
        raise ValueError()
    if not any(row.get("placement") == "auto" for row in intent["dimensions"]):
        # The established explicit-only path keeps its original validation and
        # positioning semantics instead of silently entering the new solver.
        raise ValueError()
    for before, row in zip(intent["dimensions"], source["dimensions"]):
        automatic = before.get("placement") == "auto"
        if (row["edge"] != before["edge"] or row["kind"] != before["kind"]
                or row["placement"] != ("auto" if automatic else "explicit")
                or not automatic and any(row[key] != before[key] for key in ("x_mm", "y_mm"))):
            raise ValueError()


def _payload(intent, source, result):
    contract = _sibling("pft_drawing_dimensions_contract")
    intent = contract.validate_intent(intent)
    _bind_input(intent, source)
    request = None
    if result["can_apply"]:
        request = contract.validate_resolution(intent, {
            "page": intent["page"], "view": intent["view"],
            "dimensions": [{key: row[key] for key in ("edge", "kind", "x_mm", "y_mm")}
                           for row in result["dimensions"]]})
    graphics = {"version": 1, "input": source, "result": result}
    payload = {"intent": intent, "request": request, "graphics": graphics}
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
    if len(encoded) > 112 * 1024:
        raise ValueError()
    return payload, hashlib.sha256(encoded).hexdigest()


def prepare(app, gui, document, page, view, intent, controller):
    """Freeze native inputs, solve once and detach every public-facing DTO."""
    try:
        contract, core = _sibling("pft_drawing_dimensions_contract"), _sibling("pft_drawing_layout")
        intent = contract.validate_intent(intent)
        if not any(row.get("placement") == "auto" for row in intent["dimensions"]):
            raise ValueError()
        if page.Name != intent["page"] or view.Name != intent["view"]:
            raise ValueError()
        native = _sibling("pft_drawing_layout_profile")
        context = native.snapshot(app, gui, document, page, view, deepcopy(intent), controller)
        source = core.validate_input(context["input"])
        _bind_input(intent, source)
        result = core.validate_result(core.suggest_layout(deepcopy(source)), expected=source)
        payload, fingerprint = _payload(intent, source, result)
        native.verify(app, gui, context, controller)
        if core.validate_input(context["input"]) != source:
            raise ValueError()
        return {"_plan": _MARKER, **deepcopy(payload), "native_context": context, "fingerprint": fingerprint}
    except Exception as error:
        raise _failure(error) from None


def _checked(plan):
    if (type(plan) is not dict or any(type(key) is not str for key in plan) or frozenset(plan) != _KEYS
            or type(plan["_plan"]) is not str or plan["_plan"] != _MARKER
            or type(plan["fingerprint"]) is not str or len(plan["fingerprint"]) != 64):
        raise ValueError()
    core = _sibling("pft_drawing_layout")
    graphics = plan["graphics"]
    if (type(graphics) is not dict or any(type(key) is not str for key in graphics)
            or set(graphics) != {"version", "input", "result"}
            or type(graphics["version"]) is not int or graphics["version"] != 1):
        raise ValueError()
    source = core.validate_input(graphics["input"])
    result = core.validate_result(graphics["result"], expected=source)
    payload, fingerprint = _payload(plan["intent"], source, result)
    if plan["request"] is not None:
        _sibling("pft_drawing_dimensions_contract").validate_request(plan["request"])
    if (graphics != payload["graphics"] or plan["request"] != payload["request"]
            or plan["fingerprint"] != fingerprint
            or core.validate_input(plan["native_context"]["input"]) != source):
        raise ValueError()
    return deepcopy(payload), fingerprint, plan["native_context"]


def validate_preview(value, request, *, can_apply):
    """Validate the complete public estimate, without native reads or search.

    A blocked intent has no resolved request. Unknown native inputs produce a
    fixed reason and no graphics; a solver rejection retains its complete
    detached diagram and never manufactures partial or placeholder positions.
    """
    try:
        keys = {"version", "layout_verification", "intent", "graphics", "reason_code"}
        if (type(value) is not dict or any(type(key) is not str for key in value) or set(value) != keys
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["layout_verification"]) is not str or value["layout_verification"] != "suggested_only"
                or type(can_apply) is not bool):
            raise ValueError()
        intent = _sibling("pft_drawing_dimensions_contract").validate_intent(value["intent"])
        if not any(row.get("placement") == "auto" for row in intent["dimensions"]):
            raise ValueError()
        reason = value["reason_code"]
        if reason is not None and (type(reason) is not str or reason not in _REASONS):
            raise ValueError()
        graphics = value["graphics"]
        if graphics is None:
            if can_apply or request is not None or reason is None:
                raise ValueError()
        else:
            if (reason is not None or type(graphics) is not dict
                    or any(type(key) is not str for key in graphics)
                    or set(graphics) != {"version", "input", "result"}
                    or type(graphics["version"]) is not int or graphics["version"] != 1):
                raise ValueError()
            core = _sibling("pft_drawing_layout")
            source = core.validate_input(graphics["input"])
            result = core.validate_result(graphics["result"], expected=source)
            payload, _ = _payload(intent, source, result)
            if payload["request"] != request or result["can_apply"] is not can_apply:
                raise ValueError()
            if request is not None:
                _sibling("pft_drawing_dimensions_contract").validate_request(request)
            graphics = payload["graphics"]
        result = {"version": 1, "layout_verification": "suggested_only", "intent": intent,
                  "graphics": graphics, "reason_code": reason}
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf8")) > 112 * 1024:
            raise ValueError()
        return deepcopy(result)
    except Exception:
        raise ValueError("DRAWING_LAYOUT_PREVIEW_INVALID") from None


def preview(plan):
    """Detach the exact frozen estimate for clients and the native dialog."""
    payload, _, _ = _checked(plan)
    return validate_preview({"version": 1, "layout_verification": "suggested_only",
        "intent": payload["intent"], "graphics": payload["graphics"], "reason_code": None},
        payload["request"], can_apply=payload["graphics"]["result"]["can_apply"])


def unavailable_preview(intent, error):
    """Return only a validated intent and one safe reason for missing inputs."""
    reason = _failure(error).reason_code or "DRAWING_LAYOUT_INVALID_RESULT"
    return validate_preview({"version": 1, "layout_verification": "suggested_only", "intent": intent,
                             "graphics": None, "reason_code": reason}, None, can_apply=False)


def verify(app, gui, plan, controller, *, defaults_only=False):
    """Revalidate all frozen data and native inputs, without rerunning the solver."""
    try:
        if type(defaults_only) is not bool:
            raise ValueError()
        before, fingerprint, context = _checked(plan)
        native = _sibling("pft_drawing_layout_profile")
        callback = native.verify_defaults if defaults_only else native.verify
        callback(app, gui, context, controller)
        after, current_fingerprint, current_context = _checked(plan)
        if after != before or current_fingerprint != fingerprint or current_context is not context:
            raise ValueError()
    except Exception as error:
        raise _failure(error, stale=True) from None


def verify_created(app, gui, plan, created, controller):
    """Bind a new-native-style check to this same immutable layout plan."""
    try:
        before, fingerprint, context = _checked(plan)
        _sibling("pft_drawing_layout_profile").verify_created_style(app, gui, context, created, controller)
        after, current_fingerprint, current_context = _checked(plan)
        if before != after or fingerprint != current_fingerprint or current_context is not context:
            raise ValueError()
        return True
    except Exception as error:
        failure = _failure(error)
        failure.code = "DIMENSION_LAYOUT_STYLE_UNVERIFIED"
        failure.args = ("新尺寸的实际样式未通过布局计划核对；请检查文档和撤销记录。",)
        raise failure from None
