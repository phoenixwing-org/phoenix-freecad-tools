# SPDX-License-Identifier: Apache-2.0
"""Neutral detached protocol fixtures; no PDF parser or user data."""
import base64
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools/pft_pdf_contract.py"
spec = importlib.util.spec_from_file_location("pdf_contract_fixture", PATH)
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)
DATA = b"%PDF-neutral-protocol-fixture"
DIGEST = hashlib.sha256(DATA).hexdigest()


def success(width=2, height=1):
    return {"ok": True, "backend": "coregraphics", "input_sha256": DIGEST,
            "byte_count": len(DATA), "page_count": 1, "media_box_points": [10.0, 20.0, 200.0, 100.0],
            "rotation_degrees": 90, "user_unit": 1, "width": width, "height": height,
            "stride": width * 4, "pixel_format": "rgba8888-premultiplied",
            "pixels_base64": base64.b64encode(b"\x01\x02\x03\xff" * (width * height)).decode("ascii")}


def validate(value, **changes):
    expected = {"input_sha256": DIGEST, "byte_count": len(DATA), "width": 2, "height": 1}
    expected.update(changes)
    return contract.validate_pdf_response(value, **expected)


class PdfContractTests(unittest.TestCase):
    def reject(self, function, *args, code=None, **kwargs):
        with self.assertRaises(contract.PftPdfError) as failure:
            function(*args, **kwargs)
        if code is not None:
            self.assertEqual(failure.exception.code, code)
        self.assertNotIn("private-untrusted", str(failure.exception))

    def test_request_round_trip_keeps_exact_immutable_bytes_and_fixed_dimensions(self):
        wire = contract.encode_pdf_request(DATA, 1000, 1000)
        decoded = contract.decode_pdf_request(wire)
        self.assertEqual(decoded, {"data": DATA, "input_sha256": DIGEST, "width": 1000, "height": 1000})
        self.assertEqual(set(json.loads(wire)), {"pdf_base64", "input_sha256", "width", "height"})
        self.assertLessEqual(len(wire), contract.MAX_REQUEST_BYTES)

    def test_maximum_pdf_budget_is_supported_but_one_more_byte_is_refused(self):
        data = b"a" * contract.MAX_PDF_BYTES
        wire = contract.encode_pdf_request(data, 1, 1)
        self.assertEqual(contract.decode_pdf_request(wire)["data"], data)
        self.reject(contract.encode_pdf_request, data + b"a", 1, 1, code="PDF_INPUT_TOO_LARGE")

    def test_dimensions_reject_bool_fraction_axis_and_area_overflow(self):
        for width, height in ((True, 1), (1, False), (0, 1), (-1, 1), (1.0, 1), (1, "2"),
                              (2049, 1), (1, 2049), (1001, 1000), (2048, 2048)):
            with self.subTest(width=width, height=height):
                self.reject(contract.encode_pdf_request, DATA, width, height, code="PDF_INVALID_REQUEST")
        self.assertEqual(contract.decode_pdf_request(contract.encode_pdf_request(DATA, 2048, 1))["width"], 2048)

    def test_request_requires_bytes_exact_schema_and_bound_digest(self):
        for data in (None, b"", bytearray(DATA), memoryview(DATA), "private-untrusted"):
            self.reject(contract.encode_pdf_request, data, 1, 1)
        wire = json.loads(contract.encode_pdf_request(DATA, 2, 1))
        for mutation in (lambda v: v.update(extra="private-untrusted"), lambda v: v.pop("width"),
                         lambda v: v.update(input_sha256="0" * 64),
                         lambda v: v.update(input_sha256=DIGEST.upper()),
                         lambda v: v.update(pdf_base64=""), lambda v: v.update(width=True)):
            value = deepcopy(wire); mutation(value)
            self.reject(contract.decode_pdf_request, json.dumps(value).encode("utf8"))

    def test_strict_json_rejects_escaped_duplicate_keys_constants_and_nesting(self):
        for raw in (b'{"width":1,"w\\u0069dth":2}', b'{"ok":NaN}', b'{"ok":Infinity}',
                    b'\xef\xbb\xbf{}', b'{}{}', b'\xff', b'[' * 2000 + b']' * 2000,
                    b" " * (contract.MAX_REQUEST_BYTES + 1)):
            with self.subTest(size=len(raw)):
                self.reject(contract.decode_pdf_request, raw, code="PDF_INVALID_REQUEST")

    def test_base64_must_be_canonical_without_whitespace_or_ambiguous_padding(self):
        value = json.loads(contract.encode_pdf_request(b"f", 1, 1))
        for encoded in ("Zh==", "Zg===", "Zg==\n", " Zg==", "_w==", "\ud800"):
            value["pdf_base64"] = encoded
            self.reject(contract.decode_pdf_request, json.dumps(value).encode("ascii"))

    def test_success_result_is_detached_and_binds_input_digest_bytes_and_canvas(self):
        value = success()
        result = validate(value)
        self.assertEqual(result, value)
        result["media_box_points"][0] = 999
        self.assertEqual(value["media_box_points"][0], 10)
        for changes in ({"input_sha256": "0" * 64}, {"byte_count": len(DATA) + 1},
                        {"width": 1}, {"height": 2}):
            self.reject(validate, value, **changes, code="PDF_INVALID_RESPONSE")

    def test_success_exact_literals_numeric_types_and_nonfinite_values_are_rejected(self):
        for key, replacements in {
            "ok": (1, "true"), "backend": ("qtsvg", None), "byte_count": (True, 0, contract.MAX_PDF_BYTES + 1),
            "page_count": (True, 0, 2), "rotation_degrees": (True, 90.0, -90, 360),
            "user_unit": (True, 2, float("nan"), float("inf")), "width": (True, 1.0),
            "stride": (True, 7, 8.0), "pixel_format": ("argb32", "rgba8888"),
            "input_sha256": ("a" * 63, "a" * 64 + "\n"),
        }.items():
            for replacement in replacements:
                value = success(); value[key] = replacement
                with self.subTest(key=key, value=repr(replacement)):
                    self.reject(validate, value, code="PDF_INVALID_RESPONSE")
        value = success(); value["unknown"] = "private-untrusted"; self.reject(validate, value)
        value = success(); value.pop("stride"); self.reject(validate, value)

    def test_media_box_requires_four_finite_numbers_and_positive_extents(self):
        for media in (None, (0, 0, 1, 1), [0, 0, 1], [0, 0, 0, 1], [0, 0, 1, -1],
                      [True, 0, 1, 1], [0, 0, "1", 1], [0, 0, float("nan"), 1],
                      [0, 0, float("inf"), 1], [10 ** 1000, 0, 1, 1],
                      [1e308, 0, 1e308, 1]):
            value = success(); value["media_box_points"] = media
            self.reject(validate, value, code="PDF_INVALID_RESPONSE")

    def test_pixels_self_validate_complete_structure_budget_byte_count_and_opacity(self):
        value = success()
        self.assertEqual(contract.response_pixels(value), b"\x01\x02\x03\xff" * 2)
        for pixel_bytes in (b"", b"\xff" * 4, b"\xff" * 12, b"\x01\x02\x03\x00" * 2):
            value = success(); value["pixels_base64"] = base64.b64encode(pixel_bytes).decode("ascii")
            self.reject(contract.response_pixels, value, code="PDF_INVALID_RESPONSE")
        value = success(); value["width"] = 1001; value["height"] = 1000
        self.reject(contract.response_pixels, value)
        self.reject(contract.response_pixels, {"pixels_base64": "private-untrusted"})
        self.reject(contract.response_pixels, contract.error_response("PDF_ENCRYPTED"))

    def test_maximum_opaque_raster_fits_the_wire_response_budget(self):
        value = success(1000, 1000)
        raw = contract.encode_pdf_response(value)
        self.assertLess(len(raw), contract.MAX_RESPONSE_BYTES)
        result = contract.decode_pdf_response(raw, input_sha256=DIGEST, byte_count=len(DATA), width=1000, height=1000)
        self.assertEqual(len(contract.response_pixels(result)), 4_000_000)

    def test_failure_has_only_fixed_code_and_unknown_code_becomes_generic(self):
        for code in contract.PDF_ERROR_CODES:
            response = contract.error_response(code)
            self.assertEqual(validate(response), {"ok": False, "code": code})
            self.assertEqual(contract.PftPdfError(code).code, code)
        for value in ("private-untrusted", None, 1, []):
            error = contract.PftPdfError(value)
            self.assertEqual(error.code, "PDF_RENDER_FAILED")
            self.assertNotIn("private-untrusted", str(error))
        self.assertEqual(validate({"ok": False, "code": "private-untrusted"}),
                         {"ok": False, "code": "PDF_RENDER_FAILED"})
        for value in ({"ok": False, "code": "PDF_ENCRYPTED", "message": "private-untrusted"},
                      {"ok": False}, {"ok": False, "code": True}):
            self.reject(validate, value)

    def test_response_json_rejects_escaped_duplicates_trailing_data_and_large_output(self):
        for raw in (b'{"ok":false,"code":"PDF_ENCRYPTED","co\\u0064e":"PDF_PAGE_COUNT"}',
                    b'{"ok":false,"code":"PDF_ENCRYPTED"}{}', b'{"ok":NaN}',
                    b" " * (contract.MAX_RESPONSE_BYTES + 1)):
            self.reject(contract.decode_pdf_response, raw, input_sha256=DIGEST,
                        byte_count=len(DATA), width=2, height=1, code="PDF_INVALID_RESPONSE")

    def test_untrusted_containers_are_not_stringified_or_inspected(self):
        class Forbidden:
            def __str__(self): raise AssertionError("must not stringify")
            def __getattribute__(self, name): raise AssertionError("must not inspect")
        for key in ("media_box_points", "pixels_base64", "width", "backend", "input_sha256"):
            value = success(); value[key] = Forbidden()
            self.reject(validate, value)


if __name__ == "__main__":
    unittest.main()
