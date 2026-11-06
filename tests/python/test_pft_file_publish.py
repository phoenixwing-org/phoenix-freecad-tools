# SPDX-License-Identifier: Apache-2.0
"""Synthetic filesystem races for bounded frozen-byte publication."""
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools"
_saved_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_file_publish as publish
finally:
    sys.path[:] = _saved_path


class PftFilePublishTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pft-file-publish-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.target = self.root / "page.pdf"
        self.data = b"synthetic frozen document bytes\x00\xff\n"
        self.policy = {"extensions": (".pdf", ".svg"), "max_bytes": 4096}

    def prepare(self, path=None, data=None):
        return publish.prepare_file_export(str(path or self.target), self.data if data is None else data, **self.policy)

    def write(self, plan, **options):
        return publish.write_file_export(plan, **self.policy, **options)

    def assert_no_stage(self, root=None):
        self.assertEqual(list((root or self.root).rglob(".pft-export-*")), [])

    def test_cancel_performs_no_path_or_stage_io(self):
        with patch.object(publish, "export_target") as path, patch.object(publish, "create_stage") as stage:
            self.assertIsNone(publish.prepare_file_export(None, None, **self.policy))
            self.assertIsNone(publish.prepare_file_export("", None, **self.policy))
            self.assertIsNone(self.write(None))
        path.assert_not_called(); stage.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_new_frozen_bytes_and_empty_artifact_publish_exactly_with_private_mode(self):
        for data in (self.data, b""):
            with self.subTest(empty=not data):
                self.target.unlink(missing_ok=True)
                plan = self.prepare(data=data)
                self.assertFalse(self.target.exists())
                self.assertEqual(plan["digest"], hashlib.sha256(data).hexdigest())
                receipt = self.write(plan)
                self.assertEqual(receipt, {"path": str(self.target), "digest": plan["digest"], "size": len(data),
                                          "publication_state": "published", "cleanup_state": "complete",
                                          "directory_fd": publish.directory_fd_supported()})
                self.assertEqual(self.target.read_bytes(), data)
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
                self.assert_no_stage()

    def test_invalid_local_policy_and_mutable_or_oversized_data_cannot_stage(self):
        policies = ({"extensions": (".pdf",), "max_bytes": True},
                    {"extensions": (".PDF",), "max_bytes": 4096},
                    {"extensions": (".pdf", ".pdf"), "max_bytes": 4096},
                    {"extensions": (".pdf\n",), "max_bytes": 4096},
                    {"extensions": (), "max_bytes": 4096})
        with patch.object(publish, "create_stage") as stage:
            for policy in policies:
                with self.subTest(policy=policy), self.assertRaises(publish.PftFilePublishError) as caught:
                    publish.prepare_file_export(str(self.target), self.data, **policy)
                self.assertEqual(caught.exception.code, "INVALID_EXPORT_POLICY")
            for data in (bytearray(self.data), None, b"x" * 4097):
                with self.subTest(data_type=type(data).__name__), self.assertRaises(publish.PftFilePublishError):
                    publish.prepare_file_export(str(self.target), data, **self.policy)
        stage.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_path_extension_control_missing_parent_and_symlinks_are_refused(self):
        source = self.root / "original.pdf"
        source.write_bytes(b"keep")
        self.target.symlink_to(source)
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        paths = ("relative.pdf", str(self.root / "source.FCStd"), str(self.root / "bad\n.pdf"),
                 str(self.root / "missing" / "page.pdf"), str(self.target), str(alias / "page.pdf"))
        for path in paths:
            with self.subTest(path_kind=Path(path).name), self.assertRaises(publish.PftFilePublishError):
                publish.prepare_file_export(path, self.data, **self.policy)
        self.assertEqual(source.read_bytes(), b"keep")
        self.assert_no_stage()

    def test_oversized_or_nonregular_existing_target_is_not_read_or_overwritten(self):
        self.target.write_bytes(b"x" * 4097)
        with self.assertRaises(publish.PftFilePublishError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, "EXPORT_TARGET_TOO_LARGE")
        self.target.unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.target)
            with self.assertRaises(publish.PftFilePublishError) as caught:
                self.prepare()
            self.assertEqual(caught.exception.code, "INVALID_EXPORT_TARGET")
        self.assert_no_stage()

    def test_overwrite_needs_exact_boolean_and_complete_original_fingerprint(self):
        self.target.write_bytes(b"original")
        plan = self.prepare()
        self.assertEqual(plan["_fingerprint"][-1], hashlib.sha256(b"original").hexdigest())
        for value in (False, 1, "yes"):
            with self.subTest(value=value), self.assertRaises(publish.PftFilePublishError) as caught:
                self.write(plan, overwrite=value)
            self.assertEqual(caught.exception.publication_state, "not_published")
            self.assertEqual(caught.exception.cleanup_state, "complete")
        self.write(plan, overwrite=True)
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assert_no_stage()

    def test_changed_or_newly_appeared_target_remains_untouched(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.target.unlink(missing_ok=True)
                if existing:
                    self.target.write_bytes(b"original")
                plan = self.prepare()
                self.target.write_bytes(b"independent revision")
                with self.assertRaises(publish.PftFilePublishError) as caught:
                    self.write(plan, overwrite=True)
                self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
                self.assertEqual(caught.exception.publication_state, "not_published")
                self.assertEqual(self.target.read_bytes(), b"independent revision")
        self.assert_no_stage()

    def test_tampered_bytes_digest_or_missing_target_snapshot_is_not_reconstructed(self):
        for kind in ("bytes", "digest", "directories", "fingerprint"):
            with self.subTest(kind=kind):
                plan = self.prepare()
                if kind == "bytes": plan["data"] += b"changed"
                elif kind == "digest": plan["digest"] = "0" * 64
                elif kind == "directories": plan.pop("_directories")
                else: plan.pop("_fingerprint")
                with patch.object(publish, "create_stage") as stage:
                    with self.assertRaises(publish.PftFilePublishError) as caught:
                        self.write(plan)
                    self.assertEqual(caught.exception.publication_state, "not_published")
                    stage.assert_not_called()
        self.assertFalse(self.target.exists())

    def _replaceable(self, name, existed, ancestor):
        base = self.root / name
        parent = base / "selected" / "nested"
        parent.mkdir(parents=True)
        target = parent / "page.pdf"
        replaced = parent.parent if ancestor else parent
        replacement = base / "replacement"
        replacement.mkdir()
        replacement_parent = replacement / "nested" if ancestor else replacement
        replacement_parent.mkdir(exist_ok=True)
        if existed:
            target.write_bytes(b"original")
            os.link(target, replacement_parent / target.name)
        plan = self.prepare(target)
        saved = base / "saved-directory"
        def swap():
            replaced.rename(saved)
            replacement.rename(replaced)
        return base, target, plan, saved, swap

    def test_parent_and_ancestor_identity_remain_bound_during_confirmation(self):
        for existed in (False, True):
            for ancestor in (False, True):
                with self.subTest(existed=existed, ancestor=ancestor):
                    base, target, plan, saved, swap = self._replaceable(str((existed, ancestor)), existed, ancestor)
                    swap()
                    self.assertEqual(publish._fingerprint(target, 4096), plan["_fingerprint"])
                    with patch.object(publish, "create_stage") as stage:
                        with self.assertRaises(publish.PftFilePublishError) as caught:
                            self.write(plan, overwrite=existed)
                        self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
                        self.assertEqual(caught.exception.publication_state, "not_published")
                        stage.assert_not_called()
                    self.assertEqual(target.exists(), existed)
                    self.assert_no_stage(base)

    def test_parent_and_ancestor_changes_after_flush_clean_only_opened_namespace(self):
        if not publish.directory_fd_supported(): self.skipTest("directory-relative OS APIs unavailable")
        for existed in (False, True):
            for ancestor in (False, True):
                with self.subTest(existed=existed, ancestor=ancestor):
                    base, target, plan, saved, swap = self._replaceable(str((existed, ancestor)), existed, ancestor)
                    original_fsync = os.fsync
                    def flush_then_swap(descriptor):
                        original_fsync(descriptor)
                        swap()
                    with patch.object(publish.os, "fsync", side_effect=flush_then_swap), \
                            patch.object(publish.os, "link") as link, patch.object(publish.os, "replace") as replace:
                        with self.assertRaises(publish.PftFilePublishError) as caught:
                            self.write(plan, overwrite=existed)
                        self.assertEqual((caught.exception.publication_state, caught.exception.cleanup_state),
                                         ("not_published", "complete"))
                        link.assert_not_called(); replace.assert_not_called()
                    self.assertEqual(target.exists(), existed)
                    self.assert_no_stage(base)

    def test_swap_inside_stage_creation_stays_in_opened_directory(self):
        if not publish.directory_fd_supported(): self.skipTest("directory-relative OS APIs unavailable")
        base, target, plan, saved, swap = self._replaceable("stage", False, False)
        def stage_after_swap(path, directory):
            swap()
            result = publish.create_stage(path, directory)
            self.assertEqual(len(list(saved.glob(".pft-export-*"))), 1)
            return result
        with self.assertRaises(publish.PftFilePublishError) as caught:
            self.write(plan, _stage_factory=stage_after_swap)
        self.assertEqual(caught.exception.publication_state, "not_published")
        self.assertEqual(caught.exception.cleanup_state, "complete")
        self.assertFalse(target.exists())
        self.assert_no_stage(base)

    def test_late_parent_or_ancestor_swap_never_claims_selected_path_success(self):
        if not publish.directory_fd_supported(): self.skipTest("directory-relative OS APIs unavailable")
        for existed in (False, True):
            for ancestor in (False, True):
                with self.subTest(existed=existed, ancestor=ancestor):
                    base, target, plan, saved, swap = self._replaceable(str((existed, ancestor)), existed, ancestor)
                    name = "replace" if existed else "link"
                    operation = getattr(os, name)
                    def swapped(source, destination, **kwargs):
                        self.assertIsInstance(kwargs.get("src_dir_fd"), int)
                        self.assertEqual(kwargs["src_dir_fd"], kwargs.get("dst_dir_fd"))
                        swap()
                        return operation(source, destination, **kwargs)
                    with patch.object(publish.os, name, side_effect=swapped) as call:
                        with self.assertRaises(publish.PftFilePublishError) as caught:
                            self.write(plan, overwrite=existed)
                        self.assertEqual(caught.exception.code, "EXPORT_TARGET_CHANGED")
                        self.assertEqual(caught.exception.publication_state, "unknown")
                        self.assertEqual(call.call_count, 1)
                    self.assertEqual(target.exists(), existed)
                    if existed: self.assertEqual(target.read_bytes(), b"original")
                    self.assertEqual(next(saved.rglob("page.pdf")).read_bytes(), self.data)
                    self.assert_no_stage(base)

    def test_foreign_stage_before_publication_is_preserved_with_unknown_cleanup(self):
        plan = self.prepare()
        original_fsync = os.fsync
        moved = self.root / "moved-own-stage"
        foreign = []
        def replace_stage(descriptor):
            original_fsync(descriptor)
            stage = next(self.root.glob(".pft-export-*"))
            stage.rename(moved)
            stage.write_bytes(b"foreign replacement")
            foreign.append(stage)
        with patch.object(publish.os, "fsync", side_effect=replace_stage), patch.object(publish.os, "link") as call:
            with self.assertRaises(publish.PftFilePublishError) as caught:
                self.write(plan)
            call.assert_not_called()
        self.assertEqual((caught.exception.publication_state, caught.exception.cleanup_state),
                         ("not_published", "unknown"))
        self.assertFalse(self.target.exists())
        self.assertEqual(moved.read_bytes(), self.data)
        self.assertEqual(foreign[0].read_bytes(), b"foreign replacement")

    def test_missing_stage_before_publication_does_not_claim_cleanup_complete(self):
        plan = self.prepare()
        original_fsync = os.fsync
        moved = self.root / "moved-own-stage"
        def move_stage(descriptor):
            original_fsync(descriptor)
            next(self.root.glob(".pft-export-*")).rename(moved)
        with patch.object(publish.os, "fsync", side_effect=move_stage):
            with self.assertRaises(publish.PftFilePublishError) as caught:
                self.write(plan)
        self.assertEqual((caught.exception.publication_state, caught.exception.cleanup_state),
                         ("not_published", "unknown"))
        self.assertEqual(moved.read_bytes(), self.data)
        self.assertFalse(self.target.exists())

    def test_same_inode_stage_content_change_is_detected_by_owned_descriptor_readback(self):
        plan = self.prepare()
        original_fsync = os.fsync
        def mutate_stage(descriptor):
            original_fsync(descriptor)
            next(self.root.glob(".pft-export-*")).write_bytes(b"different bytes")
        with patch.object(publish.os, "fsync", side_effect=mutate_stage), patch.object(publish.os, "link") as call:
            with self.assertRaises(publish.PftFilePublishError) as caught:
                self.write(plan)
            call.assert_not_called()
        self.assertEqual((caught.exception.publication_state, caught.exception.cleanup_state),
                         ("not_published", "complete"))
        self.assertFalse(self.target.exists())
        self.assert_no_stage()

    def test_foreign_stage_swapped_inside_link_is_not_reported_as_our_publication(self):
        plan = self.prepare()
        original_link = os.link
        moved = self.root / "moved-own-stage"
        foreign = []
        def publish_foreign(source, destination, **kwargs):
            stage = self.root / Path(source).name
            stage.rename(moved)
            stage.write_bytes(b"foreign at publish")
            foreign.append(stage)
            return original_link(source, destination, **kwargs)
        with patch.object(publish.os, "link", side_effect=publish_foreign) as call:
            with self.assertRaises(publish.PftFilePublishError) as caught:
                self.write(plan)
        self.assertEqual(call.call_count, 1)
        self.assertEqual((caught.exception.publication_state, caught.exception.cleanup_state), ("unknown", "unknown"))
        self.assertEqual(self.target.read_bytes(), b"foreign at publish")
        self.assertEqual(foreign[0].read_bytes(), b"foreign at publish")
        self.assertEqual(moved.read_bytes(), self.data)

    def test_publication_inode_and_final_digest_are_both_checked(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                self.target.unlink(missing_ok=True)
                plan = self.prepare()
                original_link = os.link
                def change_final(source, destination, **kwargs):
                    original_link(source, destination, **kwargs)
                    if replacement: self.target.unlink()
                    self.target.write_bytes(b"independent final bytes")
                with patch.object(publish.os, "link", side_effect=change_final) as call:
                    with self.assertRaises(publish.PftFilePublishError) as caught:
                        self.write(plan)
                self.assertEqual(call.call_count, 1)
                self.assertEqual(caught.exception.publication_state, "unknown")
                self.assertEqual(self.target.read_bytes(), b"independent final bytes")
                self.assert_no_stage()

    def test_racing_creation_is_preserved_and_publication_is_not_retried(self):
        plan = self.prepare()
        original_link = os.link
        def race(source, destination, **kwargs):
            self.target.write_bytes(b"independent new file")
            return original_link(source, destination, **kwargs)
        with patch.object(publish.os, "link", side_effect=race) as call:
            with self.assertRaises(publish.PftFilePublishError) as caught:
                self.write(plan)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(caught.exception.publication_state, "unknown")
        self.assertEqual(self.target.read_bytes(), b"independent new file")
        self.assert_no_stage()

    def test_publication_syscall_that_publishes_then_raises_is_unknown_without_rollback(self):
        for existed in (False, True):
            with self.subTest(existed=existed):
                self.target.unlink(missing_ok=True)
                if existed: self.target.write_bytes(b"original")
                plan = self.prepare()
                name = "replace" if existed else "link"
                operation = getattr(os, name)
                def publish_then_raise(source, destination, **kwargs):
                    operation(source, destination, **kwargs)
                    raise OSError("synthetic unpublished path must not leak")
                with patch.object(publish.os, name, side_effect=publish_then_raise) as call:
                    with self.assertRaises(publish.PftFilePublishError) as caught:
                        self.write(plan, overwrite=existed)
                self.assertEqual(call.call_count, 1)
                self.assertEqual(caught.exception.publication_state, "unknown")
                self.assertNotIn("synthetic unpublished path", caught.exception.message)
                self.assertEqual(self.target.read_bytes(), self.data)
                self.assert_no_stage()

    def test_successful_publication_followed_by_unlink_failure_stays_published(self):
        plan = self.prepare()
        original_unlink = os.unlink
        def fail_stage_cleanup(name, **kwargs):
            if Path(name).name.startswith(".pft-export-"):
                raise OSError("synthetic cleanup detail")
            return original_unlink(name, **kwargs)
        with patch.object(publish.os, "unlink", side_effect=fail_stage_cleanup), \
                patch.object(publish.os, "link", wraps=os.link) as call:
            with self.assertRaises(publish.PftFilePublishError) as caught:
                self.write(plan)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(caught.exception.code, "EXPORT_CLEANUP_FAILED")
        self.assertEqual((caught.exception.publication_state, caught.exception.cleanup_state), ("published", "unknown"))
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertEqual(len(list(self.root.glob(".pft-export-*"))), 1)
        self.assertNotIn("synthetic cleanup detail", caught.exception.message)

    def test_successful_publication_followed_by_parent_close_failure_stays_published(self):
        if not publish.directory_fd_supported(): self.skipTest("directory-relative OS APIs unavailable")
        plan = self.prepare()
        original_close = os.close
        def close_then_raise(descriptor):
            is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
            original_close(descriptor)
            if is_directory: raise OSError("synthetic close detail")
        with patch.object(publish.os, "close", side_effect=close_then_raise):
            with self.assertRaises(publish.PftFilePublishError) as caught:
                self.write(plan)
        self.assertEqual((caught.exception.publication_state, caught.exception.cleanup_state), ("published", "unknown"))
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assert_no_stage()

    def test_foreign_cleanup_name_after_valid_publication_is_preserved_and_reported(self):
        plan = self.prepare()
        original_link = os.link
        moved = self.root / "moved-own-stage"
        foreign = []
        def link_then_replace_stage(source, destination, **kwargs):
            original_link(source, destination, **kwargs)
            stage = self.root / Path(source).name
            stage.rename(moved)
            stage.write_bytes(b"foreign after valid publication")
            foreign.append(stage)
        with patch.object(publish.os, "link", side_effect=link_then_replace_stage):
            with self.assertRaises(publish.PftFilePublishError) as caught:
                self.write(plan)
        self.assertEqual((caught.exception.publication_state, caught.exception.cleanup_state), ("published", "unknown"))
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertEqual(moved.read_bytes(), self.data)
        self.assertEqual(foreign[0].read_bytes(), b"foreign after valid publication")

    def test_directory_mode_change_is_refused_before_staging(self):
        folder = self.root / "private"
        folder.mkdir(mode=0o700)
        target = folder / "page.pdf"
        plan = self.prepare(target)
        folder.chmod(0o755)
        with patch.object(publish, "create_stage") as stage:
            with self.assertRaises(publish.PftFilePublishError) as caught:
                self.write(plan)
            stage.assert_not_called()
        self.assertEqual(caught.exception.publication_state, "not_published")
        self.assertFalse(target.exists())

    def test_legacy_fallback_is_explicit_and_strict_callers_can_require_directory_fd(self):
        plan = self.prepare()
        with patch.object(publish.os, "supports_dir_fd", set()):
            with self.assertRaises(publish.PftFilePublishError) as caught:
                self.write(plan, require_directory_fd=True)
            self.assertEqual(caught.exception.code, "EXPORT_DIRECTORY_FD_REQUIRED")
            self.assertEqual(caught.exception.publication_state, "not_published")
            receipt = self.write(plan)
            self.assertFalse(receipt["directory_fd"])
            self.assertEqual(self.target.read_bytes(), self.data)
            self.target.write_bytes(b"original fallback")
            plan = self.prepare()
            self.write(plan, overwrite=True)
            self.assertEqual(self.target.read_bytes(), self.data)
        self.assert_no_stage()


if __name__ == "__main__":
    unittest.main()
