# SPDX-License-Identifier: Apache-2.0
"""Pure bounded PNG sanitization and explicit atomic thumbnail file export."""

import hashlib
import os
from pathlib import Path
import stat
import struct
import tempfile
import unicodedata
import zlib

PFT_MAX_PNG_BYTES = 4 * 1024 * 1024
PFT_MAX_PNG_PIXELS = 1024 * 1024
PFT_MAX_PNG_CHUNKS = 4096
_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TRANSLATABLE_STRINGS = (
    "PNG 数据损坏或格式无效。", "PNG 数据或像素数量超过缩略图限制。",
    "PNG 仅支持8位非交错 RGB 或 RGBA 图像。", "PNG 包含尚不支持的色彩配置，未导出。",
    "PNG 尺寸与预期缩略图不一致。", "PNG 文件名必须使用绝对路径和 .png 扩展名。",
    "导出目录必须已存在，且路径不能经过符号链接。", "目标不是可覆盖的普通 PNG 文件。",
    "现有 PNG 文件超过4 MiB，未读取或覆盖。", "目标文件在确认后变化，请重新选择并确认。",
    "目标文件已存在，请明确确认覆盖。", "PNG 导出计划无效或已变化，请重新预览。",
    "PNG 导出失败；未保存 CAD 文档。",
)


class PftThumbnailError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _invalid():
    return PftThumbnailError("PNG_INVALID", "PNG 数据损坏或格式无效。")


def _colour_chunk(kind, payload, colour):
    """Only retain fixed binary colour/transparency fields, never profiles/text."""
    if kind == b"gAMA":
        valid = len(payload) == 4 and struct.unpack(">I", payload)[0] > 0
    elif kind == b"cHRM":
        values = struct.unpack(">8I", payload) if len(payload) == 32 else ()
        valid = bool(values) and all(n <= 100000 for n in values) and all(
            values[i] + values[i + 1] <= 100000 for i in range(0, 8, 2))
    elif kind == b"sRGB":
        valid = len(payload) == 1 and payload[0] <= 3
    elif kind == b"sBIT":
        valid = len(payload) == (3 if colour == 2 else 4) and all(1 <= n <= 8 for n in payload)
    elif kind == b"tRNS":
        valid = colour == 2 and len(payload) == 6 and all(n <= 255 for n in struct.unpack(">3H", payload))
    else:
        valid = False
    if not valid:
        raise _invalid()


