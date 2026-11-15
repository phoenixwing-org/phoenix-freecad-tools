# SPDX-License-Identifier: Apache-2.0
"""Prepare and create a constrained Part Design solid in a new, unsaved document."""

import math
import re


OPERATION = "create_basic_part"
MIN_MM = 0.1
MAX_MM = 1000
CAPABILITIES = {"version": 1, "operation": OPERATION, "confirmation": "native_gui_only",
                "target": "new_unsaved_document", "profiles": ["annulus", "rectangle", "circle"],
                "length_unit": "mm", "minimum_mm": MIN_MM, "maximum_mm": MAX_MM, "saves_document": False}
_PROFILE_FIELDS = {
    "annulus": ("outerDiameterMm", "innerDiameterMm"),
    "rectangle": ("widthMm", "heightMm"),
    "circle": ("diameterMm",),
}
_DIMENSION_NAMES = {
    "outerDiameterMm": "OuterDiameter", "innerDiameterMm": "InnerDiameter",
    "diameterMm": "Diameter", "widthMm": "Width", "heightMm": "Height", "thicknessMm": "Length",
}
_DOCUMENT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_MESSAGES = {
    "BASIC_PART_INVALID": "新建零件的名称、轮廓或毫米尺寸无效。",
    "BASIC_PART_CONTEXT_CHANGED": "仅可从无打开文档且无事务的窗口新建；请检查当前窗口后重新预览。",
    "BASIC_PART_NATIVE_UNAVAILABLE": "当前 FreeCAD 无法确认新建零件所需的原生功能。",
    "BASIC_PART_CREATE_UNVERIFIED": "新建文档状态无法确认，请检查当前窗口。",
    "BASIC_PART_GEOMETRY_UNVERIFIED": "零件的原生参数或几何未通过核对。",
    "BASIC_PART_WRITE_FAILED": "新建零件未完成；请检查未保存的文档和撤销记录。",
}


class PftBasicPartError(RuntimeError):
    def __init__(self, code):
        super().__init__(_MESSAGES[code])
        self.code = code


def _fail(code):
    raise PftBasicPartError(code) from None


def validate_request(value):
    """Return an owned copy of the exact, bounded parameters."""
    if type(value) is not dict:
        _fail("BASIC_PART_INVALID")
    profile, name = value.get("profile"), value.get("name")
    if type(profile) is not str or profile not in _PROFILE_FIELDS or type(name) is not str or not _DOCUMENT_NAME.fullmatch(name):
        _fail("BASIC_PART_INVALID")
    fields = (*_PROFILE_FIELDS[profile], "thicknessMm")
    if set(value) != {"profile", "name", *fields}:
        _fail("BASIC_PART_INVALID")
    if any(type(value[key]) not in (float, int) or not MIN_MM <= value[key] <= MAX_MM for key in fields):
        _fail("BASIC_PART_INVALID")
    if profile == "annulus" and (value["outerDiameterMm"] - value["innerDiameterMm"]) / 2 + 1e-10 < MIN_MM:
        _fail("BASIC_PART_INVALID")
    return {"profile": profile, "name": name, **{key: float(value[key]) for key in fields}}


def preview_rows(request):
    """List the new document, editable nodes and every dimension before creation."""
    profile = request["profile"]
    rows = [
        (request["name"], "Document", "New unsaved document"),
        ("Body", "TypeId", "PartDesign::Body"),
        ("Profile", "Sketcher::SketchObject", profile),
        ("Pad", "TypeId", "PartDesign::Pad"),
    ]
    rows.extend(("Profile", _DIMENSION_NAMES[key] + " (mm)", request[key]) for key in _PROFILE_FIELDS[profile])
    rows.append(("Pad", "Length (mm)", request["thicknessMm"]))
    return [{"object": obj, "field": field, "old": None, "new": value, "status": "change", "reason": ""}
            for obj, field, value in rows]


def require_blank_context(app, gui, controller):
    controller._ensure_main_thread()
    try:
        documents = app.listDocuments()
        blank = (type(documents) is dict and not documents and app.ActiveDocument is None and gui.activeDocument() is None
                 and app.getActiveTransaction() is None)
    except Exception:
        _fail("BASIC_PART_NATIVE_UNAVAILABLE")
    if not blank:
        _fail("BASIC_PART_CONTEXT_CHANGED")


