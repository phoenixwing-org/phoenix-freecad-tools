# SPDX-License-Identifier: Apache-2.0
"""Injected Qt-core process contract: no Qt, Node, service or browser is run."""
from pathlib import Path
import sys
import unittest

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
_previous_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_desk_qprocess as desk_process
finally:
    sys.path[:] = _previous_path


class PftFakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class PftFakeEnvironment:
    inherited = {}

    def __init__(self):
        self.values = {}

    @classmethod
    def systemEnvironment(cls):
        result = cls()
        result.values = dict(cls.inherited)
        return result

    def contains(self, key):
        return key in self.values

    def value(self, key):
        return self.values[key]

    def insert(self, key, value):
        self.values[key] = value


class PftFakeTimer:
    def __init__(self, parent):
        self.parent, parent.timer = parent, self
        self.timeout = PftFakeSignal()
        self.active, self.interval, self.single_shot = False, None, False

    def setSingleShot(self, value):
        self.single_shot = value

    def start(self, interval):
        self.interval, self.active = interval, True

    def stop(self):
        self.active = False

    def fire(self):
        if self.active:
            if self.single_shot:
                self.active = False
            self.timeout.emit()


class PftFakeProcess:
    NormalExit, CrashExit = "normal", "crash"
    FailedToStart, ReadError, WriteError, UnknownError, Crashed = "start-error", "read-error", "write-error", "unknown-error", "crashed"
    instances = []
    fail_start = False

    def __init__(self, parent):
        self.parent = parent
        self.readyReadStandardOutput = PftFakeSignal()
        self.readyReadStandardError = PftFakeSignal()
        self.finished = PftFakeSignal()
        self.errorOccurred = PftFakeSignal()
        self.stdout, self.stderr = bytearray(), bytearray()
        self.environment, self.started = None, None
        self.kill_count, self.deleted = 0, False
        self.auto_finish_kill = True
        type(self).instances.append(self)

    def setProcessEnvironment(self, environment):
        self.environment = environment.values

    def start(self, program, arguments):
        self.started = (program, list(arguments))
        if type(self).fail_start:
            raise RuntimeError("synthetic process start failure")

    def readAllStandardOutput(self):
        result = bytes(self.stdout)
        self.stdout.clear()
        return result

    def readAllStandardError(self):
        result = bytes(self.stderr)
        self.stderr.clear()
        return result

    def output(self, data):
        self.stdout.extend(data)
        self.readyReadStandardOutput.emit()

    def diagnostic(self, data):
        self.stderr.extend(data)
        self.readyReadStandardError.emit()

    def kill(self):
        self.kill_count += 1
        if self.auto_finish_kill:
            self.errorOccurred.emit(self.Crashed)
            self.finished.emit(9, self.CrashExit)

    def deleteLater(self):
        self.deleted = True


class PftFakeCore:
    QProcess = PftFakeProcess
    QTimer = PftFakeTimer
    QProcessEnvironment = PftFakeEnvironment


