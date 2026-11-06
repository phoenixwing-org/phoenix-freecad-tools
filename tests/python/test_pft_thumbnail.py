# SPDX-License-Identifier: Apache-2.0
"""Public PNG fixtures and filesystem races; no FreeCAD, Qt or image libraries."""

import hashlib
import importlib.util
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
spec = importlib.util.spec_from_file_location("pft_thumbnail", ADDON / "pft_thumbnail.py")
thumbnail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(thumbnail)
SIGNATURE = b"\x89PNG\r\n\x1a\n"


def chunk(kind, payload=b""):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def fixture(colour=2, width=2, height=1, prefix=(), suffix=(), raw=None, stream=None):
    # Two explicit pixels: red and green, each with independent RGBA alpha.
    raw = raw if raw is not None else (b"\x00\xff\x00\x00\x00\xff\x00" if colour == 2
                                      else b"\x00\xff\x00\x00\x00\x00\xff\x00\x80")
    stream = zlib.compress(raw) if stream is None else stream
    return (SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour, 0, 0, 0))
            + b"".join(prefix) + chunk(b"IDAT", stream) + b"".join(suffix) + chunk(b"IEND"))


def chunks(data):
    result, offset = [], 8
    while offset < len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        result.append((data[offset + 4:offset + 8], data[offset + 8:offset + 8 + length]))
        offset += length + 12
    return result


