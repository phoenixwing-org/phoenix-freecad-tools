# SPDX-License-Identifier: Apache-2.0
"""Service settings defaults, atomic persistence and precise absence rollback."""
from pathlib import Path
import sys
import types
import unittest

ADDON = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools"
_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_workbench_i18n as i18n
    import pft_workbench_settings as settings
finally:
    sys.path[:] = _path


class Preferences:
    def __init__(self, strings=None, bools=None):
        self.strings = dict(strings or {})
        self.bools = dict(bools or {})
        self.reads = []
        self.writes = []
        self.removals = []
        self.fail_key = None
        self.ignore_key = None
        self.fail_removal = False
        self.on_write = None

    def GetString(self, key, default=""):
        self.reads.append(("String", key))
        return self.strings.get(key, default)

    def GetBool(self, key, default=False):
        self.reads.append(("Bool", key))
        return self.bools.get(key, default)

    def GetStrings(self):
        return list(self.strings)

    def GetBools(self):
        return list(self.bools)

    def _set(self, values, key, value):
        self.writes.append((key, value))
        if self.on_write:
            self.on_write()
        if key != self.ignore_key:
            values[key] = value
        if key == self.fail_key:
            self.fail_key = None
            raise RuntimeError("setter failed after mutation")

    def SetString(self, key, value):
        self._set(self.strings, key, value)

    def SetBool(self, key, value):
        self._set(self.bools, key, value)

    def RemString(self, key):
        self.removals.append(("String", key))
        if self.fail_removal:
            raise RuntimeError("removal failed")
        self.strings.pop(key, None)

    def RemBool(self, key):
        self.removals.append(("Bool", key))
        if self.fail_removal:
            raise RuntimeError("removal failed")
        self.bools.pop(key, None)


class App:
    def __init__(self, preferences):
        self.preferences = preferences
        self.groups = []
        self.persist_calls = []
        self.disk_strings = dict(preferences.strings)
        self.disk_bools = dict(preferences.bools)
        self.on_persist = None

    def saveParameter(self, group="User parameter"):
        self.persist_calls.append(group)
        self.disk_strings = dict(self.preferences.strings)
        self.disk_bools = dict(self.preferences.bools)
        if self.on_persist:
            self.on_persist()

    def ParamGet(self, group):
        self.groups.append(group)
        return self.preferences

    @property
    def ActiveDocument(self):
        raise AssertionError("settings must not inspect a document")


