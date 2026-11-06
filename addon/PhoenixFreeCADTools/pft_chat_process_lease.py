# SPDX-License-Identifier: Apache-2.0
"""Durable, cooperating-process lease for the dedicated native chat profile.

Only the profile marker and managed configuration are read. Authentication,
account and conversation files are never inspected. Acquisition never probes
PIDs or recovers an old lease. Explicit inspection may observe only the recorded
child PID with signal zero; it never signals a child to stop, probes a parent,
scans processes, applies expiry or discovers legacy unregistered processes.

Callers must obtain home/cwd from prepare_chat_profile for FreeCAD's actual
user-data directory. This module validates that fixed layout and its ownership;
it does not decide which FreeCAD user-data root is authoritative. Existing v1
profiles remain usable. The lease covers cooperating processes using this new
protocol, not an unregistered process started by an earlier plugin version.

The short flock serializes cooperating writers. It does not defend against a
malicious process with the same UID. Keep the lock inode permanently: deleting
it would allow a second lock domain while the first inode is still locked.
"""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import unicodedata

from pft_chat_profile import PROFILE_CONFIG, _PROFILE_MARKER, validate_executable

LEASE_FILENAME = "process-lease.json"
LOCK_FILENAME = "process.lock"
OWNER_PROTOCOL = "phoenix-freecad-chat-process-v1"
MAX_LEASE_BYTES = 8192
_MESSAGES = {
    "CHAT_PROCESS_LEASE_BUSY": "专属聊天已有进程登记或正在连接；未启动新的进程。",
    "CHAT_PROCESS_LEASE_INVALID": "专属聊天进程登记未知或已损坏；未自动清理或接管。",
    "CHAT_PROCESS_LEASE_UNSAFE": "专属聊天进程目录或文件不安全；未启动新的进程。",
    "CHAT_PROCESS_LEASE_CHANGED": "专属聊天进程登记或目录已变化；未覆盖或移除。",
    "CHAT_PROCESS_LEASE_PROFILE_INVALID": "专属聊天配置未识别或已变化；请核对后再连接。",
    "CHAT_PROCESS_LEASE_UNSUPPORTED": "当前平台无法验证专属聊天进程租约。",
    "CHAT_PROCESS_LEASE_IO_FAILED": "无法验证专属聊天进程租约；未自动清理或接管。",
    "CHAT_PROCESS_LEASE_UPDATE_UNVERIFIED": "专属聊天进程租约更新无法确认；请人工核对登记。",
    "CHAT_PROCESS_LEASE_RELEASE_UNVERIFIED": "专属聊天进程租约释放无法确认；请人工核对登记。",
    "CHAT_PROCESS_LEASE_STATE_INVALID": "专属聊天进程租约状态不允许此操作。",
}


class PftChatProcessLeaseError(RuntimeError):
    def __init__(self, code, uncertain=False):
        self.code, self.message, self.uncertain = code, _MESSAGES[code], bool(uncertain)
        super().__init__(self.message)


def _fail(suffix, uncertain=False):
    raise PftChatProcessLeaseError("CHAT_PROCESS_LEASE_" + suffix, uncertain) from None


def _supported():
    if (os.name != "posix" or sys.platform == "win32"
            or not all(hasattr(os, name) for name in ("getuid", "O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"))):
        _fail("UNSUPPORTED")
    try:
        import fcntl
    except ImportError:
        _fail("UNSUPPORTED")
    return fcntl


def _absolute(value):
    if (type(value) is not str or not value or len(value) > 4096 or value != value.strip()
            or any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value)):
        _fail("PROFILE_INVALID")
    if len(value.encode("utf-8")) > 4096:
        _fail("PROFILE_INVALID")
    path = Path(value)
    if (not path.is_absolute() or ".." in path.parts or str(path) != value or path.anchor != "/"):
        _fail("PROFILE_INVALID")
    return path


def _identity(info):
    return info.st_dev, info.st_ino


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _private(info, directory=False):
    kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (not kind or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or not directory and info.st_nlink != 1):
        _fail("UNSAFE")


