# SPDX-License-Identifier: Apache-2.0
"""Qt transport tests with an owned child stub; no native process is started."""
import base64
import binascii
from enum import IntEnum
import gc
import importlib.util
import json
import struct
import zlib
from pathlib import Path
import types
import unittest
from unittest.mock import patch

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
    def __init__(self): self.callbacks, self.connect_calls, self.disconnect_calls = [], 0, 0
    def connect(self, callback):
        self.connect_calls += 1; self.callbacks.append(callback); return True
    def disconnect(self, callback):
        self.disconnect_calls += 1; self.callbacks.remove(callback)
    def emit(self, *values):
        for callback in tuple(self.callbacks):
            receiver = getattr(callback, '__self__', None)
            if isinstance(receiver, PftQObject) and receiver.cpp_deleted:
                continue
            callback(*values)

    def discard_generic_callable_contexts(self):
        self.callbacks[:] = [callback for callback in self.callbacks
            if isinstance(getattr(callback, '__self__', None), PftQObject)
            and hasattr(callback, '_pft_slot_types')]


class PftQObject:
    def __init__(self, parent=None):
        self.parent, self.children = parent, []
        self.cpp_deleted, self.deleted = False, False
        self.destroyed = PftSignal()
        if isinstance(parent, PftQObject):
            parent.children.append(self)

    def destroy(self):
        if self.cpp_deleted:
            return
        self.cpp_deleted = True
        self.destroyed.emit(self)
        for child in tuple(self.children):
            child.destroy()


def pft_slot(*types):
    def decorate(callback):
        callback._pft_slot_types = types
        return callback
    return decorate


class PftQtCore:
    now = 100.0

    def __init__(self):
        self.processes, self.timers = [], []
        self.application = PftQObject()
        self.QCoreApplication = types.SimpleNamespace(instance=lambda: self.application)
        self.QObject, self.Slot = PftQObject, pft_slot
        owner = self
        class PftProcess(PftQObject):
            class ProcessError(IntEnum):
                FailedToStart, Crashed = 0, 1
            class ExitStatus(IntEnum):
                NormalExit, CrashExit = 0, 1
            FailedToStart, NormalExit = 0, 0
            NotRunning, Starting, Running = 0, 1, 2
            ProcessState = types.SimpleNamespace(NotRunning=0, Starting=1, Running=2)
            def __init__(self, parent):
                super().__init__(parent)
                owner.processes.append(self)
                self.parent, self.killed, self.deleted = parent, 0, False
                self.cpp_deleted, self.delete_calls = False, 0
                self.state_value = self.Starting
                self.pid = 1000 + len(owner.processes)
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
            def state(self): self.live(); return self.state_value
            def processId(self): self.live(); return self.pid
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
        class PftTimer(PftQObject):
            def __init__(self, parent):
                super().__init__(parent)
                self.parent, self.timeout, self.active, self.delay = parent, PftSignal(), False, None
                owner.timers.append(self)
            def setSingleShot(self, value): self.single_shot = value
            def start(self, milliseconds):
                self.active, self.delay = True, milliseconds
                self.deadline = PftQtCore.now + milliseconds / 1000
            def stop(self): self.active = False
            def expire(self):
                if self.active:
                    PftQtCore.now = max(PftQtCore.now, self.deadline)
                    if self.single_shot: self.active = False
                    self.timeout.emit()
        class PftEnvironment:
            def __init__(self): self.values = {}
            def insert(self, key, value): self.values[key] = value
        self.QProcess, self.QTimer, self.QProcessEnvironment = PftProcess, PftTimer, PftEnvironment

    def discard_generic_callable_contexts(self):
        for process in self.processes:
            for name in ('readyReadStandardOutput', 'readyReadStandardError', 'finished',
                         'errorOccurred', 'started', 'destroyed'):
                getattr(process, name).discard_generic_callable_contexts()
        for timer in self.timers:
            timer.timeout.discard_generic_callable_contexts()


