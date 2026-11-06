# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
import math
from pathlib import Path
import types
import unittest

OPERATIONS_PATH = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools" / "pft_operations.py"
if not OPERATIONS_PATH.exists():
    OPERATIONS_PATH = Path(__file__).with_name("pft_operations.py")
spec = importlib.util.spec_from_file_location("phoenix_operations_test", OPERATIONS_PATH)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class Failure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def vector(x=0, y=0, z=0):
    return types.SimpleNamespace(x=x, y=y, z=z)


def placement(x=0):
    return types.SimpleNamespace(Base=vector(x, 0, 0), Rotation=types.SimpleNamespace(Axis=vector(0, 0, 1), Angle=0.5))


class Unit:
    def __init__(self, signature=(1, 0, 0, 0, 0, 0, 0, 0), unit_type="Length"):
        self.Signature = signature
        self.Type = unit_type

    def __str__(self):
        return "Unit: mm (1,0,0,0,0,0,0,0) [Length]"


class Quantity:
    def __init__(self, value, signature=(1, 0, 0, 0, 0, 0, 0, 0), unit_type="Length"):
        self.Value = value
        self.Unit = Unit(signature, unit_type)

    @property
    def UserString(self):
        raise AssertionError("display units must not be paired with internal Value")


class Document:
    def __init__(self, name):
        self.Name = name
        self.Label = name
        self.Objects = []
        self.FileName = "/Users/private/confidential/part.FCStd"
        self.State = []
        self.Touched = True

    def getObject(self, name):
        return next((obj for obj in self.Objects if obj.Name == name), None)

    def recompute(self):
        raise AssertionError("read operations must not recompute")

    def save(self):
        raise AssertionError("read operations must not save")


class Object:
    def __init__(self, document, name, type_id="Part::Feature"):
        self.Document = document
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.InList = []
        self.OutList = []
        self.State = []
        self.ViewObject = types.SimpleNamespace(Visibility=True)
        self.PropertiesList = []
        self.Placement = placement()
        self._property_types = {}
        document.Objects.append(self)

    def getTypeIdOfProperty(self, name):
        return self._property_types.get(name, "App::PropertyString")


