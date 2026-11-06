# SPDX-License-Identifier: Apache-2.0
"""Synthetic private instance records only; no CAD, network or process probes."""
import errno
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
spec = importlib.util.spec_from_file_location("pft_instance_registry", ADDON / "pft_instance_registry.py")
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


@unittest.skipUnless(os.name == "posix", "POSIX ownership/mode contract")
class InstanceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pft-instance-test-", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir(mode=0o700)
        self.directory = self.base / "instances"
        self.directory.mkdir(mode=0o700)

    @staticmethod
    def record(index=1, **changes):
        record = {"schema_version": 1, "owner": "phoenix-freecad-tools",
                  "instance_id": format(index, "032x"), "service_id": "f" * 32,
                  "pid": 123, "url": "http://127.0.0.1:23456/", "protocol_version": 1,
                  "addon_version": "0.3.2"}
        record.update(changes)
        return record

    def publish(self, record=None, **options):
        return registry.publish_instance(self.record() if record is None else record,
                                         directory=self.directory, **options)

    def write(self, name, raw, mode=0o600, directory=None):
        target = (self.directory if directory is None else directory) / name
        target.write_bytes(raw)
        target.chmod(mode)
        return target

    def write_record(self, record=None, **changes):
        record = self.record(**changes) if record is None else record
        return self.write(record["instance_id"] + ".json", json.dumps(record).encode())

    def error(self, suffix, action, uncertain=False):
        with self.assertRaises(registry.PftInstanceRegistryError) as caught:
            action()
        self.assertEqual(caught.exception.code, "INSTANCE_REGISTRY_" + suffix)
        self.assertIs(caught.exception.uncertain, uncertain)
        self.assertEqual(str(caught.exception), caught.exception.message)
        self.assertNotIn(str(self.base), caught.exception.message)
        return caught.exception

    def test_import_has_no_registry_or_home_filesystem_io(self):
        alternate = importlib.util.module_from_spec(spec)
        with patch.object(os, "open", side_effect=AssertionError("no import I/O")), \
             patch.object(os, "stat", side_effect=AssertionError("no import I/O")), \
             patch.object(os, "scandir", side_effect=AssertionError("no import I/O")), \
             patch.object(os.path, "expanduser", side_effect=AssertionError("no home lookup")):
            spec.loader.exec_module(alternate)
        self.assertTrue(callable(alternate.publish_instance))

    def test_macos_and_linux_fallback_resolution_is_explicit_and_does_not_create(self):
        mac = registry.resolve_instance_directory(home=self.home, platform="darwin", env={})
        linux = registry.resolve_instance_directory(home=self.home, platform="linux", env={})
        self.assertEqual(mac, self.home / "Library/Application Support/PhoenixTools/mcp/runtime/instances")
        self.assertEqual(linux, self.home / ".local/state/PhoenixTools/mcp/runtime/instances")
        self.assertFalse(mac.exists())
        self.assertFalse(linux.exists())
        state = self.base / "state"
        self.assertEqual(registry.resolve_instance_directory(home=self.home, platform="linux", env={
            "XDG_RUNTIME_DIR": "", "XDG_STATE_HOME": str(state)}), state / "PhoenixTools/mcp/runtime/instances")
        self.assertFalse(state.exists())

    def test_existing_private_linux_runtime_wins_and_missing_root_falls_back(self):
        runtime = self.base / "runtime"
        runtime.mkdir(mode=0o700)
        env = {"XDG_RUNTIME_DIR": str(runtime), "XDG_STATE_HOME": str(self.base / "state")}
        self.assertEqual(registry.resolve_instance_directory(home=self.home, platform="linux", env=env),
                         runtime / "phoenix-tools/mcp/instances")
        runtime.rmdir()
        self.assertEqual(registry.resolve_instance_directory(home=self.home, platform="linux", env=env),
                         self.base / "state/PhoenixTools/mcp/runtime/instances")
        self.assertFalse(runtime.exists())

    def test_explicit_runtime_insecure_or_symlink_never_uses_an_alternate_namespace(self):
        runtime = self.base / "runtime"
        runtime.mkdir(mode=0o755)
        options = dict(home=self.home, platform="linux", env={"XDG_RUNTIME_DIR": str(runtime)})
        self.error("UNSAFE", lambda: registry.resolve_instance_directory(**options))
        runtime.chmod(0o700)
        linked = self.base / "linked-runtime"
        linked.symlink_to(runtime, target_is_directory=True)
        options["env"]["XDG_RUNTIME_DIR"] = str(linked)
        self.error("UNSAFE", lambda: registry.resolve_instance_directory(**options))
        self.assertFalse((self.home / ".local").exists())

    def test_malformed_paths_and_windows_acl_are_refused_before_creation(self):
        for value in ("relative", "~/runtime", "/neutral/../runtime", "/neutral/./runtime", "/neutral/\x00runtime", "/neutral/\n", "/neutral/\u200bruntime", 1, None):
            with self.subTest(value_type=type(value).__name__):
                self.error("INVALID_PATH", lambda: registry.resolve_instance_directory(home=self.home,
                    platform="linux", env={"XDG_RUNTIME_DIR": value}))
        for key in ("XDG_RUNTIME_DIR", "XDG_STATE_HOME"):
            self.error("INVALID_PATH", lambda: registry.resolve_instance_directory(home=self.home,
                platform="linux", env={key: "relative"}))
        self.error("UNSUPPORTED", lambda: registry.resolve_instance_directory(home=self.home, platform="win32", env={}))
        self.error("UNSUPPORTED", lambda: self.publish(platform="win32"))
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_controlled_default_creation_uses_private_fixed_suffix_and_keeps_existing_modes(self):
        library = self.home / "Library"
        library.mkdir(mode=0o755)
        mac = registry.publish_instance(self.record(), home=self.home, platform="darwin", env={})
        mac_path = registry.resolve_instance_directory(home=self.home, platform="darwin", env={})
        self.assertEqual(stat.S_IMODE(library.stat().st_mode), 0o755)
        for directory in (library / "Application Support", *list(mac_path.parents)[:3], mac_path):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertTrue(mac.remove())
        linux = registry.publish_instance(self.record(), home=self.home, platform="linux", env={})
        linux_path = registry.resolve_instance_directory(home=self.home, platform="linux", env={})
        self.assertTrue(linux_path.is_dir())
        self.assertTrue(linux.remove())
        self.assertEqual(list(linux_path.iterdir()), [])

    def test_explicit_directory_and_state_base_must_exist_without_arbitrary_creation(self):
        missing = self.base / "missing"
        self.error("IO_FAILED", lambda: registry.publish_instance(self.record(), directory=missing))
        self.error("IO_FAILED", lambda: registry.publish_instance(self.record(), home=self.home, platform="linux",
            env={"XDG_STATE_HOME": str(missing)}))
        self.assertFalse(missing.exists())

    def test_publish_and_remove_are_private_exact_and_idempotent_without_process_probes(self):
        record = self.record()
        with patch.object(os, "kill", side_effect=AssertionError("must not probe PIDs")):
            registration = self.publish(record)
            target = self.directory / (record["instance_id"] + ".json")
            self.assertEqual(json.loads(target.read_bytes()), record)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(target.stat().st_nlink, 1)
            self.assertEqual(len(list(self.directory.iterdir())), 1)
            self.assertTrue(registration.remove())
            self.assertFalse(target.exists())
            later = self.write_record()
            self.assertTrue(registration.remove())
            self.assertTrue(later.exists())

    def test_exact_schema_and_canonical_loopback_url_are_required_before_filesystem_writes(self):
        changes = [{"schema_version": True}, {"schema_version": 2}, {"protocol_version": False},
                   {"protocol_version": 2}, {"owner": "other"}, {"addon_version": "0.3.1"},
                   {"pid": False}, {"pid": 0}, {"pid": 1.0}, {"pid": 9007199254740992},
                   {"instance_id": "A" * 32}, {"instance_id": "f" * 31}, {"service_id": "g" * 32},
                   {"url": "http://localhost:23456/"}, {"url": "http://127.0.0.1:80/"},
                   {"url": "http://127.0.0.1:02345/"}, {"url": "http://127.0.0.1:65536/"},
                   {"url": "https://127.0.0.1:23456/"}, {"url": "http://127.0.0.1:23456"},
                   {"url": "http://127.0.0.1:23456/path"}, {"url": "http://user@127.0.0.1:23456/"},
                   {"url": "http://127.0.0.1:23456/?x=1"}, {"url": "http://127.0.0.1:23456/#x"}]
        for change in changes:
            with self.subTest(field=next(iter(change))):
                self.error("INVALID_RECORD", lambda: self.publish(self.record(**change)))
        for mutation in ("extra", "missing"):
            record = self.record()
            if mutation == "extra": record["document"] = "Never registered"
            else: del record["pid"]
            self.error("INVALID_RECORD", lambda: self.publish(record))
        self.assertEqual(list(self.directory.iterdir()), [])
        for port in (1024, 65535):
            registration = self.publish(self.record(url="http://127.0.0.1:" + str(port) + "/"))
            registration.remove()

    def test_existing_valid_registration_is_never_overwritten_even_if_byte_equal(self):
        existing = self.write_record()
        before = existing.read_bytes(), existing.stat().st_ino
        self.error("EXISTS", self.publish)
        self.assertEqual((existing.read_bytes(), existing.stat().st_ino), before)
        self.assertEqual(len(list(self.directory.iterdir())), 1)

    def test_unknown_names_damaged_json_duplicate_keys_and_name_mismatch_are_preserved(self):
        records = [b"not-json", b'{"schema_version":1,"schema_version":1}', b'[]', b'{}',
                   json.dumps(self.record(pid=1.0)).encode(), json.dumps(self.record(pid=True)).encode(),
                   json.dumps(self.record(2)).encode(), b'\xff']
        name = self.record()["instance_id"] + ".json"
        for raw in records:
            with self.subTest(raw_length=len(raw)):
                target = self.write(name, raw)
                self.error("INVALID_RECORD", lambda: self.publish(self.record(3)))
                self.assertEqual(target.read_bytes(), raw)
                target.unlink()
        unknown = self.write("unrecognized.json", b"retained")
        self.error("INVALID_ENTRY", self.publish)
        self.assertEqual(unknown.read_bytes(), b"retained")

    def test_file_modes_symlinks_hardlinks_and_foreign_ownership_are_refused(self):
        target = self.write_record()
        target.chmod(0o644)
        self.error("UNSAFE", lambda: self.publish(self.record(2)))
        target.chmod(0o600)
        outside = self.base / "hardlink"
        os.link(target, outside)
        self.error("UNSAFE", lambda: self.publish(self.record(2)))
        target.unlink()
        target.symlink_to(outside)
        self.error("UNSAFE", lambda: self.publish(self.record(2)))
        target.unlink(); outside.unlink()
        if os.getuid() != 0:
            with patch.object(registry.os, "getuid", return_value=os.getuid() + 1):
                self.error("UNSAFE", self.publish)

    def test_unsafe_registry_suffix_or_ancestor_is_never_chmoded_or_followed(self):
        self.directory.chmod(0o755)
        self.error("UNSAFE", self.publish)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o755)
        self.directory.chmod(0o700)
        linked = self.base / "registry-link"
        linked.symlink_to(self.directory, target_is_directory=True)
        self.error("UNSAFE", lambda: registry.publish_instance(self.record(), directory=linked))
        writable = self.base / "writable"
        writable.mkdir(mode=0o777)
        writable.chmod(0o777)
        nested = writable / "instances"
        nested.mkdir(mode=0o700)
        self.error("UNSAFE", lambda: registry.publish_instance(self.record(), directory=nested))
        self.assertEqual(list(nested.iterdir()), [])

    def test_staging_is_bounded_private_and_ignored_without_reading_its_contents(self):
        stage = self.write(".pft-instance-" + "a" * 32 + ".tmp", b"arbitrary interrupted staging bytes")
        registration = self.publish()
        self.assertTrue(stage.exists())
        registration.remove()
        stage.chmod(0o644)
        self.error("UNSAFE", self.publish)
        stage.chmod(0o600)
        stage.rename(self.directory / ".pft-instance-unknown.tmp")
        self.error("INVALID_ENTRY", self.publish)

    def test_record_and_entry_count_and_byte_budgets_are_enforced_without_cleanup(self):
        for index in range(1, 101): self.write_record(self.record(index))
        self.error("LIMIT", lambda: self.publish(self.record(101)))
        self.assertEqual(len(list(self.directory.iterdir())), 100)
        for path in self.directory.iterdir(): path.unlink()
        for index in range(200): self.write(".pft-instance-" + format(index, "032x") + ".tmp", b"")
        self.error("LIMIT", self.publish)
        self.assertEqual(len(list(self.directory.iterdir())), 200)
        for path in self.directory.iterdir(): path.unlink()
        oversized = self.write(self.record()["instance_id"] + ".json", b" " * 8193)
        self.error("LIMIT", lambda: self.publish(self.record(2)))
        self.assertEqual(oversized.stat().st_size, 8193)

    def test_exclusive_publication_keeps_a_record_created_after_initial_scan(self):
        original_link = registry.os.link
        foreign = self.record(service_id="e" * 32)
        def racing_link(source, target, **kwargs):
            self.write_record(foreign)
            return original_link(source, target, **kwargs)
        with patch.object(registry.os, "link", side_effect=racing_link):
            self.error("EXISTS", self.publish)
        target = self.directory / (foreign["instance_id"] + ".json")
        self.assertEqual(json.loads(target.read_bytes()), foreign)
        self.assertEqual(len(list(self.directory.iterdir())), 1)

    def test_prepublication_write_or_stage_fsync_failure_keeps_no_public_record(self):
        for operation in ("write", "fsync"):
            with self.subTest(operation=operation):
                with patch.object(registry.os, operation, side_effect=OSError(errno.EIO, "synthetic private detail")):
                    self.error("IO_FAILED", self.publish)
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_postpublication_fsync_failure_is_uncertain_and_does_not_delete_or_retry(self):
        original_fsync = registry.os.fsync
        def failing_directory_fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode): raise OSError(errno.EIO, "synthetic private detail")
            return original_fsync(fd)
        with patch.object(registry.os, "fsync", side_effect=failing_directory_fsync):
            self.error("PUBLISH_UNVERIFIED", self.publish, uncertain=True)
        target = self.directory / (self.record()["instance_id"] + ".json")
        self.assertEqual(json.loads(target.read_bytes()), self.record())
        self.assertEqual(len(list(self.directory.iterdir())), 1)
        self.error("EXISTS", self.publish)

    def test_remove_rejects_replaced_inode_changed_bytes_or_missing_record(self):
        for mutation in ("inode", "bytes", "missing"):
            with self.subTest(mutation=mutation):
                registration = self.publish()
                target = self.directory / (self.record()["instance_id"] + ".json")
                if mutation == "inode":
                    replacement = self.write(".pft-instance-" + "e" * 32 + ".tmp", target.read_bytes())
                    os.replace(replacement, target)
                elif mutation == "bytes":
                    target.write_bytes(json.dumps(self.record(service_id="e" * 32)).encode())
                else:
                    target.unlink()
                self.error("CHANGED", registration.remove)
                self.error("STATE_INVALID", registration.remove)
                if target.exists(): target.unlink()

    def test_remove_rejects_hardlink_or_mode_change_and_preserves_unrelated_entries(self):
        registration = self.publish()
        target = self.directory / (self.record()["instance_id"] + ".json")
        target.chmod(0o644)
        self.error("UNSAFE", registration.remove)
        target.chmod(0o600)
        target.unlink()
        registration = self.publish()
        outside = self.base / "extra-link"
        os.link(target, outside)
        self.error("UNSAFE", registration.remove)
        self.assertTrue(target.exists())
        target.unlink(); outside.unlink()
        registration = self.publish()
        unrelated = self.write("unrelated-preserved", b"other content")
        self.assertTrue(registration.remove())
        self.assertEqual(unrelated.read_bytes(), b"other content")

    def test_remove_rejects_replaced_directory_even_with_identical_record_bytes(self):
        registration = self.publish()
        old = self.base / "old-instances"
        self.directory.rename(old)
        self.directory.mkdir(mode=0o700)
        replacement = self.write_record()
        self.error("CHANGED", registration.remove)
        self.assertTrue(replacement.exists())
        self.assertTrue((old / replacement.name).exists())

    def test_removal_fsync_uncertain_never_claims_registration_still_exists_or_deletes_later_one(self):
        registration = self.publish()
        with patch.object(registry.os, "fsync", side_effect=OSError(errno.EIO, "synthetic private detail")):
            self.error("REMOVE_UNVERIFIED", registration.remove, uncertain=True)
        self.assertEqual(list(self.directory.iterdir()), [])
        later = self.write_record()
        self.error("STATE_INVALID", registration.remove)
        self.assertTrue(later.exists())

    def test_directory_lock_contention_and_unsupported_lock_are_explicit_without_new_files(self):
        _, fcntl = registry._supported()
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.error("BUSY", self.publish)
        fcntl.flock(fd, fcntl.LOCK_UN)
        with patch.object(fcntl, "flock", side_effect=OSError(errno.ENOTSUP, "unsupported")):
            self.error("UNSUPPORTED", self.publish)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_partial_native_read_and_write_remain_bounded_and_publish_complete_json(self):
        original_write = registry.os.write
        def partial_write(fd, data): return original_write(fd, data[:7])
        with patch.object(registry.os, "write", side_effect=partial_write):
            registration = self.publish()
        target = self.directory / (self.record()["instance_id"] + ".json")
        self.assertEqual(json.loads(target.read_bytes()), self.record())
        original_read = registry.os.read
        def partial_read(fd, amount): return original_read(fd, min(amount, 5))
        with patch.object(registry.os, "read", side_effect=partial_read):
            self.assertTrue(registration.remove())

    def test_entry_budget_reserves_both_stage_and_exclusive_published_link(self):
        for index in range(199): self.write(".pft-instance-" + format(index, "032x") + ".tmp", b"")
        self.error("LIMIT", self.publish)
        self.assertEqual(len(list(self.directory.iterdir())), 199)
        next(self.directory.iterdir()).unlink()
        maximum = []
        original_link = registry.os.link
        def observed_link(source, target, **kwargs):
            result = original_link(source, target, **kwargs)
            maximum.append(len(list(self.directory.iterdir())))
            return result
        with patch.object(registry.os, "link", side_effect=observed_link):
            registration = self.publish()
        self.assertEqual(maximum, [200])
        self.assertTrue(registration.remove())
        self.assertEqual(len(list(self.directory.iterdir())), 198)

    def test_cross_namespace_directory_identity_is_rechecked_before_publication(self):
        original_link = registry.os.link
        original_check = registry._check_directory
        old = self.base / "original-instances"
        def replace_before_check(path, private_from, identity):
            self.directory.rename(old)
            self.directory.mkdir(mode=0o700)
            return original_check(path, private_from, identity)
        with patch.object(registry, "_check_directory", side_effect=replace_before_check), \
             patch.object(registry.os, "link", wraps=original_link) as linked:
            self.error("CHANGED", self.publish)
        linked.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertEqual(list(old.iterdir()), [])

    def test_postpublication_staging_cleanup_uncertainty_is_not_automatically_retried(self):
        with patch.object(registry, "_unlink_staging", return_value=False) as cleanup:
            self.error("PUBLISH_UNVERIFIED", self.publish, uncertain=True)
        self.assertEqual(cleanup.call_count, 1)
        files = list(self.directory.iterdir())
        self.assertEqual(len(files), 2)
        self.assertTrue(any(path.name.endswith(".json") for path in files))
        self.assertTrue(any(path.name.endswith(".tmp") for path in files))
        self.assertTrue(all(path.stat().st_nlink == 2 for path in files))

    def test_postpublication_replacement_is_uncertain_and_never_deleted_as_owned(self):
        original_read = registry._read
        target_name = self.record()["instance_id"] + ".json"
        swapped = []
        foreign = self.record(service_id="d" * 32)
        def replaced_read(fd, name, expected_id):
            if name == target_name and not swapped:
                swapped.append(True)
                temporary = self.write(".pft-instance-" + "e" * 32 + ".tmp", json.dumps(foreign).encode())
                os.replace(temporary, self.directory / name)
            return original_read(fd, name, expected_id)
        with patch.object(registry, "_read", side_effect=replaced_read):
            self.error("PUBLISH_UNVERIFIED", self.publish, uncertain=True)
        self.assertEqual(json.loads((self.directory / target_name).read_bytes()), foreign)
        self.assertEqual(len(list(self.directory.iterdir())), 1)

    def test_private_files_are_bounded_before_content_read_and_runtime_root_never_accepts_public_mode(self):
        target = self.write(self.record()["instance_id"] + ".json", b"x" * 8193)
        original_open = registry.os.open
        opened_files = []
        def observed_open(name, flags, *args, **kwargs):
            if name == target.name: opened_files.append(name)
            return original_open(name, flags, *args, **kwargs)
        with patch.object(registry.os, "open", side_effect=observed_open):
            self.error("LIMIT", lambda: self.publish(self.record(2)))
        # Metadata-size check occurs after safe open but before any content read.
        self.assertEqual(opened_files, [target.name])
        runtime = self.base / "public-runtime"
        runtime.mkdir(mode=0o755)
        self.error("UNSAFE", lambda: registry.resolve_instance_directory(home=self.home, platform="linux",
            env={"XDG_RUNTIME_DIR": str(runtime), "XDG_STATE_HOME": str(self.base / "other")}))
        self.assertFalse((self.base / "other").exists())

    def test_no_registration_contains_paths_friendly_names_or_extra_mutable_fields(self):
        value = self.record()
        registration = self.publish(value)
        value["service_id"] = "d" * 32
        value["document"] = "Neutral ignored caller mutation"
        target = self.directory / (self.record()["instance_id"] + ".json")
        saved = json.loads(target.read_bytes())
        self.assertEqual(saved, self.record())
        self.assertEqual(set(saved), {"schema_version", "owner", "instance_id", "service_id", "pid", "url",
                                     "protocol_version", "addon_version"})
        self.assertNotIn(str(self.base), target.read_text())
        self.assertTrue(registration.remove())



if __name__ == "__main__": unittest.main()
