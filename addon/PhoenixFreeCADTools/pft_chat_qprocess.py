# SPDX-License-Identifier: Apache-2.0
"""Qt transport: an owned Codex child with bounded, strict JSONL stdio."""

import json
import math
import time

# Inbound: one 1 MiB MCP result plus 256 KiB for app-server JSONL wrapping.
_PFT_LINE_LIMIT = 1280 * 1024
# Outbound ordinary requests retain their independent pre-existing bound.
_PFT_WRITE_LIMIT = 512 * 1024
_PFT_STDERR_LIMIT = 16 * 1024
_PFT_ENVIRONMENT = frozenset(("PATH", "HOME", "USERPROFILE", "SystemRoot", "SYSTEMROOT",
                            "WINDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR",
                            "TEMP", "TMP", "CODEX_HOME", "OTEL_SDK_DISABLED", "DO_NOT_TRACK",
                            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                            "http_proxy", "https_proxy", "all_proxy", "no_proxy"))


class PftChatProcessError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("non-finite JSON number")


def _validate_json(value, depth=0, budget=None):
    if budget is None:
        budget = [50000]
    budget[0] -= 1
    if depth > 64 or budget[0] < 0:
        raise ValueError("JSON nesting or item limit")
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_json(key, depth + 1, budget)
            _validate_json(item, depth + 1, budget)
    elif isinstance(value, list):
        for item in value:
            _validate_json(item, depth + 1, budget)


def _decode_line(raw):
    value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_pairs,
                       parse_constant=_constant)
    if not isinstance(value, dict):
        raise ValueError("JSON-RPC must be an object")
    _validate_json(value)
    return value



