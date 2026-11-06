# SPDX-License-Identifier: Apache-2.0
"""Typed native user-parameter persistence, with one verified restoration.

No FreeCAD/Qt imports, CAD access, files, process actions or preference reads at
import time. App.saveParameter saves the whole user parameter set, not only the
selected group. A native success return proves the API accepted the save; it is
not an fsync or independent disk-content guarantee.
"""

import re


class PftPreferenceError(RuntimeError):
    """Fixed public failure code/message; native exceptions are never exposed."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


_KINDS = {"String": str, "Bool": bool, "Int": int}
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}\Z")


def _typed(kind, value):
    return type(value) is _KINDS[kind] and (kind != "Int" or -2147483648 <= value <= 2147483647)


def _changes(preference_path, changes):
    if (not isinstance(preference_path, str) or not preference_path.startswith("User parameter:")
            or len(preference_path) > 1024 or any(ord(char) < 32 for char in preference_path)
            or not isinstance(changes, (tuple, list)) or not 1 <= len(changes) <= 32):
        raise PftPreferenceError("PREFERENCE_INVALID_CHANGES", "Invalid typed user-parameter changes.")
    result, keys = [], set()
    for change in changes:
        if not isinstance(change, (tuple, list)) or len(change) != 4:
            raise PftPreferenceError("PREFERENCE_INVALID_CHANGES", "Invalid typed user-parameter changes.")
        kind, key, default, value = change
        if (not isinstance(kind, str) or kind not in _KINDS or not isinstance(key, str)
                or not _KEY.fullmatch(key) or key in keys or not _typed(kind, default) or not _typed(kind, value)):
            raise PftPreferenceError("PREFERENCE_INVALID_CHANGES", "Invalid typed user-parameter changes.")
        keys.add(key)
        result.append((kind, key, default, value))
    return tuple(result)


def _enumerated_keys(preference, kind):
    values = getattr(preference, "Get" + kind + "s")()
    if isinstance(values, dict):
        keys = tuple(values)
    elif isinstance(values, (list, tuple)):
        if all(isinstance(item, str) for item in values):
            keys = tuple(values)
        elif all(isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[0], str) for item in values):
            keys = tuple(item[0] for item in values)
        else:
            raise ValueError("Unsupported parameter enumeration")
    else:
        raise ValueError("Unsupported parameter enumeration")
    if any(not isinstance(key, str) for key in keys) or len(set(keys)) != len(keys):
        raise ValueError("Ambiguous parameter enumeration")
    return frozenset(keys)


def _snapshot(preference, kind, key, default):
    for method in ("Get" + kind, "Get" + kind + "s", "Set" + kind, "Rem" + kind):
        if not callable(getattr(preference, method, None)):
            raise ValueError("Exact parameter restoration API unavailable")
    existed = key in _enumerated_keys(preference, kind)
    value = getattr(preference, "Get" + kind)(key, default)
    if not _typed(kind, value):
        raise ValueError("Unexpected native parameter value type")
    return kind, key, existed, value


def _verify(preference, snapshots):
    for kind, key, existed, value in snapshots:
        if (key in _enumerated_keys(preference, kind)) is not existed:
            raise ValueError("Parameter presence does not match")
        if existed:
            current = getattr(preference, "Get" + kind)(key, value)
            if not _typed(kind, current) or current != value:
                raise ValueError("Parameter value does not match")


def _persist(persist):
    if persist("User parameter") is not None:
        raise ValueError("Unexpected native persistence return")


def save_parameter_changes(app, preference_path, changes):
    """Save bounded (kind,key,default,new_value) entries or raise a fixed error.

    Exact key presence is mandatory. No missing-enumeration/removal fallback is
    allowed. Snapshot failure causes zero writes. Once a setter is attempted,
    failure restores all selected entries, verifies the complete original RAM
    state, and only then flushes that restored state. A partial/unknown RAM
    restoration is never flushed, and restoration is never blindly repeated.
    """
    changes = _changes(preference_path, changes)
    try:
        persist = getattr(app, "saveParameter", None)
    except Exception:
        persist = None
    if not callable(persist):
        raise PftPreferenceError("PREFERENCE_PERSIST_UNAVAILABLE", "Native user-parameter persistence is unavailable.")
    try:
        preference = app.ParamGet(preference_path)
        before = tuple(_snapshot(preference, kind, key, default) for kind, key, default, _ in changes)
        _verify(preference, before)
    except Exception:
        raise PftPreferenceError("PREFERENCE_SNAPSHOT_UNAVAILABLE", "Original parameter values and key presence could not be verified. No value was written.") from None
    after = tuple((kind, key, True, value) for kind, key, _, value in changes)
    attempted, persistence_attempted = False, False
    try:
        for kind, key, _, value in changes:
            attempted = True  # A native setter can mutate before raising.
            getattr(preference, "Set" + kind)(key, value)
        _verify(preference, after)
        persistence_attempted = True
        _persist(persist)
        _verify(preference, after)
    except Exception:
        if not attempted:
            raise PftPreferenceError("PREFERENCE_WRITE_FAILED", "Parameter changes were not saved.") from None
        rollback_failed = False
        for kind, key, existed, value in reversed(before):
            try:
                if existed:
                    getattr(preference, "Set" + kind)(key, value)
                else:
                    getattr(preference, "Rem" + kind)(key)
            except Exception:
                rollback_failed = True
        try:
            _verify(preference, before)
        except Exception:
            rollback_failed = True
        if rollback_failed:
            raise PftPreferenceError("PREFERENCE_ROLLBACK_UNVERIFIED", "Parameter changes failed and full restoration could not be verified. The partial restoration was not saved.") from None
        try:
            _persist(persist)
            _verify(preference, before)
        except Exception:
            raise PftPreferenceError("PREFERENCE_PERSIST_RESTORE_UNVERIFIED", "Original RAM values were restored, but persistence of the restored settings could not be verified.") from None
        if persistence_attempted:
            raise PftPreferenceError("PREFERENCE_PERSIST_FAILED", "Requested settings could not be persisted. Original settings were restored and saved.") from None
        raise PftPreferenceError("PREFERENCE_WRITE_FAILED", "Requested settings could not be written. Original settings were restored and saved.") from None