class PftDeskProcessTests(unittest.TestCase):
    def setUp(self):
        PftFakeProcess.instances, PftFakeProcess.fail_start = [], False
        PftFakeEnvironment.inherited = {"PATH": "example-path", "HOME": "/example/home", "LANG": "en_US.UTF-8",
            "TZ": "UTC", "NODE_OPTIONS": "must-not-inherit", "NODE_PATH": "must-not-inherit", "PYTHONPATH": "must-not-inherit",
            "PHOENIX_USER_DATA_DIR": "must-not-inherit", "DYLD_INSERT_LIBRARIES": "must-not-inherit", "UNRELATED_SECRET": "must-not-inherit"}
        self.completed, self.failed = [], []
        self.subject = desk_process.PftDeskProcess(PftFakeCore, self)

    def start(self):
        self.subject.start("example-node", ["example-cli", "status", "--state-dir", "example-state"],
            lambda *args: self.completed.append(args), self.failed.append)
        return PftFakeProcess.instances[-1]

    def assert_failed_once(self, code):
        self.assertEqual(len(self.failed), 1)
        self.assertEqual(self.failed[0].code, code)
        self.assertFalse(self.completed)
        self.assertIsNone(self.subject._process)

    def test_success_preserves_exact_bytes_and_drains_tail_without_opening_or_killing_anything(self):
        process = self.start()
        self.assertEqual(process.started, ("example-node", ["example-cli", "status", "--state-dir", "example-state"]))
        self.assertTrue(process.timer.single_shot)
        self.assertEqual(process.timer.interval, 15000)
        process.output(b'{"example":"')
        process.output("文本".encode("utf-8"))
        process.stdout.extend(b'"}\n')  # Qt may deliver the final bytes only at finish.
        process.diagnostic(b"one\n")
        process.stderr.extend(b"two\n")
        process.finished.emit(0, process.NormalExit)
        self.assertEqual(self.completed, [(0, '{"example":"文本"}\n'.encode("utf-8"), b"one\ntwo\n")])
        self.assertFalse(self.failed)
        self.assertEqual(process.kill_count, 0)
        self.assertFalse(process.timer.active)
        self.assertTrue(process.deleted)
        self.assertIsNone(self.subject._process)

    def test_environment_is_explicit_allowlist_and_does_not_repurpose_home(self):
        process = self.start()
        self.assertEqual(process.environment, {"PATH": "example-path", "HOME": "/example/home", "LANG": "en_US.UTF-8", "TZ": "UTC"})
        for key in ("NODE_OPTIONS", "NODE_PATH", "PYTHONPATH", "PHOENIX_USER_DATA_DIR", "DYLD_INSERT_LIBRARIES", "UNRELATED_SECRET"):
            self.assertNotIn(key, process.environment)

    def test_exact_output_limits_are_allowed_and_never_merged(self):
        process = self.start()
        process.output(b"o" * (64 * 1024))
        process.diagnostic(b"e" * (8 * 1024))
        process.finished.emit(1, process.NormalExit)
        self.assertFalse(self.failed)
        self.assertEqual(len(self.completed[0][1]), 64 * 1024)
        self.assertEqual(len(self.completed[0][2]), 8 * 1024)
        self.assertEqual(process.kill_count, 0)

    def test_stdout_over_limit_kills_only_this_helper_and_finishes_as_error(self):
        process = self.start()
        process.output(b"a" * (64 * 1024))
        process.output(b"b")
        self.assert_failed_once("DESK_OUTPUT_TOO_LARGE")
        self.assertEqual(process.kill_count, 1)
        process.finished.emit(0, process.NormalExit)
        self.assertEqual(len(self.failed), 1)
        self.assertFalse(self.completed)

    def test_stderr_over_limit_is_also_error_even_with_ready_looking_stdout(self):
        process = self.start()
        process.output(b'{"state":"ready"}\n')
        process.diagnostic(b"e" * (8 * 1024 + 1))
        self.assert_failed_once("DESK_OUTPUT_TOO_LARGE")
        self.assertEqual(process.kill_count, 1)

    def test_final_undelivered_output_is_checked_again_for_overflow(self):
        process = self.start()
        process.stdout.extend(b"o" * (64 * 1024 + 1))
        process.finished.emit(0, process.NormalExit)
        self.assert_failed_once("DESK_OUTPUT_TOO_LARGE")

    def test_failed_to_start_clears_singleflight_and_late_finished_is_ignored(self):
        process = self.start()
        process.errorOccurred.emit(process.FailedToStart)
        self.assert_failed_once("DESK_PROCESS_FAILED")
        self.assertEqual(process.kill_count, 0)
        process.finished.emit(0, process.NormalExit)
        self.assertEqual(len(self.failed), 1)
        next_process = self.start()
        self.assertIsNot(next_process, process)

    def test_start_exception_is_reported_and_releases_the_helper(self):
        PftFakeProcess.fail_start = True
        process = self.start()
        self.assert_failed_once("DESK_PROCESS_FAILED")
        self.assertTrue(process.deleted)
        self.assertFalse(process.timer.active)

    def test_timeout_targets_only_created_cli_not_an_independent_daemon(self):
        daemon = PftFakeProcess(None)  # A distinct process is never selected by PID/port.
        process = self.start()
        process.timer.fire()
        self.assert_failed_once("DESK_CLI_TIMEOUT")
        self.assertEqual(process.kill_count, 1)
        self.assertEqual(daemon.kill_count, 0)
        process.timer.timeout.emit()
        process.finished.emit(0, process.NormalExit)
        self.assertEqual(process.kill_count, 1)
        self.assertEqual(len(self.failed), 1)

    def test_crash_exit_never_passes_a_ready_looking_payload_to_completion(self):
        process = self.start()
        process.output(b'{"state":"ready"}\n')
        process.finished.emit(0, process.CrashExit)
        self.assert_failed_once("DESK_PROCESS_FAILED")

    def test_read_error_cannot_be_followed_by_a_successful_ready_result(self):
        process = self.start()
        process.output(b'{"state":"ready"}\n')
        process.errorOccurred.emit(process.ReadError)
        process.finished.emit(0, process.NormalExit)
        self.assert_failed_once("DESK_PROCESS_FAILED")

    def test_duplicate_finish_and_stale_signals_cannot_complete_or_clear_a_new_run(self):
        process = self.start()
        process.finished.emit(0, process.NormalExit)
        next_process = self.start()
        process.finished.emit(0, process.NormalExit)
        process.errorOccurred.emit(process.FailedToStart)
        process.timer.timeout.emit()
        self.assertIs(self.subject._process, next_process)
        self.assertEqual(len(self.completed), 1)
        self.assertFalse(self.failed)
        self.assertEqual(next_process.kill_count, 0)
        next_process.finished.emit(0, next_process.NormalExit)
        self.assertEqual(len(self.completed), 2)

    def test_active_helper_rejects_another_start_without_allocating_or_killing(self):
        process = self.start()
        with self.assertRaises(desk_process.PftDeskError) as caught:
            self.start()
        self.assertEqual(caught.exception.code, "DESK_BUSY")
        self.assertEqual(len(PftFakeProcess.instances), 1)
        self.assertEqual(process.kill_count, 0)


if __name__ == "__main__":
    unittest.main()
