# SPDX-License-Identifier: Apache-2.0
"""Neutral native-shaped unit display fixtures; no FreeCAD, settings or CAD I/O."""
import json
import math
import types
import unittest
from unittest.mock import patch

import test_pft_document_tools as fixture

tools = fixture.tools


class DisplayQuantity(fixture.Quantity):
    __module__ = "Base"

    def __init__(self, value, signature=(1, 0, 0, 0, 0, 0, 0, 0), preferred=("1 in", 25.4, "in")):
        super().__init__(value, signature)
        self.preferred, self.display_gets, self.display_calls = preferred, 0, 0
        self.getter_error = None

    def __getattribute__(self, name):
        if name == "getUserPreferred":
            self.display_gets += 1
            if self.getter_error is not None:
                raise self.getter_error
        return super().__getattribute__(name)

    def getUserPreferred(self):
        self.display_calls += 1
        if isinstance(self.preferred, Exception):
            raise self.preferred
        return self.preferred


DisplayQuantity.__name__ = "Quantity"


class DisplayUnits:
    def __init__(self):
        self.calls, self.quantities = [], []
        self.responses = {60.0: ("2.362 in", 25.4, "in"), 40.0: ("1.575 in", 25.4, "in"),
                          8.0: ("0.315 in", 25.4, "in"), 0.0: ("0", 25.4, "in")}

    def Quantity(self, number, unit):
        self.calls.append((number, unit))
        preferred = self.responses[number]
        if isinstance(preferred, Exception):
            raise preferred
        result = DisplayQuantity(number, preferred=preferred)
        self.quantities.append(result)
        return result

    def setSchema(self, *_):
        raise AssertionError("must not alter global units")