def _close(fd):
    try:
        os.close(fd)
        return True
    except OSError:
        return False  # Never retry a close whose descriptor ownership is unknown.


def _pairs(pairs):
    record = {}
    for key, value in pairs:
        if key in record:
            _fail("INVALID")
        record[key] = value
    return record


def _bad_number(_text):
    _fail("INVALID")


def _json(raw, maximum):
    if len(raw) > maximum:
        _fail("INVALID")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_float=_bad_number, parse_constant=_bad_number)
    except PftChatProcessLeaseError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        _fail("INVALID")


def _pid(value):
    return type(value) is int and 1 <= value <= 2 ** 31 - 1


def _record(raw):
    record = _json(raw, MAX_LEASE_BYTES)
    if (type(record) is not dict or set(record) != {
            "schema_version", "owner", "owner_protocol", "nonce", "parent_pid", "child_pid", "state", "program"}
            or type(record["schema_version"]) is not int or record["schema_version"] != 1
            or record["owner"] != "phoenix-freecad-tools" or record["owner_protocol"] != OWNER_PROTOCOL
            or type(record["nonce"]) is not str or len(record["nonce"]) != 64
            or any(char not in "0123456789abcdef" for char in record["nonce"])
            or not _pid(record["parent_pid"]) or record["state"] not in ("launching", "running")
            or record["state"] == "launching" and record["child_pid"] is not None
            or record["state"] == "running" and not _pid(record["child_pid"])):
        _fail("INVALID")
    try:
        _absolute(record["program"])
    except PftChatProcessLeaseError:
        _fail("INVALID")
    return record


def _read_at(parent, name, maximum):
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    _private(before)
    if before.st_size > maximum:
        _fail("INVALID")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        opened = os.fstat(fd)
        _private(opened)
        if _stamp(before) != _stamp(opened):
            _fail("CHANGED")
        raw = bytearray()
        while len(raw) <= maximum:
            chunk = os.read(fd, maximum + 1 - len(raw))
            if not chunk:
                break
            raw.extend(chunk)
        final = os.fstat(fd)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _private(final)
        _private(named)
        if _stamp(opened) != _stamp(final) or _stamp(final) != _stamp(named):
            _fail("CHANGED")
        if len(raw) > maximum:
            _fail("INVALID")
        return bytes(raw), final
    finally:
        active_error = sys.exc_info()[0] is not None
        if not _close(fd) and not active_error:
            _fail("IO_FAILED", uncertain=True)


def _walk(path):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            following = os.open(part, flags, dir_fd=fd)
            previous, fd = fd, following
            if not _close(previous):
                _fail("IO_FAILED", uncertain=True)
        return fd
    except BaseException:
        _close(fd)
        raise


@contextmanager
def _profile(home, cwd):
    home_path, cwd_path = _absolute(home), _absolute(cwd)
    base = home_path.parent
    if home_path.name != "home" or base.name != "CodexChat" or base.parent.name != "PhoenixTools" or cwd_path != base / "workspace":
        _fail("PROFILE_INVALID")
    base_fd = _walk(base)
    children = []
    try:
        _private(os.fstat(base_fd), directory=True)
        for name in ("home", "workspace"):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base_fd)
            children.append(fd)
            _private(os.fstat(fd), directory=True)
        marker, _ = _read_at(base_fd, "profile.json", 4096)
        decoded = _json(marker, 4096)
        if (decoded != _PROFILE_MARKER or type(decoded.get("schema_version")) is not int
                or set(decoded) != set(_PROFILE_MARKER)):
            _fail("PROFILE_INVALID")
        config, _ = _read_at(children[0], "config.toml", 16384)
        if config != PROFILE_CONFIG.encode("utf-8"):
            _fail("PROFILE_INVALID")
        identities = tuple(_identity(os.fstat(fd)) for fd in (base_fd, *children))
        yield base_fd, identities
    finally:
        active_error = sys.exc_info()[0] is not None
        closed = [_close(fd) for fd in (*reversed(children), base_fd)]
        if not all(closed) and not active_error:
            _fail("IO_FAILED", uncertain=True)


