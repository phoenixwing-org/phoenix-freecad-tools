# SPDX-License-Identifier: Apache-2.0
"""Execute as one Phoenix execute_script request on FreeCAD's GUI thread.

Creates one unsaved public demonstration document. Rejects a name collision;
never closes a document. Failure aborts this script's document transaction.
"""
import math
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
    _require(bool(sketch.FullyConstrained), "Sketch must have zero remaining freedom: " + sketch.Name)


def _build_plate(doc, body_name, thickness):
    body = doc.addObject("PartDesign::Body", body_name)
    outline = body.newObject("Sketcher::SketchObject", "OutlineSketch")
    corners = [(-30.0, -20.0), (30.0, -20.0), (30.0, 20.0), (-30.0, 20.0)]
    for index, point in enumerate(corners):
        end = corners[(index + 1) % 4]
        outline.addGeometry(Part.LineSegment(App.Vector(point[0], point[1], 0), App.Vector(end[0], end[1], 0)), False)
    for index in range(4):
        outline.addConstraint(Sketcher.Constraint("Coincident", index, 2, (index + 1) % 4, 1))
        outline.addConstraint(Sketcher.Constraint("Horizontal" if index % 2 == 0 else "Vertical", index))
    width = outline.addConstraint(Sketcher.Constraint("Distance", 0, 60.0))
    height = outline.addConstraint(Sketcher.Constraint("Distance", 1, 40.0))
    outline.renameConstraint(width, "Width")
    outline.renameConstraint(height, "Height")
    # Opposite corners are symmetric about the sketch origin. Width/height
    # remain editable while the rectangle remains centred on that origin.
    outline.addConstraint(Sketcher.Constraint("Symmetric", 0, 1, 2, 1, -1, 1))
    doc.recompute()
    _solve(outline)

    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = outline
    pad.Length = thickness
    pad.Reversed = False
    doc.recompute()
    _require(not pad.Shape.isNull(), "Pad did not produce a shape")

    holes = body.newObject("Sketcher::SketchObject", "HoleSketch")
    # Attach by a dimensional expression, avoiding a fragile FaceN support.
    holes.setExpression("Placement.Base.z", pad.Name + ".Length")
    for index, (x, y) in enumerate([(-20.0, -10.0), (20.0, -10.0), (20.0, 10.0), (-20.0, 10.0)]):
        holes.addGeometry(Part.Circle(App.Vector(x, y, 0), App.Vector(0, 0, 1), 3.0), False)
        holes.addConstraint(Sketcher.Constraint("Radius", index, 3.0))
        holes.addConstraint(Sketcher.Constraint("DistanceX", index, 3, x))
        holes.addConstraint(Sketcher.Constraint("DistanceY", index, 3, y))
    doc.recompute()
    _solve(holes)

    pocket = body.newObject("PartDesign::Pocket", "Pocket")
    pocket.Profile = holes
    pocket.Type = 1  # FreeCAD PartDesign Pocket: ThroughAll.
    pocket.Reversed = False
    body.Tip = pocket
    doc.recompute()
    for feature in (outline, pad, holes):
        feature.ViewObject.Visibility = False
    body.ViewObject.Visibility = True
    pocket.ViewObject.Visibility = True
    return body, outline, pad, holes, pocket


def _snapshot(body, outline, holes, thickness):
    shape = body.Shape
    _require(not shape.isNull(), "Final body shape is empty")
    box = shape.BoundBox
    bbox = [box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax]
    expected_box = [-30.0, -20.0, 0.0, 30.0, 20.0, float(thickness)]
    expected_volume = (60.0 * 40.0 - 4.0 * math.pi * 3.0 ** 2) * thickness
    _require(bool(outline.FullyConstrained) and bool(holes.FullyConstrained), "Both sketches must remain fully constrained")
    result = {
        "bbox": bbox, "volume": shape.Volume,
        "expected": {"bbox": expected_box, "volume": expected_volume},
        "maxdiff": {"bbox_mm": max(abs(a - b) for a, b in zip(bbox, expected_box)), "volume_mm3": abs(shape.Volume - expected_volume)},
        "valid": bool(shape.isValid()), "solids": len(shape.Solids),
        "sketchDOF": {outline.Name: 0, holes.Name: 0},
        "sketchDOF_evidence": "Native FullyConstrained=true after solve and recompute; 0 is inferred from that flag",
        "units": {"bbox": "mm", "volume": "mm^3"},
    }
    _require(result["valid"] and result["solids"] == 1, "Expected exactly one valid solid")
    _require(result["maxdiff"]["bbox_mm"] < 1e-7, "Plate bounding box is outside tolerance")
    _require(result["maxdiff"]["volume_mm3"] < 1e-6, "Plate volume is outside tolerance")
    return result


DOCUMENT_NAME = "PhoenixParametricDemo"
_require(DOCUMENT_NAME not in App.listDocuments(), "PhoenixParametricDemo already exists; refusing to overwrite or close it")
document = App.newDocument(DOCUMENT_NAME)
document.openTransaction("Phoenix: constrained plate and thickness edit")
try:
    body, outline, pad, holes, pocket = _build_plate(document, "PlateBody", 6.0)
    initial = _snapshot(body, outline, holes, 6.0)
    pad.Length = 8.0
    document.recompute()
    _solve(outline)
    _solve(holes)
    document.recompute()
    updated = _snapshot(body, outline, holes, 8.0)
    _require(abs(holes.Placement.Base.z - 8.0) < 1e-7, "Hole plane did not follow Pad.Length")
    document.commitTransaction()
except BaseException:
    document.abortTransaction()
    raise

result = {
    "demo": "editable_constrained_plate", "document": document.Name, "body": body.Name,
    "initial_thickness_mm": 6.0, "final_thickness_mm": 8.0,
    "initial": initial, **updated,
    "features": [outline.Name, pad.Name, holes.Name, pocket.Name],
    "saved": False, "modified_documents": [document.Name],
}
print("PhoenixParametricDemo: constrained plate 60 x 40, thickness 6 -> 8 mm, 4 x diameter 6; valid single solid.")
