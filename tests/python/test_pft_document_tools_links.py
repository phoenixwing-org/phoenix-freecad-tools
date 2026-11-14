# SPDX-License-Identifier: Apache-2.0
"""Synthetic explicit native-reference reads; no CAD, Qt or source resolution."""
import gc
import json
import types
import unittest
import weakref
from unittest.mock import patch

import test_pft_document_tools as fixture

tools = fixture.tools


class Forbidden:
    def __getattribute__(self, name):
        raise AssertionError("untrusted target attribute access")
    def __bool__(self): raise AssertionError("untrusted truth conversion")
    def __eq__(self, other): raise AssertionError("untrusted equality")
    def __hash__(self): raise AssertionError("untrusted hash")
    def __iter__(self): raise AssertionError("untrusted iteration")
    def __str__(self): raise AssertionError("untrusted string conversion")
    def __repr__(self): raise AssertionError("untrusted representation")


class ForbiddenList(list):
    def __len__(self): raise AssertionError("custom length")
    def __iter__(self): raise AssertionError("custom iteration")
    def __getitem__(self, _): raise AssertionError("custom indexing")


class ForbiddenTuple(tuple):
    def __len__(self): raise AssertionError("custom length")
    def __iter__(self): raise AssertionError("custom iteration")
    def __getitem__(self, _): raise AssertionError("custom indexing")


class ForbiddenString(str):
    def __hash__(self): raise AssertionError("custom hash")
    def __eq__(self, _): raise AssertionError("custom equality")
    def __iter__(self): raise AssertionError("custom iteration")
    def __str__(self): raise AssertionError("custom string conversion")


class RegistryTarget(fixture.PftObject):
    def __init__(self, document, name):
        super().__init__(document, name, kind="App::Origin")
        self.accesses = []
    def __getattribute__(self, name):
        if name in ("Name", "Document", "TypeId"):
            values = object.__getattribute__(self, "__dict__")
            if "accesses" in values: values["accesses"].append(name)
        if name in ("Shape", "Label", "PropertiesList", "LinkedObject", "getLinkedObject", "getSubObject", "Placement", "ViewObject"):
            raise AssertionError("reference targets are metadata-only")
        return object.__getattribute__(self, name)