def sanitize_png(data, width=None, height=None):
    """Validate one RGB/RGBA image and strip ancillary metadata, retaining IDAT bytes.

    Supports only PNG bit-depth 8, colour types 2/6 and non-interlaced images.
    The pixel budget is total width*height, not an assumption about display DPI.
    ICC profiles are refused because preserving opaque profiles could retain
    identifying text; silently dropping them could change the intended colour.
    """
    if not isinstance(data, bytes) or not data.startswith(_SIGNATURE):
        raise _invalid()
    if len(data) > PFT_MAX_PNG_BYTES:
        raise PftThumbnailError("PNG_TOO_LARGE", "PNG 数据或像素数量超过缩略图限制。")
    for expected in (width, height):
        if expected is not None and (type(expected) is not int or expected <= 0):
            raise PftThumbnailError("PNG_DIMENSIONS_MISMATCH", "PNG 尺寸与预期缩略图不一致。")
    offset, seen, kept, compressed = len(_SIGNATURE), set(), [_SIGNATURE], []
    chunk_count = 0
    dimensions, colour, row_size = None, None, None
    idat_started = idat_ended = ended = False
    while offset < len(data):
        chunk_count += 1
        if chunk_count > PFT_MAX_PNG_CHUNKS:
            raise PftThumbnailError("PNG_TOO_LARGE", "PNG 数据或像素数量超过缩略图限制。")
        if len(data) - offset < 12:
            raise _invalid()
        length = struct.unpack_from(">I", data, offset)[0]
        if length > len(data) - offset - 12:
            raise _invalid()
        kind = data[offset + 4:offset + 8]
        if any(not (65 <= char <= 90 or 97 <= char <= 122) for char in kind) or kind[2] & 32:
            raise _invalid()
        finish = offset + 12 + length
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF != crc:
            raise _invalid()
        if not seen and kind != b"IHDR":
            raise _invalid()
        if kind != b"IDAT" and idat_started:
            idat_ended = True
        retain = False
        if kind == b"IHDR":
            if seen or length != 13:
                raise _invalid()
            w, h, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            if not 1 <= w <= 0x7FFFFFFF or not 1 <= h <= 0x7FFFFFFF:
                raise _invalid()
            if w * h > PFT_MAX_PNG_PIXELS:
                raise PftThumbnailError("PNG_TOO_LARGE", "PNG 数据或像素数量超过缩略图限制。")
            if depth != 8 or colour not in (2, 6) or compression != 0 or filtering != 0 or interlace != 0:
                raise PftThumbnailError("PNG_UNSUPPORTED", "PNG 仅支持8位非交错 RGB 或 RGBA 图像。")
            if (width is not None and width != w) or (height is not None and height != h):
                raise PftThumbnailError("PNG_DIMENSIONS_MISMATCH", "PNG 尺寸与预期缩略图不一致。")
            dimensions, row_size, retain = (w, h), w * (3 if colour == 2 else 4) + 1, True
        elif kind == b"PLTE":
            if kind in seen or idat_started or b"tRNS" in seen or not 3 <= length <= 768 or length % 3:
                raise _invalid()
            retain = True
        elif kind == b"IDAT":
            if idat_ended:
                raise _invalid()
            idat_started, retain = True, True
            compressed.append(payload)
        elif kind == b"IEND":
            if length or not idat_started or finish != len(data):
                raise _invalid()
            ended = retain = True
        elif kind in (b"gAMA", b"cHRM", b"sRGB", b"sBIT", b"tRNS"):
            if kind in seen or idat_started or (kind != b"tRNS" and b"PLTE" in seen):
                raise _invalid()
            _colour_chunk(kind, payload, colour)
            retain = True
        elif kind == b"iCCP":
            raise PftThumbnailError("PNG_COLOR_UNSUPPORTED", "PNG 包含尚不支持的色彩配置，未导出。")
        elif kind in (b"acTL", b"fcTL", b"fdAT") or not kind[0] & 32:
            raise _invalid()  # Unknown critical chunks and animations are not one static image.
        if retain:
            kept.append(data[offset:finish])
        seen.add(kind)
        offset = finish
        if ended:
            break
    if not ended or dimensions is None:
        raise _invalid()
    expected = row_size * dimensions[1]
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(b"".join(compressed), expected + 1)
        if (len(raw) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
                or any(raw[start] > 4 for start in range(0, expected, row_size))):
            raise _invalid()
    except zlib.error:
        raise _invalid() from None
    return b"".join(kept)


def _export_target(filename):
    if (not isinstance(filename, str) or not os.path.isabs(filename) or Path(filename).suffix.lower() != ".png"
            or any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in filename)):
        raise PftThumbnailError("INVALID_EXPORT_PATH", "PNG 文件名必须使用绝对路径和 .png 扩展名。")
    target = Path(os.path.abspath(filename))
    current, directories = Path(target.anchor), []
    for part in target.parts[1:-1]:
        current /= part
        try:
            info = current.lstat()
        except OSError:
            raise PftThumbnailError("INVALID_EXPORT_DIRECTORY", "导出目录必须已存在，且路径不能经过符号链接。") from None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise PftThumbnailError("INVALID_EXPORT_DIRECTORY", "导出目录必须已存在，且路径不能经过符号链接。")
        directories.append((str(current), info.st_dev, info.st_ino, info.st_mode))
    return target, tuple(directories)


