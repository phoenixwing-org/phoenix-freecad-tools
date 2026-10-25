# SPDX-License-Identifier: Apache-2.0
"""Current-memory reads, target scope, Markdown escaping and explicit exports."""
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools"
_saved_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_document_tools as tools
finally:
    sys.path[:] = _saved_path


class PftDoc:
    def __init__(self, name="Example"):
        self.Name, self.Label = name, "Synthetic document"
        self.FileName = "/example/private-folder/only-local-ui.FCStd"
        self.Objects = []
        self.touched = True
    def getObject(self, name): return next((obj for obj in self.Objects if obj.Name == name), None)
    def isTouched(self): return self.touched
    def recompute(self): raise AssertionError("must not recompute")
    def save(self): raise AssertionError("must not save CAD")
    def openTransaction(self, *_): raise AssertionError("must not start transactions")


class PftShape:
    def __init__(self, lengths=(60, 40, 8), null=False):
        self.BoundBox = types.SimpleNamespace(**dict(zip(("XLength", "YLength", "ZLength"), lengths)))
        self.null = null
    def isNull(self): return self.null
    def isValid(self): raise AssertionError("must not validate geometry")


class PftObject:
    def __init__(self, doc, name, kind="Part::Feature", shape=None, visible=True):
        self.Document, self.Name, self.Label, self.TypeId = doc, name, name, kind
        self.PropertiesList, self.Group, self.Tip = ["Shape"], [], None
        self.ViewObject = types.SimpleNamespace(Visibility=visible)
        self.shape, self.reads, self.shape_error = shape or PftShape(), 0, None
        doc.Objects.append(self)
    @property
    def Shape(self):
        self.reads += 1
        if self.shape_error: raise self.shape_error
        return self.shape
    def getLinkedObject(self, *_): raise AssertionError("must not resolve source")
    def getGlobalPlacement(self): raise AssertionError("must not guess/apply transforms")


class PftSelection:
    def __init__(self): self.records = []
    def getSelectionEx(self, *args): return list(self.records)
    def getSelection(self, *args): return []
    def addSelection(self, *_): raise AssertionError("must not change selection")
    def clearSelection(self): raise AssertionError("must not change selection")


class PftBomObject(PftObject):
    """Native-style property metadata and getters, including forwarded Link reports."""
    def __init__(self, doc, name, values, kinds=None, kind="Part::Feature"):
        super().__init__(doc, name, kind=kind)
        self.bom_values, self.bom_reads, self.type_reads = dict(values), [], []
        self.bom_types = {key: "App::PropertyString" for key in values}
        self.bom_types.update(kinds or {})
        self.PropertiesList += list(values)
        self.properties_error = False
        self.property_list_reads = 0
    def __getattribute__(self, name):
        if name == "PropertiesList":
            values = object.__getattribute__(self, "__dict__")
            if "property_list_reads" in values:
                values["property_list_reads"] += 1
            if values.get("properties_error"):
                raise RuntimeError("private metadata exception")
        if name == "LinkedObject":
            raise AssertionError("BOM summary must not resolve a Link source")
        return super().__getattribute__(name)
    def __getattr__(self, name):
        if name not in self.bom_values:
            raise AttributeError(name)
        self.bom_reads.append(name)
        value = self.bom_values.get(name, AssertionError("missing field must not be read"))
        if isinstance(value, Exception):
            raise value
        return value
    def getTypeIdOfProperty(self, name):
        self.type_reads.append(name)
        value = self.bom_types[name]
        if isinstance(value, Exception):
            raise value
        return value


class Unit:
    def __init__(self, signature): self.Signature = signature
    @property
    def Type(self): raise AssertionError("must not infer internal basis from display/debug labels")
    def __str__(self): raise AssertionError("must not stringify native units")


class Quantity:
    def __init__(self, value, signature=(1, 0, 0, 0, 0, 0, 0, 0)):
        self.Value, self.Unit = value, Unit(signature)
    @property
    def UserString(self): raise AssertionError("must not read display-schema quantity strings")
    def __str__(self): raise AssertionError("must not stringify native quantities")


class PftOpaqueValue:
    def __str__(self): raise AssertionError("must not stringify arbitrary native data")
    def __repr__(self): raise AssertionError("must not use native debug representations")


class PftApp:
    def __init__(self, doc):
        self.ActiveDocument = doc
        self.documents = {doc.Name: doc} if doc else {}
    def getDocument(self, name): return self.documents.get(name)
    def listDocuments(self): return self.documents
    def setActiveDocument(self, *_): raise AssertionError("must not activate documents")


class PftGui:
    def __init__(self, doc):
        self.Selection = PftSelection()
        self.Modified = True
        self.editing = False
        self.body = None
    def getDocument(self, name):
        return types.SimpleNamespace(Modified=self.Modified, getInEdit=lambda: object() if self.editing else None,
            activeView=lambda: types.SimpleNamespace(getActiveObject=lambda key: self.body))


