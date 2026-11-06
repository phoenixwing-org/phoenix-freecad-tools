# SPDX-License-Identifier: Apache-2.0
"""Qt process adapter for the owned Desk CLI helper, never a Desk daemon owner."""
from pft_desk_service import PftDeskError

_OS_ENVIRONMENT = ("PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE",
                   "LOCALAPPDATA", "APPDATA", "LANG", "LC_ALL", "LC_CTYPE", "TZ")


class PftDeskProcess:
    """Drain bounded output asynchronously; timeout kills only this CLI child.

    The Desk background service is independently owned and detached by its CLI.
    No discovered PID/port is ever used for termination. A failed ensure may
    already have started a service, so callers must status-check rather than retry.
    """
    def __init__(self, core, parent):
        self.core, self.parent = core, parent
        self._process = None

    def start(self, program, arguments, completed, failed):
        if self._process is not None:
            raise PftDeskError("DESK_BUSY", "Desk 操作正在进行，请等待完成。")
        core = self.core
        process = core.QProcess(self.parent)
        timer = core.QTimer(process)
        timer.setSingleShot(True)
        chunks = {"stdout": bytearray(), "stderr": bytearray()}
        failure = [None]
        finished = [False]
        self._process = process
        environment = core.QProcessEnvironment()
        inherited = core.QProcessEnvironment.systemEnvironment()
        for key in _OS_ENVIRONMENT:
            if inherited.contains(key):
                environment.insert(key, inherited.value(key))
        process.setProcessEnvironment(environment)

        def finish(exit_code=None):
            if finished[0]:
                return
            finished[0] = True
            timer.stop()
            self._process = None
            process.deleteLater()
            if failure[0] is not None:
                failed(failure[0])
            elif exit_code is None:
                failed(PftDeskError("DESK_PROCESS_FAILED", "无法运行 Desk CLI；请核对 Node 和入口路径。"))
            else:
                completed(exit_code, bytes(chunks["stdout"]), bytes(chunks["stderr"]))

        def stop_helper(error):
            if finished[0] or failure[0] is not None:
                return
            failure[0] = error
            # This is the exact QProcess created here, not any registry PID.
            process.kill()

        def drain(channel, limit):
            if finished[0]:
                return
            reader = process.readAllStandardOutput if channel == "stdout" else process.readAllStandardError
            data = bytes(reader())
            room = limit - len(chunks[channel])
            chunks[channel].extend(data[:max(0, room)])
            if len(data) > room:
                stop_helper(PftDeskError("DESK_OUTPUT_TOO_LARGE", "Desk CLI 输出超过限制；后台状态未知，请重新检查。"))

        def exited(code, status):
            if finished[0]:
                return
            drain("stdout", 64 * 1024)
            drain("stderr", 8 * 1024)
            if status != core.QProcess.NormalExit and failure[0] is None:
                failure[0] = PftDeskError("DESK_PROCESS_FAILED", "Desk CLI 异常退出；后台状态未知，请重新检查。")
            finish(code)

        def process_error(error):
            if error == core.QProcess.FailedToStart:
                finish()
            else:
                stop_helper(PftDeskError("DESK_PROCESS_FAILED", "Desk CLI 读取或执行失败；后台状态未知，请重新检查。"))

        process.readyReadStandardOutput.connect(lambda: drain("stdout", 64 * 1024))
        process.readyReadStandardError.connect(lambda: drain("stderr", 8 * 1024))
        process.finished.connect(exited)
        process.errorOccurred.connect(process_error)
        timer.timeout.connect(lambda: stop_helper(PftDeskError("DESK_CLI_TIMEOUT", "Desk CLI 超时；服务状态未知，请稍后检查，未终止后台服务。")))
        try:
            timer.start(15000)
            process.start(program, arguments)
        except Exception:
            finish()
