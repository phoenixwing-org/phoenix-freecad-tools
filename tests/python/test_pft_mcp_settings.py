# SPDX-License-Identifier: Apache-2.0
"""Only synthetic settings in owned temporary directories; no real preferences."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

MODULE = Path(__file__).parents[2] / "addon" / "PhoenixFreeCADTools" / "pft_mcp_settings.py"
spec = importlib.util.spec_from_file_location("pft_mcp_settings_test", MODULE)
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pft-mcp-settings-", dir="/private/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.kw = {"home": self.home, "platform": "darwin", "env": {}}
        self.path = settings.resolve_config_path(**self.kw)

    def payload(self, port=9876):
        return {"schema_version": 1, "bridge": {"preferred_port": port}}

    def seed(self, raw=None, mode=0o600):
        current = self.home
        for name in self.path.parent.relative_to(self.home).parts:
            current /= name
            current.mkdir(mode=0o700, exist_ok=True)
        self.path.write_bytes(json.dumps(self.payload()).encode() if raw is None else raw)
        self.path.chmod(mode)

    def error(self, code, call):
        with self.assertRaises(settings.PftMcpSettingsError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn(str(self.home), str(raised.exception))
        return raised.exception

    def read(self):
        return settings.read_config(**self.kw)

    def save(self, value=None, expected=None):
        return settings.save_config(self.payload() if value is None else value,
                                    expected_etag=expected, **self.kw)

    def test_platform_paths_relative_xdg_fallback_and_no_creation(self):
        self.assertEqual(self.path, self.home / "Library/Application Support/PhoenixTools/mcp/config.json")
        for xdg in ("", "relative/project"):
            self.assertEqual(settings.resolve_config_path(home=self.home, platform="linux",
                             env={"XDG_CONFIG_HOME": xdg}), self.home / ".config/PhoenixTools/mcp/config.json")
        xdg = self.home / "explicit"
        self.assertEqual(settings.resolve_config_path(home=self.home, platform="linux",
                         env={"XDG_CONFIG_HOME": str(xdg)}), xdg / "PhoenixTools/mcp/config.json")
        self.assertEqual(settings.resolve_config_path(home=self.home, platform="win32",
                         env={"LOCALAPPDATA": str(self.home)}), self.home / "PhoenixTools/mcp/config.json")
        self.assertEqual(list(self.home.iterdir()), [])
        self.error("MCP_SETTINGS_PATH_INVALID", lambda: settings.resolve_config_path(home="relative", platform="linux", env={}))
        self.error("MCP_SETTINGS_PATH_INVALID", lambda: settings.resolve_config_path(home=self.home, platform="win32", env={}))

    def test_missing_returns_fresh_default_without_files(self):
        record = self.read()
        self.assertEqual(record, {"source": "default", "settings": self.payload(),
                                 "config_path": str(self.path), "etag": None})
        record["settings"]["bridge"]["preferred_port"] = 1234
        self.assertEqual(self.read()["settings"], self.payload())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_save_read_permissions_roundtrip_and_update(self):
        first = self.save()
        self.assertEqual(first, self.read())
        self.assertEqual(first["source"], "user_config")
        self.assertEqual(len(first["etag"]), 64)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.path.stat().st_nlink, 1)
        current = self.path.parent
        while current != self.home:
            self.assertEqual(stat.S_IMODE(current.stat().st_mode), 0o700)
            current = current.parent
        second = self.save(self.payload(65535), first["etag"])
        self.assertEqual(second["settings"], self.payload(65535))
        self.assertNotEqual(first["etag"], second["etag"])
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_valid_port_boundaries(self):
        for port in (1024, 9876, 65535):
            with self.subTest(port=port):
                self.seed(json.dumps(self.payload(port)).encode())
                self.assertEqual(self.read()["settings"], self.payload(port))

    def test_invalid_schema_types_ranges_and_unimplemented_fields(self):
        invalid = [None, [], {}, {"schema_version": True, "bridge": {"preferred_port": 9876}},
                   {"schema_version": 1.0, "bridge": {"preferred_port": 9876}},
                   {"schema_version": 1, "bridge": []},
                   {"schema_version": 1, "bridge": {"preferred_port": 9876}, "discovery": {}},
                   {"schema_version": 1, "bridge": {"preferred_port": 9876, "host": "localhost"}}]
        invalid += [self.payload(port) for port in (False, True, 9876.0, "9876", 1023, 65536, None)]
        for value in invalid:
            with self.subTest(value=value):
                before = list(self.home.iterdir())
                self.error("MCP_SETTINGS_INVALID", lambda: settings.save_config(value, expected_etag=None, **self.kw))
                self.assertEqual(list(self.home.iterdir()), before)
                self.seed(json.dumps(value).encode())
                self.error("MCP_SETTINGS_INVALID", self.read)
                self.path.unlink()
                # Leave known private directories; validation itself must not create files.

    def test_v2_discovery_is_an_explicit_strict_boolean_with_no_implicit_v1_migration(self):
        first = self.save()
        before = self.path.read_bytes(), self.path.stat().st_ino, self.path.stat().st_mtime_ns
        self.assertEqual(self.read()["settings"], self.payload())
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_ino, self.path.stat().st_mtime_ns), before)
        for enabled in (True, False):
            selected = {"schema_version": 2, "bridge": {"preferred_port": 12340}, "discovery": {"enabled": enabled}}
            result = settings.save_config(selected, expected_etag=first["etag"], **self.kw)
            self.assertEqual(result["settings"], selected)
            self.assertEqual(self.read(), result)
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
            self.assertNotEqual(result["etag"], first["etag"])
            first = result
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_v2_discovery_rejects_missing_unknown_coerced_and_duplicate_fields(self):
        base = {"schema_version": 2, "bridge": {"preferred_port": 9876}}
        invalid = [base, {**base, "discovery": None}, {**base, "discovery": {}},
                   {**base, "discovery": {"enabled": True, "directory": "/neutral/unsupported"}},
                   {**base, "discovery": {"enabled": True}, "extra": False},
                   {**self.payload(), "discovery": {"enabled": False}}]
        invalid += [{**base, "discovery": {"enabled": value}} for value in (0, 1, "true", "false", None, [], {})]
        for value in invalid:
            with self.subTest(value_type=type(value.get("discovery")).__name__):
                self.error("MCP_SETTINGS_INVALID", lambda: settings.save_config(value, expected_etag=None, **self.kw))
                self.seed(json.dumps(value).encode())
                before = self.path.read_bytes()
                self.error("MCP_SETTINGS_INVALID", self.read)
                self.assertEqual(self.path.read_bytes(), before)
                self.path.unlink()
        duplicates = [
            b'{"schema_version":2,"bridge":{"preferred_port":9876},"discovery":{"enabled":true,"enabled":false}}',
            b'{"schema_version":2,"bridge":{"preferred_port":9876},"discovery":{"enabled":true},"discovery":{"enabled":false}}',
            b'{"schema_version":2,"bridge":{"preferred_port":9876},"discovery":{"enabled":true,"enabl\\u0065d":false}}',
        ]
        for raw in duplicates:
            self.seed(raw)
            self.error("MCP_SETTINGS_INVALID", self.read)
            self.assertEqual(self.path.read_bytes(), raw)
            self.path.unlink()

    def test_unknown_version_never_falls_back_or_overwrites(self):
        raw = b'{"schema_version":3,"bridge":{"preferred_port":9876}}'
        self.seed(raw)
        self.error("MCP_SETTINGS_VERSION_UNSUPPORTED", self.read)
        self.error("MCP_SETTINGS_VERSION_UNSUPPORTED", self.save)
        self.assertEqual(self.path.read_bytes(), raw)

    def test_duplicate_keys_nan_utf8_bom_trailing_and_nested_json(self):
        invalid = [
            b'{"schema_version":1,"schema_version":1,"bridge":{"preferred_port":9876}}',
            b'{"schema_version":1,"bridge":{"preferred_port":9876,"preferred_port":9877}}',
            b'{"schema_version":1,"bridge":{"preferred_port":NaN}}',
            b'{"schema_version":1,"bridge":{"preferred_port":Infinity}}',
            b'{"schema_version":1,"bridge":{"preferred_port":-Infinity}}',
            b'{"schema_version":1,"bridge":{"preferred_port":[9876]}}',
            b'{"schema_version":1,"bridge":{"preferred_port":9876}}{}',
            b'\xef\xbb\xbf{"schema_version":1,"bridge":{"preferred_port":9876}}',
            b'\xff', b'', b'[' * 1100 + b']' * 1100,
        ]
        for raw in invalid:
            with self.subTest(raw=raw[:50]):
                self.seed(raw)
                self.error("MCP_SETTINGS_INVALID", self.read)
                self.error("MCP_SETTINGS_INVALID", self.save)
                self.assertEqual(self.path.read_bytes(), raw)

    def test_byte_budget_8192_8193(self):
        raw = json.dumps(self.payload()).encode()
        self.seed(raw + b" " * (8192-len(raw)))
        self.assertEqual(self.read()["settings"], self.payload())
        self.seed(raw + b" " * (8193-len(raw)))
        self.error("MCP_SETTINGS_INVALID", self.read)

    def test_existing_application_directories_are_not_repaired(self):
        self.seed()
        for directory in (self.path.parent, self.path.parent.parent):
            directory.chmod(0o755)
            self.error("MCP_SETTINGS_UNSAFE", self.read)
            self.error("MCP_SETTINGS_UNSAFE", self.save)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o755)
            directory.chmod(0o700)

    def test_existing_system_ancestors_may_use_normal_permissions(self):
        self.seed()
        base = self.home / "Library"
        base.chmod(0o755)
        before = self.read()
        self.save(self.payload(1024), before["etag"])
        self.assertEqual(stat.S_IMODE(base.stat().st_mode), 0o755)

    def test_file_permissions_hardlink_and_owner_rejected(self):
        self.seed(mode=0o644)
        self.error("MCP_SETTINGS_UNSAFE", self.read)
        self.path.chmod(0o600)
        other = self.home / "hardlink"
        os.link(self.path, other)
        self.error("MCP_SETTINGS_UNSAFE", self.read)
        other.unlink()
        with patch.object(settings.os, "getuid", return_value=os.getuid()+1):
            self.error("MCP_SETTINGS_UNSAFE", self.read)

    def test_symlink_at_file_and_every_parent_is_rejected(self):
        self.seed()
        original = self.path.read_bytes()
        self.path.unlink()
        self.path.symlink_to(self.home / "absent")
        self.error("MCP_SETTINGS_UNSAFE", self.read)
        self.path.unlink()
        self.path.write_bytes(original)
        self.path.chmod(0o600)
        for directory in (self.path.parent, self.path.parent.parent, self.home / "Library"):
            moved = directory.with_name(directory.name + "-owned")
            directory.rename(moved)
            directory.symlink_to(moved, target_is_directory=True)
            self.error("MCP_SETTINGS_UNREADABLE", self.read)
            self.error("MCP_SETTINGS_UNREADABLE", self.save)
            directory.unlink()
            moved.rename(directory)

    def test_directory_and_fifo_cannot_be_read_as_config(self):
        self.seed()
        self.path.unlink()
        self.path.mkdir(mode=0o700)
        self.error("MCP_SETTINGS_UNSAFE", self.read)
        self.path.rmdir()
        os.mkfifo(self.path, 0o600)
        self.error("MCP_SETTINGS_UNSAFE", self.read)

    def test_only_missing_not_permission_errors_uses_default(self):
        with patch.object(settings.os, "open", side_effect=PermissionError("secret host path")):
            failure = self.error("MCP_SETTINGS_UNREADABLE", self.read)
        self.assertNotIn("secret", str(failure))

    def test_stale_etag_and_missing_expectation_are_rejected(self):
        first = self.save()
        self.error("MCP_SETTINGS_CHANGED", self.save)
        self.save(self.payload(1234), first["etag"])
        self.error("MCP_SETTINGS_CHANGED", lambda: self.save(self.payload(2345), first["etag"]))
        self.assertEqual(self.read()["settings"], self.payload(1234))
        self.error("MCP_SETTINGS_INVALID", lambda: self.save(expected=True))

    def test_same_bytes_replaced_with_new_inode_changes_etag(self):
        first = self.save()
        raw = self.path.read_bytes()
        replacement = self.path.with_name("replacement")
        replacement.write_bytes(raw)
        replacement.chmod(0o600)
        os.replace(replacement, self.path)
        self.assertNotEqual(self.read()["etag"], first["etag"])
        self.error("MCP_SETTINGS_CHANGED", lambda: self.save(expected=first["etag"]))

    def test_unknown_existing_lock_is_retained(self):
        first = self.save()
        lock = self.path.with_name("config.json.lock")
        lock.write_bytes(b"unknown old owner")
        lock.chmod(0o600)
        self.error("MCP_SETTINGS_BUSY", lambda: self.save(expected=first["etag"]))
        self.assertEqual(lock.read_bytes(), b"unknown old owner")
        self.assertEqual(self.read()["settings"], self.payload())

    def test_under_lock_recheck_detects_concurrent_change(self):
        first = self.save()
        real_owned = settings._owned
        def race(parent, name):
            value = real_owned(parent, name)
            if name == "config.json.lock":
                self.path.write_text(json.dumps(self.payload(4321)))
            return value
        with patch.object(settings, "_owned", side_effect=race):
            self.error("MCP_SETTINGS_CHANGED", lambda: self.save(expected=first["etag"]))
        self.assertEqual(self.read()["settings"], self.payload(4321))
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_write_or_prepublication_fsync_failure_preserves_original(self):
        first = self.save()
        raw = self.path.read_bytes()
        for target in ("write", "fsync"):
            with self.subTest(target=target), patch.object(settings.os, target, side_effect=OSError("private failure")):
                failure = self.error("MCP_SETTINGS_WRITE_FAILED", lambda: self.save(self.payload(1234), first["etag"]))
                self.assertFalse(failure.may_have_saved)
            self.assertEqual(self.path.read_bytes(), raw)
            self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_after_publication_fsync_failure_is_explicitly_uncertain(self):
        first = self.save()
        real_fsync = os.fsync
        def fail_directory(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("private directory")
            return real_fsync(descriptor)
        with patch.object(settings.os, "fsync", side_effect=fail_directory):
            failure = self.error("MCP_SETTINGS_SAVE_UNVERIFIED", lambda: self.save(self.payload(1234), first["etag"]))
        self.assertTrue(failure.may_have_saved)
        self.assertEqual(self.read()["settings"], self.payload(1234))
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_postpublication_readback_failure_does_not_restore_or_retry(self):
        first = self.save()
        real_read = settings._read_at
        calls = []
        def fail_fourth(parent, path):
            calls.append(None)
            if len(calls) == 4:
                raise OSError("private readback content")
            return real_read(parent, path)
        with patch.object(settings, "_read_at", side_effect=fail_fourth):
            failure = self.error("MCP_SETTINGS_SAVE_UNVERIFIED", lambda: self.save(self.payload(1234), first["etag"]))
        self.assertEqual(len(calls), 4)
        self.assertTrue(failure.may_have_saved)
        self.assertEqual(self.read()["settings"], self.payload(1234))

    def test_replaced_lock_is_never_deleted(self):
        first = self.save()
        real_check = settings._check_owned
        raced = []
        def replace_lock(parent, owned):
            if owned[0] == "config.json.lock" and not raced:
                raced.append(True)
                lock = self.path.with_name(owned[0])
                lock.unlink()
                lock.write_bytes(b"foreign replacement")
                lock.chmod(0o600)
            return real_check(parent, owned)
        with patch.object(settings, "_check_owned", side_effect=replace_lock):
            self.error("MCP_SETTINGS_CLEANUP_UNVERIFIED", lambda: self.save(expected=first["etag"]))
        self.assertEqual(self.path.with_name("config.json.lock").read_bytes(), b"foreign replacement")

    def test_windows_missing_default_existing_and_save_unsupported(self):
        kw = {"home": self.home, "platform": "win32", "env": {"LOCALAPPDATA": str(self.home)}}
        path = settings.resolve_config_path(**kw)
        self.assertEqual(settings.read_config(**kw)["source"], "default")
        self.error("MCP_SETTINGS_UNSUPPORTED", lambda: settings.save_config(self.payload(), expected_etag=None, **kw))
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(self.payload()))
        self.error("MCP_SETTINGS_UNSUPPORTED", lambda: settings.read_config(**kw))
        path.unlink()
        path.symlink_to(self.home / "absent")
        self.error("MCP_SETTINGS_UNSAFE", lambda: settings.read_config(**kw))

    def test_nonexistent_injected_root_is_not_created(self):
        missing = self.home / "not-created"
        kw = {"home": missing, "platform": "linux", "env": {}}
        self.assertEqual(settings.read_config(**kw)["source"], "default")
        self.error("MCP_SETTINGS_PATH_INVALID", lambda: settings.save_config(self.payload(), expected_etag=None, **kw))
        self.assertFalse(missing.exists())

    def test_owner_execute_and_special_file_modes_are_rejected(self):
        for mode in (0o700, 0o1600):
            with self.subTest(mode=mode):
                self.seed(mode=mode)
                self.error("MCP_SETTINGS_UNSAFE", self.read)

    def test_relative_xdg_reads_actual_fallback_and_absolute_xdg_uses_selected_root(self):
        relative = {"home": self.home, "platform": "linux", "env": {"XDG_CONFIG_HOME": "relative"}}
        saved = settings.save_config(self.payload(1234), expected_etag=None, **relative)
        self.assertEqual(saved["config_path"], str(self.home / ".config/PhoenixTools/mcp/config.json"))
        self.assertEqual(settings.read_config(**relative), saved)
        root = self.home / "xdg"
        root.mkdir(mode=0o700)
        absolute = {"home": self.home, "platform": "linux", "env": {"XDG_CONFIG_HOME": str(root)}}
        selected = settings.save_config(self.payload(4321), expected_etag=None, **absolute)
        self.assertEqual(selected["settings"], self.payload(4321))
        self.assertEqual(settings.read_config(**relative)["settings"], self.payload(1234))

    def test_real_competing_writer_is_nonblocking_and_only_one_publishes(self):
        first = self.save()
        acquired, release = threading.Event(), threading.Event()
        real_owned = settings._owned
        outcome = []
        def hold(parent, name):
            result = real_owned(parent, name)
            if name == "config.json.lock":
                acquired.set()
                if not release.wait(2):
                    raise RuntimeError("Synthetic writer gate timed out")
            return result
        def writer():
            try:
                outcome.append(self.save(self.payload(1234), first["etag"]))
            except Exception as error:
                outcome.append(error)
        with patch.object(settings, "_owned", side_effect=hold):
            thread = threading.Thread(target=writer)
            thread.start()
            try:
                self.assertTrue(acquired.wait(2))
                self.error("MCP_SETTINGS_BUSY", lambda: self.save(self.payload(2345), first["etag"]))
            finally:
                release.set()
                thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], dict)
        self.assertEqual(self.read()["settings"], self.payload(1234))

    def test_failure_initializing_owned_file_removes_only_that_file(self):
        first = self.save()
        with patch.object(settings.os, "fchmod", side_effect=OSError("private setup")):
            failure = self.error("MCP_SETTINGS_WRITE_FAILED", lambda: self.save(expected=first["etag"]))
        self.assertFalse(failure.may_have_saved)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_publish_then_raise_is_uncertain_and_not_retried(self):
        first = self.save()
        real_replace = settings.os.replace
        calls = []
        def publish_and_fail(*args, **kwargs):
            calls.append(None)
            real_replace(*args, **kwargs)
            raise OSError("Synthetic unknown publish result")
        with patch.object(settings.os, "replace", side_effect=publish_and_fail):
            failure = self.error("MCP_SETTINGS_SAVE_UNVERIFIED", lambda: self.save(self.payload(1234), first["etag"]))
        self.assertTrue(failure.may_have_saved)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.read()["settings"], self.payload(1234))

    def test_replaced_temporary_name_is_not_published_or_deleted(self):
        first = self.save()
        original = self.path.read_bytes()
        real_check = settings._check_owned
        foreign = []
        def change(parent, owned):
            if owned[0].endswith(".tmp") and not foreign:
                temporary = self.path.with_name(owned[0])
                temporary.rename(temporary.with_name("owned-original"))
                temporary.write_bytes(b"foreign temporary")
                temporary.chmod(0o600)
                foreign.append(temporary)
            return real_check(parent, owned)
        with patch.object(settings, "_check_owned", side_effect=change):
            self.error("MCP_SETTINGS_CLEANUP_UNVERIFIED", lambda: self.save(expected=first["etag"]))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(foreign[0].read_bytes(), b"foreign temporary")

    def test_parent_replacement_is_rejected_and_foreign_config_retained(self):
        first = self.save()
        real_same = settings._same_parent
        raced = []
        def replace_parent(parent, path, create_from):
            if not raced:
                raced.append(True)
                directory = self.path.parent
                directory.rename(directory.with_name("mcp-owned"))
                directory.mkdir(mode=0o700)
                self.path.write_text(json.dumps(self.payload(4321)))
                self.path.chmod(0o600)
            return real_same(parent, path, create_from)
        with patch.object(settings, "_same_parent", side_effect=replace_parent):
            self.error("MCP_SETTINGS_CHANGED", lambda: self.save(expected=first["etag"]))
        self.assertEqual(self.read()["settings"], self.payload(4321))


    def test_missing_config_in_nonprivate_application_parent_is_default_but_save_rejects(self):
        self.seed()
        self.path.unlink()
        for directory in (self.path.parent, self.path.parent.parent):
            directory.chmod(0o755)
        self.assertEqual(self.read()["source"], "default")
        self.error("MCP_SETTINGS_UNSAFE", self.save)
        self.assertFalse(self.path.exists())

    def test_noncanonical_or_control_paths_are_rejected(self):
        for value in (str(self.home)+"/", str(self.home)+"//child",
                      str(self.home)+"/./child", str(self.home)+"/../child",
                      str(self.home)+"/name\u200b", str(self.home)+" "):
            with self.subTest(value=value):
                self.error("MCP_SETTINGS_PATH_INVALID",
                           lambda: settings.resolve_config_path(home=value, platform="linux", env={}))

    def test_cleanup_close_error_remains_fixed_and_cleans_lock_after_publish(self):
        first = self.save()
        real_owned, real_close = settings._owned, settings.os.close
        temporary_fd = []
        def remember(parent, name):
            owned = real_owned(parent, name)
            if name.endswith(".tmp"):
                temporary_fd.append(owned[1])
            return owned
        def close_then_fail(descriptor):
            real_close(descriptor)
            if descriptor in temporary_fd:
                temporary_fd.remove(descriptor)
                raise OSError("private close diagnostic")
        with patch.object(settings, "_owned", side_effect=remember), patch.object(settings.os, "close", side_effect=close_then_fail):
            failure = self.error("MCP_SETTINGS_CLEANUP_UNVERIFIED",
                                 lambda: self.save(self.payload(1234), first["etag"]))
        self.assertTrue(failure.may_have_saved)
        self.assertNotIn("private", str(failure))
        self.assertEqual(self.read()["settings"], self.payload(1234))
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


if __name__ == "__main__":
    unittest.main()