def _same_profile(home, cwd, identities):
    with _profile(home, cwd) as (_parent, current):
        if current != identities:
            _fail("CHANGED")


def _lock_named(parent, fd, identity):
    opened = os.fstat(fd)
    named = os.stat(LOCK_FILENAME, dir_fd=parent, follow_symlinks=False)
    if _identity(opened) != identity or _identity(named) != identity:
        _fail("CHANGED")
    _private(opened)
    _private(named)
    if opened.st_size != 0 or named.st_size != 0:
        _fail("CHANGED")


@contextmanager
def _locked(home, cwd, identities=None, lock_identity=None, *, create_lock=True):
    fcntl = _supported()
    with _profile(home, cwd) as (parent, current):
        if identities is not None and identities != current:
            _fail("CHANGED")
        fresh = False
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        if lock_identity is None and create_lock:
            try:
                fd = os.open(LOCK_FILENAME, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                fresh = True
            except FileExistsError:
                fd = os.open(LOCK_FILENAME, flags, dir_fd=parent)
        else:
            fd = os.open(LOCK_FILENAME, flags, dir_fd=parent)
        try:
            if fresh:
                os.fchmod(fd, 0o600)
                os.fsync(fd)
                os.fsync(parent)
            opened = os.fstat(fd)
            _private(opened)
            identity = _identity(opened)
            if lock_identity is not None and identity != lock_identity:
                _fail("CHANGED")
            _lock_named(parent, fd, identity)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                _fail("BUSY")
            _lock_named(parent, fd, identity)
            _same_profile(home, cwd, current)
            yield parent, current, fd, identity
            _lock_named(parent, fd, identity)
            _same_profile(home, cwd, current)
        finally:
            active_error = sys.exc_info()[0] is not None
            # close releases flock even if there was no successful acquisition.
            if not _close(fd) and not active_error:
                _fail("IO_FAILED", uncertain=True)


def _encoded(record):
    raw = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    _record(raw)
    return raw


def _stage(parent, raw):
    name = ".process-lease-" + secrets.token_hex(16) + ".tmp"
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    identity = _identity(os.fstat(fd))
    try:
        os.fchmod(fd, 0o600)
        position = 0
        while position < len(raw):
            count = os.write(fd, raw[position:])
            if count <= 0:
                raise OSError("lease staging write failed")
            position += count
        os.fsync(fd)
        _private(os.fstat(fd))
        return name, identity
    except BaseException:
        _cleanup_stage(parent, name, identity)
        raise
    finally:
        active_error = sys.exc_info()[0] is not None
        if not _close(fd) and not active_error:
            _cleanup_stage(parent, name, identity)
            _fail("IO_FAILED", uncertain=True)


def _check_stage(parent, staged, expected):
    raw, info = _read_at(parent, staged[0], MAX_LEASE_BYTES)
    if raw != expected or _identity(info) != staged[1]:
        _fail("CHANGED")


def _cleanup_stage(parent, name, identity):
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    _private(info)
    if _identity(info) != identity:
        _fail("CHANGED")
    os.unlink(name, dir_fd=parent)


def _cleanup_in_profile(home, cwd, expected_ids, staged):
    try:
        with _profile(home, cwd) as (parent, ids):
            if ids != expected_ids:
                _fail("CHANGED")
            _cleanup_stage(parent, *staged)
    except PftChatProcessLeaseError:
        raise
    except OSError:
        _fail("IO_FAILED", uncertain=True)


class PftChatProcessLease:
    """Capability for this acquisition; discarding it never releases its lease.

    release_after_exit requires the caller's positive observation of this exact
    owned child exiting. release_failed_to_start requires confirmed never-started
    failure. stop/kill requests, a deleted QObject or a dead parent are not proof.
    """
    def __init__(self, home, cwd, record, raw, stamp, identities, lock_identity):
        self._home, self._cwd = home, cwd
        self._record, self._raw, self._stamp = dict(record), raw, stamp
        self._identities, self._lock_identity = identities, lock_identity
        self._released, self._uncertain = False, False

    def _open(self):
        if self._released:
            _fail("STATE_INVALID")
        if self._uncertain:
            _fail("UPDATE_UNVERIFIED", uncertain=True)

    def _owned(self, parent):
        raw, info = _read_at(parent, LEASE_FILENAME, MAX_LEASE_BYTES)
        record = _record(raw)
        if (raw != self._raw or record["nonce"] != self._record["nonce"]
                or _stamp(info) != self._stamp):
            _fail("CHANGED")
        return record

    def mark_started(self, pid):
        """Record the exact owned child's positive PID, never discover one."""
        self._open()
        if not _pid(pid):
            _fail("STATE_INVALID")
        if self._record["state"] == "running" and self._record["child_pid"] != pid:
            _fail("STATE_INVALID")
        staged, parent, published = None, None, False
        try:
            with _locked(self._home, self._cwd, self._identities, self._lock_identity) as (parent, _ids, lock_fd, lock_id):
                self._owned(parent)
                if self._record["state"] == "running":
                    return True
                record = {**self._record, "state": "running", "child_pid": pid}
                raw = _encoded(record)
                staged = _stage(parent, raw)
                self._owned(parent)
                _lock_named(parent, lock_fd, lock_id)
                _same_profile(self._home, self._cwd, self._identities)
                _check_stage(parent, staged, raw)
                published_identity = staged[1]
                os.replace(staged[0], LEASE_FILENAME, src_dir_fd=parent, dst_dir_fd=parent)
                published, staged = True, None
                os.fsync(parent)
                actual, info = _read_at(parent, LEASE_FILENAME, MAX_LEASE_BYTES)
                if actual != raw or _identity(info) != published_identity:
                    _fail("CHANGED")
                self._record, self._raw, self._stamp = record, actual, _stamp(info)
            return True
        except Exception as error:
            if published:
                self._uncertain = True
                _fail("UPDATE_UNVERIFIED", uncertain=True)
            if isinstance(error, PftChatProcessLeaseError):
                raise
            _fail("IO_FAILED")
        finally:
            if staged is not None and parent is not None:
                # The with block has closed parent; reopen only the same pinned
                # profile, then remove only this known staging inode.
                _cleanup_in_profile(self._home, self._cwd, self._identities, staged)

    def _release(self, failed_to_start):
        if self._released:
            if self._uncertain:
                _fail("RELEASE_UNVERIFIED", uncertain=True)
            return True  # Never inspect or delete a later acquisition's record.
        self._open()
        if failed_to_start and self._record["state"] != "launching":
            _fail("STATE_INVALID")
        removed = False
        try:
            with _locked(self._home, self._cwd, self._identities, self._lock_identity) as (parent, _ids, lock_fd, lock_id):
                self._owned(parent)
                _lock_named(parent, lock_fd, lock_id)
                _same_profile(self._home, self._cwd, self._identities)
                os.unlink(LEASE_FILENAME, dir_fd=parent)
                removed, self._released = True, True
                os.fsync(parent)
            return True
        except Exception as error:
            if removed:
                self._uncertain = True
                _fail("RELEASE_UNVERIFIED", uncertain=True)
            if isinstance(error, PftChatProcessLeaseError):
                raise
            _fail("IO_FAILED")

    def release_after_exit(self):
        """Caller has confirmed this exact owned process exited, not requested stop."""
        return self._release(False)

    def release_failed_to_start(self):
        """Caller has confirmed never-started failure; invalid once marked running."""
        return self._release(True)


def acquire(home, cwd, program):
    """Create launching registration before any attempt to start app-server.

    Any existing registration is retained and rejected. Valid PIDs, dead-looking
    PIDs, incomplete startup and old timestamps never grant recovery authority.
    """
    _supported()
    staged, identities, published = None, None, False
    try:
        home_path, cwd_path = str(_absolute(home)), str(_absolute(cwd))
        canonical_program = validate_executable(program)
        if canonical_program != program:
            _fail("PROFILE_INVALID")
        record = {"schema_version": 1, "owner": "phoenix-freecad-tools", "owner_protocol": OWNER_PROTOCOL,
                  "nonce": secrets.token_hex(32), "parent_pid": os.getpid(), "child_pid": None,
                  "state": "launching", "program": canonical_program}
        raw = _encoded(record)
        with _locked(home_path, cwd_path) as (parent, identities, lock_fd, lock_identity):
            try:
                existing, _info = _read_at(parent, LEASE_FILENAME, MAX_LEASE_BYTES)
            except FileNotFoundError:
                pass
            else:
                _record(existing)
                _fail("BUSY")
            staged = _stage(parent, raw)
            _lock_named(parent, lock_fd, lock_identity)
            _same_profile(home_path, cwd_path, identities)
            _check_stage(parent, staged, raw)
            # link publishes without overwriting a noncooperating appearance;
            # remove our staging link before accepting the single-link lease.
            os.link(staged[0], LEASE_FILENAME, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            published = True
            info = os.stat(staged[0], dir_fd=parent, follow_symlinks=False)
            named = os.stat(LEASE_FILENAME, dir_fd=parent, follow_symlinks=False)
            if (_identity(info) != staged[1] or _identity(named) != staged[1]
                    or info.st_nlink != 2 or named.st_nlink != 2):
                _fail("CHANGED")
            published_identity = staged[1]
            os.unlink(staged[0], dir_fd=parent)
            staged = None
            os.fsync(parent)
            actual, info = _read_at(parent, LEASE_FILENAME, MAX_LEASE_BYTES)
            if actual != raw or _identity(info) != published_identity:
                _fail("CHANGED")
            lease = PftChatProcessLease(home_path, cwd_path, record, actual, _stamp(info), identities, lock_identity)
        return lease
    except Exception as error:
        if published:
            _fail("UPDATE_UNVERIFIED", uncertain=True)
        if isinstance(error, PftChatProcessLeaseError):
            raise
        _fail("IO_FAILED")
    finally:
        if staged is not None and identities is not None and not published:
            _cleanup_in_profile(home_path, cwd_path, identities, staged)


def _observe_recorded_child(pid):
    # This is an explicit existence observation, not identity verification.
    # Success can mean PID reuse; it never authorizes deletion or signaling.
    if not _pid(pid):
        return "unknown"
    try:
        os.kill(pid, 0)
    except OSError as error:
        return "exited" if error.errno == errno.ESRCH else "unknown"
    return "present"


def _user_profile_location(user_data_directory):
    # App.getUserAppDataDir commonly includes a trailing separator. Strip only
    # that spelling; never resolve symlinks, .., ~ or a different user-data root.
    if type(user_data_directory) is not str or len(user_data_directory) > 4096:
        _fail("PROFILE_INVALID")
    data = _absolute(user_data_directory.rstrip("/") or "/")
    data_fd = _walk(data)  # The actual supplied data root must already exist.
    opened = []
    try:
        info = os.fstat(data_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            _fail("UNSAFE")
        try:
            phoenix = os.open("PhoenixTools", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=data_fd)
        except FileNotFoundError:
            return None
        opened.append(phoenix)
        info = os.fstat(phoenix)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            _fail("UNSAFE")
        try:
            base = os.open("CodexChat", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=phoenix)
        except FileNotFoundError:
            return None
        opened.append(base)
        _private(os.fstat(base), directory=True)
        base_path = data / "PhoenixTools" / "CodexChat"
        return str(base_path / "home"), str(base_path / "workspace"), _identity(os.fstat(base))
    finally:
        active_error = sys.exc_info()[0] is not None
        closed = [_close(fd) for fd in (*reversed(opened), data_fd)]
        if not all(closed) and not active_error:
            _fail("IO_FAILED", uncertain=True)


class PftChatRegistrationInspection:
    """Bound snapshot of an explicit check; public fields contain no paths.

    present means only that the recorded PID currently exists, not that it is
    still the registered child. absent describes this registration, never proof
    that an unregistered legacy child is absent. Only an exited snapshot can be
    cleared, with all ownership metadata and ESRCH rechecked inside the lock.
    """
    __slots__ = ("_status", "_child_pid", "_lease", "_cleared", "_uncertain")

    def __init__(self, status, child_pid=None, lease=None):
        self._status, self._child_pid, self._lease = status, child_pid, lease
        self._cleared, self._uncertain = False, False

    @property
    def status(self):
        return self._status

    @property
    def child_pid(self):
        return self._child_pid

    @property
    def can_clear(self):
        return self._status == "exited" and self._lease is not None and not self._cleared and not self._uncertain

    def clear_exited(self):
        """Explicit user action; never reconnect, stop a process or retry work."""
        if self._cleared:
            if self._uncertain:
                _fail("RELEASE_UNVERIFIED", uncertain=True)
            return True  # Do not inspect a later acquisition after our success.
        if not self.can_clear:
            _fail("STATE_INVALID")
        owned, removed = self._lease, False
        try:
            with _locked(owned._home, owned._cwd, owned._identities, owned._lock_identity,
                         create_lock=False) as (parent, _ids, lock_fd, lock_id):
                record = owned._owned(parent)
                if record["state"] != "running" or record["child_pid"] != self._child_pid:
                    _fail("CHANGED")
                _lock_named(parent, lock_fd, lock_id)
                _same_profile(owned._home, owned._cwd, owned._identities)
                observed = _observe_recorded_child(self._child_pid)
                if observed != "exited":
                    self._status = observed
                    _fail("BUSY" if observed == "present" else "STATE_INVALID")
                # Observe against the same registration even if an unrelated
                # callback changed a file while the existence probe returned.
                owned._owned(parent)
                _lock_named(parent, lock_fd, lock_id)
                _same_profile(owned._home, owned._cwd, owned._identities)
                os.unlink(LEASE_FILENAME, dir_fd=parent)
                removed, self._cleared = True, True
                os.fsync(parent)
            self._status, self._child_pid = "absent", None
            return True
        except Exception as error:
            if removed:
                self._status, self._uncertain = "unknown", True
                _fail("RELEASE_UNVERIFIED", uncertain=True)
            if self._status == "exited":
                self._status = "unknown"  # Inspect again before another clear attempt.
            if isinstance(error, PftChatProcessLeaseError):
                raise
            _fail("IO_FAILED")


def inspect_user_registration(user_data_directory):
    """Inspect the fixed existing profile without creating or repairing files.

    A malformed profile/registration or missing lock with a registration is an
    error, not absent. Only a valid running child PID is probed with kill(pid, 0).
    No authentication/history files, parent PID or process command is inspected.
    """
    _supported()
    try:
        location = _user_profile_location(user_data_directory)
        if location is None:
            return PftChatRegistrationInspection("absent")
        home, cwd, base_identity = location
        with _profile(home, cwd) as (parent, identities):
            if identities[0] != base_identity:
                _fail("CHANGED")
            try:
                os.stat(LEASE_FILENAME, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                _same_profile(home, cwd, identities)
                return PftChatRegistrationInspection("absent")
        with _locked(home, cwd, identities, create_lock=False) as (parent, _ids, _lock_fd, lock_identity):
            raw, info = _read_at(parent, LEASE_FILENAME, MAX_LEASE_BYTES)
            record = _record(raw)
            owned = PftChatProcessLease(home, cwd, record, raw, _stamp(info), identities, lock_identity)
            pid = record["child_pid"]
            status = _observe_recorded_child(pid) if record["state"] == "running" else "unknown"
            owned._owned(parent)
            inspection = PftChatRegistrationInspection(status, pid, owned)
        return inspection
    except PftChatProcessLeaseError:
        raise
    except (OSError, ValueError, UnicodeError):
        _fail("IO_FAILED")
