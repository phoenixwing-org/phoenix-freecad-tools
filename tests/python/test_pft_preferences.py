# SPDX-License-Identifier: Apache-2.0
"""Typed preferences must survive native-save faults without false success."""
from pathlib import Path
import sys
import unittest

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_preferences as preferences
finally:
    sys.path[:] = _path

GROUP = "User parameter:BaseApp/Preferences/Mod/PublicExample"
CHANGES = (("String", "ExampleText", "", "new"), ("Bool", "ExampleEnabled", False, True), ("Int", "ExampleSize", 0, 12))
PRIVATE_EXCEPTION = "/Users/synthetic-private/native/fixture-only.py"


class Values:
    def __init__(self, values=None, enumeration="keys"):
        self.values = {kind: dict((values or {}).get(kind, {})) for kind in ("String", "Bool", "Int")}
        self.events = []
        self.enumeration = enumeration
        self.fail_setter_once = None
        self.ignore = None
        self.fail_remove = None
        self.after_set = None
        self.after_remove = None

    def _get(self, kind, key, default): return self.values[kind].get(key, default)
    def _enum(self, kind):
        mapping = self.values[kind]
        if self.enumeration == "dict": return dict(mapping)
        if self.enumeration == "pairs": return tuple(mapping.items())
        return tuple(mapping)
    def _set(self, kind, key, value):
        self.events.append(("set", kind, key, value))
        if (kind, key) != self.ignore: self.values[kind][key] = value
        if self.after_set: self.after_set(kind, key, value)
        if self.fail_setter_once == (kind, key):
            self.fail_setter_once = None
            raise RuntimeError(PRIVATE_EXCEPTION)
    def _remove(self, kind, key):
        self.events.append(("remove", kind, key))
        if self.fail_remove == (kind, key): raise RuntimeError(PRIVATE_EXCEPTION)
        self.values[kind].pop(key, None)
        if self.after_remove: self.after_remove(kind, key)
    def GetString(self, key, default=""): return self._get("String", key, default)
    def GetBool(self, key, default=False): return self._get("Bool", key, default)
    def GetInt(self, key, default=0): return self._get("Int", key, default)
    def GetStrings(self): return self._enum("String")
    def GetBools(self): return self._enum("Bool")
    def GetInts(self): return self._enum("Int")
    def SetString(self, key, value): self._set("String", key, value)
    def SetBool(self, key, value): self._set("Bool", key, value)
    def SetInt(self, key, value): self._set("Int", key, value)
    def RemString(self, key): self._remove("String", key)
    def RemBool(self, key): self._remove("Bool", key)
    def RemInt(self, key): self._remove("Int", key)


class App:
    def __init__(self, values):
        self.values = values
        self.groups, self.saves = [], []
        self.disk = self.copy_values()
        self.fail_saves = set()
        self.return_values = {}
        self.after_save = None
    def copy_values(self): return {kind: dict(items) for kind, items in self.values.values.items()}
    def ParamGet(self, group): self.groups.append(group); return self.values
    def saveParameter(self, group):
        self.saves.append(group)
        self.disk = self.copy_values()  # A failing native save may already have written.
        if self.after_save: self.after_save(len(self.saves))
        if len(self.saves) in self.fail_saves: raise RuntimeError(PRIVATE_EXCEPTION)
        return self.return_values.get(len(self.saves))
    @property
    def ActiveDocument(self): raise AssertionError("preferences cannot access CAD")


