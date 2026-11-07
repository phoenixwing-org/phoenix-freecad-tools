# SPDX-License-Identifier: Apache-2.0
"""Owned helper lifecycle tests; no PDF parser, account or native child."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools"
_saved = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_pdf_qprocess as adapter
finally:
    sys.path[:] = _saved
DATA = b"%PDF-independent-transport-fixture"


def response(**changes):
    value = {"ok": True, "backend": "coregraphics", "input_sha256": hashlib.sha256(DATA).hexdigest(),
             "byte_count": len(DATA), "page_count": 1, "media_box_points": [0, 0, 200, 100],
             "rotation_degrees": 0, "user_unit": 1, "width": 2, "height": 1, "stride": 8,
             "pixel_format": "rgba8888-premultiplied",
             "pixels_base64": base64.b64encode(b"\x01\x02\x03\xff" * 2).decode("ascii")}
    value.update(changes)
    return json.dumps(value).encode("ascii")


class Signal:
    def __init__(self): self.callbacks = []
    def connect(self, callback): self.callbacks.append(callback)
    def emit(self, *args):
        for callback in tuple(self.callbacks): callback(*args)


class Core:
    def __init__(self):
        self.processes, self.timers = [], []
        self.application = object()
        self.QCoreApplication = types.SimpleNamespace(instance=lambda: self.application)
        self.start_hook = self.configure_hook = self.timer_hook = None
        self.partial = None
        self.pending = 0
        owner = self

        class Process:
            FailedToStart, NormalExit, CrashExit = 0, 0, 1
            NotRunning, Starting, Running = 0, 1, 2
            def __init__(self, parent):
                self.parent, self.state_value = parent, self.NotRunning
                self.killed, self.deleted, self.closed = 0, 0, 0
                self.stdout, self.stderr, self.writes = bytearray(), bytearray(), []
                self.cpp_deleted = False
                for name in ("started", "bytesWritten", "readyReadStandardOutput", "readyReadStandardError",
                             "finished", "errorOccurred", "destroyed"):
                    setattr(self, name, Signal())
                owner.processes.append(self)
            def live(self):
                if self.cpp_deleted: raise RuntimeError("neutral-deleted-wrapper")
            def setProcessEnvironment(self, environment):
                self.environment = environment.values
                if owner.configure_hook: owner.configure_hook(self)
            def setWorkingDirectory(self, value): self.cwd = value
            def start(self, program, args):
                self.program, self.args, self.state_value = program, args, self.Starting
                if owner.start_hook: owner.start_hook(self)
            def state(self): self.live(); return self.state_value
            def bytesToWrite(self): self.live(); return owner.pending
            def write(self, data):
                self.live()
                count = min(owner.partial, len(data)) if owner.partial is not None else len(data)
                if count > 0: self.writes.append(data[:count])
                return count
            def closeWriteChannel(self): self.live(); self.closed += 1
            def kill(self): self.live(); self.killed += 1
            def deleteLater(self): self.live(); self.deleted += 1
            def readAllStandardOutput(self):
                self.live(); result = bytes(self.stdout); self.stdout.clear(); return result
            def readAllStandardError(self):
                self.live(); result = bytes(self.stderr); self.stderr.clear(); return result
            def ready(self): self.state_value = self.Running; self.started.emit()
            def output(self, data): self.stdout.extend(data); self.readyReadStandardOutput.emit()
            def diagnostic(self, data): self.stderr.extend(data); self.readyReadStandardError.emit()
            def exit(self, code=0, status=0):
                self.state_value = self.NotRunning; self.finished.emit(code, status)
            def destroy(self): self.cpp_deleted = True; self.destroyed.emit()

        class Timer:
            def __init__(self, parent):
                self.parent, self.timeout, self.active = parent, Signal(), False
                owner.timers.append(self)
                if owner.timer_hook: owner.timer_hook(self)
            def setSingleShot(self, value): self.single = value
            def start(self, value): self.delay, self.active = value, True
            def stop(self): self.active = False

        class Environment:
            def __init__(self): self.values = {}
            def insert(self, key, value): self.values[key] = value
        self.QProcess, self.QTimer, self.QProcessEnvironment = Process, Timer, Environment


class PdfProcessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pft-pdf-process-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.program = self.root / "bin" / "python3"
        self.program.parent.mkdir()
        self.program.write_bytes(b"neutral-not-executed")
        self.program.chmod(0o700)
        self.runtime = {"program": str(self.program), "helper": str(ADDON / "pft_pdf_helper.py")}
        self.core = Core()
        self.parent = object()
        self.process = adapter.PftPdfPreviewProcess(self.core, self.parent)
        self.completed, self.failed = [], []

    def start(self, **options):
        self.process.start(self.runtime, options.get("data", DATA), 2, 1,
                           options.get("completed", self.completed.append),
                           options.get("failed", self.failed.append))
        return self.core.processes[-1]

    def assert_failure(self, code):
        self.assertEqual([error.code for error in self.failed], [code])
        self.assertEqual(self.completed, [])
        self.assertNotIn("private-untrusted", str(self.failed))

    def test_application_parent_exact_isolated_args_and_environment(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "private-untrusted", "PYTHONPATH": "/neutral",
                                       "DYLD_INSERT_LIBRARIES": "/neutral", "HTTP_PROXY": "http://neutral"}):
            child = self.start()
        self.assertIs(child.parent, self.core.application)
        self.assertIsNot(child.parent, self.parent)
        self.assertEqual(child.program, str(self.program))
        self.assertEqual(child.args, ["-I", "-S", "-B", self.runtime["helper"]])
        self.assertEqual(child.cwd, str(ADDON))
        self.assertEqual(child.environment, {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
        self.assertEqual(child.writes, [])
        self.assertIs(self.core.timers[0].parent, child)
        self.assertEqual(self.core.timers[0].delay, 15000)

    def test_partial_writes_and_fragmented_response_bind_exact_bytes_before_callback(self):
        self.core.partial = 7
        child = self.start(); child.ready()
        self.assertEqual(child.closed, 1)
        request = adapter.encode_pdf_request(DATA, 2, 1)
        self.assertEqual(b"".join(child.writes), request)
        wire = response()
        for start in range(0, len(wire), 9): child.output(wire[start:start + 9])
        self.assertEqual(self.completed + self.failed, [])
        child.exit()
        self.assertFalse(self.process.has_active_session)
        self.assertEqual(self.completed[0]["input_sha256"], hashlib.sha256(DATA).hexdigest())
        self.assertEqual(self.failed, [])
        self.assertEqual(child.deleted, 1)
        self.assertFalse(self.core.timers[0].active)

    def test_backpressure_resumes_on_bytes_written_and_closes_only_once(self):
        self.core.pending = 65537
        child = self.start(); child.ready()
        self.assertEqual(child.writes, [])
        self.assertEqual(child.closed, 0)
        self.core.pending = 0
        child.bytesWritten.emit(65537)
        self.assertEqual(b"".join(child.writes), adapter.encode_pdf_request(DATA, 2, 1))
        self.assertEqual(child.closed, 1)
        child.bytesWritten.emit(1)
        self.assertEqual(child.closed, 1)

    def test_large_request_is_sent_in_bounded_chunks(self):
        data = b"x" * 120000
        child = self.start(data=data); child.ready()
        self.assertGreater(len(child.writes), 1)
        self.assertTrue(all(len(chunk) <= 65536 for chunk in child.writes))
        self.assertEqual(b"".join(child.writes), adapter.encode_pdf_request(data, 2, 1))

    def test_cancel_starting_waits_for_exit_and_kills_late_started_child_again(self):
        child = self.start()
        self.process.cancel(); self.process.cancel()
        self.assertTrue(self.process.has_active_session)
        self.assertEqual(child.killed, 1)
        self.assertEqual(self.failed, [])
        with self.assertRaises(adapter.PftPdfError) as caught: self.start()
        self.assertEqual(caught.exception.code, "PDF_BUSY")
        child.ready()
        self.assertEqual(child.killed, 2)
        self.assertEqual(child.writes, [])
        child.exit(-9, 1)
        self.assert_failure("PDF_CANCELLED")
        self.assertFalse(self.process.has_active_session)
        child.exit(-9, 1); child.errorOccurred.emit(1); self.core.timers[0].timeout.emit()
        self.assert_failure("PDF_CANCELLED")
        self.assertEqual(child.deleted, 1)

    def test_timeout_retires_only_after_exact_child_termination(self):
        child = self.start(); child.ready()
        self.core.timers[0].timeout.emit()
        self.assertTrue(self.process.has_active_session)
        self.assertEqual(self.failed, [])
        self.assertEqual(child.killed, 1)
        child.exit(-9, 1)
        self.assert_failure("PDF_TIMEOUT")

    def test_stdout_and_stderr_budgets_fail_without_exposing_diagnostics(self):
        for channel, limit in (("stdout", adapter.MAX_RESPONSE_BYTES), ("stderr", 16 * 1024)):
            with self.subTest(channel=channel):
                self.failed.clear()
                child = self.start(); child.ready()
                send = child.output if channel == "stdout" else child.diagnostic
                send(b"x" * limit)
                self.assertEqual(child.killed, 0)
                send(b"x")
                self.assertEqual(child.killed, 1)
                self.assertTrue(self.process.has_active_session)
                child.exit(-9, 1)
                self.assert_failure("PDF_OUTPUT_TOO_LARGE")

    def test_pending_output_is_drained_on_finish(self):
        child = self.start(); child.ready()
        child.stdout.extend(response())
        child.exit()
        self.assertEqual(len(self.completed), 1)
        self.assertEqual(self.failed, [])

    def test_response_must_be_valid_and_bound_to_input_not_only_zero_exit(self):
        for wire in (b"private-untrusted-diagnostic", response(input_sha256="0" * 64), response(width=3),
                     b'{"ok":false,"code":"PDF_ENCRYPTED"}', response() + b"{}"):
            with self.subTest(wire_size=len(wire)):
                self.failed.clear()
                child = self.start(); child.ready(); child.output(wire); child.exit()
                self.assertEqual(self.completed, [])
                self.assertEqual(len(self.failed), 1)
                self.assertIn(self.failed[0].code, ("PDF_INVALID_RESPONSE", "PDF_ENCRYPTED"))

    def test_nonzero_or_crash_is_failure_even_with_valid_response(self):
        for code, status in ((1, 0), (0, 1), (-9, 1)):
            self.failed.clear()
            child = self.start(); child.ready(); child.output(response()); child.exit(code, status)
            self.assert_failure("PDF_PROCESS_FAILED")

    def test_failed_to_start_releases_live_qobject_once_without_kill(self):
        def failed_start(child):
            child.state_value = child.NotRunning
            child.errorOccurred.emit(child.FailedToStart)
        self.core.start_hook = failed_start
        child = self.start()
        self.assert_failure("PDF_PROCESS_FAILED")
        self.assertEqual(child.killed, 0)
        self.assertEqual(child.deleted, 1)
        child.exit(1, 1)
        self.assertEqual(child.deleted, 1)

    def test_configuration_exception_deletes_object_without_spawning(self):
        def broken(_): raise RuntimeError("private-untrusted-configuration")
        self.core.configure_hook = broken
        child = self.start()
        self.assert_failure("PDF_PROCESS_FAILED")
        self.assertFalse(hasattr(child, "program"))
        self.assertEqual(child.deleted, 1)
        self.assertEqual(child.killed, 0)

    def test_start_exception_after_spawn_keeps_busy_kills_and_waits(self):
        def broken(child): child.ready(); raise RuntimeError("private-untrusted-after-spawn")
        self.core.start_hook = broken
        child = self.start()
        self.assertTrue(self.process.has_active_session)
        self.assertEqual(child.deleted, 0)
        self.assertEqual(child.killed, 1)
        self.assertEqual(self.failed, [])
        child.exit(-9, 1)
        self.assert_failure("PDF_PROCESS_FAILED")

    def test_start_exception_with_proven_not_running_retires_without_kill(self):
        def broken(child): child.state_value = child.NotRunning; raise RuntimeError("neutral-start")
        self.core.start_hook = broken
        child = self.start()
        self.assert_failure("PDF_PROCESS_FAILED")
        self.assertEqual(child.killed, 0)
        self.assertEqual(child.deleted, 1)

    def test_unknown_state_or_failed_kill_does_not_imply_exit(self):
        child = self.start()
        def unknown(): raise RuntimeError("neutral-state")
        child.state = unknown
        child.kill = unknown
        self.process.cancel()
        self.assertTrue(self.process.has_active_session)
        self.assertEqual(self.failed, [])
        with self.assertRaises(adapter.PftPdfError): self.start()
        child.destroy()
        self.assert_failure("PDF_CANCELLED")
        self.assertEqual(child.deleted, 0)

    def test_invalid_or_zero_write_is_failure_not_truncated_success(self):
        for partial in (0, -1):
            self.failed.clear(); self.core.partial = partial
            child = self.start(); child.ready()
            self.assertEqual(child.killed, 1)
            self.assertEqual(child.closed, 0)
            child.exit(-9, 1)
            self.assert_failure("PDF_WRITE_FAILED")

    def test_destroyed_process_delivers_one_fixed_terminal_callback(self):
        child = self.start(); child.destroy(); child.destroy()
        self.assert_failure("PDF_PROCESS_FAILED")
        self.assertFalse(self.process.has_active_session)
        self.assertEqual(child.deleted, 0)

    def test_reentrant_callback_starts_new_child_without_late_old_mutations(self):
        events = []
        def completed(reply): events.append(reply); self.start()
        old = self.start(completed=completed)
        old.ready(); old.output(response()); old.exit()
        new = self.core.processes[-1]
        self.assertIsNot(new, old)
        self.assertTrue(self.process.has_active_session)
        old.exit(); old.started.emit(); old.errorOccurred.emit(1); self.core.timers[0].timeout.emit()
        self.assertEqual(len(events), 1)
        self.assertEqual(old.deleted, 1)
        self.assertEqual(new.killed, 0)
        self.assertIs(self.process._session["process"], new)

    def test_explicit_retry_retires_only_failed_authoritatively_stopped_child(self):
        old = self.start(); self.process.cancel()
        old.state_value = old.NotRunning
        new = self.start()
        self.assertIsNot(new, old)
        self.assert_failure("PDF_CANCELLED")
        old.exit()
        self.assertEqual(old.deleted, 1)
        self.assertIs(self.process._session["process"], new)

    def test_callback_exception_cannot_prevent_retirement(self):
        def broken(_): raise RuntimeError("neutral-closed-widget")
        child = self.start(completed=broken); child.ready(); child.output(response()); child.exit()
        self.assertFalse(self.process.has_active_session)
        self.assertEqual(child.deleted, 1)

    def test_timer_creation_failure_cleans_up_unstarted_object(self):
        def broken(_): raise RuntimeError("neutral-no-timer")
        self.core.timer_hook = broken
        with self.assertRaises(adapter.PftPdfError) as caught: self.start()
        self.assertEqual(caught.exception.code, "PDF_PROCESS_FAILED")
        self.assertEqual(self.core.processes[0].deleted, 1)
        self.assertEqual(self.failed, [])

    def test_invalid_runtime_request_callback_and_timeout_never_spawn(self):
        for runtime in ({}, {**self.runtime, "extra": "neutral"}, {**self.runtime, "program": "relative"},
                        {**self.runtime, "helper": str(self.root / "unknown.py")}):
            with self.subTest(runtime_keys=list(runtime)), self.assertRaises(adapter.PftPdfError):
                self.process.start(runtime, DATA, 2, 1, self.completed.append, self.failed.append)
        with self.assertRaises(adapter.PftPdfError): self.start(data=bytearray(DATA))
        with self.assertRaises(adapter.PftPdfError): self.start(completed=None)
        for timeout in (True, 0, 99, 60001, 1.2):
            with self.assertRaises(adapter.PftPdfError): adapter.PftPdfPreviewProcess(self.core, timeout_ms=timeout)
        self.assertEqual(self.core.processes, [])

    def test_runtime_resolver_accepts_only_existing_bundle_programs(self):
        expected = adapter.resolve_pdf_runtime(str(self.root), platform="darwin", python_version=(3, 11))
        self.assertEqual(expected, self.runtime)
        self.assertEqual(self.core.processes, [])
        self.program.chmod(0o600)
        with self.assertRaises(adapter.PftPdfError):
            adapter.resolve_pdf_runtime(str(self.root), platform="darwin", python_version=(3, 11))
        with self.assertRaises(adapter.PftPdfError) as caught:
            adapter.resolve_pdf_runtime(str(self.root), platform="win32")
        self.assertEqual(caught.exception.code, "PDF_BACKEND_UNAVAILABLE")

    def test_shared_resource_subdirectory_is_not_the_application_runtime_prefix(self):
        shared = self.root / "share" / "freecad"
        shared.mkdir(parents=True)
        with self.assertRaises(adapter.PftPdfError) as caught:
            adapter.resolve_pdf_runtime(str(shared), platform="darwin", python_version=(3, 11))
        self.assertEqual(caught.exception.code, "PDF_RUNTIME_UNAVAILABLE")
        self.assertEqual(adapter.resolve_pdf_runtime(str(self.root), platform="darwin", python_version=(3, 11)),
                         self.runtime)

    def test_runtime_resolver_rejects_escape_symlink_missing_relative_and_controls(self):
        self.program.unlink(); self.program.symlink_to("/bin/sh")
        for path in (str(self.root), str(self.root / "missing"), "relative", str(self.root) + "\n"):
            with self.subTest(path_kind=Path(path).name), self.assertRaises(adapter.PftPdfError):
                adapter.resolve_pdf_runtime(path, platform="darwin", python_version=(3, 11))


if __name__ == "__main__":
    unittest.main()
