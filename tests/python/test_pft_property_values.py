# SPDX-License-Identifier: Apache-2.0
"""Nested property read failures through independent native-style Python fakes."""
import json
import unittest
from unittest.mock import Mock

import test_pft_document_tools as fixtures


tools = fixtures.tools
_MISSING = object()
_LENGTH = (1, 0, 0, 0, 0, 0, 0, 0)


def _reported(value):
    if value is _MISSING:
        raise AttributeError("synthetic missing native member")
    if isinstance(value, Exception):
        raise value
    return value


class Unit:
    def __init__(self, signature, reads):
        self.signature, self.reads = signature, reads

    @property
    def Signature(self):
        self.reads["Signature"] += 1
        return _reported(self.signature)

    @property
    def Type(self):
        self.reads["display"] += 1
        raise AssertionError("display type must not select internal units")

    def __str__(self):
        self.reads["stringify"] += 1
        raise AssertionError("native units must not be stringified")

    __repr__ = __str__


class Quantity:
    """Match the native type name without importing FreeCAD or using its API."""
    def __init__(self, value=0.0, signature=_LENGTH, unit=_MISSING):
        self.reads = {"Value": 0, "Unit": 0, "Signature": 0, "display": 0, "stringify": 0}
        self.number = value
        self.native_unit = Unit(signature, self.reads) if unit is _MISSING else unit

    @property
    def Value(self):
        self.reads["Value"] += 1
        return _reported(self.number)

    @property
    def Unit(self):
        self.reads["Unit"] += 1
        return _reported(self.native_unit)

    @property
    def UserString(self):
        self.reads["display"] += 1
        raise AssertionError("display-schema string must not be read")

    def __str__(self):
        self.reads["stringify"] += 1
        raise AssertionError("native quantities must not be stringified")

    __repr__ = __str__


