# SPDX-License-Identifier: Apache-2.0
"""Private temporary profiles only; no process launch, real credentials or history."""
import errno
import gc
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
_saved_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_chat_process_lease as leases
    from pft_chat_profile import prepare_chat_profile
finally:
    sys.path[:] = _saved_path


@unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "POSIX native lease")
class ChatProcessLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pft-chat-lease-")
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name).resolve()
        self.profile = prepare_chat_profile(str(self.data))
        self.home, self.cwd = self.profile["home"], self.profile["cwd"]
        self.base = Path(self.home).parent
        self.path, self.lock = self.base / leases.LEASE_FILENAME, self.base / leases.LOCK_FILENAME
        self.program = self.data / "owned-program-fixture"
        self.program.write_text("nonexecuted fixture", encoding="utf-8")
        self.program.chmod(0o700)

    def acquire(self):
        return leases.acquire(self.home, self.cwd, str(self.program))

    def record(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def assert_error(self, suffix, call):
        with self.assertRaises(leases.PftChatProcessLeaseError) as raised:
            call()
        self.assertEqual(raised.exception.code, "CHAT_PROCESS_LEASE_" + suffix)
        self.assertNotIn(str(self.data), str(raised.exception))
        return raised.exception

    def write_record(self, record):
        self.path.write_text(json.dumps(record), encoding="utf-8")
        self.path.chmod(0o600)

    def test_acquire_creates_durable_launching_record_and_stable_private_lock(self):
        owned = self.acquire()
        record = self.record()
        self.assertEqual(set(record), {"schema_version", "owner", "owner_protocol", "nonce", "parent_pid", "child_pid", "state", "program"})
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["owner"], "phoenix-freecad-tools")
        self.assertEqual(record["owner_protocol"], leases.OWNER_PROTOCOL)
        self.assertEqual(len(record["nonce"]), 64)
        self.assertEqual(record["parent_pid"], os.getpid())
        self.assertIsNone(record["child_pid"])
        self.assertEqual(record["state"], "launching")
        self.assertEqual(record["program"], str(self.program))
        for path in (self.path, self.lock):
            info = path.lstat()
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
            self.assertEqual(info.st_nlink, 1)
            self.assertEqual(info.st_uid, os.getuid())
        self.assertEqual(self.lock.stat().st_size, 0)
        self.assertEqual(list(self.base.glob(".process-lease-*.tmp")), [])
        self.assertIsInstance(owned, leases.PftChatProcessLease)

    def test_launching_and_running_registrations_always_block_a_second_acquisition(self):
        owned = self.acquire()
        before = self.path.read_bytes()
        self.assert_error("BUSY", self.acquire)
        self.assertEqual(self.path.read_bytes(), before)
        owned.mark_started(31234)
        before = self.path.read_bytes()
        self.assert_error("BUSY", self.acquire)
        self.assertEqual(self.path.read_bytes(), before)

    def test_mark_started_atomically_replaces_only_owned_lease_and_keeps_lock_inode(self):
        owned = self.acquire()
        lock_inode, before = self.lock.stat().st_ino, self.record()
        lease_inode = self.path.stat().st_ino
        replace = os.replace
        observed = []
        def publish(source, target, **kwargs):
            observed.append(self.record()["state"])
            return replace(source, target, **kwargs)
        with patch.object(leases.os, "replace", side_effect=publish):
            self.assertTrue(owned.mark_started(31234))
        self.assertEqual(observed, ["launching"])
        self.assertEqual(self.record(), {**before, "state": "running", "child_pid": 31234})
        self.assertNotEqual(self.path.stat().st_ino, lease_inode)
        self.assertEqual(self.lock.stat().st_ino, lock_inode)
        self.assertEqual(list(self.base.glob(".process-lease-*.tmp")), [])
        with patch.object(leases.os, "replace", side_effect=AssertionError("same PID is not rewritten")):
            self.assertTrue(owned.mark_started(31234))
        self.assert_error("STATE_INVALID", lambda: owned.mark_started(31235))

    def test_release_failed_to_start_removes_only_lease_and_reuses_stable_lock(self):
        owned = self.acquire()
        lock_inode = self.lock.stat().st_ino
        self.assertTrue(owned.release_failed_to_start())
        self.assertFalse(self.path.exists())
        self.assertEqual(self.lock.stat().st_ino, lock_inode)
        later = self.acquire()
        self.assertEqual(self.lock.stat().st_ino, lock_inode)
        later_nonce = self.record()["nonce"]
        self.assertTrue(owned.release_failed_to_start())
        self.assertEqual(self.record()["nonce"], later_nonce)
        self.assertTrue(later.release_failed_to_start())

    def test_confirmed_exit_can_release_running_or_still_launching_owned_registration(self):
        for started in (False, True):
            owned = self.acquire()
            if started:
                owned.mark_started(31234)
                self.assert_error("STATE_INVALID", owned.release_failed_to_start)
            self.assertTrue(owned.release_after_exit())
            self.assertFalse(self.path.exists())
            self.assertTrue(owned.release_after_exit())
            self.assert_error("STATE_INVALID", lambda: owned.mark_started(31234))

    def test_invalid_pids_never_modify_registration(self):
        owned = self.acquire()
        before = self.path.read_bytes()
        for pid in (None, True, False, 0, -1, 1.0, "12", 2 ** 31):
            with self.subTest(pid=pid):
                self.assert_error("STATE_INVALID", lambda: owned.mark_started(pid))
                self.assertEqual(self.path.read_bytes(), before)

    def test_discarded_owner_does_not_clean_registration_or_infer_parent_death(self):
        owned = self.acquire()
        owned.mark_started(31234)
        before = self.path.read_bytes()
        del owned
        gc.collect()
        with patch.object(leases.os, "kill", side_effect=AssertionError("must not probe or signal a PID")):
            self.assert_error("BUSY", self.acquire)
        self.assertEqual(self.path.read_bytes(), before)
        record = self.record()
        record["parent_pid"], record["child_pid"] = 2 ** 31 - 2, 2 ** 31 - 3
        self.write_record(record)
        os.utime(self.path, (1, 1))
        before = self.path.read_bytes()
        with patch.object(leases.os, "kill", side_effect=AssertionError("old records are never auto-cleaned")):
            self.assert_error("BUSY", self.acquire)
        self.assertEqual(self.path.read_bytes(), before)

    def test_existing_v1_profile_does_not_inspect_auth_history_or_workspace_contents(self):
        auth = Path(self.home) / "auth.json"
        history = Path(self.home) / "history.jsonl"
        auth.write_text("synthetic credential sentinel", encoding="utf-8")
        history.write_text("synthetic history sentinel", encoding="utf-8")
        auth.chmod(0)
        history.chmod(0)
        opener, opened = os.open, []
        def observe(path, *args, **kwargs):
            opened.append(os.fspath(path))
            if Path(path).name in {"auth.json", "history.jsonl"}:
                raise AssertionError("must never open authentication or conversation files")
            return opener(path, *args, **kwargs)
        with patch.object(leases.os, "open", side_effect=observe):
            self.acquire()
        self.assertFalse({"auth.json", "history.jsonl"}.intersection(opened))
        auth.chmod(0o600)
        history.chmod(0o600)
        self.assertEqual(auth.read_text(), "synthetic credential sentinel")
        self.assertEqual(history.read_text(), "synthetic history sentinel")

    def test_short_flock_contention_refuses_without_creating_lease(self):
        import fcntl
        self.lock.write_bytes(b"")
        self.lock.chmod(0o600)
        with self.lock.open("r+b") as external:
            fcntl.flock(external.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_error("BUSY", self.acquire)
            self.assertFalse(self.path.exists())
        self.assertTrue(self.acquire().release_failed_to_start())

    def test_lock_is_not_held_between_operations(self):
        import fcntl
        owned = self.acquire()
        with self.lock.open("r+b") as other:
            fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(other.fileno(), fcntl.LOCK_UN)
        owned.mark_started(31234)
        with self.lock.open("r+b") as other:
            fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(other.fileno(), fcntl.LOCK_UN)

    def test_corrupt_unknown_oversized_duplicate_and_non_integer_json_records_are_retained(self):
        valid = {"schema_version": 1, "owner": "phoenix-freecad-tools", "owner_protocol": leases.OWNER_PROTOCOL,
                 "nonce": "a" * 64, "parent_pid": 123, "child_pid": None, "state": "launching", "program": str(self.program)}
        malformed = [b"", b"not-json", b"\xff", b"[]", b"{}", b"x" * (leases.MAX_LEASE_BYTES + 1),
                     json.dumps({**valid, "schema_version": 2}).encode(),
                     json.dumps({**valid, "owner_protocol": "unrecognized"}).encode(),
                     json.dumps({**valid, "parent_pid": True}).encode(),
                     json.dumps({**valid, "child_pid": 123}).encode(),
                     json.dumps({**valid, "state": "running"}).encode(),
                     json.dumps({**valid, "extra": "unknown"}).encode(),
                     json.dumps({**valid, "nonce": "X" * 64}).encode(),
                     json.dumps({**valid, "program": "relative"}).encode(),
                     json.dumps(valid).replace('"schema_version": 1', '"schema_version": 1.0').encode(),
                     json.dumps(valid).replace('"parent_pid": 123', '"parent_pid": NaN').encode(),
                     (json.dumps(valid)[:-1] + ',"state":"launching"}').encode()]
        for raw in malformed:
            with self.subTest(length=len(raw)):
                self.path.write_bytes(raw)
                self.path.chmod(0o600)
                self.assert_error("INVALID", self.acquire)
                self.assertEqual(self.path.read_bytes(), raw)

    def test_symlink_hardlink_fifo_or_nonprivate_lease_is_retained(self):
        target = self.data / "unrelated-file"
        target.write_text("keep")
        self.path.symlink_to(target)
        self.assert_error("UNSAFE", self.acquire)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(target.read_text(), "keep")
        self.path.unlink()
        self.path.write_text("keep")
        self.path.chmod(0o600)
        os.link(self.path, target.with_name("linked-registration"))
        self.assert_error("UNSAFE", self.acquire)
        self.assertEqual(self.path.read_text(), "keep")
        self.path.unlink()
        os.mkfifo(self.path, 0o600)
        self.assert_error("UNSAFE", self.acquire)
        self.assertTrue(stat.S_ISFIFO(self.path.lstat().st_mode))
        self.path.unlink()
        self.path.write_text("keep")
        self.path.chmod(0o644)
        self.assert_error("UNSAFE", self.acquire)
        self.assertEqual(self.path.read_text(), "keep")

    def test_lock_permissions_hardlink_and_nonempty_inode_are_rejected_without_repair(self):
        self.lock.write_bytes(b"")
        self.lock.chmod(0o644)
        self.assert_error("UNSAFE", self.acquire)
        self.assertEqual(stat.S_IMODE(self.lock.stat().st_mode), 0o644)
        self.lock.chmod(0o600)
        alias = self.data / "lock-alias"
        os.link(self.lock, alias)
        self.assert_error("UNSAFE", self.acquire)
        self.assertEqual(self.lock.stat().st_nlink, 2)
        alias.unlink()
        self.lock.write_bytes(b"unknown")
        self.assert_error("CHANGED", self.acquire)
        self.assertEqual(self.lock.read_bytes(), b"unknown")
        self.assertFalse(self.path.exists())

    def test_unsafe_profile_paths_and_managed_files_do_not_create_registration(self):
        for directory in (self.base, Path(self.home), Path(self.cwd)):
            with self.subTest(directory=directory.name):
                directory.chmod(0o755)
                self.assert_error("UNSAFE", self.acquire)
                self.assertFalse(self.path.exists())
                directory.chmod(0o700)
        for filename in (self.base / "profile.json", Path(self.home) / "config.toml"):
            filename.chmod(0o644)
            self.assert_error("UNSAFE", self.acquire)
            filename.chmod(0o600)
        marker = self.base / "profile.json"
        old = marker.read_bytes()
        marker.write_text('{"owner":"phoenix-freecad-tools","schema_version":true,"purpose":"dedicated-native-chat"}')
        self.assert_error("PROFILE_INVALID", self.acquire)
        marker.write_bytes(old)
        config = Path(self.home) / "config.toml"
        config.write_text("unknown configuration")
        self.assert_error("PROFILE_INVALID", self.acquire)
        self.assertEqual(config.read_text(), "unknown configuration")

    def test_no_symlink_ancestor_or_mismatched_layout_is_accepted(self):
        alias = self.data / "via-link"
        alias.symlink_to(self.data, target_is_directory=True)
        with self.assertRaises(leases.PftChatProcessLeaseError):
            leases.acquire(str(alias / "PhoenixTools" / "CodexChat" / "home"),
                           str(alias / "PhoenixTools" / "CodexChat" / "workspace"), str(self.program))
        self.assertFalse(self.path.exists())
        for home, cwd in ((self.cwd, self.home), (self.home + "/..", self.cwd), (self.home, self.home), ("relative", self.cwd)):
            self.assert_error("PROFILE_INVALID", lambda: leases.acquire(home, cwd, str(self.program)))
        self.assertFalse(self.path.exists())

    def test_record_nonce_or_inode_replacement_is_never_adopted_or_removed(self):
        owned = self.acquire()
        old = self.record()
        self.write_record({**old, "nonce": "f" * 64})
        before = self.path.read_bytes()
        self.assert_error("CHANGED", owned.release_after_exit)
        self.assertEqual(self.path.read_bytes(), before)
        self.write_record(old)
        replacement = self.base / "replacement-fixture"
        replacement.write_bytes(self.path.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(self.path)
        before = self.path.read_bytes()
        self.assert_error("CHANGED", lambda: owned.mark_started(31234))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.record()["state"], "launching")

    def test_lock_inode_replacement_during_update_preserves_existing_registration(self):
        owned = self.acquire()
        before = self.path.read_bytes()
        stage = leases._stage
        def replace_lock(parent, raw):
            result = stage(parent, raw)
            replacement = self.base / "new-lock-fixture"
            replacement.write_bytes(b"")
            replacement.chmod(0o600)
            replacement.replace(self.lock)
            return result
        with patch.object(leases, "_stage", side_effect=replace_lock):
            self.assert_error("CHANGED", lambda: owned.mark_started(31234))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.base.glob(".process-lease-*.tmp")), [])
        self.assert_error("CHANGED", owned.release_after_exit)

    def test_same_named_profile_directory_replacement_is_not_the_owned_profile(self):
        owned = self.acquire()
        old_base = self.base.with_name("retained-profile-fixture")
        self.base.rename(old_base)
        prepare_chat_profile(str(self.data))
        self.assert_error("CHANGED", owned.release_after_exit)
        self.assertTrue((old_base / leases.LEASE_FILENAME).exists())
        self.assertFalse(self.path.exists())

    def test_write_failure_does_not_replace_old_registration_and_cleans_only_owned_stage(self):
        owned = self.acquire()
        before = self.path.read_bytes()
        with patch.object(leases.os, "write", side_effect=OSError("synthetic write failure")):
            self.assert_error("IO_FAILED", lambda: owned.mark_started(31234))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.base.glob(".process-lease-*.tmp")), [])
        self.assertTrue(owned.release_failed_to_start())

    def test_postpublication_fsync_failure_retains_running_record_and_blocks_recovery(self):
        owned = self.acquire()
        fsync = os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("synthetic directory fsync failure")
            return fsync(fd)
        with patch.object(leases.os, "fsync", side_effect=fail_directory):
            error = self.assert_error("UPDATE_UNVERIFIED", lambda: owned.mark_started(31234))
        self.assertTrue(error.uncertain)
        self.assertEqual(self.record()["state"], "running")
        self.assert_error("UPDATE_UNVERIFIED", owned.release_after_exit)
        self.assert_error("BUSY", self.acquire)

    def test_postrelease_fsync_failure_is_reported_and_never_removes_a_later_lease(self):
        owned = self.acquire()
        with patch.object(leases.os, "fsync", side_effect=OSError("synthetic release durability failure")):
            error = self.assert_error("RELEASE_UNVERIFIED", owned.release_after_exit)
        self.assertTrue(error.uncertain)
        self.assertFalse(self.path.exists())
        later = self.acquire()
        before = self.path.read_bytes()
        self.assert_error("RELEASE_UNVERIFIED", owned.release_after_exit)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(later.release_failed_to_start())

    def test_publish_without_overwrite_retains_a_racing_unknown_registration(self):
        stage = leases._stage
        def appeared(parent, raw):
            result = stage(parent, raw)
            self.path.write_text("unknown concurrent registration")
            self.path.chmod(0o600)
            return result
        with patch.object(leases, "_stage", side_effect=appeared):
            self.assert_error("IO_FAILED", self.acquire)
        self.assertEqual(self.path.read_text(), "unknown concurrent registration")
        self.assertEqual(list(self.base.glob(".process-lease-*.tmp")), [])

    def test_unknown_stage_replacement_is_preserved_without_publishing(self):
        owned = self.acquire()
        before = self.path.read_bytes()
        stage = leases._stage
        replaced = []
        def substitute(parent, raw):
            result = stage(parent, raw)
            temporary = self.base / "unknown-stage-fixture"
            temporary.write_bytes(raw)
            temporary.chmod(0o600)
            temporary.replace(self.base / result[0])
            replaced.append(self.base / result[0])
            return result
        with patch.object(leases, "_stage", side_effect=substitute):
            self.assert_error("CHANGED", lambda: owned.mark_started(31234))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(replaced[0].exists())

    def test_wrong_uid_and_linked_profile_files_are_rejected_without_repair(self):
        current_uid = os.getuid()
        with patch.object(leases.os, "getuid", return_value=current_uid + 1):
            self.assert_error("UNSAFE", self.acquire)
        self.assertFalse(self.path.exists())
        for filename in (self.base / "profile.json", Path(self.home) / "config.toml"):
            alias = self.data / (filename.name + ".linked-fixture")
            os.link(filename, alias)
            self.assert_error("UNSAFE", self.acquire)
            self.assertEqual(filename.stat().st_nlink, 2)
            alias.unlink()
        self.assertFalse(self.path.exists())

    def test_invalid_program_and_unbounded_path_never_create_a_lease(self):
        for program in (str(self.program) + ".missing", "relative", "", None):
            with self.subTest(program_type=type(program).__name__):
                self.assert_error("IO_FAILED", lambda: leases.acquire(self.home, self.cwd, program))
                self.assertFalse(self.path.exists())
        self.assert_error("PROFILE_INVALID", lambda: leases.acquire("/" + "a" * 4096, self.cwd, str(self.program)))
        self.assertFalse(self.path.exists())

    def inspect(self):
        return leases.inspect_user_registration(str(self.data))

    def running(self):
        owned = self.acquire()
        owned.mark_started(31234)
        return owned

    @staticmethod
    def missing_pid():
        return ProcessLookupError(errno.ESRCH, "synthetic absent PID")

    def test_inspection_absent_is_readonly_without_creating_profile_or_lock(self):
        before = set(self.base.iterdir())
        with patch.object(leases.os, "kill", side_effect=AssertionError("no registration to probe")):
            snapshot = self.inspect()
            self.assertEqual(snapshot.status, "absent")
            self.assertIsNone(snapshot.child_pid)
            self.assertFalse(snapshot.can_clear)
            self.assert_error("STATE_INVALID", snapshot.clear_exited)
        self.assertEqual(set(self.base.iterdir()), before)
        self.assertFalse(self.lock.exists())
        retained = self.base.with_name("retained-profile-fixture")
        self.base.rename(retained)
        before = set(self.data.rglob("*"))
        self.assertEqual(self.inspect().status, "absent")
        self.assertEqual(set(self.data.rglob("*")), before)
        retained.parent.rename(self.data / "retained-phoenix-fixture")
        before = set(self.data.iterdir())
        self.assertEqual(self.inspect().status, "absent")
        self.assertEqual(set(self.data.iterdir()), before)

    def test_inspection_validates_existing_root_and_profile_instead_of_calling_malformed_absent(self):
        with self.assertRaises(leases.PftChatProcessLeaseError):
            leases.inspect_user_registration(str(self.data / "missing-root"))
        home = Path(self.home)
        home.rename(home.with_name("retained-home-fixture"))
        with self.assertRaises(leases.PftChatProcessLeaseError):
            self.inspect()
        self.assertFalse(self.path.exists())
        home.with_name("retained-home-fixture").rename(home)
        (self.base / "profile.json").write_text("unknown marker")
        self.assert_error("INVALID", self.inspect)
        self.assertFalse(self.lock.exists())

    def test_inspection_with_registration_requires_existing_stable_lock(self):
        self.running()
        before = self.path.read_bytes()
        self.lock.unlink()  # Synthetic corruption, never production recovery.
        with patch.object(leases.os, "kill", side_effect=AssertionError("missing lock cannot authorize a probe")):
            self.assert_error("IO_FAILED", self.inspect)
        self.assertFalse(self.lock.exists())
        self.assertEqual(self.path.read_bytes(), before)

    def test_launching_inspection_is_unknown_and_cannot_clear_or_probe_any_pid(self):
        self.acquire()
        before = self.path.read_bytes()
        with patch.object(leases.os, "kill", side_effect=AssertionError("launching has no child PID")):
            snapshot = self.inspect()
            self.assertEqual(snapshot.status, "unknown")
            self.assertIsNone(snapshot.child_pid)
            self.assertFalse(snapshot.can_clear)
            self.assert_error("STATE_INVALID", snapshot.clear_exited)
        self.assertEqual(self.path.read_bytes(), before)

    def test_running_inspection_reports_only_pid_presence_permission_unknown_or_esrch(self):
        self.running()
        before = self.path.read_bytes()
        for outcome, expected in ((None, "present"), (PermissionError(errno.EPERM, "synthetic denied"), "unknown"),
                                  (OSError(errno.EIO, "synthetic uncertain"), "unknown"), (self.missing_pid(), "exited")):
            with self.subTest(status=expected), patch.object(leases.os, "kill", side_effect=outcome) as probe:
                snapshot = leases.inspect_user_registration(str(self.data) + "/")
                self.assertEqual(snapshot.status, expected)
                self.assertEqual(snapshot.child_pid, 31234)
                self.assertEqual(snapshot.can_clear, expected == "exited")
                probe.assert_called_once_with(31234, 0)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertFalse(hasattr(snapshot, "nonce"))
                self.assertFalse(hasattr(snapshot, "home"))
                with self.assertRaises(AttributeError):
                    snapshot.status = "exited"

    def test_exited_clear_reprobes_under_same_kernel_lock_and_keeps_stable_inode(self):
        import fcntl
        self.running()
        lock_inode = self.lock.stat().st_ino
        with patch.object(leases.os, "kill", side_effect=self.missing_pid()) as probe:
            snapshot = self.inspect()
        probe.assert_called_once_with(31234, 0)
        def under_lock(pid, signal):
            self.assertEqual((pid, signal), (31234, 0))
            with self.lock.open("r+b") as other:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            raise self.missing_pid()
        with patch.object(leases.os, "kill", side_effect=under_lock) as probe:
            self.assertTrue(snapshot.clear_exited())
        probe.assert_called_once_with(31234, 0)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.lock.stat().st_ino, lock_inode)
        self.assertEqual(snapshot.status, "absent")
        self.assertIsNone(snapshot.child_pid)
        self.assertFalse(snapshot.can_clear)
        later = self.acquire()
        before = self.path.read_bytes()
        with patch.object(leases.os, "kill", side_effect=AssertionError("idempotent clear must not probe later records")):
            self.assertTrue(snapshot.clear_exited())
        self.assertEqual(self.path.read_bytes(), before)
        later.release_failed_to_start()

    def test_exited_clear_refuses_reused_or_uncertain_pid_and_preserves_registration(self):
        self.running()
        before = self.path.read_bytes()
        for outcome, code, expected in ((None, "BUSY", "present"), (PermissionError(errno.EPERM, "denied"), "STATE_INVALID", "unknown")):
            with patch.object(leases.os, "kill", side_effect=self.missing_pid()):
                snapshot = self.inspect()
            with patch.object(leases.os, "kill", side_effect=outcome) as probe:
                self.assert_error(code, snapshot.clear_exited)
            probe.assert_called_once_with(31234, 0)
            self.assertEqual(snapshot.status, expected)
            self.assertFalse(snapshot.can_clear)
            self.assertEqual(self.path.read_bytes(), before)

    def test_clear_refuses_changed_nonce_inode_profile_and_lock_before_pid_probe(self):
        self.running()
        with patch.object(leases.os, "kill", side_effect=self.missing_pid()):
            nonce_snapshot = self.inspect()
        self.write_record({**self.record(), "nonce": "f" * 64})
        before = self.path.read_bytes()
        with patch.object(leases.os, "kill", side_effect=AssertionError("new nonce must not be probed or cleared")):
            self.assert_error("CHANGED", nonce_snapshot.clear_exited)
        self.assertEqual(self.path.read_bytes(), before)
        with patch.object(leases.os, "kill", side_effect=self.missing_pid()):
            inode_snapshot = self.inspect()
        replacement = self.base / "replacement-fixture"
        replacement.write_bytes(before)
        replacement.chmod(0o600)
        replacement.replace(self.path)
        with patch.object(leases.os, "kill", side_effect=AssertionError("new inode must not be probed or cleared")):
            self.assert_error("CHANGED", inode_snapshot.clear_exited)
        with patch.object(leases.os, "kill", side_effect=self.missing_pid()):
            lock_snapshot = self.inspect()
        replacement = self.base / "replacement-lock-fixture"
        replacement.write_bytes(b"")
        replacement.chmod(0o600)
        replacement.replace(self.lock)
        with patch.object(leases.os, "kill", side_effect=AssertionError("new lock must not be probed or cleared")):
            self.assert_error("CHANGED", lock_snapshot.clear_exited)
        with patch.object(leases.os, "kill", side_effect=self.missing_pid()):
            profile_snapshot = self.inspect()
        Path(self.cwd).rename(self.base / "retained-workspace-fixture")
        Path(self.cwd).mkdir(mode=0o700)
        with patch.object(leases.os, "kill", side_effect=AssertionError("new profile identity must not be probed or cleared")):
            self.assert_error("CHANGED", profile_snapshot.clear_exited)
        self.assertEqual(self.path.read_bytes(), before)

    def test_registration_changed_during_pid_probe_is_preserved_by_inspection_and_clear(self):
        self.running()
        def changed(pid, signal):
            self.assertEqual((pid, signal), (31234, 0))
            self.write_record({**self.record(), "nonce": "e" * 64})
            raise self.missing_pid()
        with patch.object(leases.os, "kill", side_effect=changed):
            self.assert_error("CHANGED", self.inspect)
        with patch.object(leases.os, "kill", side_effect=self.missing_pid()):
            snapshot = self.inspect()
        def changed_again(pid, signal):
            self.write_record({**self.record(), "nonce": "d" * 64})
            raise self.missing_pid()
        with patch.object(leases.os, "kill", side_effect=changed_again):
            self.assert_error("CHANGED", snapshot.clear_exited)
        self.assertTrue(self.path.exists())
        self.assertEqual(self.record()["nonce"], "d" * 64)
        self.assertFalse(snapshot.can_clear)

    def test_inspection_and_clear_lock_contention_never_create_or_remove_registration(self):
        import fcntl
        self.running()
        before = self.path.read_bytes()
        with patch.object(leases.os, "kill", side_effect=self.missing_pid()):
            snapshot = self.inspect()
        with self.lock.open("r+b") as other:
            fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(leases.os, "kill", side_effect=AssertionError("lock contention must stop before probe")):
                self.assert_error("BUSY", self.inspect)
                self.assert_error("BUSY", snapshot.clear_exited)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(snapshot.can_clear)

    def test_malformed_registration_inspection_never_probes_or_reports_absent(self):
        self.acquire()
        for raw in (b"{}", b"not-json", b"x" * (leases.MAX_LEASE_BYTES + 1)):
            self.path.write_bytes(raw)
            with patch.object(leases.os, "kill", side_effect=AssertionError("invalid record cannot authorize probe")):
                self.assert_error("INVALID", self.inspect)
            self.assertEqual(self.path.read_bytes(), raw)

    def test_explicit_clear_postdelete_failure_reports_uncertain_without_claiming_record_remains(self):
        self.running()
        with patch.object(leases.os, "kill", side_effect=self.missing_pid()):
            snapshot = self.inspect()
        with patch.object(leases.os, "kill", side_effect=self.missing_pid()), \
             patch.object(leases.os, "fsync", side_effect=OSError("synthetic directory failure")):
            error = self.assert_error("RELEASE_UNVERIFIED", snapshot.clear_exited)
        self.assertTrue(error.uncertain)
        self.assertFalse(self.path.exists())
        self.assertEqual(snapshot.status, "unknown")
        self.assertFalse(snapshot.can_clear)
        later = self.acquire()
        before = self.path.read_bytes()
        self.assert_error("RELEASE_UNVERIFIED", snapshot.clear_exited)
        self.assertEqual(self.path.read_bytes(), before)
        later.release_failed_to_start()

    def test_inspection_does_not_read_auth_history_or_create_files_and_windows_is_unsupported(self):
        self.running()
        for name in ("auth.json", "history.jsonl", "sessions"):
            (Path(self.home) / name).write_text("synthetic sentinel")
        before = set(self.base.rglob("*"))
        opener = os.open
        def known_only(path, *args, **kwargs):
            if Path(path).name in {"auth.json", "history.jsonl", "sessions"}:
                raise AssertionError("inspection must not read private account/history artifacts")
            self.assertFalse(len(args) > 0 and args[0] & os.O_CREAT)
            return opener(path, *args, **kwargs)
        with patch.object(leases.os, "open", side_effect=known_only), \
             patch.object(leases.os, "kill", side_effect=self.missing_pid()):
            self.assertEqual(self.inspect().status, "exited")
        self.assertEqual(set(self.base.rglob("*")), before)
        with patch.object(leases.sys, "platform", "win32"), \
             patch.object(leases.os, "open", side_effect=AssertionError("unsupported inspection must not read paths")):
            self.assert_error("UNSUPPORTED", self.inspect)

    def test_windows_is_explicitly_unsupported_before_any_filesystem_read(self):
        with patch.object(leases.sys, "platform", "win32"), \
             patch.object(leases.os, "open", side_effect=AssertionError("must not inspect Windows ACLs with POSIX modes")):
            self.assert_error("UNSUPPORTED", self.acquire)


if __name__ == "__main__":
    unittest.main()
