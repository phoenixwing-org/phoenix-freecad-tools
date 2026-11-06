# SPDX-License-Identifier: Apache-2.0
"""Strict, private MCP settings. No import-time I/O, FreeCAD, Qt or services.

The exclusive lock coordinates writers using this protocol; it is not protection
against a malicious process with the same user privileges. A post-publication
failure is reported as uncertain, never automatically retried or rolled back.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import unicodedata

MAX_BYTES = 8192
DEFAULT_PORT = 9876
_MESSAGES = {
    "MCP_SETTINGS_INVALID": "Invalid MCP settings.",
    "MCP_SETTINGS_VERSION_UNSUPPORTED": "Unsupported MCP settings version.",
    "MCP_SETTINGS_PATH_INVALID": "Invalid MCP settings location.",
    "MCP_SETTINGS_UNSAFE": "MCP settings storage is not private and safe.",
    "MCP_SETTINGS_UNREADABLE": "MCP settings could not be read.",
    "MCP_SETTINGS_UNSUPPORTED": "Private MCP settings storage is unsupported.",
    "MCP_SETTINGS_CHANGED": "MCP settings changed; read them again before saving.",
    "MCP_SETTINGS_BUSY": "MCP settings storage is locked; inspect the existing lock before recovery.",
    "MCP_SETTINGS_WRITE_FAILED": "MCP settings were not verified as published.",
    "MCP_SETTINGS_SAVE_UNVERIFIED": "MCP settings may have been saved; inspect them before another save.",
    "MCP_SETTINGS_CLEANUP_UNVERIFIED": "Owned MCP settings artifact cleanup could not be verified.",
}


class PftMcpSettingsError(RuntimeError):
    def __init__(self, code, may_have_saved=False):
        self.code = code
        self.message = _MESSAGES[code]
        self.may_have_saved = bool(may_have_saved)
        super().__init__(self.message)


def _fail(code):
    raise PftMcpSettingsError(code) from None


def _settings(value):
    if (type(value) is not dict or any(type(k) is not str for k in value)
            or not {"schema_version", "bridge"}.issubset(value)
            or set(value) - {"schema_version", "bridge", "discovery"}
            or type(value["schema_version"]) is not int):
        _fail("MCP_SETTINGS_INVALID")
    version = value["schema_version"]
    if version not in (1, 2):
        _fail("MCP_SETTINGS_VERSION_UNSUPPORTED")
    expected = {"schema_version", "bridge"} if version == 1 else {"schema_version", "bridge", "discovery"}
    if set(value) != expected:
        _fail("MCP_SETTINGS_INVALID")
    bridge = value["bridge"]
    if (type(bridge) is not dict or any(type(k) is not str for k in bridge)
            or set(bridge) != {"preferred_port"}
            or type(bridge["preferred_port"]) is not int
            or not 1024 <= bridge["preferred_port"] <= 65535):
        _fail("MCP_SETTINGS_INVALID")
    result = {"schema_version": version, "bridge": {"preferred_port": bridge["preferred_port"]}}
    if version == 2:
        discovery = value["discovery"]
        if (type(discovery) is not dict or any(type(k) is not str for k in discovery)
                or set(discovery) != {"enabled"} or type(discovery["enabled"]) is not bool):
            _fail("MCP_SETTINGS_INVALID")
        result["discovery"] = {"enabled": discovery["enabled"]}
    return result


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail("MCP_SETTINGS_INVALID")
        result[key] = value
    return result


def _constant(unused):
    _fail("MCP_SETTINGS_INVALID")


def _decode(raw):
    if len(raw) > MAX_BYTES:
        _fail("MCP_SETTINGS_INVALID")
    try:
        return _settings(json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                                    parse_constant=_constant))
    except PftMcpSettingsError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        _fail("MCP_SETTINGS_INVALID")


def _absolute(value):
    try:
        text = os.fspath(value)
        if (type(text) is not str or text != text.strip()
                or any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in text)):
            _fail("MCP_SETTINGS_PATH_INVALID")
        path = Path(text)
        if (not path.is_absolute() or ".." in path.parts or str(path) != text
                or path.anchor == "//"):
            _fail("MCP_SETTINGS_PATH_INVALID")
        return path
    except PftMcpSettingsError:
        raise
    except (TypeError, ValueError, OSError):
        _fail("MCP_SETTINGS_PATH_INVALID")


def _layout(home=None, platform=None, env=None):
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    try:
        home = _absolute(Path.home() if home is None else home)
        if platform == "darwin":
            base = home / "Library" / "Application Support"
            create_from = len(home.parts) - 1
        elif platform == "win32":
            base = _absolute(env.get("LOCALAPPDATA", ""))
            create_from = len(base.parts) - 1
        elif platform == "linux":
            candidate = env.get("XDG_CONFIG_HOME", "")
            if candidate and Path(candidate).is_absolute():
                base = _absolute(candidate)
                create_from = len(base.parts) - 1
            else:
                base = home / ".config"
                create_from = len(home.parts) - 1
        else:
            _fail("MCP_SETTINGS_UNSUPPORTED")
        return base / "PhoenixTools" / "mcp" / "config.json", create_from, platform
    except PftMcpSettingsError:
        raise
    except (TypeError, ValueError, OSError, RuntimeError):
        _fail("MCP_SETTINGS_PATH_INVALID")


def resolve_config_path(*, home=None, platform=None, env=None):
    """Compute a location without reading or creating its contents."""
    return _layout(home, platform, env)[0]


def _close(descriptor):
    try:
        os.close(descriptor)
        return True
    except OSError:
        return False  # Never retry a close with uncertain descriptor ownership.


def _identity(info):
    return info.st_dev, info.st_ino


def _metadata(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _private(info, directory=False):
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077
            or not directory and (info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & ~0o600)):
        _fail("MCP_SETTINGS_UNSAFE")


def _parent(path, create_from, create=False, verify_private=True):
    """Pin each ancestor; create only the fixed suffix after the supplied root."""
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "getuid")):
        _fail("MCP_SETTINGS_UNSUPPORTED")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current = os.open(path.anchor, flags)
    parts = path.parent.parts[1:]
    try:
        for index, name in enumerate(parts):
            created = False
            try:
                following = os.open(name, flags, dir_fd=current)
            except FileNotFoundError:
                if not create:
                    previous, current = current, None
                    if not _close(previous):
                        _fail("MCP_SETTINGS_UNREADABLE")
                    return None
                if index < create_from:
                    _fail("MCP_SETTINGS_PATH_INVALID")
                try:
                    os.mkdir(name, 0o700, dir_fd=current)
                    created = True
                    os.fsync(current)
                except FileExistsError:
                    pass
                following = os.open(name, flags, dir_fd=current)
            previous, current = current, following
            if not _close(previous):
                _fail("MCP_SETTINGS_UNREADABLE")
            if created:
                os.fchmod(current, 0o700)  # Only the directory just created here.
                os.fsync(current)
            if verify_private and index >= len(parts) - 2:
                _private(os.fstat(current), directory=True)
        return current
    except BaseException:
        if current is not None:
            _close(current)
        raise


def _default(path):
    return {"source": "default", "settings": {"schema_version": 1,
            "bridge": {"preferred_port": DEFAULT_PORT}}, "config_path": str(path), "etag": None}


def _read_at(parent, path):
    try:
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return _default(path)
    _private(before)
    descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        opened = os.fstat(descriptor)
        _private(opened)
        if _metadata(before) != _metadata(opened):
            _fail("MCP_SETTINGS_CHANGED")
        if opened.st_size > MAX_BYTES:
            _fail("MCP_SETTINGS_INVALID")
        chunks, total = [], 0
        while total <= MAX_BYTES:
            chunk = os.read(descriptor, MAX_BYTES + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        raw = b"".join(chunks)
        final = os.fstat(descriptor)
        named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        _private(final)
        _private(named)
        if _metadata(opened) != _metadata(final) or _metadata(final) != _metadata(named):
            _fail("MCP_SETTINGS_CHANGED")
        settings = _decode(raw)
        etag = hashlib.sha256(raw + repr(_metadata(final)).encode("ascii")).hexdigest()
        return {"source": "user_config", "settings": settings, "config_path": str(path), "etag": etag}
    finally:
        if not _close(descriptor):
            _fail("MCP_SETTINGS_UNREADABLE")


def _windows_read(path):
    # A POSIX mode value cannot verify a Windows ACL. Reparse points are unsafe.
    for part in reversed((path,) + tuple(path.parents)):
        try:
            info = os.lstat(part)
        except FileNotFoundError:
            return _default(path)
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            _fail("MCP_SETTINGS_UNSAFE")
        if part != path and not stat.S_ISDIR(info.st_mode):
            _fail("MCP_SETTINGS_UNSAFE")
    _fail("MCP_SETTINGS_UNSUPPORTED")


def read_config(*, home=None, platform=None, env=None):
    """Missing is default without creation; existing invalid/unsafe is an error."""
    path, create_from, platform = _layout(home, platform, env)
    parent = None
    try:
        if platform == "win32":
            return _windows_read(path)
        parent = _parent(path, create_from, verify_private=False)
        if parent is None:
            return _default(path)
        try:
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return _default(path)
        # Missing configuration is default even in an existing public parent.
        # Before reading any existing file, revalidate privacy and parent identity.
        _same_parent(parent, path, create_from)
        return _read_at(parent, path)
    except PftMcpSettingsError:
        raise
    except OSError:
        _fail("MCP_SETTINGS_UNREADABLE")
    finally:
        if parent is not None and not _close(parent):
            _fail("MCP_SETTINGS_UNREADABLE")


def _expected(record, etag):
    if record["etag"] != etag:
        _fail("MCP_SETTINGS_CHANGED")


def _same_parent(parent, path, create_from):
    other = _parent(path, create_from)
    try:
        if other is None or _identity(os.fstat(other)) != _identity(os.fstat(parent)):
            _fail("MCP_SETTINGS_CHANGED")
    finally:
        if other is not None and not _close(other):
            _fail("MCP_SETTINGS_UNREADABLE")


def _owned(parent, name):
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
    try:
        identity = _identity(os.fstat(descriptor))
    except BaseException:
        _close(descriptor)
        raise PftMcpSettingsError("MCP_SETTINGS_CLEANUP_UNVERIFIED") from None
    owned = name, descriptor, identity
    try:
        os.fchmod(descriptor, 0o600)
        _private(os.fstat(descriptor))
        return owned
    except BaseException:
        if not _cleanup(parent, owned):
            raise PftMcpSettingsError("MCP_SETTINGS_CLEANUP_UNVERIFIED") from None
        raise


def _check_owned(parent, owned):
    name, descriptor, identity = owned
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    _private(info)
    if _identity(info) != identity or _identity(os.fstat(descriptor)) != identity:
        _fail("MCP_SETTINGS_CHANGED")


def _cleanup(parent, owned):
    if owned is None:
        return True
    name, descriptor, identity = owned
    cleaned = True
    try:
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            info = None
        if info is not None:
            if _identity(info) != identity or not stat.S_ISREG(info.st_mode):
                cleaned = False
            else:
                os.unlink(name, dir_fd=parent)
    except OSError:
        cleaned = False
    closed = _close(descriptor)
    return cleaned and closed


def save_config(settings, *, expected_etag, home=None, platform=None, env=None):
    """Explicit cooperating-writer save, with optimistic preview binding.

    None expects a still-missing file. A returned etag binds original bytes and
    file identity. Existing directories/files are never chmod'ed or repaired.
    Windows saving is unsupported until a genuine ACL verifier is implemented.
    """
    settings = _settings(settings)
    if expected_etag is not None and (type(expected_etag) is not str
            or len(expected_etag) != 64 or any(c not in "0123456789abcdef" for c in expected_etag)):
        _fail("MCP_SETTINGS_INVALID")
    path, create_from, platform = _layout(home, platform, env)
    if platform == "win32":
        _fail("MCP_SETTINGS_UNSUPPORTED")
    # Validate the entire existing configuration before creating directories.
    _expected(read_config(home=home, platform=platform, env=env), expected_etag)
    raw = (json.dumps(settings, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    parent, lock, temporary = None, None, None
    published, failure, result = False, None, None
    try:
        parent = _parent(path, create_from, create=True)
        try:
            lock = _owned(parent, "config.json.lock")
        except FileExistsError:
            _fail("MCP_SETTINGS_BUSY")
        _same_parent(parent, path, create_from)
        _expected(_read_at(parent, path), expected_etag)  # Recheck under the lock.
        _check_owned(parent, lock)
        temporary = _owned(parent, ".config.json." + secrets.token_hex(16) + ".tmp")
        offset = 0
        while offset < len(raw):
            count = os.write(temporary[1], raw[offset:])
            if count <= 0:
                raise OSError()
            offset += count
        os.fsync(temporary[1])
        _check_owned(parent, temporary)
        _same_parent(parent, path, create_from)
        _expected(_read_at(parent, path), expected_etag)
        _check_owned(parent, lock)
        # Set before the syscall: an uncertain publish must not invite a retry.
        published = True
        os.replace(temporary[0], path.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
        _same_parent(parent, path, create_from)
        _check_owned(parent, lock)
        result = _read_at(parent, path)
        if result["source"] != "user_config" or result["settings"] != settings:
            _fail("MCP_SETTINGS_SAVE_UNVERIFIED")
    except PftMcpSettingsError as error:
        failure = error
    except OSError:
        failure = PftMcpSettingsError("MCP_SETTINGS_WRITE_FAILED")
    finally:
        clean_temp = _cleanup(parent, temporary) if parent is not None else True
        clean_lock = _cleanup(parent, lock) if parent is not None else True
        if parent is not None:
            try:
                if published:
                    os.fsync(parent)
            except OSError:
                failure = PftMcpSettingsError("MCP_SETTINGS_SAVE_UNVERIFIED", True)
            finally:
                if not _close(parent):
                    failure = PftMcpSettingsError("MCP_SETTINGS_CLEANUP_UNVERIFIED", published)
        if not clean_temp or not clean_lock:
            failure = PftMcpSettingsError("MCP_SETTINGS_CLEANUP_UNVERIFIED", published)
    if failure is not None:
        if published and failure.code != "MCP_SETTINGS_CLEANUP_UNVERIFIED":
            raise PftMcpSettingsError("MCP_SETTINGS_SAVE_UNVERIFIED", True) from None
        raise failure from None
    return result
