# SPDX-License-Identifier: Apache-2.0
"""Per-provider API credentials in a separate FreeCAD user data directory."""

import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit


_MAX_BYTES = 8192
_KEY = re.compile(r"[!-~]{8,512}\Z")
_WINDOWS = os.name == "nt"


def _dpapi(data, scope, *, encrypt):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    def blob(value):
        buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        return Blob(len(value), buffer)

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    transform = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    transform.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    transform.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source, entropy, result = blob(data), blob(scope), Blob()
    try:
        if not transform(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(result)):
            raise OSError("Windows could not protect or unlock the API credential.")
        if not result.data or not 0 < result.size <= _MAX_BYTES:
            raise ValueError("Invalid DPAPI output.")
        return ctypes.string_at(result.data, result.size)
    finally:
        if encrypt:
            ctypes.memset(source.data, 0, source.size)
        if result.data:
            if not encrypt:
                ctypes.memset(result.data, 0, result.size)
            kernel32.LocalFree(result.data)


class PftApiCredentialStore:
    """Bind one remembered key to a provider and the configured HTTPS base URL."""

    def __init__(self, user_data, provider, endpoint):
        url = urlsplit(endpoint)
        if (not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", provider) or url.scheme != "https"
                or not url.hostname or url.username is not None or url.password is not None
                or not re.fullmatch(r"https://[A-Za-z0-9.:[\]-]+(?:/[A-Za-z0-9._~/-]*)?", endpoint)
                or any(segment in (".", "..") for segment in url.path.split("/"))):
            raise ValueError("Invalid credential scope.")
        authority = url.netloc.lower()
        if url.port == 443:
            authority = authority.removesuffix(":443")
        identity = provider + "\nhttps://" + authority + url.path.rstrip("/")
        self.scope = hashlib.sha256(identity.encode("ascii")).digest()
        self.header = b"PFT-DPAPI-1\n" if _WINDOWS else b"PFT-POSIX-1\n"
        self.header += self.scope.hex().encode("ascii") + b"\n"
        self.root = Path(user_data)
        if not self.root.is_absolute():
            raise ValueError("Invalid FreeCAD user data directory.")
        self.directory = self.root / "PhoenixToolsApiCredentials"
        self.file = self.directory / (self.scope.hex() + ".key")

    @staticmethod
    def _check(info, *, directory=False, private=True):
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if (not expected(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                or not directory and info.st_nlink != 1):
            raise OSError("Unsafe API credential path.")
        if not _WINDOWS:
            denied = (0o077 if private else 0o022)
            if info.st_uid != os.getuid() or info.st_mode & denied:
                raise OSError("Unsafe API credential permissions.")

    def _directory_exists(self, *, create=False):
        self._check(self.root.stat(), directory=True, private=False)
        if create:
            self.directory.mkdir(mode=0o700, exist_ok=True)
        try:
            self._check(self.directory.lstat(), directory=True)
        except FileNotFoundError:
            return False
        return True

    def load(self):
        if not self._directory_exists():
            return None
        try:
            self._check(self.file.lstat())
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            descriptor = os.open(self.file, flags | getattr(os, "O_BINARY", 0))
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as stream:
            self._check(os.fstat(stream.fileno()))
            data = stream.read(_MAX_BYTES + 1)
        if not data.startswith(self.header) or len(data) > _MAX_BYTES:
            raise ValueError("Invalid API credential file.")
        payload = data[len(self.header):]
        key = (_dpapi(payload, self.scope, encrypt=False) if _WINDOWS else payload).decode("ascii")
        if not _KEY.fullmatch(key):
            raise ValueError("Invalid saved API key.")
        return key

    def save(self, key):
        if type(key) is not str or not _KEY.fullmatch(key):
            raise ValueError("Invalid API key.")
        self._directory_exists(create=True)
        try:
            self._check(self.file.lstat())
        except FileNotFoundError:
            pass
        payload = _dpapi(key.encode("ascii"), self.scope, encrypt=True) if _WINDOWS else key.encode("ascii")
        data = self.header + payload
        if len(data) > _MAX_BYTES:
            raise ValueError("API credential file is too large.")
        descriptor, temporary = tempfile.mkstemp(dir=self.directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                self._check(os.fstat(stream.fileno()))
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.file)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def clear(self):
        if self._directory_exists():
            try:
                self._check(self.file.lstat())
                self.file.unlink()
            except FileNotFoundError:
                pass