def _require_owned_document(app, document, name):
    documents = app.listDocuments()
    if (document.Name != name or document.FileName != "" or app.ActiveDocument is not document
            or set(documents) != {name} or documents[name] is not document):
        _fail("BASIC_PART_CREATE_UNVERIFIED")


def _add_profile(app, part, sketcher, sketch, request, mutate):
    vector = app.Vector
    constraint = sketcher.Constraint
    profile = request["profile"]
    if profile == "rectangle":
        half_width, half_height = request["widthMm"] / 2, request["heightMm"] / 2
        corners = [(-half_width, -half_height), (half_width, -half_height),
                   (half_width, half_height), (-half_width, half_height)]
        for index, (x, y) in enumerate(corners):
            next_x, next_y = corners[(index + 1) % 4]
            mutate(sketch.addGeometry, part.LineSegment(vector(x, y, 0), vector(next_x, next_y, 0)), False)
        for index in range(4):
            mutate(sketch.addConstraint, constraint("Coincident", index, 2, (index + 1) % 4, 1))
            orientation = "Horizontal" if index % 2 == 0 else "Vertical"
            mutate(sketch.addConstraint, constraint(orientation, index))
        width = mutate(sketch.addConstraint, constraint("Distance", 0, request["widthMm"]))
        height = mutate(sketch.addConstraint, constraint("Distance", 1, request["heightMm"]))
        mutate(sketch.addConstraint, constraint("Symmetric", 0, 1, 2, 1, -1, 1))
        indexes = {"widthMm": width, "heightMm": height}
    else:
        indexes = {}
        for field in _PROFILE_FIELDS[profile]:
            diameter = request[field]
            index = mutate(sketch.addGeometry, part.Circle(vector(0, 0, 0), vector(0, 0, 1), diameter / 2), False)
            mutate(sketch.addConstraint, constraint("Coincident", index, 3, -1, 1))
            indexes[field] = mutate(sketch.addConstraint, constraint("Diameter", index, diameter))
    for field, index in indexes.items():
        mutate(sketch.renameConstraint, index, _DIMENSION_NAMES[field])
    return indexes


def _expected_metrics(request):
    profile = request["profile"]
    if profile == "rectangle":
        width, height = request["widthMm"], request["heightMm"]
        area = width * height
    else:
        width = height = request["outerDiameterMm"] if profile == "annulus" else request["diameterMm"]
        inner = request.get("innerDiameterMm", 0.0)
        area = math.pi * (width - inner) * (width + inner) / 4
    length = request["thicknessMm"]
    return (-width / 2, -height / 2, 0.0, width / 2, height / 2, length), area * length


def _close_enough(actual, expected):
    return math.isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-7)


def validate_result(value, request):
    profile = request["profile"]
    expected = {"kind": "basic_part", "profile": profile, "document": request["name"],
                "body": "Body", "sketch": "Profile", "pad": "Pad", "through_hole": profile == "annulus",
                "saves_document": False}
    if (type(value) is not dict or set(value) != {*expected, "dimensions_mm", "bounds_mm", "volume_mm3"}
            or any(type(value[key]) is not type(item) or value[key] != item for key, item in expected.items())):
        _fail("BASIC_PART_GEOMETRY_UNVERIFIED")
    measured, bounds, volume = value["dimensions_mm"], value["bounds_mm"], value["volume_mm3"]
    fields = (*_PROFILE_FIELDS[profile], "thicknessMm")
    expected_box, expected_volume = _expected_metrics(request)
    if (type(measured) is not dict or set(measured) != set(fields)
            or type(bounds) is not list or len(bounds) != 6):
        _fail("BASIC_PART_GEOMETRY_UNVERIFIED")
    measurements = [(measured[key], request[key]) for key in fields]
    measurements.extend(zip(bounds, expected_box))
    measurements.append((volume, expected_volume))
    if any(type(actual) not in (float, int) or not -1e12 <= actual <= 1e12
           or not _close_enough(actual, expected_value) for actual, expected_value in measurements):
        _fail("BASIC_PART_GEOMETRY_UNVERIFIED")
    return value