class PropertyReferenceTests(unittest.TestCase):
    def setUp(self):
        self.doc = fixture.PftDoc()
        self.app, self.gui = fixture.PftApp(self.doc), fixture.PftGui(self.doc)
        self.target = RegistryTarget(self.doc, "Anchor")
        self.second = RegistryTarget(self.doc, "Auxiliary")
        self.source = fixture.PftBomObject(self.doc, "Source", {"Reference": self.target}, {"Reference": "App::PropertyLink"})

    def read(self, fields=("Reference",), targets=("Source",)):
        return tools.read_document_tools(self.app, self.gui, target_names=list(targets), property_names=None if fields is None else list(fields))

    def field(self, value, native_type="App::PropertyLink"):
        self.source.bom_values["Reference"] = value
        self.source.bom_types["Reference"] = native_type
        return self.read()["properties"][0]["fields"][0]

    def encoded(self, value, kind="reference", entries=None):
        target = self.target
        registry = {id(target): (target, "Anchor"), id(self.second): (self.second, "Auxiliary")} if entries is None else entries
        return tools._property_reference(value, kind, ("Example", registry))

    def test_exact_sixteen_native_types_and_four_shapes_are_supported(self):
        expected = {}
        values = {"Link": (self.target, "reference"), "LinkList": ([self.target, None, self.target], "reference_list"),
                  "LinkSub": ((self.target, ["Face2", ""]), "subreference"),
                  "LinkSubList": ([(self.target, ("Edge3", "Face2"))], "subreference_list")}
        for family, (value, kind) in values.items():
            for suffix in ("", "Child", "Global", "Hidden"):
                native_type = "App::Property" + family + suffix
                expected[native_type] = kind
                with self.subTest(native_type=native_type):
                    before = len(self.source.bom_reads)
                    field = self.field(value, native_type)
                    self.assertEqual((field["kind"], field["status"]), (kind, "available"))
                    self.assertEqual(len(self.source.bom_reads) - before, 1)
                    self.assertEqual(field["registry_membership"], "registered_at_scan")
                    self.assertIs(field["link_source_resolved"], False)
                    self.assertEqual(field["subelement_validation"], "not_performed")
        self.assertEqual(tools._REFERENCE_PROPERTY_KINDS, expected)

    def test_empty_scalar_link_is_explicit_no_live_target_report_not_unknown_object(self):
        for native in ("App::PropertyLink", "App::PropertyLinkSub"):
            field = self.field(None, native)
            self.assertEqual(field["status"], "available")
            self.assertIsNone(field["value"]["target"])
            self.assertEqual(field["none_semantics"], "no_live_target_reported")
            self.assertNotIn("reference_status", field)
        self.assertIsNone(self.field(None, "App::PropertyLinkSub")["value"]["subelements"])
        self.assertEqual(self.field((self.target, []), "App::PropertyLinkSub")["value"]["subelements"], [])

    def test_empty_lists_distinct_from_scalar_none(self):
        for native in ("App::PropertyLinkList", "App::PropertyLinkSubList"):
            field = self.field([], native)
            self.assertEqual((field["status"], field["value"]), ("empty", []))
        self.assertEqual(self.field(None, "App::PropertyLinkList")["status"], "unsupported")

    def test_link_list_keeps_order_duplicates_and_none_slots(self):
        field = self.field([self.target, None, self.second, self.target], "App::PropertyLinkList")
        self.assertEqual(field["value"], [{"target": {"document": "Example", "object": "Anchor"}}, {"target": None},
            {"target": {"document": "Example", "object": "Auxiliary"}}, {"target": {"document": "Example", "object": "Anchor"}}])

    def test_subnames_preserve_getter_reported_text_and_empty_names_without_resolution(self):
        names = ["Face2", "", "Nested.$Display.Edge3", "Mapped;:N7", "面一"]
        field = self.field((self.target, names), "App::PropertyLinkSub")
        self.assertEqual(field["value"]["subelements"], names)
        self.assertIsNot(field["value"]["subelements"], names)
        self.assertEqual(field["subelement_validation"], "not_performed")

    def test_sublist_keeps_native_grouping_A_B_A_and_repeated_subnames(self):
        value = [(self.target, ("Face1", "Face1")), (self.second, ("",)), (self.target, ("Edge2",))]
        field = self.field(value, "App::PropertyLinkSubList")
        self.assertEqual([x["target"]["object"] for x in field["value"]], ["Anchor", "Auxiliary", "Anchor"])
        self.assertEqual([x["subelements"] for x in field["value"]], [["Face1", "Face1"], [""], ["Edge2"]])

    def test_encoder_dispatches_no_target_attributes_or_magic_operations(self):
        poison = Forbidden()
        context = ("Example", {id(poison): (poison, "MetadataOnly")})
        for value, kind in ((poison, "reference"), ([poison, None, poison], "reference_list"),
                            ((poison, ["Face1"]), "subreference"), ([(poison, ("Face1",))], "subreference_list")):
            result = tools._property_reference(value, kind, context)
            self.assertEqual(result["status"], "available")

    def test_full_reader_uses_authorized_scan_but_new_encoding_has_zero_target_getters(self):
        original = tools._property_reference
        calls = []
        def observe(value, kind, context):
            before = (list(self.target.accesses), list(self.second.accesses))
            result = original(value, kind, context)
            self.assertEqual(before, (self.target.accesses, self.second.accesses))
            calls.append(kind)
            return result
        with patch.object(tools, "_property_reference", side_effect=observe):
            snapshot = self.read()
        self.assertEqual(calls, ["reference"])
        self.assertTrue(self.target.accesses)
        self.assertLessEqual(set(self.target.accesses), {"Name", "Document", "TypeId"})
        self.assertEqual(snapshot["properties"][0]["fields"][0]["value"]["target"]["object"], "Anchor")
        self.assertNotIn("Anchor", [x["Name"] for x in snapshot["candidates"]])

    def test_registry_reuses_same_live_scan_Name_Document_read_counts(self):
        document = types.SimpleNamespace()
        target = RegistryTarget(self.doc, "Metadata")
        target.Document = document
        document.getObject = lambda name: target if name == "Metadata" else None
        self.assertTrue(tools._live(document, target))
        original = list(target.accesses)
        target.accesses.clear()
        objects, registry = tools._scan_reference_registry(document, [target])
        self.assertEqual(target.accesses, original)
        self.assertIs(objects[0], target)
        self.assertIs(registry[id(target)][0], target)
        self.assertEqual(registry[id(target)][1], "Metadata")

    def test_registry_retains_strong_objects_and_checks_is_not_only_id(self):
        class Value: pass
        value = Value(); reference = weakref.ref(value)
        context = ("Example", {id(value): (value, "Strong")})
        key = id(value); del value; gc.collect()
        self.assertIsNotNone(reference())
        self.assertEqual(tools._property_reference(reference(), "reference", context)["status"], "available")
        impostor = Forbidden()
        result = tools._property_reference(impostor, "reference", ("Example", {id(impostor): (reference(), "Strong")}))
        self.assertEqual((result["status"], result["reference_status"], result["value"]), ("unknown", "not_in_captured_registry", None))
        self.assertEqual(key, id(reference()))

    def test_unregistered_foreign_and_same_name_different_object_are_unknown_not_null(self):
        for value in (Forbidden(), object(), types.SimpleNamespace(Name="Anchor", Document=self.doc), False, 0, "Anchor"):
            result = self.encoded(value)
            self.assertEqual((result["status"], result["value"], result["reference_status"]), ("unknown", None, "not_in_captured_registry"))

    def test_wrong_current_registry_identity_is_excluded_at_scan(self):
        document = types.SimpleNamespace()
        first, replacement = types.SimpleNamespace(), types.SimpleNamespace()
        first.Document, first.Name = document, "Same"
        replacement.Document, replacement.Name = document, "Same"
        document.getObject = lambda _: replacement
        objects, registry = tools._scan_reference_registry(document, [first, replacement])
        self.assertEqual(objects, [replacement])
        result = tools._property_reference(first, "reference", ("Example", registry))
        self.assertEqual(result["reference_status"], "not_in_captured_registry")
        self.assertIs(registry[id(replacement)][0], replacement)

    def test_registration_is_explicitly_scan_time_not_claimed_current_after_removal(self):
        snapshot = self.read()
        self.doc.Objects.remove(self.target)
        self.assertEqual(snapshot["properties"][0]["fields"][0]["registry_membership"], "registered_at_scan")
        self.assertEqual(snapshot["properties"][0]["fields"][0]["value"]["target"]["object"], "Anchor")
        result = self.read()["properties"][0]["fields"][0]
        self.assertEqual((result["status"], result["value"]), ("unknown", None))

    def test_invalid_context_never_invents_target_identity(self):
        for context in (None, {}, ("Example",), (Forbidden(), {}), ("Example", []), ("", {})):
            result = tools._property_reference(Forbidden(), "reference", context)
            self.assertEqual((result["status"], result["value"], result["reference_status"]), ("unknown", None, "registry_unavailable"))

    def test_bad_custom_containers_and_subname_subclasses_are_not_dispatched(self):
        values = ((ForbiddenList(), "reference_list"), (ForbiddenList(), "subreference_list"),
                  (ForbiddenTuple((self.target, [])), "subreference"),
                  ((self.target, ForbiddenList()), "subreference"),
                  ([(self.target, ForbiddenTuple())], "subreference_list"),
                  ((self.target, [ForbiddenString("Edge1")]), "subreference"),
                  ([(self.target, ["Face1"])], "subreference_list"),
                  ((self.target, ("Face1",)), "subreference"),
                  ([(None, ())], "subreference_list"))
        for value, kind in values:
            with self.subTest(kind=kind):
                self.assertEqual(self.encoded(value, kind)["status"], "unsupported")

    def test_one_unknown_member_discards_entire_field_not_valid_prefix(self):
        for value, native in (([self.target, Forbidden(), self.second], "App::PropertyLinkList"),
                              ([(self.target, ("Face1",)), (Forbidden(), ("Edge2",))], "App::PropertyLinkSubList")):
            field = self.field(value, native)
            self.assertEqual((field["status"], field["value"]), ("unknown", None))
            self.assertEqual(field["reference_status"], "not_in_captured_registry")

    def test_one_hundred_null_slots_preserved_and_101_rejected_without_prefix(self):
        field = self.field([None] * 100, "App::PropertyLinkList")
        self.assertEqual(field["status"], "available")
        self.assertEqual(field["value"], [{"target": None}] * 100)
        field = self.field([None] * 101, "App::PropertyLinkList")
        self.assertEqual((field["status"], field["value"], field["limit_reason"]), ("unknown", None, "item_limit"))
        self.assertTrue(field["truncated"])

    def test_subname_100_limit_is_cumulative_across_sublist_groups(self):
        field = self.field((self.target, [""] * 100), "App::PropertyLinkSub")
        self.assertEqual(field["status"], "available")
        self.assertEqual(len(field["value"]["subelements"]), 100)
        for value, native in (((self.target, [""] * 101), "App::PropertyLinkSub"),
                              ([(self.target, ("",) * 60), (self.second, ("",) * 41)], "App::PropertyLinkSubList")):
            field = self.field(value, native)
            self.assertEqual((field["status"], field["value"], field["limit_reason"]), ("unknown", None, "subelement_limit"))

    def test_group_limit_precedes_member_processing(self):
        field = self.field([Forbidden()] * 101, "App::PropertyLinkSubList")
        self.assertEqual((field["status"], field["value"], field["limit_reason"]), ("unknown", None, "item_limit"))

    def test_field_utf8_budget_accepts_exact2048_and_rejects2049(self):
        base = self.encoded((self.target, [""]), "subreference")["value"]
        overhead = len(json.dumps(base, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        count = tools.PFT_MAX_PROPERTY_BYTES - overhead
        text = "é" * (count // 2) + "x" * (count % 2)
        result = self.encoded((self.target, [text]), "subreference")
        self.assertEqual(result["status"], "available")
        self.assertEqual(len(json.dumps(result["value"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")), 2048)
        exceeded = self.encoded((self.target, [text + "x"]), "subreference")
        self.assertEqual((exceeded["status"], exceeded["value"], exceeded["limit_reason"]), ("unknown", None, "utf8_budget"))

    def test_invalid_text_is_not_silently_sanitized_or_partly_published(self):
        for text in ("Face1\x00", "Face1\n", "Face1\u200d", "Face1\ud800", "x" * 2049):
            field = self.field((self.target, ["Face2", text]), "App::PropertyLinkSub")
            self.assertEqual((field["status"], field["value"], field["reference_status"]), ("unknown", None, "invalid_reference_text"))
            self.assertFalse(field["sanitized"])

    def test_missing_bad_native_type_and_XLink_never_get_value(self):
        for native in ("App::PropertyXLink", "App::PropertyXLinkSub", "App::PropertyXLinkList", "App::PropertyXLinkSubList",
                       "App::PropertyLinkCustom", "App::PropertyLinkPython", "App::PropertyLinkSubListUnknown", ForbiddenString("App::PropertyLink"), Forbidden()):
            self.source.bom_types["Reference"] = native
            before = list(self.source.bom_reads)
            with patch.object(self.source, "getTypeIdOfProperty", return_value=native):
                field = self.read()["properties"][0]["fields"][0]
            self.assertEqual((field["status"], field["value"]), ("unsupported", None))
            self.assertEqual(before, self.source.bom_reads)
        field = self.read(("Missing",))["properties"][0]["fields"][0]
        self.assertEqual(field["status"], "missing")

    def test_default_BOM_and_empty_fields_or_targets_add_no_reference_reads(self):
        self.source.bom_values["PartNumber"] = self.target
        self.source.bom_types["PartNumber"] = "App::PropertyLink"
        self.source.PropertiesList.append("PartNumber")
        with patch.object(tools, "_scan_reference_registry", wraps=tools._scan_reference_registry) as scan:
            result = self.read(None)
            self.assertEqual(result["property_scope"], "bom")
            self.assertEqual(result["properties"][0]["fields"][0]["status"], "unsupported")
            self.assertEqual(self.source.bom_reads, [])
            self.assertEqual(self.read(())["properties"], [])
            self.assertEqual(self.read(("Reference",), ())["properties"], [])
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(self.source.bom_reads, [])

    def test_unselected_owners_are_not_read_and_getter_failure_is_redacted(self):
        other = fixture.PftBomObject(self.doc, "Other", {"Reference": AssertionError("must not read unrelated field")}, {"Reference": "App::PropertyLink"})
        self.field(self.target)
        self.assertEqual(other.bom_reads, [])
        field = self.field(RuntimeError("synthetic secret detail"))
        self.assertEqual((field["status"], field["value"]), ("read_failed", None))
        self.assertNotIn("synthetic secret detail", json.dumps(field))

    def test_information_and_markdown_render_only_frozen_primitive_values(self):
        self.source.bom_values["Reference"] = (self.target, ["$A|B<>", "Face2"])
        self.source.bom_types["Reference"] = "App::PropertyLinkSub"
        snapshot = self.read()
        frozen = json.loads(json.dumps(snapshot, ensure_ascii=False))
        self.source.bom_values["Reference"] = AssertionError("do not reread source")
        before = (list(self.target.accesses), list(self.source.bom_reads))
        for value in (snapshot, frozen):
            information = tools.format_information(value)
            markdown = tools.format_markdown(value)
            self.assertIn('"subelement_validation":"not_performed"', information)
            self.assertIn('"object":"Anchor"', information)
            self.assertIn(tools.escape_markdown(tools._property_display(value["properties"][0]["fields"][0], lambda v:v)), markdown)
            self.assertNotIn(' | $A|B<> | ', markdown)
        self.assertEqual(before, (self.target.accesses, self.source.bom_reads))

    def test_reference_metadata_counts_in_exact_aggregate_budget(self):
        context = ("Example", {id(self.target): (self.target, "Anchor")})
        row = tools._object_properties([self.source], ("Reference",), explicit=True, reference_context=context)
        size = len(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))
        with patch.object(tools, "PFT_MAX_PROPERTIES_BYTES", size):
            self.assertEqual(tools._object_properties([self.source], ("Reference",), explicit=True, reference_context=context), row)
        with patch.object(tools, "PFT_MAX_PROPERTIES_BYTES", size - 1), self.assertRaises(tools.PftDocumentToolsError) as error:
            tools._object_properties([self.source], ("Reference",), explicit=True, reference_context=context)
        self.assertEqual(error.exception.code, "BOM_PROPERTIES_TOO_LARGE")

    def test_real128KiB_aggregate_limit_stops_instead_of_returning_prefix(self):
        owners = [self.source]
        self.source.bom_values["Reference"] = [None] * 100
        self.source.bom_types["Reference"] = "App::PropertyLinkList"
        for n in range(70):
            owners.append(fixture.PftBomObject(self.doc, "Owner" + str(n), {"Reference": [None] * 100}, {"Reference": "App::PropertyLinkList"}))
        with self.assertRaises(tools.PftDocumentToolsError) as error:
            self.read(targets=tuple(obj.Name for obj in owners))
        self.assertEqual(error.exception.code, "BOM_PROPERTIES_TOO_LARGE")
        self.assertEqual(tools.PFT_MAX_PROPERTIES_BYTES, 128 * 1024)


if __name__ == "__main__":
    unittest.main()