class PropertyValueFailureTests(unittest.TestCase):
    def setUp(self):
        self.document = fixtures.PftDoc("PropertyFixture")

    def field(self, value):
        obj = fixtures.PftBomObject(self.document, "Part", {"Length": value}, {"Length": "App::PropertyLength"})
        field = tools._property_field(obj, "Length", obj.PropertiesList, True, True)
        self.assertEqual(obj.reads, 0)
        self.assertEqual(obj.bom_reads, ["Length"])
        self.assert_no_display_reads(value)
        return field

    def assert_no_display_reads(self, quantity):
        self.assertEqual(quantity.reads["display"], 0)
        self.assertEqual(quantity.reads["stringify"], 0)

    def assert_nested_failure(self, member):
        error = RuntimeError("synthetic nested native getter failure")
        quantity = Quantity(value=error if member == "Value" else 0.0,
                            signature=error if member == "Signature" else _LENGTH)
        if member == "Unit": quantity.native_unit = error
        field = self.field(quantity)
        self.assertEqual(field["status"], "read_failed")
        self.assertIsNone(field["value"])
        self.assertFalse(field["truncated"])
        self.assertFalse(field["sanitized"])
        self.assertNotIn("synthetic nested native getter failure", json.dumps(field))

    def test_quantity_value_getter_exception_is_read_failed(self):
        self.assert_nested_failure("Value")

    def test_quantity_unit_getter_exception_is_read_failed(self):
        self.assert_nested_failure("Unit")

    def test_quantity_signature_getter_exception_is_read_failed(self):
        self.assert_nested_failure("Signature")

    def test_missing_nested_members_are_unknown_instead_of_failed_or_empty(self):
        for member in ("Value", "Unit", "Signature"):
            with self.subTest(member=member):
                quantity = Quantity(value=_MISSING if member == "Value" else 0.0,
                                    signature=_MISSING if member == "Signature" else _LENGTH)
                if member == "Unit": quantity.native_unit = _MISSING
                field = self.field(quantity)
                self.assertEqual(field["status"], "unknown")
                self.assertIsNone(field["value"])

    def test_none_nested_members_are_unknown_instead_of_zero_or_empty(self):
        for member in ("Value", "Unit", "Signature"):
            with self.subTest(member=member):
                quantity = Quantity(value=None if member == "Value" else 0.0,
                                    signature=None if member == "Signature" else _LENGTH)
                if member == "Unit": quantity.native_unit = None
                field = self.field(quantity)
                self.assertEqual(field["status"], "unknown")
                self.assertIsNone(field["value"])

    def test_malformed_values_and_signatures_are_unknown_without_display_fallback(self):
        bad_values = (False, "0", float("nan"), float("inf"), float("-inf"), fixtures.PftOpaqueValue())
        for value in bad_values:
            with self.subTest(value_type=type(value).__name__):
                field = self.field(Quantity(value=value))
                self.assertEqual(field["status"], "unknown")
                self.assertIsNone(field["value"])
        bad_signatures = ("mm", (), (1,) * 7, (1,) * 9, (True, 0, 0, 0, 0, 0, 0, 0),
                          (1.0, 0, 0, 0, 0, 0, 0, 0), (128, 0, 0, 0, 0, 0, 0, 0),
                          (-129, 0, 0, 0, 0, 0, 0, 0), {"length": 1})
        for signature in bad_signatures:
            with self.subTest(signature_type=type(signature).__name__):
                field = self.field(Quantity(signature=signature))
                self.assertEqual(field["status"], "unknown")
                self.assertIsNone(field["value"])

    def test_zero_quantity_is_available_with_internal_value_and_never_displayed_units(self):
        quantity = Quantity(value=0.0)
        field = self.field(quantity)
        self.assertEqual(field["status"], "available")
        self.assertEqual(field["value"], {"kind": "quantity", "value": 0.0, "unit": "mm",
            "unit_signature": list(_LENGTH), "unit_system": "freecad_internal"})
        self.assertIs(type(field["value"]["value"]), float)

    def test_nested_failure_propagates_to_object_status_without_any_shape_read(self):
        quantity = Quantity(signature=RuntimeError("synthetic unit access failure"))
        obj = fixtures.PftBomObject(self.document, "Part", {"Length": quantity}, {"Length": "App::PropertyLength"})
        row = tools._object_properties([obj], ("Length",), explicit=True)[0]
        self.assertEqual(row["status"], "read_failed")
        self.assertEqual(row["fields"][0]["status"], "read_failed")
        self.assertIsNone(row["fields"][0]["value"])
        self.assertEqual(obj.reads, 0)
        self.assert_no_display_reads(quantity)
        self.assertNotIn("synthetic unit access failure", json.dumps(row))

    def test_default_five_bom_fields_do_not_expand_native_supported_types_or_read_values(self):
        names = tools.validate_property_names(None)
        self.assertEqual(names, tuple(name for name, _ in tools.BOM_FIELDS))
        self.assertEqual(len(names), 5)
        forbidden = AssertionError("non-string default BOM getter must not run")
        types = dict(zip(names, ("App::PropertyEnumeration", "App::PropertyLength", "App::PropertyQuantity",
                                 "App::PropertyBool", "App::PropertyInteger")))
        obj = fixtures.PftBomObject(self.document, "Part", {name: forbidden for name in names}, types)
        row = tools._object_properties([obj], names, explicit=False)[0]
        self.assertEqual([field["name"] for field in row["fields"]], list(names))
        self.assertTrue(all(field["status"] == "unsupported" and field["value"] is None for field in row["fields"]))
        self.assertTrue(all("kind" not in field for field in row["fields"]))
        self.assertEqual(obj.bom_reads, [])
        self.assertEqual(obj.reads, 0)

    def test_missing_field_and_none_empty_false_zero_keep_separate_scalar_meanings(self):
        values = {"Text": "", "Flag": False, "Count": 0, "Unknown": None}
        obj = fixtures.PftBomObject(self.document, "Part", values,
            {"Flag": "App::PropertyBool", "Count": "App::PropertyInteger"})
        names = ("Missing", "Text", "Flag", "Count", "Unknown")
        row = tools._object_properties([obj], names, explicit=True)[0]
        self.assertEqual([(field["status"], field["value"]) for field in row["fields"]],
            [("missing", None), ("empty", ""), ("available", False), ("available", 0), ("unknown", None)])
        self.assertNotIn("Missing", obj.bom_reads)
        self.assertEqual(obj.reads, 0)

    def test_link_report_reads_only_selected_instance_value_without_source_or_extra_shape(self):
        source = fixtures.PftBomObject(self.document, "Source", {"Length": AssertionError("source getter forbidden")},
                                       {"Length": "App::PropertyLength"})
        quantity = Quantity(value=0.0)
        link = fixtures.PftBomObject(self.document, "Instance", {"Length": quantity, "LinkedObject": source,
            "Shape": fixtures.PftOpaqueValue(), "Opaque": fixtures.PftOpaqueValue()},
            {"Length": "App::PropertyLength", "LinkedObject": "App::PropertyLink", "Shape": "Part::PropertyPartShape",
             "Opaque": "App::PropertyPythonObject"}, kind="App::Link")
        link.getLinkedObject = Mock(side_effect=AssertionError("source resolution forbidden"))
        link.getGlobalPlacement = Mock(side_effect=AssertionError("extra transformation forbidden"))
        row = tools._object_properties([link], ("Length", "LinkedObject", "Shape", "Opaque"), explicit=True)[0]
        self.assertEqual([field["status"] for field in row["fields"]], ["available", "unsupported", "unsupported", "unsupported"])
        self.assertEqual(row["fields"][0]["value"]["value"], 0.0)
        self.assertFalse(row["link_source_resolved"])
        self.assertEqual(row["locality"], "not_verified")
        self.assertEqual(link.bom_reads, ["Length"])
        self.assertEqual(source.bom_reads, [])
        self.assertEqual((link.reads, source.reads), (0, 0))
        link.getLinkedObject.assert_not_called()
        link.getGlobalPlacement.assert_not_called()
        self.assert_no_display_reads(quantity)


if __name__ == "__main__":
    unittest.main()
