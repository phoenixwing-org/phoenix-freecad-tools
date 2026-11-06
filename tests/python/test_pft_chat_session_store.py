# SPDX-License-Identifier: Apache-2.0
"""Bounded metadata registry with synthetic private profiles; no child processes."""
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
    import pft_chat_session_store as sessions
    import pft_chat_process_lease as lease
    from pft_chat_profile import prepare_chat_profile
finally:
    sys.path[:] = _saved_path


@unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "POSIX native storage")
class ChatSessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pft-chat-session-")
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name).resolve()
        self.profile = prepare_chat_profile(str(self.data))
        self.home, self.cwd = self.profile["home"], self.profile["cwd"]
        self.base = Path(self.home).parent
        self.path, self.lock = self.base / sessions.REGISTRY_FILENAME, self.base / lease.LOCK_FILENAME
        self.program = self.data / "unexecuted-program-fixture"
        self.program.write_text("synthetic nonexecuted program")
        self.program.chmod(0o700)
        owned = lease.acquire(self.home, self.cwd, str(self.program))
        owned.release_failed_to_start()  # Create the cooperating protocol's stable lock.

    def store(self):
        return sessions.PftChatSessionStore(self.home, self.cwd)

    def register(self, store=None, thread_id="thread-1", created_at=1700000000, model="fixture-model", fingerprint="a" * 64):
        store = self.store() if store is None else store
        return store.register_thread(thread_id, created_at, model, fingerprint)

    def record(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write_record(self, record):
        raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.path.write_bytes(raw)
        self.path.chmod(0o600)
        return raw

    def assert_error(self, suffix, call):
        with self.assertRaises(sessions.PftChatSessionStoreError) as raised:
            call()
        self.assertEqual(raised.exception.code, "CHAT_SESSION_STORE_" + suffix)
        self.assertNotIn(str(self.data), str(raised.exception))
        return raised.exception

    def test_constructor_only_validates_profile_and_returns_canonical_readonly_bindings(self):
        self.path.write_text("unrecognized existing registry")
        self.path.chmod(0o600)
        before = {path: (path.stat().st_ino, path.stat().st_mtime_ns) for path in self.base.rglob("*")}
        store = self.store()
        self.assertEqual((store.home, store.cwd), (self.home, self.cwd))
        with self.assertRaises(AttributeError):
            store.home = "other"
        self.assertEqual({path: (path.stat().st_ino, path.stat().st_mtime_ns) for path in self.base.rglob("*")}, before)
        self.assert_error("INVALID", store.list_sessions)
        self.assertEqual(self.path.read_text(), "unrecognized existing registry")

    def test_missing_registry_lists_empty_without_creating_files_and_missing_thread_is_rejected(self):
        self.lock.unlink()  # Valid unused v1 profile, no process lock or registry yet.
        before = set(self.base.rglob("*"))
        store = self.store()
        self.assertEqual(store.list_sessions(), [])
        self.assert_error("NOT_FOUND", lambda: store.get_session("not-registered"))
        self.assertEqual(sessions.list_user_sessions(str(self.data)), [])
        self.assertEqual(set(self.base.rglob("*")), before)
        self.assert_error("IO_FAILED", lambda: self.register(store))
        self.assertFalse(self.lock.exists())
        self.assertFalse(self.path.exists())

    def test_register_freezes_only_allowed_metadata_in_private_profile_bound_registry(self):
        store = self.store()
        self.assertEqual(store.list_sessions(), [])
        result = self.register(store)
        expected = {"thread_id": "thread-1", "created_at": 1700000000, "model": "fixture-model", "scope_fingerprint": "a" * 64,
                    "history_mode": "paginated", "cli_version": "0.160.0", "provider": "openai"}
        self.assertEqual(result, expected)
        self.assertEqual(store.list_sessions(), [expected])
        self.assertEqual(store.get_session("thread-1"), expected)
        record = self.record()
        self.assertEqual(record["sessions"], [expected])
        self.assertEqual(len(record["profile_id"]), 64)
        for name, directory in (("base", self.base), ("home", Path(self.home)), ("workspace", Path(self.cwd))):
            info = directory.stat()
            self.assertEqual(record["profile_identity"][name], [info.st_dev, info.st_ino])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.path.stat().st_nlink, 1)
        self.assertNotIn(str(self.data), self.path.read_text())
        self.assertFalse({"title", "text", "preview", "account", "home", "cwd"}.intersection(result))
        result["model"] = "caller mutation"
        listed = store.list_sessions()
        listed[0]["thread_id"] = "caller mutation"
        self.assertEqual(store.get_session("thread-1"), expected)
        self.assertEqual(list(self.base.glob(".sessions-*.tmp")), [])

    def test_same_thread_registration_is_idempotent_only_for_identical_metadata(self):
        store = self.store()
        expected = self.register(store)
        before = self.path.read_bytes(), self.path.stat().st_ino, self.path.stat().st_mtime_ns
        with patch.object(sessions.os, "replace", side_effect=AssertionError("must not rewrite identical metadata")), \
             patch.object(sessions.os, "write", side_effect=AssertionError("must not write identical metadata")), \
             patch.object(sessions.os, "fsync", side_effect=AssertionError("must not fsync identical metadata")):
            self.assertEqual(self.register(store), expected)
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_ino, self.path.stat().st_mtime_ns), before)
        self.assert_error("CHANGED", lambda: self.register(store, model="different-model"))
        self.assert_error("CHANGED", lambda: self.register(store, created_at=1700000001))
        self.assert_error("CHANGED", lambda: self.register(store, fingerprint="b" * 64))
        self.assertEqual(self.path.read_bytes(), before[0])

    def test_observed_etag_rejects_external_updates_and_requires_new_store_to_read_them(self):
        first = self.store()
        self.register(first)
        second = self.store()
        self.assertEqual(len(second.list_sessions()), 1)
        self.register(first, thread_id="thread-2")
        before = self.path.read_bytes()
        self.assert_error("CHANGED", second.list_sessions)
        self.assert_error("CHANGED", lambda: self.register(second, thread_id="thread-3"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.store().list_sessions()), 2)

    def test_previously_missing_registry_cannot_silently_adopt_a_later_publication(self):
        stale = self.store()
        self.assertEqual(stale.list_sessions(), [])
        self.register()
        before = self.path.read_bytes()
        self.assert_error("CHANGED", lambda: self.register(stale, thread_id="thread-2"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_same_bytes_new_inode_or_new_profile_id_is_not_adopted_by_observer(self):
        store = self.store()
        self.register(store)
        replacement = self.base / "same-byte-fixture"
        replacement.write_bytes(self.path.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(self.path)
        self.assert_error("CHANGED", store.list_sessions)
        fresh = self.store()
        fresh.list_sessions()
        self.write_record({**self.record(), "profile_id": "f" * 64})
        self.assert_error("CHANGED", fresh.list_sessions)

    def test_copied_registry_cannot_claim_a_different_profile(self):
        self.register()
        original = self.path.read_bytes()
        other_data = self.data / "other-userdata"
        other_data.mkdir(mode=0o700)
        other = prepare_chat_profile(str(other_data))
        owned = lease.acquire(other["home"], other["cwd"], str(self.program))
        owned.release_failed_to_start()
        target = Path(other["home"]).parent / sessions.REGISTRY_FILENAME
        target.write_bytes(original)
        target.chmod(0o600)
        store = sessions.PftChatSessionStore(other["home"], other["cwd"])
        self.assert_error("PROFILE_MISMATCH", store.list_sessions)
        self.assert_error("PROFILE_MISMATCH", lambda: self.register(store, thread_id="thread-2"))
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(self.path.read_bytes(), original)

    def test_same_named_workspace_replacement_does_not_rebind_existing_store(self):
        store = self.store()
        self.register(store)
        before = self.path.read_bytes()
        Path(self.cwd).rename(self.base / "retained-workspace-fixture")
        Path(self.cwd).mkdir(mode=0o700)
        self.assert_error("PROFILE_MISMATCH", store.list_sessions)
        self.assert_error("CHANGED", lambda: self.register(store, thread_id="thread-2"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_registry_schema_is_bounded_strict_json_and_never_repairs_unknown_data(self):
        self.register()
        valid = self.record()
        original_session = valid["sessions"][0]
        cases = [b"", b"not-json", b"[]", b"\xff", b"x" * (sessions.MAX_REGISTRY_BYTES + 1),
                 json.dumps({**valid, "schema_version": True}).encode(),
                 json.dumps({**valid, "schema_version": 2}).encode(),
                 json.dumps({**valid, "unknown": "value"}).encode(),
                 json.dumps({**valid, "sessions": [original_session, original_session]}).encode(),
                 json.dumps({**valid, "sessions": [{**original_session, "created_at": True}]}).encode(),
                 json.dumps({**valid, "sessions": [{**original_session, "created_at": 1.0}]}).encode(),
                 json.dumps({**valid, "sessions": [{**original_session, "provider": "other"}]}).encode(),
                 json.dumps({**valid, "sessions": [{**original_session, "cli_version": "0.161.0"}]}).encode(),
                 json.dumps({**valid, "sessions": [{**original_session, "history_mode": "extended"}]}).encode(),
                 json.dumps({**valid, "sessions": [{**original_session, "title": "must never be stored"}]}).encode(),
                 (json.dumps(valid)[:-1] + ',"owner":"phoenix-freecad-tools"}').encode(),
                 json.dumps(valid).replace('"created_at": 1700000000', '"created_at": NaN').encode()]
        for raw in cases:
            with self.subTest(length=len(raw)):
                self.path.write_bytes(raw)
                self.assert_error("INVALID", self.store().list_sessions)
                self.assertEqual(self.path.read_bytes(), raw)

    def test_metadata_rejects_unbounded_ids_models_bad_hashes_timestamps_and_modes_before_write(self):
        store = self.store()
        arguments = ["thread-1", 1700000000, "fixture-model", "a" * 64, "paginated"]
        bad = [(0, ""), (0, "x" * 257), (0, "示" * 86), (0, "thread\nline"), (0, " thread"),
               (1, True), (1, -1), (1, 1.5), (1, 1700000000000), (1, 2 ** 63),
               (2, ""), (2, "x" * 257), (2, "hidden\u202e-model"),
               (3, "A" * 64), (3, "g" * 64), (3, "a" * 63), (3, None), (4, "extended"), (4, None)]
        for index, value in bad:
            with self.subTest(index=index, value_type=type(value).__name__):
                current = list(arguments)
                current[index] = value
                self.assert_error("INVALID", lambda: store.register_thread(*current))
                self.assertFalse(self.path.exists())
        self.assertEqual(store.register_thread("first", 0, "x" * 256, "a" * 64)["created_at"], 0)
        self.assertEqual(store.register_thread("last", sessions.MAX_CREATED_AT, "fixture-model", "b" * 64)["created_at"], sessions.MAX_CREATED_AT)

    def test_real_count_limit_is_100_without_eviction_or_partial_write(self):
        self.register()
        record = self.record()
        template = record["sessions"][0]
        record["sessions"] = [{**template, "thread_id": "thread-%03d" % index} for index in range(99)]
        self.write_record(record)
        store = self.store()
        self.register(store, thread_id="thread-099")
        self.assertEqual(len(store.list_sessions()), 100)
        before = self.path.read_bytes()
        self.assert_error("LIMIT", lambda: self.register(store, thread_id="thread-100"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_real_64kib_limit_rejects_whole_next_registration_without_eviction(self):
        self.register()
        record = self.record()
        template = record["sessions"][0]
        record["sessions"] = []
        next_id = None
        for index in range(100):
            candidate = {**template, "thread_id": "thread-%03d-" % index + "t" * 240, "model": "m" * 256}
            expanded = {**record, "sessions": [*record["sessions"], candidate]}
            raw = (json.dumps(expanded, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
            if len(raw) > sessions.MAX_REGISTRY_BYTES:
                next_id = candidate["thread_id"]
                break
            record = expanded
        self.assertIsNotNone(next_id)
        before = self.write_record(record)
        self.assertLessEqual(len(before), 64 * 1024)
        self.assertLess(len(record["sessions"]), 100)
        self.assert_error("LIMIT", lambda: self.register(self.store(), thread_id=next_id, model="m" * 256))
        self.assertEqual(self.path.read_bytes(), before)

    def test_nonprivate_symlink_hardlink_fifo_and_missing_lock_are_preserved(self):
        self.register()
        before = self.path.read_bytes()
        self.path.chmod(0o644)
        self.assert_error("UNSAFE", self.store().list_sessions)
        self.path.chmod(0o600)
        alias = self.data / "registry-alias"
        os.link(self.path, alias)
        self.assert_error("UNSAFE", self.store().list_sessions)
        alias.unlink()
        self.path.unlink()
        self.path.symlink_to(self.data / "unrelated")
        self.assert_error("UNSAFE", self.store().list_sessions)
        self.path.unlink()
        os.mkfifo(self.path, 0o600)
        self.assert_error("UNSAFE", self.store().list_sessions)
        self.path.unlink()
        self.path.write_bytes(before)
        self.path.chmod(0o600)
        self.lock.unlink()
        self.assert_error("IO_FAILED", self.store().list_sessions)
        self.assertFalse(self.lock.exists())
        self.assertEqual(self.path.read_bytes(), before)

    def test_registration_requires_short_kernel_lock_and_keeps_stable_inode(self):
        import fcntl
        store = self.store()
        lock_inode = self.lock.stat().st_ino
        with self.lock.open("r+b") as other:
            fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_error("BUSY", lambda: self.register(store))
        self.assertFalse(self.path.exists())
        self.register(store)
        self.register(store, thread_id="thread-2")
        self.assertEqual(self.lock.stat().st_ino, lock_inode)
        with self.lock.open("r+b") as other:
            fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(other.fileno(), fcntl.LOCK_UN)

    def test_atomic_update_write_failure_preserves_old_data_and_cleans_owned_stage(self):
        store = self.store()
        self.register(store)
        before = self.path.read_bytes()
        with patch.object(sessions.os, "write", side_effect=OSError("synthetic write failure")):
            self.assert_error("IO_FAILED", lambda: self.register(store, thread_id="thread-2"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.base.glob(".sessions-*.tmp")), [])

    def test_postpublication_failure_is_uncertain_and_same_store_never_retries(self):
        store = self.store()
        self.register(store)
        fsync = os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("synthetic post-publication durability failure")
            return fsync(fd)
        with patch.object(sessions.os, "fsync", side_effect=fail_directory):
            error = self.assert_error("SAVE_UNVERIFIED", lambda: self.register(store, thread_id="thread-2"))
        self.assertTrue(error.uncertain)
        self.assertEqual(len(self.record()["sessions"]), 2)
        self.assert_error("SAVE_UNVERIFIED", store.list_sessions)
        self.assert_error("SAVE_UNVERIFIED", lambda: self.register(store, thread_id="thread-2"))
        self.assertEqual(len(self.store().list_sessions()), 2)

    def test_first_publication_never_overwrites_a_racing_unknown_file(self):
        store = self.store()
        link = os.link
        def appeared(source, target, **kwargs):
            self.path.write_text("unknown concurrent file")
            self.path.chmod(0o600)
            return link(source, target, **kwargs)
        with patch.object(sessions.os, "link", side_effect=appeared):
            self.assert_error("IO_FAILED", lambda: self.register(store))
        self.assertEqual(self.path.read_text(), "unknown concurrent file")
        self.assertEqual(list(self.base.glob(".sessions-*.tmp")), [])

    def test_list_user_sessions_is_readonly_and_only_reads_our_metadata(self):
        expected = self.register()
        for filename in ("auth.json", "history.jsonl", "sessions"):
            (Path(self.home) / filename).write_text("synthetic unread private artifact")
        before = set(self.base.rglob("*"))
        opener = os.open
        def allowed(path, *args, **kwargs):
            if Path(path).name in {"auth.json", "history.jsonl", "sessions"}:
                raise AssertionError("must not read authentication or official chat history")
            self.assertFalse(len(args) > 0 and args[0] & os.O_CREAT)
            return opener(path, *args, **kwargs)
        with patch.object(sessions.os, "open", side_effect=allowed):
            self.assertEqual(sessions.list_user_sessions(str(self.data) + "/"), [expected])
        self.assertEqual(set(self.base.rglob("*")), before)
        retained = self.base.with_name("retained-profile-fixture")
        self.base.rename(retained)
        before = set(self.data.rglob("*"))
        self.assertEqual(sessions.list_user_sessions(str(self.data)), [])
        self.assertEqual(set(self.data.rglob("*")), before)
        self.base.mkdir(mode=0o700)
        self.assert_error("IO_FAILED", lambda: sessions.list_user_sessions(str(self.data)))

    def test_privacy_gate_rejects_code_roots_without_reading_or_writing_profile(self):
        with patch.object(sessions, "_EXCLUDED_STORAGE_ROOTS", (self.data,)), \
             patch.object(lease, "_profile", side_effect=AssertionError("privacy gate must precede profile access")):
            self.assert_error("PROFILE_INVALID", self.store)
        self.assertFalse(self.path.exists())

    def test_source_layout_exclusion_does_not_treat_installed_mod_userdata_as_a_repo(self):
        source = self.data / "source-fixture"
        module = source / "addon" / "PhoenixFreeCADTools" / "pft_chat_session_store.py"
        module.parent.mkdir(parents=True)
        module.write_text("synthetic module marker")
        (source / "package.json").write_text('{}')
        self.assertEqual(sessions._excluded_storage_roots(module), (module.parent, source))
        installed = self.data / "FreeCAD" / "v1-1" / "Mod" / "PhoenixFreeCADTools" / "pft_chat_session_store.py"
        installed.parent.mkdir(parents=True)
        installed.write_text("synthetic installed module marker")
        self.assertEqual(sessions._excluded_storage_roots(installed), (installed.parent,))
        installed_data = installed.parents[2]
        with patch.object(sessions, "_EXCLUDED_STORAGE_ROOTS", sessions._excluded_storage_roots(installed)):
            sessions._storage_gate(str(installed_data / "PhoenixTools" / "CodexChat" / "home"),
                                   str(installed_data / "PhoenixTools" / "CodexChat" / "workspace"))
        link = self.data / "development-module-link.py"
        link.symlink_to(module)
        self.assertEqual(sessions._excluded_storage_roots(link), (module.parent, source))

    def test_privacy_gate_matches_directory_identity_even_when_lexical_spelling_differs(self):
        # Inject the same source-root identity at a different lexical location;
        # no dependency on this test filesystem's case-sensitivity is required.
        excluded = self.data / "source-root-spelling-fixture"
        real_lstat = Path.lstat
        source_identity = self.data.lstat()
        def alias_metadata(path, *args, **kwargs):
            return source_identity if path == excluded else real_lstat(path, *args, **kwargs)
        with patch.object(sessions, "_EXCLUDED_STORAGE_ROOTS", (excluded,)), \
             patch.object(Path, "lstat", alias_metadata), \
             patch.object(lease, "_profile", side_effect=AssertionError("identity gate must precede profile reads")):
            self.assert_error("PROFILE_INVALID", self.store)
        self.assertFalse(self.path.exists())

    def test_windows_is_unsupported_before_profile_or_registry_access(self):
        with patch.object(sessions.sys, "platform", "win32"), \
             patch.object(sessions.os, "open", side_effect=AssertionError("unsupported platform must not open private paths")):
            self.assert_error("UNSUPPORTED", self.store)
            self.assert_error("UNSUPPORTED", lambda: sessions.list_user_sessions(str(self.data)))


if __name__ == "__main__":
    unittest.main()
