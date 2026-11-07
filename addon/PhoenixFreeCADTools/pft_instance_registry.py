# SPDX-License-Identifier: Apache-2.0
"""Private, bounded instance discovery records for cooperating POSIX producers.

No import-time filesystem access, CAD, network or process inspection. Publication
never replaces an existing record. Cleanup is limited to the exact bytes/inode
and directory identities captured by the successful publisher. Same-UID hostile
processes are outside this cooperative protocol; uncertain outcomes fail closed.
"""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import unicodedata

MAX_RECORDS = 100
MAX_DIRECTORY_ENTRIES = 200
MAX_RECORD_BYTES = 8192
STAGING_PREFIX = ".pft-instance-"
_RECORD_NAME = re.compile(r"([0-9a-f]{32})\.json\Z")
_STAGING_NAME = re.compile(r"\.pft-instance-[0-9a-f]{32}\.tmp\Z")
_HEX_ID = re.compile(r"[0-9a-f]{32}\Z")
_URL = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{3,4})/\Z")
_KEYS = frozenset(("schema_version", "owner", "instance_id", "service_id", "pid", "url",
                   "protocol_version", "addon_version"))
_MESSAGES = {
    "UNSUPPORTED": "Private instance registration is unsupported on this platform.",
    "INVALID_PATH": "The instance registry path is invalid.",
    "UNSAFE": "The instance registry directory or file is unsafe.",
    "CHANGED": "The instance registration or directory changed; nothing was replaced or removed.",
    "INVALID_RECORD": "An instance registration is unrecognized or damaged.",
    "INVALID_ENTRY": "The instance registry contains an unrecognized entry.",
    "LIMIT": "The instance registry exceeds its count or byte limit.",
    "EXISTS": "This instance registration already exists; it was not replaced.",
    "BUSY": "The instance registry is busy; no automatic retry was made.",
    "IO_FAILED": "The instance registry operation could not be verified.",
    "PUBLISH_UNVERIFIED": "Instance registration publication could not be confirmed; inspect the registry manually.",
    "REMOVE_UNVERIFIED": "Instance registration removal could not be confirmed; inspect the registry manually.",
    "STATE_INVALID": "This instance registration no longer permits this operation.",
}


class PftInstanceRegistryError(RuntimeError):
    def __init__(self, code, *, uncertain=False):
        suffix = code.removeprefix("INSTANCE_REGISTRY_")
        self.code = "INSTANCE_REGISTRY_" + suffix
        self.message = _MESSAGES[suffix]
        self.uncertain = bool(uncertain)
        super().__init__(self.message)


def _fail(code, *, uncertain=False):
    raise PftInstanceRegistryError(code, uncertain=uncertain) from None


def _supported(platform=None):
    selected = sys.platform if platform is None else platform
    if (selected not in ("darwin", "linux") or os.name != "posix"
            or not all(hasattr(os, name) for name in ("getuid", "O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"))):
        _fail("UNSUPPORTED")
    try:
        import fcntl
    except ImportError:
        _fail("UNSUPPORTED")
    return selected, fcntl


def _path(value):
    if isinstance(value, Path):
        value = str(value)
    if (type(value) is not str or not value or len(value.encode("utf-8", "surrogatepass")) > 4096
            or value != value.strip() or not value.startswith("/") or value.startswith("//")
            or any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value)
            or any(part in (".", "..") for part in value.split("/"))):
        _fail("INVALID_PATH")
    return Path(value)


def _identity(info):
    return info.st_dev, info.st_ino


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _directory(info, private):
    mode = stat.S_IMODE(info.st_mode)
    if not stat.S_ISDIR(info.st_mode):
        _fail("UNSAFE")
    if private:
        if info.st_uid != os.getuid() or mode != 0o700:
            _fail("UNSAFE")
    elif (info.st_uid not in (0, os.getuid())
          or mode & 0o022 and not (info.st_uid == 0 and mode & stat.S_ISVTX)):
        # Root-owned sticky ancestors support trusted temporary QA locations.
        # The registry suffix itself always requires private mode 0700.
        _fail("UNSAFE")


def _file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        _fail("UNSAFE")


def _close(fd):
    try:
        os.close(fd)
    except OSError:
        pass  # A close with uncertain descriptor ownership is never retried.


