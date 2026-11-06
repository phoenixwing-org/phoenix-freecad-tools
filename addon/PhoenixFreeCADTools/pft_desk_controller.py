# SPDX-License-Identifier: Apache-2.0
"""Desk preferences and single-flight orchestration; no Qt or document access."""
from pft_desk_service import PftDeskError, browser_url, build_command, parse_cli_result, validate_configuration
from pft_workbench_settings import PREFERENCE_PATH, _snapshot, _restore

_DESK_PATH = PREFERENCE_PATH + "/Desk"
_KEYS = {"node_executable": "NodeExecutable", "cli_entry": "CliEntry", "state_dir": "StateDirectory"}


def load_desk_configuration(app):
    try:
        preference = app.ParamGet(_DESK_PATH)
        result = {name: preference.GetString(key, "") for name, key in _KEYS.items()}
        if any(not isinstance(value, str) for value in result.values()):
            raise TypeError("Desk preferences must be strings")
        return result
    except Exception as error:
        raise PftDeskError("DESK_SETTINGS_READ_FAILED", "无法读取 Desk 连接设置。") from error


def save_desk_configuration(app, configuration):
    """Store explicit choices and flush native user parameters before success.

    FreeCAD's SetString mutates the in-memory parameter tree. A successful
    native saveParameter is also required so a crash need not lose the choices.
    The native API saves the user parameter set, not an individual Desk group.
    """
    configuration = validate_configuration(configuration)
    persist = getattr(app, "saveParameter", None)
    if not callable(persist):
        raise PftDeskError("DESK_SETTINGS_PERSIST_UNAVAILABLE", "当前 FreeCAD 无法持久保存 Desk 设置。")

    def persist_user_parameters():
        if persist("User parameter") is not None:
            raise RuntimeError("Native user parameter save returned an unexpected result")

    attempted = []
    persistence_attempted = False
    try:
        preference = app.ParamGet(_DESK_PATH)
        snapshots = {name: _snapshot(preference, "String", key, "") for name, key in _KEYS.items()}
        for name, key in _KEYS.items():
            attempted.append(snapshots[name])
            preference.SetString(key, configuration[name])
        if any(preference.GetString(key, "") != configuration[name] for name, key in _KEYS.items()):
            raise RuntimeError("Desk preference verification failed")
        persistence_attempted = True
        persist_user_parameters()
    except Exception as error:
        rollback_failed = False
        for snapshot in reversed(attempted):
            try:
                _restore(preference, snapshot)
            except Exception:
                rollback_failed = True
        if rollback_failed:
            # Do not make a known partial RAM rollback durable, or claim that
            # the first failed native save left the file untouched.
            raise PftDeskError("DESK_SETTINGS_ROLLBACK_FAILED", "Desk 设置保存失败，部分原设置无法恢复。") from error
        if attempted:
            try:
                persist_user_parameters()
            except Exception as restore_error:
                raise PftDeskError("DESK_SETTINGS_PERSIST_RESTORE_FAILED",
                                   "Desk 设置保存失败，原设置恢复后的落盘无法确认。") from restore_error
        if persistence_attempted:
            raise PftDeskError("DESK_SETTINGS_PERSIST_FAILED",
                               "Desk 设置未能持久保存，原设置已恢复并保存。") from error
        raise PftDeskError("DESK_SETTINGS_WRITE_FAILED", "Desk 设置保存失败，原设置已恢复。") from error
    return configuration


class PftDeskController:
    """Coordinate explicit user actions with an injected asynchronous runner.

    A browser action always runs a fresh status, never an implicit ensure.
    Completion of an older/already finished attempt cannot update a newer one.
    """
    def __init__(self, runner, changed, open_url):
        self.runner = runner
        self.changed = changed
        self.open_url = open_url
        self.snapshot = None
        self.busy = False
        self._attempt = None

    def _publish(self, error=None):
        self.changed({"busy": self.busy, "snapshot": self.snapshot,
                      "error": {"code": error.code, "message": error.message} if error else None})

    def invalidate(self):
        if self.busy:
            raise PftDeskError("DESK_BUSY", "Desk 操作正在进行，请等待完成。")
        self.snapshot = None
        self._publish()

    def run(self, configuration, action):
        if self.busy:
            self._publish(PftDeskError("DESK_BUSY", "Desk 操作正在进行，请等待完成。"))
            return
        if action not in ("status", "ensure", "open"):
            self._publish(PftDeskError("DESK_INVALID_ACTION", "不支持的 Desk 操作。"))
            return
        self.snapshot = None
        try:
            program, arguments = build_command(configuration, "status" if action == "open" else action)
        except PftDeskError as error:
            self._publish(error)
            return
        attempt = object()
        self._attempt, self.busy = attempt, True
        self._publish()

        def failed(error):
            if self._attempt is not attempt:
                return
            self._attempt, self.busy, self.snapshot = None, False, None
            if not isinstance(error, PftDeskError):
                error = PftDeskError("DESK_PROCESS_FAILED", "Desk 命令执行失败；后台状态未知，请重新检查。")
            self._publish(error)

        def completed(exit_code, stdout, stderr):
            if self._attempt is not attempt:
                return
            try:
                snapshot = parse_cli_result(exit_code, stdout, stderr)
                self.snapshot = snapshot
                if action == "open" and self.open_url(browser_url(snapshot)) is not True:
                    raise PftDeskError("DESK_BROWSER_OPEN_FAILED", "无法打开系统浏览器；后台未停止。")
            except PftDeskError as error:
                failed(error)
                return
            except Exception:
                failed(PftDeskError("DESK_PROCESS_FAILED", "Desk 操作失败；后台状态未知，请重新检查。"))
                return
            self._attempt, self.busy = None, False
            self._publish()

        try:
            self.runner.start(program, arguments, completed, failed)
        except Exception as error:
            failed(error)
