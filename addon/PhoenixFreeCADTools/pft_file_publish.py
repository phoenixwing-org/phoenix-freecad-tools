# SPDX-License-Identifier: Apache-2.0
"""Bounded frozen-byte publication, independent of FreeCAD and Qt.

Callers provide a fixed extension whitelist and byte budget, never a remote
policy. A successful receipt confirms the selected namespace and bytes at the
last check. This is not a compare-and-swap overwrite or a power-loss durability
promise. Without directory-relative OS APIs, the legacy path-based fallback
provides weaker namespace protection; callers can require directory descriptors.
"""
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import unicodedata


class PftFilePublishError(RuntimeError):
    """Fixed error with separate publication and owned-stage cleanup outcomes."""
    def __init__(self, code, message, *, publication_state="not_published", cleanup_state="complete"):
        super().__init__(message)
        self.code, self.message = code, message
        self.publication_state, self.cleanup_state = publication_state, cleanup_state


_MESSAGES = {
    "INVALID_EXPORT_POLICY": "The local export policy is invalid.",
    "INVALID_EXPORT_PATH": "An absolute filename with an allowed extension is required.",
    "INVALID_EXPORT_DIRECTORY": "Export ancestors must be existing real directories.",
    "INVALID_EXPORT_TARGET": "The export target must be a regular file, not a symbolic link.",
    "INVALID_EXPORT_DATA": "The frozen export bytes exceed the local byte budget or are invalid.",
    "EXPORT_TARGET_TOO_LARGE": "The existing export target exceeds the bounded read budget.",
    "EXPORT_PLAN_INVALID": "The frozen export plan is invalid or has changed.",
    "EXPORT_TARGET_CHANGED": "The confirmed target, directory, or staged bytes changed.",
    "OVERWRITE_REQUIRED": "Replacing the confirmed existing target requires explicit approval.",
    "EXPORT_DIRECTORY_FD_REQUIRED": "Directory-relative file publication is unavailable.",
    "EXPORT_CLEANUP_FAILED": "Owned-stage cleanup could not be confirmed; inspect the publication outcome.",
    "EXPORT_FAILED": "File publication failed; inspect the publication outcome before retrying.",
}


def _error(code, *, publication_state="not_published", cleanup_state="complete"):
    return PftFilePublishError(code, _MESSAGES[code], publication_state=publication_state,
                               cleanup_state=cleanup_state)


def _policy(extensions, max_bytes):
    if (type(extensions) not in (tuple, list, frozenset, set) or not 1 <= len(extensions) <= 16
            or any(type(value) is not str or re.fullmatch(r"\.[a-z0-9]{1,16}", value) is None
                   for value in extensions) or len(set(extensions)) != len(extensions)
            or type(max_bytes) is not int or max_bytes <= 0):
        raise _error("INVALID_EXPORT_POLICY")
    return frozenset(extensions), max_bytes


def export_target(filename, *, extensions):
    """Validate the complete existing ancestor chain without creating anything."""
    allowed, _ = _policy(extensions, 1)
    if (type(filename) is not str or not os.path.isabs(filename)
            or Path(filename).suffix.lower() not in allowed
            or any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in filename)):
        raise _error("INVALID_EXPORT_PATH")
    target = Path(os.path.abspath(filename))
    current, directories = Path(target.anchor), []
    try:
        # Include the root identity as well as each named ancestor.
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise _error("INVALID_EXPORT_DIRECTORY")
        directories.append((str(current), info.st_dev, info.st_ino, info.st_mode))
        for part in target.parts[1:-1]:
            current /= part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise _error("INVALID_EXPORT_DIRECTORY")
            directories.append((str(current), info.st_dev, info.st_ino, info.st_mode))
    except OSError:
        raise _error("INVALID_EXPORT_DIRECTORY") from None
    return target, tuple(directories)


