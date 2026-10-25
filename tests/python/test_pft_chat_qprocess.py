# SPDX-License-Identifier: Apache-2.0
"""Qt transport tests with an owned child stub; no native process is started."""
import base64
import binascii
import importlib.util
import json
import struct
import zlib
from pathlib import Path
import types
import unittest

ADDON = Path(__file__).resolve().parents[2] / 'addon' / 'PhoenixFreeCADTools'
spec = importlib.util.spec_from_file_location('pft_chat_qprocess', ADDON / 'pft_chat_qprocess.py')
transport_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport_module)


def synthetic_cached_thumbnail_event():
    """An independent, valid RGB PNG large enough to exceed the old JSONL bound."""
    width, height = 400, 350
    scanline = b'\x00' + bytes((index * 7) & 255 for index in range(width * 3))
    def chunk(kind, data=b''):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', binascii.crc32(body) & 0xffffffff)
    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
           + chunk(b'IDAT', zlib.compress(scanline * height, level=0)) + chunk(b'IEND'))
    image = base64.b64encode(png).decode('ascii')
    return {'method': 'item/completed', 'params': {'threadId': 'neutral-thread', 'turnId': 'neutral-turn',
        'item': {'id': 'neutral-thumbnail-call', 'type': 'mcpToolCall', 'server': 'phoenix_freecad',
            'tool': 'desk_get_cached_thumbnail', 'status': 'completed',
            'arguments': {'workspace': '/neutral/not-displayed', 'rel': 'parts/Bracket.FCStd', 'revision': 'a' * 64},
            'result': {'content': [{'type': 'image', 'mimeType': 'image/png', 'data': image}],
                'structuredContent': {'ok': True, 'source_kind': 'desk_saved_cache',
                    'data': {'width': width, 'height': height, 'image_in_content': True}}}}}}


class PftSignal:
    def __init__(self): self.callbacks = []
    def connect(self, callback): self.callbacks.append(callback)
    def disconnect(self, callback): self.callbacks.remove(callback)
    def emit(self, *values):
        for callback in tuple(self.callbacks): callback(*values)


class PftQtCore:
    def __init__(self):
        self.processes, self.timers = [], []
        self.application = object()
        self.QCoreApplication = types.SimpleNamespace(instance=lambda: self.application)
        owner = self
        class PftProcess:
            FailedToStart, NormalExit = 0, 0
            def __init__(self, parent):
                owner.processes.append(self)
                self.parent, self.killed, self.deleted = parent, 0, False
                self.cpp_deleted, self.delete_calls = False, 0
                self.stdout, self.stderr, self.writes = bytearray(), bytearray(), []
                for name in ('readyReadStandardOutput', 'readyReadStandardError', 'finished', 'errorOccurred', 'started', 'destroyed'):
                    setattr(self, name, PftSignal())
            def setProcessEnvironment(self, environment): self.environment = environment.values
            def setWorkingDirectory(self, cwd): self.cwd = cwd
            def start(self, program, args): self.program, self.args = program, args
            def live(self):
                if self.cpp_deleted: raise RuntimeError('Internal C++ object QProcess already deleted')
            def write(self, data): self.live(); self.writes.append(data); return len(data)
            def bytesToWrite(self): self.live(); return 0
            def kill(self): self.live(); self.killed += 1
            def deleteLater(self): self.live(); self.deleted = True; self.delete_calls += 1
            def readAllStandardOutput(self):
                self.live()
                result = bytes(self.stdout); self.stdout.clear(); return result
            def readAllStandardError(self):
                self.live()
                result = bytes(self.stderr); self.stderr.clear(); return result
            def output(self, data): self.stdout.extend(data); self.readyReadStandardOutput.emit()
            def diagnostic(self, data): self.stderr.extend(data); self.readyReadStandardError.emit()
        class PftTimer:
            def __init__(self, parent):
                self.parent, self.timeout, self.active, self.delay = parent, PftSignal(), False, None
                owner.timers.append(self)
            def setSingleShot(self, value): self.single_shot = value
            def start(self, milliseconds): self.active, self.delay = True, milliseconds
            def stop(self): self.active = False
            def expire(self):
                if self.active:
                    if self.single_shot: self.active = False
                    self.timeout.emit()
        class PftEnvironment:
            def __init__(self): self.values = {}
            def insert(self, key, value): self.values[key] = value
        self.QProcess, self.QTimer, self.QProcessEnvironment = PftProcess, PftTimer, PftEnvironment


