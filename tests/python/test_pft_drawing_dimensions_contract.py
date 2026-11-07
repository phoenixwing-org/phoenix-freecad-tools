# SPDX-License-Identifier: Apache-2.0
"""Independent synthetic dimension DTOs; no native imports or user data."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools/pft_drawing_dimensions_contract.py"
spec = importlib.util.spec_from_file_location("drawing_dimensions_contract_fixture", PATH)
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def dimension_request():
    return {"page": "DrawingPage", "view": "TopView",
            "dimensions": [{"edge": "Edge0", "kind": "Distance", "x_mm": -12, "y_mm": 20.5},
                           {"edge": "Edge17", "kind": "Diameter", "x_mm": 30, "y_mm": -25}]}


def dimension_result(request=None):
    request = dimension_request() if request is None else request
    rows = [dict(row, name="Dimension" + str(index), value_mm=5 + index * 2.5)
            for index, row in enumerate(request["dimensions"])]
    return {"kind": "drawing_dimensions", "page": request["page"], "view": request["view"],
            "dimensions": rows, "measurement_verification": "native_reference_and_value_checked",
            "projection_verification": "not_checked", "manual_review_required": True}


class DrawingDimensionsContractTests(unittest.TestCase):
    def reject_request(self, value):
        with self.assertRaises(ValueError) as failure:
            contract.validate_request(value)
        self.assertEqual(str(failure.exception), "图纸尺寸数据格式无效。")

    def reject_result(self, value, expected=None):
        with self.assertRaises(ValueError) as failure:
            contract.validate_result(value, expected)
        self.assertEqual(str(failure.exception), "图纸尺寸数据格式无效。")

    def test_capabilities_are_exact_and_preserve_unverified_projection(self):
        self.assertEqual(contract.KINDS, ("Distance", "DistanceX", "DistanceY", "Diameter", "Radius"))
        self.assertEqual(contract.CAPABILITIES, {
            "version": 1, "operation": "add_drawing_dimensions", "confirmation": "native_gui_only",
            "preview_method": "prepare_part_changes", "request_method": "request_part_change_approval",
            "status_method": "get_part_change_status", "kinds": list(contract.KINDS), "max_dimensions": 8,
            "edge_index_base": 0, "maximum_edge_index": 2047, "position_scope": "view_relative_paper_mm",
            "measurement_unit": "mm", "saves_document": False, "projection_verification": "not_checked"})

    def test_valid_request_and_result_are_detached_from_caller_and_each_other(self):
        request = dimension_request()
        result = dimension_result(request)
        request_before, result_before = deepcopy(request), deepcopy(result)
        checked_request = contract.validate_request(request)
        checked_result = contract.validate_result(result, expected=request)
        self.assertEqual(checked_request, request)
        self.assertEqual(checked_result, result)
        self.assertIsNot(checked_request, request)
        self.assertIsNot(checked_result["dimensions"], result["dimensions"])
        checked_request["dimensions"][0]["edge"] = "Edge9"
        checked_result["dimensions"][0]["value_mm"] = 99
        self.assertEqual(request, request_before)
        self.assertEqual(result, result_before)

    def test_unknown_or_missing_keys_at_both_levels_never_pass(self):
        for is_result in (False, True):
            original = dimension_result() if is_result else dimension_request()
            reject = self.reject_result if is_result else self.reject_request
            for path in (None, 0, 1):
                target = original if path is None else original["dimensions"][path]
                for missing in target:
                    value = deepcopy(original)
                    (value if path is None else value["dimensions"][path]).pop(missing)
                    with self.subTest(result=is_result, path=path, missing=missing): reject(value)
                value = deepcopy(original)
                (value if path is None else value["dimensions"][path])["unknown"] = "untrusted synthetic text"
                reject(value)

    def test_rows_are_bounded_lists_of_exact_mappings(self):
        class DictSubclass(dict):
            pass
        class ListSubclass(list):
            pass
        for rows in (None, (), {}, [], ListSubclass(dimension_request()["dimensions"]),
                     [None], [DictSubclass(dimension_request()["dimensions"][0])]):
            value = dimension_request(); value["dimensions"] = rows
            self.reject_request(value)
        self.reject_request(DictSubclass(dimension_request()))
        for count in (1, 8):
            request = dimension_request()
            request["dimensions"] = [{"edge": "Edge" + str(index), "kind": "Distance", "x_mm": 0, "y_mm": 0}
                                     for index in range(count)]
            self.assertEqual(contract.validate_request(request), request)
            self.assertEqual(contract.validate_result(dimension_result(request), request), dimension_result(request))
        request["dimensions"].append({"edge": "Edge8", "kind": "Radius", "x_mm": 0, "y_mm": 0})
        self.reject_request(request)
        self.reject_result(dimension_result(request))

    def test_edge_names_use_zero_based_canonical_decimal_and_hard_limit(self):
        invalid = ("Edge", "Edge00", "Edge01", "Edge0000", "Edge-1", "Edge+1", "Edge1.0", "edge0",
                   "Edge 1", " Edge1", "Edge1 ", "Edge1\n", "Edge1\r", "Edge1\x00", "Edge1\u200b",
                   "Edge2048", "Edge9999", "Edge10000", "Edge" + "1" * 5000, 0, True, None)
        for edge in invalid:
            request = dimension_request(); request["dimensions"][0]["edge"] = edge
            with self.subTest(edge=repr(edge)[:80]): self.reject_request(request)
        for edge in ("Edge0", "Edge1", "Edge2047"):
            request = dimension_request(); request["dimensions"][0]["edge"] = edge
            self.assertEqual(contract.validate_request(request), request)

    def test_native_kinds_are_exact_and_not_arbitrary_measurement_modes(self):
        for kind in ("distance", "DistanceZ", "Angle", "Area", "True", "Distance ", "Distance\n", True, None):
            request = dimension_request(); request["dimensions"][0]["kind"] = kind
            with self.subTest(kind=kind): self.reject_request(request)
        for kind in contract.KINDS:
            request = dimension_request(); request["dimensions"][0]["kind"] = kind
            self.assertEqual(contract.validate_request(request), request)

    def test_duplicate_edge_and_kind_is_rejected_even_with_different_positions(self):
        request = dimension_request()
        request["dimensions"][1] = dict(request["dimensions"][0], x_mm=900)
        self.reject_request(request)
        self.reject_result(dimension_result(request))
        request["dimensions"][1]["kind"] = "DistanceX"
        self.assertEqual(contract.validate_request(request), request)
        request["dimensions"][1].update(edge="Edge1", kind="Distance")
        self.assertEqual(contract.validate_request(request), request)

    def test_positions_require_finite_native_numbers_and_inclusive_bounds(self):
        class IntSubclass(int):
            pass
        class FloatSubclass(float):
            pass
        invalid = (True, False, None, "1", float("nan"), float("inf"), -float("inf"),
                   -1000.0001, 1000.0001, 10 ** 1000, -(10 ** 1000), IntSubclass(1), FloatSubclass(1))
        for key in ("x_mm", "y_mm"):
            for number in invalid:
                request = dimension_request(); request["dimensions"][0][key] = number
                self.reject_request(request)
                result = dimension_result(); result["dimensions"][0][key] = number
                self.reject_result(result)
            for number in (-1000, -1000.0, 0, -0.0, 999.5, 1000):
                request = dimension_request(); request["dimensions"][0][key] = number
                checked = contract.validate_request(request)["dimensions"][0][key]
                self.assertEqual(checked, number)
                self.assertIs(type(checked), type(number))

    def test_value_is_finite_positive_mm_without_boolean_coercion(self):
        for number in (True, False, None, "5", 0, -0.0, -1, 1e9 + 1, float("nan"), float("inf"), 10 ** 1000):
            result = dimension_result(); result["dimensions"][0]["value_mm"] = number
            with self.subTest(number=repr(number)[:80]): self.reject_result(result)
        for number in (1, 1e-12, 1e9):
            result = dimension_result(); result["dimensions"][0]["value_mm"] = number
            self.assertEqual(contract.validate_result(result), result)

    def test_owner_and_dimension_names_share_native_unicode_rules(self):
        invalid = ("", ".", "..", " leading", "trailing ", "a/b", "a\\b", "a:b", "a\n", "a\u200b", "a\ud800", "字" * 65, True)
        for key in ("page", "view", "name"):
            for name in invalid:
                result = dimension_result()
                (result["dimensions"][0] if key == "name" else result)[key] = name
                with self.subTest(key=key, name=repr(name)): self.reject_result(result)
        request = dimension_request(); request["page"] = "页" + "😀" * 63; request["view"] = "俯视"
        self.assertEqual(contract.validate_request(request), request)
        self.assertEqual(contract.validate_result(dimension_result(request), request), dimension_result(request))
        request["view"] = request["page"]
        self.reject_request(request)
        self.reject_result(dimension_result(request))

    def test_new_dimension_names_are_distinct_and_cannot_alias_existing_owners(self):
        for name in ("DrawingPage", "TopView", "Dimension0"):
            result = dimension_result(); result["dimensions"][1]["name"] = name
            with self.subTest(name=name): self.reject_result(result)

    def test_expected_request_binds_page_view_count_order_kind_edge_and_exact_position(self):
        request = dimension_request()
        for change in ("page", "view", "count", "order", "kind", "edge", "x_mm", "y_mm"):
            result = dimension_result(request)
            if change in ("page", "view"): result[change] = "DifferentOwner"
            elif change == "count": result["dimensions"].pop()
            elif change == "order": result["dimensions"].reverse()
            elif change == "kind": result["dimensions"][0]["kind"] = "DistanceX"
            elif change == "edge": result["dimensions"][0]["edge"] = "Edge1"
            else: result["dimensions"][0][change] += 1e-10
            with self.subTest(change=change): self.reject_result(result, request)
        for expected in ({}, {"page": "DrawingPage", "view": "TopView", "dimensions": []},
                         dict(request, unexpected=True), False):
            self.reject_result(dimension_result(), expected)

    def test_success_literals_cannot_claim_projection_or_manufacturing_completion(self):
        changes = {"kind": ("drawing_draft", "dimensions", None),
                   "measurement_verification": ("not_checked", "complete", True),
                   "projection_verification": ("checked", "ready", True),
                   "manual_review_required": (False, 1, "true", None)}
        for key, replacements in changes.items():
            for replacement in replacements:
                result = dimension_result(); result[key] = replacement
                with self.subTest(key=key, replacement=replacement): self.reject_result(result)

    def test_no_untrusted_object_is_stringified_or_inspected_or_coerced(self):
        class Forbidden:
            def __str__(self): raise AssertionError("must not stringify")
            def __float__(self): raise AssertionError("must not coerce")
            def __getattribute__(self, name): raise AssertionError("must not inspect attributes")
        for key in ("page", "view", "dimensions"):
            request = dimension_request(); request[key] = Forbidden()
            self.reject_request(request)
        for key in ("edge", "kind", "x_mm", "y_mm"):
            request = dimension_request(); request["dimensions"][0][key] = Forbidden()
            self.reject_request(request)
        for key in ("name", "value_mm"):
            result = dimension_result(); result["dimensions"][0][key] = Forbidden()
            self.reject_result(result)

    def test_auto_intent_is_explicit_opt_in_and_manual_contract_stays_unchanged(self):
        intent = dimension_request()
        intent["dimensions"][0] = {"edge": "Edge0", "kind": "Distance", "placement": "auto"}
        before = deepcopy(intent)
        checked = contract.validate_intent(intent)
        self.assertEqual(checked, intent)
        checked["dimensions"][0]["edge"] = "Edge2"
        self.assertEqual(intent, before)
        self.reject_request(intent)
        self.assertEqual(contract.validate_intent(dimension_request()), dimension_request())
        for row in ({"edge": "Edge0", "kind": "Distance"},
                    {"edge": "Edge0", "kind": "Distance", "x_mm": 0},
                    {"edge": "Edge0", "kind": "Distance", "placement": "explicit"},
                    {"edge": "Edge0", "kind": "Distance", "placement": True},
                    {"edge": "Edge0", "kind": "Distance", "placement": "auto", "x_mm": 0, "y_mm": 1},
                    {"edge": "Edge0", "kind": "Distance", "placement": "auto", "extra": 1}):
            intent["dimensions"][0] = row
            with self.subTest(row=row), self.assertRaises(ValueError):
                contract.validate_intent(intent)

    def test_auto_intent_keeps_canonical_names_kinds_batch_and_duplicate_limits(self):
        auto = {"edge": "Edge0", "kind": "Distance", "placement": "auto"}
        for row in (dict(auto, edge="Edge00"), dict(auto, edge="Edge2048"),
                    dict(auto, kind="DistanceZ"), dict(auto, kind=True),
                    dict(auto, edge="Edge0\n")):
            intent = dimension_request(); intent["dimensions"] = [row]
            with self.assertRaises(ValueError): contract.validate_intent(intent)
        for rows in ([], [auto] * 9, [auto, dict(auto, x_mm=1, y_mm=2)],
                     [auto, {"edge": "Edge0", "kind": "Distance", "x_mm": 1, "y_mm": 2}],
                     (auto,)):
            intent = dimension_request(); intent["dimensions"] = rows
            with self.assertRaises(ValueError): contract.validate_intent(intent)
        intent = dimension_request()
        intent["dimensions"] = [dict(auto, edge="Edge" + str(i), kind=contract.KINDS[i % 5]) for i in range(8)]
        self.assertEqual(contract.validate_intent(intent), intent)

    def test_resolution_binds_complete_order_and_only_auto_rows_may_gain_positions(self):
        intent = dimension_request()
        intent["dimensions"][0] = {"edge": "Edge0", "kind": "Distance", "placement": "auto"}
        resolved = dimension_request()
        resolved["dimensions"][0].update(x_mm=45.25, y_mm=-18.75)
        self.assertEqual(contract.validate_resolution(intent, resolved), resolved)
        before = deepcopy(resolved)
        checked = contract.validate_resolution(intent, resolved)
        checked["dimensions"][0]["x_mm"] = 0
        self.assertEqual(resolved, before)
        for mode in ("page", "view", "count", "order", "edge", "kind", "explicit_x", "explicit_y", "auto_coords", "unresolved"):
            value = deepcopy(resolved)
            if mode in ("page", "view"): value[mode] = "DifferentOwner"
            elif mode == "count": value["dimensions"].pop()
            elif mode == "order": value["dimensions"].reverse()
            elif mode in ("edge", "kind"): value["dimensions"][0][mode] = "Edge1" if mode == "edge" else "DistanceX"
            elif mode.startswith("explicit"): value["dimensions"][1]["x_mm" if mode.endswith("x") else "y_mm"] += 1e-10
            elif mode == "auto_coords": value["dimensions"][0]["placement"] = "auto"
            else: value["dimensions"][0] = deepcopy(intent["dimensions"][0])
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                contract.validate_resolution(intent, value)


if __name__ == "__main__": unittest.main()
