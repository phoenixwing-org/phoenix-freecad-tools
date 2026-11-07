# SPDX-License-Identifier: Apache-2.0
"""One bounded PDF request per isolated helper process; no Qt or CAD imports.

The host owns process lifetime and timeouts. Byte and raster budgets do not
bound a native PDF parser's CPU or internal allocations. No passwords, paths,
filenames, PDF creation operations or diagnostic text are part of this protocol.
"""
import base64
import ctypes as C
import importlib.util
import math
from pathlib import Path
import sys

# -I removes the script directory from sys.path. Load only the adjacent shipped
# pure contract, without consulting PYTHONPATH or the user's site packages.
_spec = importlib.util.spec_from_file_location("_pft_pdf_helper_contract",
                                              Path(__file__).with_name("pft_pdf_contract.py"))
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)

_P, _D, _Z, _B, _I = C.c_void_p, C.c_double, C.c_size_t, C.c_bool, C.c_int


class _Point(C.Structure):
    _fields_ = (("x", _D), ("y", _D))


class _Size(C.Structure):
    _fields_ = (("width", _D), ("height", _D))


class _Rect(C.Structure):
    _fields_ = (("origin", _Point), ("size", _Size))


class _Transform(C.Structure):
    _fields_ = tuple((key, _D) for key in ("a", "b", "c", "d", "tx", "ty"))


def _rect(x, y, width, height):
    return _Rect(_Point(x, y), _Size(width, height))


def _bind(library, name, arguments, result):
    function = getattr(library, name)
    function.argtypes, function.restype = arguments, result


def _fit_transform(transform, media, rotation, width, height):
    """Uniformly fit a native 1:1 page transform; do not crop or rotate twice."""
    natural_width, natural_height = ((media[3], media[2]) if rotation in (90, 270)
                                     else (media[2], media[3]))
    scale = min(width / natural_width, height / natural_height)
    if not math.isfinite(scale) or scale <= 0:
        raise contract.PftPdfError("PDF_INVALID_PAPER")
    x = (width - natural_width * scale) / 2
    y = (height - natural_height * scale) / 2
    values = (transform.a * scale, transform.b * scale,
              transform.c * scale, transform.d * scale,
              transform.tx * scale + x, transform.ty * scale + y)
    if any(not math.isfinite(value) for value in values):
        raise contract.PftPdfError("PDF_INVALID_PAPER")
    return _Transform(*values)


