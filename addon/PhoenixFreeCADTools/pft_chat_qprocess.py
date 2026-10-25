# SPDX-License-Identifier: Apache-2.0
"""Qt transport: an owned Codex child with bounded, strict JSONL stdio."""

import json
import math

# Inbound: one 1 MiB MCP result plus 256 KiB for app-server JSONL wrapping.
_PFT_LINE_LIMIT = 1280 * 1024
# Outbound ordinary requests retain their independent pre-existing bound.
_PFT_WRITE_LIMIT = 512 * 1024
_PFT_STDERR_LIMIT = 16 * 1024
_PFT_ENVIRONMENT = frozenset(("PATH", "HOME", "USERPROFILE", "SystemRoot", "SYSTEMROOT",
                            "WINDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR",
                            "TEMP", "TMP", "CODEX_HOME", "OTEL_SDK_DISABLED", "DO_NOT_TRACK"))


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
    def __init__(self, core, parent):
        self.core, self.parent = core, parent
        self._session = None

    def start(self, program, args, env, cwd, on_data, on_exit, on_error):
        if self._session is not None:
            raise PftChatProcessError("CHAT_PROCESS_BUSY", "聊天进程尚未退出，请稍后重试。")
        core = self.core
        application = getattr(core, "QCoreApplication", None)
        try:
            owner = application.instance() if application is not None else None
        except RuntimeError:
            owner = None
        # Never parent the child to the transient panel, even without an app.
        process = core.QProcess(owner)
        timer = core.QTimer(process)
        timer.setSingleShot(True)
        session = {"process": process, "timer": timer, "stdout": bytearray(), "stderr": 0,
                   "pending": bytearray(), "started": False, "failed": False, "killed": False,
                   "finished": False, "finishing": False, "object_gone": False,
                   "timeout_generation": 0, "timeout_callback": None, "timeout_handler": None}
        self._session = session

        def current():
            return self._session is session and not session["finished"]

        def stop_timer():
            session["timeout_generation"] += 1
            session["timeout_callback"] = None
            handler, session["timeout_handler"] = session["timeout_handler"], None
            try:
                timer.stop()
            except RuntimeError:
                pass
            if handler is not None:
                try:
                    timer.timeout.disconnect(handler)
                except (RuntimeError, TypeError):
                    pass

        def release():
            if session["finished"]:
                return
            session["finished"] = True
            stop_timer()
            session["pending"].clear()
            session["stdout"].clear()
            if self._session is session:
                self._session = None
            try:
                process.deleteLater()
            except RuntimeError:
                pass

        def kill_owned():
            if session["finished"] or session["killed"]:
                return
            session["killed"] = True
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
        session["stop_timer"] = stop_timer

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
            if error == core.QProcess.FailedToStart:
                release()  # FailedToStart is not guaranteed to emit finished.

        def started():
            if current() and not session["failed"]:
                session["started"] = True
                pending = bytes(session["pending"])
                session["pending"].clear()
                try:
                    accepted = process.write(pending) if pending else 0
                except RuntimeError:
                    object_gone("CHAT_WRITE_FAILED", "无法发送聊天消息，连接已停止。")
                    return
                if accepted != len(pending):
                    failed("CHAT_WRITE_FAILED", "无法发送聊天消息，连接已停止。")

        try:
            process.readyReadStandardOutput.connect(drain_stdout)
            process.readyReadStandardError.connect(drain_stderr)
            process.finished.connect(finished)
            process.errorOccurred.connect(process_error)
            process.started.connect(started)
            destroyed = getattr(process, "destroyed", None)
            if destroyed is not None:
                destroyed.connect(lambda *_args: object_gone())
            environment = core.QProcessEnvironment()
            for key, value in env.items():
                if key in _PFT_ENVIRONMENT and isinstance(value, str):
                    environment.insert(key, value)
            environment.insert("OTEL_SDK_DISABLED", "true")
            environment.insert("DO_NOT_TRACK", "1")
            process.setProcessEnvironment(environment)
            process.setWorkingDirectory(cwd)
            process.start(program, list(args))
        except Exception:
            process_error(core.QProcess.FailedToStart)

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
        """Bound an operation; optional metadata may supply a nonfatal callback."""
        session = self._session
        if (session is None or session["failed"] or session["finished"]
                or session["finishing"] or session["object_gone"] or session["killed"]):
            return
        session["stop_timer"]()
        generation = session["timeout_generation"]
        session["timeout_callback"] = on_timeout

        def timed_out():
            if (self._session is not session or session["timeout_generation"] != generation
                    or session["failed"] or session["finished"] or session["finishing"]
                    or session["object_gone"] or session["killed"]):
                return
            callback = session["timeout_callback"]
            session["stop_timer"]()
            if callback is None:
                session["fail"]("CHAT_TIMEOUT", "聊天操作超时，已停止本次专属连接；不会自动重试。")
                return
            try:
                callback()
            except Exception:
                session["fail"]("CHAT_TIMEOUT_CALLBACK_FAILED", "聊天超时处理失败，连接已停止；不会自动重试。")

        session["timeout_handler"] = timed_out
        try:
            session["timer"].timeout.connect(timed_out)
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
