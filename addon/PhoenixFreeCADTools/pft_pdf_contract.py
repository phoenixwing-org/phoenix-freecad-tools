# SPDX-License-Identifier: Apache-2.0
"""Bounded frozen-PDF helper protocol, independent of Qt and native libraries."""
import base64
import binascii
import hashlib
import json
import math
import re

MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 12 * 1024 * 1024
MAX_RESPONSE_BYTES = 6 * 1024 * 1024
MAX_PIXELS = 1_000_000
MAX_AXIS = 2048
PIXEL_FORMAT = "rgba8888-premultiplied"

_MESSAGES = {
    "PDF_INVALID_REQUEST": "The frozen PDF preview request is invalid.",
    "PDF_INPUT_TOO_LARGE": "The frozen PDF exceeds the local byte budget.",
    "PDF_INPUT_MISMATCH": "The frozen PDF bytes do not match the requested digest.",
    "PDF_INVALID_RESPONSE": "The PDF preview helper response is invalid.",
    "PDF_UNSUPPORTED_BACKEND": "A supported native PDF preview backend is unavailable.",
    "PDF_INVALID_DOCUMENT": "The frozen bytes could not be read as a PDF document.",
    "PDF_ENCRYPTED": "Encrypted PDF documents are not supported for this preview.",
    "PDF_PAGE_COUNT": "This preview requires exactly one PDF page.",
    "PDF_INVALID_PAPER": "The PDF page rectangle or rotation is unsupported.",
    "PDF_USER_UNIT_UNSUPPORTED": "The PDF page has an unsupported user unit.",
    "PDF_RENDER_FAILED": "The frozen PDF preview could not be rendered.",
    "PDF_BUSY": "A PDF preview is already in progress.",
    "PDF_CANCELLED": "The PDF preview was cancelled.",
    "PDF_TIMEOUT": "The PDF preview helper exceeded its time budget.",
    "PDF_PROCESS_FAILED": "The PDF preview helper did not finish successfully.",
    "PDF_WRITE_FAILED": "The frozen PDF request could not be sent to its helper.",
    "PDF_OUTPUT_TOO_LARGE": "The PDF preview helper exceeded its output budget.",
    "PDF_BACKEND_UNAVAILABLE": "A native PDF preview backend is unavailable.",
    "PDF_RUNTIME_UNAVAILABLE": "A trusted PDF preview helper runtime is unavailable.",
    "PDF_PROTOCOL_INVALID": "The PDF preview helper protocol is invalid.",
}
PDF_ERROR_CODES = frozenset(_MESSAGES)
_REQUEST_KEYS = frozenset(("pdf_base64", "input_sha256", "width", "height"))
_SUCCESS_KEYS = frozenset(("ok", "backend", "input_sha256", "byte_count", "page_count",
                           "media_box_points", "rotation_degrees", "user_unit", "width",
                           "height", "stride", "pixel_format", "pixels_base64"))


class PftPdfError(RuntimeError):
    """A fixed public error; native diagnostics and input data are never included."""
    def __init__(self, code):
        self.code = code if type(code) is str and code in PDF_ERROR_CODES else "PDF_RENDER_FAILED"
        self.message = _MESSAGES[self.code]
        super().__init__(self.message)


def _reject(code):
    raise PftPdfError(code)


def _mapping(value, keys, code):
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or frozenset(value) != keys):
        _reject(code)


def _dimensions(width, height, code):
    if (type(width) is not int or type(height) is not int
            or not 1 <= width <= MAX_AXIS or not 1 <= height <= MAX_AXIS
            or width * height > MAX_PIXELS):
        _reject(code)
    return width, height


def _digest(value, code):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        _reject(code)
    return value