def _stat_values(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _fingerprint(target, max_bytes, directory=None):
    try:
        before = (target.lstat() if directory is None else
                  os.stat(target.name, dir_fd=directory, follow_symlinks=False))
    except FileNotFoundError:
        return None
    except OSError:
        raise _error("EXPORT_FAILED") from None
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise _error("INVALID_EXPORT_TARGET")
    if before.st_size > max_bytes:
        raise _error("EXPORT_TARGET_TOO_LARGE")
    try:
        descriptor = os.open(target if directory is None else target.name,
                             os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                             dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if _stat_values(before) != _stat_values(opened):
                raise _error("EXPORT_TARGET_CHANGED")
            contents = stream.read(max_bytes + 1)
            after = os.fstat(stream.fileno())
        named = (target.lstat() if directory is None else
                 os.stat(target.name, dir_fd=directory, follow_symlinks=False))
        if (len(contents) > max_bytes or _stat_values(before) != _stat_values(after)
                or _stat_values(after) != _stat_values(named)):
            raise _error("EXPORT_TARGET_CHANGED")
    except OSError:
        raise _error("EXPORT_TARGET_CHANGED") from None
    return (*_stat_values(after), hashlib.sha256(contents).hexdigest())


def prepare_file_export(filename, data, *, extensions, max_bytes):
    """Freeze exact bytes and the bounded existing target; perform no writes."""
    allowed, budget = _policy(extensions, max_bytes)
    if filename is None or filename == "":
        return None
    target, directories = export_target(filename, extensions=allowed)
    if type(data) is not bytes or len(data) > budget:
        raise _error("INVALID_EXPORT_DATA")
    fingerprint = _fingerprint(target, budget)
    return {"path": str(target), "data": data, "digest": hashlib.sha256(data).hexdigest(),
            "exists": fingerprint is not None, "_fingerprint": fingerprint, "_directories": directories}


def directory_fd_supported():
    supported = {function.__name__ for function in os.supports_dir_fd}
    return {"open", "stat", "link", "rename", "unlink"} <= supported


def create_stage(target, directory, *, prefix=".pft-export-"):
    """Trusted local helper; the returned descriptor permits an exact readback."""
    if type(prefix) is not str or re.fullmatch(r"\.pft-[a-z0-9-]{1,48}-", prefix) is None:
        raise _error("INVALID_EXPORT_POLICY")
    if directory is None:
        return tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=str(target.parent))
    name = prefix + secrets.token_hex(16) + ".tmp"
    descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=directory)
    return descriptor, name


