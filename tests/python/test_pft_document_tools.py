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


class Vector:
    """Native-shaped fixture: only the three documented scalar getters are read."""
    __module__ = "Base"
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z
    def __iter__(self): raise AssertionError("must not iterate a vector")
    def __str__(self): raise AssertionError("must not stringify a vector")
    def __repr__(self): raise AssertionError("must not represent a vector")
    @property
    def Length(self): raise AssertionError("must not calculate vector length")
    @property
    def Unit(self): raise AssertionError("bare vectors do not report quantity units")
    @property
    def Proxy(self): raise AssertionError("must not inspect a proxy")


class Rotation:
    """Native-shaped fixture; only Q is part of the readonly contract."""
    __module__ = "Base"
    def __init__(self, quaternion=(0.0, 0.0, 0.0, 1.0)):
        self.quaternion, self.accesses = quaternion, []
    @property
    def Q(self):
        self.accesses.append("Q")
        if isinstance(self.quaternion, Exception): raise self.quaternion
        return self.quaternion
    @property
    def Axis(self): raise AssertionError("must not derive a rotation axis")
    @property
    def Angle(self): raise AssertionError("must not derive a rotation angle")
    @property
    def Matrix(self): raise AssertionError("must not derive a matrix")
    @property
    def Proxy(self): raise AssertionError("must not inspect a proxy")
    def toMatrix(self): raise AssertionError("must not transform native data")
    def normalize(self): raise AssertionError("must not normalize native quaternions")
    def __str__(self): raise AssertionError("must not stringify a native rotation")
    def __repr__(self): raise AssertionError("must not represent a native rotation")


