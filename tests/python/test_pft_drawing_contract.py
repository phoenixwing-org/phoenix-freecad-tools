# SPDX-License-Identifier: Apache-2.0
"""Independent synthetic drawing DTOs; no native imports, documents or services."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools/pft_drawing_contract.py"
spec = importlib.util.spec_from_file_location("drawing_contract_fixture", PATH)
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def drawing_result(source="Source", page="PageDraft"):
    return {"kind": "drawing_draft", "page": page, "template": "TemplateDraft", "source": source,
            "views": [{"name": "FrontDraft", "kind": "front"}, {"name": "TopDraft", "kind": "top"},
                      {"name": "AxonometricDraft", "kind": "axonometric"}],
            "scale": 0.5, "projection": "first_angle", "paper": "A4_landscape",
            "projection_verification": "not_checked", "manual_review_required": True}


class DrawingContractTests(unittest.TestCase):
    def reject(self, result, expected=None):
        with self.assertRaises(ValueError) as failure:
            contract.validate_design_result(result, expected)
        self.assertEqual(str(failure.exception), "图纸草稿结果格式无效。")

    def test_exact_result_is_detached_and_bound_to_preview_source_and_page(self):
        value = drawing_result()
        result = contract.validate_design_result(value, {"source": "Source", "page": "PageDraft"})
        self.assertEqual(result, value)
        result["views"][0]["name"] = "ChangedCopy"
        self.assertEqual(value["views"][0]["name"], "FrontDraft")
        for request in ({"source": "Other", "page": "PageDraft"}, {"source": "Source", "page": "Other"},
                        {"source": "Source", "page": None}, {}, {"source": "Source", "page": "PageDraft", "extra": 1}):
            self.reject(value, request)

    def test_unknown_and_missing_keys_are_rejected_in_result_and_each_view(self):
        for path in (None, 0, 1, 2):
            for extra in (False, True):
                value = drawing_result()
                target = value if path is None else value["views"][path]
                if extra: target["unknown"] = "synthetic untrusted data"
                else: target.pop(next(iter(target)))
                with self.subTest(path=path, extra=extra): self.reject(value)

    def test_no_projection_or_manual_review_success_can_be_fabricated(self):
        for key, values in {"kind": ("drawing", None), "projection": ("third_angle",),
                "paper": ("A3",), "projection_verification": ("checked", True),
                "manual_review_required": (False, 1, "true")}.items():
            for replacement in values:
                value = drawing_result(); value[key] = replacement
                with self.subTest(key=key, value=replacement): self.reject(value)

    def test_scale_requires_finite_positive_number_without_boolean_coercion(self):
        for scale in (0, -1, True, None, "0.5", float("nan"), float("inf"), 10 ** 1000):
            value = drawing_result(); value["scale"] = scale
            self.reject(value)
        for scale in (1, 0.001, 0.5):
            value = drawing_result(); value["scale"] = scale
            self.assertEqual(contract.validate_design_result(value), value)

    def test_three_views_have_fixed_order_and_five_distinct_new_names(self):
        for change in ("missing", "extra", "order", "duplicate", "source", "page", "template"):
            value = drawing_result()
            if change == "missing": value["views"].pop()
            elif change == "extra": value["views"].append(deepcopy(value["views"][0]))
            elif change == "order": value["views"].reverse()
            elif change == "duplicate": value["views"][1]["name"] = value["views"][0]["name"]
            elif change == "source": value["views"][0]["name"] = value["source"]
            elif change == "page": value["views"][1]["name"] = value["page"]
            else: value["template"] = value["page"]
            with self.subTest(change=change): self.reject(value)

    def test_native_names_accept_unicode_codepoints_and_reject_paths_and_controls(self):
        for field in ("source", "page", "template"):
            for name in ("", ".", "..", " leading", "trailing ", "a/b", "a\\b", "a:b", "a\n", "a\u200b", "a\ud800", "字" * 65):
                value = drawing_result(); value[field] = name
                with self.subTest(field=field, name=repr(name)): self.reject(value)
        value = drawing_result(source="来源" + "😀" * 62, page="图纸页")
        self.assertEqual(contract.validate_design_result(value), value)

    def test_blocked_request_explicitly_allows_null_page_but_never_a_success_binding(self):
        value = {"source": "Source", "page": None}
        self.assertEqual(contract.validate_design_request(value, allow_blocked=True), value)
        with self.assertRaises(ValueError): contract.validate_design_request(value)
        with self.assertRaises(ValueError): contract.validate_design_request({"source": "Source", "page": "Source"})

    def test_validator_does_not_stringify_or_read_native_like_values(self):
        class Forbidden:
            def __str__(self): raise AssertionError("must not stringify")
            def __getattribute__(self, name): raise AssertionError("must not inspect native values")
        for key in ("source", "page", "scale", "views"):
            value = drawing_result(); value[key] = Forbidden()
            self.reject(value)


if __name__ == "__main__": unittest.main()