@contextmanager
def _open_directory(path, *, private_from, create_from=None, missing_ok=False):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptors, identities = [], []
    try:
        descriptor = os.open("/", flags)
        descriptors.append(descriptor)
        root = os.fstat(descriptor)
        _directory(root, private_from <= 0)
        identities.append(_identity(root))
        missing = False
        for index, name in enumerate(path.parts[1:], 1):
            private = index >= private_from
            try:
                before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                if create_from is None or index < create_from:
                    if missing_ok:
                        missing = True
                        break
                    _fail("IO_FAILED")
                try:
                    os.mkdir(name, 0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass  # Concurrent creation is accepted only after full validation.
                before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            _directory(before, private)
            child = os.open(name, flags, dir_fd=descriptor)
            descriptors.append(child)
            after = os.fstat(child)
            _directory(after, private)
            if _identity(before) != _identity(after):
                _fail("CHANGED")
            descriptor = child
            identities.append(_identity(after))
        yield (None, None) if missing else (descriptor, tuple(identities))
    except PftInstanceRegistryError:
        raise
    except OSError:
        _fail("IO_FAILED")
    finally:
        for descriptor in reversed(descriptors):
            _close(descriptor)


def _check_directory(path, private_from, expected):
    with _open_directory(path, private_from=private_from) as (_, actual):
        if actual != expected:
            _fail("CHANGED")


def _environment_value(environment, key):
    value = environment.get(key, "")
    if type(value) is not str:
        _fail("INVALID_PATH")
    return value


def _location(*, home=None, platform=None, env=None):
    selected, _ = _supported(platform)
    home = _path(os.path.expanduser("~") if home is None else home)
    environment = os.environ if env is None else env
    if not hasattr(environment, "get"):
        _fail("INVALID_PATH")
    if selected == "darwin":
        base = home / "Library" / "Application Support"
        path = base / "PhoenixTools" / "mcp" / "runtime" / "instances"
        return path, len(base.parts), len(home.parts)
    runtime = _environment_value(environment, "XDG_RUNTIME_DIR")
    if runtime:
        runtime = _path(runtime)
        with _open_directory(runtime, private_from=len(runtime.parts) - 1, missing_ok=True) as (fd, _):
            if fd is not None:
                path = runtime / "phoenix-tools" / "mcp" / "instances"
                return path, len(runtime.parts) - 1, len(runtime.parts)
    state = _environment_value(environment, "XDG_STATE_HOME")
    if state:
        base = _path(state)
        create_from = len(base.parts)  # An explicit arbitrary base is not created.
    else:
        base = home / ".local" / "state"
        create_from = len(home.parts)
    return base / "PhoenixTools" / "mcp" / "runtime" / "instances", len(base.parts), create_from


def resolve_instance_directory(*, home=None, platform=None, env=None):
    """Resolve the fixed namespace; only an explicit Linux runtime root is inspected."""
    return _location(home=home, platform=platform, env=env)[0]


def _target(directory, *, home, platform, env):
    _supported(platform)
    if directory is not None:
        path = _path(directory)
        return path, len(path.parts) - 1, None
    return _location(home=home, platform=platform, env=env)


@contextmanager
def _locked(descriptor):
    _, fcntl = _supported()
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN):
            _fail("BUSY")
        if error.errno in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS, errno.EINVAL):
            _fail("UNSUPPORTED")
        _fail("IO_FAILED")
    try:
        yield
    finally:
        # Closing this exact directory descriptor also releases this short lock.
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass


def _record(value, expected_id=None):
    if (type(value) is not dict or set(value) != _KEYS
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["protocol_version"]) is not int or value["protocol_version"] != 1
            or type(value["owner"]) is not str or value["owner"] != "phoenix-freecad-tools"
            or type(value["addon_version"]) is not str or value["addon_version"] != "0.4.1"
            or any(type(value[key]) is not str or not _HEX_ID.fullmatch(value[key]) for key in ("instance_id", "service_id"))
            or type(value["pid"]) is not int or not 1 <= value["pid"] <= 9007199254740991
            or type(value["url"]) is not str):
        _fail("INVALID_RECORD")
    match = _URL.fullmatch(value["url"])
    if (match is None or not 1024 <= int(match[1]) <= 65535
            or expected_id is not None and value["instance_id"] != expected_id):
        _fail("INVALID_RECORD")
    return dict(value)


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("INVALID_RECORD")
        value[key] = item
    return value


def _bad_number(_):
    _fail("INVALID_RECORD")


def _decode(raw, expected_id):
    if len(raw) > MAX_RECORD_BYTES:
        _fail("LIMIT")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_float=_bad_number, parse_constant=_bad_number)
        return _record(value, expected_id)
    except PftInstanceRegistryError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        _fail("INVALID_RECORD")


def _read(descriptor, name, expected_id):
    before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    _file(before)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(name, flags, dir_fd=descriptor)
    try:
        opened = os.fstat(fd)
        _file(opened)
        if _stamp(before) != _stamp(opened):
            _fail("CHANGED")
        if opened.st_size > MAX_RECORD_BYTES:
            _fail("LIMIT")
        raw = bytearray()
        while len(raw) <= MAX_RECORD_BYTES:
            chunk = os.read(fd, MAX_RECORD_BYTES + 1 - len(raw))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > MAX_RECORD_BYTES:
                _fail("LIMIT")
        raw = bytes(raw)
        if len(raw) != opened.st_size:
            _fail("CHANGED")
        after = os.fstat(fd)
        _file(after)
        path_after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        _file(path_after)
        if _stamp(opened) != _stamp(after) or _stamp(after) != _stamp(path_after):
            _fail("CHANGED")
        return _decode(raw, expected_id), raw, _stamp(after)
    finally:
        _close(fd)


