# SPDX-License-Identifier: Apache-2.0
"""Exercise action separation, stale callbacks and atomic local preferences."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools"
_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_desk_controller as controller
finally:
    sys.path[:] = _path
from test_pft_workbench_settings_flags import Preferences, App as PreferenceApp

READY = {"state": "ready", "code": "verified", "reason": "Verified example", "api_origin": "http://127.0.0.1:12345", "web_url": "http://127.0.0.1:12345/", "instance_id": "12345678-1234-4234-8234-123456789abc", "created": False, "reused": True, "app_version": "0.5.0", "release_status": "not-published", "runtime_id": "sha256:" + "a" * 64, "pid": 123}
CONFIG = {"node_executable": "/example/node", "cli_entry": "/example/scripts/desk-web-service.mjs", "state_dir": "/example/state"}


class App(PreferenceApp):
    def __init__(self, preferences):
        super().__init__(preferences)
        self.persist_calls, self.fail_persist_calls = [], set()
        self.disk_strings = dict(preferences.strings)

    def saveParameter(self, config="User parameter"):
        self.persist_calls.append(config)
        # Native writes may partially succeed before raising. A second native
        # save is required to persist the restored keys, not just repair RAM.
        self.disk_strings = dict(self.preferences.strings)
        if len(self.persist_calls) in self.fail_persist_calls:
            raise RuntimeError("native parameter save failed after writing")


class Runner:
    def __init__(self):
        self.calls = []

    def start(self, program, arguments, completed, failed):
        self.calls.append((program, arguments, completed, failed))

    def complete(self, snapshot=READY, exit_code=0, index=-1):
        self.calls[index][2](exit_code, json.dumps(snapshot).encode(), b"")


class PftDeskControllerTests(unittest.TestCase):
    def setUp(self):
        self.runner, self.states, self.opened = Runner(), [], []
        self.subject = controller.PftDeskController(self.runner, self.states.append, lambda url: self.opened.append(url) is None)
        self.build = patch.object(controller, "build_command", side_effect=lambda config, action: ("example-node", [action]))
        self.build.start()
        self.addCleanup(self.build.stop)

    def test_status_and_ensure_are_separate_and_neither_opens_browser(self):
        self.subject.run(CONFIG, "status")
        self.assertTrue(self.subject.busy)
        self.runner.complete()
        self.assertFalse(self.subject.busy)
        self.subject.run(CONFIG, "ensure")
        self.runner.complete()
        self.assertEqual([call[1] for call in self.runner.calls], [["status"], ["ensure"]])
        self.assertFalse(self.opened)

    def test_browser_action_probes_status_again_and_never_ensures(self):
        self.subject.run(CONFIG, "open")
        self.assertFalse(self.opened)
        self.assertEqual(self.runner.calls[0][1], ["status"])
        self.runner.complete()
        self.assertEqual(self.opened, [READY["web_url"]])

    def test_nonready_does_not_open_and_cannot_reuse_old_ready(self):
        self.subject.run(CONFIG, "status"); self.runner.complete()
        absent = {**READY, "state": "absent", "code": "not_found", "created": False, "reused": False,
                  **{key: None for key in ("api_origin", "web_url", "instance_id", "pid")}}
        self.subject.run(CONFIG, "open"); self.runner.complete(absent, 1)
        self.assertFalse(self.opened)
        self.assertIsNone(self.subject.snapshot)
        self.assertEqual(self.states[-1]["error"]["code"], "DESK_NOT_READY")

    def test_single_flight_and_duplicate_or_late_callbacks_cannot_affect_new_run(self):
        self.subject.run(CONFIG, "status")
        old_done, old_failed = self.runner.calls[-1][2:]
        self.subject.run(CONFIG, "ensure")
        self.assertEqual(len(self.runner.calls), 1)
        self.assertTrue(self.subject.busy)
        self.runner.complete()
        self.subject.run(CONFIG, "status")
        old_done(0, json.dumps(READY).encode(), b"")
        old_failed(RuntimeError("private details"))
        self.assertTrue(self.subject.busy)
        self.assertIsNone(self.subject.snapshot)
        self.runner.complete()
        self.assertFalse(self.subject.busy)

    def test_configuration_change_invalidates_ready_and_busy_cannot_invalidate(self):
        self.subject.run(CONFIG, "status")
        with self.assertRaises(controller.PftDeskError): self.subject.invalidate()
        self.runner.complete(); self.subject.invalidate()
        self.assertIsNone(self.subject.snapshot)

    def test_malformed_output_or_runner_failure_never_opens_and_clears_busy(self):
        self.subject.run(CONFIG, "open")
        self.runner.calls[-1][2](0, b'<html>ready</html>', b'private path')
        self.assertFalse(self.subject.busy)
        self.assertFalse(self.opened)
        self.subject.run(CONFIG, "status")
        self.runner.calls[-1][3](RuntimeError("private details"))
        self.assertNotIn("private", self.states[-1]["error"]["message"])
        self.assertIsNone(self.subject.snapshot)

    def test_failed_browser_launch_reports_failure_without_process_action(self):
        self.subject.open_url = lambda url: False
        self.subject.run(CONFIG, "open"); self.runner.complete()
        self.assertEqual(self.states[-1]["error"]["code"], "DESK_BROWSER_OPEN_FAILED")
        self.assertEqual(len(self.runner.calls), 1)

    def test_configuration_error_never_dispatches(self):
        with patch.object(controller, "build_command", side_effect=controller.PftDeskError("CONFIGURATION_INVALID", "Invalid")):
            self.subject.run({}, "status")
        self.assertFalse(self.runner.calls)
        self.assertFalse(self.subject.busy)


class PftDeskPreferenceTests(unittest.TestCase):
    def test_loading_has_no_writes_or_document_access(self):
        prefs = Preferences(); app = App(prefs)
        self.assertEqual(controller.load_desk_configuration(app), {key: "" for key in CONFIG})
        self.assertFalse(prefs.writes)
        self.assertEqual(app.persist_calls, [])
        self.assertTrue(app.groups[0].endswith("/PhoenixTools/Desk"))

    def test_successful_save_only_stores_canonical_choices(self):
        prefs = Preferences(); app = App(prefs)
        with patch.object(controller, "validate_configuration", return_value=CONFIG):
            self.assertEqual(controller.save_desk_configuration(app, {}), CONFIG)
        self.assertEqual(set(prefs.strings), {"NodeExecutable", "CliEntry", "StateDirectory"})
        self.assertEqual(controller.load_desk_configuration(app), CONFIG)
        self.assertEqual(app.persist_calls, ["User parameter"])
        self.assertEqual(app.disk_strings, prefs.strings)

    def test_setter_that_mutates_then_raises_rolls_back_absence_and_existing(self):
        prefs = Preferences(strings={"NodeExecutable": "previous-node"}); app = App(prefs)
        prefs.fail_key = "CliEntry"
        with patch.object(controller, "validate_configuration", return_value=CONFIG):
            with self.assertRaises(controller.PftDeskError) as caught:
                controller.save_desk_configuration(app, CONFIG)
        self.assertEqual(caught.exception.code, "DESK_SETTINGS_WRITE_FAILED")
        self.assertEqual(prefs.strings, {"NodeExecutable": "previous-node"})
        self.assertEqual(app.persist_calls, ["User parameter"])
        self.assertEqual(app.disk_strings, {"NodeExecutable": "previous-node"})

    def test_native_save_failure_restores_original_values_and_absence_on_disk(self):
        original = {"NodeExecutable": "previous-node", "CliEntry": "previous-cli"}
        prefs = Preferences(strings=original); app = App(prefs)
        app.fail_persist_calls = {1}
        with patch.object(controller, "validate_configuration", return_value=CONFIG), \
             self.assertRaises(controller.PftDeskError) as caught:
            controller.save_desk_configuration(app, CONFIG)
        self.assertEqual(caught.exception.code, "DESK_SETTINGS_PERSIST_FAILED")
        self.assertEqual(prefs.strings, original)
        self.assertEqual(app.disk_strings, original)
        self.assertEqual(app.persist_calls, ["User parameter", "User parameter"])

    def test_failed_native_restore_is_reported_separately_from_recovered_failure(self):
        original = {"StateDirectory": "previous-state"}
        prefs = Preferences(strings=original); app = App(prefs)
        app.fail_persist_calls = {1, 2}
        with patch.object(controller, "validate_configuration", return_value=CONFIG), \
             self.assertRaises(controller.PftDeskError) as caught:
            controller.save_desk_configuration(app, CONFIG)
        self.assertEqual(caught.exception.code, "DESK_SETTINGS_PERSIST_RESTORE_FAILED")
        self.assertIn("落盘无法确认", caught.exception.message)
        self.assertEqual(prefs.strings, original)
        self.assertEqual(len(app.persist_calls), 2)

    def test_failed_ram_rollback_is_not_persisted_as_a_restored_configuration(self):
        prefs = Preferences(); app = App(prefs)
        prefs.fail_removal = True
        app.fail_persist_calls = {1}
        with patch.object(controller, "validate_configuration", return_value=CONFIG), \
             self.assertRaises(controller.PftDeskError) as caught:
            controller.save_desk_configuration(app, CONFIG)
        self.assertEqual(caught.exception.code, "DESK_SETTINGS_ROLLBACK_FAILED")
        self.assertEqual(len(app.persist_calls), 1)

    def test_missing_native_persistence_api_refuses_to_report_a_ram_only_success(self):
        prefs = Preferences(); app = PreferenceApp(prefs)
        app.saveParameter = None
        with patch.object(controller, "validate_configuration", return_value=CONFIG), \
             self.assertRaises(controller.PftDeskError) as caught:
            controller.save_desk_configuration(app, CONFIG)
        self.assertEqual(caught.exception.code, "DESK_SETTINGS_PERSIST_UNAVAILABLE")
        self.assertEqual(prefs.writes, [])
        self.assertEqual(app.groups, [])

    def test_ignored_preference_write_restores_without_reporting_success(self):
        prefs = Preferences(); app = App(prefs)
        prefs.ignore_key = "StateDirectory"
        with patch.object(controller, "validate_configuration", return_value=CONFIG), \
             self.assertRaises(controller.PftDeskError) as caught:
            controller.save_desk_configuration(app, CONFIG)
        self.assertEqual(caught.exception.code, "DESK_SETTINGS_WRITE_FAILED")
        self.assertEqual(prefs.strings, {})
        self.assertEqual(app.disk_strings, {})
        self.assertEqual(app.persist_calls, ["User parameter"])

    def test_invalid_configuration_has_no_preference_writes(self):
        prefs = Preferences(); app = App(prefs)
        with self.assertRaises(controller.PftDeskError): controller.save_desk_configuration(app, {})
        self.assertFalse(prefs.writes)
        self.assertEqual(app.persist_calls, [])


if __name__ == "__main__":
    unittest.main()
