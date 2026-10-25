# SPDX-License-Identifier: Apache-2.0
"""Synthetic data contract tests; no FreeCAD, Qt, user state or native process."""
import copy
import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools/pft_pad_contract.py"
spec = importlib.util.spec_from_file_location("pft_pad_contract_test", MODULE_PATH)
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def fixture():
    return {"kind": "pad_length", "body": "Body", "pad": "Pad", "length_mm": 10.0,
            "recompute_count": 2, "checked_objects": 12,
            "geometry": {
                "pad": {"volume_mm3": 120.0, "dimensions_mm": [3.0, 4.0, 10.0], "solids": 1},
                "body": {"volume_mm3": 120.0, "dimensions_mm": [4.0, 3.0, 10.0], "solids": 1}},
            "validation": "native_valid_one_solid_and_length_scaled_volume"}


class PftPadContractTests(unittest.TestCase):
    def assert_invalid(self, value, expected=None):
        with self.assertRaises(ValueError) as error:
            contract.validate_design_result(value, expected)
        self.assertEqual(str(error.exception), "Pad 设计结果格式无效。")

    def test_accepts_complete_result_without_assuming_identical_geometry(self):
        value = fixture()
        result = contract.validate_design_result(value)
        self.assertEqual(result, value)
        self.assertNotEqual(result["geometry"]["pad"], result["geometry"]["body"])

    def test_complete_expected_binding_is_exact_with_equivalent_numeric_length(self):
        value = fixture()
        self.assertEqual(contract.validate_design_result(value, {
            "body": "Body", "pad": "Pad", "length_mm": 10}), value)
        for field, replacement in (("body", "OtherBody"), ("pad", "OtherPad"), ("length_mm", 11)):
            expected = {"body": "Body", "pad": "Pad", "length_mm": 10}
            expected[field] = replacement
            with self.subTest(field=field):
                self.assert_invalid(value, expected)

    def test_result_is_deeply_detached_in_both_directions(self):
        value = fixture()
        result = contract.validate_design_result(value)
        self.assertIsNot(result, value)
        self.assertIsNot(result["geometry"], value["geometry"])
        for target in ("pad", "body"):
            self.assertIsNot(result["geometry"][target], value["geometry"][target])
            self.assertIsNot(result["geometry"][target]["dimensions_mm"],
                             value["geometry"][target]["dimensions_mm"])
        value["geometry"]["pad"]["dimensions_mm"][0] = 99
        result["geometry"]["body"]["dimensions_mm"][0] = 88
        self.assertEqual(result["geometry"]["pad"]["dimensions_mm"][0], 3.0)
        self.assertEqual(value["geometry"]["body"]["dimensions_mm"][0], 4.0)

    def test_unknown_and_missing_fields_are_rejected_at_every_level(self):
        for path in ((), ("geometry",), ("geometry", "pad"), ("geometry", "body")):
            value = fixture()
            target = value
            for key in path:
                target = target[key]
            missing = copy.deepcopy(value)
            missing_target = missing
            for key in path:
                missing_target = missing_target[key]
            missing_target.pop(next(iter(missing_target)))
            target["untrusted_extra"] = "synthetic-provider-field"
            with self.subTest(path=path, issue="extra"):
                self.assert_invalid(value)
            with self.subTest(path=path, issue="missing"):
                self.assert_invalid(missing)

    def test_string_literals_are_exact(self):
        for field in ("kind", "validation"):
            for replacement in (None, True, 1, [], {}, "", "wrong"):
                value = fixture()
                value[field] = replacement
                with self.subTest(field=field, replacement=replacement):
                    self.assert_invalid(value)

    def test_length_is_finite_bounded_and_never_boolean(self):
        for length in (0.01, 10000, 10, 10.5):
            value = fixture()
            value["length_mm"] = length
            self.assertEqual(contract.validate_design_result(value)["length_mm"], length)
        for length in (True, False, 0, -1, 0.009, 10000.01, float("nan"),
                       float("inf"), -float("inf"), "10", None, 10 ** 400):
            value = fixture()
            value["length_mm"] = length
            with self.subTest(length_type=type(length).__name__):
                self.assert_invalid(value)

    def test_counts_are_integer_bounded_and_native_zero_is_only_a_reported_count(self):
        for count in (0, 1, 9007199254740991):
            value = fixture()
            value["recompute_count"] = count
            self.assertEqual(contract.validate_design_result(value)["recompute_count"], count)
        for field, invalid in (
                ("recompute_count", (True, False, -1, 0.0, 1.5, 9007199254740992, float("inf"))),
                ("checked_objects", (True, False, 0, -1, 65, 1.0, 1.5, "1"))):
            for count in invalid:
                value = fixture()
                value[field] = count
                with self.subTest(field=field, count=count):
                    self.assert_invalid(value)
        for count in (1, 64):
            value = fixture()
            value["checked_objects"] = count
            self.assertEqual(contract.validate_design_result(value)["checked_objects"], count)

    def test_positive_geometry_has_no_invented_size_or_volume_cap(self):
        value = fixture()
        for target in ("pad", "body"):
            value["geometry"][target]["volume_mm3"] = 1e300
            value["geometry"][target]["dimensions_mm"] = [1e-300, 1e200, 1e300]
        self.assertEqual(contract.validate_design_result(value), value)
        for target in ("pad", "body"):
            for replacement in (True, False, 0, -1, float("nan"), float("inf"), "1", None):
                value = fixture()
                value["geometry"][target]["volume_mm3"] = replacement
                with self.subTest(target=target, field="volume", replacement=replacement):
                    self.assert_invalid(value)
                for index in range(3):
                    value = fixture()
                    value["geometry"][target]["dimensions_mm"][index] = replacement
                    with self.subTest(target=target, index=index):
                        self.assert_invalid(value)

    def test_dimensions_are_an_exact_three_element_list_and_solids_exactly_one(self):
        for target in ("pad", "body"):
            for dimensions in (None, "123", [], [1, 2], [1, 2, 3, 4], (1, 2, 3), {1, 2, 3}):
                value = fixture()
                value["geometry"][target]["dimensions_mm"] = dimensions
                with self.subTest(target=target, dimensions=dimensions):
                    self.assert_invalid(value)
            for count in (True, False, 0, 2, 1.0, "1", None):
                value = fixture()
                value["geometry"][target]["solids"] = count
                with self.subTest(target=target, count=count):
                    self.assert_invalid(value)

    def test_names_are_bounded_non_control_non_path_strings_without_coercion(self):
        for name in ("B", "主体", "Body 1", "Body-1", "Body.1", "界" * 64):
            value = fixture()
            value["body"] = value["pad"] = name
            self.assertEqual(contract.validate_design_result(value)["body"], name)
        invalid = ("", "界" * 65, " Body", "Body ", "\tBody", "Body\n", ".", "..",
                   "/Body", "Body/Pad", "Body\\Pad", "C:Pad", "A\x00B", "A\x7fB",
                   "A\u200bB", "A\ud800B", None, True, 1)
        for field in ("body", "pad"):
            for name in invalid:
                value = fixture()
                value[field] = name
                with self.subTest(field=field, name_type=type(name).__name__):
                    self.assert_invalid(value)

    def test_expected_requires_the_complete_exact_primitive_contract(self):
        for expected in ({}, {"body": "Body"}, {"body": "Body", "pad": "Pad", "length_mm": 10, "approved": True},
                         [], True, "Body", {"body": "Body", "pad": "Pad", "length_mm": True},
                         {"body": " Body", "pad": "Pad", "length_mm": 10}):
            with self.subTest(expected_type=type(expected).__name__):
                self.assert_invalid(fixture(), expected)

    def test_arbitrary_objects_and_subclasses_are_not_inspected_or_coerced(self):
        class UntrustedObject:
            def __getattribute__(self, name):
                raise AssertionError("must not read arbitrary getters")
            def __str__(self):
                raise AssertionError("must not stringify untrusted data")
            def __float__(self):
                raise AssertionError("must not coerce numbers")
            def __iter__(self):
                raise AssertionError("must not iterate arbitrary objects")
            def __eq__(self, other):
                raise AssertionError("must not compare arbitrary objects")
        class DictSubclass(dict):
            def __iter__(self):
                raise AssertionError("must not read overridden mapping methods")
        class ListSubclass(list):
            def __iter__(self):
                raise AssertionError("must not read overridden list methods")
        class StringSubclass(str):
            def strip(self):
                raise AssertionError("must not call overridden string methods")
        class IntSubclass(int):
            def __float__(self):
                raise AssertionError("must not coerce numeric subclasses")
        class FloatSubclass(float):
            def __float__(self):
                raise AssertionError("must not coerce numeric subclasses")
        class UntrustedMeta(type):
            def __eq__(self, other):
                raise AssertionError("must not compare arbitrary metaclasses")
        class UntrustedNumeric(metaclass=UntrustedMeta):
            pass
        obj = UntrustedObject()
        numeric = fixture()
        numeric["length_mm"] = UntrustedNumeric()
        self.assert_invalid(numeric)
        for value in (obj, DictSubclass(fixture())):
            self.assert_invalid(value)
        for field in fixture():
            value = fixture()
            value[field] = obj
            with self.subTest(field=field):
                self.assert_invalid(value)
        for field, replacement in (("body", StringSubclass("Body")),
                                   ("pad", StringSubclass("Pad")),
                                   ("length_mm", FloatSubclass(10)),
                                   ("recompute_count", IntSubclass(2))):
            value = fixture()
            value[field] = replacement
            self.assert_invalid(value)
        value = fixture()
        value["geometry"]["pad"]["dimensions_mm"] = ListSubclass([1, 2, 3])
        self.assert_invalid(value)
        for expected in (obj, DictSubclass({"body": "Body", "pad": "Pad", "length_mm": 10})):
            self.assert_invalid(fixture(), expected)
        value = fixture()
        value[1] = value.pop("kind")
        self.assert_invalid(value)

    def test_cyclic_untrusted_containers_are_rejected_without_recursing(self):
        value = fixture()
        value["geometry"]["pad"]["dimensions_mm"][0] = value
        self.assert_invalid(value)
        value = fixture()
        value["geometry"] = value
        self.assert_invalid(value)


if __name__ == "__main__":
    unittest.main()