class PreferencePersistenceTests(unittest.TestCase):
    def assert_code(self, code, call):
        with self.assertRaises(preferences.PftPreferenceError) as failure: call()
        self.assertEqual(failure.exception.code, code)
        self.assertNotIn(PRIVATE_EXCEPTION, str(failure.exception))
        self.assertIsNone(failure.exception.__cause__)
        return failure.exception

    def save(self, app, changes=CHANGES): return preferences.save_parameter_changes(app, GROUP, changes)

    def test_native_save_after_full_ram_verification_and_preserves_unrelated_values(self):
        values = Values({"String": {"Unrelated": "preserve"}})
        app = App(values)
        def verify_at_save(number):
            self.assertEqual(number, 1)
            self.assertEqual(values.values, {"String": {"Unrelated": "preserve", "ExampleText": "new"},
                "Bool": {"ExampleEnabled": True}, "Int": {"ExampleSize": 12}})
        app.after_save = verify_at_save
        self.assertIsNone(self.save(app))
        self.assertEqual(app.saves, ["User parameter"])
        self.assertEqual(app.disk, app.copy_values())
        self.assertEqual(app.groups, [GROUP])

    def test_missing_native_save_refuses_before_preference_access(self):
        values = Values(); app = App(values); app.saveParameter = None
        self.assert_code("PREFERENCE_PERSIST_UNAVAILABLE", lambda: self.save(app))
        self.assertEqual(app.groups, []); self.assertEqual(values.events, [])

    def test_invalid_typed_requests_are_rejected_before_preference_access(self):
        for changes in ((), (("Bool", "Flag", False, 1),), (("Int", "Size", 0, True),),
                        (("Int", "Size", 0, 2147483648),), (("Float", "Size", 0.0, 1.0),),
                        (("String", "Same", "", "a"), ("Bool", "Same", False, True)),
                        (("String", "../bad", "", "a"),)):
            with self.subTest(changes=changes):
                app = App(Values())
                self.assert_code("PREFERENCE_INVALID_CHANGES", lambda: self.save(app, changes))
                self.assertEqual(app.groups, []); self.assertEqual(app.values.events, [])

    def test_snapshots_require_exact_enumeration_and_removal_before_any_write(self):
        for failure in ("missing_enum", "missing_remove", "bad_enum", "bad_type", "read_error"):
            with self.subTest(failure=failure):
                values = Values(); app = App(values)
                if failure == "missing_enum": values.GetInts = None
                if failure == "missing_remove": values.RemInt = None
                if failure == "bad_enum": values.GetStrings = lambda: "ExampleText"
                if failure == "bad_type": values.GetBool = lambda *args: 0
                if failure == "read_error": values.GetStrings = lambda: (_ for _ in ()).throw(RuntimeError(PRIVATE_EXCEPTION))
                self.assert_code("PREFERENCE_SNAPSHOT_UNAVAILABLE", lambda: self.save(app))
                self.assertEqual(values.events, []); self.assertEqual(app.saves, [])

    def test_key_dictionary_tuple_and_pair_enumerations_all_restore_absence_exactly(self):
        for form in ("keys", "dict", "pairs"):
            with self.subTest(form=form):
                values = Values({"String": {"ExampleText": "old"}}, enumeration=form); app = App(values)
                values.fail_setter_once = ("Int", "ExampleSize")
                before = app.copy_values()
                self.assert_code("PREFERENCE_WRITE_FAILED", lambda: self.save(app))
                self.assertEqual(app.copy_values(), before); self.assertEqual(app.disk, before)
                self.assertEqual(app.saves, ["User parameter"])

    def test_each_mutating_setter_failure_restores_complete_original_scope_once(self):
        for kind, key, _, _ in CHANGES:
            with self.subTest(kind=kind):
                values = Values({"String": {"ExampleText": "old"}, "Bool": {"ExampleEnabled": False}})
                app = App(values); before = app.copy_values(); values.fail_setter_once = (kind, key)
                self.assert_code("PREFERENCE_WRITE_FAILED", lambda: self.save(app))
                self.assertEqual(app.copy_values(), before); self.assertEqual(app.disk, before)
                self.assertEqual(app.saves, ["User parameter"])

    def test_failing_first_setter_also_restores_an_unattempted_selected_key_changed_by_callback(self):
        values = Values({"Int": {"ExampleSize": 8}}); app = App(values)
        before = app.copy_values(); values.fail_setter_once = ("String", "ExampleText")
        def change_later_key(kind, key, value):
            if kind == "String" and value == "new": values.values["Int"]["ExampleSize"] = 99
        values.after_set = change_later_key
        self.assert_code("PREFERENCE_WRITE_FAILED", lambda: self.save(app))
        self.assertEqual(app.copy_values(), before)
        self.assertEqual(app.disk, before)
        self.assertEqual(app.saves, ["User parameter"])

    def test_ignored_default_values_cannot_masquerade_as_present_settings(self):
        for kind, key, default in (("String", "Text", ""), ("Bool", "Flag", False), ("Int", "Size", 0)):
            with self.subTest(kind=kind):
                values = Values(); values.ignore = (kind, key); app = App(values)
                self.assert_code("PREFERENCE_WRITE_FAILED", lambda: self.save(app, ((kind, key, default, default),)))
                self.assertEqual(app.disk, {"String": {}, "Bool": {}, "Int": {}})
                self.assertEqual(app.saves, ["User parameter"])

    def test_first_native_save_can_write_then_fail_and_requires_restored_save(self):
        values = Values({"String": {"ExampleText": "old"}}); app = App(values)
        before = app.copy_values(); app.fail_saves = {1}
        self.assert_code("PREFERENCE_PERSIST_FAILED", lambda: self.save(app))
        self.assertEqual(app.saves, ["User parameter", "User parameter"])
        self.assertEqual(app.disk, before); self.assertEqual(app.copy_values(), before)

    def test_unexpected_native_result_is_not_treated_as_success(self):
        for result in (True, False, 0, "saved"):
            with self.subTest(result=result):
                app = App(Values()); app.return_values[1] = result
                self.assert_code("PREFERENCE_PERSIST_FAILED", lambda: self.save(app))
                self.assertEqual(len(app.saves), 2)
                self.assertEqual(app.disk, {"String": {}, "Bool": {}, "Int": {}})

    def test_restore_save_failure_is_not_retried_or_reported_durable(self):
        app = App(Values()); app.fail_saves = {1, 2}
        self.assert_code("PREFERENCE_PERSIST_RESTORE_UNVERIFIED", lambda: self.save(app))
        self.assertEqual(len(app.saves), 2)
        self.assertEqual(app.copy_values(), {"String": {}, "Bool": {}, "Int": {}})

    def test_partial_ram_restore_is_never_flushed(self):
        values = Values(); app = App(values); app.fail_saves = {1}; values.fail_remove = ("Bool", "ExampleEnabled")
        self.assert_code("PREFERENCE_ROLLBACK_UNVERIFIED", lambda: self.save(app))
        self.assertEqual(len(app.saves), 1)
        self.assertEqual(values.values["Bool"], {"ExampleEnabled": True})
        self.assertEqual(app.disk["String"], {"ExampleText": "new"})

    def test_rollback_final_readback_detects_callback_that_changes_an_already_restored_key(self):
        values = Values(); app = App(values); values.fail_setter_once = ("Int", "ExampleSize")
        def change_restored_key(kind, key):
            if kind == "String": values.values["Int"]["ExampleSize"] = 99
        values.after_remove = change_restored_key
        self.assert_code("PREFERENCE_ROLLBACK_UNVERIFIED", lambda: self.save(app))
        self.assertEqual(app.saves, [])
        self.assertEqual(values.values["Int"], {"ExampleSize": 99})

    def test_post_save_callback_mutation_is_detected_and_original_settings_persisted(self):
        values = Values(); app = App(values)
        def mutate_first_save(number):
            if number == 1: values.values["String"]["ExampleText"] = "unexpected"
        app.after_save = mutate_first_save
        self.assert_code("PREFERENCE_PERSIST_FAILED", lambda: self.save(app))
        self.assertEqual(len(app.saves), 2)
        self.assertEqual(app.disk, {"String": {}, "Bool": {}, "Int": {}})

    def test_mutation_after_restored_save_is_unverified_and_not_saved_again(self):
        values = Values(); app = App(values); app.fail_saves = {1}
        def mutate_restored_save(number):
            if number == 2: values.values["String"]["ExampleText"] = "unexpected after restore"
        app.after_save = mutate_restored_save
        self.assert_code("PREFERENCE_PERSIST_RESTORE_UNVERIFIED", lambda: self.save(app))
        self.assertEqual(len(app.saves), 2)
        self.assertEqual(values.values["String"], {"ExampleText": "unexpected after restore"})


if __name__ == "__main__":
    unittest.main()