class PftChatProcess:
    """Own one application-parented child until its terminal signal is handled.

    A dock can be deleted as soon as its closeEvent returns. The process must
    outlive that dock so asynchronous kill/finished delivery remains safe.
    """
    def __init__(self, core, parent, lease_factory=None):
        self.core, self.parent = core, parent
        self._lease_factory = lease_factory
        self._session = None

    @property
    def has_active_session(self):
        """A wrapper is still owned; inspecting this flag never probes a PID."""
        return self._session is not None

    def _release_stopped_session(self):
        session = self._session
        if (session is None or session["finished"] or session["finishing"] or
                not (session["failed"] or session["killed"])):
            return
        # A killed child can reach NotRunning without a delivered finished.
        # Only an explicit new start may retire that exact owned wrapper.
        process_class = self.core.QProcess
        not_running = getattr(process_class, "NotRunning", None)
        if not_running is None:
            not_running = getattr(getattr(process_class, "ProcessState", None), "NotRunning", None)
        try:
            state = session["process"].state()
        except (AttributeError, RuntimeError, TypeError):
            return
        if not_running is not None and type(state) is not bool and state == not_running:
            session["confirm_exit"]()
            session["release"]()

    def start(self, program, args, env, cwd, on_data, on_exit, on_error):
        self._release_stopped_session()
        if self._session is not None:
            raise PftChatProcessError("CHAT_PROCESS_BUSY", "聊天进程尚未退出，请稍后重试。")
        # Acquire before creating or starting a child. The native view supplies
        # the production factory; isolated protocol tests may omit file I/O.
        lease = self._lease_factory(env.get("CODEX_HOME"), cwd, program) if self._lease_factory is not None else None
        core = self.core
        application = getattr(core, "QCoreApplication", None)
        try:
            owner = application.instance() if application is not None else None
        except RuntimeError:
            owner = None
        # Never parent the child to the transient panel, even without an app.
        try:
            process = core.QProcess(owner)
            timer = core.QTimer(process)
            timer.setSingleShot(True)
        except Exception:
            # No call to QProcess.start has occurred. There cannot be a child.
            if lease is not None:
                try:
                    lease.release_failed_to_start()
                except Exception:
                    raise PftChatProcessError("CHAT_PROCESS_LEASE_RELEASE_UNVERIFIED",
                        "聊天进程登记清理未确认；请核对专属配置后再连接。") from None
            raise PftChatProcessError("CHAT_PROCESS_FAILED",
                "无法运行聊天服务；请检查专属配置与 Codex 执行文件。") from None
        session = {"process": process, "timer": timer, "stdout": bytearray(), "stderr": 0,
                   "pending": bytearray(), "started": False, "failed": False, "killed": False,
                   "finished": False, "finishing": False, "object_gone": False,
                   "lease": lease, "lease_release_attempted": False, "spawn_invoked": False,
                   "exit_confirmed": False, "pid": None, "running_kill_sent": False,
                   "timeout_generation": 0, "timeout_deadline": None, "timeout_callback": None,
                   "timeout_handler": None, "timeout_connection": None,
                   "receiver": None, "signal_connections": []}
        self._session = session

        def current():
            return self._session is session and not session["finished"]

        def stop_timer():
            session["timeout_generation"] += 1
            session["timeout_callback"] = session["timeout_deadline"] = None
            try:
                timer.stop()
            except RuntimeError:
                pass

        def confirm_exit(never_started=False):
            # Native exit evidence belongs to this captured session, even when
            # a trailing data/error callback closes the dock or reconnects.
            session["exit_confirmed"] = True
            if lease is None or session["lease_release_attempted"]:
                return
            session["lease_release_attempted"] = True
            try:
                if never_started:
                    lease.release_failed_to_start()
                else:
                    lease.release_after_exit()
            except Exception:
                session["failed"] = True
                try:
                    on_error(PftChatProcessError("CHAT_PROCESS_LEASE_RELEASE_UNVERIFIED",
                        "聊天进程登记清理未确认；请核对专属配置后再连接。"))
                except Exception:
                    pass

        def release():
            if session["finished"]:
                return
            session["finished"] = True
            stop_timer()
            session["pending"].clear()
            session["stdout"].clear()
            receiver = session["receiver"]
            if receiver is not None:
                receiver.invalidate()
            if self._session is session:
                self._session = None
            try:
                process.deleteLater()
            except RuntimeError:
                pass

        def kill_owned():
            if session["finished"] or session["exit_confirmed"]:
                return
            # A kill requested during Starting may precede a late started
            # signal. Send once more to that same now-running owned handle.
            if session["killed"] and (not session["started"] or session["running_kill_sent"]):
                return
            session["killed"] = True
            if session["started"]:
                session["running_kill_sent"] = True
            if session["object_gone"]:
                release()
                return
            try:
                process.kill()
            except RuntimeError:
                session["object_gone"] = True
                release()

        def failed(code, message):
            if not current() or session["failed"]:
                return
            session["failed"] = True
            stop_timer()
            try:
                on_error(PftChatProcessError(code, message))
            except Exception:
                # A callback may belong to an already deleted panel.
                pass
            finally:
                kill_owned()

        def object_gone(code="CHAT_PROCESS_FAILED", message="无法运行聊天服务；请检查专属配置与 Codex 执行文件。"):
            if not current():
                return
            session["object_gone"] = True
            failed(code, message)
            release()  # A deleted QObject cannot deliver another finished.

        session["kill_owned"], session["object_gone_callback"], session["fail"] = kill_owned, object_gone, failed
        session["stop_timer"], session["release"], session["confirm_exit"] = stop_timer, release, confirm_exit

        def drain_stdout():
            if not current() or session["failed"]:
                return
            try:
                raw = bytes(process.readAllStandardOutput())
            except RuntimeError:
                object_gone()
                return
            if len(raw) > 2 * _PFT_LINE_LIMIT:
                failed("CHAT_PROTOCOL_TOO_LARGE", "聊天服务消息超过限制，连接已停止。")
                return
            for part in raw.splitlines(keepends=True):
                session["stdout"].extend(part)
                if len(session["stdout"]) > _PFT_LINE_LIMIT:
                    failed("CHAT_PROTOCOL_TOO_LARGE", "聊天服务消息超过限制，连接已停止。")
                    return
                if not session["stdout"].endswith(b"\n"):
                    continue
                line = bytes(session["stdout"][:-1])
                session["stdout"].clear()
                try:
                    value = _decode_line(line)
                    on_data(value)
                except Exception:
                    failed("CHAT_PROTOCOL_INVALID", "聊天服务返回无效消息，连接已停止。")
                    return
                if not current() or session["failed"]:
                    return

        def drain_stderr():
            if not current() or session["failed"]:
                return
            try:
                session["stderr"] += len(bytes(process.readAllStandardError()))
            except RuntimeError:
                object_gone()
                return
            if session["stderr"] > _PFT_STDERR_LIMIT:
                failed("CHAT_STDERR_TOO_LARGE", "聊天服务诊断输出超过限制，连接已停止。")

        def finished(code, _status):
            if session["finished"] or session["finishing"]:
                return
            session["finishing"] = True
            confirm_exit()
            active = current()
            if active:
                drain_stdout()
                drain_stderr()
                if session["stdout"] and not session["failed"]:
                    failed("CHAT_PROTOCOL_INVALID", "聊天服务消息不完整，连接已停止。")
            notify_exit = active and not session["failed"]
            release()
            if notify_exit:
                try:
                    on_exit(code)
                except Exception:
                    pass

        def process_error(error):
            if not current():
                return
            failed("CHAT_PROCESS_FAILED", "无法运行聊天服务；请检查专属配置与 Codex 执行文件。")
            if error == core.QProcess.FailedToStart and not session["started"]:
                # Only a native signal plus NotRunning proves a failed launch.
                try:
                    state = process.state()
                    not_running = getattr(core.QProcess, "NotRunning", None)
                    if not_running is None:
                        not_running = core.QProcess.ProcessState.NotRunning
                    if type(state) is not bool and state == not_running:
                        confirm_exit(never_started=True)
                        release()  # FailedToStart may never emit finished.
                except (AttributeError, RuntimeError, TypeError):
                    pass

        def started():
            if not current() or session["finishing"]:
                return
            was_started = session["started"]
            session["started"] = True
            if lease is not None:
                try:
                    pid = process.processId()
                    if type(pid) is not int or pid <= 0 or (was_started and pid != session["pid"]):
                        raise ValueError("unconfirmed owned child PID")
                    if not was_started:
                        session["pid"] = pid
                        lease.mark_started(pid)
                except Exception:
                    failed("CHAT_PROCESS_LEASE_UPDATE_UNVERIFIED",
                        "聊天进程登记未确认，连接已停止；不会自动重试。")
                    kill_owned()
                    return
            if session["failed"] or session["killed"]:
                kill_owned()
                return
            if not was_started:
                pending = bytes(session["pending"])
                session["pending"].clear()
                try:
                    accepted = process.write(pending) if pending else 0
                except RuntimeError:
                    object_gone("CHAT_WRITE_FAILED", "无法发送聊天消息，连接已停止。")
                    return
                if accepted != len(pending):
                    failed("CHAT_WRITE_FAILED", "无法发送聊天消息，连接已停止。")

        def timed_out():
            if (not current() or session["failed"] or session["finishing"]
                    or session["object_gone"] or session["killed"]):
                return
            deadline = session["timeout_deadline"]
            if deadline is None:
                return
            remaining = deadline - time.monotonic()
            if remaining > 0:
                # A queued signal from an earlier arm (or an early coarse Qt
                # timer) cannot expire the current operation or renew its budget.
                try:
                    timer.start(max(1, math.ceil(remaining * 1000)))
                except RuntimeError:
                    failed("CHAT_PROCESS_FAILED", "无法运行聊天服务；请检查专属配置与 Codex 执行文件。")
                return
            callback = session["timeout_callback"]
            stop_timer()  # Consume first; an optional callback may arm anew.
            if callback is None:
                failed("CHAT_TIMEOUT", "聊天操作超时，已停止本次专属连接；不会自动重试。")
                return
            try:
                callback()
            except Exception:
                failed("CHAT_TIMEOUT_CALLBACK_FAILED", "聊天超时处理失败，连接已停止；不会自动重试。")

        try:
            class _PftChatProcessReceiver(core.QObject):
                """Keep Qt's signal context on the owned process, not a dialog."""
                def __init__(self, parent, callbacks):
                    super().__init__(parent)
                    self._callbacks = callbacks

                def invalidate(self):
                    self._callbacks = {}

                def _dispatch(self, name, *args):
                    callback = self._callbacks.get(name)
                    if callback is not None:
                        callback(*args)

                @core.Slot()
                def read_stdout(self):
                    self._dispatch("stdout")

                @core.Slot()
                def read_stderr(self):
                    self._dispatch("stderr")

                @core.Slot(int, core.QProcess.ExitStatus)
                def process_finished(self, code, status):
                    self._dispatch("finished", code, status)

                @core.Slot(core.QProcess.ProcessError)
                def process_error(self, error):
                    self._dispatch("error", error)

                @core.Slot()
                def process_started(self):
                    self._dispatch("started")

                @core.Slot(core.QObject)
                def process_destroyed(self, _object=None):
                    # QObject emits destroyed before deleting its children, so
                    # this process-owned receiver still handles that boundary.
                    self._dispatch("destroyed")

                @core.Slot()
                def timed_out(self):
                    self._dispatch("timeout")

            receiver = _PftChatProcessReceiver(process, {
                "stdout": drain_stdout, "stderr": drain_stderr, "finished": finished,
                "error": process_error, "started": started, "destroyed": object_gone,
                "timeout": timed_out})
            session["receiver"] = receiver
            # One stable QObject slot per signal for this exact session. Arms
            # update only deadline/state, never the connection or its context.
            session["timeout_handler"] = receiver.timed_out
            for signal, slot in ((timer.timeout, session["timeout_handler"]),
                                 (process.readyReadStandardOutput, receiver.read_stdout),
                                 (process.readyReadStandardError, receiver.read_stderr),
                                 (process.finished, receiver.process_finished),
                                 (process.errorOccurred, receiver.process_error),
                                 (process.started, receiver.process_started),
                                 (process.destroyed, receiver.process_destroyed)):
                connection = signal.connect(slot)
                if not connection:
                    raise RuntimeError("process signal connection unavailable")
                session["signal_connections"].append(connection)
            session["timeout_connection"] = session["signal_connections"][0]
            environment = core.QProcessEnvironment()
            for key, value in env.items():
                if key in _PFT_ENVIRONMENT and isinstance(value, str):
                    environment.insert(key, value)
            environment.insert("OTEL_SDK_DISABLED", "true")
            environment.insert("DO_NOT_TRACK", "1")
            process.setProcessEnvironment(environment)
            process.setWorkingDirectory(cwd)
            session["spawn_invoked"] = True
            process.start(program, list(args))
        except Exception:
            # An exception after invoking start may follow an actual spawn.
            # Never manufacture FailedToStart evidence for that path.
            if not session["spawn_invoked"]:
                confirm_exit(never_started=True)
            failed("CHAT_PROCESS_FAILED", "无法运行聊天服务；请检查专属配置与 Codex 执行文件。")
            if not session["spawn_invoked"]:
                release()

    def write(self, data):
        session = self._session
        if session is None or session["failed"] or not isinstance(data, bytes):
            raise PftChatProcessError("CHAT_NOT_CONNECTED", "聊天连接尚未就绪。")
        process = session["process"]
        try:
            queued = process.bytesToWrite() if hasattr(process, "bytesToWrite") else 0
            if len(data) + len(session["pending"]) + queued > _PFT_WRITE_LIMIT:
                raise PftChatProcessError("CHAT_WRITE_TOO_LARGE", "待发送聊天消息超过限制。")
            if not session["started"]:
                session["pending"].extend(data)
            elif process.write(data) != len(data):
                raise PftChatProcessError("CHAT_WRITE_FAILED", "无法发送聊天消息。")
        except RuntimeError as error:
            if isinstance(error, PftChatProcessError):
                raise
            session["object_gone_callback"]("CHAT_WRITE_FAILED", "无法发送聊天消息。")
            raise PftChatProcessError("CHAT_WRITE_FAILED", "无法发送聊天消息。") from None

    def arm_timeout(self, milliseconds, on_timeout=None):
        """Arm the session's stable slot; metadata may use a nonfatal callback."""
        session = self._session
        if (session is None or session["failed"] or session["finished"]
                or session["finishing"] or session["object_gone"] or session["killed"]):
            return
        if type(milliseconds) is not int or not 1 <= milliseconds <= 2147483647:
            session["fail"]("CHAT_PROCESS_FAILED", "无法运行聊天服务；请检查专属配置与 Codex 执行文件。")
            return
        session["stop_timer"]()
        session["timeout_callback"] = on_timeout
        session["timeout_deadline"] = time.monotonic() + milliseconds / 1000
        try:
            session["timer"].start(milliseconds)
        except RuntimeError:
            session["fail"]("CHAT_PROCESS_FAILED", "无法运行聊天服务；请检查专属配置与 Codex 执行文件。")

    def clear_timeout(self):
        if self._session is not None:
            self._session["stop_timer"]()

    def stop(self):
        session = self._session
        if session is None or session["finished"]:
            return
        session["failed"] = True
        self.clear_timeout()
        # Keep the wrapper/session alive until this exact child's finished.
        session["kill_owned"]()
