# SPDX-License-Identifier: Apache-2.0
"""Run after parametric_plate.py, as a new execute_script request.

Exports only that public demo to a private temporary STEP, reads its BRep, and
rebuilds a second editable body from the explicitly known demo dimensions.
This is NOT automatic recovery of arbitrary STEP feature history.
"""
import math
import os
import tempfile
import FreeCAD as App
import Part
import Sketcher
import PartDesign


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _solve(sketch):
    code = sketch.solve()
    _require(code in (0, None), "Sketch solver reported an error: " + sketch.Name)
    _require(bool(sketch.FullyConstrained), "Sketch must be fully constrained: " + sketch.Name)


def _build_known_plate(doc):
    # Self-contained because execute_script starts with fresh globals. The
    # dimensions below are declared demo inputs, not reverse-engineered data.
    body = doc.addObject("PartDesign::Body", "RebuiltBody")
    outline = body.newObject("Sketcher::SketchObject", "OutlineSketch")
    points = [(-30.0, -20.0), (30.0, -20.0), (30.0, 20.0), (-30.0, 20.0)]
    for index in range(4):
        start, end = points[index], points[(index + 1) % 4]
        outline.addGeometry(Part.LineSegment(App.Vector(start[0], start[1], 0), App.Vector(end[0], end[1], 0)), False)
    for index in range(4):
        outline.addConstraint(Sketcher.Constraint("Coincident", index, 2, (index + 1) % 4, 1))
        outline.addConstraint(Sketcher.Constraint("Horizontal" if index % 2 == 0 else "Vertical", index))
    width = outline.addConstraint(Sketcher.Constraint("Distance", 0, 60.0))
    height = outline.addConstraint(Sketcher.Constraint("Distance", 1, 40.0))
    outline.renameConstraint(width, "Width")
    outline.renameConstraint(height, "Height")
    outline.addConstraint(Sketcher.Constraint("Symmetric", 0, 1, 2, 1, -1, 1))
    doc.recompute()
    _solve(outline)
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile, pad.Length, pad.Reversed = outline, 8.0, False
    doc.recompute()
    holes = body.newObject("Sketcher::SketchObject", "HoleSketch")
    holes.setExpression("Placement.Base.z", pad.Name + ".Length")
    for index, (x, y) in enumerate([(-20.0, -10.0), (20.0, -10.0), (20.0, 10.0), (-20.0, 10.0)]):
        holes.addGeometry(Part.Circle(App.Vector(x, y, 0), App.Vector(0, 0, 1), 3.0), False)
        holes.addConstraint(Sketcher.Constraint("Radius", index, 3.0))
        holes.addConstraint(Sketcher.Constraint("DistanceX", index, 3, x))
        holes.addConstraint(Sketcher.Constraint("DistanceY", index, 3, y))
    doc.recompute()
    _solve(holes)
    pocket = body.newObject("PartDesign::Pocket", "Pocket")
    pocket.Profile, pocket.Type, pocket.Reversed = holes, 1, False
    body.Tip = pocket
    doc.recompute()
    for feature in (outline, pad, holes):
        feature.ViewObject.Visibility = False
    pocket.ViewObject.Visibility = True
    body.ViewObject.Visibility = True
    return body, outline, holes


def _metrics(shape):
    _require(not shape.isNull(), "An expected shape is empty")
    box = shape.BoundBox
    return {"bbox": [box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax],
            "volume": shape.Volume, "valid": bool(shape.isValid()), "solids": len(shape.Solids),
            "units": {"bbox": "mm", "volume": "mm^3"}}


def _compare(left, right):
    a, b = _metrics(left), _metrics(right)
    difference = left.cut(right).Volume + right.cut(left).Volume
    comparison = {"maxdiff_bbox_mm": max(abs(x - y) for x, y in zip(a["bbox"], b["bbox"])),
                  "volume_difference_mm3": abs(a["volume"] - b["volume"]),
                  "symmetric_difference_volume_mm3": difference}
    tolerance = max(1e-5, a["volume"] * 1e-7)
    _require(a["valid"] and b["valid"] and a["solids"] == b["solids"] == 1, "Comparison requires two valid single solids")
    _require(comparison["maxdiff_bbox_mm"] < 1e-6, "STEP/rebuild bounding box differs")
    _require(comparison["volume_difference_mm3"] < tolerance, "STEP/rebuild volume differs")
    _require(difference < tolerance, "STEP/rebuild geometric difference exceeds tolerance")
    comparison["volume_tolerance_mm3"] = tolerance
    return comparison