class PftChatProcessTests(unittest.TestCase):
    def setUp(self):
        self.core, self.parent = PftQtCore(), object()
        self.transport = transport_module.PftChatProcess(self.core, self.parent)
        self.data, self.exits, self.errors = [], [], []
    def start(self):
        self.transport.start('/opt/example/codex', ['app-server', '--listen', 'stdio://'],
            {'CODEX_HOME': '/var/example/isolated', 'HOME': '/var/example/user',
             'NODE_OPTIONS': '--injected', 'OPENAI_API_KEY': 'never-inherit', 'HTTP_PROXY': 'never-inherit'},
            '/var/example/clean', self.data.append, self.exits.append, self.errors.append)
        return self.core.processes[-1]

    def test_start_uses_exact_program_args_clean_cwd_and_filtered_environment(self):
        process = self.start()
        self.assertEqual(process.program, '/opt/example/codex')
        self.assertEqual(process.args, ['app-server', '--listen', 'stdio://'])
        self.assertEqual(process.cwd, '/var/example/clean')
        self.assertEqual(process.environment['CODEX_HOME'], '/var/example/isolated')
        self.assertEqual(process.environment['OTEL_SDK_DISABLED'], 'true')
        self.assertIs(process.parent, self.core.application)
        self.assertIsNot(process.parent, self.parent)
        for key in ('NODE_OPTIONS', 'OPENAI_API_KEY', 'HTTP_PROXY'):
            self.assertNotIn(key, process.environment)
        self.assertEqual(self.data + self.errors + self.exits, [])

    def test_write_queues_until_started_and_utf8_jsonl_may_arrive_in_fragments(self):
        process = self.start()
        payload = b'{"id":1,"method":"initialize","params":{}}\n'
        self.transport.write(payload)
        self.assertEqual(process.writes, [])
        process.started.emit()
        self.assertEqual(process.writes, [payload])
        wire = '{"id":1,"result":{"text":"例"}}\n{"method":"fixture","params":{}}\n'.encode('utf-8')
        process.output(wire[:29])
        process.output(wire[29:31])
        process.output(wire[31:])
        self.assertEqual(self.data[0]['result']['text'], '例')
        self.assertEqual(len(self.data), 2)
        self.assertEqual(self.errors, [])

    def test_duplicate_keys_nonfinite_invalid_utf8_surrogates_and_arrays_are_rejected(self):
        for wire in (b'{"id":1,"id":2}\n', b'{"x":{"a":1,"a":2}}\n', b'{"x":NaN}\n',
                     b'{"x":1e9999}\n', b'{"x":"\xff"}\n', b'{"x":"\\ud800"}\n', b'[]\n', b'\n'):
            with self.subTest(wire=wire):
                core = PftQtCore()
                transport = transport_module.PftChatProcess(core, self.parent)
                errors, data = [], []
                transport.start('/opt/codex', [], {'CODEX_HOME': '/var/example/home'}, '/var/example/clean',
                                data.append, lambda _code: None, errors.append)
                core.processes[0].output(wire)
                self.assertEqual(data, [])
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0].code, 'CHAT_PROTOCOL_INVALID')
                self.assertEqual(core.processes[0].killed, 1)

    def test_output_stderr_and_pending_writes_have_independent_limits(self):
        process = self.start()
        process.output(b'x' * (1280 * 1024 + 1))
        self.assertEqual(self.errors[-1].code, 'CHAT_PROTOCOL_TOO_LARGE')
        process.finished.emit(1, 0)
        process = self.start()
        process.diagnostic(b'private-diagnostic' * 1024)
        self.assertEqual(self.errors[-1].code, 'CHAT_STDERR_TOO_LARGE')
        self.assertNotIn('private-diagnostic', self.errors[-1].message)
        process.finished.emit(1, 0)
        self.start()
        with self.assertRaises(transport_module.PftChatProcessError) as error:
            self.transport.write(b'x' * (512 * 1024 + 1))
        self.assertEqual(error.exception.code, 'CHAT_WRITE_TOO_LARGE')

    def test_large_synthetic_png_completed_event_arrives_in_fragments_and_next_message_is_readable(self):
        process = self.start()
        event = synthetic_cached_thumbnail_event()
        wire = (json.dumps(event, separators=(',', ':')) + '\n').encode('utf-8')
        self.assertGreater(len(wire), 512 * 1024)
        self.assertLess(len(wire), 1280 * 1024)
        self.assertLess(len(base64.b64decode(event['params']['item']['result']['content'][0]['data'])), 600 * 1024)
        for start in range(0, len(wire), 13753):
            process.output(wire[start:start + 13753])
        process.output(b'{"method":"fixture/after-image","params":{}}\n')
        self.assertEqual(self.data, [event, {'method': 'fixture/after-image', 'params': {}}])
        self.assertEqual(self.errors, [])
        self.assertEqual(process.killed, 0)
        process.finished.emit(0, 0)
        self.assertEqual(self.exits, [0])
        self.assertIsNone(self.transport._session)

    def test_exact_inbound_line_bound_is_accepted_but_one_more_byte_kills_and_releases_only_owned_child(self):
        self.assertEqual(transport_module._PFT_LINE_LIMIT, 1280 * 1024)
        prefix, suffix = b'{"value":"', b'"}\n'
        wire = prefix + b'x' * (1280 * 1024 - len(prefix) - len(suffix)) + suffix
        process = self.start()
        process.output(wire[:700000]); process.output(wire[700000:])
        self.assertEqual(len(self.data), 1)
        self.assertEqual(self.errors, [])
        process.finished.emit(0, 0)
        process = self.start()
        over = wire[:-3] + b'x' + wire[-3:]
        process.output(over[:700000]); process.output(over[700000:])
        self.assertEqual([error.code for error in self.errors], ['CHAT_PROTOCOL_TOO_LARGE'])
        self.assertEqual(process.killed, 1)
        self.assertEqual(len(self.data), 1)
        process.finished.emit(1, 0)
        self.assertTrue(process.deleted)
        self.assertIsNone(self.transport._session)
        new = self.start()
        process.output(b'{"old":true}\n')
        new.output(b'{"next":true}\n')
        self.assertEqual(self.data[-1], {'next': True})
        self.assertEqual(new.killed, 0)

    def test_write_bound_remains_512_kib_for_pending_started_and_already_queued_bytes(self):
        self.assertEqual(transport_module._PFT_WRITE_LIMIT, 512 * 1024)
        process = self.start()
        payload = b'x' * (512 * 1024)
        self.transport.write(payload)
        with self.assertRaises(transport_module.PftChatProcessError) as error:
            self.transport.write(b'x')
        self.assertEqual(error.exception.code, 'CHAT_WRITE_TOO_LARGE')
        process.started.emit()
        self.assertEqual(process.writes, [payload])
        with self.assertRaises(transport_module.PftChatProcessError) as error:
            self.transport.write(payload + b'x')
        self.assertEqual(error.exception.code, 'CHAT_WRITE_TOO_LARGE')
        process.bytesToWrite = lambda: 512 * 1024
        with self.assertRaises(transport_module.PftChatProcessError) as error:
            self.transport.write(b'x')
        self.assertEqual(error.exception.code, 'CHAT_WRITE_TOO_LARGE')
        self.assertEqual(process.writes, [payload])
        self.assertEqual(process.killed, 0)

    def test_timeout_only_kills_owned_child_and_late_signals_cannot_mutate_new_session(self):
        old = self.start()
        self.transport.arm_timeout(30000)
        self.assertTrue(self.core.timers[-1].single_shot)
        self.core.timers[-1].timeout.emit()
        self.assertEqual(self.errors[-1].code, 'CHAT_TIMEOUT')
        self.assertEqual(old.killed, 1)
        old.finished.emit(1, 0)
        new = self.start()
        before = len(self.errors)
        old.output(b'{"id":5,"result":{}}\n')
        old.errorOccurred.emit(1)
        self.assertEqual(len(self.errors), before)
        self.assertEqual(self.data, [])
        self.assertEqual(new.killed, 0)
        new.output(b'{"id":6,"result":{}}\n')
        self.assertEqual(self.data[-1]['id'], 6)

    def test_optional_metadata_timeout_keeps_child_and_later_rpc_working(self):
        process = self.start()
        process.started.emit()
        expired = []
        self.transport.arm_timeout(15, lambda: expired.append('metadata'))
        timer = self.core.timers[-1]
        queued = timer.timeout.callbacks[-1]
        timer.expire()
        queued()  # A duplicate queued delivery cannot run the same callback twice.
        timer.timeout.emit()
        self.assertEqual(expired, ['metadata'])
        self.assertEqual(self.errors, [])
        self.assertEqual(process.killed, 0)
        self.assertFalse(timer.active)
        payload = b'{"id":2,"method":"thread/start","params":{}}\n'
        self.transport.write(payload)
        process.output(b'{"id":2,"result":{"thread":{"id":"later"}}}\n')
        self.assertEqual(process.writes[-1], payload)
        self.assertEqual(self.data[-1]['result']['thread']['id'], 'later')
        process.finished.emit(0, 0)
        self.assertEqual(self.exits, [0])

    def test_clear_and_rearm_invalidate_already_queued_timeout_slots(self):
        process = self.start()
        expired = []
        timer = self.core.timers[-1]
        self.transport.arm_timeout(10, lambda: expired.append('cleared'))
        cleared = timer.timeout.callbacks[-1]
        self.transport.clear_timeout()
        cleared()
        timer.timeout.emit()
        self.assertFalse(timer.active)
        self.transport.arm_timeout(20, lambda: expired.append('superseded'))
        superseded = timer.timeout.callbacks[-1]
        self.transport.arm_timeout(30, lambda: expired.append('current'))
        superseded()
        self.assertTrue(timer.active)
        self.assertEqual(timer.delay, 30)
        self.assertEqual(expired, [])
        timer.expire()
        self.assertEqual(expired, ['current'])
        self.assertEqual(self.errors, [])
        self.assertEqual(process.killed, 0)

    def test_optional_callback_can_rearm_a_fatal_rpc_timeout(self):
        process = self.start()
        process.started.emit()
        def metadata_expired():
            self.transport.write(b'{"id":3,"method":"thread/start"}\n')
            self.transport.arm_timeout(50)
        self.transport.arm_timeout(10, metadata_expired)
        timer = self.core.timers[-1]
        queued = timer.timeout.callbacks[-1]
        timer.expire()
        queued()
        self.assertTrue(timer.active)
        self.assertEqual(timer.delay, 50)
        self.assertEqual(process.writes, [b'{"id":3,"method":"thread/start"}\n'])
        self.assertEqual(self.errors, [])
        timer.expire()
        self.assertEqual([error.code for error in self.errors], ['CHAT_TIMEOUT'])
        self.assertEqual(process.killed, 1)

    def test_optional_timeout_callback_failure_is_fatal_and_redacts_details(self):
        process = self.start()
        def broken(): raise RuntimeError('/private/callback-details')
        self.transport.arm_timeout(10, broken)
        timer = self.core.timers[-1]
        queued = timer.timeout.callbacks[-1]
        timer.expire()
        queued()
        self.assertEqual([error.code for error in self.errors], ['CHAT_TIMEOUT_CALLBACK_FAILED'])
        self.assertNotIn('/private', self.errors[0].message)
        self.assertEqual(process.killed, 1)
        with self.assertRaises(transport_module.PftChatProcessError):
            self.transport.write(b'{}\n')

    def test_stop_exit_and_destroy_invalidate_optional_timeout_and_old_session(self):
        for terminal in ('stop', 'exit', 'destroyed'):
            with self.subTest(terminal=terminal):
                core = PftQtCore()
                transport = transport_module.PftChatProcess(core, self.parent)
                expired, errors, exits = [], [], []
                transport.start('/opt/codex', [], {}, '/var/example/clean',
                                lambda _data: None, exits.append, errors.append)
                old = core.processes[-1]
                timer = core.timers[-1]
                transport.arm_timeout(10, lambda: expired.append('old'))
                queued = timer.timeout.callbacks[-1]
                if terminal == 'stop':
                    transport.stop()
                    queued()
                    self.assertEqual(old.killed, 1)
                    old.finished.emit(1, 0)
                elif terminal == 'exit':
                    old.finished.emit(0, 0)
                    self.assertEqual(exits, [0])
                else:
                    old.cpp_deleted = True
                    old.destroyed.emit(old)
                    self.assertEqual([error.code for error in errors], ['CHAT_PROCESS_FAILED'])
                self.assertFalse(timer.active)
                self.assertIsNone(transport._session)
                before = len(errors)
                transport.start('/opt/codex', [], {}, '/var/example/clean',
                                lambda _data: None, exits.append, errors.append)
                new = core.processes[-1]
                transport.arm_timeout(20, lambda: expired.append('new'))
                queued()
                timer.timeout.emit()
                self.assertEqual(expired, [])
                self.assertEqual(len(errors), before)
                self.assertEqual(new.killed, 0)
                core.timers[-1].expire()
                self.assertEqual(expired, ['new'])

    def test_finishing_child_cannot_arm_optional_timeout_from_final_stdout(self):
        expired = []
        def receive(value):
            self.data.append(value)
            self.transport.arm_timeout(10, lambda: expired.append('too-late'))
        self.transport.start('/opt/codex', [], {}, '/var/example/clean',
                             receive, self.exits.append, self.errors.append)
        process = self.core.processes[-1]
        timer = self.core.timers[-1]
        process.stdout.extend(b'{"id":1,"result":{}}\n')
        process.finished.emit(0, 0)
        timer.timeout.emit()
        self.assertEqual(len(self.data), 1)
        self.assertEqual(self.exits, [0])
        self.assertEqual(expired, [])
        self.assertFalse(timer.active)
        self.assertEqual(process.killed, 0)

    def test_stop_is_nonblocking_does_not_publish_stale_exit_and_releases_after_finished(self):
        process = self.start()
        self.transport.stop()
        self.assertEqual(process.killed, 1)
        with self.assertRaises(transport_module.PftChatProcessError): self.start()
        process.finished.emit(0, 0)
        self.assertEqual(self.exits, [])
        self.assertTrue(process.deleted)
        self.start()
        self.assertEqual(len(self.core.processes), 2)

    def test_failed_to_start_and_partial_final_line_report_once(self):
        process = self.start()
        process.errorOccurred.emit(self.core.QProcess.FailedToStart)
        process.finished.emit(1, 0)
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self.exits, [])
        process = self.start()
        process.output(b'{"unfinished":')
        process.finished.emit(0, 0)
        self.assertEqual(self.errors[-1].code, 'CHAT_PROTOCOL_INVALID')
        self.assertEqual(self.exits, [])

    def test_error_callback_may_stop_owned_child_and_stop_is_idempotent(self):
        self.transport.start('/opt/codex', [], {'CODEX_HOME': '/var/example/home'}, '/var/example/clean',
                             self.data.append, self.exits.append, lambda error: (self.errors.append(error), self.transport.stop()))
        process = self.core.processes[-1]
        process.output(b'invalid-json\n')
        self.transport.stop()
        self.assertEqual(process.killed, 1)
        self.assertEqual(len(self.errors), 1)
        process.finished.emit(1, 0)
        self.assertIsNone(self.transport._session)

    def test_started_write_failure_is_reported_and_burst_output_is_bounded(self):
        process = self.start()
        self.transport.write(b'{"id":1}\n')
        def fail(_data): raise RuntimeError('/private/exception-details')
        process.write = fail
        process.started.emit()
        self.assertEqual(self.errors[-1].code, 'CHAT_WRITE_FAILED')
        self.assertNotIn('/private', self.errors[-1].message)
        process.finished.emit(1, 0)
        process = self.start()
        notification = b'{"notification":true}\n'
        process.output(notification * ((2 * 1280 * 1024) // len(notification) + 1))
        self.assertEqual(self.errors[-1].code, 'CHAT_PROTOCOL_TOO_LARGE')
        self.assertEqual(self.data, [])

    def test_transient_dock_deletion_does_not_destroy_application_owned_process(self):
        dock = types.SimpleNamespace(deleted=False)
        self.transport = transport_module.PftChatProcess(self.core, dock)
        process = self.start()
        dock.deleted = True
        # Simulate QObject parent destruction: only its own children die.
        for child in self.core.processes:
            if child.parent is dock: child.cpp_deleted = True
        self.transport.stop()
        self.assertFalse(process.cpp_deleted)
        self.assertEqual(process.killed, 1)
        self.assertIs(self.transport._session['process'], process)
        self.assertFalse(process.deleted)
        process.finished.emit(0, 0)
        self.assertIsNone(self.transport._session)
        self.assertEqual(process.delete_calls, 1)

    def test_no_application_falls_back_to_unparented_process_never_the_dock(self):
        del self.core.QCoreApplication
        process = self.start()
        self.assertIsNone(process.parent)
        self.transport.stop()
        process.finished.emit(0, 0)

    def test_duplicate_finished_is_single_terminal_callback_and_cleanup(self):
        process = self.start()
        process.finished.emit(0, 0)
        process.finished.emit(0, 0)
        self.assertEqual(self.exits, [0])
        self.assertEqual(process.delete_calls, 1)
        self.assertEqual(self.errors, [])

    def test_deleted_cpp_stdout_stderr_and_finished_callbacks_are_safe(self):
        for event in ('readyReadStandardOutput', 'readyReadStandardError', 'finished'):
            with self.subTest(event=event):
                core = PftQtCore()
                transport = transport_module.PftChatProcess(core, self.parent)
                errors, exits = [], []
                transport.start('/opt/codex', [], {}, '/var/example/clean', lambda _data: None, exits.append, errors.append)
                process = core.processes[-1]
                process.cpp_deleted = True
                signal = getattr(process, event)
                signal.emit(0, 0) if event == 'finished' else signal.emit()
                process.finished.emit(0, 0)
                process.readyReadStandardError.emit()
                self.assertIsNone(transport._session)
                self.assertEqual(len(errors), 1)
                self.assertEqual(exits, [])

    def test_reentrant_stop_while_finishing_never_reads_deleted_stderr(self):
        def receive(value):
            self.data.append(value)
            self.transport.stop()
            process.cpp_deleted = True
        self.transport.start('/opt/codex', [], {}, '/var/example/clean', receive, self.exits.append, self.errors.append)
        process = self.core.processes[-1]
        process.stdout.extend(b'{"id":1,"result":{}}\n')
        process.finished.emit(0, 0)
        self.assertEqual(len(self.data), 1)
        self.assertEqual(self.exits, [])
        self.assertIsNone(self.transport._session)

    def test_deleted_cpp_write_and_kill_never_escape_raw_runtimeerror(self):
        for operation in ('write', 'stop'):
            with self.subTest(operation=operation):
                core = PftQtCore()
                transport = transport_module.PftChatProcess(core, self.parent)
                errors = []
                transport.start('/opt/codex', [], {}, '/var/example/clean', lambda _data: None, lambda _code: None, errors.append)
                process = core.processes[-1]
                process.started.emit()
                process.cpp_deleted = True
                if operation == 'write':
                    with self.assertRaises(transport_module.PftChatProcessError) as caught:
                        transport.write(b'{}\n')
                    self.assertEqual(caught.exception.code, 'CHAT_WRITE_FAILED')
                else:
                    transport.stop()
                    transport.stop()
                self.assertIsNone(transport._session)

    def test_deleted_timer_is_safe_and_arm_failure_still_kills_owned_process(self):
        process = self.start()
        def deleted(*_args): raise RuntimeError('Internal C++ timer already deleted')
        timer = self.core.timers[-1]
        timer.start, timer.stop = deleted, deleted
        self.transport.clear_timeout()
        self.transport.arm_timeout(10)
        self.assertEqual(process.killed, 1)
        self.assertIsNotNone(self.transport._session)
        process.finished.emit(1, 0)
        self.assertIsNone(self.transport._session)

    def test_late_duplicate_finish_and_timer_cannot_touch_new_session(self):
        old = self.start()
        old_timer = self.core.timers[-1]
        self.transport.stop()
        old.finished.emit(0, 0)
        new = self.start()
        old.cpp_deleted = True
        old.finished.emit(0, 0)
        old.errorOccurred.emit(1)
        old_timer.timeout.emit()
        self.assertIs(self.transport._session['process'], new)
        self.assertEqual(new.killed, 0)
        self.assertFalse(new.deleted)
        self.assertEqual(self.errors, [])


if __name__ == '__main__': unittest.main()
