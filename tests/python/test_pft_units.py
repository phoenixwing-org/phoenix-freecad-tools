# SPDX-License-Identifier: Apache-2.0
"""Pure synthetic dimension signatures; no FreeCAD, Qt or native processes."""
import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools/pft_units.py"
spec = importlib.util.spec_from_file_location("pft_units_test", MODULE_PATH)
units = importlib.util.module_from_spec(spec)
spec.loader.exec_module(units)


class PftInternalUnitTests(unittest.TestCase):
    def assert_unit(self, signature, expected):
        self.assertEqual(units.pft_internal_unit(signature), {
            "unit": expected, "unit_signature": list(signature), "unit_system": "freecad_internal"})

    def test_each_native_base_dimension_uses_the_fixed_internal_basis(self):
        for index, symbol in enumerate(("mm", "kg", "s", "A", "K", "mol", "cd", "deg")):
            signature = [0] * 8
            signature[index] = 1
            with self.subTest(symbol=symbol):
                self.assert_unit(signature, symbol)
                self.assert_unit(tuple(signature), symbol)

    def test_dimensionless_signature_uses_one(self):
        self.assert_unit([0] * 8, "1")
        self.assert_unit((0,) * 8, "1")

    def test_composite_dimensions_follow_basis_order_with_explicit_products(self):
        for signature, text in (([2, 0, 0, 0, 0, 0, 0, 0], "mm^2"),
                                ([3, 0, 0, 0, 0, 0, 0, 0], "mm^3"),
                                ([1, 1, -2, 0, 0, 0, 0, 0], "mm*kg*s^-2"),
                                ([-3, 1, 0, 0, 0, 0, 0, 0], "mm^-3*kg"),
                                ([0, 1, -2, -1, 0, 0, 0, 0], "kg*s^-2*A^-1"),
                                ([1, 1, 1, 1, 1, 1, 1, 1], "mm*kg*s*A*K*mol*cd*deg")):
            with self.subTest(signature=signature):
                self.assert_unit(signature, text)

    def test_negative_exponents_and_signed_native_boundaries_are_not_normalized(self):
        self.assert_unit([-1, 0, -1, 0, 0, 0, 0, -1], "mm^-1*s^-1*deg^-1")
        self.assert_unit([-128, 127, 0, 0, 0, 0, 0, 0], "mm^-128*kg^127")
        self.assert_unit([0, 0, 0, 0, 0, 0, 0, -128], "deg^-128")

    def test_out_of_range_or_non_integer_powers_are_unknown(self):
        class IntegerSubclass(int):
            pass
        for value in (-129, 128, True, False, 1.0, "1", None, [], {}, IntegerSubclass(1)):
            for index in range(8):
                signature = [0] * 8
                signature[index] = value
                with self.subTest(index=index, value=value):
                    self.assertIsNone(units.pft_internal_unit(signature))

    def test_only_eight_dimensional_actual_lists_and_tuples_are_accepted(self):
        for signature in (None, False, True, 8, 8.0, "mm", "00000000", {}, {0}, range(8),
                          [], (), [0], [0] * 7, [0] * 9, (0,) * 7, (0,) * 9):
            with self.subTest(signature=signature):
                self.assertIsNone(units.pft_internal_unit(signature))

    def test_arbitrary_objects_are_not_stringified_or_inspected_for_units(self):
        class NativeLookingObject:
            def __getattribute__(self, name):
                raise AssertionError("must not introspect a native-looking object")
            def __str__(self):
                raise AssertionError("must not guess units from text")
            def __iter__(self):
                raise AssertionError("must not duck-type native dimensions")
        self.assertIsNone(units.pft_internal_unit(NativeLookingObject()))

    def test_malformed_native_container_is_unknown_without_guessing(self):
        class UnreadableList(list):
            def __iter__(self):
                raise RuntimeError("synthetic unavailable signature")
        self.assertIsNone(units.pft_internal_unit(UnreadableList([0] * 8)))
        cycle = []
        cycle.extend([cycle] * 8)
        self.assertIsNone(units.pft_internal_unit(cycle))

    def test_result_never_exposes_the_original_mutable_signature(self):
        signature = [1, 0, -2, 0, 0, 0, 0, 0]
        result = units.pft_internal_unit(signature)
        self.assertIsNot(result["unit_signature"], signature)
        signature[0] = 99
        self.assertEqual(result["unit_signature"], [1, 0, -2, 0, 0, 0, 0, 0])
        self.assertEqual(result["unit"], "mm*s^-2")
        result["unit_signature"][1] = 5
        self.assertEqual(signature[1], 0)
        second = units.pft_internal_unit((1, 0, -2, 0, 0, 0, 0, 0))
        self.assertIsNot(second["unit_signature"], result["unit_signature"])
        self.assertEqual(second["unit_signature"], [1, 0, -2, 0, 0, 0, 0, 0])


if __name__ == "__main__":
    unittest.main()
