# SPDX-License-Identifier: Apache-2.0
"""One owned, isolated PDF helper; no CAD, browser or service ownership."""
import hashlib
import os
from pathlib import Path
import stat
import sys
import unicodedata

from pft_pdf_contract import (PftPdfError, MAX_RESPONSE_BYTES, encode_pdf_request,
                              decode_pdf_response)


def _absolute(value):
    return (type(value) is str and 0 < len(value) <= 4096 and os.path.isabs(value)
            and not any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value))


def resolve_pdf_runtime(application_home, *, platform=None, python_version=None):
    """Find an existing Python within the running application's resources.

    Pass FreeCAD's App.getHomePath(), not App.getResourceDir(): some bundles put
    shared resources in a subdirectory of the application's runtime prefix.
    No PATH search, installation, subprocess, user preference or network access.
    The returned paths stay private process configuration, never tool results.
    """
    if (sys.platform if platform is None else platform) != "darwin":
        raise PftPdfError("PDF_BACKEND_UNAVAILABLE")
    if not _absolute(application_home):
        raise PftPdfError("PDF_RUNTIME_UNAVAILABLE")
    try:
        root = Path(application_home).resolve(strict=True)
        if not root.is_dir():
            raise ValueError()
        version = sys.version_info[:2] if python_version is None else python_version
        if (type(version) not in (tuple, list) or len(version) != 2
                or any(type(x) is not int or not 0 <= x <= 99 for x in version)):
            raise ValueError()
        helper = Path(__file__).resolve().with_name("pft_pdf_helper.py")
        if not helper.is_file() or helper.is_symlink():
            raise ValueError()
        for name in ("python%d.%d" % tuple(version), "python3", "python"):
            candidate = root / "bin" / name
            try:
                program = candidate.resolve(strict=True)
                program.relative_to(root)
                if stat.S_ISREG(program.stat().st_mode) and os.access(program, os.X_OK):
                    return {"program": str(program), "helper": str(helper)}
            except (OSError, ValueError):
                continue
    except (OSError, ValueError):
        pass
    raise PftPdfError("PDF_RUNTIME_UNAVAILABLE")


def _runtime(value):
    if (type(value) is not dict or set(value) != {"program", "helper"}
            or not all(_absolute(x) for x in value.values())):
        raise PftPdfError("PDF_RUNTIME_UNAVAILABLE")
    try:
        program, helper = Path(value["program"]), Path(value["helper"])
        if (program.resolve(strict=True) != program or not program.is_file()
                or not os.access(program, os.X_OK)
                or helper != Path(__file__).resolve().with_name("pft_pdf_helper.py")
                or not helper.is_file() or helper.is_symlink()):
            raise ValueError()
    except (OSError, ValueError):
        raise PftPdfError("PDF_RUNTIME_UNAVAILABLE") from None
    return str(program), str(helper)


def _enum(owner, group, name):
    value = getattr(owner, name, None)
    return value if value is not None else getattr(getattr(owner, group, None), name, None)


