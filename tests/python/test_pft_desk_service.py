# SPDX-License-Identifier: Apache-2.0
"""Local Desk CLI configuration and result contract; never starts a service."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
spec = importlib.util.spec_from_file_location("pft_desk_service_tests", ADDON / "pft_desk_service.py")
desk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desk)


def ready(**changes):
    value = {"state": "ready", "code": "verified", "reason": "Synthetic compatible local service",
             "api_origin": "http://127.0.0.1:54321", "web_url": "http://127.0.0.1:54321/",
             "instance_id": "12345678-1234-4234-8234-123456789abc", "pid": 1234,
             "runtime_id": "sha256:" + "a" * 64, "created": False, "reused": True,
             "release_status": "not-published", "app_version": "0.5.0", "lock_present": True}
    value.update(changes)
    return value


def failure(state="absent", **changes):
    value = ready(state=state, code="no_registration", api_origin=None, web_url=None,
                  instance_id=None, pid=None, runtime_id=None, reused=False)
    value.update(changes)
    return value


class PftDeskServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pft-desk-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "Desk candidate"
        self.cli = self.repo / "scripts" / "desk-web-service.mjs"
        self.cli.parent.mkdir(parents=True)
        self.cli.write_text("// synthetic entry; never executed\n", encoding="utf-8")
        self.node = self.root / "node binary"
        self.node.write_text("synthetic executable; never executed\n", encoding="utf-8")
        self.node.chmod(0o700)
        self.state = self.root / "private namespace"
        self.config = {"node_executable": str(self.node), "cli_entry": str(self.cli), "state_dir": str(self.state)}

    def parse(self, value, exit_code=0, stderr=b""):
        return desk.parse_cli_result(exit_code, json.dumps(value).encode("utf-8"), stderr)

    def assert_error(self, code, callback):
        with self.assertRaises(desk.PftDeskError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.message, str(caught.exception))
        self.assertNotIn(str(self.root), str(caught.exception))

    def test_configuration_and_fixed_argv_do_not_create_an_absent_namespace(self):
        self.assertEqual(desk.validate_configuration(self.config), self.config)
        for action in ("status", "ensure"):
            self.assertEqual(desk.build_command(self.config, action),
                             (str(self.node), [str(self.cli), action, "--state-dir", str(self.state)]))
        self.assertFalse(self.state.exists())
        self.assertEqual(self.cli.read_text(), "// synthetic entry; never executed\n")

    def test_paths_are_canonical_and_shell_characters_remain_one_argument(self):
        folder = self.root / "not shell; $(command)"
        folder.mkdir()
        config = {**self.config, "state_dir": str(folder / ".." / folder.name / "new state")}
        canonical = desk.validate_configuration(config)
        self.assertEqual(canonical["state_dir"], str(folder / "new state"))
        program, argv = desk.build_command(config, "ensure")
        self.assertEqual(program, str(self.node))
        self.assertEqual(argv[-1], str(folder / "new state"))
        self.assertEqual(len(argv), 4)

    def test_existing_private_owned_namespace_is_not_modified(self):
        self.state.mkdir(mode=0o700)
        before = self.state.stat()
        self.assertEqual(desk.validate_configuration(self.config), self.config)
        after = self.state.stat()
        self.assertEqual((before.st_mode, before.st_mtime_ns, before.st_ctime_ns),
                         (after.st_mode, after.st_mtime_ns, after.st_ctime_ns))
        self.assertEqual(list(self.state.iterdir()), [])

    def test_configuration_rejects_missing_extra_relative_and_control_paths(self):
        for value in ({}, {**self.config, "args": ["--port", "1"]},
                      {**self.config, "state_dir": "relative"}, {**self.config, "state_dir": str(self.root) + "\n"},
                      {**self.config, "state_dir": str(self.root) + "\u202e"},
                      {**self.config, "node_executable": None}):
            self.assert_error("CONFIGURATION_INVALID", lambda value=value: desk.validate_configuration(value))
        self.assert_error("UNSUPPORTED_ACTION", lambda: desk.build_command(self.config, "stop"))

    def test_executable_and_cli_must_be_real_existing_files_with_exact_entry_name(self):
        self.node.chmod(0o600)
        self.assert_error("CONFIGURATION_INVALID", lambda: desk.validate_configuration(self.config))
        self.node.chmod(0o700)
        self.node.unlink()
        self.assert_error("PATH_UNAVAILABLE", lambda: desk.validate_configuration(self.config))
        self.node.mkdir()
        self.assert_error("CONFIGURATION_INVALID", lambda: desk.validate_configuration(self.config))
        self.node.rmdir()
        self.node.write_text("fixture")
        self.node.chmod(0o700)
        self.cli.rename(self.cli.with_name("other.mjs"))
        self.assert_error("PATH_UNAVAILABLE", lambda: desk.validate_configuration(self.config))
        self.config["cli_entry"] = str(self.cli.with_name("other.mjs"))
        self.assert_error("CONFIGURATION_INVALID", lambda: desk.validate_configuration(self.config))

    def test_symlinked_executable_cli_and_namespace_ancestors_are_refused(self):
        node_link = self.root / "linked-node"
        node_link.symlink_to(self.node)
        self.assert_error("UNSAFE_PATH", lambda: desk.validate_configuration({**self.config, "node_executable": str(node_link)}))
        repo_link = self.root / "linked-repo"
        repo_link.symlink_to(self.repo, target_is_directory=True)
        self.assert_error("UNSAFE_PATH", lambda: desk.validate_configuration({**self.config, "cli_entry": str(repo_link / "scripts" / self.cli.name)}))
        parent = self.root / "linked-namespace"
        parent.symlink_to(self.root, target_is_directory=True)
        self.assert_error("UNSAFE_PATH", lambda: desk.validate_configuration({**self.config, "state_dir": str(parent / "missing")}))
        self.state.symlink_to(self.root, target_is_directory=True)
        self.assert_error("UNSAFE_PATH", lambda: desk.validate_configuration(self.config))

    def test_namespace_refuses_root_home_source_and_source_parent_without_writes(self):
        with patch.object(desk.Path, "home", return_value=self.root / "home"):
            for value in (Path(self.root.anchor), self.root / "home", self.repo, self.repo / "data", self.root, ADDON, ADDON / "state"):
                self.assert_error("INVALID_STATE_DIRECTORY", lambda value=value: desk.validate_configuration({**self.config, "state_dir": str(value)}))
        self.assertFalse((self.repo / "data").exists())

    def test_namespace_refuses_non_directory_public_permissions_and_foreign_owner(self):
        self.state.write_text("keep")
        self.assert_error("INVALID_STATE_DIRECTORY", lambda: desk.validate_configuration(self.config))
        self.state.unlink()
        self.state.mkdir(mode=0o700)
        if os.name != "nt":
            self.state.chmod(0o750)
            self.assert_error("INVALID_STATE_DIRECTORY", lambda: desk.validate_configuration(self.config))
            self.state.chmod(0o700)
        if hasattr(desk.os, "getuid"):
            uid = os.getuid()
            with patch.object(desk.os, "getuid", return_value=uid + 1):
                self.assert_error("INVALID_STATE_DIRECTORY", lambda: desk.validate_configuration(self.config))

    def test_namespace_ancestor_file_is_refused_and_missing_deep_path_is_not_created(self):
        parent = self.root / "ordinary-file"
        parent.write_text("keep")
        self.assert_error("UNSAFE_PATH", lambda: desk.validate_configuration({**self.config, "state_dir": str(parent / "state")}))
        deep = self.root / "new parent" / "new state"
        desk.validate_configuration({**self.config, "state_dir": str(deep)})
        self.assertFalse(deep.parent.exists())

    def test_ready_result_projects_known_identity_and_returns_exact_url_without_io(self):
        value = ready(unknown_private_path="must-not-forward")
        result = self.parse(value, stderr=b"diagnostics are not identity")
        self.assertNotIn("unknown_private_path", result)
        self.assertEqual(desk.browser_url(result), value["web_url"])
        self.assertEqual(result["pid"], 1234)
        self.assertEqual(self.parse(ready(created=True, reused=False))["created"], True)
        self.assertEqual(desk.browser_url(self.parse(ready(web_url=ready()["api_origin"]))), ready()["api_origin"])

    def test_nonready_dtos_remain_nonready_and_never_supply_browser_authority(self):
        for state in ("absent", "unavailable", "incompatible"):
            value = failure(state)
            self.assertEqual(self.parse(value, 1), value)
            self.assert_error("DESK_NOT_READY", lambda: desk.browser_url(value))
        self.assertEqual(self.parse(failure("incompatible", code="invalid_arguments"), 2)["state"], "incompatible")
        self.assertIsNotNone(self.parse(failure(runtime_id="sha256:" + "b" * 64), 1)["runtime_id"])

    def test_native_filesystem_error_codes_remain_valid_nonready_cli_results(self):
        # The real CLI forwards fs error.code while rejecting a missing or
        # unreadable manifest. These are valid failure DTOs, never ready.
        for code in ("ENOENT", "EACCES", "EPERM"):
            result = self.parse(failure("incompatible", code=code), 1)
            self.assertEqual(result["code"], code)
            self.assert_error("DESK_NOT_READY", lambda result=result: desk.browser_url(result))

    def test_exit_code_and_state_cannot_disagree(self):
        for value, exit_code in ((ready(), 1), (failure(), 0), (failure(), 2),
                                 (failure(code="invalid_arguments"), 1)):
            self.assert_error("INVALID_RESPONSE", lambda value=value, exit_code=exit_code: self.parse(value, exit_code))
        for exit_code in (-1, 3, True, None):
            self.assert_error("PROCESS_FAILED", lambda exit_code=exit_code: self.parse(ready(), exit_code))

    def test_stdout_requires_one_utf8_json_object_without_duplicate_keys_or_nonfinite_numbers(self):
        invalid = (b"", b"<html>ready</html>", b"null", b"[]", b"true", b"{}\n{}", b"\xff", b"{\"a\":1,\"a\":2}", b"{\"a\":NaN}")
        for value in invalid:
            self.assert_error("INVALID_RESPONSE", lambda value=value: desk.parse_cli_result(0, value, b""))
        self.assert_error("INVALID_RESPONSE", lambda: desk.parse_cli_result(0, "{}", b""))
        self.assert_error("INVALID_RESPONSE", lambda: desk.parse_cli_result(0, b"{}", ""))

    def test_both_output_streams_are_bounded_and_stderr_cannot_supply_a_result(self):
        self.assert_error("OUTPUT_TOO_LARGE", lambda: desk.parse_cli_result(0, b"x" * (64 * 1024 + 1), b""))
        self.assert_error("OUTPUT_TOO_LARGE", lambda: desk.parse_cli_result(0, b"{}", b"x" * (8 * 1024 + 1)))
        self.assert_error("INVALID_RESPONSE", lambda: desk.parse_cli_result(0, b"", json.dumps(ready()).encode()))

    def test_ready_requires_complete_current_identity_and_safe_integer_pid(self):
        invalid = ({"app_version": "0.6.0"}, {"release_status": "published"}, {"runtime_id": None}, {"runtime_id": "sha256:not-a-hash"},
                   {"instance_id": "not-an-instance"}, {"instance_id": "12345678-1234-0234-0234-123456789abc"},
                   {"pid": True}, {"pid": 0}, {"pid": 1.2}, {"pid": 9007199254740992},
                   {"created": False, "reused": False}, {"created": True, "reused": True}, {"created": 1},
                   {"code": "not_verified"}, {"reason": ""}, {"reason": "x" * 2049}, {"reason": "line\nbreak"},
                   {"lock_present": "yes"}, {"state": "healthy"})
        for change in invalid:
            self.assert_error("INVALID_RESPONSE", lambda change=change: self.parse(ready(**change)))
        for key in ready():
            if key == "lock_present":
                continue
            value = ready()
            del value[key]
            self.assert_error("INVALID_RESPONSE", lambda value=value: self.parse(value))

    def test_ready_urls_reject_remote_credentials_redirect_paths_queries_and_origin_mismatch(self):
        for url in ("https://127.0.0.1:54321/", "http://localhost:54321/", "http://127.0.0.1:54322/", "http://192.0.2.1:54321/",
                    "http://user:secret@127.0.0.1:54321/", "http://127.0.0.1:54321/page", "http://127.0.0.1:54321/?q=1",
                    "http://127.0.0.1:54321/#part", " http://127.0.0.1:54321/", "http://127.0.0.1:54321/\n"):
            self.assert_error("INVALID_RESPONSE", lambda url=url: self.parse(ready(web_url=url)))
        for origin in ("http://127.0.0.1:0", "http://127.0.0.1:65536", "http://127.0.0.1:054321", "http://127.0.0.1:54321/"):
            self.assert_error("INVALID_RESPONSE", lambda origin=origin: self.parse(ready(api_origin=origin, web_url=origin)))

    def test_nonready_rejects_forged_url_identity_and_creation_flags(self):
        for change in ({"web_url": ready()["web_url"]}, {"api_origin": ready()["api_origin"]},
                       {"instance_id": ready()["instance_id"]}, {"pid": 1234}, {"created": True}, {"reused": True},
                       {"runtime_id": "garbage"}, {"code": "verified"}):
            self.assert_error("INVALID_RESPONSE", lambda change=change: self.parse(failure(**change), 1))
        self.assert_error("INVALID_RESPONSE", lambda: desk.browser_url(ready(web_url="https://example.invalid/")))


if __name__ == "__main__":
    unittest.main()