class PftChatProcessTests(unittest.TestCase):
    def setUp(self):
        PftQtCore.now = 100.0
        self.clock_patch = patch.object(transport_module, "time", types.SimpleNamespace(monotonic=lambda: PftQtCore.now), create=True)
        self.clock_patch.start(); self.addCleanup(self.clock_patch.stop)
        self.core, self.parent = PftQtCore(), object()
        self.transport = transport_module.PftChatProcess(self.core, self.parent)
        self.data, self.exits, self.errors = [], [], []
    def start(self):
        self.transport.start('/opt/example/codex', ['app-server', '--listen', 'stdio://'],
            {'CODEX_HOME': '/var/example/isolated', 'HOME': '/var/example/user',
             'NODE_OPTIONS': '--injected', 'OPENAI_API_KEY': 'never-inherit', 'HTTP_PROXY': 'http://127.0.0.1:18888'},
            '/var/example/clean', self.data.append, self.exits.append, self.errors.append)
        return self.core.processes[-1]

    def test_session_flag_stays_true_until_owned_wrapper_is_released(self):
        self.assertIs(self.transport.has_active_session, False)
        process = self.start()
        self.assertIs(self.transport.has_active_session, True)
        self.transport.stop()
        self.assertIs(self.transport.has_active_session, True)
        process.finished.emit(0, 0)
        self.assertIs(self.transport.has_active_session, False)

    def test_new_start_releases_only_failed_not_running_owned_session(self):
        old = self.start()
        old.started.emit()
        timer = self.core.timers[-1]
        self.transport.arm_timeout(10)
        queued_timeout = timer.timeout.callbacks[-1]
        self.transport.stop()
        self.assertEqual(old.killed, 1)
        self.assertIsNotNone(self.transport._session)
        old.state_value = self.core.QProcess.NotRunning
        new = self.start()
        self.assertEqual(old.delete_calls, 1)
        self.assertIs(self.transport._session['process'], new)
        self.assertEqual(new.killed, 0)
        old.finished.emit(0, 0)
        old.finished.emit(0, 0)
        old.errorOccurred.emit(1)
        queued_timeout()
        timer.timeout.emit()
        self.assertEqual(old.delete_calls, 1)
        self.assertEqual(self.exits, [])
        self.assertEqual(self.errors, [])
        self.assertIs(self.transport._session['process'], new)

    def test_scoped_qt_not_running_enum_releases_failed_owned_session_without_kill(self):
        old = self.start()
        self.transport._session["failed"] = True
        old.state_value = self.core.QProcess.ProcessState.NotRunning
        del self.core.QProcess.NotRunning
        new = self.start()
        self.assertEqual(old.killed, 0)
        self.assertEqual(old.delete_calls, 1)
        self.assertIs(self.transport._session["process"], new)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.exits, [])

    def test_busy_or_unknown_owned_process_is_never_released_or_killed_again(self):
        for state in (self.core.QProcess.Starting, self.core.QProcess.Running, None, 'unknown'):
            with self.subTest(state=state):
                self.setUp()
                old = self.start()
                self.transport.stop()
                old.state_value = state
                with self.assertRaises(transport_module.PftChatProcessError) as failure:
                    self.start()
                self.assertEqual(failure.exception.code, 'CHAT_PROCESS_BUSY')
                self.assertEqual(old.killed, 1)
                self.assertEqual(old.delete_calls, 0)
                self.assertEqual(len(self.core.processes), 1)
        self.setUp()
        old = self.start()
        old.state_value = self.core.QProcess.NotRunning
        with self.assertRaises(transport_module.PftChatProcessError): self.start()
        self.assertEqual(old.killed, 0)
        self.assertEqual(old.delete_calls, 0)
        self.transport.stop()
        def unknown_state(): raise RuntimeError('Neutral unavailable process state')
        old.state = unknown_state
        with self.assertRaises(transport_module.PftChatProcessError): self.start()
        self.assertEqual(old.killed, 1)
        self.assertEqual(old.delete_calls, 0)

    def test_disconnected_controller_can_explicitly_connect_after_stopped_session(self):
        controller_spec = importlib.util.spec_from_file_location('neutral_process_controller', ADDON / 'pft_chat_controller.py')
        controller_module = importlib.util.module_from_spec(controller_spec)
        controller_spec.loader.exec_module(controller_module)
        events = []
        controller = controller_module.PftChatController(self.transport, events.append, lambda _url: True, network_environment=lambda: {})
        values = ('/opt/neutral/codex', '/var/neutral/chat-home', '/var/neutral/chat-work')
        controller.connect(*values)
        old = self.core.processes[-1]
        controller.disconnect()
        old.state_value = self.core.QProcess.NotRunning
        controller.connect(*values)
        self.assertEqual(controller.snapshot['phase'], 'connecting')
        self.assertIsNone(controller.snapshot['error'])
        self.assertEqual(len(self.core.processes), 2)
        self.assertEqual(old.killed, 1)
        self.assertEqual(old.delete_calls, 1)
        self.assertEqual(self.core.processes[-1].killed, 0)
        self.assertNotIn('account/login/start', repr(self.core.processes[-1].writes))

    def test_start_uses_exact_program_args_clean_cwd_and_filtered_environment(self):
        process = self.start()
        self.assertEqual(process.program, '/opt/example/codex')
        self.assertEqual(process.args, ['app-server', '--listen', 'stdio://'])
        self.assertEqual(process.cwd, '/var/example/clean')
        self.assertEqual(process.environment['CODEX_HOME'], '/var/example/isolated')
        self.assertEqual(process.environment['OTEL_SDK_DISABLED'], 'true')
        self.assertIs(process.parent, self.core.application)
        self.assertIsNot(process.parent, self.parent)
        self.assertEqual(process.environment['HTTP_PROXY'], 'http://127.0.0.1:18888')
        for key in ('NODE_OPTIONS', 'OPENAI_API_KEY'):
            self.assertNotIn(key, process.environment)
        self.assertEqual(self.data + self.errors + self.exits, [])

    def test_each_native_signal_has_one_process_child_receiver_and_exact_slot_signature(self):
        process = self.start()
        receiver = self.transport._session['receiver']
        self.assertIsInstance(receiver, self.core.QObject)
        self.assertIs(receiver.parent, process)
        bindings = ((process.readyReadStandardOutput, ()),
                    (process.readyReadStandardError, ()),
                    (process.finished, (int, self.core.QProcess.ExitStatus)),
                    (process.errorOccurred, (self.core.QProcess.ProcessError,)),
                    (process.started, ()), (process.destroyed, (self.core.QObject,)),
                    (self.core.timers[-1].timeout, ()))
        for signal, signature in bindings:
            self.assertEqual(signal.connect_calls, 1)
            self.assertEqual(len(signal.callbacks), 1)
            callback = signal.callbacks[0]
            self.assertIs(callback.__self__, receiver)
            self.assertEqual(callback._pft_slot_types, signature)
        self.assertEqual(len(self.transport._session['signal_connections']), 7)
        self.assertIs(self.transport._session['timeout_handler'], self.core.timers[-1].timeout.callbacks[0])

    def test_data_stderr_and_normal_exit_survive_generic_context_retirement_and_gc(self):
        process = self.start()
        process.started.emit()
        process.output(b'{"id":1,"result":{"neutral_echo":"before"}}\n')
        self.core.discard_generic_callable_contexts()
        gc.collect()
        process.output(b'{"id":2,"result":{"neutral_echo":"after"}}\n')
        process.diagnostic(b'neutral diagnostic')
        self.assertEqual(self.transport._session['stderr'], len(b'neutral diagnostic'))
        process.finished.emit(0, self.core.QProcess.ExitStatus.NormalExit)
        self.assertEqual([row['id'] for row in self.data], [1, 2])
        self.assertEqual(self.exits, [0])
        self.assertEqual(self.errors, [])
        self.assertFalse(self.transport.has_active_session)
        self.assertEqual(process.delete_calls, 1)

    def test_registered_error_and_stable_timeouts_survive_generic_context_retirement(self):
        for event in ('error', 'fatal_timeout', 'optional_timeout'):
            with self.subTest(event=event):
                self.setUp()
                process = self.start()
                expired = []
                if event == 'optional_timeout':
                    self.transport.arm_timeout(10, lambda: expired.append(True))
                else:
                    self.transport.arm_timeout(10)
                self.core.discard_generic_callable_contexts()
                gc.collect()
                if event == 'error':
                    process.state_value = self.core.QProcess.NotRunning
                    process.errorOccurred.emit(self.core.QProcess.ProcessError.FailedToStart)
                    self.assertEqual([e.code for e in self.errors], ['CHAT_PROCESS_FAILED'])
                    self.assertFalse(self.transport.has_active_session)
                else:
                    self.core.timers[-1].expire()
                    self.assertEqual(expired, [True] if event == 'optional_timeout' else [])
                    self.assertEqual([e.code for e in self.errors], [] if event == 'optional_timeout' else ['CHAT_TIMEOUT'])
                    self.assertEqual(process.killed, 0 if event == 'optional_timeout' else 1)
                    process.finished.emit(0, self.core.QProcess.ExitStatus.NormalExit)
                    self.assertEqual(self.exits, [0] if event == 'optional_timeout' else [])
                    self.assertFalse(self.transport.has_active_session)

    def test_retired_receiver_slots_cannot_touch_replacement_session(self):
        old = self.start()
        receiver = self.transport._session['receiver']
        old.finished.emit(0, self.core.QProcess.ExitStatus.NormalExit)
        self.assertEqual(receiver._callbacks, {})
        new = self.start()
        receiver.read_stdout(); receiver.read_stderr(); receiver.process_started()
        receiver.process_error(self.core.QProcess.ProcessError.Crashed)
        receiver.process_finished(1, self.core.QProcess.ExitStatus.CrashExit)
        receiver.process_destroyed(old); receiver.timed_out()
        self.assertEqual(self.exits, [0])
        self.assertEqual(self.errors, [])
        self.assertIs(self.transport._session['process'], new)
        self.assertEqual(new.killed, 0)

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
        self.core.timers[-1].expire()
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
        self.assertGreaterEqual(timer.delay, 30)
        self.assertLessEqual(timer.delay, 31)
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
        self.assertGreaterEqual(timer.delay, 50)
        self.assertLessEqual(timer.delay, 51)
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
        process.state_value = self.core.QProcess.NotRunning
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


    def test_timeout_slot_is_connected_once_and_clear_rearm_never_disconnects_it(self):
        self.start()
        timer = self.core.timers[-1]
        self.assertEqual(timer.timeout.connect_calls, 1)
        handler = self.transport._session['timeout_handler']
        for delay in (10, 20, 30, 40):
            self.transport.arm_timeout(delay)
            self.assertIs(self.transport._session['timeout_handler'], handler)
            self.transport.clear_timeout()
            self.assertIs(self.transport._session['timeout_handler'], handler)
        self.assertEqual(timer.timeout.connect_calls, 1)
        self.assertEqual(timer.timeout.disconnect_calls, 0)
        self.assertEqual(len(timer.timeout.callbacks), 1)

    def test_early_or_queued_old_signal_uses_current_absolute_deadline_without_renewal(self):
        process = self.start()
        self.transport.arm_timeout(10)
        old_delivery = self.transport._session['timeout_handler']
        PftQtCore.now = 100.005
        self.transport.arm_timeout(100)
        deadline = self.transport._session['timeout_deadline']
        PftQtCore.now = 100.020  # The old deadline passed; the new one has not.
        old_delivery()
        self.assertEqual(self.transport._session['timeout_deadline'], deadline)
        self.assertEqual(self.errors, [])
        self.assertEqual(process.killed, 0)
        self.assertTrue(self.core.timers[-1].active)
        PftQtCore.now = deadline + 0.001
        old_delivery()
        old_delivery()
        self.assertEqual([error.code for error in self.errors], ['CHAT_TIMEOUT'])
        self.assertEqual(process.killed, 1)

    def test_cleared_stable_slot_ignores_late_signal_even_after_old_deadline(self):
        process = self.start()
        self.transport.arm_timeout(10)
        handler = self.transport._session['timeout_handler']
        self.transport.clear_timeout()
        PftQtCore.now = 200.0
        handler()
        self.assertIsNone(self.transport._session['timeout_deadline'])
        self.assertEqual(self.errors, [])
        self.assertEqual(process.killed, 0)
        self.assertFalse(self.core.timers[-1].active)

    def test_timeout_connection_failure_is_fatal_before_starting_the_owned_stub(self):
        with patch.object(PftSignal, 'connect', return_value=False):
            process = self.start()
        self.assertEqual([error.code for error in self.errors], ['CHAT_PROCESS_FAILED'])
        self.assertEqual(process.killed, 0)
        self.assertIsNone(self.transport._session)
        self.assertFalse(hasattr(process, 'program'))

    def test_standard_proxy_environment_is_explicit_and_never_admits_other_tokens(self):
        keys = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')
        environment = {key: 'http://127.0.0.1:18888' for key in keys}
        environment.update(NODE_OPTIONS='--synthetic-injection', OPENAI_API_KEY='not-a-real-key',
                           ANTHROPIC_API_KEY='not-a-real-key', SSH_AUTH_SOCK='/synthetic/agent')
        self.transport.start('/opt/example/codex', [], environment, '/var/example/clean',
                             self.data.append, self.exits.append, self.errors.append)
        process = self.core.processes[-1]
        for key in keys: self.assertEqual(process.environment[key], environment[key])
        for key in ('NODE_OPTIONS', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'SSH_AUTH_SOCK'):
            self.assertNotIn(key, process.environment)


