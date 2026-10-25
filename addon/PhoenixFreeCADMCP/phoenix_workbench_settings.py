# SPDX-License-Identifier: Apache-2.0
"""Settings controller: only the injected application's local preferences.

No FreeCAD/Qt imports or document access. Chinese is the default; reading an
unset preference never writes a value. Change the active language only after
the explicit choice has been stored and checked.
"""

from phoenix_workbench_i18n import LANGUAGES, set_language

PREFERENCE_PATH = "User parameter:BaseApp/Preferences/Mod/PhoenixTools"
LANGUAGE_KEY = "Language"


class SettingsError(RuntimeError):
    pass


def load_language(app):
    try:
        stored = app.ParamGet(PREFERENCE_PATH).GetString(LANGUAGE_KEY, "zh")
    except Exception as error:
        set_language("zh")
        raise SettingsError("无法读取语言设置，已使用简体中文。") from error
    language = stored if stored in LANGUAGES else "zh"
    set_language(language)
    return language


def save_language(app, language):
    if language not in LANGUAGES:
        raise SettingsError("不支持的界面语言。")
    try:
        preference = app.ParamGet(PREFERENCE_PATH)
        preference.SetString(LANGUAGE_KEY, language)
        if preference.GetString(LANGUAGE_KEY, "") != language:
            raise RuntimeError("Language preference did not retain the requested value")
    except Exception as error:
        raise SettingsError("语言设置保存失败；尚未切换界面语言。") from error
    set_language(language)
    return language