def _scan(descriptor):
    records, count = [], 0
    with os.scandir(descriptor) as entries:
        for entry in entries:
            count += 1
            if count > MAX_DIRECTORY_ENTRIES:
                _fail("LIMIT")
            name = entry.name
            match = _RECORD_NAME.fullmatch(name)
            if match is not None:
                if len(records) >= MAX_RECORDS:
                    _fail("LIMIT")
                value, _, _ = _read(descriptor, name, match[1])
                records.append(value)
            elif _STAGING_NAME.fullmatch(name):
                _file(os.stat(name, dir_fd=descriptor, follow_symlinks=False))
            else:
                _fail("INVALID_ENTRY")
    return records, count


def _unlink_staging(descriptor, name, identity):
    try:
        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if _identity(info) != identity:
            return False
        os.unlink(name, dir_fd=descriptor)
        return True
    except OSError:
        return False


class PftInstanceRegistration:
    """An in-memory cleanup capability, never a stale-record recovery API."""
    def __init__(self, path, private_from, directory_identity, name, raw, file_stamp):
        self._path, self._private_from, self._directory_identity = path, private_from, directory_identity
        self._name, self._raw, self._file_stamp = name, raw, file_stamp
        self._state = "published"

    def remove(self):
        if self._state == "removed":
            return True  # Never examine or delete a later registration at the same name.
        if self._state != "published":
            _fail("STATE_INVALID")
        unlink_attempted = False
        try:
            with _open_directory(self._path, private_from=self._private_from) as (fd, identity):
                with _locked(fd):
                    if identity != self._directory_identity:
                        _fail("CHANGED")
                    try:
                        _, raw, stamp = _read(fd, self._name, self._name[:-5])
                    except FileNotFoundError:
                        _fail("CHANGED")
                    if raw != self._raw or stamp != self._file_stamp:
                        _fail("CHANGED")
                    _check_directory(self._path, self._private_from, identity)
                    unlink_attempted = True
                    os.unlink(self._name, dir_fd=fd)
                    os.fsync(fd)
                    _check_directory(self._path, self._private_from, identity)
            self._state = "removed"
            return True
        except Exception as error:
            self._state = "uncertain" if unlink_attempted else "changed"
            if unlink_attempted:
                _fail("REMOVE_UNVERIFIED", uncertain=True)
            if isinstance(error, PftInstanceRegistryError):
                raise error from None
            _fail("CHANGED" if isinstance(error, FileNotFoundError) else "IO_FAILED")


def publish_instance(record, *, directory=None, home=None, platform=None, env=None):
    """Atomically publish one new registration; refuse every existing filename."""
    _supported(platform)
    value = _record(record)
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_RECORD_BYTES:
        _fail("LIMIT")
    path, private_from, create_from = _target(directory, home=home, platform=platform, env=env)
    published = publication_attempted = False
    stage_name = stage_identity = None
    with _open_directory(path, private_from=private_from, create_from=create_from) as (fd, identity):
        with _locked(fd):
            try:
                existing, entry_count = _scan(fd)
                if len(existing) >= MAX_RECORDS or entry_count + 2 > MAX_DIRECTORY_ENTRIES:
                    _fail("LIMIT")
                name = value["instance_id"] + ".json"
                try:
                    os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    _fail("EXISTS")
                stage_name = STAGING_PREFIX + secrets.token_hex(16) + ".tmp"
                stage_fd = os.open(stage_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                                   | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=fd)
                try:
                    os.fchmod(stage_fd, 0o600)
                    info = os.fstat(stage_fd)
                    _file(info)
                    stage_identity = _identity(info)
                    remaining = memoryview(raw)
                    while remaining:
                        written = os.write(stage_fd, remaining)
                        if written <= 0:
                            _fail("IO_FAILED")
                        remaining = remaining[written:]
                    os.fsync(stage_fd)
                finally:
                    _close(stage_fd)
                _check_directory(path, private_from, identity)
                publication_attempted = True
                try:
                    os.link(stage_name, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                except FileExistsError:
                    publication_attempted = False
                    _fail("EXISTS")
                published = True
                if not _unlink_staging(fd, stage_name, stage_identity):
                    _fail("PUBLISH_UNVERIFIED", uncertain=True)
                stage_name = None
                os.fsync(fd)
                actual, actual_raw, file_stamp = _read(fd, name, value["instance_id"])
                if actual != value or actual_raw != raw or file_stamp[:2] != stage_identity:
                    _fail("CHANGED")
                _check_directory(path, private_from, identity)
                return PftInstanceRegistration(path, private_from, identity, name, raw, file_stamp)
            except Exception as error:
                if (not published and not publication_attempted
                        and stage_name is not None and stage_identity is not None):
                    _unlink_staging(fd, stage_name, stage_identity)
                if published or publication_attempted:
                    _fail("PUBLISH_UNVERIFIED", uncertain=True)
                if isinstance(error, PftInstanceRegistryError):
                    raise error from None
                _fail("IO_FAILED")