def _base64(value, maximum, code):
    if type(value) is not str or len(value) > 4 * ((maximum + 2) // 3):
        _reject(code)
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        _reject(code)
    if len(result) > maximum or base64.b64encode(result).decode("ascii") != value:
        _reject(code)
    return result


def _loads(raw, maximum, code):
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        _reject(code)

    def pairs(values):
        value = {}
        for key, item in values:
            if key in value:
                _reject(code)
            value[key] = item
        return value

    def constant(_):
        _reject(code)

    try:
        return json.loads(raw.decode("utf8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        _reject(code)


def _dumps(value, maximum, code):
    try:
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
    except (TypeError, ValueError, OverflowError, RecursionError):
        _reject(code)
    if len(raw) > maximum:
        _reject(code)
    return raw


def encode_pdf_request(pdf_bytes, width, height):
    """Freeze one bytes value into the exact bounded wire request."""
    if type(pdf_bytes) is not bytes or not pdf_bytes:
        _reject("PDF_INVALID_REQUEST")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        _reject("PDF_INPUT_TOO_LARGE")
    _dimensions(width, height, "PDF_INVALID_REQUEST")
    return _dumps({"pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
                   "input_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                   "width": width, "height": height}, MAX_REQUEST_BYTES, "PDF_INVALID_REQUEST")


def decode_pdf_request(raw):
    """Validate JSON and its digest, returning only detached bytes and dimensions."""
    value = _loads(raw, MAX_REQUEST_BYTES, "PDF_INVALID_REQUEST")
    _mapping(value, _REQUEST_KEYS, "PDF_INVALID_REQUEST")
    width, height = _dimensions(value["width"], value["height"], "PDF_INVALID_REQUEST")
    digest = _digest(value["input_sha256"], "PDF_INVALID_REQUEST")
    data = _base64(value["pdf_base64"], MAX_PDF_BYTES, "PDF_INVALID_REQUEST")
    if not data:
        _reject("PDF_INVALID_REQUEST")
    if hashlib.sha256(data).hexdigest() != digest:
        _reject("PDF_INPUT_MISMATCH")
    return {"data": data, "input_sha256": digest, "width": width, "height": height}


def error_response(code):
    """Return a fixed failure without exposing exception text or input identity."""
    return {"ok": False, "code": PftPdfError(code).code}


def validate_media_box(value):
    """Check a native rectangle in default PDF user-space points; never convert units."""
    if type(value) is not list or len(value) != 4:
        _reject("PDF_INVALID_PAPER")
    try:
        if (any(type(number) not in (int, float) or not math.isfinite(number) for number in value)
                or value[2] <= 0 or value[3] <= 0
                or not math.isfinite(value[0] + value[2])
                or not math.isfinite(value[1] + value[3])):
            _reject("PDF_INVALID_PAPER")
    except OverflowError:
        _reject("PDF_INVALID_PAPER")
    return list(value)


def _response(value):
    code = "PDF_INVALID_RESPONSE"
    if type(value) is not dict or type(value.get("ok")) is not bool:
        _reject(code)
    if value["ok"] is False:
        _mapping(value, frozenset(("ok", "code")), code)
        if type(value["code"]) is not str:
            _reject(code)
        return error_response(value["code"]), None
    _mapping(value, _SUCCESS_KEYS, code)
    width, height = _dimensions(value["width"], value["height"], code)
    if (type(value["backend"]) is not str or value["backend"] != "coregraphics"
            or type(value["byte_count"]) is not int or not 1 <= value["byte_count"] <= MAX_PDF_BYTES
            or type(value["page_count"]) is not int or value["page_count"] != 1
            or type(value["rotation_degrees"]) is not int or value["rotation_degrees"] not in (0, 90, 180, 270)
            or type(value["user_unit"]) not in (int, float) or value["user_unit"] != 1
            or type(value["stride"]) is not int or value["stride"] != width * 4
            or type(value["pixel_format"]) is not str or value["pixel_format"] != PIXEL_FORMAT):
        _reject(code)
    digest = _digest(value["input_sha256"], code)
    try:
        media = validate_media_box(value["media_box_points"])
    except PftPdfError:
        _reject(code)
    pixels = _base64(value["pixels_base64"], MAX_PIXELS * 4, code)
    if len(pixels) != width * height * 4 or pixels[3::4] != b"\xff" * (width * height):
        _reject(code)
    return {"ok": True, "backend": "coregraphics", "input_sha256": digest,
            "byte_count": value["byte_count"], "page_count": 1, "media_box_points": media,
            "rotation_degrees": value["rotation_degrees"], "user_unit": 1,
            "width": width, "height": height, "stride": width * 4,
            "pixel_format": PIXEL_FORMAT, "pixels_base64": value["pixels_base64"]}, pixels


def validate_pdf_response(value, *, input_sha256, byte_count, width, height):
    """Validate and detach a result, binding successful pixels to this exact input."""
    _digest(input_sha256, "PDF_INVALID_RESPONSE")
    _dimensions(width, height, "PDF_INVALID_RESPONSE")
    if type(byte_count) is not int or not 1 <= byte_count <= MAX_PDF_BYTES:
        _reject("PDF_INVALID_RESPONSE")
    result, _ = _response(value)
    if result["ok"] and any(result[key] != expected for key, expected in (
            ("input_sha256", input_sha256), ("byte_count", byte_count),
            ("width", width), ("height", height))):
        _reject("PDF_INVALID_RESPONSE")
    return result


def decode_pdf_response(raw, *, input_sha256, byte_count, width, height):
    return validate_pdf_response(_loads(raw, MAX_RESPONSE_BYTES, "PDF_INVALID_RESPONSE"),
                                 input_sha256=input_sha256, byte_count=byte_count,
                                 width=width, height=height)


def response_pixels(value):
    """Self-validate an entire success DTO before returning bounded owned RGBA bytes."""
    result, pixels = _response(value)
    if not result["ok"]:
        _reject("PDF_INVALID_RESPONSE")
    return pixels


def encode_pdf_response(value):
    result, _ = _response(value)
    return _dumps(result, MAX_RESPONSE_BYTES, "PDF_INVALID_RESPONSE")
