# SPDX-License-Identifier: Apache-2.0
"""Pure local adapter for the explicit Desk browser-service CLI.

The selected local Node/CLI files are trusted executable configuration. Desk's
CLI, not this module, verifies its manifest, Node version/hash, registration,
live HTTP identity and HTML without redirects. We validate only that CLI's
bounded result contract; we do not independently probe HTTP, start processes,
open a browser, repair permissions, create directories or claim stop authority.
The caller must use an argument-array process API with shell execution off.
"""

import json
import os
from pathlib import Path
import re
import stat
import unicodedata

PFT_DESK_APP_VERSION = "0.5.0"
PFT_MAX_STDOUT_BYTES = 64 * 1024
PFT_MAX_STDERR_BYTES = 8 * 1024
_PFT_MAX_SAFE_INTEGER = 9007199254740991
_PFT_RUNTIME = re.compile(r"sha256:[a-f0-9]{64}\Z")
_PFT_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z", re.I)
_PFT_ORIGIN = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})\Z")
_PFT_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}\Z")
_PFT_STATES = frozenset(("ready", "absent", "unavailable", "incompatible"))


class PftDeskError(RuntimeError):
    """Stable machine code and a short, path-free UI message."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _text(value, maximum=4096):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and not any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value))


def _path(value):
    if not _text(value) or value != value.strip() or not os.path.isabs(value):
        raise PftDeskError("CONFIGURATION_INVALID", "Desk 配置必须使用明确的绝对路径。")
    return Path(os.path.abspath(value))


def _inspect_path(value, allow_missing=False):
    """Inspect every component without following links; never repair anything."""
    current = Path(value.anchor)
    missing = False
    for index, part in enumerate(value.parts[1:]):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not allow_missing:
                raise PftDeskError("PATH_UNAVAILABLE", "配置的 Node 或 Desk CLI 文件不存在。") from None
            missing = True
            continue
        except OSError:
            raise PftDeskError("PATH_UNAVAILABLE", "无法读取 Desk 配置路径，请人工核对。") from None
        if stat.S_ISLNK(info.st_mode):
            raise PftDeskError("UNSAFE_PATH", "Desk 配置路径不能经过符号链接。")
        if index < len(value.parts) - 2 and not stat.S_ISDIR(info.st_mode):
            raise PftDeskError("UNSAFE_PATH", "Desk 配置路径的祖先必须是真实目录。")
    if missing:
        return None
    try:
        return value.lstat()
    except OSError:
        raise PftDeskError("PATH_UNAVAILABLE", "Desk 配置路径在检查期间变化。") from None


def _overlaps(first, second):
    return first == second or first in second.parents or second in first.parents


def validate_configuration(config):
    """Return the three canonical path strings without writing or probing Node.

    An absent namespace is valid: status reports absence and only an explicit
    ensure may ask Desk to create it. Existing namespaces must be private and
    owned by the current user, matching the Desk CLI's native-platform policy.
    No Node version subprocess is run; the CLI enforces its exact manifest pin.
    """
    keys = {"node_executable", "cli_entry", "state_dir"}
    if not isinstance(config, dict) or set(config) != keys:
        raise PftDeskError("CONFIGURATION_INVALID", "请完整配置 Node、Desk CLI 和独立状态目录，不接受额外参数。")
    node, cli, state_dir = (_path(config[key]) for key in ("node_executable", "cli_entry", "state_dir"))
    node_info, cli_info = _inspect_path(node), _inspect_path(cli)
    if not stat.S_ISREG(node_info.st_mode) or not os.access(str(node), os.X_OK):
        raise PftDeskError("CONFIGURATION_INVALID", "Node 必须是已有且可执行的普通文件。")
    if not stat.S_ISREG(cli_info.st_mode) or cli.name != "desk-web-service.mjs" or cli.parent.name != "scripts":
        raise PftDeskError("CONFIGURATION_INVALID", "Desk CLI 必须是已有 scripts/desk-web-service.mjs 普通文件。")
    repo = cli.parent.parent
    home = Path(os.path.abspath(str(Path.home())))
    addon = Path(os.path.abspath(__file__)).parent
    if (state_dir == Path(state_dir.anchor) or state_dir == home
            or _overlaps(state_dir, repo) or _overlaps(state_dir, addon)):
        raise PftDeskError("INVALID_STATE_DIRECTORY", "状态目录不能是根目录、主目录、源码目录或其父目录，且不能放入插件内。")
    state_info = _inspect_path(state_dir, allow_missing=True)
    if state_info is not None:
        if not stat.S_ISDIR(state_info.st_mode):
            raise PftDeskError("INVALID_STATE_DIRECTORY", "状态目录必须是真实目录。")
        if os.name != "nt" and state_info.st_mode & 0o077:
            raise PftDeskError("INVALID_STATE_DIRECTORY", "已有状态目录必须仅当前用户可访问，请人工核对权限。")
        getuid = getattr(os, "getuid", None)
        if callable(getuid) and state_info.st_uid != getuid():
            raise PftDeskError("INVALID_STATE_DIRECTORY", "已有状态目录必须归当前用户所有，不会接管其他目录。")
    return {"node_executable": str(node), "cli_entry": str(cli), "state_dir": str(state_dir)}


def build_command(config, action):
    """Return (program, arguments); no shell text, extra flags or process action."""
    if action not in ("status", "ensure"):
        raise PftDeskError("UNSUPPORTED_ACTION", "Desk 仅支持检查状态或明确启动后台。")
    checked = validate_configuration(config)
    return checked["node_executable"], [checked["cli_entry"], action, "--state-dir", checked["state_dir"]]


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json_constant(_value):
    raise ValueError("non-finite JSON number")


def _invalid():
    return PftDeskError("INVALID_RESPONSE", "Desk CLI 状态格式或身份字段无效；不会打开网页或接管服务。")


def _validated_snapshot(value, exit_code):
    if not isinstance(value, dict) or value.get("state") not in _PFT_STATES:
        raise _invalid()
    state = value["state"]
    required = {"code", "reason", "api_origin", "web_url", "instance_id", "reused", "created",
                "release_status", "app_version", "runtime_id", "pid"}
    if (not required.issubset(value) or not _text(value["code"], 128) or not _PFT_CODE.fullmatch(value["code"])
            or not _text(value["reason"], 2048) or value["app_version"] != PFT_DESK_APP_VERSION
            or value["release_status"] != "not-published" or type(value["created"]) is not bool
            or type(value["reused"]) is not bool
            or ("lock_present" in value and type(value["lock_present"]) is not bool)):
        raise _invalid()
    runtime_id = value["runtime_id"]
    if runtime_id is not None and (not isinstance(runtime_id, str) or not _PFT_RUNTIME.fullmatch(runtime_id)):
        raise _invalid()
    if state == "ready":
        origin = value["api_origin"]
        match = _PFT_ORIGIN.fullmatch(origin) if isinstance(origin, str) else None
        if (exit_code != 0 or value["code"] != "verified" or match is None or int(match.group(1)) > 65535
                or value["web_url"] not in (origin, origin + "/") or runtime_id is None
                or not isinstance(value["instance_id"], str) or not _PFT_UUID.fullmatch(value["instance_id"])
                or type(value["pid"]) is not int or not 0 < value["pid"] <= _PFT_MAX_SAFE_INTEGER
                or value["created"] == value["reused"]):
            raise _invalid()
    else:
        expected_exit = 2 if value["code"] == "invalid_arguments" else 1
        if (exit_code != expected_exit or value["code"] == "verified" or value["created"] or value["reused"]
                or any(value[key] is not None for key in ("api_origin", "web_url", "instance_id", "pid"))):
            raise _invalid()
    # Do not pass through unknown data, stderr, local paths or executable text.
    fields = ("state", "code", "reason", "api_origin", "web_url", "instance_id", "created", "reused",
              "release_status", "app_version", "runtime_id", "pid", "lock_present")
    return {key: value[key] for key in fields if key in value}


def parse_cli_result(exit_code, stdout, stderr):
    """Parse exactly one bounded UTF-8 JSON object, never HTML or stderr identity.

    A valid failure DTO is returned for UI display; only malformed/inconsistent
    output raises. HTTP redirect and runtime checks are the selected CLI's work.
    The snapshot proves a CLI verification at that time, not later liveness.
    """
    if type(exit_code) is not int or exit_code not in (0, 1, 2):
        raise PftDeskError("PROCESS_FAILED", "Desk CLI 未正常结束；不会根据进程号停止任何后台。")
    if not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
        raise _invalid()
    if len(stdout) > PFT_MAX_STDOUT_BYTES or len(stderr) > PFT_MAX_STDERR_BYTES:
        raise PftDeskError("OUTPUT_TOO_LARGE", "Desk CLI 输出超过大小限制，结果未被接受。")
    try:
        value = json.loads(stdout.decode("utf-8", errors="strict"), object_pairs_hook=_json_pairs,
                           parse_constant=_json_constant)
        return _validated_snapshot(value, exit_code)
    except PftDeskError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise _invalid() from None


def browser_url(snapshot):
    """Return only the exact root URL from a validated ready DTO; no I/O occurs."""
    if not isinstance(snapshot, dict) or snapshot.get("state") != "ready":
        raise PftDeskError("DESK_NOT_READY", "请先检查并确认 Desk 后台就绪，再打开网页。")
    return _validated_snapshot(snapshot, 0)["web_url"]
