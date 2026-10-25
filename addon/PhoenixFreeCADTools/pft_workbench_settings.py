# SPDX-License-Identifier: Apache-2.0
"""Settings controller: only the injected application's local preferences.

No FreeCAD/Qt imports or document access. Chinese is the default; reading an
unset preference never writes a value. Change the active language only after
the explicit choice has been natively persisted and checked.
"""

from pft_workbench_i18n import LANGUAGES, set_language
from pft_preferences import PftPreferenceError, save_parameter_changes

PREFERENCE_PATH = "User parameter:BaseApp/Preferences/Mod/PhoenixTools"
LANGUAGE_KEY = "Language"
AUTO_START_KEY = "AutoStartMCP"
TOOLBAR_BUTTON_STYLE_KEY = "ToolbarButtonStyle"
TOOLBAR_BUTTON_STYLES = ("icon_text", "icon")
_UNSET = object()


class PftSettingsError(RuntimeError):
    def __init__(self, message, code="WORKBENCH_SETTINGS_ERROR"):
        super().__init__(message)
        self.code, self.message = code, message


def _save_changes(app, changes):
    try:
        save_parameter_changes(app, PREFERENCE_PATH, changes)
    except PftPreferenceError as error:
        messages = {
            "PREFERENCE_PERSIST_UNAVAILABLE": "当前 FreeCAD 无法持久保存工作台设置。",
            "PREFERENCE_SNAPSHOT_UNAVAILABLE": "无法完整读取原工作台设置，未写入设置；尚未切换界面语言。",
            "PREFERENCE_WRITE_FAILED": "工作台设置保存失败；原设置已恢复并保存，尚未切换界面语言。",
            "PREFERENCE_PERSIST_FAILED": "工作台设置未能持久保存，原设置已恢复并保存；尚未切换界面语言。",
            "PREFERENCE_ROLLBACK_UNVERIFIED": "工作台设置保存失败，且部分原设置无法恢复或确认；尚未切换界面语言。",
            "PREFERENCE_PERSIST_RESTORE_UNVERIFIED": "工作台设置保存失败，原设置恢复后的持久保存无法确认；尚未切换界面语言。",
        }
        raise PftSettingsError(messages.get(error.code, "工作台设置保存失败；尚未切换界面语言。"), error.code) from None


def load_language(app):
    try:
        stored = app.ParamGet(PREFERENCE_PATH).GetString(LANGUAGE_KEY, "zh")
    except Exception as error:
        set_language("zh")
        raise PftSettingsError("无法读取语言设置，已使用简体中文。") from error
    language = stored if stored in LANGUAGES else "zh"
    set_language(language)
    return language


def save_language(app, language):
    if type(language) is not str or language not in LANGUAGES:
        raise PftSettingsError("不支持的界面语言。")
    _save_changes(app, (("String", LANGUAGE_KEY, "zh", language),))
    set_language(language)
    return language


def _read_service_options(preference):
    options = {
        "auto_start_mcp": preference.GetBool(AUTO_START_KEY, False),
    }
    if any(type(value) is not bool for value in options.values()):
        raise TypeError("MCP preferences must return bool values")
    return options


def load_service_options(app):
    """Read startup flags without changing language; unavailable storage is safe."""
    try:
        return _read_service_options(app.ParamGet(PREFERENCE_PATH))
    except Exception:
        return {"auto_start_mcp": False}


def _read_toolbar_button_style(preference):
    stored = preference.GetString(TOOLBAR_BUTTON_STYLE_KEY, "icon_text")
    return stored if type(stored) is str and stored in TOOLBAR_BUTTON_STYLES else "icon_text"


def load_toolbar_button_style(app):
    """Read only toolbar presentation; never change language or preferences."""
    try:
        return _read_toolbar_button_style(app.ParamGet(PREFERENCE_PATH))
    except Exception:
        return "icon_text"


def load_options(app):
    """Read the complete settings dialog state without writing preferences."""
    try:
        preference = app.ParamGet(PREFERENCE_PATH)
        stored = preference.GetString(LANGUAGE_KEY, "zh")
        options = _read_service_options(preference)
        toolbar_button_style = _read_toolbar_button_style(preference)
    except Exception as error:
        raise PftSettingsError("无法读取工作台设置。") from error
    language = stored if stored in LANGUAGES else "zh"
    set_language(language)
    return {"language": language, "toolbar_button_style": toolbar_button_style, **options}


def _snapshot(preference, kind, key, default):
    """Keep absence distinct from a stored default when enumeration is available."""
    getter = getattr(preference, "Get" + kind)
    enumerator = getattr(preference, "Get" + kind + "s", None)
    exists = key in enumerator() if callable(enumerator) else None
    return kind, key, exists, getter(key, default)


def _restore(preference, snapshot):
    kind, key, existed, value = snapshot
    if existed is False:
        remover = getattr(preference, "Rem" + kind, None)
        if not callable(remover):
            # Older adapters cannot represent absence; retain the original value.
            getattr(preference, "Set" + kind)(key, value)
            if getattr(preference, "Get" + kind)(key, value) != value:
                raise RuntimeError("Preference rollback did not retain its value")
            return
        remover(key)
        if key in getattr(preference, "Get" + kind + "s")():
            raise RuntimeError("Preference rollback did not remove a new key")
        return
    getattr(preference, "Set" + kind)(key, value)
    restored = getattr(preference, "Get" + kind)(key, value)
    if type(restored) is not type(value) or restored != value:
        raise RuntimeError("Preference rollback did not retain its value")


def save_options(app, language, auto_start_mcp, toolbar_button_style=_UNSET):
    """Flush choices natively; legacy calls leave toolbar presentation intact."""
    if type(language) is not str or language not in LANGUAGES:
        raise PftSettingsError("不支持的界面语言。")
    if type(auto_start_mcp) is not bool:
        raise PftSettingsError("MCP 设置必须使用布尔值。")
    if toolbar_button_style is not _UNSET and (
            type(toolbar_button_style) is not str or toolbar_button_style not in TOOLBAR_BUTTON_STYLES):
        raise PftSettingsError("不支持的工具条显示样式。")
    changes = (
        ("String", LANGUAGE_KEY, "zh", language),
        ("Bool", AUTO_START_KEY, False, auto_start_mcp),
    )
    if toolbar_button_style is not _UNSET:
        changes += (("String", TOOLBAR_BUTTON_STYLE_KEY, "icon_text", toolbar_button_style),)
    _save_changes(app, changes)
    set_language(language)