class DocumentUserUnitsTests(unittest.TestCase):
    def setUp(self):
        self.doc = fixture.PftDoc()
        self.app, self.gui = fixture.PftApp(self.doc), fixture.PftGui(self.doc)
        self.units = DisplayUnits()
        self.app.Units = self.units
        self.quantity = DisplayQuantity(25.4)
        self.obj = fixture.PftBomObject(self.doc, "Sample", {"Length": self.quantity},
                                       {"Length": "App::PropertyLength"})

    def read(self, enabled=True, fields=("Length",), targets=None):
        return tools.read_document_tools(self.app, self.gui, target_names=[self.obj.Name] if targets is None else targets,
                                         property_names=list(fields), use_user_units=enabled)

    def test_default_mode_keeps_internal_values_and_never_accesses_display_apis(self):
        self.quantity.getter_error = AssertionError("display getter forbidden in default mode")
        self.app.Units = types.SimpleNamespace(Quantity=lambda *_: self.fail("must not construct display quantities"))
        snapshot = self.read(False)
        self.assertIs(snapshot["use_user_units"], False)
        self.assertNotIn("display_dimensions", snapshot["dimensions"][0])
        field = snapshot["properties"][0]["fields"][0]
        self.assertNotIn("display_quantity", field)
        self.assertEqual(field["value"]["value"], 25.4)
        self.assertEqual(self.quantity.display_gets, 0)
        self.assertEqual(self.obj.reads, 1)
        self.assertIn("60 | 40 | 8", tools.format_dimensions(snapshot))
        self.assertIn("25.4 mm", tools.format_information(snapshot))

    def test_option_requires_exact_bool_even_without_an_open_document(self):
        for value in (None, 0, 1, "true", [], {}):
            with self.subTest(value=value), self.assertRaises(tools.PftDocumentToolsError) as caught:
                tools.read_document_tools(fixture.PftApp(None), self.gui, use_user_units=value)
            self.assertEqual(caught.exception.code, "INVALID_UNIT_DISPLAY")
        for value in (False, True):
            self.assertIs(tools.read_document_tools(fixture.PftApp(None), self.gui, use_user_units=value)["use_user_units"], value)

    def test_dimensions_and_property_freeze_native_text_factor_and_unit_without_changing_raw_values(self):
        snapshot = self.read()
        field = snapshot["properties"][0]["fields"][0]
        self.assertEqual(field["value"], {"kind": "quantity", "value": 25.4, "unit": "mm",
            "unit_signature": [1, 0, 0, 0, 0, 0, 0, 0], "unit_system": "freecad_internal"})
        self.assertEqual(field["display_quantity"], {"status": "available", "text": "1 in", "factor": 25.4,
            "unit": "in", "source": "native_getUserPreferred", "sanitized": False, "truncated": False})
        row = snapshot["dimensions"][0]
        self.assertEqual([row[key] for key in ("length_mm", "width_mm", "height_mm")], [60.0, 40.0, 8.0])
        self.assertEqual(self.units.calls, [(60.0, "mm"), (40.0, "mm"), (8.0, "mm")])
        self.assertTrue(all(quantity.display_calls == 1 for quantity in self.units.quantities))
        self.assertEqual(self.quantity.display_calls, 1)
        self.assertEqual(self.obj.reads, 1)
        self.assertIn("尺寸（FreeCAD 显示单位）", tools.format_dimensions(snapshot))
        self.assertIn("2.362 in | 1.575 in | 0.315 in", tools.format_dimensions(snapshot))
        self.assertIn(" | 1 in | ", tools.format_information(snapshot))
        self.assertNotIn("(内部值", tools.format_information(snapshot))
        self.assertIn("2\\.362 in", tools.format_markdown(snapshot))

    def test_all_supported_quantity_dimensions_use_display_text_without_inventing_conversions(self):
        cases = {"Force": (1000.0, (1, 1, -2, 0, 0, 0, 0, 0), "1,000 N", 1000.0, "N"),
                 "Temperature": (293.15, (0, 0, 0, 0, 1, 0, 0, 0), "293,150 K", 1.0, "K"),
                 "Area": (645.16, (2, 0, 0, 0, 0, 0, 0, 0), "1 in²", 645.16, "in²"),
                 "Volume": (16387.064, (3, 0, 0, 0, 0, 0, 0, 0), "1 in³", 16387.064, "in³"),
                 "Fraction": (698.5, (1, 0, 0, 0, 0, 0, 0, 0), "2' 3\" + 1/2\"", 25.4, "in")}
        self.obj.bom_values = {name: DisplayQuantity(value, signature, (text, factor, unit))
                               for name, (value, signature, text, factor, unit) in cases.items()}
        self.obj.bom_types = {name: "App::PropertyQuantity" for name in cases}
        self.obj.PropertiesList = ["Shape", *cases]
        snapshot = self.read(fields=cases)
        fields = {field["name"]: field for field in snapshot["properties"][0]["fields"]}
        for name, (value, signature, text, factor, unit) in cases.items():
            self.assertEqual(fields[name]["value"]["value"], value)
            self.assertEqual(fields[name]["value"]["unit_signature"], list(signature))
            self.assertEqual(fields[name]["display_quantity"]["text"], text)
            self.assertIn(text, tools.format_information(snapshot))
            self.assertIn(tools.escape_markdown(text), tools.format_markdown(snapshot))

    def test_display_errors_do_not_destroy_available_internal_quantity(self):
        for error, attribute, expected in ((RuntimeError("neutral private details"), False, "read_failed"),
                (AttributeError("neutral private details"), True, "unknown"),
                (RuntimeError("neutral private details"), True, "read_failed")):
            with self.subTest(attribute=attribute, expected=expected):
                self.quantity.preferred, self.quantity.getter_error = ("1 in", 25.4, "in"), None
                if attribute: self.quantity.getter_error = error
                else: self.quantity.preferred = error
                snapshot = self.read()
                field = snapshot["properties"][0]["fields"][0]
                self.assertEqual(field["status"], "available")
                self.assertEqual(field["value"]["value"], 25.4)
                self.assertEqual(field["display_quantity"]["status"], expected)
                self.assertIsNone(field["display_quantity"]["text"])
                self.assertIn("25.4 mm (显示单位不可读取:", tools.format_information(snapshot))
                self.assertNotIn("neutral private details", json.dumps(snapshot))

    def test_invalid_raw_quantity_never_calls_display_getter_or_publishes_display_value(self):
        for value, signature in ((math.nan, [1, 0, 0, 0, 0, 0, 0, 0]),
                                 (25.4, [0, 0, 0, 0, 0, 0, 0, 1])):
            self.quantity.Value, self.quantity.Unit.Signature = value, signature
            field = self.read()["properties"][0]["fields"][0]
            self.assertEqual(field["status"], "unknown")
            self.assertIsNone(field["value"])
            self.assertEqual(field["display_quantity"]["status"], "unknown")
        self.assertEqual(self.quantity.display_gets, 0)

    def test_preferred_tuple_types_and_factor_are_strict_without_coercion(self):
        candidates = (["1 in", 25.4, "in"], ("1 in", 25.4), (1, 25.4, "in"), ("1 in", 1, "in"),
                      ("1 in", True, "in"), ("1 in", 0.0, "in"), ("1 in", -1.0, "in"),
                      ("1 in", math.nan, "in"), ("1 in", math.inf, "in"), ("1 in", 25.4, fixture.PftOpaqueValue()),
                      ("", 1.0, ""), (" \n\t", 1.0, "mm"))
        for preferred in candidates:
            with self.subTest(value_type=type(preferred)):
                display = tools._quantity_display(DisplayQuantity(25.4, preferred=preferred))
                self.assertEqual(display["status"], "unknown")
                self.assertIsNone(display["text"])
                self.assertIsNone(display["factor"])
                self.assertIsNone(display["unit"])
        self.assertEqual(tools._quantity_display(self.quantity)["status"], "available")
        self.assertEqual(tools._quantity_display(fixture.Quantity(25.4))["status"], "unsupported")

    def test_zero_negative_zero_and_dimensionless_empty_unit_remain_valid(self):
        self.quantity.Value, self.quantity.Unit.Signature = -0.0, [0] * 8
        self.quantity.preferred = ("0,000", 1.0, "")
        self.obj.bom_types["Length"] = "App::PropertyQuantity"
        self.obj.shape = fixture.PftShape((0, 40, 8))
        snapshot = self.read()
        field = snapshot["properties"][0]["fields"][0]
        self.assertEqual(math.copysign(1, field["value"]["value"]), -1)
        self.assertEqual(field["display_quantity"]["unit"], "")
        self.assertEqual(field["display_quantity"]["status"], "available")
        self.assertEqual(snapshot["dimensions"][0]["display_dimensions"]["length_mm"]["text"], "0")

    def test_display_utf8_budgets_and_control_cleaning_never_publish_a_partial_quantity(self):
        exact = "界" * 682 + "ab"
        display = tools._quantity_display(DisplayQuantity(1.0, preferred=(exact, 1.0, "mm")))
        self.assertEqual(len(display["text"].encode("utf-8")), 2048)
        for text, unit in ((exact + "c", "mm"), ("x" * 2049, "mm"), ("1 mm", "u" * 129)):
            display = tools._quantity_display(DisplayQuantity(1.0, preferred=(text, 1.0, unit)))
            self.assertEqual(display["status"], "unknown")
            self.assertTrue(display["truncated"])
            self.assertIsNone(display["text"])
            self.assertEqual(display["limit_reason"], "utf8_budget")
        display = tools._quantity_display(DisplayQuantity(1.0, preferred=("1\n\r\t\x00\ud800 mm", 1.0, "\u202emm")))
        self.assertEqual(display["text"], "1      mm")
        self.assertEqual(display["unit"], " mm")
        self.assertTrue(display["sanitized"])
        json.dumps(display, allow_nan=False).encode("utf-8")

    def test_dimension_constructor_failure_is_separate_and_keeps_cached_geometry(self):
        self.units.responses[40.0] = RuntimeError("neutral private constructor detail")
        snapshot = self.read()
        row = snapshot["dimensions"][0]
        self.assertEqual(row["status"], "available")
        self.assertEqual(row["width_mm"], 40.0)
        self.assertEqual(row["display_dimensions"]["width_mm"]["status"], "read_failed")
        self.assertEqual(row["display_dimensions"]["length_mm"]["status"], "available")
        self.assertIn("40 mm (显示单位不可读取: 读取失败)", tools.format_dimensions(snapshot))
        self.assertNotIn("neutral private constructor detail", json.dumps(snapshot))
        self.assertEqual(self.obj.reads, 1)

    def test_missing_units_namespace_and_invalid_cached_shape_do_not_claim_display_values(self):
        del self.app.Units
        row = self.read()["dimensions"][0]
        self.assertTrue(all(display["status"] == "unknown" for display in row["display_dimensions"].values()))
        self.app.Units = self.units
        self.obj.shape = fixture.PftShape((math.nan, 40, 8))
        row = self.read()["dimensions"][0]
        self.assertEqual(row["status"], "invalid_cache")
        self.assertIsNone(row["length_mm"])
        self.assertEqual(self.units.calls, [])
        self.assertIn("显示单位不可读取", tools.format_dimensions({"use_user_units": True, "dimensions": [row]}))

    def test_refresh_replaces_values_but_old_snapshot_and_all_renderers_use_only_frozen_dtos(self):
        snapshot = self.read()
        rendered = tuple(function(snapshot) for function in (tools.format_information, tools.format_dimensions, tools.format_markdown))
        reads = self.obj.reads, self.quantity.display_gets, self.quantity.display_calls, list(self.units.calls)
        self.quantity.Value, self.quantity.preferred = 50.8, ("2 in", 25.4, "in")
        self.units.responses[60.0] = ("60 mm", 1.0, "mm")
        self.assertEqual(rendered, tuple(function(snapshot) for function in (tools.format_information, tools.format_dimensions, tools.format_markdown)))
        self.assertEqual(reads, (self.obj.reads, self.quantity.display_gets, self.quantity.display_calls, self.units.calls))
        refreshed = self.read()
        self.assertEqual(refreshed["properties"][0]["fields"][0]["display_quantity"]["text"], "2 in")
        self.assertEqual(snapshot["properties"][0]["fields"][0]["display_quantity"]["text"], "1 in")
        self.assertIn("60 mm", tools.format_dimensions(refreshed))

    def test_new_display_metadata_counts_toward_property_and_combined_dimension_budget(self):
        self.quantity.preferred = ("n" * 1000, 1.0, "mm")
        with patch.object(tools, "PFT_MAX_PROPERTIES_BYTES", 700):
            self.read(False)
            with self.assertRaises(tools.PftDocumentToolsError) as caught:
                self.read()
        self.assertEqual(caught.exception.code, "BOM_PROPERTIES_TOO_LARGE")
        for key in self.units.responses: self.units.responses[key] = ("n" * 300, 1.0, "mm")
        with patch.object(tools, "PFT_MAX_PROPERTIES_BYTES", 1000):
            with self.assertRaises(tools.PftDocumentToolsError) as caught:
                self.read(fields=())
        self.assertEqual(caught.exception.code, "UNIT_DISPLAY_TOO_LARGE")

    def test_combined_budget_counts_complete_wrappers_utf8_and_array_separators_at_exact_boundary(self):
        second = fixture.PftBomObject(self.doc, "Second", {"Length": DisplayQuantity(50.8, preferred=("2½ in", 25.4, "in"))},
                                      {"Length": "App::PropertyLength"})
        targets = [self.obj.Name, second.Name]
        snapshot = self.read(targets=targets)
        payload = {"properties": snapshot["properties"], "dimensions": [
            {"display_dimensions": row["display_dimensions"]} for row in snapshot["dimensions"]]}
        size = len(json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))
        with patch.object(tools, "PFT_MAX_PROPERTIES_BYTES", size):
            self.assertEqual(self.read(targets=targets)["properties"], snapshot["properties"])
        with patch.object(tools, "PFT_MAX_PROPERTIES_BYTES", size - 1):
            with self.assertRaises(tools.PftDocumentToolsError) as caught:
                self.read(targets=targets)
        self.assertEqual(caught.exception.code, "UNIT_DISPLAY_TOO_LARGE")

    def test_empty_target_scope_and_default_bom_do_not_expand_quantity_property_reads(self):
        snapshot = self.read(targets=[])
        self.assertEqual(snapshot["dimensions"], [])
        self.assertEqual(snapshot["properties"], [])
        self.assertEqual(self.units.calls, [])
        self.assertEqual(self.quantity.display_gets, 0)
        snapshot = tools.read_document_tools(self.app, self.gui, target_names=[self.obj.Name], use_user_units=True)
        self.assertEqual([field["name"] for field in snapshot["properties"][0]["fields"]], [name for name, _ in tools.BOM_FIELDS])
        self.assertEqual(self.quantity.display_gets, 0)
        self.assertNotIn("Length", self.obj.bom_reads)


if __name__ == "__main__":
    unittest.main()