class PftPngSanitizerTests(unittest.TestCase):
    def assert_code(self, code, callback):
        with self.assertRaises(thumbnail.PftThumbnailError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_identifying_metadata_is_removed_while_idat_and_binary_colour_are_exact(self):
        gamma = chunk(b"gAMA", struct.pack(">I", 45455))
        srgb = chunk(b"sRGB", b"\x00")
        text = chunk(b"tEXt", b"Author\x00Public fixture name; /private/example/work.FCStd")
        international = chunk(b"iTXt", b"Title\x00\x00\x00\x00\x00Public fixture title")
        compressed_text = chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(b"private fixture metadata"))
        source = fixture(prefix=(gamma, srgb, text, international, compressed_text, chunk(b"eXIf", b"private fixture"),
                                 chunk(b"pHYs", b"\x00" * 9), chunk(b"vpAg", b"unknown private data")),
                         suffix=(chunk(b"tIME", b"\x00" * 7),))
        clean = thumbnail.sanitize_png(source, width=2, height=1)
        self.assertEqual([kind for kind, _ in chunks(clean)], [b"IHDR", b"gAMA", b"sRGB", b"IDAT", b"IEND"])
        self.assertEqual(dict(chunks(clean))[b"IDAT"], dict(chunks(source))[b"IDAT"])
        self.assertEqual(zlib.decompress(dict(chunks(clean))[b"IDAT"]), b"\x00\xff\x00\x00\x00\xff\x00")
        for private in (b"Author", b"Title", b"/private/", b"private fixture", b"unknown private"):
            self.assertNotIn(private, clean)
        self.assertEqual(thumbnail.sanitize_png(clean), clean)

    def test_rgba_alpha_and_rgb_transparent_colour_are_preserved(self):
        rgba = fixture(colour=6)
        self.assertEqual(thumbnail.sanitize_png(rgba), rgba)
        self.assertEqual(zlib.decompress(dict(chunks(rgba))[b"IDAT"])[4::4], b"\x00\x80")
        transparent = fixture(prefix=(chunk(b"tRNS", struct.pack(">3H", 255, 0, 0)),))
        self.assertEqual(thumbnail.sanitize_png(transparent), transparent)
        self.assert_code("PNG_INVALID", lambda: thumbnail.sanitize_png(fixture(colour=6, prefix=(chunk(b"tRNS", b"\x00" * 6),))))

    def test_multiple_consecutive_idat_chunks_retain_original_compressed_bytes(self):
        source = fixture()
        header, stream = chunks(source)[:2]
        divided = SIGNATURE + chunk(*header) + chunk(b"IDAT", stream[1][:2]) + chunk(b"IDAT") + chunk(b"IDAT", stream[1][2:]) + chunk(b"IEND")
        self.assertEqual(thumbnail.sanitize_png(divided), divided)

    def test_crc_truncation_order_and_trailing_bytes_are_rejected(self):
        png = fixture()
        bad_crc = bytearray(png); bad_crc[-1] ^= 1
        header, stream, end = [chunk(kind, payload) for kind, payload in chunks(png)]
        cases = (bytes(bad_crc), png[:-1], png + b"extra", SIGNATURE + stream + header + end,
                 SIGNATURE + header + header + stream + end, SIGNATURE + header + stream,
                 SIGNATURE + header + stream + chunk(b"tEXt", b"x") + stream + end,
                 SIGNATURE + header + chunk(b"ABCD") + stream + end,
                 SIGNATURE + header + chunk(b"abca") + stream + end,
                 SIGNATURE + header + stream + chunk(b"IEND", b"x"))
        for data in cases:
            with self.subTest(length=len(data)):
                self.assert_code("PNG_INVALID", lambda: thumbnail.sanitize_png(data))

    def test_compressed_stream_filters_and_decompression_bomb_are_rejected(self):
        stream = dict(chunks(fixture()))[b"IDAT"]
        cases = (fixture(stream=stream[:-1]), fixture(stream=stream + zlib.compress(b"hidden")),
                 fixture(raw=b"\x05" + b"\x00" * 6), fixture(raw=b"\x00" * 6),
                 fixture(raw=b"\x00" * (8 * 1024 * 1024)))
        for data in cases:
            with self.subTest(length=len(data)):
                self.assert_code("PNG_INVALID", lambda: thumbnail.sanitize_png(data))

    def test_size_dimensions_and_supported_formats_are_explicit(self):
        self.assert_code("PNG_TOO_LARGE", lambda: thumbnail.sanitize_png(fixture() + b"x" * thumbnail.PFT_MAX_PNG_BYTES))
        self.assert_code("PNG_TOO_LARGE", lambda: thumbnail.sanitize_png(fixture(width=1025, height=1024)))
        self.assert_code("PNG_TOO_LARGE", lambda: thumbnail.sanitize_png(fixture(prefix=(chunk(b"vpAg"),) * thumbnail.PFT_MAX_PNG_CHUNKS)))
        for width, height in ((1, 1), (True, None), (None, 0), (None, 2)):
            self.assert_code("PNG_DIMENSIONS_MISMATCH", lambda: thumbnail.sanitize_png(fixture(), width, height))
        for depth, colour, interlace in ((16, 2, 0), (8, 3, 0), (8, 6, 1)):
            data = SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, depth, colour, 0, 0, interlace)) + chunk(b"IDAT") + chunk(b"IEND")
            self.assert_code("PNG_UNSUPPORTED", lambda: thumbnail.sanitize_png(data))
        self.assert_code("PNG_COLOR_UNSUPPORTED", lambda: thumbnail.sanitize_png(fixture(prefix=(chunk(b"iCCP", b"Private profile\x00\x00data"),))))

    def test_malformed_retained_colour_chunks_are_not_silently_kept(self):
        for kind, payload in ((b"gAMA", b"\x00" * 4), (b"cHRM", b"x"), (b"sRGB", b"\x04"),
                              (b"sBIT", b"\x09\x08\x08"), (b"tRNS", struct.pack(">3H", 256, 0, 0))):
            with self.subTest(kind=kind):
                self.assert_code("PNG_INVALID", lambda: thumbnail.sanitize_png(fixture(prefix=(chunk(kind, payload),))))


class PftPngExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pft-thumbnail-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.target = self.root / "public-thumbnail.png"
        self.png = fixture()
    def assert_no_stage(self):
        self.assertEqual(list(self.root.glob(".pft-thumbnail-*")), [])

    def test_cancel_has_no_io_and_preparation_does_not_create_output(self):
        with patch.object(thumbnail, "_export_target") as target, patch.object(thumbnail.tempfile, "mkstemp") as stage:
            self.assertIsNone(thumbnail.prepare_png_export(None, None))
            self.assertIsNone(thumbnail.prepare_png_export("", None))
            self.assertIsNone(thumbnail.write_png_export(None))
            target.assert_not_called(); stage.assert_not_called()
        plan = thumbnail.prepare_png_export(str(self.target), self.png)
        self.assertFalse(plan["exists"])
        self.assertFalse(self.target.exists())

    def test_new_export_publishes_exact_sanitized_preview_bytes(self):
        source = fixture(prefix=(chunk(b"tEXt", b"Author\x00private fixture"),))
        plan = thumbnail.prepare_png_export(str(self.target), source)
        self.assertEqual(plan["digest"], hashlib.sha256(plan["data"]).hexdigest())
        self.assertEqual(thumbnail.write_png_export(plan), str(self.target))
        self.assertEqual(self.target.read_bytes(), self.png)
        self.assert_no_stage()

    def test_existing_target_requires_true_confirmation_and_changed_target_is_preserved(self):
        self.target.write_bytes(b"original")
        plan = thumbnail.prepare_png_export(str(self.target), self.png)
        for confirmation in (False, 1, "yes"):
            with self.assertRaises(thumbnail.PftThumbnailError) as caught:
                thumbnail.write_png_export(plan, overwrite=confirmation)
            self.assertEqual(caught.exception.code, "OVERWRITE_REQUIRED")
        self.assertEqual(self.target.read_bytes(), b"original")
        thumbnail.write_png_export(plan, overwrite=True)
        self.assertEqual(self.target.read_bytes(), self.png)
        plan = thumbnail.prepare_png_export(str(self.target), self.png)
        self.target.write_bytes(b"concurrent user change")
        with self.assertRaises(thumbnail.PftThumbnailError) as caught:
            thumbnail.write_png_export(plan, overwrite=True)
        self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
        self.assertEqual(self.target.read_bytes(), b"concurrent user change")
        self.assert_no_stage()

    def test_new_file_publish_does_not_overwrite_a_racing_creation(self):
        plan = thumbnail.prepare_png_export(str(self.target), self.png)
        link = os.link
        def race(source, destination, **kwargs):
            self.target.write_bytes(b"user created during publication")
            link(source, destination, **kwargs)
        with patch.object(thumbnail.os, "link", side_effect=race):
            with self.assertRaises(thumbnail.PftThumbnailError):
                thumbnail.write_png_export(plan)
        self.assertEqual(self.target.read_bytes(), b"user created during publication")
        self.assert_no_stage()

    def test_symlink_leaf_parent_or_replaced_directory_is_refused(self):
        original = self.root / "original.png"; original.write_bytes(b"keep")
        self.target.symlink_to(original)
        with self.assertRaises(thumbnail.PftThumbnailError):
            thumbnail.prepare_png_export(str(self.target), self.png)
        self.assertEqual(original.read_bytes(), b"keep")
        self.target.unlink()
        alias = self.root / "alias"; alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(thumbnail.PftThumbnailError):
            thumbnail.prepare_png_export(str(alias / "new.png"), self.png)
        folder = self.root / "folder"; folder.mkdir()
        plan = thumbnail.prepare_png_export(str(folder / "new.png"), self.png)
        folder.rename(self.root / "old-folder"); folder.mkdir()
        with self.assertRaises(thumbnail.PftThumbnailError) as caught:
            thumbnail.write_png_export(plan)
        self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
        self.assertEqual(list(folder.iterdir()), [])

    def _replaceable_directory(self, name, existed, ancestor):
        base = self.root / name
        parent = base / "selected" / "nested"
        parent.mkdir(parents=True)
        target = parent / "image.png"
        replaced = parent.parent if ancestor else parent
        replacement = base / "replacement"
        replacement.mkdir()
        replacement_parent = replacement / "nested" if ancestor else replacement
        replacement_parent.mkdir(exist_ok=True)
        if existed:
            target.write_bytes(b"original image bytes")
            # Keep the complete leaf fingerprint unchanged across replacement;
            # the directory identity must provide the rejection.
            os.link(target, replacement_parent / target.name)
        plan = thumbnail.prepare_png_export(str(target), self.png)
        saved = base / "original-directory"
        def replace_directory():
            replaced.rename(saved)
            replacement.rename(replaced)
        return base, target, plan, replace_directory, saved

    def test_parent_and_ancestor_identity_remain_bound_to_confirmation(self):
        for existed in (False, True):
            for ancestor in (False, True):
                with self.subTest(existed=existed, ancestor=ancestor):
                    base, target, plan, replace_directory, saved = self._replaceable_directory(
                        "confirm-%s-%s" % (existed, ancestor), existed, ancestor)
                    replace_directory()
                    self.assertEqual(thumbnail._fingerprint(target), plan["_fingerprint"])
                    with patch.object(thumbnail, "_create_png_stage") as stage:
                        with self.assertRaises(thumbnail.PftThumbnailError) as caught:
                            thumbnail.write_png_export(plan, overwrite=existed)
                        self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
                        stage.assert_not_called()
                    self.assertEqual(target.exists(), existed)
                    if existed:
                        self.assertEqual(target.read_bytes(), b"original image bytes")
                        self.assertEqual(next(saved.rglob("image.png")).read_bytes(), b"original image bytes")
                    self.assertEqual(list(base.rglob(".pft-thumbnail-*")), [])

    def test_directory_changed_after_flush_never_publishes_or_leaks_owned_stage(self):
        if not thumbnail._png_directory_fd_supported():
            self.skipTest("directory-relative cleanup is unavailable on this platform")
        for existed in (False, True):
            for ancestor in (False, True):
                with self.subTest(existed=existed, ancestor=ancestor):
                    base, target, plan, replace_directory, saved = self._replaceable_directory(
                        "flush-%s-%s" % (existed, ancestor), existed, ancestor)
                    original_fsync = os.fsync
                    def swap_after_flush(descriptor):
                        original_fsync(descriptor)
                        replace_directory()
                    with patch.object(thumbnail.os, "fsync", side_effect=swap_after_flush), \
                            patch.object(thumbnail.os, "link") as link, patch.object(thumbnail.os, "replace") as replace:
                        with self.assertRaises(thumbnail.PftThumbnailError) as caught:
                            thumbnail.write_png_export(plan, overwrite=existed)
                        self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
                        link.assert_not_called(); replace.assert_not_called()
                    self.assertEqual(target.exists(), existed)
                    if existed:
                        self.assertEqual(target.read_bytes(), b"original image bytes")
                    self.assertEqual(list(base.rglob(".pft-thumbnail-*")), [])

    def test_directory_changed_during_stage_creation_keeps_creation_in_opened_namespace(self):
        if not thumbnail._png_directory_fd_supported():
            self.skipTest("directory-relative staging is unavailable on this platform")
        base, target, plan, replace_directory, saved = self._replaceable_directory("stage-creation", False, False)
        original_stage = thumbnail._create_png_stage
        observed = []
        def stage_after_swap(*args, **kwargs):
            replace_directory()
            result = original_stage(*args, **kwargs)
            observed.append(next(saved.glob(".pft-thumbnail-*")))
            return result
        with patch.object(thumbnail, "_create_png_stage", side_effect=stage_after_swap):
            with self.assertRaises(thumbnail.PftThumbnailError) as caught:
                thumbnail.write_png_export(plan)
            self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
        self.assertEqual(len(observed), 1)
        self.assertFalse(target.exists())
        self.assertEqual(list(base.rglob(".pft-thumbnail-*")), [])

    def test_late_parent_or_ancestor_swap_never_writes_replacement_or_claims_success(self):
        if not thumbnail._png_directory_fd_supported():
            self.skipTest("directory-relative publication is unavailable on this platform")
        for existed in (False, True):
            for ancestor in (False, True):
                with self.subTest(existed=existed, ancestor=ancestor):
                    base, target, plan, replace_directory, saved = self._replaceable_directory(
                        "late-%s-%s" % (existed, ancestor), existed, ancestor)
                    method = "replace" if existed else "link"
                    original_publish = getattr(os, method)
                    def publish_after_swap(source, destination, **kwargs):
                        self.assertIsInstance(kwargs.get("src_dir_fd"), int)
                        self.assertEqual(kwargs["src_dir_fd"], kwargs.get("dst_dir_fd"))
                        replace_directory()
                        return original_publish(source, destination, **kwargs)
                    with patch.object(thumbnail.os, method, side_effect=publish_after_swap) as publish:
                        with self.assertRaises(thumbnail.PftThumbnailError) as caught:
                            thumbnail.write_png_export(plan, overwrite=existed)
                        self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
                        self.assertEqual(publish.call_count, 1)
                    self.assertEqual(target.exists(), existed)
                    if existed:
                        self.assertEqual(target.read_bytes(), b"original image bytes")
                    self.assertEqual(next(saved.rglob("image.png")).read_bytes(), self.png)
                    self.assertEqual(list(base.rglob(".pft-thumbnail-*")), [])

    def test_replaced_stage_is_neither_published_nor_cleaned_as_our_own(self):
        plan = thumbnail.prepare_png_export(str(self.target), self.png)
        original_fsync = os.fsync
        foreign = []
        moved = self.root / "moved-owned-stage"
        def replace_stage(descriptor):
            original_fsync(descriptor)
            stage = next(self.root.glob(".pft-thumbnail-*"))
            stage.rename(moved)
            stage.write_bytes(b"independent foreign bytes")
            foreign.append(stage)
        with patch.object(thumbnail.os, "fsync", side_effect=replace_stage), \
                patch.object(thumbnail.os, "link") as publish:
            with self.assertRaises(thumbnail.PftThumbnailError) as caught:
                thumbnail.write_png_export(plan)
            self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
            publish.assert_not_called()
        self.assertFalse(self.target.exists())
        self.assertEqual(foreign[0].read_bytes(), b"independent foreign bytes")
        self.assertEqual(moved.read_bytes(), self.png)

    def test_same_inode_stage_content_change_cannot_replace_frozen_png(self):
        plan = thumbnail.prepare_png_export(str(self.target), self.png)
        original_fsync = os.fsync
        def change_stage(descriptor):
            original_fsync(descriptor)
            next(self.root.glob(".pft-thumbnail-*")).write_bytes(b"changed in place")
        with patch.object(thumbnail.os, "fsync", side_effect=change_stage), \
                patch.object(thumbnail.os, "link") as publish:
            with self.assertRaises(thumbnail.PftThumbnailError) as caught:
                thumbnail.write_png_export(plan)
            self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
            publish.assert_not_called()
        self.assertFalse(self.target.exists())
        self.assert_no_stage()

    def test_stage_swapped_inside_publication_does_not_report_foreign_output_success(self):
        plan = thumbnail.prepare_png_export(str(self.target), self.png)
        original_link = os.link
        moved = self.root / "moved-owned-stage"
        foreign = []
        def publish_foreign(source, destination, **kwargs):
            stage = self.root / Path(source).name
            stage.rename(moved)
            stage.write_bytes(b"foreign bytes at publication")
            foreign.append(stage)
            return original_link(source, destination, **kwargs)
        with patch.object(thumbnail.os, "link", side_effect=publish_foreign) as publish:
            with self.assertRaises(thumbnail.PftThumbnailError) as caught:
                thumbnail.write_png_export(plan)
            self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
            self.assertEqual(publish.call_count, 1)
        # Publication may already have happened; do not roll back unknown output.
        self.assertEqual(self.target.read_bytes(), b"foreign bytes at publication")
        self.assertEqual(foreign[0].read_bytes(), b"foreign bytes at publication")
        self.assertEqual(moved.read_bytes(), self.png)

    def test_fallback_without_directory_fd_keeps_normal_new_and_overwrite_exports(self):
        with patch.object(thumbnail.os, "supports_dir_fd", set()):
            plan = thumbnail.prepare_png_export(str(self.target), self.png)
            self.assertEqual(thumbnail.write_png_export(plan), str(self.target))
            self.assertEqual(self.target.read_bytes(), self.png)
            self.target.write_bytes(b"original fallback bytes")
            plan = thumbnail.prepare_png_export(str(self.target), self.png)
            self.assertEqual(thumbnail.write_png_export(plan, overwrite=True), str(self.target))
            self.assertEqual(self.target.read_bytes(), self.png)
        self.assert_no_stage()

    def test_invalid_paths_tampered_bytes_and_failed_replace_never_publish(self):
        for path in ("relative.png", str(self.root / "model.FCStd"), str(self.root / "missing" / "new.png"), str(self.root / "bad\n.png")):
            with self.assertRaises(thumbnail.PftThumbnailError):
                thumbnail.prepare_png_export(path, self.png)
        plan = thumbnail.prepare_png_export(str(self.target), self.png)
        plan["data"] += b"changed"
        with self.assertRaises(thumbnail.PftThumbnailError) as caught:
            thumbnail.write_png_export(plan)
        self.assertEqual(caught.exception.code, "EXPORT_PLAN_INVALID")
        self.assertFalse(self.target.exists())
        self.target.write_bytes(b"keep")
        plan = thumbnail.prepare_png_export(str(self.target), self.png)
        with patch.object(thumbnail.os, "replace", side_effect=OSError("private fixture path")):
            with self.assertRaises(thumbnail.PftThumbnailError) as caught:
                thumbnail.write_png_export(plan, overwrite=True)
        self.assertNotIn("private fixture path", caught.exception.message)
        self.assertEqual(self.target.read_bytes(), b"keep")
        self.assert_no_stage()


if __name__ == "__main__":
    unittest.main()