def _stat_values(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _fingerprint(target):
    try:
        before = target.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise PftThumbnailError("EXPORT_FAILED", "PNG 导出失败；未保存 CAD 文档。") from None
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise PftThumbnailError("INVALID_EXPORT_TARGET", "目标不是可覆盖的普通 PNG 文件。")
    if before.st_size > PFT_MAX_PNG_BYTES:
        raise PftThumbnailError("EXPORT_TARGET_TOO_LARGE", "现有 PNG 文件超过4 MiB，未读取或覆盖。")
    try:
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if _stat_values(before) != _stat_values(opened):
                raise PftThumbnailError("EXPORT_TARGET_CHANGED", "目标文件在确认后变化，请重新选择并确认。")
            contents = stream.read(PFT_MAX_PNG_BYTES + 1)
            after = os.fstat(stream.fileno())
        if (len(contents) > PFT_MAX_PNG_BYTES or _stat_values(before) != _stat_values(opened)
                or _stat_values(opened) != _stat_values(after) or _stat_values(after) != _stat_values(target.lstat())):
            raise PftThumbnailError("EXPORT_TARGET_CHANGED", "目标文件在确认后变化，请重新选择并确认。")
    except OSError:
        raise PftThumbnailError("EXPORT_TARGET_CHANGED", "目标文件在确认后变化，请重新选择并确认。") from None
    return (*_stat_values(after), hashlib.sha256(contents).hexdigest())


def prepare_png_export(filename, image_data):
    """Prepare in-memory bytes and a complete bounded target fingerprint; no writes."""
    if filename is None or filename == "":
        return None
    target, directories = _export_target(filename)
    data = sanitize_png(image_data)
    fingerprint = _fingerprint(target)
    return {"path": str(target), "data": data, "digest": hashlib.sha256(data).hexdigest(),
            "exists": fingerprint is not None, "_fingerprint": fingerprint, "_directories": directories}


def write_png_export(plan, overwrite=False):
    """Publish exactly the prepared sanitized bytes; new-file publication never replaces."""
    if plan is None:
        return None
    if not isinstance(plan, dict) or not isinstance(plan.get("data"), bytes):
        raise PftThumbnailError("EXPORT_PLAN_INVALID", "PNG 导出计划无效或已变化，请重新预览。")
    data = plan["data"]
    if (hashlib.sha256(data).hexdigest() != plan.get("digest") or sanitize_png(data) != data):
        raise PftThumbnailError("EXPORT_PLAN_INVALID", "PNG 导出计划无效或已变化，请重新预览。")
    target, directories = _export_target(plan.get("path"))
    fingerprint = _fingerprint(target)
    if (directories != plan.get("_directories") or fingerprint != plan.get("_fingerprint")
            or (fingerprint is not None) is not plan.get("exists")):
        raise PftThumbnailError("EXPORT_TARGET_CHANGED", "目标文件在确认后变化，请重新选择并确认。")
    if fingerprint is not None and overwrite is not True:
        raise PftThumbnailError("OVERWRITE_REQUIRED", "目标文件已存在，请明确确认覆盖。")
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".pft-thumbnail-", suffix=".tmp", dir=str(target.parent))
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _, current_directories = _export_target(str(target))
        if current_directories != directories or _fingerprint(target) != fingerprint:
            raise PftThumbnailError("EXPORT_TARGET_CHANGED", "目标文件在确认后变化，请重新选择并确认。")
        if fingerprint is not None:
            os.replace(temporary, str(target))
        else:
            os.link(temporary, str(target))
        return str(target)
    except PftThumbnailError:
        raise
    except OSError:
        raise PftThumbnailError("EXPORT_FAILED", "PNG 导出失败；未保存 CAD 文档。") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                raise PftThumbnailError("EXPORT_FAILED", "PNG 导出失败；未保存 CAD 文档。") from None
