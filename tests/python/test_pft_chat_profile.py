# SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

ADDON = Path(__file__).resolve().parents[2] / 'addon' / 'PhoenixFreeCADTools'
_saved_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    from pft_chat_profile import prepare_chat_profile, validate_executable, PftChatProfileError, PROFILE_CONFIG
finally:
    sys.path[:] = _saved_path


class ChatProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)

    def test_private_dedicated_profile_preserves_existing_codex_and_chat_data(self):
        unrelated = self.data / 'existing-chat-data'
        unrelated.write_text('keep')
        profile = prepare_chat_profile(str(self.data))
        home = Path(profile['home'])
        auth = home / 'auth.json'
        auth.write_text('synthetic-owned-server-data')
        self.assertEqual(prepare_chat_profile(str(self.data)), profile)
        self.assertEqual(auth.read_text(), 'synthetic-owned-server-data')
        self.assertEqual(unrelated.read_text(), 'keep')
        self.assertEqual((home / 'config.toml').read_text(), PROFILE_CONFIG)
        self.assertEqual(list(Path(profile['cwd']).iterdir()), [])
        for folder in (home.parent, home, Path(profile['cwd'])):
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        self.assertIn('cli_auth_credentials_store = "file"', PROFILE_CONFIG)
        self.assertIn('forced_login_method = "chatgpt"', PROFILE_CONFIG)

    def test_unknown_existing_directory_and_changed_configuration_are_preserved(self):
        parent = self.data / 'PhoenixTools'
        parent.mkdir()
        base = parent / 'CodexChat'
        base.mkdir(mode=0o700)
        marker = base / 'keep.txt'
        marker.write_text('unknown')
        with self.assertRaises(PftChatProfileError): prepare_chat_profile(str(self.data))
        self.assertEqual(marker.read_text(), 'unknown')
        marker.unlink(); base.rmdir()
        profile = prepare_chat_profile(str(self.data))
        config = Path(profile['home']) / 'config.toml'
        config.write_text('user-edited')
        with self.assertRaises(PftChatProfileError): prepare_chat_profile(str(self.data))
        self.assertEqual(config.read_text(), 'user-edited')

    def test_symlink_or_public_private_directory_is_rejected_without_following(self):
        target = self.data / 'elsewhere'; target.mkdir()
        parent = self.data / 'PhoenixTools'; parent.symlink_to(target, target_is_directory=True)
        with self.assertRaises(PftChatProfileError): prepare_chat_profile(str(self.data))
        self.assertEqual(list(target.iterdir()), [])
        parent.unlink()
        profile = prepare_chat_profile(str(self.data))
        Path(profile['home']).chmod(0o755)
        with self.assertRaises(PftChatProfileError): prepare_chat_profile(str(self.data))

    def test_workspace_instructions_are_preserved_and_reuse_is_rejected(self):
        profile = prepare_chat_profile(str(self.data))
        instructions = Path(profile["cwd"]) / "AGENTS.md"
        instructions.write_text("synthetic custom instructions")
        with self.assertRaises(PftChatProfileError): prepare_chat_profile(str(self.data))
        self.assertEqual(instructions.read_text(), "synthetic custom instructions")

    def test_executable_validation_rejects_relative_hidden_or_nonexecutable_paths(self):
        for candidate in ('node', '', '/example/\u202ecodex', ' /example/codex', str(self.data)):
            with self.subTest(candidate=candidate), self.assertRaises(PftChatProfileError): validate_executable(candidate)
        executable = self.data / 'codex'; executable.write_text('fixture')
        with self.assertRaises(PftChatProfileError): validate_executable(str(executable))
        executable.chmod(0o700)
        self.assertEqual(validate_executable(str(executable)), str(executable.resolve()))


if __name__ == '__main__': unittest.main()