def write_file_export(plan, *, extensions, max_bytes, overwrite=False, stage_prefix=".pft-export-",
                      require_directory_fd=False, _stage_factory=None, _directory_fd_supported=None):
    """Publish frozen bytes once, returning a receipt or an explicitly scoped error.

    The private hooks retain older callers' fault-injection seams. They are local
    callables, never serialized plan fields. Entering a publication syscall makes
    its outcome unknown until final inode and byte checks prove publication.
    """
    allowed, budget = _policy(extensions, max_bytes)
    if plan is None:
        return None
    if (type(plan) is not dict or type(plan.get("data")) is not bytes or len(plan["data"]) > budget
            or hashlib.sha256(plan["data"]).hexdigest() != plan.get("digest")):
        raise _error("EXPORT_PLAN_INVALID")
    if not {"path", "exists", "_fingerprint", "_directories"} <= plan.keys():
        raise _error("EXPORT_TARGET_CHANGED")
    data = plan["data"]
    target, directories = export_target(plan.get("path"), extensions=allowed)
    fingerprint = _fingerprint(target, budget)
    if (directories != plan.get("_directories") or fingerprint != plan.get("_fingerprint")
            or (fingerprint is not None) is not plan.get("exists")):
        raise _error("EXPORT_TARGET_CHANGED")
    if fingerprint is not None and overwrite is not True:
        raise _error("OVERWRITE_REQUIRED")
    supported = (_directory_fd_supported or directory_fd_supported)()
    if require_directory_fd is not False and require_directory_fd is not True:
        raise _error("INVALID_EXPORT_POLICY")
    if require_directory_fd and not supported:
        raise _error("EXPORT_DIRECTORY_FD_REQUIRED")
    stage_factory = _stage_factory or (lambda path, directory: create_stage(path, directory, prefix=stage_prefix))
    temporary = parent_descriptor = stage_descriptor = stage_identity = None
    publication_state, cleanup_state, failure = "not_published", "complete", None
    stage_consumed, cleanup_failed = False, False
    try:
        if supported:
            parent_descriptor = os.open(str(target.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                                        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
            parent_info = os.fstat(parent_descriptor)
            if directories[-1][1:] != (parent_info.st_dev, parent_info.st_ino, parent_info.st_mode):
                raise _error("EXPORT_TARGET_CHANGED")
        # An injected creator that raises after creating an unreported file has
        # an unknown cleanup outcome; no guessed path is ever removed.
        cleanup_state = "unknown"
        stage_descriptor, temporary = stage_factory(target, parent_descriptor)
        stage_info = os.fstat(stage_descriptor)
        stage_identity = (stage_info.st_dev, stage_info.st_ino)
        if not stat.S_ISREG(stage_info.st_mode):
            raise _error("EXPORT_TARGET_CHANGED")
        with os.fdopen(stage_descriptor, "w+b") as stream:
            stage_descriptor = None  # The stream now owns this descriptor.
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            if stream.read(budget + 1) != data:
                raise _error("EXPORT_TARGET_CHANGED")
        _, current_directories = export_target(str(target), extensions=allowed)
        staged = os.stat(temporary, dir_fd=parent_descriptor, follow_symlinks=False)
        if (current_directories != directories or _fingerprint(target, budget, parent_descriptor) != fingerprint
                or (staged.st_dev, staged.st_ino) != stage_identity or not stat.S_ISREG(staged.st_mode)):
            raise _error("EXPORT_TARGET_CHANGED")
        publication_state = "unknown"
        if parent_descriptor is not None:
            if fingerprint is not None:
                os.replace(temporary, target.name, src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor)
                stage_consumed = True
            else:
                os.link(temporary, target.name, src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor,
                        follow_symlinks=False)
        elif fingerprint is not None:
            os.replace(temporary, str(target))
            stage_consumed = True
        else:
            os.link(temporary, str(target))
        _, current_directories = export_target(str(target), extensions=allowed)
        published = _fingerprint(target, budget, parent_descriptor)
        if (current_directories != directories or published is None
                or published[:2] != stage_identity or published[-1] != plan["digest"]):
            raise _error("EXPORT_TARGET_CHANGED")
        publication_state = "published"
    except PftFilePublishError as error:
        failure = error.code
    except Exception:
        # OS and local callback errors cannot leak filenames or change the
        # publication outcome back to "not_published".
        failure = "EXPORT_FAILED"
    finally:
        if stage_descriptor is not None:
            try:
                os.close(stage_descriptor)
            except Exception:
                cleanup_failed = True
                failure = failure or "EXPORT_CLEANUP_FAILED"
        if temporary is not None and stage_identity is not None:
            try:
                info = os.stat(temporary, dir_fd=parent_descriptor, follow_symlinks=False)
                if (info.st_dev, info.st_ino) != stage_identity or not stat.S_ISREG(info.st_mode):
                    failure = failure or "EXPORT_TARGET_CHANGED"
                else:
                    os.unlink(temporary, dir_fd=parent_descriptor)
                    cleanup_state = "complete"
            except FileNotFoundError:
                if stage_consumed and publication_state == "published":
                    cleanup_state = "complete"
                else:
                    failure = failure or "EXPORT_CLEANUP_FAILED"
            except Exception:
                cleanup_failed = True
                failure = failure or "EXPORT_CLEANUP_FAILED"
        if parent_descriptor is not None:
            try:
                os.close(parent_descriptor)
            except Exception:
                cleanup_failed = True
                failure = failure or "EXPORT_CLEANUP_FAILED"
        if cleanup_failed:
            cleanup_state = "unknown"
    if failure is not None:
        raise _error(failure, publication_state=publication_state, cleanup_state=cleanup_state)
    return {"path": str(target), "digest": plan["digest"], "size": len(data),
            "publication_state": publication_state, "cleanup_state": cleanup_state,
            "directory_fd": bool(supported)}
