# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
SPEC = importlib.util.spec_from_file_location("pft_api_credentials_test", ADDON / "pft_api_credentials.py")
credentials_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(credentials_module)
PftApiCredentialStore = credentials_module.PftApiCredentialStore


class ApiCredentialTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.data = Path(scratch.name)
        self.endpoint = "https://api.example.invalid/v1"
        self.store = PftApiCredentialStore(self.data, "fixture", self.endpoint)
        self.key = "synthetic-test-key"

    def test_remember_reuse_and_clear_only_the_matching_scope(self):
        self.assertIsNone(self.store.load())
        self.assertFalse(self.store.directory.exists())
        other = PftApiCredentialStore(self.data, "fixture", "https://api.example.invalid/v2")
        service = PftApiCredentialStore(self.data, "other", self.endpoint)
        self.store.save(self.key)
        other.save("second-synthetic-key")
        self.assertIsNone(service.load())
        same = PftApiCredentialStore(self.data, "fixture", "https://API.example.invalid:443/v1/")
        self.assertEqual(same.load(), self.key)
        same.save("new-synthetic-key")
        self.assertEqual(self.store.load(), "new-synthetic-key")
        same.clear()
        same.clear()
        self.assertIsNone(self.store.load())
        self.assertEqual(other.load(), "second-synthetic-key")

    def test_reject_unsafe_scope_or_key(self):
        for endpoint in ("http://api.example.invalid", "https://u@api.example.invalid",
                         "https://api.example.invalid?x=1", "https://api.example.invalid/v1/../v2",
                         "https://api.example.invalid:70000"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                PftApiCredentialStore(self.data, "fixture", endpoint)
        with self.assertRaises(ValueError):
            self.store.save("header\r\ninjection")
        self.assertFalse(self.store.directory.exists())

    @unittest.skipIf(os.name == "nt", "POSIX permission and symlink semantics")
    def test_permissions_and_symlinks(self):
        self.store.save(self.key)
        self.assertEqual(stat.S_IMODE(self.store.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.store.file.stat().st_mode), 0o600)
        self.store.file.chmod(0o644)
        with self.assertRaises(OSError):
            self.store.load()
        with self.assertRaises(OSError):
            self.store.save("replacement-key")
        self.store.file.unlink()
        original = self.data / "fixture.txt"
        original.write_bytes(b"preserve")
        self.store.file.symlink_to(original)
        with self.assertRaises(OSError):
            self.store.load()
        with self.assertRaises(OSError):
            self.store.clear()
        self.assertEqual(original.read_bytes(), b"preserve")

    @unittest.skipUnless(os.name == "nt", "requires native Windows DPAPI")
    def test_windows_uses_protected_storage(self):
        self.store.save(self.key)
        self.assertNotIn(self.key.encode("ascii"), self.store.file.read_bytes())
        self.assertEqual(self.store.load(), self.key)