class _CoreGraphics:
    """Explicit 64-bit macOS C ABI. Create/Copy results are owned; Get results borrowed."""
    def __init__(self):
        if (sys.platform != "darwin" or C.sizeof(_P) != 8 or C.sizeof(C.c_long) != 8
                or C.sizeof(_D) != 8):
            raise contract.PftPdfError("PDF_UNSUPPORTED_BACKEND")
        try:
            self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            self.cg = C.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            for name, args, result in (
                    ("CFDataCreate", [_P, C.POINTER(C.c_ubyte), C.c_long], _P),
                    ("CFRelease", [_P], None)):
                _bind(self.cf, name, args, result)
            for name, args, result in (
                    ("CGDataProviderCreateWithCFData", [_P], _P),
                    ("CGDataProviderRelease", [_P], None),
                    ("CGPDFDocumentCreateWithProvider", [_P], _P),
                    ("CGPDFDocumentRelease", [_P], None),
                    ("CGPDFDocumentIsEncrypted", [_P], _B),
                    ("CGPDFDocumentIsUnlocked", [_P], _B),
                    ("CGPDFDocumentGetNumberOfPages", [_P], _Z),
                    ("CGPDFDocumentGetPage", [_P, _Z], _P),
                    ("CGPDFPageGetBoxRect", [_P, C.c_int32], _Rect),
                    ("CGPDFPageGetRotationAngle", [_P], _I),
                    ("CGPDFPageGetDrawingTransform", [_P, C.c_int32, _Rect, _I, _B], _Transform),
                    ("CGPDFPageGetDictionary", [_P], _P),
                    ("CGPDFDictionaryGetObject", [_P, C.c_char_p, C.POINTER(_P)], _B),
                    ("CGPDFObjectGetType", [_P], C.c_int32),
                    ("CGPDFDictionaryGetNumber", [_P, C.c_char_p, C.POINTER(_D)], _B),
                    ("CGColorSpaceCreateDeviceRGB", [], _P),
                    ("CGColorSpaceRelease", [_P], None),
                    ("CGBitmapContextCreate", [_P, _Z, _Z, _Z, _Z, _P, C.c_uint32], _P),
                    ("CGContextSetRGBFillColor", [_P, _D, _D, _D, _D], None),
                    ("CGContextFillRect", [_P, _Rect], None),
                    ("CGContextConcatCTM", [_P, _Transform], None),
                    ("CGContextClipToRect", [_P, _Rect], None),
                    ("CGContextDrawPDFPage", [_P, _P], None),
                    ("CGContextFlush", [_P], None),
                    ("CGContextRelease", [_P], None)):
                _bind(self.cg, name, args, result)
        except (OSError, AttributeError):
            raise contract.PftPdfError("PDF_UNSUPPORTED_BACKEND") from None

    def create_data(self, data):
        storage = (C.c_ubyte * len(data)).from_buffer_copy(data)
        # CFDataCreate copies storage; no Python callback or borrowed buffer.
        return self.cf.CFDataCreate(None, storage, len(data))

    def release_data(self, value):
        self.cf.CFRelease(value)

    def create_provider(self, data):
        return self.cg.CGDataProviderCreateWithCFData(data)

    def release_provider(self, value):
        self.cg.CGDataProviderRelease(value)

    def create_document(self, provider):
        return self.cg.CGPDFDocumentCreateWithProvider(provider)

    def release_document(self, value):
        self.cg.CGPDFDocumentRelease(value)

    def encrypted(self, document):
        return bool(self.cg.CGPDFDocumentIsEncrypted(document))

    def unlocked(self, document):
        return bool(self.cg.CGPDFDocumentIsUnlocked(document))

    def page_count(self, document):
        return int(self.cg.CGPDFDocumentGetNumberOfPages(document))

    def page(self, document):
        return self.cg.CGPDFDocumentGetPage(document, 1)  # one-based, borrowed

    def media_box(self, page):
        box = self.cg.CGPDFPageGetBoxRect(page, 0)  # kCGPDFMediaBox
        return [box.origin.x, box.origin.y, box.size.width, box.size.height]

    def rotation(self, page):
        return int(self.cg.CGPDFPageGetRotationAngle(page))

    def user_unit(self, page):
        dictionary = self.cg.CGPDFPageGetDictionary(page)  # borrowed
        if not dictionary:
            raise contract.PftPdfError("PDF_INVALID_DOCUMENT")
        entry = _P()
        if not self.cg.CGPDFDictionaryGetObject(dictionary, b"UserUnit", C.byref(entry)):
            return 1.0
        # A present non-number must not be mistaken for an absent default.
        if not entry or self.cg.CGPDFObjectGetType(entry) not in (3, 4):
            raise contract.PftPdfError("PDF_USER_UNIT_UNSUPPORTED")
        value = _D()
        if not self.cg.CGPDFDictionaryGetNumber(dictionary, b"UserUnit", C.byref(value)):
            raise contract.PftPdfError("PDF_USER_UNIT_UNSUPPORTED")
        return value.value

    def create_color_space(self):
        return self.cg.CGColorSpaceCreateDeviceRGB()

    def release_color_space(self, value):
        self.cg.CGColorSpaceRelease(value)

    def create_bitmap(self, buffer, width, height, colorspace):
        # PremultipliedLast | ByteOrder32Big: address-order RGBA on either endian.
        return self.cg.CGBitmapContextCreate(buffer, width, height, 8, width * 4,
                                             colorspace, 1 | (4 << 12))

    def release_bitmap(self, value):
        self.cg.CGContextRelease(value)

    def draw(self, context, page, width, height, media, rotation):
        target = _rect(0, 0, width, height)
        self.cg.CGContextSetRGBFillColor(context, 1, 1, 1, 1)
        self.cg.CGContextFillRect(context, target)
        natural_width, natural_height = ((media[3], media[2]) if rotation in (90, 270)
                                         else (media[2], media[3]))
        # GetDrawingTransform only scales down. First obtain the native 1:1
        # /Rotate and origin transform, then fit uniformly in either direction.
        transform = self.cg.CGPDFPageGetDrawingTransform(
            page, 0, _rect(0, 0, natural_width, natural_height), 0, True)
        transform = _fit_transform(transform, media, rotation, width, height)
        # The native transform already includes /Rotate; no second rotation
        # or Y flip. The aspect-ratio fit retains the complete MediaBox.
        self.cg.CGContextConcatCTM(context, transform)
        # PDF content streams may paint outside their page box. Clip in the
        # transformed page coordinates so those marks cannot enter the margins.
        self.cg.CGContextClipToRect(context, _rect(*media))
        self.cg.CGContextDrawPDFPage(context, page)
        self.cg.CGContextFlush(context)