def _verify_solid(app, document, body, sketch, pad, request, dimensions):
    _require_owned_document(app, document, request["name"])
    profile_link = pad.Profile
    measured = {field: sketch.getDatum(index).Value for field, index in dimensions.items()}
    measured["thicknessMm"] = pad.Length.Value
    if (body.TypeId != "PartDesign::Body" or sketch.TypeId != "Sketcher::SketchObject" or pad.TypeId != "PartDesign::Pad"
            or body.Tip is not pad or sketch.FullyConstrained is not True
            or type(profile_link) not in (tuple, list) or len(profile_link) != 2
            or profile_link[0] is not sketch or profile_link[1]
            or sketch.getParentGeoFeatureGroup() is not body or pad.getParentGeoFeatureGroup() is not body
            or any(document.getObject(name) is not obj for name, obj in (("Body", body), ("Profile", sketch), ("Pad", pad)))
            or any(sketch.Constraints[index].Name != _DIMENSION_NAMES[field]
                   for field, index in dimensions.items())):
        _fail("BASIC_PART_GEOMETRY_UNVERIFIED")
    expected_nodes = {body.Name, sketch.Name, pad.Name, body.Origin.Name,
                      *(feature.Name for feature in body.Origin.OriginFeatures)}
    if {obj.Name for obj in document.Objects} != expected_nodes:
        _fail("BASIC_PART_GEOMETRY_UNVERIFIED")
    shape = body.Shape
    if shape.isNull() or not shape.isValid() or len(shape.Solids) != 1:
        _fail("BASIC_PART_GEOMETRY_UNVERIFIED")
    # Use exact topology rather than view-generated triangulation.
    box = shape.optimalBoundingBox(False, False)
    bounds = [box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax]
    volume = shape.Volume
    through_hole = request["profile"] == "annulus"
    if through_hole:
        inner, outer, length = request["innerDiameterMm"], request["outerDiameterMm"], request["thicknessMm"]
        for z in (length / 4, length / 2, 3 * length / 4):
            if (shape.isInside(app.Vector(0, 0, z), 1e-7, False)
                    or not shape.isInside(app.Vector((inner + outer) / 4, 0, z), 1e-7, False)):
                _fail("BASIC_PART_GEOMETRY_UNVERIFIED")
    return validate_result({"kind": "basic_part", "profile": request["profile"], "document": document.Name,
            "body": body.Name, "sketch": sketch.Name, "pad": pad.Name, "dimensions_mm": measured, "bounds_mm": bounds,
            "volume_mm3": volume, "through_hole": through_hole, "saves_document": False}, request)


def apply_plan(app, gui, request, controller):
    """Run only from the business layer's native approval callback."""
    request = validate_request(request)
    require_blank_context(app, gui, controller)
    try:
        import Part
        import PartDesign  # Registers the native feature types.
        import Sketcher
    except ImportError:
        _fail("BASIC_PART_NATIVE_UNAVAILABLE")
    # Module initialization can run host callbacks. Revalidate before creation.
    require_blank_context(app, gui, controller)
    name = request["name"]
    try:
        document = app.newDocument(name)
        _require_owned_document(app, document, name)
    except Exception:
        _fail("BASIC_PART_CREATE_UNVERIFIED")
    try:
        controller._ensure_idle(app, gui, document)
        if document.Objects:
            _fail("BASIC_PART_CREATE_UNVERIFIED")
        result = []

        def write(check_owner):
            def mutate(action, *args):
                _require_owned_document(app, document, name)
                check_owner()
                output = action(*args)
                check_owner(require_pending=True)
                return output

            body = mutate(document.addObject, "PartDesign::Body", "Body")
            sketch = mutate(body.newObject, "Sketcher::SketchObject", "Profile")
            indexes = _add_profile(app, Part, Sketcher, sketch, request, mutate)
            pad = mutate(body.newObject, "PartDesign::Pad", "Pad")
            mutate(setattr, pad, "Profile", sketch)
            mutate(setattr, pad, "Length", request["thicknessMm"])
            mutate(setattr, body, "Tip", pad)
            mutate(document.recompute)
            mutate(setattr, sketch.ViewObject, "Visibility", False)
            result.extend((body, sketch, pad, indexes))
            _verify_solid(app, document, body, sketch, pad, request, indexes)
            check_owner()

        def rollback():
            _require_owned_document(app, document, name)
            if document.Objects:
                _fail("BASIC_PART_WRITE_FAILED")

        def committed():
            body, sketch, pad, indexes = result
            result.append(_verify_solid(app, document, body, sketch, pad, request, indexes))

        controller._run_transaction(app, document, "Phoenix: create basic part", write, rollback, committed)
        return result[-1]
    except (PftBasicPartError, controller.PftWriteError):
        raise
    except Exception:
        _fail("BASIC_PART_WRITE_FAILED")