class Placement:
    __module__ = "Base"
    def __init__(self, base=Ellipsis, rotation=Ellipsis):
        self.base_value = Vector() if base is Ellipsis else base
        self.rotation_value = Rotation() if rotation is Ellipsis else rotation
        self.accesses = []
    @property
    def Base(self):
        self.accesses.append("Base")
        if isinstance(self.base_value, Exception): raise self.base_value
        return self.base_value
    @property
    def Rotation(self):
        self.accesses.append("Rotation")
        if isinstance(self.rotation_value, Exception): raise self.rotation_value
        return self.rotation_value
    @property
    def Matrix(self): raise AssertionError("must not derive a placement matrix")
    @property
    def Proxy(self): raise AssertionError("must not inspect a proxy")
    def toMatrix(self): raise AssertionError("must not derive a placement matrix")
    def multVec(self, *_): raise AssertionError("must not apply native transforms")
    def __str__(self): raise AssertionError("must not stringify native placements")
    def __repr__(self): raise AssertionError("must not represent native placements")


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

    def test_constrained_native_scalar_getters_copy_values_and_keep_frozen_rendering(self):
        values = {"Count": -3, "Ratio": 1.25, "Percent": 0, "Precision": 0.001,
                  "Measure": Quantity(2.5, (0,) * 8)}
        kinds = {"Count": "App::PropertyIntegerConstraint", "Ratio": "App::PropertyFloatConstraint",
                 "Percent": "App::PropertyPercent", "Precision": "App::PropertyPrecision",
                 "Measure": "App::PropertyQuantityConstraint"}
        picked = PftBomObject(self.doc, "ConstrainedParameters", values, kinds)
        picked.getPropertyByName = lambda *_: self.fail("must not read native constraints or editor metadata")
        snapshot = self.read([picked.Name], list(values))
        fields = {field["name"]: field for field in snapshot["properties"][0]["fields"]}
        self.assertEqual([field["kind"] for field in fields.values()], ["integer", "float", "integer", "float", "quantity"])
        for name, value in values.items():
            self.assertEqual(fields[name]["status"], "available")
            self.assertEqual(fields[name]["type"], kinds[name])
            self.assertEqual(fields[name]["value"], value if name != "Measure" else {
                "kind": "quantity", "value": 2.5, "unit": "1", "unit_signature": [0] * 8,
                "unit_system": "freecad_internal"})
        self.assertEqual(picked.bom_reads, list(values))
        information, markdown = tools.format_information(snapshot), tools.format_markdown(snapshot)
        self.assertIn("App::PropertyIntegerConstraint | -3 |", information)
        self.assertIn("App::PropertyPercent | 0 |", information)
        self.assertIn("App::PropertyPrecision | 0.001 |", information)
        self.assertIn("App::PropertyQuantityConstraint | 2.5 1 |", information)
        frozen = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
        picked.bom_values.update(Count=7, Ratio=0.0, Percent=75, Precision=0.25)
        values["Measure"].Value = 8.5
        self.assertEqual(self.read([picked.Name], list(values))["properties"][0]["fields"][0]["value"], 7)
        self.assertEqual(json.dumps(snapshot, ensure_ascii=False, allow_nan=False), frozen)
        self.assertEqual(tools.format_information(snapshot), information)
        self.assertEqual(tools.format_markdown(snapshot), markdown)
        self.assertEqual(self.gui.Selection.records, [])
        self.assertIs(self.app.ActiveDocument, self.doc)

    def test_constrained_scalar_types_are_exact_strict_and_do_not_infer_bounds_or_units(self):
        for native_type, good, wrong in (("App::PropertyIntegerConstraint", 0, 1.0),
                                        ("App::PropertyPercent", 125, 1.0),
                                        ("App::PropertyFloatConstraint", 0.0, 0),
                                        ("App::PropertyPrecision", -0.25, 0)):
            for value, status in ((good, "available"), (wrong, "unsupported"), (False, "unsupported"),
                                  ((1, 0, 100, 1), "unsupported"), (None, "unknown"),
                                  (RuntimeError("synthetic getter failure"), "read_failed")):
                with self.subTest(native_type=native_type, value_type=type(value).__name__, status=status):
                    picked = PftBomObject(self.doc, "ScalarBoundary" + str(len(self.doc.Objects)), {"Parameter": value}, {"Parameter": native_type})
                    field = self.read([picked.Name], ["Parameter"])["properties"][0]["fields"][0]
                    self.assertEqual(field["status"], status)
                    self.assertEqual(field["value"], good if status == "available" else None)
                    self.assertNotIn("unit", field)
            if native_type.endswith(("FloatConstraint", "Precision")):
                for value in (math.nan, math.inf, -math.inf):
                    picked.bom_values["Parameter"] = value
                    field = self.read([picked.Name], ["Parameter"])["properties"][0]["fields"][0]
                    self.assertEqual((field["status"], field["value"]), ("unknown", None))
            else:
                picked.bom_values["Parameter"] = 2 ** 63
                field = self.read([picked.Name], ["Parameter"])["properties"][0]["fields"][0]
                self.assertEqual((field["status"], field["value"]), ("unknown", None))

    def test_quantity_constraint_uses_reported_signature_without_guessing_physical_kind(self):
        for signature, unit in (((0,) * 8, "1"), ((1, 0, 0, 0, 0, 0, 0, 0), "mm"),
                                ((0, 0, 0, 0, 0, 0, 0, 1), "deg")):
            with self.subTest(signature=signature):
                value = Quantity(0.0, signature)
                picked = PftBomObject(self.doc, "QuantityConstraint" + str(len(self.doc.Objects)), {"Parameter": value},
                                      {"Parameter": "App::PropertyQuantityConstraint"})
                field = self.read([picked.Name], ["Parameter"])["properties"][0]["fields"][0]
                self.assertEqual(field["status"], "available")
                self.assertEqual(field["value"]["value"], 0.0)
                self.assertEqual(field["value"]["unit"], unit)
                self.assertEqual(field["value"]["unit_signature"], list(signature))
                value.Unit.Signature = [True] * 8
                invalid = self.read([picked.Name], ["Parameter"])["properties"][0]["fields"][0]
                self.assertEqual((invalid["status"], invalid["value"]), ("unknown", None))
        for failed in ("Value", "Unit", "Signature"):
            accesses = []
            def read(name, value):
                accesses.append(name)
                if name == failed:
                    raise RuntimeError("synthetic quantity constraint failure")
                return value
            unit = type("Unit", (), {"Signature": property(lambda _: read("Signature", [0] * 8))})()
            value = type("Quantity", (), {"Value": property(lambda _: read("Value", 2.5)),
                                           "Unit": property(lambda _: read("Unit", unit))})()
            picked.bom_values["Parameter"] = value
            row = self.read([picked.Name], ["Parameter"])["properties"][0]
            self.assertEqual((row["status"], row["fields"][0]["status"], row["fields"][0]["value"]),
                             ("read_failed", "read_failed", None))
            self.assertEqual(accesses, ["Value", "Unit", "Signature"][:["Value", "Unit", "Signature"].index(failed) + 1])

    def test_new_constrained_types_never_expand_default_bom_or_accept_type_prefixes(self):
        for native_type in ("App::PropertyIntegerConstraint", "App::PropertyPercent", "App::PropertyFloatConstraint",
                            "App::PropertyPrecision", "App::PropertyQuantityConstraint"):
            with self.subTest(native_type=native_type):
                picked = PftBomObject(self.doc, "ConstrainedScope" + str(len(self.doc.Objects)), {"PartNumber": AssertionError("must not read default")},
                                      {"PartNumber": native_type})
                fields = self.read([picked.Name])["properties"][0]["fields"]
                self.assertEqual([field["name"] for field in fields], [name for name, _ in tools.BOM_FIELDS])
                self.assertEqual(fields[0]["status"], "unsupported")
                self.assertEqual(picked.bom_reads, [])
                picked.bom_types["PartNumber"] = native_type + "Unrecognized"
                field = self.read([picked.Name], ["PartNumber"])["properties"][0]["fields"][0]
                self.assertEqual((field["status"], field["value"]), ("unsupported", None))
                self.assertEqual(picked.bom_reads, [])

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


    def test_explicit_primitive_lists_preserve_order_duplicates_zero_false_and_native_bool_tuple(self):
        values = {"Tags": ["A", "", "A", "中|文"], "Counts": [0, -2, 3],
                  "Weights": [0.0, 1.25], "Flags": (False, True), "Empty": []}
        kinds = {"Tags": "App::PropertyStringList", "Counts": "App::PropertyIntegerList",
                 "Weights": "App::PropertyFloatList", "Flags": "App::PropertyBoolList",
                 "Empty": "App::PropertyStringList"}
        picked = PftBomObject(self.doc, "PrimitiveLists", values, kinds)
        snapshot = self.read([picked.Name], list(values))
        fields = {field["name"]: field for field in snapshot["properties"][0]["fields"]}
        for name, value in values.items():
            self.assertEqual(fields[name]["value"], list(value))
            self.assertEqual(fields[name]["item_count"], len(value))
            self.assertEqual(fields[name]["status"], "empty" if name == "Empty" else "available")
            self.assertFalse(fields[name]["truncated"])
        self.assertIs(type(fields["Counts"]["value"][0]), int)
        self.assertIs(type(fields["Weights"]["value"][0]), float)
        self.assertIs(fields["Flags"]["value"][0], False)
        self.assertEqual(fields["Flags"]["kind"], "boolean_list")
        self.assertEqual(picked.bom_reads, list(values))
        text = tools.format_information(snapshot)
        self.assertIn('App::PropertyIntegerList | [0,-2,3] |', text)
        self.assertIn('App::PropertyBoolList | [false,true] |', text)
        self.assertIn('App::PropertyStringList | [] | 空值', text)
        self.assertEqual(self.gui.Selection.records, [])
        self.assertIs(self.app.ActiveDocument, self.doc)

    def test_local_integer_lists_preserve_signed64_without_javascript_rounding(self):
        values = [-(2 ** 63), -(2 ** 53 + 1), 0, 2 ** 53 + 1, 2 ** 63 - 1]
        picked = PftBomObject(self.doc, "NativeIntegerBounds", {"Counts": values},
                              {"Counts": "App::PropertyIntegerList"})
        snapshot = self.read([picked.Name], ["Counts"])
        field = snapshot["properties"][0]["fields"][0]
        self.assertEqual(field["status"], "available")
        self.assertEqual(field["value"], values)
        expected = json.dumps(values, separators=(",", ":"))
        self.assertIn(expected, tools.format_information(snapshot))
        self.assertIn(tools.escape_markdown(expected), tools.format_markdown(snapshot))
        for value in (-(2 ** 63) - 1, 2 ** 63):
            picked.bom_values["Counts"] = [value]
            result = self.read([picked.Name], ["Counts"])["properties"][0]["fields"][0]
            self.assertEqual(result["status"], "unknown")
            self.assertIsNone(result["value"])
        self.assertEqual(field["value"], values)

    def test_primitive_list_empty_missing_unknown_and_getter_failure_remain_distinct(self):
        values = {"Empty": [], "BlankItem": [""], "Unknown": None,
                  "Failed": RuntimeError("synthetic list getter detail")}
        picked = PftBomObject(self.doc, "ListStates", values,
                              {name: "App::PropertyStringList" for name in values})
        row = self.read([picked.Name], [*values, "Missing"])["properties"][0]
        fields = {field["name"]: field for field in row["fields"]}
        self.assertEqual({name: field["status"] for name, field in fields.items()}, {
            "Empty": "empty", "BlankItem": "available", "Unknown": "unknown",
            "Failed": "read_failed", "Missing": "missing"})
        self.assertEqual(fields["Empty"]["value"], [])
        self.assertEqual(fields["BlankItem"]["value"], [""])
        self.assertEqual(row["status"], "read_failed")
        for name in ("Unknown", "Failed", "Missing"):
            self.assertIsNone(fields[name]["value"])
        self.assertNotIn("Missing", picked.bom_reads)
        self.assertNotIn("synthetic list getter detail", json.dumps(row))

    def test_primitive_lists_reject_mixed_types_nested_objects_and_nonfinite_values(self):
        values = {"IntegerBool": [False], "IntegerFloat": [1.0], "BoolNumber": [0],
                  "FloatBool": [False], "FloatInteger": [0], "StringNumber": ["ok", 0],
                  "Nested": [["value"]], "Object": [PftOpaqueValue()], "NoneItem": [None],
                  "Container": PftOpaqueValue(), "Mapping": {"a": "value"}, "String": "abc",
                  "NaN": [math.nan], "Infinity": [math.inf], "LargeInteger": [2 ** 100]}
        kinds = {name: "App::PropertyStringList" for name in values}
        kinds.update({"IntegerBool": "App::PropertyIntegerList", "IntegerFloat": "App::PropertyIntegerList",
                      "BoolNumber": "App::PropertyBoolList", "FloatBool": "App::PropertyFloatList",
                      "FloatInteger": "App::PropertyFloatList", "NaN": "App::PropertyFloatList",
                      "Infinity": "App::PropertyFloatList", "LargeInteger": "App::PropertyIntegerList"})
        picked = PftBomObject(self.doc, "StrictLists", values, kinds)
        fields = {field["name"]: field for field in self.read([picked.Name], list(values))["properties"][0]["fields"]}
        for name, field in fields.items():
            with self.subTest(name=name):
                self.assertEqual(field["status"], "unknown" if name in ("NaN", "Infinity", "LargeInteger") else "unsupported")
                self.assertIsNone(field["value"])
                self.assertFalse(field["truncated"])

    def test_custom_list_subclasses_are_not_iterated_or_stringified(self):
        class OpaqueList(list):
            def __iter__(self): raise AssertionError("must not iterate a custom container")
            def __len__(self): raise AssertionError("must not inspect a custom container")
            def __str__(self): raise AssertionError("must not stringify a custom container")
            def __repr__(self): raise AssertionError("must not represent a custom container")
        picked = PftBomObject(self.doc, "CustomList", {"Names": OpaqueList(["A"])},
                              {"Names": "App::PropertyStringList"})
        field = self.read([picked.Name], ["Names"])["properties"][0]["fields"][0]
        self.assertEqual(field["status"], "unsupported")
        self.assertIsNone(field["value"])
        self.assertNotIn("item_count", field)

    def test_list_types_never_expand_default_bom_or_read_unsupported_list_getters(self):
        values = {"PartNumber": ["public"], "Links": AssertionError("must not read links"),
                  "Vectors": [],
                  "Placements": [], "Tags": ["explicit only"]}
        kinds = {"PartNumber": "App::PropertyStringList", "Links": "App::PropertyLinkList",
                 "Vectors": "App::PropertyVectorList", "Placements": "App::PropertyPlacementList",
                 "Tags": "App::PropertyStringList"}
        picked = PftBomObject(self.doc, "ListScope", values, kinds)
        row = self.read([picked.Name])["properties"][0]
        self.assertEqual([field["name"] for field in row["fields"]], [name for name, _ in tools.BOM_FIELDS])
        self.assertEqual(row["fields"][0]["status"], "unsupported")
        self.assertEqual(picked.bom_reads, [])
        fields = self.read([picked.Name], ["Links", "Vectors", "Placements", "Tags"])["properties"][0]["fields"]
        self.assertEqual([field["status"] for field in fields], ["unsupported", "empty", "empty", "available"])
        self.assertEqual(fields[1]["value"], [])
        self.assertEqual(picked.bom_reads, ["Vectors", "Placements", "Tags"])

    def test_explicit_vector_types_copy_raw_components_and_declare_only_type_supported_units(self):
        values = {"Raw": Vector(0.0, -2.5, 3.25), "Offset": Vector(10.0, 0.0, -4.0),
                  "Position": Vector(1.0, 2.0, 3.0)}
        kinds = {"Raw": "App::PropertyVector", "Offset": "App::PropertyVectorDistance", "Position": "App::PropertyPosition"}
        picked = PftBomObject(self.doc, "RawVectors", values, kinds)
        snapshot = self.read([picked.Name], list(values))
        fields = {field["name"]: field for field in snapshot["properties"][0]["fields"]}
        for name, vector in values.items():
            field = fields[name]
            self.assertEqual(field["kind"], "vector")
            self.assertEqual(field["status"], "available")
            self.assertEqual(field["value"], {"x": vector.x, "y": vector.y, "z": vector.z})
            self.assertEqual(field["unit_source"], "native_property_type")
            self.assertEqual(field["coordinate_scope"], "reported_property_value")
            self.assertEqual(field["coordinate_frame"], "not_declared_by_property_type")
            self.assertFalse(field["placement_applied_by_tool"])
            self.assertFalse(field["link_source_resolved"])
        self.assertIsNone(fields["Raw"]["unit"])
        self.assertIsNone(fields["Raw"]["unit_signature"])
        self.assertEqual(fields["Raw"]["unit_system"], "not_declared")
        for name in ("Offset", "Position"):
            self.assertEqual(fields[name]["unit"], "mm")
            self.assertEqual(fields[name]["unit_signature"], [1, 0, 0, 0, 0, 0, 0, 0])
            self.assertEqual(fields[name]["unit_system"], "freecad_internal")
        self.assertEqual(fields["Raw"]["value"]["x"], 0.0)
        self.assertIs(type(fields["Raw"]["value"]["x"]), float)
        self.assertEqual(self.gui.Selection.records, [])
        self.assertIs(self.app.ActiveDocument, self.doc)

    def test_vector_list_preserves_complete_order_duplicates_zero_and_empty_list(self):
        vectors = [Vector(), Vector(-1.0, 2.0, 3.0), Vector()]
        picked = PftBomObject(self.doc, "VectorSequences", {"Points": vectors, "Empty": []},
                              {"Points": "App::PropertyVectorList", "Empty": "App::PropertyVectorList"})
        fields = {field["name"]: field for field in self.read([picked.Name], ["Points", "Empty"])["properties"][0]["fields"]}
        self.assertEqual(fields["Points"]["value"], [{"x": 0.0, "y": 0.0, "z": 0.0},
                         {"x": -1.0, "y": 2.0, "z": 3.0}, {"x": 0.0, "y": 0.0, "z": 0.0}])
        self.assertEqual(fields["Points"]["kind"], "vector_list")
        self.assertEqual(fields["Points"]["item_count"], 3)
        self.assertEqual(fields["Points"]["status"], "available")
        self.assertIsNone(fields["Points"]["unit"])
        self.assertEqual(fields["Points"]["unit_system"], "not_declared")
        self.assertEqual(fields["Empty"]["value"], [])
        self.assertEqual(fields["Empty"]["item_count"], 0)
        self.assertEqual(fields["Empty"]["status"], "empty")
        vectors[0].x = 99.0
        vectors.append(Vector(4.0, 5.0, 6.0))
        self.assertEqual(fields["Points"]["value"][0]["x"], 0.0)
        self.assertEqual(len(fields["Points"]["value"]), 3)
        self.assertIsNot(fields["Points"]["value"][0], fields["Points"]["value"][2])

    def test_vector_attribute_absence_failure_nonfinite_and_wrong_scalar_types_are_distinct(self):
        def absent(_): raise AttributeError("synthetic unavailable component")
        def failed(_): raise RuntimeError("synthetic hidden vector detail")
        missing = type("Vector", (), {"__module__": "Base", "x": 0.0, "y": property(absent)})()
        broken = type("Vector", (), {"__module__": "Base", "x": 0.0, "y": property(failed)})()
        candidates = [(missing, "unknown"), (broken, "read_failed"), (Vector(math.nan), "unknown"),
                      (Vector(0.0, math.inf), "unknown"), (Vector(0.0, 1.0, -math.inf), "unknown"),
                      (Vector(False), "unsupported"), (Vector(1), "unsupported"), (Vector("1"), "unsupported"),
                      ((0.0, 0.0, 0.0), "unsupported"), ({"x": 0.0, "y": 0.0, "z": 0.0}, "unsupported"),
                      (type("Vector", (), {"x": 0.0, "y": 0.0, "z": 0.0})(), "unsupported")]
        for index, (value, expected) in enumerate(candidates):
            with self.subTest(index=index):
                picked = PftBomObject(self.doc, "VectorState" + str(index), {"Point": value}, {"Point": "App::PropertyPosition"})
                field = self.read([picked.Name], ["Point"])["properties"][0]["fields"][0]
                self.assertEqual(field["status"], expected)
                self.assertIsNone(field["value"])
                self.assertFalse(field["truncated"])
                self.assertNotIn("synthetic hidden vector detail", json.dumps(field))

    def test_vector_lists_reject_custom_containers_and_any_bad_member_without_a_prefix(self):
        class CustomList(list):
            def __iter__(self): raise AssertionError("must not iterate a custom vector list")
            def __len__(self): raise AssertionError("must not read custom vector list length")
            def __str__(self): raise AssertionError("must not stringify a custom vector list")
        candidates = [(CustomList([Vector()]), "unsupported"), ((Vector(),), "unsupported"),
                      ({"point": Vector()}, "unsupported"), ([Vector(), PftOpaqueValue()], "unsupported"),
                      ([Vector(), [0.0, 0.0, 0.0]], "unsupported"), ([Vector(), Vector(math.nan)], "unknown"),
                      ([Vector(), None], "unsupported")]
        for index, (value, expected) in enumerate(candidates):
            with self.subTest(index=index):
                picked = PftBomObject(self.doc, "VectorListState" + str(index), {"Points": value}, {"Points": "App::PropertyVectorList"})
                field = self.read([picked.Name], ["Points"])["properties"][0]["fields"][0]
                self.assertEqual(field["status"], expected)
                self.assertIsNone(field["value"])
                self.assertFalse(field["truncated"])
                if type(value) is list:
                    self.assertEqual(field["item_count"], len(value))
                else:
                    self.assertNotIn("item_count", field)

    def test_vector_unknown_empty_missing_and_failed_getter_do_not_collapse(self):
        values = {"Zero": Vector(), "Unknown": None, "Failed": RuntimeError("synthetic hidden vector getter"), "Empty": []}
        kinds = {"Zero": "App::PropertyVector", "Unknown": "App::PropertyVector", "Failed": "App::PropertyVector", "Empty": "App::PropertyVectorList"}
        picked = PftBomObject(self.doc, "DistinctVectors", values, kinds)
        row = self.read([picked.Name], [*values, "Missing"])["properties"][0]
        self.assertEqual({field["name"]: field["status"] for field in row["fields"]},
                         {"Zero": "available", "Unknown": "unknown", "Failed": "read_failed", "Empty": "empty", "Missing": "missing"})
        self.assertEqual(row["status"], "read_failed")
        self.assertNotIn("synthetic hidden vector getter", json.dumps(row))

    def test_vector_list_item_and_serialized_byte_limits_publish_no_partial_data(self):
        values = {"Small": [Vector()] * 20, "OverItems": [PftOpaqueValue()] * 101,
                  "OverBytes": [Vector(1.234567890123456, 2.345678901234567, 3.456789012345678)] * 100}
        picked = PftBomObject(self.doc, "BoundedVectors", values, {name: "App::PropertyVectorList" for name in values})
        fields = {field["name"]: field for field in self.read([picked.Name], list(values))["properties"][0]["fields"]}
        self.assertEqual(fields["Small"]["status"], "available")
        for name, reason in (("OverItems", "item_limit"), ("OverBytes", "utf8_budget")):
            self.assertEqual(fields[name]["status"], "unknown")
            self.assertIsNone(fields[name]["value"])
            self.assertTrue(fields[name]["truncated"])
            self.assertEqual(fields[name]["item_count"], len(values[name]))
            self.assertEqual(fields[name]["limit_reason"], reason)
        encoded_size = len(json.dumps([{"x": 0.0, "y": 0.0, "z": 0.0}], separators=(",", ":")).encode("utf-8"))
        with patch.object(tools, "PFT_MAX_PROPERTY_BYTES", encoded_size):
            self.assertEqual(tools._property_vector_list([Vector()])["status"], "available")
        with patch.object(tools, "PFT_MAX_PROPERTY_BYTES", encoded_size - 1):
            self.assertEqual(tools._property_vector_list([Vector()])["limit_reason"], "utf8_budget")

    def test_vectors_do_not_expand_default_bom_or_fetch_other_property_families(self):
        values = {"PartNumber": Vector(), "Raw": Vector(), "Point": Vector(), "Points": [],
                  "Placements": [], "Matrix": AssertionError("must not get matrices"),
                  "Links": AssertionError("must not get links"), "Python": AssertionError("must not get arbitrary python")}
        kinds = {"PartNumber": "App::PropertyVector", "Raw": "App::PropertyVector", "Point": "App::PropertyPosition",
                 "Points": "App::PropertyVectorList", "Placements": "App::PropertyPlacementList", "Matrix": "App::PropertyMatrix",
                 "Links": "App::PropertyLinkList", "Python": "App::PropertyPythonObject"}
        picked = PftBomObject(self.doc, "VectorScope", values, kinds)
        row = self.read([picked.Name])["properties"][0]
        self.assertEqual([field["name"] for field in row["fields"]], [name for name, _ in tools.BOM_FIELDS])
        self.assertEqual(row["fields"][0]["status"], "unsupported")
        self.assertEqual(picked.bom_reads, [])
        fields = self.read([picked.Name], ["Raw", "Point", "Points", "Placements", "Matrix", "Links", "Python"])["properties"][0]["fields"]
        self.assertEqual([field["status"] for field in fields], ["available", "available", "empty", "empty"] + ["unsupported"] * 3)
        self.assertEqual(picked.bom_reads, ["Raw", "Point", "Points", "Placements"])

    def test_vector_renderers_use_only_frozen_dtos_and_keep_unit_coordinate_provenance_visible(self):
        raw, position, vectors = Vector(0.0, -2.5, 3.0), Vector(4.0, 5.0, 6.0), [Vector()]
        picked = PftBomObject(self.doc, "VectorRender", {"Raw": raw, "Position": position, "Points": vectors},
                              {"Raw": "App::PropertyVector", "Position": "App::PropertyPosition", "Points": "App::PropertyVectorList"})
        snapshot = self.read([picked.Name], ["Raw", "Position", "Points"])
        before = list(picked.bom_reads)
        raw.x, position.y, vectors[0].z = 99.0, 99.0, 99.0
        picked.bom_values = {name: AssertionError("formatters must not reread vectors") for name in picked.bom_values}
        text, markdown = tools.format_information(snapshot), tools.format_markdown(snapshot)
        self.assertIn('"value":{"x":0.0,"y":-2.5,"z":3.0}', text)
        self.assertIn('"unit":null', text)
        self.assertIn('"unit":"mm"', text)
        self.assertIn('"coordinate_frame":"not_declared_by_property_type"', text)
        self.assertIn('"placement_applied_by_tool":false', text)
        self.assertIn('"value":[{"x":0.0,"y":0.0,"z":0.0}]', text)
        for field in snapshot["properties"][0]["fields"]:
            self.assertIn(tools.escape_markdown(tools._property_display(field, lambda value: value)), markdown)
        self.assertNotIn("99.0", text + markdown)
        self.assertNotIn(self.doc.FileName, markdown)
        self.assertEqual(picked.bom_reads, before)
        self.assertIn("60 | 40 | 8", tools.format_dimensions(snapshot))

    def test_vector_float_extremes_precision_and_negative_zero_survive_frozen_json_and_rendering(self):
        maximum, subnormal, precise = sys.float_info.max, float.fromhex("0x0.0000000000001p-1022"), 1.2345678901234567
        raw, negative_zero = Vector(maximum, subnormal, precise), Vector(-0.0, precise, maximum)
        sequence = [negative_zero]
        picked = PftBomObject(self.doc, "ExactVectorFloats", {"Raw": raw, "Points": sequence},
                              {"Raw": "App::PropertyVector", "Points": "App::PropertyVectorList"})
        snapshot = self.read([picked.Name], ["Raw", "Points"])
        expected = [{"x": maximum, "y": subnormal, "z": precise}, {"x": -0.0, "y": precise, "z": maximum}]
        before = list(picked.bom_reads)
        raw.x, raw.y, raw.z, negative_zero.x = 0.0, 0.0, 0.0, 1.0
        sequence.clear()
        picked.bom_values = {name: AssertionError("frozen float DTO must not reread") for name in picked.bom_values}
        roundtrip = json.loads(json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
        for copy in (snapshot, roundtrip):
            fields = copy["properties"][0]["fields"]
            self.assertEqual([field["status"] for field in fields], ["available", "available"])
            for actual, original in zip((fields[0]["value"], fields[1]["value"][0]), expected):
                for axis in ("x", "y", "z"):
                    self.assertIs(type(actual[axis]), float)
                    self.assertEqual(actual[axis].hex(), original[axis].hex())
        text, markdown = tools.format_information(snapshot), tools.format_markdown(snapshot)
        for original in expected:
            exact = json.dumps(original, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            self.assertIn(exact, text)
            self.assertIn(tools.escape_markdown(exact), markdown)
        self.assertIn('"x":-0.0', text)
        self.assertEqual(picked.bom_reads, before)

    def test_vector_metadata_and_values_count_toward_existing_aggregate_budget(self):
        names = ["Vector" + str(index) for index in range(20)]
        objects = [PftBomObject(self.doc, "VectorMany" + str(index), {name: Vector() for name in names},
                   {name: "App::PropertyVector" for name in names}) for index in range(20)]
        with self.assertRaises(tools.PftDocumentToolsError) as error:
            self.read([obj.Name for obj in objects], names)
        self.assertEqual(error.exception.code, "BOM_PROPERTIES_TOO_LARGE")

    def test_explicit_rotation_and_placement_preserve_raw_values_and_component_units(self):
        rotation = Rotation((0.0, -0.5, 0.0, -0.8660254037844386))
        placement = Placement(Vector(0.0, -12.5, 3.25), Rotation())
        picked = PftBomObject(self.doc, "NativeTransforms", {"Orientation": rotation, "Placement": placement},
                              {"Orientation": "App::PropertyRotation", "Placement": "App::PropertyPlacement"})
        fields = {field["name"]: field for field in self.read([picked.Name], ["Orientation", "Placement"])["properties"][0]["fields"]}
        orientation, position = fields["Orientation"], fields["Placement"]
        self.assertEqual(orientation["kind"], "rotation")
        self.assertEqual(orientation["value"], {"x": 0.0, "y": -0.5, "z": 0.0, "w": -0.8660254037844386})
        self.assertEqual(orientation["representation"], "quaternion_xyzw")
        self.assertEqual(orientation["unit"], "1")
        self.assertEqual(orientation["unit_signature"], [0] * 8)
        self.assertEqual(orientation["unit_system"], "freecad_internal")
        self.assertEqual(position["kind"], "placement")
        self.assertEqual(position["value"], {"base": {"x": 0.0, "y": -12.5, "z": 3.25},
                         "rotation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}})
        self.assertEqual(position["representation"], "base_xyz_rotation_quaternion_xyzw")
        self.assertIsNone(position["unit"])
        self.assertIsNone(position["unit_signature"])
        self.assertEqual(position["unit_system"], "mixed_components")
        self.assertEqual(position["component_units"], {
            "base": {"unit": "mm", "unit_signature": [1, 0, 0, 0, 0, 0, 0, 0], "unit_system": "freecad_internal"},
            "rotation": {"unit": "1", "unit_signature": [0] * 8, "unit_system": "freecad_internal"}})
        for field in fields.values():
            self.assertEqual(field["status"], "available")
            self.assertEqual(field["unit_source"], "native_property_type")
            self.assertEqual(field["coordinate_scope"], "reported_property_value")
            self.assertEqual(field["coordinate_frame"], "not_declared_by_property_type")
            self.assertFalse(field["placement_applied_by_tool"])
            self.assertFalse(field["link_source_resolved"])
            self.assertEqual(field["quaternion_normalization"], "not_checked")
            self.assertFalse(field["quaternion_normalized_by_tool"])
            self.assertFalse(field["truncated"])
        self.assertEqual(rotation.accesses, ["Q"])
        self.assertEqual(placement.accesses, ["Base", "Rotation"])
        self.assertEqual(placement.rotation_value.accesses, ["Q"])
        self.assertEqual(self.gui.Selection.records, [])
        self.assertIs(self.app.ActiveDocument, self.doc)

    def test_rotation_retains_identity_negative_identity_and_nonunit_raw_values_without_normalizing(self):
        quaternions = ((0.0, 0.0, 0.0, 1.0), (-0.0, 0.0, 0.0, -1.0),
                       (2.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0))
        values = {"Orientation" + str(index): Rotation(q) for index, q in enumerate(quaternions)}
        picked = PftBomObject(self.doc, "QuaternionIdentityStates", values,
                              {name: "App::PropertyRotation" for name in values})
        fields = self.read([picked.Name], list(values))["properties"][0]["fields"]
        for field, expected in zip(fields[:3], quaternions[:3]):
            self.assertEqual(field["status"], "available")
            self.assertEqual([field["value"][axis].hex() for axis in ("x", "y", "z", "w")],
                             [number.hex() for number in expected])
        self.assertEqual(fields[3]["status"], "unknown")
        self.assertIsNone(fields[3]["value"])
        self.assertNotIn("isIdentity", json.dumps(fields))
        zero_placement = PftBomObject(self.doc, "ZeroPlacement", {"Placement": Placement()},
                                      {"Placement": "App::PropertyPlacement"})
        field = self.read([zero_placement.Name], ["Placement"])["properties"][0]["fields"][0]
        self.assertEqual(field["status"], "available")
        self.assertEqual(field["value"]["base"], {"x": 0.0, "y": 0.0, "z": 0.0})
        self.assertEqual(field["value"]["rotation"], {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0})

    def test_rotation_rejects_malformed_and_nonfinite_quaternions_without_coercion(self):
        candidates = [None, [], [0.0, 0.0, 0.0, 1.0], (), (0.0,) * 3, (0.0,) * 5,
                      (False, 0.0, 0.0, 1.0), (0, 0.0, 0.0, 1.0), ("0", 0.0, 0.0, 1.0),
                      (math.nan, 0.0, 0.0, 1.0), (0.0, math.inf, 0.0, 1.0),
                      (0.0, 0.0, -math.inf, 1.0), (0.0, 0.0, 0.0, math.nan),
                      PftOpaqueValue(), {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}]
        for index, candidate in enumerate(candidates):
            with self.subTest(index=index):
                picked = PftBomObject(self.doc, "InvalidQuaternion" + str(index), {"Orientation": Rotation(candidate)},
                                      {"Orientation": "App::PropertyRotation"})
                field = self.read([picked.Name], ["Orientation"])["properties"][0]["fields"][0]
                self.assertEqual(field["status"], "unknown")
                self.assertIsNone(field["value"])
                self.assertFalse(field["truncated"])
        accesses = []
        class CustomTuple(tuple):
            def __len__(self): accesses.append("len"); raise AssertionError("must not inspect a tuple subclass")
            def __iter__(self): accesses.append("iter"); raise AssertionError("must not iterate a tuple subclass")
        picked = PftBomObject(self.doc, "OpaqueQuaternion", {"Orientation": Rotation(CustomTuple((0.0, 0.0, 0.0, 1.0)))},
                              {"Orientation": "App::PropertyRotation"})
        self.assertEqual(self.read([picked.Name], ["Orientation"])["properties"][0]["fields"][0]["status"], "unknown")
        self.assertEqual(accesses, [])

    def test_transform_values_require_exact_native_base_type_names_without_reading_custom_members(self):
        class CustomRotation(Rotation): pass
        class CustomPlacement(Placement): pass
        fake_rotation = type("Rotation", (), {"Q": property(lambda _: self.fail("wrong module must not read Q"))})()
        fake_placement = type("Placement", (), {"Base": property(lambda _: self.fail("wrong module must not read Base"))})()
        candidates = [(CustomRotation(), "App::PropertyRotation"), (fake_rotation, "App::PropertyRotation"),
                      ((0.0, 0.0, 0.0, 1.0), "App::PropertyRotation"),
                      (CustomPlacement(), "App::PropertyPlacement"), (fake_placement, "App::PropertyPlacement"),
                      ({"base": Vector(), "rotation": Rotation()}, "App::PropertyPlacement")]
        for index, (value, native_type) in enumerate(candidates):
            with self.subTest(index=index):
                picked = PftBomObject(self.doc, "WrongTransformType" + str(index), {"Transform": value}, {"Transform": native_type})
                field = self.read([picked.Name], ["Transform"])["properties"][0]["fields"][0]
                self.assertEqual(field["status"], "unknown")
                self.assertIsNone(field["value"])
                if hasattr(value, "accesses"):
                    self.assertEqual(value.accesses, [])

    def test_transform_nested_getter_absence_and_failure_keep_unknown_and_read_failed_distinct(self):
        def absent(_): raise AttributeError("synthetic unavailable transform member")
        def failed(_): raise RuntimeError("synthetic hidden transform detail")
        def broken_vector():
            return type("Vector", (), {"__module__": "Base", "x": 0.0, "y": property(failed)})()
        cases = [(Rotation(AttributeError("synthetic unavailable Q")), "App::PropertyRotation", "unknown"),
                 (Rotation(RuntimeError("synthetic hidden transform detail")), "App::PropertyRotation", "read_failed"),
                 (type("Rotation", (), {"__module__": "Base", "Q": property(absent)})(), "App::PropertyRotation", "unknown"),
                 (Placement(AttributeError("synthetic unavailable Base")), "App::PropertyPlacement", "unknown"),
                 (Placement(RuntimeError("synthetic hidden transform detail")), "App::PropertyPlacement", "read_failed"),
                 (Placement(broken_vector()), "App::PropertyPlacement", "read_failed"),
                 (Placement(Vector(), RuntimeError("synthetic hidden transform detail")), "App::PropertyPlacement", "read_failed"),
                 (Placement(Vector(), Rotation(RuntimeError("synthetic hidden transform detail"))), "App::PropertyPlacement", "read_failed"),
                 (Placement(Vector(), Rotation(AttributeError("synthetic unavailable Q"))), "App::PropertyPlacement", "unknown")]
        for index, (value, native_type, expected) in enumerate(cases):
            with self.subTest(index=index):
                picked = PftBomObject(self.doc, "TransformGetter" + str(index), {"Transform": value}, {"Transform": native_type})
                row = self.read([picked.Name], ["Transform"])["properties"][0]
                self.assertEqual(row["fields"][0]["status"], expected)
                self.assertIsNone(row["fields"][0]["value"])
                self.assertEqual(row["status"], "read_failed" if expected == "read_failed" else "available")
                self.assertNotIn("synthetic hidden", json.dumps(row))
                if isinstance(value, Placement) and value.accesses == ["Base"]:
                    self.assertEqual(value.rotation_value.accesses, [])
        values = {"MissingValue": None, "GetterFailed": RuntimeError("synthetic hidden transform detail")}
        picked = PftBomObject(self.doc, "TopLevelTransformStates", values, {name: "App::PropertyPlacement" for name in values})
        fields = self.read([picked.Name], [*values, "Absent"])["properties"][0]["fields"]
        self.assertEqual([field["status"] for field in fields], ["unknown", "read_failed", "missing"])

    def test_invalid_placement_component_never_publishes_partial_base_or_reads_after_failure(self):
        cases = [Placement(None), Placement(Vector(math.nan)), Placement(Vector(1)),
                 Placement(Vector(), None), Placement(Vector(), Rotation((0.0, 0.0, 0.0, 0.0)))]
        for index, value in enumerate(cases):
            with self.subTest(index=index):
                picked = PftBomObject(self.doc, "IncompletePlacement" + str(index), {"Placement": value},
                                      {"Placement": "App::PropertyPlacement"})
                field = self.read([picked.Name], ["Placement"])["properties"][0]["fields"][0]
                self.assertEqual(field["status"], "unknown")
                self.assertIsNone(field["value"])
                if index < 3:
                    self.assertEqual(value.accesses, ["Base"])
                    self.assertEqual(value.rotation_value.accesses, [])

    def test_transforms_are_explicit_only_and_never_fetch_unsupported_families_or_other_targets(self):
        values = {"PartNumber": Rotation(), "Orientation": Rotation(), "Placement": Placement(),
                  "Placements": [Placement()],
                  "Matrix": AssertionError("must not read matrices"), "Reference": AssertionError("must not read links")}
        kinds = {"PartNumber": "App::PropertyRotation", "Orientation": "App::PropertyRotation", "Placement": "App::PropertyPlacement",
                 "Placements": "App::PropertyPlacementList", "Matrix": "App::PropertyMatrix", "Reference": "App::PropertyLink"}
        picked = PftBomObject(self.doc, "ExplicitTransformScope", values, kinds, kind="App::Link")
        other = PftBomObject(self.doc, "UnselectedTransform", {"Placement": AssertionError("must not read another object")},
                             {"Placement": "App::PropertyPlacement"})
        row = self.read([picked.Name])["properties"][0]
        self.assertEqual([field["name"] for field in row["fields"]], [name for name, _ in tools.BOM_FIELDS])
        self.assertEqual(row["fields"][0]["status"], "unsupported")
        self.assertEqual(picked.bom_reads, [])
        self.assertEqual(self.read([picked.Name], [])["properties"], [])
        fields = self.read([picked.Name], ["Orientation", "Placement", "Placements", "Matrix", "Reference"])["properties"][0]["fields"]
        self.assertEqual([field["status"] for field in fields], ["available", "available", "available", "unsupported", "unsupported"])
        self.assertEqual(picked.bom_reads, ["Orientation", "Placement", "Placements"])
        self.assertEqual(other.bom_reads, [])
        self.assertEqual(values["PartNumber"].accesses, [])
        self.assertEqual(self.read([], ["Placement"])["properties"], [])
        self.assertEqual(picked.bom_reads, ["Orientation", "Placement", "Placements"])
        self.assertEqual(self.gui.Selection.records, [])
        self.assertIs(self.app.ActiveDocument, self.doc)

    def test_transform_dtos_and_renderers_freeze_float_precision_without_native_rereads(self):
        maximum = sys.float_info.max
        subnormal, precise = float.fromhex("0x0.0000000000001p-1022"), 1.2345678901234567
        expected_q = {"x": maximum, "y": subnormal, "z": precise, "w": -0.0}
        expected_base = {"x": -0.0, "y": precise, "z": maximum}
        rotation = Rotation(tuple(expected_q.values()))
        placement = Placement(Vector(*expected_base.values()), Rotation(tuple(expected_q.values())))
        picked = PftBomObject(self.doc, "ExactTransformFloats", {"Orientation": rotation, "Placement": placement},
                              {"Orientation": "App::PropertyRotation", "Placement": "App::PropertyPlacement"})
        snapshot = self.read([picked.Name], ["Orientation", "Placement"])
        before_reads = list(picked.bom_reads)
        before_accesses = (list(rotation.accesses), list(placement.accesses), list(placement.rotation_value.accesses))
        rotation.quaternion = (0.0, 0.0, 0.0, 1.0)
        placement.base_value.x = 99.0
        placement.rotation_value.quaternion = (0.0, 0.0, 0.0, 1.0)
        picked.bom_values = {name: AssertionError("formatters must not reread transforms") for name in picked.bom_values}
        roundtrip = json.loads(json.dumps(snapshot, allow_nan=False))
        for frozen in (snapshot, roundtrip):
            fields = frozen["properties"][0]["fields"]
            self.assertEqual([field["status"] for field in fields], ["available", "available"])
            for actual, expected in ((fields[0]["value"], expected_q), (fields[1]["value"]["base"], expected_base),
                                     (fields[1]["value"]["rotation"], expected_q)):
                self.assertEqual({key: number.hex() for key, number in actual.items()},
                                 {key: number.hex() for key, number in expected.items()})
        text, markdown = tools.format_information(snapshot), tools.format_markdown(snapshot)
        for field in snapshot["properties"][0]["fields"]:
            self.assertIn(tools.escape_markdown(tools._property_display(field, lambda value: value)), markdown)
        self.assertIn(json.dumps(expected_q, separators=(",", ":")), text)
        self.assertIn(json.dumps(expected_base, separators=(",", ":")), text)
        self.assertIn('"component_units":{"base":{"unit":"mm"', text)
        self.assertIn('"rotation":{"unit":"1"', text)
        self.assertIn('"quaternion_normalization":"not_checked"', text)
        self.assertNotIn(self.doc.FileName, markdown)
        self.assertNotIn('99.0', text + markdown)
        self.assertEqual(picked.bom_reads, before_reads)
        self.assertEqual((rotation.accesses, placement.accesses, placement.rotation_value.accesses), before_accesses)
        self.assertIn("60 | 40 | 8", tools.format_dimensions(snapshot))

    def test_complete_transform_json_and_metadata_respect_existing_field_and_aggregate_budgets(self):
        for kind, value in (("rotation", Rotation()), ("placement", Placement())):
            with self.subTest(kind=kind):
                complete = tools._property_transform(value, kind)
                size = len(json.dumps(complete["value"], separators=(",", ":")).encode("utf-8"))
                with patch.object(tools, "PFT_MAX_PROPERTY_BYTES", size):
                    self.assertEqual(tools._property_transform(value, kind), complete)
                with patch.object(tools, "PFT_MAX_PROPERTY_BYTES", size - 1):
                    self.assertEqual(tools._property_transform(value, kind), {
                        "value": None, "status": "unknown", "truncated": True, "limit_reason": "utf8_budget"})
        names = ["Placement" + str(index) for index in range(20)]
        objects = [PftBomObject(self.doc, "TransformBudget" + str(index), {name: Placement() for name in names},
                               {name: "App::PropertyPlacement" for name in names}) for index in range(20)]
        with self.assertRaises(tools.PftDocumentToolsError) as error:
            self.read([obj.Name for obj in objects], names)
        self.assertEqual(error.exception.code, "BOM_PROPERTIES_TOO_LARGE")

    def test_placement_lists_preserve_order_duplicates_units_empty_and_frozen_formatters(self):
        first = Placement(Vector(-0.0, 12.5, -3.25), Rotation((0.0, -0.5, 0.0, -0.8660254037844386)))
        values = [first, Placement(), first]
        picked = PftBomObject(self.doc, "PlacementSequence", {"Positions": values, "Empty": []},
                              {name: "App::PropertyPlacementList" for name in ("Positions", "Empty")})
        snapshot = self.read([picked.Name], ["Positions", "Empty"])
        fields = snapshot["properties"][0]["fields"]
        field = fields[0]
        self.assertEqual((field["kind"], field["status"], field["item_count"]), ("placement_list", "available", 3))
        self.assertEqual(field["value"][0], field["value"][2])
        self.assertEqual(field["value"][1]["base"], {"x": 0.0, "y": 0.0, "z": 0.0})
        self.assertEqual(field["component_units"]["base"]["unit"], "mm")
        self.assertEqual(field["component_units"]["rotation"]["unit"], "1")
        self.assertEqual(field["unit_system"], "mixed_components")
        self.assertFalse(field["placement_applied_by_tool"])
        self.assertFalse(field["link_source_resolved"])
        self.assertEqual(field["quaternion_normalization"], "not_checked")
        self.assertEqual((fields[1]["status"], fields[1]["value"], fields[1]["item_count"]), ("empty", [], 0))
        frozen = json.loads(json.dumps(snapshot, allow_nan=False))
        reads = list(picked.bom_reads)
        first.base_value.y = 99.0
        first.rotation_value.quaternion = (0.0, 0.0, 0.0, 1.0)
        values[:] = []
        picked.bom_values = {name: AssertionError("must not reread list") for name in picked.bom_values}
        for current in (snapshot, frozen):
            text, markdown = tools.format_information(current), tools.format_markdown(current)
            self.assertIn('"value":[{"base":{"x":-0.0,"y":12.5,"z":-3.25}', text)
            self.assertIn('"component_units":{"base":{"unit":"mm"', text)
            self.assertIn('"rotation":{"unit":"1"', text)
            self.assertIn(tools.escape_markdown(tools._property_display(field, lambda value: value)), markdown)
            self.assertNotIn('99.0', text + markdown)
        self.assertEqual(picked.bom_reads, reads)

    def test_bad_placement_list_member_does_not_publish_a_valid_prefix(self):
        bad = [object(), Placement(None), Placement(Vector(), Rotation((0.0, 0.0, 0.0, 0.0))),
               Placement(Vector(), Rotation(RuntimeError("synthetic private failure"))),
               Placement(AttributeError("unavailable Base"))]
        for index, value in enumerate(bad):
            with self.subTest(index=index):
                picked = PftBomObject(self.doc, "BrokenPlacementList" + str(index), {"Positions": [Placement(), value]},
                                      {"Positions": "App::PropertyPlacementList"})
                field = self.read([picked.Name], ["Positions"])["properties"][0]["fields"][0]
                self.assertEqual(field["status"], "read_failed" if index == 3 else "unknown")
                self.assertIsNone(field["value"])
                self.assertEqual(field["item_count"], 2)
                self.assertFalse(field["truncated"])
                self.assertNotIn("synthetic private", str(field))

    def test_placement_list_container_and_limits_are_strict_and_whole(self):
        class OpaqueList(list):
            def __iter__(self): raise AssertionError("must not iterate non-native list")
        for value in ((), OpaqueList(), "[]", {"base": []}):
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(tools._property_placement_list(value), {"value": None, "status": "unsupported"})
        self.assertEqual(tools._property_placement_list([PftOpaqueValue()] * 101), {
            "value": None, "status": "unknown", "item_count": 101, "truncated": True, "limit_reason": "item_limit"})
        values = [Placement(), Placement(Vector(1.2345678901234567, -0.0, sys.float_info.max))]
        expected = tools._property_placement_list(values)
        size = len(json.dumps(expected["value"], ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))
        with patch.object(tools, "PFT_MAX_PROPERTY_BYTES", size):
            self.assertEqual(tools._property_placement_list(values), expected)
        with patch.object(tools, "PFT_MAX_PROPERTY_BYTES", size - 1):
            self.assertEqual(tools._property_placement_list(values), {
                "value": None, "status": "unknown", "item_count": 2, "truncated": True, "limit_reason": "utf8_budget"})
        at_count = tools._property_placement_list([Placement()] * 100)
        self.assertEqual(at_count["item_count"], 100)
        self.assertEqual(at_count["limit_reason"], "utf8_budget")
        self.assertIsNone(at_count["value"])

    def test_placement_list_is_explicit_and_aggregate_budget_includes_metadata(self):
        values = {"PartNumber": AssertionError("default does not get placement lists"), "Positions": [Placement()]}
        picked = PftBomObject(self.doc, "ExplicitPlacementList", values,
                              {name: "App::PropertyPlacementList" for name in values}, kind="App::Link")
        default = self.read([picked.Name])
        self.assertEqual(default["properties"][0]["fields"][0]["status"], "unsupported")
        self.assertEqual(picked.bom_reads, [])
        snapshot = self.read([picked.Name], ["Positions"])
        self.assertEqual(picked.bom_reads, ["Positions"])
        self.assertFalse(snapshot["properties"][0]["link_source_resolved"])
        row = snapshot["properties"][0]
        exact = 2 + len(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))
        with patch.object(tools, "PFT_MAX_PROPERTIES_BYTES", exact):
            self.assertEqual(self.read([picked.Name], ["Positions"])["properties"], [row])
        with patch.object(tools, "PFT_MAX_PROPERTIES_BYTES", exact - 1), self.assertRaises(tools.PftDocumentToolsError) as error:
            self.read([picked.Name], ["Positions"])
        self.assertEqual(error.exception.code, "BOM_PROPERTIES_TOO_LARGE")

    def test_list_item_and_utf8_budgets_never_publish_a_partial_value(self):
        values = {"AtCount": [0] * 100, "OverCount": [PftOpaqueValue()] * 101,
                  "AtBytes": ["x" * 2044], "OverBytes": ["x" * 2045],
                  "WideText": ["示" * 683], "Aggregate": ["x" * 1100, "x" * 1100],
                  "Escaped": ["\\" * 1100]}
        kinds = {name: "App::PropertyStringList" for name in values}
        kinds.update(AtCount="App::PropertyIntegerList", OverCount="App::PropertyStringList")
        picked = PftBomObject(self.doc, "BoundedLists", values, kinds)
        fields = {field["name"]: field for field in self.read([picked.Name], list(values))["properties"][0]["fields"]}
        self.assertEqual(fields["AtCount"]["value"], values["AtCount"])
        self.assertEqual(len(json.dumps(fields["AtBytes"]["value"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                         tools.PFT_MAX_PROPERTY_BYTES)
        for name in ("OverCount", "OverBytes", "WideText", "Aggregate", "Escaped"):
            with self.subTest(name=name):
                self.assertEqual(fields[name]["status"], "unknown")
                self.assertIsNone(fields[name]["value"])
                self.assertTrue(fields[name]["truncated"])
                self.assertEqual(fields[name]["item_count"], len(values[name]))
                self.assertEqual(fields[name]["limit_reason"], "item_limit" if name == "OverCount" else "utf8_budget")
        info = tools.format_information(self.read([picked.Name], ["OverCount"]))
        self.assertIn("未知", info)
        self.assertIn("已截断", info)

    def test_list_rendering_uses_frozen_clean_values_and_markdown_escapes_without_more_getters(self):
        tags = ["A\x00B", '![x](file:///neutral) | <script>\nnext', ""]
        picked = PftBomObject(self.doc, "RenderedList", {"Tags": tags}, {"Tags": "App::PropertyStringList"})
        snapshot = self.read([picked.Name], ["Tags"])
        field = snapshot["properties"][0]["fields"][0]
        self.assertEqual(field["value"], ["A B", tags[1], ""])
        self.assertTrue(field["sanitized"])
        before = list(picked.bom_reads)
        tags[0] = "changed after snapshot"; tags.append("later")
        picked.bom_values["Tags"] = AssertionError("formatters must use the snapshot")
        text, markdown = tools.format_information(snapshot), tools.format_markdown(snapshot)
        self.assertIn('"A B"', text)
        self.assertIn("已净化控制字符", text)
        self.assertIn(tools.escape_markdown(tools._property_display(field, lambda value: value)), markdown)
        self.assertIn(r"\| &lt;script&gt;", markdown)
        self.assertNotIn("<script>", markdown)
        self.assertNotIn("changed after snapshot", text + markdown)
        self.assertNotIn("later", text + markdown)
        self.assertNotIn(self.doc.FileName, markdown)
        self.assertEqual(picked.bom_reads, before)
        self.assertIs(self.app.ActiveDocument, self.doc)
        self.assertEqual(self.gui.Selection.records, [])

    def test_list_fields_keep_total_response_budget_and_empty_target_scope(self):
        names = ["Field" + str(index) for index in range(20)]
        objects = [PftBomObject(self.doc, "ListBudget" + str(index), {name: ["x" * 2000] for name in names},
                               {name: "App::PropertyStringList" for name in names}) for index in range(4)]
        self.assertEqual(self.read([], names)["properties"], [])
        self.assertEqual(sum(len(obj.bom_reads) for obj in objects), 0)
        with self.assertRaises(tools.PftDocumentToolsError) as error:
            self.read([obj.Name for obj in objects], names)
        self.assertEqual(error.exception.code, "BOM_PROPERTIES_TOO_LARGE")
        self.assertEqual(self.gui.Selection.records, [])
        self.assertIs(self.app.ActiveDocument, self.doc)


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
    def _replaceable_directory(self, name, existed, ancestor):
        base = self.root / name
        parent = base / "selected" / "nested"
        parent.mkdir(parents=True)
        target = parent / "notes.md"
        replaced = parent.parent if ancestor else parent
        replacement = base / "replacement"
        replacement.mkdir()
        replacement_parent = replacement / "nested" if ancestor else replacement
        replacement_parent.mkdir(exist_ok=True)
        if existed:
            target.write_text("original")
            # Preserve the complete target stamp across the directory replacement:
            # a target-only guard cannot distinguish these two destination paths.
            os.link(target, replacement_parent / target.name)
        plan = tools.prepare_markdown_export(str(target), "# frozen 示例\n")
        saved = base / "original-directory"
        def replace_directory():
            replaced.rename(saved)
            replacement.rename(replaced)
        return base, target, plan, replace_directory, saved
    def test_directory_identity_is_frozen_while_native_confirmation_is_open(self):
        for existed in (False, True):
            for ancestor in (False, True):
                with self.subTest(existed=existed, ancestor=ancestor):
                    base, target, plan, replace_directory, saved = self._replaceable_directory(
                        "confirm-%s-%s" % (existed, ancestor), existed, ancestor)
                    replace_directory()
                    # Even an unchanged existing inode is insufficient: the chosen
                    # parent or ancestor must still be the one that was confirmed.
                    self.assertEqual(tools._stamp(target), plan["_stamp"])
                    with patch.object(tools.tempfile, "mkstemp") as stage, \
                            patch.object(tools.os, "replace") as replace, patch.object(tools.os, "link") as link:
                        with self.assertRaises(tools.PftDocumentToolsError) as caught:
                            tools.write_markdown_export(plan, overwrite=existed)
                        self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
                        stage.assert_not_called(); replace.assert_not_called(); link.assert_not_called()
                    self.assertEqual(target.exists(), existed)
                    if existed:
                        self.assertEqual(target.read_text(), "original")
                        self.assertEqual(next(saved.rglob("notes.md")).read_text(), "original")
                    self.assertEqual(list(base.rglob(".pft-markdown-*")), [])
    def test_directory_identity_is_rechecked_before_publication_without_retry(self):
        if os.unlink not in os.supports_dir_fd:
            self.skipTest("directory descriptor cleanup is unavailable on this platform")
        for existed in (False, True):
            for ancestor in (False, True):
                with self.subTest(existed=existed, ancestor=ancestor):
                    base, target, plan, replace_directory, saved = self._replaceable_directory(
                        "publish-%s-%s" % (existed, ancestor), existed, ancestor)
                    original_fsync = os.fsync
                    original_stage = tools._create_markdown_stage
                    def replace_after_flush(descriptor):
                        original_fsync(descriptor)
                        replace_directory()
                    with patch.object(tools, "_create_markdown_stage", wraps=original_stage) as stage, \
                            patch.object(tools.os, "fsync", side_effect=replace_after_flush) as flush, \
                            patch.object(tools.os, "replace") as replace, patch.object(tools.os, "link") as link:
                        with self.assertRaises(tools.PftDocumentToolsError) as caught:
                            tools.write_markdown_export(plan, overwrite=existed)
                        self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
                        self.assertEqual(stage.call_count, 1)
                        self.assertEqual(flush.call_count, 1)
                        replace.assert_not_called(); link.assert_not_called()
                    self.assertEqual(target.exists(), existed)
                    if existed:
                        self.assertEqual(target.read_text(), "original")
                        self.assertEqual(next(saved.rglob("notes.md")).read_text(), "original")
                    self.assertEqual(list(base.rglob(".pft-markdown-*")), [])
    def test_directory_changed_during_stage_creation_is_refused_and_own_stage_cleaned(self):
        base, target, plan, replace_directory, saved = self._replaceable_directory(
            "stage-creation", False, False)
        original_stage = tools._create_markdown_stage
        def stage_in_replacement(*args, **kwargs):
            replace_directory()
            return original_stage(*args, **kwargs)
        with patch.object(tools, "_create_markdown_stage", side_effect=stage_in_replacement) as stage, \
                patch.object(tools.os, "replace") as replace, patch.object(tools.os, "link") as link:
            with self.assertRaises(tools.PftDocumentToolsError) as caught:
                tools.write_markdown_export(plan)
            self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
            self.assertEqual(stage.call_count, 1)
            replace.assert_not_called(); link.assert_not_called()
        self.assertFalse(target.exists())
        self.assertEqual(list(base.rglob(".pft-markdown-*")), [])
    def test_directory_swap_inside_publish_stays_in_original_namespace_and_is_not_success(self):
        if not tools._markdown_directory_fd_supported():
            self.skipTest("directory-relative publication is unavailable on this platform")
        for existed in (False, True):
            for ancestor in (False, True):
                with self.subTest(existed=existed, ancestor=ancestor):
                    base, target, plan, replace_directory, saved = self._replaceable_directory(
                        "late-publish-%s-%s" % (existed, ancestor), existed, ancestor)
                    method = "replace" if existed else "link"
                    original_publish = getattr(os, method)
                    def publish_after_swap(source, destination, **kwargs):
                        self.assertIsInstance(kwargs.get("src_dir_fd"), int)
                        self.assertEqual(kwargs["src_dir_fd"], kwargs.get("dst_dir_fd"))
                        replace_directory()
                        return original_publish(source, destination, **kwargs)
                    with patch.object(tools.os, method, side_effect=publish_after_swap) as publish:
                        with self.assertRaises(tools.PftDocumentToolsError) as caught:
                            tools.write_markdown_export(plan, overwrite=existed)
                        self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
                        self.assertEqual(publish.call_count, 1)
                    self.assertEqual(target.exists(), existed)
                    if existed:
                        self.assertEqual(target.read_text(), "original")
                    # Publication might already have happened in the confirmed,
                    # renamed directory. It must never target its replacement.
                    self.assertEqual(next(saved.rglob("notes.md")).read_text(), plan["text"])
                    self.assertEqual(list(base.rglob(".pft-markdown-*")), [])
    def test_replaced_stage_inode_is_not_published_or_deleted_as_our_stage(self):
        plan = tools.prepare_markdown_export(str(self.target), "frozen export")
        original_fsync = os.fsync
        foreign_stage = []
        def replace_own_stage(descriptor):
            original_fsync(descriptor)
            stage = next(self.root.glob(".pft-markdown-*"))
            stage.rename(self.root / "moved-owned-stage")
            stage.write_text("foreign bytes")
            foreign_stage.append(stage)
        with patch.object(tools.os, "fsync", side_effect=replace_own_stage), \
                patch.object(tools.os, "link") as publish:
            with self.assertRaises(tools.PftDocumentToolsError) as caught:
                tools.write_markdown_export(plan)
            self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
            publish.assert_not_called()
        self.assertFalse(self.target.exists())
        self.assertEqual(foreign_stage[0].read_text(), "foreign bytes")
        self.assertEqual((self.root / "moved-owned-stage").read_text(), "frozen export")
    def test_platform_without_directory_fd_keeps_atomic_export_and_cleanup(self):
        plan = tools.prepare_markdown_export(str(self.target), "# frozen 示例\n")
        with patch.object(tools.os, "supports_dir_fd", set()):
            self.assertEqual(tools.write_markdown_export(plan), str(self.target))
        self.assertEqual(self.target.read_text(), "# frozen 示例\n")
        self.assert_no_stage()
    def test_directory_mode_changed_after_confirmation_is_refused(self):
        parent = self.root / "selected"
        parent.mkdir(mode=0o700)
        target = parent / "notes.md"
        plan = tools.prepare_markdown_export(str(target), "new")
        parent.chmod(0o755)
        with patch.object(tools.tempfile, "mkstemp") as stage:
            with self.assertRaises(tools.PftDocumentToolsError) as caught:
                tools.write_markdown_export(plan)
            self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
            stage.assert_not_called()
        self.assertFalse(target.exists())
    def test_plan_without_directory_identity_is_not_reconstructed_or_published(self):
        plan = tools.prepare_markdown_export(str(self.target), "new")
        plan.pop("_directories")
        with patch.object(tools.tempfile, "mkstemp") as stage:
            with self.assertRaises(tools.PftDocumentToolsError) as caught:
                tools.write_markdown_export(plan)
            self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
            stage.assert_not_called()
        self.assertFalse(self.target.exists())
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
        def race(source, destination, **kwargs):
            self.target.write_text("user created during publication")
            original_link(source, destination, **kwargs)
        with patch.object(tools.os, "link", side_effect=race):
            with self.assertRaises(tools.PftDocumentToolsError): tools.write_markdown_export(plan)
        self.assertEqual(self.target.read_text(), "user created during publication")
        self.assert_no_stage()


    def test_markdown_frozen_text_bytes_and_digest_must_remain_consistent(self):
        for key, value in (("text", "other text"), ("data", b"other bytes"), ("digest", "0" * 64)):
            with self.subTest(key=key):
                plan = tools.prepare_markdown_export(str(self.target), "frozen text")
                plan[key] = value
                with patch.object(tools, "_create_markdown_stage") as stage:
                    with self.assertRaises(tools.PftDocumentToolsError) as caught:
                        tools.write_markdown_export(plan)
                    self.assertEqual(caught.exception.code, "INVALID_MARKDOWN")
                    stage.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_markdown_same_inode_stage_mutation_is_not_published(self):
        plan = tools.prepare_markdown_export(str(self.target), "frozen text")
        original_fsync = os.fsync
        def rewrite_stage(descriptor):
            original_fsync(descriptor)
            next(self.root.glob(".pft-markdown-*")).write_text("changed in place")
        with patch.object(tools.os, "fsync", side_effect=rewrite_stage), patch.object(tools.os, "link") as link:
            with self.assertRaises(tools.PftDocumentToolsError) as caught:
                tools.write_markdown_export(plan)
            link.assert_not_called()
        self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
        self.assertEqual((caught.exception.publication_state, caught.exception.cleanup_state),
                         ("not_published", "complete"))
        self.assertFalse(self.target.exists())
        self.assert_no_stage()

    def test_markdown_foreign_stage_published_inside_link_is_unknown_and_not_rolled_back(self):
        plan = tools.prepare_markdown_export(str(self.target), "frozen text")
        original_link = os.link
        moved = self.root / "moved-own-stage"
        foreign = []
        def publish_foreign(source, destination, **kwargs):
            stage = self.root / Path(source).name
            stage.rename(moved)
            stage.write_text("foreign replacement")
            foreign.append(stage)
            return original_link(source, destination, **kwargs)
        with patch.object(tools.os, "link", side_effect=publish_foreign) as link:
            with self.assertRaises(tools.PftDocumentToolsError) as caught:
                tools.write_markdown_export(plan)
        self.assertEqual(link.call_count, 1)
        self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
        self.assertEqual((caught.exception.publication_state, caught.exception.cleanup_state), ("unknown", "unknown"))
        self.assertIn("可能已写出", caught.exception.message)
        self.assertIn("不要重复导出", caught.exception.message)
        self.assertEqual(self.target.read_text(), "foreign replacement")
        self.assertEqual(foreign[0].read_text(), "foreign replacement")
        self.assertEqual(moved.read_text(), "frozen text")

    def test_markdown_final_bytes_recheck_detects_mutation_after_link(self):
        plan = tools.prepare_markdown_export(str(self.target), "frozen text")
        original_link = os.link
        def rewrite_final(source, destination, **kwargs):
            original_link(source, destination, **kwargs)
            self.target.write_text("independent final revision")
        with patch.object(tools.os, "link", side_effect=rewrite_final) as link:
            with self.assertRaises(tools.PftDocumentToolsError) as caught:
                tools.write_markdown_export(plan)
        self.assertEqual(link.call_count, 1)
        self.assertEqual(caught.exception.publication_state, "unknown")
        self.assertEqual(self.target.read_text(), "independent final revision")
        self.assert_no_stage()

    def test_markdown_verified_publish_with_cleanup_failure_preserves_published_outcome(self):
        plan = tools.prepare_markdown_export(str(self.target), "frozen text")
        original_unlink = os.unlink
        def fail_cleanup(name, **kwargs):
            if Path(name).name.startswith(".pft-markdown-"):
                raise OSError("synthetic cleanup detail")
            return original_unlink(name, **kwargs)
        with patch.object(tools.os, "unlink", side_effect=fail_cleanup), \
                patch.object(tools.os, "link", wraps=os.link) as link:
            with self.assertRaises(tools.PftDocumentToolsError) as caught:
                tools.write_markdown_export(plan)
        self.assertEqual(link.call_count, 1)
        self.assertEqual(caught.exception.code, "EXPORT_FAILED")
        self.assertEqual((caught.exception.publication_state, caught.exception.cleanup_state), ("published", "unknown"))
        self.assertIn("已发布", caught.exception.message)
        self.assertIn("不要重复导出", caught.exception.message)
        self.assertNotIn("synthetic cleanup detail", caught.exception.message)
        self.assertEqual(self.target.read_text(), "frozen text")

    def test_markdown_publish_then_raise_keeps_existing_error_class_and_uncertain_outcome(self):
        self.target.write_text("original")
        plan = tools.prepare_markdown_export(str(self.target), "frozen text")
        original_replace = os.replace
        def publish_then_raise(source, destination, **kwargs):
            original_replace(source, destination, **kwargs)
            raise OSError("synthetic native error")
        with patch.object(tools.os, "replace", side_effect=publish_then_raise) as replace:
            with self.assertRaises(tools.PftDocumentToolsError) as caught:
                tools.write_markdown_export(plan, overwrite=True)
        self.assertEqual(replace.call_count, 1)
        self.assertEqual(caught.exception.code, "EXPORT_FAILED")
        self.assertEqual(caught.exception.publication_state, "unknown")
        self.assertIn("不要重复导出", caught.exception.message)
        self.assertNotIn("synthetic native error", caught.exception.message)
        self.assertEqual(self.target.read_text(), "frozen text")
        self.assert_no_stage()


if __name__ == "__main__": unittest.main()