def _render(request, native):
    owned = []

    def own(value, release, code="PDF_RENDER_FAILED"):
        if not value:
            raise contract.PftPdfError(code)
        owned.append((release, value))
        return value

    try:
        data = own(native.create_data(request["data"]), native.release_data)
        provider = own(native.create_provider(data), native.release_provider)
        document = own(native.create_document(provider), native.release_document, "PDF_INVALID_DOCUMENT")
        if native.encrypted(document) is not False:
            raise contract.PftPdfError("PDF_ENCRYPTED")
        if native.unlocked(document) is not True:
            raise contract.PftPdfError("PDF_INVALID_DOCUMENT")
        count = native.page_count(document)
        if type(count) is not int or count != 1:
            raise contract.PftPdfError("PDF_PAGE_COUNT")
        page = native.page(document)  # borrowed until document release
        if not page:
            raise contract.PftPdfError("PDF_INVALID_DOCUMENT")
        media = contract.validate_media_box(native.media_box(page))
        rotation = native.rotation(page)
        if type(rotation) is not int or rotation not in (0, 90, 180, 270):
            raise contract.PftPdfError("PDF_INVALID_PAPER")
        user_unit = native.user_unit(page)
        if type(user_unit) not in (int, float) or not math.isfinite(user_unit) or user_unit != 1:
            raise contract.PftPdfError("PDF_USER_UNIT_UNSUPPORTED")
        width, height = request["width"], request["height"]
        # The host's fixed canvas may contain white margins; it never crops.
        buffer = (C.c_ubyte * (width * height * 4))()
        colorspace = own(native.create_color_space(), native.release_color_space)
        context = own(native.create_bitmap(buffer, width, height, colorspace), native.release_bitmap)
        native.draw(context, page, width, height, media, rotation)
        pixels = bytes(buffer)  # owned copy before native bitmap release
        return {"ok": True, "backend": "coregraphics", "input_sha256": request["input_sha256"],
                "byte_count": len(request["data"]), "page_count": 1, "media_box_points": media,
                "rotation_degrees": rotation, "user_unit": 1, "width": width, "height": height,
                "stride": width * 4, "pixel_format": contract.PIXEL_FORMAT,
                "pixels_base64": base64.b64encode(pixels).decode("ascii")}
    finally:
        failed = False
        for release, value in reversed(owned):
            try:
                release(value)
            except Exception:
                failed = True
        if failed:
            raise contract.PftPdfError("PDF_RENDER_FAILED")


def process_request(raw, *, native_factory=None):
    """Return only a strict protocol response; facade injection is for pure tests."""
    try:
        request = contract.decode_pdf_request(raw)
        native = (native_factory or _CoreGraphics)()
        value = _render(request, native)
        value = contract.validate_pdf_response(value, input_sha256=request["input_sha256"],
                                               byte_count=len(request["data"]),
                                               width=request["width"], height=request["height"])
        return contract.encode_pdf_response(value)
    except contract.PftPdfError as error:
        return contract.encode_pdf_response(contract.error_response(error.code))
    except Exception:
        return contract.encode_pdf_response(contract.error_response("PDF_RENDER_FAILED"))


def main(stdin=None, stdout=None):
    """Read one bounded JSON value, then emit exactly one bounded response."""
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout.buffer if stdout is None else stdout
    try:
        raw = stdin.read(contract.MAX_REQUEST_BYTES + 1)
        response = process_request(raw)
    except Exception:
        response = contract.encode_pdf_response(contract.error_response("PDF_RENDER_FAILED"))
    stdout.write(response)
    stdout.flush()


if __name__ == "__main__":
    main()