class PftDocumentReadTests(unittest.TestCase):
    def setUp(self):
        self.doc = PftDoc()
        self.app, self.gui = PftApp(self.doc), PftGui(self.doc)
        self.body = PftObject(self.doc, "Body", "PartDesign::Body")
        self.pad = PftObject(self.doc, "Pad", "PartDesign::Pad")
        self.sketch = PftObject(self.doc, "Sketch", "Sketcher::SketchObject")
        self.body.Group, self.body.Tip = [self.pad, self.sketch], self.pad
        self.hidden = PftObject(self.doc, "Hidden", visible=False)
        self.gui.body = self.body
    def read(self, targets=None, property_names=None):
        return tools.read_document_tools(self.app, self.gui, target_names=targets, property_names=property_names)
    def test_no_document_has_explicit_empty_state_and_no_writes(self):
        result = tools.read_document_tools(PftApp(None), self.gui)
        self.assertIsNone(result["document"])
        self.assertEqual(result["dimensions"], [])
        self.assertEqual(result["properties"], [])
        self.assertIn("当前没有打开的文档。", tools.format_information(result))
    def test_defaults_use_top_level_body_and_never_read_hidden_or_internal_shapes(self):
        result = self.read()
        self.assertEqual(result["selected_names"], ["Body"])
        self.assertEqual((self.body.reads, self.pad.reads, self.sketch.reads, self.hidden.reads), (1, 0, 0, 0))
        self.assertEqual(result["dimensions"][0]["unit"], "mm")
        self.assertEqual([result["dimensions"][0][key] for key in ("length_mm", "width_mm", "height_mm")], [60, 40, 8])
        self.assertEqual(self.gui.Selection.records, [])
        self.assertIs(self.app.ActiveDocument, self.doc)
    def test_raw_link_names_win_over_resolved_object_and_do_not_resolve_sources(self):
        link = PftObject(self.doc, "Link", "App::Link", PftShape((11, 12, 13)))
        self.gui.Selection.records = [types.SimpleNamespace(ObjectName="Link", DocumentName=self.doc.Name, Object=self.body, SubElementNames=[])]
        result = self.read()
        self.assertEqual(result["selected_names"], ["Link"])
        self.assertEqual(self.body.reads, 0)
        self.assertEqual(link.reads, 1)
        dimension = result["dimensions"][0]
        self.assertFalse(dimension["link_source_resolved"])
        self.assertFalse(dimension["placement_applied_by_tool"])
        self.assertEqual(dimension["coordinate_scope"], "reported_native_object_shape")
    def test_unsuitable_raw_scope_never_falls_back_to_all_recommended_parts(self):
        self.gui.Selection.records = [types.SimpleNamespace(Object=self.pad, ObjectName="Pad", DocumentName=self.doc.Name, SubElementNames=["Face1"])]
        result = self.read()
        self.assertEqual(result["selected_names"], [])
        self.assertEqual(self.body.reads, 0)
    def test_explicit_targets_are_local_unique_and_can_be_empty(self):
        self.assertEqual(self.read([])["dimensions"], [])
        self.assertEqual(self.body.reads, 0)
        self.assertEqual(self.read(["Pad"])["selected_names"], ["Pad"])
        for targets in (["absent"], ["Pad", "Pad"], "Body"):
            with self.assertRaises(tools.PftDocumentToolsError): self.read(targets)
    def test_shape_exception_null_and_invalid_extents_are_not_reported_as_zero_success(self):
        self.body.shape_error = RuntimeError("private exception path")
        result = self.read(["Body"])
        self.assertIsNone(result["dimensions"][0]["length_mm"])
        self.assertEqual(result["dimensions"][0]["status"], "read_failed")
        self.assertNotIn("private exception", tools.format_markdown(result))
        self.body.shape_error = None
        for shape, status in ((PftShape(null=True), "unavailable"), (PftShape((math.nan, 1, 1)), "invalid_cache"), (PftShape((-1, 1, 1)), "invalid_cache")):
            self.body.shape = shape
            result = self.read(["Body"])
            self.assertEqual(result["dimensions"][0]["status"], status)
            self.assertIsNone(result["dimensions"][0]["length_mm"])
    def test_context_is_native_current_memory_and_information_has_explicit_sources(self):
        self.gui.editing = True
        result = self.read([])
        info = result["document"]
        self.assertTrue(info["modified"])
        self.assertTrue(info["touched"])
        self.assertTrue(info["context"]["in_edit"]["value"])
        self.assertEqual(info["bodies"][0]["Tip"]["object"], "Pad")
        text = tools.format_information(result)
        self.assertIn("GuiDocument.Modified", text)
        self.assertIn("Document.isTouched", text)
        self.assertIn(self.doc.FileName, text)
    def test_unknown_flags_stay_unknown_and_no_geometry_validation_is_invoked(self):
        self.gui.getDocument = lambda _: types.SimpleNamespace()
        self.doc.isTouched = lambda: "wrong type"
        result = self.read([])
        self.assertIsNone(result["document"]["modified"])
        self.assertIsNone(result["document"]["touched"])
        self.assertIn("未知", tools.format_information(result))
    def test_markdown_escapes_tables_html_and_links_and_omits_saved_path(self):
        self.body.Label = "![click](file:///example) | <script>\nline"
        result = self.read(["Body"])
        markdown = tools.format_markdown(result)
        self.assertNotIn(self.doc.FileName, markdown)
        self.assertIn(r"\|", markdown)
        self.assertIn("&lt;script&gt;<br>line", markdown)
        self.assertNotIn("![click]", markdown)
        self.assertIn(r"\!\[click\]", markdown)
    def test_parent_shape_default_does_not_duplicate_its_body_and_internal_features(self):
        part = PftObject(self.doc, "Part", "App::Part")
        part.Group = [self.body]
        result = self.read()
        self.assertEqual(result["selected_names"], ["Part"])
        self.assertEqual(self.body.reads, 0)
    def test_doc_budget_fails_before_shapes_and_worker_thread_is_rejected(self):
        self.doc.Objects = [self.body] * (tools.PFT_MAX_SCAN_OBJECTS + 1)
        with self.assertRaises(tools.PftDocumentToolsError) as caught: self.read()
        self.assertEqual(caught.exception.code, "DOCUMENT_TOO_LARGE")
        self.assertEqual(self.body.reads, 0)
        failures = []
        def run():
            try: self.read()
            except tools.PftDocumentToolsError as error: failures.append(error.code)
        thread = threading.Thread(target=run); thread.start(); thread.join()
        self.assertEqual(failures, ["MAIN_THREAD_REQUIRED"])
    def test_candidate_budget_is_explicit_and_only_picked_objects_read_shapes(self):
        self.doc.Objects = []
        objects = [PftObject(self.doc, "Object" + str(index)) for index in range(201)]
        result = self.read([])
        self.assertEqual(len(result["candidates"]), 200)
        self.assertIn("对象候选超过上限，仅显示前200项。", result["warnings"])
        self.assertEqual(sum(obj.reads for obj in objects), 0)

    def test_bom_summary_preserves_empty_missing_wrong_type_and_failure_without_reading_unselected_values(self):
        picked = PftBomObject(self.doc, "Picked", {"PartNumber": "  PUBLIC-7  ", "PartVersion": "",
            "TypeCode": 7, "PartName": RuntimeError("private native exception")}, {"TypeCode": "App::PropertyInteger"})
        other = PftBomObject(self.doc, "Unselected", {"PartNumber": AssertionError("unselected getter forbidden")})
        result = self.read(["Picked"])
        row = result["properties"][0]
        fields = {field["name"]: field for field in row["fields"]}
        self.assertEqual([item["Name"] for item in result["properties"]], ["Picked"])
        self.assertEqual(row["status"], "read_failed")
        self.assertEqual({name: field["status"] for name, field in fields.items()}, {
            "PartNumber": "available", "PartVersion": "empty", "TypeCode": "unsupported",
            "ModelSeries": "missing", "PartName": "read_failed"})
        self.assertEqual(fields["PartNumber"]["value"], "  PUBLIC-7  ")
        self.assertEqual(fields["PartVersion"]["value"], "")
        self.assertIsNone(fields["ModelSeries"]["value"])
        self.assertNotIn("TypeCode", picked.bom_reads)
        self.assertNotIn("ModelSeries", picked.type_reads)
        self.assertEqual(other.bom_reads, [])
        self.assertNotIn("private native exception", tools.format_information(result) + tools.format_markdown(result))
        self.assertEqual(self.gui.Selection.records, [])
        self.assertIs(self.app.ActiveDocument, self.doc)

    def test_properties_list_failure_is_not_missing_and_type_query_failure_does_not_get_value(self):
        broken = PftBomObject(self.doc, "Broken", {"PartNumber": "unread"})
        broken.properties_error = True
        row = self.read(["Broken"])["properties"][0]
        self.assertEqual(row["status"], "read_failed")
        self.assertEqual({field["status"] for field in row["fields"]}, {"read_failed"})
        self.assertEqual(broken.bom_reads, [])
        self.assertEqual(broken.type_reads, [])
        broken.properties_error = False
        broken.bom_types["PartNumber"] = RuntimeError("private type exception")
        row = self.read(["Broken"])["properties"][0]
        self.assertEqual(row["fields"][0]["status"], "read_failed")
        self.assertEqual(broken.bom_reads, [])

    def test_link_bom_uses_only_instance_report_and_does_not_claim_local_storage(self):
        source = PftBomObject(self.doc, "Source", {"PartNumber": AssertionError("source getter forbidden")})
        link = PftBomObject(self.doc, "Instance", {"PartNumber": "reported-value"}, kind="App::Link")
        result = self.read(["Instance"])
        row = result["properties"][0]
        self.assertEqual(row["fields"][0]["value"], "reported-value")
        self.assertEqual(row["source"], "native_object")
        self.assertEqual(row["locality"], "not_verified")
        self.assertFalse(row["link_source_resolved"])
        self.assertEqual(source.bom_reads, [])
        self.assertEqual(link.bom_reads, ["PartNumber"])
        self.assertIn("未验证本地或继承来源", tools.format_information(result))

    def test_bom_value_budget_is_utf8_bounded_and_sanitization_is_explicit(self):
        picked = PftBomObject(self.doc, "Bounded", {"PartNumber": "示" * 1000, "PartName": "Public\x00name\ud800"})
        fields = {field["name"]: field for field in self.read(["Bounded"])["properties"][0]["fields"]}
        self.assertLessEqual(len(fields["PartNumber"]["value"].encode("utf-8")), tools.PFT_MAX_PROPERTY_BYTES)
        self.assertEqual(fields["PartNumber"]["value"], "示" * 682)
        self.assertTrue(fields["PartNumber"]["truncated"])
        self.assertFalse(fields["PartNumber"]["sanitized"])
        self.assertEqual(fields["PartName"]["value"], "Public name ")
        self.assertTrue(fields["PartName"]["sanitized"])
        text = tools.format_information(self.read(["Bounded"]))
        self.assertIn("已截断", text)
        self.assertIn("已净化控制字符", text)
        self.assertEqual(picked.bom_values["PartNumber"], "示" * 1000)

    def test_bom_total_budget_rejects_whole_summary_and_empty_selection_reads_no_values(self):
        objects = [PftBomObject(self.doc, "Public" + str(index),
                   {field: "x" * tools.PFT_MAX_PROPERTY_BYTES for field, _ in tools.BOM_FIELDS}) for index in range(15)]
        self.assertEqual(self.read([])["properties"], [])
        self.assertEqual(sum(len(obj.bom_reads) for obj in objects), 0)
        with self.assertRaises(tools.PftDocumentToolsError) as caught:
            self.read([obj.Name for obj in objects])
        self.assertEqual(caught.exception.code, "BOM_PROPERTIES_TOO_LARGE")
        self.assertNotIn("xxxxxxxx", caught.exception.message)
        self.assertEqual(self.gui.Selection.records, [])

    def test_bom_markdown_escapes_values_preserves_status_and_never_adds_file_name(self):
        picked = PftBomObject(self.doc, "PublicBom", {"PartNumber": "![x](file:///public) | <script>\nnext",
                                                     "PartVersion": ""})
        result = self.read([picked.Name])
        markdown = tools.format_markdown(result)
        self.assertIn("所选对象属性（BOM）", markdown)
        self.assertIn(r"\!\[x\]\(file:///public\) \| &lt;script&gt;<br>next", markdown)
        self.assertIn("空值", markdown)
        self.assertIn("缺失", markdown)
        self.assertIn("native\\_object", markdown)
        self.assertNotIn(self.doc.FileName, markdown)
        self.assertNotIn("<script>", markdown)
        self.assertIn("未选择属性对象。", tools.format_markdown(self.read([])))


    def test_property_name_contract_is_bounded_exact_and_validates_before_any_document_read(self):
        defaults = tuple(name for name, _ in tools.BOM_FIELDS)
        self.assertEqual(tools.validate_property_names(None), defaults)
        self.assertEqual(tools.validate_property_names(["CustomText", "Custom_Count", "长度"]), ("CustomText", "Custom_Count", "长度"))
        self.assertEqual(tools.validate_property_names([]), ())
        for names in ("Label", {}, {"names": ["Label"]}, ["One", "One"], ["display label"], ["Obj.Length"],
                      ["1Field"], ["class"], ["Bad\x00Name"], ["x" * 129], [False], [{}],
                      ["Field" + str(index) for index in range(21)]):
            with self.subTest(names=names), patch.object(self.app, "getDocument") as read:
                with self.assertRaises(tools.PftDocumentToolsError) as error:
                    self.read(["Body"], names)
                self.assertEqual(error.exception.code, "INVALID_PROPERTY_NAMES")
                read.assert_not_called()
                self.assertEqual(self.body.reads, 0)

    def test_explicit_fields_replace_default_bom_and_keep_zero_false_empty_missing_and_unknown_distinct(self):
        values = {"Text": "", "Count": 0, "Ratio": 0.0, "Enabled": False, "Unknown": None}
        native_types = {"Count": "App::PropertyInteger", "Ratio": "App::PropertyFloat", "Enabled": "App::PropertyBool"}
        picked = PftBomObject(self.doc, "Custom", values, native_types)
        names = ["Text", "Count", "Ratio", "Enabled", "Missing", "Unknown"]
        result = self.read([picked.Name], names)
        self.assertEqual(result["property_scope"], "explicit")
        self.assertEqual(result["property_names"], names)
        fields = {field["name"]: field for field in result["properties"][0]["fields"]}
        self.assertEqual({name: field["status"] for name, field in fields.items()}, {
            "Text": "empty", "Count": "available", "Ratio": "available", "Enabled": "available", "Missing": "missing", "Unknown": "unknown"})
        self.assertEqual(fields["Text"]["value"], "")
        self.assertIs(type(fields["Count"]["value"]), int); self.assertEqual(fields["Count"]["value"], 0)
        self.assertIs(type(fields["Ratio"]["value"]), float); self.assertEqual(fields["Ratio"]["value"], 0.0)
        self.assertIs(fields["Enabled"]["value"], False)
        self.assertIsNone(fields["Missing"]["value"]); self.assertIsNone(fields["Unknown"]["value"])
        self.assertEqual(picked.bom_reads, ["Text", "Count", "Ratio", "Enabled", "Unknown"])
        self.assertEqual([field["kind"] for field in result["properties"][0]["fields"]],
                         ["string", "integer", "float", "boolean", None, "string"])
        self.assertFalse(any(name in picked.type_reads for name, _ in tools.BOM_FIELDS))

    def test_explicit_empty_fields_skip_all_property_getters_and_schema_reads(self):
        picked = PftBomObject(self.doc, "EmptyFields", {"Custom": AssertionError("field getter forbidden")})
        picked.properties_error = True
        result = self.read([picked.Name], [])
        self.assertEqual(result["properties"], [])
        self.assertEqual(result["property_names"], [])
        self.assertEqual(picked.property_list_reads, 0)
        self.assertEqual(picked.bom_reads, [])
        self.assertEqual(picked.type_reads, [])
        self.assertEqual(picked.reads, 1)  # Existing selected-object dimensions only.

    def test_custom_fields_do_not_read_unselected_values_or_resolve_link_sources_or_extra_shapes(self):
        source = PftBomObject(self.doc, "SourceOnly", {"Custom": AssertionError("source value forbidden")})
        picked = PftBomObject(self.doc, "InstanceOnly", {"Custom": "instance report", "LinkedObject": source,
            "Shape": PftOpaqueValue()}, {"LinkedObject": "App::PropertyLink", "Shape": "Part::PropertyPartShape"}, kind="App::Link")
        result = self.read([picked.Name], ["Custom", "LinkedObject", "Shape"])
        fields = result["properties"][0]["fields"]
        self.assertEqual(fields[0]["value"], "instance report")
        self.assertEqual([field["status"] for field in fields], ["available", "unsupported", "unsupported"])
        self.assertEqual(picked.bom_reads, ["Custom"])
        self.assertEqual((picked.reads, source.reads), (1, 0))
        self.assertEqual(source.bom_reads, [])
        self.assertFalse(result["properties"][0]["link_source_resolved"])
        self.assertEqual(result["properties"][0]["locality"], "not_verified")
        self.assertEqual(self.gui.Selection.records, [])
        self.assertIs(self.app.ActiveDocument, self.doc)

    def test_custom_scalar_types_refuse_wrong_schemas_and_nonfinite_values_without_stringifying(self):
        values = {"IntegerBool": False, "BoolNumber": 0, "NotString": PftOpaqueValue(), "Object": PftOpaqueValue(),
            "NaN": float("nan"), "Infinity": float("inf"), "Big": 2 ** 100, "FloatPrecision": 2 ** 53 + 1, "IntegerFloat": 1.5,
            "TypeFailure": "unread", "ValueFailure": RuntimeError("synthetic private native detail")}
        types_ = {"IntegerBool": "App::PropertyInteger", "BoolNumber": "App::PropertyBool", "Object": "App::PropertyPythonObject",
            "NaN": "App::PropertyFloat", "Infinity": "App::PropertyFloat", "Big": "App::PropertyInteger", "FloatPrecision": "App::PropertyFloat", "IntegerFloat": "App::PropertyInteger",
            "TypeFailure": RuntimeError("synthetic private schema detail")}
        picked = PftBomObject(self.doc, "Strict", values, types_)
        result = self.read([picked.Name], list(values))
        fields = {field["name"]: field for field in result["properties"][0]["fields"]}
        for name in ("IntegerBool", "BoolNumber", "NotString", "Object", "IntegerFloat"):
            self.assertEqual(fields[name]["status"], "unsupported")
        for name in ("NaN", "Infinity", "Big", "FloatPrecision"):
            self.assertEqual(fields[name]["status"], "unknown")
        for name in ("TypeFailure", "ValueFailure"):
            self.assertEqual(fields[name]["status"], "read_failed")
        self.assertNotIn("Object", picked.bom_reads); self.assertNotIn("TypeFailure", picked.bom_reads)
        self.assertTrue(all(field["value"] is None for field in fields.values()))
        self.assertNotIn("synthetic private", json.dumps(result))
        self.assertNotIn("synthetic private", tools.format_information(result) + tools.format_markdown(result))

    def test_known_quantity_dimensions_pair_internal_value_with_matching_unit_and_never_display_schema(self):
        signatures = [((0,0,0,0,0,0,0,0), "1"), ((1,0,0,0,0,0,0,0), "mm"), ((2,0,0,0,0,0,0,0), "mm^2"),
            ((3,0,0,0,0,0,0,0), "mm^3"), ((0,1,0,0,0,0,0,0), "kg"), ((0,0,1,0,0,0,0,0), "s"), ((0,0,0,0,0,0,0,1), "deg")]
        values = {"Quantity" + str(index): Quantity(25.4 if index else 0, signature) for index, (signature, _) in enumerate(signatures)}
        picked = PftBomObject(self.doc, "Quantities", values, {name: "App::PropertyQuantity" for name in values})
        fields = self.read([picked.Name], list(values))["properties"][0]["fields"]
        for field, (signature, unit) in zip(fields, signatures):
            self.assertEqual(field["status"], "available")
            self.assertEqual(field["kind"], "quantity")
            self.assertEqual(field["value"], {"kind": "quantity", "value": 25.4 if signature != (0,0,0,0,0,0,0,0) else 0,
                "unit": unit, "unit_signature": list(signature), "unit_system": "freecad_internal"})

    def test_quantity_aliases_are_explicit_and_unknown_units_never_publish_a_guessed_value(self):
        values = {"Length": Quantity(1), "Distance": Quantity(2), "Angle": Quantity(30, (0,0,0,0,0,0,0,1)),
            "Compound": Quantity(1, (1,0,-1,0,0,0,0,0)), "BadSignature": Quantity(2, "mm"),
            "MissingSignature": Quantity(3), "BadValue": Quantity(float("nan")), "WrongType": PftOpaqueValue(),
            "ContradictoryLength": Quantity(30, (0,0,0,0,0,0,0,1))}
        del values["MissingSignature"].Unit.Signature
        kinds = {name: "App::PropertyQuantity" for name in values}
        kinds.update(Length="App::PropertyLength", Distance="App::PropertyDistance", Angle="App::PropertyAngle", ContradictoryLength="App::PropertyLength")
        picked = PftBomObject(self.doc, "QuantityBoundaries", values, kinds)
        fields = {field["name"]: field for field in self.read([picked.Name], list(values))["properties"][0]["fields"]}
        for name in ("Length", "Distance", "Angle"):
            self.assertEqual(fields[name]["status"], "available")
        for name in ("BadSignature", "MissingSignature", "BadValue", "ContradictoryLength"):
            self.assertEqual(fields[name]["status"], "unknown")
            self.assertIsNone(fields[name]["value"])
        self.assertEqual(fields["Compound"]["status"], "available")
        self.assertEqual(fields["Compound"]["value"]["unit"], "mm*s^-1")
        self.assertEqual(fields["WrongType"]["status"], "unsupported")
        self.assertIsNone(fields["WrongType"]["value"])

    def test_explicit_enumerations_read_only_selected_text_and_keep_empty_unknown_and_wrong_types(self):
        values = {"Selected": "Choice | <b>one</b>", "Empty": "", "Unknown": None,
                  "Index": 0, "FalseChoice": False, "OptionsList": ["First", "Second"],
                  "Failed": RuntimeError("synthetic getter failure")}
        picked = PftBomObject(self.doc, "Enums", values, {name: "App::PropertyEnumeration" for name in values})
        picked.getEnumerationsOfProperty = lambda *_: self.fail("must not enumerate choices")
        fields = {field["name"]: field for field in self.read([picked.Name], list(values))["properties"][0]["fields"]}
        self.assertEqual(fields["Selected"]["value"], values["Selected"])
        self.assertEqual(fields["Selected"]["kind"], "enumeration")
        self.assertEqual(fields["Empty"]["status"], "empty")
        self.assertEqual(fields["Unknown"]["status"], "unknown")
        for name in ("Index", "FalseChoice", "OptionsList"):
            self.assertEqual(fields[name]["status"], "unsupported")
            self.assertIsNone(fields[name]["value"])
        self.assertEqual(fields["Failed"]["status"], "read_failed")
        markdown = tools.format_markdown(self.read([picked.Name], ["Selected"]))
        self.assertIn(r"Choice \| &lt;b&gt;one&lt;/b&gt;", markdown)
        self.assertNotIn("<b>", markdown)

    def test_enum_strings_share_utf8_limits_and_default_bom_does_not_expand_to_enums(self):
        picked = PftBomObject(self.doc, "EnumText", {"Mode": "示" * 2000 + "\x00hidden", "PartNumber": "Choice"},
                              {"Mode": "App::PropertyEnumeration", "PartNumber": "App::PropertyEnumeration"})
        field = self.read([picked.Name], ["Mode"])["properties"][0]["fields"][0]
        self.assertLessEqual(len(field["value"].encode("utf-8")), tools.PFT_MAX_PROPERTY_BYTES)
        self.assertTrue(field["truncated"])
        self.assertEqual(field["status"], "available")
        picked.bom_reads.clear()
        fields = self.read([picked.Name])["properties"][0]["fields"]
        self.assertEqual(next(row for row in fields if row["name"] == "PartNumber")["status"], "unsupported")
        self.assertEqual(picked.bom_reads, [])

    def test_typed_quantities_use_native_internal_basis_and_reject_contradictory_dimensions(self):
        dimensions = {
            "Area": ((2,0,0,0,0,0,0,0), "mm^2"),
            "Volume": ((3,0,0,0,0,0,0,0), "mm^3"),
            "Mass": ((0,1,0,0,0,0,0,0), "kg"),
            "Density": ((-3,1,0,0,0,0,0,0), "mm^-3*kg"),
            "Speed": ((1,0,-1,0,0,0,0,0), "mm*s^-1"),
            "Acceleration": ((1,0,-2,0,0,0,0,0), "mm*s^-2"),
            "Force": ((1,1,-2,0,0,0,0,0), "mm*kg*s^-2"),
            "Pressure": ((-1,1,-2,0,0,0,0,0), "mm^-1*kg*s^-2"),
            "Frequency": ((0,0,-1,0,0,0,0,0), "s^-1"),
            "Power": ((2,1,-3,0,0,0,0,0), "mm^2*kg*s^-3"),
            "Temperature": ((0,0,0,0,1,0,0,0), "K"),
        }
        values = {name: Quantity(0 if name == "Mass" else 2.5, signature) for name, (signature, _) in dimensions.items()}
        kinds = {name: "App::Property" + name for name in values}
        picked = PftBomObject(self.doc, "TypedQuantities", values, kinds)
        snapshot = self.read([picked.Name], list(values))
        fields = {field["name"]: field for field in snapshot["properties"][0]["fields"]}
        for name, (signature, unit) in dimensions.items():
            self.assertEqual(fields[name]["status"], "available")
            self.assertEqual(fields[name]["value"], {"kind": "quantity", "value": values[name].Value,
                "unit": unit, "unit_signature": list(signature), "unit_system": "freecad_internal"})
        info = tools.format_information(snapshot)
        self.assertIn("0 kg", info)
        self.assertIn("2.5 mm^-1*kg*s^-2", info)
        self.assertNotIn("MPa", info)
        for value in values.values():
            value.Unit.Signature = [1] * 8
        for field in self.read([picked.Name], list(values))["properties"][0]["fields"]:
            self.assertEqual(field["status"], "unknown")
            self.assertIsNone(field["value"])

    def test_nested_attribute_error_is_unknown_without_guessing_missing_api_or_getter_failure(self):
        def unavailable(_):
            raise AttributeError("synthetic unavailable member")
        unavailable_unit = type("Unit", (), {"Signature": property(unavailable)})()
        candidates = [type("Quantity", (), {"Value": property(unavailable)})(),
                      type("Quantity", (), {"Value": 2.5, "Unit": property(unavailable)})(),
                      type("Quantity", (), {"Value": 2.5, "Unit": unavailable_unit})()]
        for index, value in enumerate(candidates):
            with self.subTest(index=index):
                picked = PftBomObject(self.doc, "UnknownQuantity" + str(index), {"Value": value},
                                      {"Value": "App::PropertyQuantity"})
                field = self.read([picked.Name], ["Value"])["properties"][0]["fields"][0]
                self.assertEqual(field["status"], "unknown")
                self.assertIsNone(field["value"])

    def test_generic_quantity_accepts_valid_compound_signatures_without_inventing_si_conversions(self):
        values = {"Current": Quantity(3, (0,0,0,1,0,0,0,0)), "Compound": Quantity(4, (2,1,-3,-1,0,0,0,0)),
                  "Extreme": Quantity(5, (-128,127,-128,127,-128,127,-128,127))}
        picked = PftBomObject(self.doc, "GenericQuantities", values, {name: "App::PropertyQuantity" for name in values})
        fields = {field["name"]: field for field in self.read([picked.Name], list(values))["properties"][0]["fields"]}
        self.assertEqual(fields["Current"]["value"]["unit"], "A")
        self.assertEqual(fields["Compound"]["value"]["unit"], "mm^2*kg*s^-3*A^-1")
        self.assertEqual(fields["Compound"]["value"]["value"], 4)
        self.assertEqual(fields["Extreme"]["status"], "available")
        self.assertLessEqual(len(fields["Extreme"]["value"]["unit"]), 128)

    def test_explicit_text_keeps_existing_utf8_and_total_budgets_and_empty_target_scope_reads_nothing(self):
        picked = PftBomObject(self.doc, "CustomText", {"Custom": "示" * 1000, "Other": "A\x00B"})
        result = self.read([picked.Name], ["Custom", "Other"])
        fields = result["properties"][0]["fields"]
        self.assertEqual(fields[0]["value"], "示" * 682); self.assertTrue(fields[0]["truncated"])
        self.assertEqual(fields[1]["value"], "A B"); self.assertTrue(fields[1]["sanitized"])
        before = list(picked.bom_reads)
        self.assertEqual(self.read([], ["Custom"])["properties"], [])
        self.assertEqual(picked.bom_reads, before)
        names = ["Field" + str(index) for index in range(20)]
        objects = [PftBomObject(self.doc, "Many" + str(index), {name: "x" * tools.PFT_MAX_PROPERTY_BYTES for name in names}) for index in range(4)]
        with self.assertRaises(tools.PftDocumentToolsError) as error:
            self.read([obj.Name for obj in objects], names)
        self.assertEqual(error.exception.code, "BOM_PROPERTIES_TOO_LARGE")

    def test_explicit_rendering_has_native_types_units_zero_and_false_without_bom_or_quantity_claims(self):
        picked = PftBomObject(self.doc, "Rendered", {"Text": "![x](file:///neutral) | <script>\nnext", "Count": 0,
            "Enabled": False, "Length": Quantity(25.4), "MissingValue": None},
            {"Count": "App::PropertyInteger", "Enabled": "App::PropertyBool", "Length": "App::PropertyLength"})
        result = self.read([picked.Name], ["Text", "Count", "Enabled", "Length", "MissingValue", "Absent"])
        info, markdown = tools.format_information(result), tools.format_markdown(result)
        self.assertIn("所选对象属性", info); self.assertNotIn("（BOM）", info + markdown)
        self.assertIn("App::PropertyInteger | 0 |", info)
        self.assertIn("App::PropertyBool | 否 |", info)
        self.assertIn("25.4 mm", info)
        self.assertIn("未知", info); self.assertIn("缺失", info)
        self.assertIn(r"\!\[x\]\(file:///neutral\) \| &lt;script&gt;<br>next", markdown)
        self.assertNotIn("<script>", markdown)
        self.assertNotIn(self.doc.FileName, markdown)
        self.assertIn("所选对象属性（BOM）", tools.format_information(self.read([picked.Name])))


class PftMarkdownExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pft-markdown-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.target = self.root / "notes.md"
    def assert_no_stage(self): self.assertEqual(list(self.root.glob(".pft-markdown-*")), [])
    def test_cancel_never_creates_a_file_or_stage(self):
        with patch.object(tools.tempfile, "mkstemp") as stage:
            self.assertIsNone(tools.prepare_markdown_export(None, "notes"))
            self.assertIsNone(tools.write_markdown_export(None))
            stage.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])
    def test_new_utf8_markdown_is_atomically_published_and_stage_cleaned(self):
        plan = tools.prepare_markdown_export(str(self.target), "# 示例\n")
        self.assertFalse(plan["exists"])
        self.assertFalse(self.target.exists())
        self.assertEqual(tools.write_markdown_export(plan), str(self.target))
        self.assertEqual(self.target.read_text(), "# 示例\n")
        self.assert_no_stage()
    def test_overwrite_requires_confirmation_and_replaces_only_after_check(self):
        self.target.write_text("old")
        plan = tools.prepare_markdown_export(str(self.target), "new")
        with self.assertRaises(tools.PftDocumentToolsError) as caught: tools.write_markdown_export(plan)
        self.assertEqual(caught.exception.code, "OVERWRITE_REQUIRED")
        self.assertEqual(self.target.read_text(), "old")
        tools.write_markdown_export(plan, overwrite=True)
        self.assertEqual(self.target.read_text(), "new")
        self.assert_no_stage()
    def test_changed_or_newly_appeared_target_is_preserved(self):
        for existed in (False, True):
            if self.target.exists(): self.target.unlink()
            if existed: self.target.write_text("original")
            plan = tools.prepare_markdown_export(str(self.target), "export")
            self.target.write_text("concurrent user change")
            with self.assertRaises(tools.PftDocumentToolsError) as caught: tools.write_markdown_export(plan, overwrite=True)
            self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
            self.assertEqual(self.target.read_text(), "concurrent user change")
        self.assert_no_stage()
    def test_symlink_leaf_and_ancestor_are_refused_without_touching_original(self):
        original = self.root / "original.md"; original.write_text("keep")
        self.target.symlink_to(original)
        with self.assertRaises(tools.PftDocumentToolsError): tools.prepare_markdown_export(str(self.target), "replace")
        self.assertEqual(original.read_text(), "keep")
        folder = self.root / "alias"; folder.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(tools.PftDocumentToolsError): tools.prepare_markdown_export(str(folder / "new.md"), "new")
    def test_invalid_extension_missing_parent_and_oversized_data_never_write(self):
        for filename, text in ((str(self.root / "model.FCStd"), "bad"), (str(self.root / "missing" / "notes.md"), "bad"),
                               (str(self.target), "x" * (tools.PFT_MAX_MARKDOWN_BYTES + 1)), (str(self.target), "\ud800")):
            with self.assertRaises(tools.PftDocumentToolsError): tools.prepare_markdown_export(filename, text)
        self.assertEqual(list(self.root.iterdir()), [])
    def test_failed_replace_retains_old_bytes_and_cleans_stage(self):
        self.target.write_text("original")
        plan = tools.prepare_markdown_export(str(self.target), "new")
        with patch.object(tools.os, "replace", side_effect=OSError("synthetic failure")):
            with self.assertRaises(tools.PftDocumentToolsError): tools.write_markdown_export(plan, overwrite=True)
        self.assertEqual(self.target.read_text(), "original")
        self.assert_no_stage()
    def test_new_file_publish_never_overwrites_a_racing_destination(self):
        plan = tools.prepare_markdown_export(str(self.target), "new")
        original_link = os.link
        def race(source, destination):
            Path(destination).write_text("user created during publication")
            original_link(source, destination)
        with patch.object(tools.os, "link", side_effect=race):
            with self.assertRaises(tools.PftDocumentToolsError): tools.write_markdown_export(plan)
        self.assertEqual(self.target.read_text(), "user created during publication")
        self.assert_no_stage()


if __name__ == "__main__": unittest.main()