class PftFakeProcessLease:
    """A retained ownership gate, with no filesystem or native process access."""
    def __init__(self):
        self.started_pids, self.releases = [], []
        self.released = False
        self.mark_error = self.release_error = None

    def mark_started(self, pid):
        self.started_pids.append(pid)
        if self.mark_error:
            raise self.mark_error

    def _release(self, kind):
        self.releases.append(kind)
        if self.release_error:
            raise self.release_error
        self.released = True

    def release_after_exit(self): self._release('exit')
    def release_failed_to_start(self): self._release('never_started')


class PftFakeProcessLeaseFactory:
    def __init__(self):
        self.calls, self.leases = [], []
        self.next_mark_error = self.next_release_error = None

    def __call__(self, home, cwd, program):
        self.calls.append((home, cwd, program))
        if any(not lease.released for lease in self.leases):
            raise transport_module.PftChatProcessError('CHAT_PROCESS_LEASE_BUSY', 'Synthetic owned lease remains held.')
        lease = PftFakeProcessLease()
        lease.mark_error, lease.release_error = self.next_mark_error, self.next_release_error
        self.leases.append(lease)
        return lease


class PftChatProcessLeaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.core, self.parent = PftQtCore(), object()
        self.factory = PftFakeProcessLeaseFactory()
        self.transport = transport_module.PftChatProcess(self.core, self.parent, self.factory)
        self.data, self.exits, self.errors = [], [], []

    def start(self, on_data=None, on_exit=None, on_error=None):
        self.transport.start('/opt/example/codex', ['app-server', '--listen', 'stdio://'],
            {'CODEX_HOME': '/var/example/isolated'}, '/var/example/clean',
            self.data.append if on_data is None else on_data,
            self.exits.append if on_exit is None else on_exit,
            self.errors.append if on_error is None else on_error)
        return self.core.processes[-1]

    def test_qt_construction_failure_releases_never_started_and_redacts_exception(self):
        with patch.object(self.core, 'QProcess', side_effect=RuntimeError('synthetic-private-constructor')):
            with self.assertRaises(transport_module.PftChatProcessError) as caught:
                self.start()
        self.assertEqual(caught.exception.code, 'CHAT_PROCESS_FAILED')
        self.assertNotIn('synthetic-private', caught.exception.message)
        self.assertEqual(self.factory.leases[0].releases, ['never_started'])
        self.assertIsNone(self.transport._session)

    def test_qt_construction_cleanup_failure_retains_gate_and_redacts_exception(self):
        self.factory.next_release_error = RuntimeError('synthetic-private-cleanup')
        with patch.object(self.core, 'QProcess', side_effect=RuntimeError('synthetic-private-constructor')):
            with self.assertRaises(transport_module.PftChatProcessError) as caught:
                self.start()
        self.assertEqual(caught.exception.code, 'CHAT_PROCESS_LEASE_RELEASE_UNVERIFIED')
        self.assertNotIn('synthetic-private', caught.exception.message)
        self.assertFalse(self.factory.leases[0].released)
        with self.assertRaises(transport_module.PftChatProcessError):
            self.start()
        self.assertEqual(self.core.processes, [])

    def test_acquire_uses_exact_three_paths_and_collision_never_creates_another_child(self):
        process = self.start()
        self.assertEqual(self.factory.calls, [('/var/example/isolated', '/var/example/clean', '/opt/example/codex')])
        self.assertEqual(self.factory.leases[0].releases, [])
        other = transport_module.PftChatProcess(self.core, self.parent, self.factory)
        with self.assertRaises(transport_module.PftChatProcessError) as caught:
            other.start('/opt/example/codex', [], {'CODEX_HOME': '/var/example/isolated'},
                        '/var/example/clean', self.data.append, self.exits.append, self.errors.append)
        self.assertEqual(caught.exception.code, 'CHAT_PROCESS_LEASE_BUSY')
        self.assertEqual(len(self.core.processes), 1)
        self.assertEqual(process.killed, 0)

    def test_immediate_stop_then_late_started_records_pid_without_flushing_and_kills_running_child_once(self):
        process = self.start()
        lease = self.factory.leases[0]
        self.transport.write(b'{"id":1,"method":"initialize"}\n')
        self.transport.stop()
        self.assertEqual(process.killed, 1)
        self.assertEqual(lease.releases, [])
        process.state_value = self.core.QProcess.Running
        process.started.emit()
        self.assertEqual(lease.started_pids, [process.pid])
        self.assertEqual(process.writes, [])
        self.assertEqual(process.killed, 2)
        process.started.emit()
        self.transport.stop()
        self.assertEqual(lease.started_pids, [process.pid])
        self.assertEqual(process.killed, 2)
        self.assertEqual(lease.releases, [])
        process.finished.emit(0, 0)
        process.finished.emit(0, 0)
        self.assertEqual(lease.releases, ['exit'])
        self.assertEqual(self.data + self.exits + self.errors, [])

    def test_mark_started_failure_keeps_lease_until_real_exit_and_never_flushes(self):
        self.factory.next_mark_error = RuntimeError('synthetic-private-start-record')
        process = self.start()
        lease = self.factory.leases[0]
        self.transport.write(b'{"id":1}\n')
        process.started.emit()
        self.assertEqual([error.code for error in self.errors], ['CHAT_PROCESS_LEASE_UPDATE_UNVERIFIED'])
        self.assertNotIn('synthetic-private', self.errors[0].message)
        self.assertEqual(lease.releases, [])
        self.assertEqual(process.writes, [])
        self.assertEqual(process.killed, 1)
        with self.assertRaises(transport_module.PftChatProcessError): self.start()
        process.finished.emit(1, 0)
        self.assertEqual(lease.releases, ['exit'])
        self.assertEqual(self.exits, [])

    def test_native_failed_to_start_releases_only_when_not_running_and_no_started_was_observed(self):
        for state in (PftQtCore().QProcess.NotRunning, PftQtCore().QProcess.Starting, None):
            with self.subTest(state=state):
                self.setUp()
                process = self.start()
                lease = self.factory.leases[0]
                process.state_value = state
                process.errorOccurred.emit(self.core.QProcess.FailedToStart)
                expected = ['never_started'] if state == self.core.QProcess.NotRunning else []
                self.assertEqual(lease.releases, expected)
                self.assertEqual(len(self.errors), 1)
                process.finished.emit(1, 0)
                self.assertEqual(lease.releases, expected or ['exit'])
                process.errorOccurred.emit(self.core.QProcess.FailedToStart)
                self.assertEqual(len(lease.releases), 1)

    def test_observed_started_then_failed_to_start_does_not_claim_never_started(self):
        process = self.start()
        lease = self.factory.leases[0]
        process.started.emit()
        process.state_value = self.core.QProcess.NotRunning
        process.errorOccurred.emit(self.core.QProcess.FailedToStart)
        self.assertEqual(lease.releases, [])
        process.finished.emit(1, 0)
        self.assertEqual(lease.releases, ['exit'])

    def test_exception_after_invoking_start_never_manufactures_failed_to_start_evidence(self):
        for observed_started in (False, True):
            with self.subTest(observed_started=observed_started):
                self.setUp()
                def raise_after_invocation(process, program, args):
                    process.program, process.args = program, args
                    process.state_value = self.core.QProcess.Running
                    if observed_started:
                        process.started.emit()
                    raise RuntimeError('synthetic-private-start-exception')
                with patch.object(self.core.QProcess, 'start', raise_after_invocation):
                    process = self.start()
                lease = self.factory.leases[0]
                self.assertEqual(lease.releases, [])
                self.assertEqual([error.code for error in self.errors], ['CHAT_PROCESS_FAILED'])
                self.assertNotIn('synthetic-private', self.errors[0].message)
                self.assertEqual(process.killed, 1)
                self.assertIsNotNone(self.transport._session)
                process.finished.emit(1, 0)
                self.assertEqual(lease.releases, ['exit'])
                self.assertEqual(self.exits, [])

    def test_prestart_configuration_failure_releases_never_started_once_without_kill(self):
        with patch.object(PftSignal, 'connect', return_value=False):
            process = self.start()
        lease = self.factory.leases[0]
        self.assertEqual(lease.releases, ['never_started'])
        self.assertEqual(process.killed, 0)
        self.assertFalse(hasattr(process, 'program'))
        self.assertIsNone(self.transport._session)
        self.assertEqual([error.code for error in self.errors], ['CHAT_PROCESS_FAILED'])

    def test_missing_qobject_slot_or_enum_fails_before_start_without_functor_fallback(self):
        for scope, attribute in (('core', 'QObject'), ('core', 'Slot'),
                                 ('process', 'ExitStatus'), ('process', 'ProcessError')):
            with self.subTest(attribute=attribute):
                self.setUp()
                delattr(self.core if scope == 'core' else self.core.QProcess, attribute)
                process = self.start()
                self.assertEqual(self.factory.leases[0].releases, ['never_started'])
                self.assertEqual([error.code for error in self.errors], ['CHAT_PROCESS_FAILED'])
                self.assertFalse(hasattr(process, 'program'))
                self.assertEqual(process.killed, 0)
                self.assertFalse(self.transport.has_active_session)

    def test_late_started_after_context_retirement_keeps_owned_lease_and_does_not_flush(self):
        process = self.start()
        owned_lease = self.factory.leases[0]
        self.transport.write(b'{"id":1,"method":"initialize"}\n')
        self.transport.stop()
        self.core.discard_generic_callable_contexts()
        gc.collect()
        process.started.emit()
        self.assertEqual(owned_lease.started_pids, [process.pid])
        self.assertEqual(process.writes, [])
        self.assertEqual(process.killed, 2)
        self.assertEqual(owned_lease.releases, [])
        process.finished.emit(0, self.core.QProcess.ExitStatus.NormalExit)
        self.assertEqual(owned_lease.releases, ['exit'])
        self.assertEqual(self.data + self.errors + self.exits, [])
        self.assertFalse(self.transport.has_active_session)

    def test_application_destruction_reaches_receiver_before_children_without_faking_exit(self):
        observed = []
        receiver = None
        def on_error(error):
            self.errors.append(error)
            observed.append(receiver.cpp_deleted)
        process = self.start(on_error=on_error)
        process.started.emit()
        receiver = self.transport._session['receiver']
        self.core.discard_generic_callable_contexts()
        self.core.application.destroy()
        self.assertEqual(observed, [False])
        self.assertTrue(receiver.cpp_deleted)
        self.assertEqual([error.code for error in self.errors], ['CHAT_PROCESS_FAILED'])
        self.assertFalse(self.transport.has_active_session)
        self.assertEqual(self.exits, [])
        self.assertEqual(self.factory.leases[0].releases, [])
        self.assertEqual(process.killed, 0)
        with self.assertRaises(transport_module.PftChatProcessError) as caught:
            self.start()
        self.assertEqual(caught.exception.code, 'CHAT_PROCESS_LEASE_BUSY')

    def test_qobject_disappearance_cleans_wrapper_but_keeps_lease_as_a_gate(self):
        process = self.start()
        lease = self.factory.leases[0]
        process.started.emit()
        process.cpp_deleted = True
        process.destroyed.emit()
        self.assertIsNone(self.transport._session)
        self.assertEqual(lease.releases, [])
        self.assertEqual(len(self.errors), 1)
        with self.assertRaises(transport_module.PftChatProcessError) as caught: self.start()
        self.assertEqual(caught.exception.code, 'CHAT_PROCESS_LEASE_BUSY')
        self.assertEqual(len(self.core.processes), 1)
        self.assertEqual(lease.releases, [])

    def test_confirmed_not_running_releases_old_lease_before_new_start_and_late_callbacks_are_inert(self):
        old = self.start()
        old_lease = self.factory.leases[0]
        old.started.emit()
        timer = self.core.timers[-1]
        self.transport.stop()
        old.state_value = self.core.QProcess.NotRunning
        new = self.start()
        new_lease = self.factory.leases[1]
        self.assertEqual(old_lease.releases, ['exit'])
        self.assertEqual(new_lease.releases, [])
        old.finished.emit(0, 0)
        old.errorOccurred.emit(self.core.QProcess.FailedToStart)
        old.started.emit()
        old.destroyed.emit()
        timer.timeout.emit()
        self.assertEqual(old_lease.releases, ['exit'])
        self.assertEqual(new_lease.started_pids, [])
        self.assertEqual(new_lease.releases, [])
        self.assertEqual(new.killed, 0)
        self.assertIs(self.transport._session['process'], new)

    def test_lease_release_failure_is_sanitized_once_and_retained_gate_blocks_reconnect(self):
        self.factory.next_release_error = RuntimeError('synthetic-private-release-error')
        process = self.start()
        lease = self.factory.leases[0]
        process.started.emit()
        process.finished.emit(0, 0)
        process.finished.emit(0, 0)
        self.assertEqual(lease.releases, ['exit'])
        self.assertFalse(lease.released)
        self.assertEqual([error.code for error in self.errors], ['CHAT_PROCESS_LEASE_RELEASE_UNVERIFIED'])
        self.assertNotIn('synthetic-private', self.errors[0].message)
        self.assertEqual(self.exits, [])
        self.assertIsNone(self.transport._session)
        with self.assertRaises(transport_module.PftChatProcessError) as caught: self.start()
        self.assertEqual(caught.exception.code, 'CHAT_PROCESS_LEASE_BUSY')
        self.assertEqual(len(self.core.processes), 1)

    def test_error_callback_reconnects_only_after_confirmed_old_exit_and_old_finally_cannot_kill_new_child(self):
        def on_error(error):
            self.errors.append(error)
            self.start()
        old = self.start(on_error=on_error)
        old_lease = self.factory.leases[0]
        old.started.emit()
        old.state_value = self.core.QProcess.NotRunning
        old.output(b'not-json\n')
        new = self.core.processes[-1]
        self.assertIsNot(new, old)
        self.assertEqual(old_lease.releases, ['exit'])
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(new.killed, 0)
        self.assertEqual(self.factory.leases[1].releases, [])
        old.finished.emit(1, 0)
        self.assertEqual(self.factory.leases[1].releases, [])

    def test_exit_callback_can_start_new_connection_without_old_duplicate_exit_touching_lease(self):
        def on_exit(code):
            self.exits.append(code)
            self.start()
        old = self.start(on_exit=on_exit)
        old_lease = self.factory.leases[0]
        old.started.emit()
        old.finished.emit(0, 0)
        new = self.core.processes[-1]
        self.assertIsNot(new, old)
        self.assertEqual(old_lease.releases, ['exit'])
        self.assertEqual(self.exits, [0])
        old.finished.emit(0, 0)
        old.destroyed.emit()
        self.assertEqual(self.exits, [0])
        self.assertEqual(self.factory.leases[1].releases, [])
        self.assertEqual(new.killed, 0)

    def test_real_exit_evidence_survives_trailing_stdout_callback_stop_and_qobject_deletion(self):
        process = None
        def on_data(value):
            self.data.append(value)
            self.transport.stop()
            process.cpp_deleted = True
            process.destroyed.emit()
        process = self.start(on_data=on_data)
        lease = self.factory.leases[0]
        process.started.emit()
        process.stdout.extend(b'{"id":1,"result":{}}\n')
        process.finished.emit(0, 0)
        self.assertEqual(self.data, [{'id': 1, 'result': {}}])
        self.assertEqual(lease.releases, ['exit'])
        self.assertIsNone(self.transport._session)
        self.assertEqual(process.killed, 0)
        self.assertEqual(self.exits, [])

    def test_duplicate_started_same_pid_is_idempotent_but_changed_pid_fails_without_releasing(self):
        process = self.start()
        lease = self.factory.leases[0]
        process.started.emit()
        process.started.emit()
        self.assertEqual(lease.started_pids, [process.pid])
        process.pid += 1
        process.started.emit()
        self.assertEqual([error.code for error in self.errors], ['CHAT_PROCESS_LEASE_UPDATE_UNVERIFIED'])
        self.assertEqual(lease.releases, [])
        self.assertEqual(process.killed, 1)
        process.finished.emit(1, 0)
        self.assertEqual(lease.releases, ['exit'])


if __name__ == '__main__': unittest.main()
