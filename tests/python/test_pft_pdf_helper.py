# SPDX-License-Identifier: Apache-2.0
"""Native facades and bounded stdio; a macOS-only neutral PDF clipping check."""
import base64
from copy import deepcopy
import ctypes
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools/pft_pdf_helper.py"
spec = importlib.util.spec_from_file_location("pdf_helper_fixture", PATH)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
contract = helper.contract
DATA = b"%PDF-neutral-helper-fixture"


class NativeFixture:
    def __init__(self, failure=None):
        self.calls, self.releases = [], []
        self.failure = failure
        self.is_encrypted, self.is_unlocked, self.count = False, True, 1
        self.media, self.angle, self.unit = [0.0, 0.0, 200.0, 100.0], 0, 1.0
        self.missing, self.transparent, self.release_failure = None, False, None

    def call(self, name, result=None):
        self.calls.append(name)
        if self.failure == name:
            raise RuntimeError("private-untrusted-native-diagnostic")
        return None if self.missing == name else result

    def release(self, name):
        self.releases.append(name)
        if self.release_failure == name:
            raise RuntimeError("private-untrusted-release-diagnostic")

    def create_data(self, data): return self.call("create_data", "data")
    def release_data(self, value): self.release(value)
    def create_provider(self, data): return self.call("create_provider", "provider")
    def release_provider(self, value): self.release(value)
    def create_document(self, provider): return self.call("create_document", "document")
    def release_document(self, value): self.release(value)
    def encrypted(self, document): return self.call("encrypted", self.is_encrypted)
    def unlocked(self, document): return self.call("unlocked", self.is_unlocked)
    def page_count(self, document): return self.call("page_count", self.count)
    def page(self, document): return self.call("page", "borrowed-page")
    def media_box(self, page): return self.call("media_box", deepcopy(self.media))
    def rotation(self, page): return self.call("rotation", self.angle)
    def user_unit(self, page): return self.call("user_unit", self.unit)
    def create_color_space(self): return self.call("create_color_space", "colorspace")
    def release_color_space(self, value): self.release(value)
    def create_bitmap(self, buffer, width, height, colorspace):
        self.buffer = buffer
        return self.call("create_bitmap", "bitmap")
    def release_bitmap(self, value): self.release(value)
    def draw(self, context, page, width, height, media, rotation):
        self.call("draw")
        self.buffer[:] = bytes([1, 2, 3, 0 if self.transparent else 255]) * (width * height)


def invoke(native):
    return json.loads(helper.process_request(contract.encode_pdf_request(DATA, 2, 1), native_factory=lambda: native))


def neutral_pdf(stream, *, origin=(0, 0), rotation=0):
    """Construct one independent page without PDF libraries or external files."""
    x, y = origin
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
               (f"<< /Type /Page /Parent 2 0 R /MediaBox [{x} {y} {x + 200} {y + 100}] "
                f"/Rotate {rotation} /Resources << >> /Contents 4 0 R >>").encode("ascii"),
               b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
               + stream + b"\nendstream"]
    data, offsets = bytearray(b"%PDF-1.4\n"), []
    for index, value in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(str(index).encode("ascii") + b" 0 obj\n" + value + b"\nendobj\n")
    start = len(data)
    data.extend(b"xref\n0 5\n0000000000 65535 f \n")
    for offset in offsets:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(b"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n"
                + str(start).encode("ascii") + b"\n%%EOF\n")
    return bytes(data)