class PftPdfPreviewProcess:
    """Single flight. Cancellation waits for the exact owned child to finish.

    Application parenting lets the transport outlive a deleted preview widget.
    Terminal callbacks run once, after transport retirement; callers bind their
    own UI generations and never retain paths or raw diagnostics in status.
    """
    def __init__(self, core, parent=None, *, timeout_ms=15000):
        if type(timeout_ms) is not int or not 100 <= timeout_ms <= 60000:
            raise PftPdfError("PDF_PROTOCOL_INVALID")
        self.core, self.parent, self.timeout_ms = core, parent, timeout_ms
        self._session = None

    @property
    def has_active_session(self):
        return self._session is not None

    def start(self, runtime, pdf_bytes, width, height, completed, failed):
        previous = self._session
        if previous is not None and previous["failure"] is not None:
            # A caller may explicitly try again after an authoritative stopped
            # state, even if an earlier finished signal has not been delivered.
            previous["retire_if_stopped"]()
        if self._session is not None:
            raise PftPdfError("PDF_BUSY")
        program, helper = _runtime(runtime)
        request = encode_pdf_request(pdf_bytes, width, height)
        expected = dict(input_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
                        byte_count=len(pdf_bytes), width=width, height=height)
        if not callable(completed) or not callable(failed):
            raise PftPdfError("PDF_PROTOCOL_INVALID")
        core = self.core
        process = None
        try:
            application = getattr(core, "QCoreApplication", None)
            owner = application.instance() if application is not None else None
            process = core.QProcess(owner)
            timer = core.QTimer(process)
            timer.setSingleShot(True)
        except Exception:
            if process is not None:
                try:
                    process.deleteLater()
                except Exception:
                    pass  # No child has been started at this point.
            raise PftPdfError("PDF_PROCESS_FAILED") from None
        session = {"process": process, "timer": timer, "stdout": bytearray(),
                   "stderr": 0, "request": request, "offset": 0, "closed_input": False,
                   "writing": False, "started": False, "killed": False,
                   "running_kill_sent": False, "done": False, "failure": None,
                   "spawn_invoked": False}
        self._session = session

        def current():
            return self._session is session and not session["done"]

        def finish(code=None, status=None, *, gone=False):
            if not current():
                return
            session["done"] = True
            try:
                timer.stop()
            except RuntimeError:
                pass
            error, response = session["failure"], None
            if error is None:
                if gone or code != 0 or status != _enum(core.QProcess, "ExitStatus", "NormalExit"):
                    error = PftPdfError("PDF_PROCESS_FAILED")
                else:
                    try:
                        response = decode_pdf_response(bytes(session["stdout"]), **expected)
                        if response.get("ok") is not True:
                            error = PftPdfError(response["code"])
                    except PftPdfError as problem:
                        error = problem
                    except Exception:
                        error = PftPdfError("PDF_PROTOCOL_INVALID")
            session["request"] = b""
            session["stdout"].clear()
            self._session = None
            if not gone:
                try:
                    process.deleteLater()
                except RuntimeError:
                    pass
            try:
                failed(error) if error is not None else completed(response)
            except Exception:
                pass  # A terminal callback may belong to an already closed UI.

        def retire_if_stopped():
            if not current():
                return True
            try:
                stopped = _enum(core.QProcess, "ProcessState", "NotRunning")
                if stopped is not None and process.state() == stopped:
                    finish()
                    return True
            except Exception:
                pass  # Unknown state cannot authorize replacement of a child.
            return False

        session["retire_if_stopped"] = retire_if_stopped

        def kill_owned():
            if current() and retire_if_stopped():
                return
            if not current() or (session["killed"] and (not session["started"] or session["running_kill_sent"])):
                return
            session["killed"] = True
            if session["started"]:
                session["running_kill_sent"] = True
            try:
                process.kill()
            except Exception:
                # QObject.destroyed, finished, or a later authoritative stopped
                # state owns retirement. A failed kill is not proof of exit.
                retire_if_stopped()

        def fail(code):
            if not current() or session["failure"] is not None:
                return
            session["failure"] = PftPdfError(code)
            session["request"] = b""
            try:
                timer.stop()
            except RuntimeError:
                pass
            kill_owned()

        session["fail"] = fail

        def drain(channel):
            if not current():
                return
            try:
                data = bytes(process.readAllStandardOutput() if channel == "stdout" else process.readAllStandardError())
            except Exception:
                fail("PDF_PROCESS_FAILED")
                return
            if channel == "stderr":
                session["stderr"] += len(data)  # Never expose or retain stderr text.
                if session["stderr"] > 16 * 1024:
                    fail("PDF_OUTPUT_TOO_LARGE")
            elif session["failure"] is None:
                room = MAX_RESPONSE_BYTES - len(session["stdout"])
                session["stdout"].extend(data[:max(0, room)])
                if len(data) > room:
                    fail("PDF_OUTPUT_TOO_LARGE")

        def write_request(*_args):
            if not current() or session["failure"] is not None or session["writing"] or session["closed_input"]:
                return
            session["writing"] = True
            try:
                while current() and session["failure"] is None and session["offset"] < len(session["request"]):
                    pending = process.bytesToWrite()
                    if type(pending) is not int or pending < 0:
                        fail("PDF_WRITE_FAILED")
                        return
                    if pending > 64 * 1024:
                        return
                    chunk = session["request"][session["offset"]:session["offset"] + 64 * 1024]
                    written = process.write(chunk)
                    if type(written) is not int or not 0 < written <= len(chunk):
                        fail("PDF_WRITE_FAILED")
                        return
                    session["offset"] += written
                if current() and session["failure"] is None and session["offset"] == len(session["request"]):
                    session["closed_input"] = True
                    process.closeWriteChannel()  # Qt flushes queued bytes before EOF.
                    session["request"] = b""
            except Exception:
                fail("PDF_WRITE_FAILED")
            finally:
                session["writing"] = False

        def started():
            if current():
                session["started"] = True
                if session["failure"] is not None:
                    kill_owned()
                else:
                    write_request()

        def exited(code, status):
            if current():
                drain("stdout")
                drain("stderr")
                finish(code, status)

        def process_error(error):
            if current():
                if error == _enum(core.QProcess, "ProcessError", "FailedToStart"):
                    session["failure"] = session["failure"] or PftPdfError("PDF_PROCESS_FAILED")
                    finish()  # FailedToStart is terminal; the QObject still lives.
                else:
                    fail("PDF_PROCESS_FAILED")

        try:
            process.started.connect(started)
            process.bytesWritten.connect(write_request)
            process.readyReadStandardOutput.connect(lambda: drain("stdout"))
            process.readyReadStandardError.connect(lambda: drain("stderr"))
            process.finished.connect(exited)
            process.errorOccurred.connect(process_error)
            process.destroyed.connect(lambda *_args: finish(gone=True))
            timer.timeout.connect(lambda: fail("PDF_TIMEOUT"))
            environment = core.QProcessEnvironment()
            for key, value in (("PATH", "/usr/bin:/bin"), ("LANG", "C"), ("LC_ALL", "C")):
                environment.insert(key, value)
            process.setProcessEnvironment(environment)
            process.setWorkingDirectory(str(Path(helper).parent))
            timer.start(self.timeout_ms)
            if not current() or session["failure"] is not None:
                return
            session["spawn_invoked"] = True
            process.start(program, ["-I", "-S", "-B", helper])
        except Exception:
            # Configuration may fail before any spawn, or start() may report a
            # terminal error synchronously. Never deliver a second callback.
            if current():
                session["failure"] = PftPdfError("PDF_PROCESS_FAILED")
                if not session["spawn_invoked"]:
                    finish()
                else:
                    kill_owned()

    def cancel(self):
        if self._session is not None:
            self._session["fail"]("PDF_CANCELLED")
