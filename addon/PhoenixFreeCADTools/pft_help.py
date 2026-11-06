# SPDX-License-Identifier: Apache-2.0
"""Bounded local help text and link policy; no CAD, Qt, network or processes."""
import os
from pathlib import Path
import stat
import unicodedata
from urllib.parse import urlsplit

MAX_HELP_BYTES = 192 * 1024
_HELP_FILE = Path(__file__).resolve().with_name('HELP.md')
_READ_ERROR = '帮助文档无法读取，请检查插件安装。'


class PftHelpError(RuntimeError):
    pass


def read_help():
    """Read only this module's installed HELP.md, without following a file link."""
    try:
        before = _HELP_FILE.lstat()
        if (not stat.S_ISREG(before.st_mode) or _HELP_FILE.is_symlink()
                or not 0 < before.st_size <= MAX_HELP_BYTES):
            raise ValueError()
        descriptor = os.open(str(_HELP_FILE), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                             | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                    or not 0 < opened.st_size <= MAX_HELP_BYTES):
                raise ValueError()
            data = stream.read(MAX_HELP_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = _HELP_FILE.lstat()
        if (len(data) != opened.st_size or len(data) > MAX_HELP_BYTES
                or not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                or (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                != (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)):
            raise ValueError()
        text = data.decode('utf-8', errors='strict')
        if not text.strip() or '\0' in text:
            raise ValueError()
        return text
    except (OSError, ValueError, UnicodeError):
        raise PftHelpError(_READ_ERROR) from None


def help_link(value):
    """Return an in-page anchor or an explicit HTTPS destination, otherwise None."""
    if (not isinstance(value, str) or not 0 < len(value) <= 2048 or value != value.strip()
            or '\\' in value or any(c.isspace() or unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in value)):
        return None
    if value.startswith('#'):
        return ('anchor', value[1:]) if 1 < len(value) <= 256 else None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)):
            return None
    except ValueError:
        return None
    return 'external', value