_require("PhoenixParametricDemo" in App.listDocuments(), "Run parametric_plate.py first")
_require("PhoenixStepDemo" not in App.listDocuments(), "PhoenixStepDemo already exists; refusing to overwrite or close it")
source_document = App.getDocument("PhoenixParametricDemo")
source_body = source_document.getObject("PlateBody")
_require(source_body is not None and source_body.TypeId == "PartDesign::Body", "The public demo PlateBody is missing")
source_shape = source_body.Shape.copy()
source_before = _metrics(source_shape)
known_parameters = {"width_mm": 60.0, "height_mm": 40.0, "thickness_mm": 8.0,
                    "hole_diameter_mm": 6.0, "hole_centres_mm": [[-20, -10], [20, -10], [20, 10], [-20, 10]]}
expected_volume = (2400.0 - 36.0 * math.pi) * 8.0
_require(source_before["valid"] and source_before["solids"] == 1 and abs(source_before["volume"] - expected_volume) < 1e-6,
         "Source no longer matches the known public demo; no arbitrary STEP inference is attempted")
source_objects_before = tuple(obj.Name for obj in source_document.Objects)
document = App.newDocument("PhoenixStepDemo")
document.openTransaction("Phoenix: temporary STEP round trip and known-parameter rebuild")
temporary_directory = None
try:
    with tempfile.TemporaryDirectory(prefix="phoenix-step-demo-") as temporary_directory:
        step_file = os.path.join(temporary_directory, "public_plate.step")
        source_shape.exportStep(step_file)
        _require(os.path.getsize(step_file) > 0, "Temporary STEP export is empty")
        imported_shape = Part.read(step_file)
        imported = document.addObject("Part::Feature", "ImportedSTEP")
        imported.Shape = imported_shape
        document.recompute()
        imported_metrics = _metrics(imported.Shape)
        original_to_imported = _compare(source_shape, imported.Shape)
        _require(imported.TypeId == "Part::Feature" and not imported.OutList, "Imported STEP must remain a plain BRep feature")
        imported_only_types = [obj.TypeId for obj in document.Objects]
        _require(not any(kind.startswith("Sketcher::") or kind in {"PartDesign::Pad", "PartDesign::Pocket"} for kind in imported_only_types),
                 "The import unexpectedly contains parametric history")
        rebuilt, outline, holes = _build_known_plate(document)
        rebuilt_metrics = _metrics(rebuilt.Shape)
        imported_to_rebuilt = _compare(imported.Shape, rebuilt.Shape)
        original_to_rebuilt = _compare(source_shape, rebuilt.Shape)
        imported.ViewObject.Visibility = False
    _require(not os.path.exists(temporary_directory), "Temporary STEP directory was not removed")
    _require(tuple(obj.Name for obj in source_document.Objects) == source_objects_before, "Source document object list changed unexpectedly")
    source_after = _metrics(source_body.Shape)
    _require(source_after == source_before, "Source body geometry changed unexpectedly")
    document.commitTransaction()
except BaseException:
    document.abortTransaction()
    raise

result = {
    "demo": "STEP_BRep_and_known_parameter_rebuild", "document": document.Name,
    "source_document": source_document.Name, "source_body": source_body.Name,
    "imported": {"object": imported.Name, "typeId": imported.TypeId, "original_feature_history_recovered": False, **imported_metrics},
    "rebuilt": {"object": rebuilt.Name, "mode": "explicit_known_demo_parameters", "parameters": known_parameters,
                "sketchDOF": {outline.Name: 0, holes.Name: 0}, "sketchDOF_evidence": "Native FullyConstrained=true after solve and recompute", **rebuilt_metrics},
    "comparison": {"original_to_imported": original_to_imported, "imported_to_rebuilt": imported_to_rebuilt, "original_to_rebuilt": original_to_rebuilt},
    "source_unchanged": True, "temporary_step_removed": True, "saved": False,
    "automatic_step_history_recovery": False, "modified_documents": [document.Name],
}
print("PhoenixStepDemo: temporary STEP round trip verified; rebuilt editable body from known demo dimensions, not automatic history recovery.")