def shape():
    return types.SimpleNamespace(
        BoundBox=types.SimpleNamespace(XMin=0, YMin=0, ZMin=0, XMax=60, YMax=40, ZMax=6, XLength=60, YLength=40, ZLength=6),
        ShapeType="Solid", Length=200, Area=5200, Volume=14400 - 216 * math.pi,
        CenterOfMass=vector(30, 20, 3), Vertexes=[], Edges=[], Faces=[], Solids=[object()],
        isNull=lambda: False,
    )


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.doc = Document("Doc")
        self.external = Document("External")
        self.part = Object(self.doc, "Plate")
        self.part.Shape = shape()
        self.part.Length = Quantity(60)
        self.part.PropertiesList = ["Length", "Placement"]
        self.calls = []
        self.view = types.SimpleNamespace(
            viewFront=lambda: self.calls.append("front"), viewRear=lambda: self.calls.append("rear"),
            viewLeft=lambda: self.calls.append("left"), viewRight=lambda: self.calls.append("right"),
            viewTop=lambda: self.calls.append("top"), viewBottom=lambda: self.calls.append("bottom"),
            viewAxonometric=lambda: self.calls.append("axonometric"), fitAll=lambda: self.calls.append("fit"),
        )
        self.app = types.SimpleNamespace(ActiveDocument=self.doc, getDocument=lambda name: {"Doc": self.doc, "External": self.external}[name])
        self.gui = types.SimpleNamespace(
            getDocument=lambda name: types.SimpleNamespace(activeView=lambda: self.view),
            Selection=types.SimpleNamespace(getSelectionEx=lambda *args: []),
        )

    def perform(self, method, params=None):
        result = ops.perform(method, params or {}, self.app, self.gui, Failure)
        json.dumps(result, allow_nan=False)
        return result

    def assert_error(self, code, method, params):
        with self.assertRaises(Failure) as error:
            self.perform(method, params)
        self.assertEqual(error.exception.code, code)

    def test_validation_rejects_extra_fields_types_paths_and_controls(self):
        requests = [
            ("list_objects", {"offset": True}), ("list_objects", {"limit": False}),
            ("list_objects", {"limit": 101}), ("list_objects", {"limit": 0}),
            ("list_objects", {"offset": -1}), ("list_objects", {"typeId": "Part::Feature.__class__"}),
            ("get_document_info", {"docName": "Doc\n"}), ("get_document_info", {"docName": "Doc\x85"}),
            ("get_object_info", {"objectName": "Plate", "properties": ["Shape.Faces"]}),
            ("get_object_info", {"objectName": "Plate", "properties": ["__dict__"]}),
            ("get_object_info", {"objectName": "Plate", "properties": ["Length"] * 2}),
            ("get_object_info", {"objectName": "Plate", "properties": [dict()]}),
            ("get_object_info", {"objectName": "Plate", "properties": [str(i) for i in range(21)]}),
            ("get_object_info", {}), ("measure_geometry", {"objectName": "Plate", "subelement": "Edge0"}),
            ("measure_geometry", {"objectName": "Plate", "subelement": "Faces[0]"}),
            ("view_standard", {"view": {}}), ("view_standard", {"view": "front", "fit": 1}),
            ("get_selection", {"clear": True}),
        ]
        for method, params in requests:
            with self.subTest(method=method, params=params), self.assertRaises(Failure) as error:
                ops.validate(method, params, Failure)
            self.assertEqual(error.exception.code, "INVALID_PARAMS")
        self.assertFalse(ops.validate("unknown", None, Failure))
        with self.assertRaises(Failure) as error:
            ops.perform("unknown", {}, None, None, Failure)
        self.assertEqual(error.exception.code, "METHOD_NOT_FOUND")

    def test_document_unknown_modified_and_no_fake_revision(self):
        result = self.perform("get_document_info")
        self.assertIsNone(result["modified"])
        self.assertEqual(result["instance_token"], result["document_instance_token"])
        self.assertRegex(result["document_instance_token"], r"^[A-Za-z0-9_-]{32}$")
        self.assertIsNone(result["revision"])
        self.assertTrue(result["has_saved_file"])
        self.assertNotIn("/Users/", json.dumps(result))
        self.assertFalse(result["recomputed"])
        self.assertIs(self.app.ActiveDocument, self.doc)

    def test_pagination_and_exact_type_filter(self):
        for index in range(105):
            Object(self.doc, "Body%d" % index, "PartDesign::Body")
        result = self.perform("list_objects", {"offset": 100, "limit": 100, "typeId": "PartDesign::Body"})
        self.assertEqual(result["total"], 105)
        self.assertEqual([obj["Name"] for obj in result["objects"]], ["Body%d" % i for i in range(100, 105)])
        self.assertFalse(result["has_more"])
        first = self.perform("list_objects", {"limit": 2})
        self.assertTrue(first["has_more"])
        self.assertEqual(len(first["objects"]), 2)

    def test_containment_is_separate_from_dependency(self):
        body = Object(self.doc, "Body", "PartDesign::Body")
        body.Group = [self.part]
        body.Tip = self.part
        sketch = Object(self.doc, "Sketch", "Sketcher::SketchObject")
        self.part.InList = [body]
        self.part.OutList = [sketch]
        sketch.InList = [self.part]
        result = self.perform("get_object_info", {"objectName": "Plate"})["object"]
        self.assertEqual(result["parents"][0]["object"], "Body")
        self.assertEqual(result["dependencies"][0]["object"], "Sketch")
        sketch_result = self.perform("get_object_info", {"objectName": "Sketch"})["object"]
        self.assertEqual(sketch_result["parents"], [])
        self.assertEqual(sketch_result["dependents"][0]["object"], "Plate")

    def test_quantity_vector_placement_and_unknown_property(self):
        result = self.perform("get_object_info", {"objectName": "Plate", "properties": ["Length", "Placement"]})
        self.assertEqual(result["properties"]["Length"], {"kind": "quantity", "value": 60, "unit": "mm", "unit_signature": [1, 0, 0, 0, 0, 0, 0, 0], "unit_type": "Length", "unit_system": "freecad_internal"})
        self.assertEqual(result["placement"]["translation_unit"], "mm")
        self.assertEqual(result["placement"]["rotation"]["angle_unit"], "rad")
        self.assert_error("PROPERTY_NOT_FOUND", "get_object_info", {"objectName": "Plate", "properties": ["Nonexistent"]})

    def test_native_quantity_signatures_keep_internal_value_and_units_consistent(self):
        cases = [
            (25.4, (1, 0, 0, 0, 0, 0, 0, 0), "Length", "mm"),
            (90.0, (0, 0, 0, 0, 0, 0, 0, 1), "Angle", "deg"),
            (625.0, (2, 0, 0, 0, 0, 0, 0, 0), "Area", "mm^2"),
            (2.5, (0, 1, 0, 0, 0, 0, 0, 0), "Mass", "kg"),
            (1000.0, (1, 1, -2, 0, 0, 0, 0, 0), "Force", "mm*kg*s^-2"),
            (0.001, (-1, 1, -2, 0, 0, 0, 0, 0), "Pressure", "mm^-1*kg*s^-2"),
            (0.4, (0, 0, 0, 0, 0, 0, 0, 0), "", "1"),
        ]
        for value, signature, unit_type, expected_unit in cases:
            with self.subTest(unit_type=unit_type):
                result = ops._PftJson().value(Quantity(value, signature, unit_type))
                self.assertEqual(result["value"], value)
                self.assertEqual(result["unit"], expected_unit)
                self.assertEqual(result["unit_signature"], list(signature))
                self.assertEqual(result["unit_type"], unit_type)
                self.assertEqual(result["unit_system"], "freecad_internal")
                self.assertNotIn("native_unit", result)

    def test_unavailable_or_malformed_unit_signature_is_explicitly_unknown(self):
        signatures = [None, "(1,0,0,0,0,0,0,0)", (1, 0), (1.0, 0, 0, 0, 0, 0, 0, 0),
                      (1, False, 0, 0, 0, 0, 0, 0), (128, 0, 0, 0, 0, 0, 0, 0)]
        for signature in signatures:
            with self.subTest(signature=signature):
                serializer = ops._PftJson()
                result = serializer.value(Quantity(25.4, signature))
                self.assertIsNone(result["unit"])
                self.assertIsNone(result["unit_signature"])
                self.assertIsNone(result["unit_system"])
                self.assertEqual(result["value"], 25.4)
                self.assertTrue(result["native_unit"].startswith("Unit:"))
                self.assertTrue(serializer.warnings)

    def test_native_signature_takes_precedence_over_type_or_debug_representation(self):
        # Real Unit.__str__ is a debug representation, and a display name is
        # never enough to choose the basis of Quantity.Value.
        result = ops._PftJson().value(Quantity(2.5, (0, 1, 0, 0, 0, 0, 0, 0), "Length"))
        self.assertEqual(result["unit"], "kg")
        self.assertEqual(result["value"], 2.5)

    def test_property_json_is_finite_bounded_and_hides_paths_and_svg(self):
        self.part.Data = {"nan": float("nan"), "infinite": float("inf"), "large": 2 ** 63,
                          "nested": [[[[[dict(x=1)]]]]], "list": list(range(200)),
                          "unix": "/tmp", "windows": "C:\\secret\\part.FCStd", "inline": '<svg xmlns="svg">private</svg>',
                          "long": "a" * 5000, "reference": self.part, "unknown": object()}
        self.part.FileName = "/Users/private/source.FCStd"
        self.part._property_types["FileName"] = "App::PropertyFile"
        self.part.PropertiesList = ["Data", "FileName"]
        result = self.perform("get_object_info", {"objectName": "Plate", "properties": ["Data", "FileName"]})
        data = result["properties"]["Data"]
        self.assertIsNone(data["nan"])
        self.assertIsNone(data["infinite"])
        self.assertEqual(data["large"]["decimal"], str(2 ** 63))
        self.assertLessEqual(len(data["list"]), 50)
        self.assertLessEqual(len(data["long"]), 512)
        self.assertTrue(data["unix"]["redacted"])
        self.assertTrue(data["windows"]["redacted"])
        self.assertTrue(data["inline"]["redacted"])
        self.assertTrue(result["properties"]["FileName"]["redacted"])
        self.assertEqual(data["reference"]["object"], "Plate")
        self.assertEqual(data["unknown"], {"unsupported_type": "object"})
        encoded = json.dumps(result, allow_nan=False)
        self.assertNotIn("/Users/private", encoded)
        self.assertNotIn("<svg", encoded)
        self.assertTrue(result["warnings"])

    def test_surface_volume_is_not_applicable_and_bounds_are_not_exact_clearance(self):
        face = shape()
        face.ShapeType, face.Solids, face.Volume = "Face", [], -123.5
        self.part.Shape = face
        geometry = self.perform("measure_geometry", {"objectName": "Plate"})["geometry"]
        self.assertIsNone(geometry["volume"])
        self.assertFalse(geometry["volume_applicable"])
        self.assertEqual(geometry["bounding_box_source"], "native_shape_bound_box")
        self.assertEqual(geometry["bounding_box_precision"], "kernel_enclosure_not_exact_clearance")
        self.assertIsNone(geometry["bounding_box_error_bound_mm"])
        self.assertTrue(self.perform("measure_geometry", {"objectName": "Plate"})["warnings"])
        self.part.Shape = shape()
        solid = self.perform("measure_geometry", {"objectName": "Plate"})["geometry"]
        self.assertTrue(solid["volume_applicable"])
        self.assertAlmostEqual(solid["volume"], self.part.Shape.Volume)

    def test_drawing_active_uses_only_existing_unique_3d_view_without_activation(self):
        drawing = types.SimpleNamespace()
        self.gui.getDocument = lambda name: types.SimpleNamespace(activeView=lambda: drawing, mdiViewsOfType=lambda kind: [self.view])
        self.perform("view_standard", {"view": "axonometric", "fit": True})
        self.assertEqual(self.calls, ["axonometric", "fit"])
        self.assertIs(self.app.ActiveDocument, self.doc)
        self.calls.clear()
        self.gui.getDocument = lambda name: types.SimpleNamespace(activeView=lambda: drawing, mdiViewsOfType=lambda kind: [self.view, self.view])
        self.assert_error("VIEW_UNAVAILABLE", "view_standard", {"view": "axonometric"})
        self.assertEqual(self.calls, [])
        self.gui.getDocument = lambda name: types.SimpleNamespace(activeView=lambda: self.view, mdiViewsOfType=lambda kind: [drawing, self.view])
        self.perform("view_standard", {"view": "top"})
        self.assertEqual(self.calls, ["top"])

    def test_external_link_preserves_reference_without_claiming_world_transform(self):
        target = Object(self.external, "Source")
        link = Object(self.doc, "Instance", "App::Link")
        link.LinkedObject = target
        link.LinkPlacement = placement(25)
        link.LinkTransform = True
        result = self.perform("get_object_info", {"objectName": "Instance"})
        self.assertEqual(result["link"]["sources"][0], {"document": "External", "object": "Source", "subelements": [], "external": True})
        self.assertFalse(result["link"]["instance_transform_resolved"])
        self.assertEqual(result["link"]["link_placement"]["base"]["x"], 25)

    def test_json_total_budget_limits_nested_collections(self):
        leaf = ["x" * 512] * 50
        nested = [[leaf] * 50] * 50
        serializer = ops._PftJson()
        encoded = json.dumps(serializer.value(nested), allow_nan=False)
        self.assertLess(len(encoded), 900000)
        self.assertIn("value_budget", encoded)
        huge = ops._PftJson().value(2 ** 20000)
        self.assertTrue(huge["truncated"])
        self.assertIsNone(huge["decimal"])

    def test_sketch_does_not_invoke_solver_and_unknown_dof_is_null(self):
        sketch = Object(self.doc, "Sketch", "Sketcher::SketchObject")
        sketch.Geometry = [types.SimpleNamespace(StartPoint=vector(), EndPoint=vector(10, 0, 0))]
        sketch.Constraints = [types.SimpleNamespace(Type="Distance", First=0, Value=10)]
        sketch.FullyConstrained = False
        sketch.solve = lambda: self.fail("read operation invoked solve")
        result = self.perform("get_object_info", {"objectName": "Sketch"})["sketch"]
        self.assertIsNone(result["degrees_of_freedom"])
        self.assertFalse(result["fully_constrained"])
        self.assertFalse(result["solver_invoked"])
        self.assertEqual(result["geometry_count"], 1)

    def test_geometry_and_subelement_units_not_mass(self):
        edge = shape()
        edge.ShapeType = "Edge"
        edge.Curve = types.SimpleNamespace(Radius=3)
        self.part.Shape.Edges = [edge]
        result = self.perform("measure_geometry", {"objectName": "Plate", "subelement": "Edge1"})
        geometry = result["geometry"]
        self.assertEqual(geometry["radius"], 3)
        self.assertEqual(geometry["radius_source"], "curve")
        self.assertEqual(geometry["units"]["volume"], "mm^3")
        self.assertFalse(geometry["world_transform_applied"])
        self.assertNotIn("mass", geometry)
        self.assert_error("SUBELEMENT_NOT_FOUND", "measure_geometry", {"objectName": "Plate", "subelement": "Face9"})

    def test_selection_filtered_without_changing_it(self):
        foreign = Object(self.external, "Other")
        selected = [types.SimpleNamespace(Object=self.part, DocumentName="Doc", SubElementNames=["Edge1"], PickedPoints=[vector(1, 2, 3)]),
                    types.SimpleNamespace(Object=foreign, DocumentName="External", SubElementNames=[], PickedPoints=[])]
        self.gui.Selection = types.SimpleNamespace(getSelectionEx=lambda *args: selected,
                                                  clearSelection=lambda: self.fail("selection was changed"))
        result = self.perform("get_selection")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["selection"][0]["reference"]["subelements"], ["Edge1"])
        self.assertEqual(len(selected), 2)
        self.assertEqual(self.calls, [])

    def test_reference_identifiers_are_not_silently_shortened_or_private(self):
        reference = ops._ref(self.part, self.doc, ["Link.Body.Edge1", "a" * 513, "/Users/private/template.svg"])
        self.assertEqual(reference["subelements"], ["Link.Body.Edge1"])
        self.assertTrue(reference["subelements_limited"])

    def test_techdraw_sources_and_template_have_no_private_file_or_source(self):
        source = Object(self.external, "Source")
        template = Object(self.doc, "Template", "TechDraw::DrawSVGTemplate")
        template.Template = "/Users/private/template.svg"
        template.EditableTexts = {"TITLE": "Connection plate"}
        template.Width = Quantity(297)
        template.Height = Quantity(210)
        view = Object(self.doc, "Front", "TechDraw::DrawViewPart")
        view.Source = [source]
        view.X, view.Y, view.Scale = Quantity(100), Quantity(100), 1.0
        dimension = Object(self.doc, "Diameter", "TechDraw::DrawViewDimension")
        dimension.References2D = [(view, ("Edge1",))]
        page = Object(self.doc, "Page", "TechDraw::DrawPage")
        page.Template, page.Views, page.Scale = template, [view, dimension], 1.0
        pages = self.perform("list_drawing_pages")
        self.assertEqual(pages["total"], 1)
        result = self.perform("inspect_drawing_page", {"pageName": "Page"})
        self.assertEqual(result["template"]["editable_fields"], {"TITLE": "Connection plate"})
        self.assertTrue(result["template"]["has_file_reference"])
        self.assertTrue(result["objects"][0]["sources"][0]["external"])
        self.assertEqual(result["objects"][1]["sources"][0]["subelements"], ["Edge1"])
        self.assertFalse(result["references_validated"])
        self.assertNotIn("/Users/private", json.dumps(result))
        self.assert_error("WRONG_OBJECT_TYPE", "inspect_drawing_page", {"pageName": "Plate"})

    def test_page_contents_are_bounded(self):
        page = Object(self.doc, "Page", "TechDraw::DrawPage")
        page.Views = [Object(self.doc, "View%d" % i, "TechDraw::DrawViewPart") for i in range(120)]
        result = self.perform("inspect_drawing_page", {"pageName": "Page"})
        self.assertEqual(len(result["objects"]), 100)
        self.assertEqual(result["total"], 120)
        self.assertTrue(result["truncated"])

    def test_standard_view_changes_only_camera_and_validates_fit_before_direction(self):
        result = self.perform("view_standard", {"view": "rear", "fit": True})
        self.assertEqual(self.calls, ["rear", "fit"])
        self.assertEqual(result["document"], "Doc")
        self.calls.clear()
        self.view.fitAll = None
        self.assert_error("VIEW_UNAVAILABLE", "view_standard", {"view": "top", "fit": True})
        self.assertEqual(self.calls, [])
        self.gui.getDocument = lambda name: None
        self.assert_error("VIEW_UNAVAILABLE", "view_standard", {"view": "front"})

    def test_missing_target_returns_explicit_errors(self):
        self.assert_error("DOCUMENT_NOT_FOUND", "list_objects", {"docName": "Missing"})
        self.assert_error("OBJECT_NOT_FOUND", "get_object_info", {"objectName": "Missing"})
        self.app.ActiveDocument = None
        self.assert_error("NO_ACTIVE_DOCUMENT", "get_document_info", {})


if __name__ == "__main__":
    unittest.main()
