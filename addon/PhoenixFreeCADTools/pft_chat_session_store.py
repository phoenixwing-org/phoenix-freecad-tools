# SPDX-License-Identifier: Apache-2.0
"""Owned thread metadata for the dedicated native chat profile only.

No Codex authentication, account, conversation or global thread store is read.
The caller registers a thread only after the official start response and policy
have been verified. This registry is an ownership index, not chat history.

Only POSIX cooperating writers are supported. The stable process.lock inode is
shared with the process lease and must already exist for registration. A Store
pins its observed registry etag: external changes require an explicitly new
Store, never a silent merge or overwrite. No deletion, eviction or migration is
performed. Existing v1 profiles can create their first registry after connection
has established the process lock, without touching authentication or history.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import unicodedata

import pft_chat_process_lease as _lease

REGISTRY_FILENAME = "sessions.json"
OWNER_PROTOCOL = "phoenix-freecad-chat-sessions-v1"
CLI_VERSION = "0.160.0"
_SUPPORTED_CLI_VERSIONS = frozenset((CLI_VERSION, "0.167.0-alpha.3.2"))
PROVIDER = "openai"
MAX_SESSIONS = 100
MAX_REGISTRY_BYTES = 64 * 1024
MAX_CREATED_AT = 253402300799  # Last UTC second of year 9999, within signed int64.
_UNREAD = object()
_MESSAGES = {
    "CHAT_SESSION_STORE_INVALID": "专属聊天会话登记无效；未修改登记。",
    "CHAT_SESSION_STORE_PROFILE_INVALID": "专属聊天配置未识别或已变化。",
    "CHAT_SESSION_STORE_PROFILE_MISMATCH": "会话登记不属于当前专属聊天配置。",
    "CHAT_SESSION_STORE_CHANGED": "专属聊天会话登记已变化；请重新检查后再操作。",
    "CHAT_SESSION_STORE_NOT_FOUND": "此会话没有本插件的登记。",
    "CHAT_SESSION_STORE_LIMIT": "专属聊天会话登记已达数量或大小上限。",
    "CHAT_SESSION_STORE_UNSAFE": "专属聊天会话登记目录或文件不安全。",
    "CHAT_SESSION_STORE_UNSUPPORTED": "当前平台无法验证专属聊天会话登记。",
    "CHAT_SESSION_STORE_IO_FAILED": "无法读取或保存专属聊天会话登记。",
    "CHAT_SESSION_STORE_SAVE_UNVERIFIED": "专属聊天会话登记保存无法确认；请人工核对。",
    "CHAT_SESSION_STORE_BUSY": "专属聊天会话登记正在使用，请重新检查后再操作。",
}


def _excluded_storage_roots(module_file=None):
    module_directory = Path(__file__ if module_file is None else module_file).resolve().parent
    roots = [module_directory]
    # Installed Addons live under Mod, whose grandparent is the real user-data
    # root. Only the explicit source layout plus this project's package marker
    # identifies a repository; never guess from a generic parents[2].
    if module_directory.name == "PhoenixFreeCADTools" and module_directory.parent.name == "addon":
        candidate = module_directory.parent.parent
        marker = candidate / "package.json"
        if marker.is_file() and not marker.is_symlink():
            roots.append(candidate)
    return tuple(roots)


_EXCLUDED_STORAGE_ROOTS = _excluded_storage_roots()


def _storage_gate(home, cwd):
    identities = set()
    for root in _EXCLUDED_STORAGE_ROOTS:
        try:
            identities.add(_lease._identity(root.lstat()))
        except FileNotFoundError:
            pass
    checked = set()
    for path in (Path(home).parent, Path(home), Path(cwd)):
        if any(path == root or root in path.parents for root in _EXCLUDED_STORAGE_ROOTS):
            _fail("PROFILE_INVALID")
        # Case-insensitive filesystems can spell the same source directory in
        # several ways. Check ancestor identities too, without directory scans,
        # .git access, symlink traversal or reading any profile file contents.
        for ancestor in (path, *path.parents):
            if ancestor in checked:
                continue
            checked.add(ancestor)
            try:
                info = ancestor.lstat()
            except FileNotFoundError:
                continue  # The profile validator will reject missing directories.
            if stat.S_ISLNK(info.st_mode) or _lease._identity(info) in identities:
                _fail("PROFILE_INVALID")


class PftChatSessionStoreError(RuntimeError):
    def __init__(self, code, uncertain=False):
        self.code, self.message, self.uncertain = code, _MESSAGES[code], bool(uncertain)
        super().__init__(self.message)


def _fail(suffix, uncertain=False):
    raise PftChatSessionStoreError("CHAT_SESSION_STORE_" + suffix, uncertain) from None


def _translated(error):
    if isinstance(error, PftChatSessionStoreError):
        raise error
    if isinstance(error, _lease.PftChatProcessLeaseError):
        suffix = error.code.removeprefix("CHAT_PROCESS_LEASE_")
        if suffix not in {"INVALID", "PROFILE_INVALID", "CHANGED", "UNSAFE", "UNSUPPORTED", "IO_FAILED", "BUSY"}:
            suffix = "IO_FAILED"
        _fail(suffix, uncertain=error.uncertain)
    _fail("IO_FAILED")


def _text(value, maximum=256):
    if (type(value) is not str or not value or len(value) > maximum or value != value.strip()
            or any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value)
            or len(value.encode("utf-8")) > maximum):
        _fail("INVALID")
    return value


def _hex(value):
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _metadata(thread_id, created_at, model, scope_fingerprint, history_mode="paginated", *, cli_version=CLI_VERSION):
    _text(thread_id)
    _text(model)
    if (type(created_at) is not int or not 0 <= created_at <= MAX_CREATED_AT
            or not _hex(scope_fingerprint) or type(history_mode) is not str or history_mode != "paginated"
            or type(cli_version) is not str or cli_version not in _SUPPORTED_CLI_VERSIONS):
        _fail("INVALID")
    return {"thread_id": thread_id, "created_at": created_at, "model": model,
            "scope_fingerprint": scope_fingerprint, "history_mode": history_mode,
            "cli_version": cli_version, "provider": PROVIDER}


def _session(record):
    if type(record) is not dict or set(record) != {
            "thread_id", "created_at", "model", "scope_fingerprint", "history_mode", "cli_version", "provider"}:
        _fail("INVALID")
    if type(record["cli_version"]) is not str or record["cli_version"] not in _SUPPORTED_CLI_VERSIONS or record["provider"] != PROVIDER:
        _fail("INVALID")
    return _metadata(record["thread_id"], record["created_at"], record["model"],
                     record["scope_fingerprint"], record["history_mode"], cli_version=record["cli_version"])


def _profile_identity(identities):
    return {name: list(identity) for name, identity in zip(("base", "home", "workspace"), identities)}


def _identity_contract(value):
    if type(value) is not dict or set(value) != {"base", "home", "workspace"}:
        _fail("INVALID")
    for pair in value.values():
        if (type(pair) is not list or len(pair) != 2
                or any(type(number) is not int or not 0 <= number <= 2 ** 64 - 1 for number in pair)):
            _fail("INVALID")
    return value


def _registry(record, identities):
    if (type(record) is not dict or set(record) != {
            "schema_version", "owner", "owner_protocol", "profile_id", "profile_identity", "sessions"}
            or type(record["schema_version"]) is not int or record["schema_version"] != 1
            or record["owner"] != "phoenix-freecad-tools" or record["owner_protocol"] != OWNER_PROTOCOL
            or not _hex(record["profile_id"]) or type(record["sessions"]) is not list
            or len(record["sessions"]) > MAX_SESSIONS):
        _fail("INVALID")
    if _identity_contract(record["profile_identity"]) != _profile_identity(identities):
        _fail("PROFILE_MISMATCH")
    sessions = [_session(session) for session in record["sessions"]]
    if len({session["thread_id"] for session in sessions}) != len(sessions):
        _fail("INVALID")
    return {**record, "sessions": sessions}


def _decode(raw, identities):
    # Reuse the duplicate-key, strict UTF-8 and non-float JSON decoder.
    return _registry(_lease._json(raw, MAX_REGISTRY_BYTES), identities)


def _etag(raw, info):
    return hashlib.sha256(raw + repr(_lease._stamp(info)).encode("ascii")).hexdigest()


def _read(parent, identities):
    try:
        os.stat(REGISTRY_FILENAME, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None, None
    try:
        raw, info = _lease._read_at(parent, REGISTRY_FILENAME, MAX_REGISTRY_BYTES)
    except FileNotFoundError:
        _fail("CHANGED")
    return _decode(raw, identities), _etag(raw, info)


def _encoded(record, identities):
    _registry(record, identities)
    raw = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(raw) > MAX_REGISTRY_BYTES:
        _fail("LIMIT")
    return raw


def _stage(parent, raw):
    name = ".sessions-" + secrets.token_hex(16) + ".tmp"
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    identity = _lease._identity(os.fstat(fd))
    try:
        os.fchmod(fd, 0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                raise OSError("registry staging write failed")
            offset += written
        os.fsync(fd)
        _lease._private(os.fstat(fd))
        return name, identity
    except BaseException:
        _lease._cleanup_stage(parent, name, identity)
        raise
    finally:
        active_error = sys.exc_info()[0] is not None
        if not _lease._close(fd) and not active_error:
            _lease._cleanup_stage(parent, name, identity)
            _fail("IO_FAILED", uncertain=True)


def _check_stage(parent, staged, expected):
    raw, info = _lease._read_at(parent, staged[0], MAX_REGISTRY_BYTES)
    if raw != expected or _lease._identity(info) != staged[1]:
        _fail("CHANGED")


class PftChatSessionStore:
    """Profile-bound metadata store; construction does not read a registry."""
    def __init__(self, home, cwd):
        try:
            _lease._supported()
            home, cwd = str(_lease._absolute(home)), str(_lease._absolute(cwd))
            _storage_gate(home, cwd)
            with _lease._profile(home, cwd) as (_parent, identities):
                self._home, self._cwd, self._identities = home, cwd, identities
            self._expected, self._profile_id, self._lock_identity = _UNREAD, None, None
            self._uncertain = False
        except Exception as error:
            _translated(error)

    @property
    def home(self):
        return self._home

    @property
    def cwd(self):
        return self._cwd

    def _ready(self):
        if self._uncertain:
            _fail("SAVE_UNVERIFIED", uncertain=True)

    def _observe(self, record, etag):
        if self._expected is not _UNREAD and etag != self._expected:
            _fail("CHANGED")
        if record is not None and self._profile_id is not None and record["profile_id"] != self._profile_id:
            _fail("CHANGED")
        self._expected = etag
        self._profile_id = record["profile_id"] if record is not None else None

    def list_sessions(self):
        self._ready()
        try:
            with _lease._profile(self._home, self._cwd) as (parent, identities):
                if identities != self._identities:
                    _fail("PROFILE_MISMATCH")
                try:
                    os.stat(REGISTRY_FILENAME, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    _lease._same_profile(self._home, self._cwd, identities)
                    self._observe(None, None)
                    return []
            with _lease._locked(self._home, self._cwd, self._identities, self._lock_identity,
                                create_lock=False) as (parent, _ids, _lock_fd, lock_identity):
                record, etag = _read(parent, self._identities)
                if record is None:
                    _fail("CHANGED")
                self._observe(record, etag)
                self._lock_identity = lock_identity
                result = [dict(session) for session in record["sessions"]]
            return result
        except Exception as error:
            _translated(error)

    def get_session(self, thread_id):
        _text(thread_id)
        for session in self.list_sessions():
            if session["thread_id"] == thread_id:
                return session
        _fail("NOT_FOUND")

    def register_thread(self, thread_id, created_at, model, scope_fingerprint, history_mode="paginated", *, cli_version=CLI_VERSION):
        """Register only the caller's policy-verified official start response."""
        self._ready()
        session = _metadata(thread_id, created_at, model, scope_fingerprint, history_mode, cli_version=cli_version)
        staged, published = None, False
        try:
            with _lease._locked(self._home, self._cwd, self._identities, self._lock_identity,
                                create_lock=False) as (parent, _ids, lock_fd, lock_identity):
                record, etag = _read(parent, self._identities)
                self._observe(record, etag)
                self._lock_identity = lock_identity
                if record is None:
                    record = {"schema_version": 1, "owner": "phoenix-freecad-tools", "owner_protocol": OWNER_PROTOCOL,
                              "profile_id": secrets.token_hex(32), "profile_identity": _profile_identity(self._identities), "sessions": []}
                else:
                    for existing in record["sessions"]:
                        if existing["thread_id"] == thread_id:
                            if existing != session:
                                _fail("CHANGED")
                            return dict(existing)
                if len(record["sessions"]) >= MAX_SESSIONS:
                    _fail("LIMIT")
                record = {**record, "sessions": [*record["sessions"], session]}
                raw = _encoded(record, self._identities)
                staged = _stage(parent, raw)
                current, current_etag = _read(parent, self._identities)
                self._observe(current, current_etag)
                _lease._lock_named(parent, lock_fd, lock_identity)
                _lease._same_profile(self._home, self._cwd, self._identities)
                _check_stage(parent, staged, raw)
                published_identity = staged[1]
                if current is None:
                    os.link(staged[0], REGISTRY_FILENAME, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                    published = True
                    source = os.stat(staged[0], dir_fd=parent, follow_symlinks=False)
                    target = os.stat(REGISTRY_FILENAME, dir_fd=parent, follow_symlinks=False)
                    if (_lease._identity(source) != published_identity or _lease._identity(target) != published_identity
                            or source.st_nlink != 2 or target.st_nlink != 2):
                        _fail("CHANGED")
                    os.unlink(staged[0], dir_fd=parent)
                else:
                    os.replace(staged[0], REGISTRY_FILENAME, src_dir_fd=parent, dst_dir_fd=parent)
                    published = True
                staged = None
                os.fsync(parent)
                actual, info = _lease._read_at(parent, REGISTRY_FILENAME, MAX_REGISTRY_BYTES)
                if actual != raw or _lease._identity(info) != published_identity:
                    _fail("CHANGED")
                verified = _decode(actual, self._identities)
                self._expected, self._profile_id = _etag(actual, info), verified["profile_id"]
            return dict(session)
        except Exception as error:
            if published:
                self._uncertain = True
                _fail("SAVE_UNVERIFIED", uncertain=True)
            _translated(error)
        finally:
            if staged is not None and not published:
                try:
                    _lease._cleanup_in_profile(self._home, self._cwd, self._identities, staged)
                except Exception as error:
                    _translated(error)


def list_user_sessions(user_data_directory):
    """Read only this plugin's registry under the actual supplied FreeCAD root."""
    try:
        _lease._supported()
        location = _lease._user_profile_location(user_data_directory)
        if location is None:
            return []
        home, cwd, base_identity = location
        store = PftChatSessionStore(home, cwd)
        if store._identities[0] != base_identity:
            _fail("PROFILE_MISMATCH")
        return store.list_sessions()
    except Exception as error:
        _translated(error)