class WorkbenchSettingsFlagsTests(unittest.TestCase):
    def setUp(self):
        self.previous_language = i18n.get_language()
        self.addCleanup(i18n.set_language, self.previous_language)
        i18n.set_language("zh")
        self.preferences = Preferences()
        self.app = App(self.preferences)

    def test_unset_full_options_have_safe_defaults_and_do_not_write(self):
        self.assertEqual(settings.load_options(self.app), {
            "language": "zh", "toolbar_button_style": "icon_text", "auto_start_mcp": False})
        self.assertEqual(self.preferences.writes, [])
        self.assertEqual(self.preferences.strings, {})
        self.assertEqual(self.preferences.bools, {})
        self.assertEqual(self.app.groups, [settings.PREFERENCE_PATH])

    def test_full_options_load_language_and_startup_flag(self):
        self.preferences.strings[settings.LANGUAGE_KEY] = "en"
        self.preferences.bools[settings.AUTO_START_KEY] = True
        self.assertEqual(settings.load_options(self.app), {
            "language": "en", "toolbar_button_style": "icon_text", "auto_start_mcp": True})
        self.assertEqual(i18n.get_language(), "en")
        self.assertEqual(self.preferences.writes, [])

    def test_toolbar_reader_is_read_only_and_independent_of_language_and_startup(self):
        i18n.set_language("en")
        for stored, expected in ((None, "icon_text"), ("icon_text", "icon_text"), ("icon", "icon"),
                                 ("invalid", "icon_text"), (17, "icon_text"), (["icon"], "icon_text")):
            with self.subTest(stored=stored):
                preferences = Preferences(strings={} if stored is None else {settings.TOOLBAR_BUTTON_STYLE_KEY: stored})
                self.assertEqual(settings.load_toolbar_button_style(App(preferences)), expected)
                self.assertEqual(i18n.get_language(), "en")
                self.assertEqual(preferences.reads, [("String", settings.TOOLBAR_BUTTON_STYLE_KEY)])
                self.assertEqual(preferences.writes, [])
        self.assertEqual(settings.load_toolbar_button_style(types.SimpleNamespace()), "icon_text")
        self.assertEqual(i18n.get_language(), "en")

    def test_full_options_load_stored_toolbar_style_and_normalize_invalid_values(self):
        for stored, expected in (("icon", "icon"), ("unknown", "icon_text")):
            self.preferences.strings[settings.TOOLBAR_BUTTON_STYLE_KEY] = stored
            self.assertEqual(settings.load_options(self.app)["toolbar_button_style"], expected)
        self.assertEqual(self.preferences.writes, [])

    def test_service_options_do_not_change_language(self):
        i18n.set_language("en")
        self.assertEqual(settings.load_service_options(self.app), {
            "auto_start_mcp": False})
        self.assertEqual(i18n.get_language(), "en")
        self.assertEqual(self.preferences.writes, [])

    def test_service_defaults_when_application_has_no_preferences(self):
        self.assertEqual(settings.load_service_options(types.SimpleNamespace()), {
            "auto_start_mcp": False})

    def test_failed_service_read_uses_safe_startup_default(self):
        class BrokenPreferences(Preferences):
            def GetBool(self, key, default=False):
                raise RuntimeError("read failed")
        self.assertEqual(settings.load_service_options(App(BrokenPreferences())), {
            "auto_start_mcp": False})

    def test_full_read_failure_preserves_current_language_and_raises(self):
        i18n.set_language("en")
        with self.assertRaises(settings.PftSettingsError):
            settings.load_options(types.SimpleNamespace())
        self.assertEqual(i18n.get_language(), "en")

    def test_unknown_stored_language_normalizes_without_writing(self):
        self.preferences.strings[settings.LANGUAGE_KEY] = "invalid"
        self.assertEqual(settings.load_options(self.app)["language"], "zh")
        self.assertEqual(self.preferences.strings, {settings.LANGUAGE_KEY: "invalid"})
        self.assertEqual(self.preferences.writes, [])

    def test_non_boolean_storage_is_rejected_by_full_read(self):
        self.preferences.bools[settings.AUTO_START_KEY] = 1
        with self.assertRaises(settings.PftSettingsError):
            settings.load_options(self.app)
        self.assertEqual(settings.load_service_options(self.app), {
            "auto_start_mcp": False})

    def test_save_both_choices_before_switching_language(self):
        self.preferences.on_write = lambda: self.assertEqual(i18n.get_language(), "zh")
        self.app.on_persist = lambda: self.assertEqual(i18n.get_language(), "zh")
        self.assertIsNone(settings.save_options(self.app, "en", True))
        self.assertEqual(self.preferences.strings, {settings.LANGUAGE_KEY: "en"})
        self.assertEqual(self.preferences.bools, {settings.AUTO_START_KEY: True})
        self.assertEqual(self.preferences.writes, [
            (settings.LANGUAGE_KEY, "en"), (settings.AUTO_START_KEY, True)])
        self.assertEqual(self.app.groups, [settings.PREFERENCE_PATH])
        self.assertEqual(self.app.persist_calls, ["User parameter"])
        self.assertEqual(self.app.disk_strings, self.preferences.strings)
        self.assertEqual(self.app.disk_bools, self.preferences.bools)
        self.assertEqual(i18n.get_language(), "en")

    def test_save_toolbar_style_is_verified_with_language_and_startup(self):
        self.preferences.on_write = lambda: self.assertEqual(i18n.get_language(), "zh")
        settings.save_options(self.app, "en", True, "icon")
        self.assertEqual(self.preferences.strings, {
            settings.LANGUAGE_KEY: "en", settings.TOOLBAR_BUTTON_STYLE_KEY: "icon"})
        self.assertEqual(self.preferences.bools, {settings.AUTO_START_KEY: True})
        self.assertEqual(settings.load_toolbar_button_style(self.app), "icon")
        self.assertEqual(i18n.get_language(), "en")

    def test_legacy_save_preserves_existing_toolbar_choice(self):
        self.preferences.strings[settings.TOOLBAR_BUTTON_STYLE_KEY] = "icon"
        settings.save_options(self.app, "en", False)
        self.assertEqual(self.preferences.strings[settings.TOOLBAR_BUTTON_STYLE_KEY], "icon")
        self.assertNotIn((settings.TOOLBAR_BUTTON_STYLE_KEY, "icon"), self.preferences.writes)

    def test_invalid_toolbar_choice_has_no_preference_or_language_effect(self):
        for value in (None, 1, True, "", "icon-only", ["icon"]):
            with self.subTest(value=value), self.assertRaises(settings.PftSettingsError):
                settings.save_options(self.app, "en", True, value)
        self.assertEqual(self.app.groups, [])
        self.assertEqual(self.preferences.writes, [])
        self.assertEqual(i18n.get_language(), "zh")

    def test_toolbar_write_failure_or_ignored_write_rolls_back_all_choices(self):
        for initial_style in (None, "icon_text"):
            for failure in ("fail_key", "ignore_key"):
                with self.subTest(initial_style=initial_style, failure=failure):
                    originals = {settings.LANGUAGE_KEY: "zh"}
                    if initial_style is not None:
                        originals[settings.TOOLBAR_BUTTON_STYLE_KEY] = initial_style
                    preferences = Preferences(strings=originals, bools={settings.AUTO_START_KEY: False})
                    setattr(preferences, failure, settings.TOOLBAR_BUTTON_STYLE_KEY)
                    with self.assertRaises(settings.PftSettingsError):
                        settings.save_options(App(preferences), "en", True, "icon")
                    self.assertEqual(preferences.strings, originals)
                    self.assertEqual(preferences.bools, {settings.AUTO_START_KEY: False})
                    self.assertEqual(i18n.get_language(), "zh")

    def test_ignored_toolbar_default_write_is_not_mistaken_for_stored_choice(self):
        self.preferences.ignore_key = settings.TOOLBAR_BUTTON_STYLE_KEY
        with self.assertRaises(settings.PftSettingsError):
            settings.save_options(self.app, "en", False, "icon_text")
        self.assertEqual(self.preferences.strings, {})
        self.assertEqual(self.preferences.bools, {})
        self.assertEqual(i18n.get_language(), "zh")

    def test_invalid_requested_values_never_read_or_write_preferences(self):
        for language, auto_start in (
            ("xx", False), ("en", 1), ("en", 0),
            ("en", "false"), ("en", None),
        ):
            with self.subTest(values=(language, auto_start)):
                with self.assertRaises(settings.PftSettingsError):
                    settings.save_options(self.app, language, auto_start)
        self.assertEqual(self.app.groups, [])
        self.assertEqual(self.preferences.writes, [])

    def test_each_failing_setter_restores_all_originally_missing_keys(self):
        for key in (settings.LANGUAGE_KEY, settings.AUTO_START_KEY):
            with self.subTest(key=key):
                preferences = Preferences()
                preferences.fail_key = key
                with self.assertRaises(settings.PftSettingsError):
                    settings.save_options(App(preferences), "en", True)
                self.assertEqual(preferences.strings, {})
                self.assertEqual(preferences.bools, {})
                self.assertEqual(i18n.get_language(), "zh")

    def test_partial_failure_restores_existing_values_and_new_key_absence(self):
        for original_bools in ({}, {settings.AUTO_START_KEY: False}):
            with self.subTest(original_bools=original_bools):
                preferences = Preferences(
                    strings={settings.LANGUAGE_KEY: "unknown-original"},
                    bools=original_bools)
                preferences.fail_key = settings.AUTO_START_KEY
                with self.assertRaises(settings.PftSettingsError):
                    settings.save_options(App(preferences), "en", True)
                self.assertEqual(preferences.strings, {
                    settings.LANGUAGE_KEY: "unknown-original"})
                self.assertEqual(preferences.bools, original_bools)
                self.assertEqual(i18n.get_language(), "zh")

    def test_silently_ignored_writes_detected_even_for_requested_defaults(self):
        for key in (settings.LANGUAGE_KEY, settings.AUTO_START_KEY):
            with self.subTest(key=key):
                preferences = Preferences()
                preferences.ignore_key = key
                with self.assertRaises(settings.PftSettingsError):
                    settings.save_options(App(preferences), "zh", False)
                self.assertEqual(preferences.strings, {})
                self.assertEqual(preferences.bools, {})

    def test_snapshot_failure_causes_zero_writes(self):
        self.preferences.GetBools = lambda: (_ for _ in ()).throw(RuntimeError("snapshot failed"))
        with self.assertRaises(settings.PftSettingsError):
            settings.save_options(self.app, "en", True)
        self.assertEqual(self.preferences.writes, [])
        self.assertEqual(self.preferences.removals, [])
        self.assertEqual(i18n.get_language(), "zh")

    def test_incomplete_rollback_is_reported_without_switching_language(self):
        self.preferences.fail_key = settings.AUTO_START_KEY
        self.preferences.fail_removal = True
        with self.assertRaisesRegex(settings.PftSettingsError, "部分原设置无法恢复"):
            settings.save_options(self.app, "en", True)
        self.assertEqual(i18n.get_language(), "zh")

    def test_retired_confirmation_preference_is_ignored_by_both_readers(self):
        # Use the historical literal, independent of the removed settings API.
        legacy_key = "ConfirmStopMCP"
        for value in (False, 0, "not-a-boolean", None):
            with self.subTest(legacy_value=value):
                i18n.set_language("zh")
                preferences = Preferences(
                    strings={settings.LANGUAGE_KEY: "en"},
                    bools={settings.AUTO_START_KEY: True, legacy_key: value})
                app = App(preferences)
                self.assertEqual(settings.load_service_options(app), {
                    "auto_start_mcp": True})
                self.assertEqual(i18n.get_language(), "zh")
                self.assertEqual(settings.load_options(app), {
                    "language": "en", "toolbar_button_style": "icon_text", "auto_start_mcp": True})
                self.assertEqual(i18n.get_language(), "en")
                self.assertIs(preferences.bools[legacy_key], value)
                self.assertEqual(set(preferences.reads), {
                    ("Bool", settings.AUTO_START_KEY),
                    ("String", settings.LANGUAGE_KEY),
                    ("String", settings.TOOLBAR_BUTTON_STYLE_KEY)})
                self.assertEqual(preferences.writes, [])
                self.assertEqual(preferences.removals, [])

    def test_retired_confirmation_preference_survives_saving_and_rollback(self):
        legacy_key = "ConfirmStopMCP"
        for value in (False, 0, "not-a-boolean", None):
            for failure in (False, True):
                with self.subTest(legacy_value=value, setter_failure=failure):
                    i18n.set_language("zh")
                    preferences = Preferences(bools={legacy_key: value})
                    if failure:
                        preferences.fail_key = settings.AUTO_START_KEY
                        with self.assertRaises(settings.PftSettingsError):
                            settings.save_options(App(preferences), "en", True)
                        self.assertEqual(preferences.strings, {})
                        self.assertEqual(preferences.bools, {legacy_key: value})
                        self.assertEqual(i18n.get_language(), "zh")
                    else:
                        settings.save_options(App(preferences), "en", True)
                        self.assertEqual(preferences.strings, {
                            settings.LANGUAGE_KEY: "en"})
                        self.assertEqual(preferences.bools, {
                            settings.AUTO_START_KEY: True, legacy_key: value})
                        self.assertEqual(i18n.get_language(), "en")
                    self.assertIs(preferences.bools[legacy_key], value)
                    self.assertFalse(any(key == legacy_key
                                         for _, key in preferences.reads))
                    self.assertFalse(any(key == legacy_key
                                         for key, _ in preferences.writes))
                    self.assertFalse(any(key == legacy_key
                                         for _, key in preferences.removals))

    def test_unavailable_native_save_refuses_without_ram_or_language_changes(self):
        self.app.saveParameter = None
        with self.assertRaises(settings.PftSettingsError) as failure:
            settings.save_options(self.app, "en", True, "icon")
        self.assertEqual(failure.exception.code, "PREFERENCE_PERSIST_UNAVAILABLE")
        self.assertEqual(self.app.groups, [])
        self.assertEqual(self.preferences.writes, [])
        self.assertEqual(i18n.get_language(), "zh")

    def test_failed_native_save_restores_and_saves_old_choices_without_language_switch(self):
        native_save = self.app.saveParameter
        def failing_first_save(group):
            native_save(group)
            if len(self.app.persist_calls) == 1:
                raise RuntimeError("/Users/synthetic-private/native/fixture-only.py")
        self.app.saveParameter = failing_first_save
        with self.assertRaises(settings.PftSettingsError) as failure:
            settings.save_options(self.app, "en", True, "icon")
        self.assertEqual(failure.exception.code, "PREFERENCE_PERSIST_FAILED")
        self.assertNotIn("synthetic-private", str(failure.exception))
        self.assertEqual(self.app.persist_calls, ["User parameter", "User parameter"])
        self.assertEqual(self.preferences.strings, {})
        self.assertEqual(self.preferences.bools, {})
        self.assertEqual(self.app.disk_strings, {})
        self.assertEqual(self.app.disk_bools, {})
        self.assertEqual(i18n.get_language(), "zh")

    def test_legacy_language_api_stays_available(self):
        self.assertEqual(settings.save_language(self.app, "en"), "en")
        self.assertEqual(settings.load_language(self.app), "en")
        self.assertEqual(self.preferences.bools, {})


if __name__ == "__main__":
    unittest.main()