class PdfHelperTests(unittest.TestCase):
    def test_success_releases_owned_objects_in_reverse_order_and_not_borrowed_page(self):
        native = NativeFixture()
        result = invoke(native)
        validated = contract.validate_pdf_response(result,
            input_sha256=contract.decode_pdf_request(contract.encode_pdf_request(DATA, 2, 1))["input_sha256"],
            byte_count=len(DATA), width=2, height=1)
        self.assertEqual(contract.response_pixels(validated), b"\x01\x02\x03\xff" * 2)
        self.assertEqual(native.releases, ["bitmap", "colorspace", "document", "provider", "data"])
        self.assertEqual(result["media_box_points"], native.media)

    def test_each_native_exception_is_fixed_and_releases_every_prior_resource(self):
        expected = {
            "create_data": [], "create_provider": ["data"], "create_document": ["provider", "data"],
            "encrypted": ["document", "provider", "data"], "unlocked": ["document", "provider", "data"],
            "page_count": ["document", "provider", "data"], "page": ["document", "provider", "data"],
            "media_box": ["document", "provider", "data"], "rotation": ["document", "provider", "data"],
            "user_unit": ["document", "provider", "data"], "create_color_space": ["document", "provider", "data"],
            "create_bitmap": ["colorspace", "document", "provider", "data"],
            "draw": ["bitmap", "colorspace", "document", "provider", "data"],
        }
        for name, releases in expected.items():
            with self.subTest(stage=name):
                native = NativeFixture(failure=name)
                self.assertEqual(invoke(native), {"ok": False, "code": "PDF_RENDER_FAILED"})
                self.assertEqual(native.releases, releases)

    def test_null_native_allocations_or_page_are_refused_without_invalid_releases(self):
        for name, code, releases in (
                ("create_data", "PDF_RENDER_FAILED", []),
                ("create_provider", "PDF_RENDER_FAILED", ["data"]),
                ("create_document", "PDF_INVALID_DOCUMENT", ["provider", "data"]),
                ("page", "PDF_INVALID_DOCUMENT", ["document", "provider", "data"]),
                ("create_color_space", "PDF_RENDER_FAILED", ["document", "provider", "data"]),
                ("create_bitmap", "PDF_RENDER_FAILED", ["colorspace", "document", "provider", "data"])):
            native = NativeFixture(); native.missing = name
            with self.subTest(stage=name):
                self.assertEqual(invoke(native), {"ok": False, "code": code})
                self.assertEqual(native.releases, releases)

    def test_encrypted_or_locked_document_never_reaches_page_or_pixel_readers(self):
        for encrypted, unlocked, code in ((True, False, "PDF_ENCRYPTED"), (True, True, "PDF_ENCRYPTED"),
                                         (False, False, "PDF_INVALID_DOCUMENT")):
            native = NativeFixture(); native.is_encrypted = encrypted; native.is_unlocked = unlocked
            self.assertEqual(invoke(native), {"ok": False, "code": code})
            self.assertNotIn("page_count", native.calls)
            self.assertNotIn("draw", native.calls)
            self.assertEqual(native.releases, ["document", "provider", "data"])

    def test_only_exactly_one_native_page_is_supported(self):
        for count in (0, 2, 100000, True, 1.0, None):
            native = NativeFixture(); native.count = count
            self.assertEqual(invoke(native), {"ok": False, "code": "PDF_PAGE_COUNT"})
            self.assertNotIn("page", native.calls)

    def test_invalid_paper_rotation_or_user_unit_is_refused_before_bitmap_allocation(self):
        for attribute, values, code in (
                ("media", ([0, 0, 0, 100], [0, 0, float("inf"), 100], [0, 0, 200, float("nan")]), "PDF_INVALID_PAPER"),
                ("angle", (-90, 360, 45, True, 0.0), "PDF_INVALID_PAPER"),
                ("unit", (0, 2, -1, True, float("nan"), float("inf"), "1"), "PDF_USER_UNIT_UNSUPPORTED")):
            for value in values:
                native = NativeFixture(); setattr(native, attribute, value)
                with self.subTest(attribute=attribute, value=repr(value)):
                    self.assertEqual(invoke(native), {"ok": False, "code": code})
                    self.assertNotIn("create_bitmap", native.calls)

    def test_release_failure_never_reports_success_and_still_releases_others(self):
        for name in ("bitmap", "colorspace", "document", "provider", "data"):
            native = NativeFixture(); native.release_failure = name
            self.assertEqual(invoke(native), {"ok": False, "code": "PDF_RENDER_FAILED"})
            self.assertEqual(native.releases, ["bitmap", "colorspace", "document", "provider", "data"])

    def test_pixels_must_remain_opaque_even_if_a_native_facade_reports_success(self):
        native = NativeFixture(); native.transparent = True
        self.assertEqual(invoke(native), {"ok": False, "code": "PDF_INVALID_RESPONSE"})
        self.assertEqual(native.releases, ["bitmap", "colorspace", "document", "provider", "data"])

    def test_bad_request_does_not_construct_or_call_native_backend(self):
        called = []
        response = helper.process_request(b'{"width":true}', native_factory=lambda: called.append(True))
        self.assertEqual(json.loads(response), {"ok": False, "code": "PDF_INVALID_REQUEST"})
        self.assertEqual(called, [])

    def test_main_performs_one_bounded_read_and_one_fixed_response(self):
        class Input:
            def __init__(self): self.reads = []
            def read(self, limit): self.reads.append(limit); return b"{}"
        source, output = Input(), io.BytesIO()
        helper.main(source, output)
        self.assertEqual(source.reads, [contract.MAX_REQUEST_BYTES + 1])
        self.assertEqual(json.loads(output.getvalue()), {"ok": False, "code": "PDF_INVALID_REQUEST"})
        self.assertNotIn(b"private-untrusted", output.getvalue())

    def test_main_oversized_request_and_io_exception_do_not_echo_diagnostics(self):
        class FailedInput:
            def read(self, limit): raise RuntimeError("private-untrusted-io-diagnostic")
        for source, code in ((io.BytesIO(b" " * (contract.MAX_REQUEST_BYTES + 1)), "PDF_INVALID_REQUEST"),
                             (FailedInput(), "PDF_RENDER_FAILED")):
            output = io.BytesIO(); helper.main(source, output)
            self.assertEqual(json.loads(output.getvalue()), {"ok": False, "code": code})

    def test_native_backend_initialization_is_lazy_and_platform_failure_is_fixed(self):
        with patch.object(helper.C, "CDLL", side_effect=AssertionError("must not load at import")) as loader:
            spec = importlib.util.spec_from_file_location("pdf_helper_no_native_import", PATH)
            loaded = importlib.util.module_from_spec(spec); spec.loader.exec_module(loaded)
            loader.assert_not_called()
        with patch.object(helper.sys, "platform", "unsupported"):
            self.assertEqual(json.loads(helper.process_request(contract.encode_pdf_request(DATA, 2, 1))),
                             {"ok": False, "code": "PDF_UNSUPPORTED_BACKEND"})

    def test_all_c_functions_have_explicit_abi_signatures(self):
        class Function:
            argtypes = restype = "unset"
        class Library:
            def __init__(self): self.functions = {}
            def __getattr__(self, name): return self.functions.setdefault(name, Function())
        libraries = [Library(), Library()]
        with patch.object(helper.sys, "platform", "darwin"), patch.object(helper.C, "CDLL", side_effect=libraries):
            native = helper._CoreGraphics()
        functions = {**native.cf.functions, **native.cg.functions}
        self.assertEqual(len(functions), 27)
        for name, function in functions.items():
            with self.subTest(function=name):
                self.assertIsInstance(function.argtypes, list)
                self.assertNotEqual(function.restype, "unset")
        self.assertIs(functions["CGPDFDocumentGetNumberOfPages"].restype, ctypes.c_size_t)
        self.assertIs(functions["CGPDFPageGetBoxRect"].restype, helper._Rect)
        self.assertIs(functions["CGPDFPageGetDrawingTransform"].restype, helper._Transform)
        self.assertIs(functions["CGPDFDocumentCreateWithProvider"].restype, ctypes.c_void_p)
        self.assertEqual(functions["CGContextClipToRect"].argtypes, [ctypes.c_void_p, helper._Rect])
        self.assertIsNone(functions["CGContextClipToRect"].restype)

    def test_native_draw_clips_original_media_coordinates_after_transform_before_paint(self):
        calls = []

        def rectangle(value):
            return (value.origin.x, value.origin.y, value.size.width, value.size.height)

        class Graphics:
            def CGContextSetRGBFillColor(self, *args): calls.append(("white", args))
            def CGContextFillRect(self, context, rect): calls.append(("fill", rectangle(rect)))
            def CGPDFPageGetDrawingTransform(self, page, box, target, angle, preserve):
                calls.append(("transform", (page, box, rectangle(target), angle, preserve)))
                return helper._Transform(1, 0, 0, 1, -10, -20)
            def CGContextConcatCTM(self, context, transform): calls.append(("concat", context))
            def CGContextClipToRect(self, context, rect): calls.append(("clip", (context, rectangle(rect))))
            def CGContextDrawPDFPage(self, context, page): calls.append(("draw", (context, page)))
            def CGContextFlush(self, context): calls.append(("flush", context))

        native = object.__new__(helper._CoreGraphics)
        native.cg = Graphics()
        native.draw(11, 12, 1000, 1000, [10, 20, 200, 100], 0)
        self.assertEqual([name for name, _ in calls],
                         ["white", "fill", "transform", "concat", "clip", "draw", "flush"])
        self.assertEqual(calls[1], ("fill", (0, 0, 1000, 1000)))
        self.assertEqual(calls[4], ("clip", (11, (10, 20, 200, 100))))
        self.assertEqual(calls[5], ("draw", (11, 12)))

    @unittest.skipUnless(sys.platform == "darwin", "CoreGraphics requires macOS")
    def test_native_media_box_clip_keeps_out_of_page_marks_out_of_white_margins(self):
        for origin, rotation in (((0, 0), 0), ((10, 20), 0), ((10, 20), 90)):
            with self.subTest(origin=origin, rotation=rotation):
                x, y = origin
                stream = (f"1 0 0 rg -1000 -1000 2000 2000 re f "
                          f"0 0 1 rg {x} {y} 200 100 re f").encode("ascii")
                data = neutral_pdf(stream, origin=origin, rotation=rotation)
                process = subprocess.run([sys.executable, "-I", str(PATH)],
                                         input=contract.encode_pdf_request(data, 100, 100),
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                self.assertEqual(process.returncode, 0)
                self.assertEqual(process.stderr, b"")
                result = contract.decode_pdf_response(process.stdout,
                    input_sha256=hashlib.sha256(data).hexdigest(), byte_count=len(data), width=100, height=100)
                self.assertIs(result["ok"], True)
                self.assertEqual(result["rotation_degrees"], rotation)
                self.assertEqual(result["media_box_points"], [x, y, 200, 100])
                pixels = contract.response_pixels(result)

                def pixel(x, y):
                    offset = (y * 100 + x) * 4
                    return pixels[offset:offset + 4]

                self.assertEqual(pixel(50, 50), b"\x00\x00\xff\xff")
                margins = ((10, 50), (90, 50)) if rotation == 90 else ((50, 10), (50, 90))
                for x, y in margins:
                    self.assertEqual(pixel(x, y), b"\xff\xff\xff\xff")
                self.assertNotIn(b"\xff\x00\x00\xff", (pixels[i:i + 4] for i in range(0, len(pixels), 4)))

    def test_present_non_numeric_user_unit_cannot_be_mistaken_for_absent_default(self):
        class Graphics:
            found, object_type, number, reads = True, 6, 1.0, 0
            def CGPDFPageGetDictionary(self, page): return 10
            def CGPDFDictionaryGetObject(self, dictionary, key, out):
                out._obj.value = 20
                return self.found
            def CGPDFObjectGetType(self, value): return self.object_type
            def CGPDFDictionaryGetNumber(self, dictionary, key, out):
                self.reads += 1; out._obj.value = self.number; return True
        native = object.__new__(helper._CoreGraphics); native.cg = Graphics()
        with self.assertRaises(contract.PftPdfError) as failure: native.user_unit(1)
        self.assertEqual(failure.exception.code, "PDF_USER_UNIT_UNSUPPORTED")
        self.assertEqual(native.cg.reads, 0)
        native.cg.found = False
        self.assertEqual(native.user_unit(1), 1.0)
        self.assertEqual(native.cg.reads, 0)
        native.cg.found, native.cg.object_type, native.cg.number = True, 3, 2.0
        self.assertEqual(native.user_unit(1), 2.0)

    def test_uniform_fit_scales_up_or_down_with_white_margins_and_native_rotation(self):
        for angle, raw, expected in (
                (0, (1, 0, 0, 1, 0, 0), (5, 0, 0, 5, 0, 250)),
                (90, (0, -1, 1, 0, 0, 200), (0, -5, 5, 0, 250, 1000)),
                (180, (-1, 0, 0, -1, 200, 100), (-5, 0, 0, -5, 1000, 750)),
                (270, (0, 1, -1, 0, 100, 0), (0, 5, -5, 0, 750, 0))):
            result = helper._fit_transform(helper._Transform(*raw), [0, 0, 200, 100], angle, 1000, 1000)
            self.assertEqual(tuple(getattr(result, name) for name, _ in helper._Transform._fields_), expected)
        result = helper._fit_transform(helper._Transform(1, 0, 0, 1, -10, -20),
                                       [10, 20, 200, 100], 0, 100, 100)
        self.assertEqual((result.a, result.d, result.tx, result.ty), (0.5, 0.5, -5, 15))

    def test_nonfinite_native_transform_or_overflowing_fit_is_refused(self):
        for raw, media in (((float("nan"), 0, 0, 1, 0, 0), [0, 0, 200, 100]),
                           ((1, 0, 0, 1, float("inf"), 0), [0, 0, 200, 100]),
                           ((1, 0, 0, 1, 0, 0), [0, 0, 5e-324, 5e-324])):
            with self.assertRaises(contract.PftPdfError) as failure:
                helper._fit_transform(helper._Transform(*raw), media, 0, 1000, 1000)
            self.assertEqual(failure.exception.code, "PDF_INVALID_PAPER")


if __name__ == "__main__":
    unittest.main()
