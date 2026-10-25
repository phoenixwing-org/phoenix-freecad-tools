# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import types

ADDON = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools"
_saved = sys.path[:]
try:
    sys.path.insert(0, str(ADDON))
    import pft_chat_settings as settings
    import pft_desk_service  # Pure validation module; no process or service action.
finally:
    sys.path[:] = _saved


class Preference:
    def __init__(self): self.values, self.writes, self.fail = {}, [], None
    def GetString(self, key, default=""): return self.values.get(key, default)
    def GetBool(self, key, default=False): return self.values.get(key, default)
    def GetInt(self, key, default=0): return self.values.get(key, default)
    def GetStrings(self): return [key for key, value in self.values.items() if isinstance(value, str)]
    def GetBools(self): return [key for key, value in self.values.items() if type(value) is bool]
    def GetInts(self): return [key for key, value in self.values.items() if type(value) is int]
    def SetString(self, key, value): self._set(key, value)
    def SetBool(self, key, value): self._set(key, value)
    def SetInt(self, key, value): self._set(key, value)
    def RemString(self, key): self.values.pop(key, None)
    def RemBool(self, key): self.values.pop(key, None)
    def RemInt(self, key): self.values.pop(key, None)
    def _set(self, key, value):
        self.values[key] = value; self.writes.append(key)
        if self.fail == key:
            self.fail = None
            raise RuntimeError("synthetic write failure")


class App:
    def __init__(self):
        self.preference, self.groups, self.persisted = Preference(), [], []
    def saveParameter(self, group):
        if group != "User parameter": raise AssertionError("unexpected parameter scope")
        self.persisted.append(dict(self.preference.values))
    def ParamGet(self, group): self.groups.append(group); return self.preference
    @property
    def ActiveDocument(self): raise AssertionError("settings must not read CAD")


class ChatSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.node = self.root / "node"; self.node.write_text("fixture"); self.node.chmod(0o700)
        package = self.root / "package"; (package / "dist").mkdir(parents=True)
        (package / "package.json").write_text(json.dumps({"name": "phoenix-freecad-mcp", "version": "0.6.0"}))
        self.cli = package / "dist/cli.js"; self.cli.write_text("fixture")
        self.app = App()

    def test_unset_and_failed_read_are_disabled_without_writes(self):
        expected = {"enabled": False, "desk_enabled": False, "node_executable": "", "cli_entry": ""}
        self.assertEqual(settings.load_chat_options(self.app), expected)
        self.assertEqual(self.app.preference.writes, [])
        self.app.ParamGet = lambda group: (_ for _ in ()).throw(RuntimeError())
        self.assertEqual(settings.load_chat_options(self.app), expected)

    def test_explicit_save_and_build_use_only_own_preferences_and_running_bridge(self):
        options = settings.save_chat_options(self.app, True, str(self.node), str(self.cli))
        self.assertEqual(settings.load_chat_options(self.app), options)
        self.assertEqual(set(self.app.groups), {settings.PREFERENCE_PATH})
        mcp = settings.build_chat_mcp_config(options, {"running": True, "port": 9876})
        self.assertEqual(mcp, {"node_executable": str(self.node.resolve()), "cli_entry": str(self.cli.resolve()), "bridge_url": "http://127.0.0.1:9876/"})
        self.assertIsNone(settings.build_chat_mcp_config({"enabled": False}, None))
        for status in ({}, {"running": False}, {"running": True, "stopping": True}, {"running": True, "port": True}, {"running": True, "port": 65536}):
            with self.subTest(status=status), self.assertRaises(settings.PftChatSettingsError): settings.build_chat_mcp_config(options, status)

    def test_invalid_or_unbuilt_paths_are_rejected_before_preference_writes(self):
        for node, cli in (("node", str(self.cli)), (str(self.node), "dist/cli.js"), (str(self.node), str(self.root)),
                          (str(self.node) + "\u202e", str(self.cli))):
            with self.subTest(node=node, cli=cli), self.assertRaises(settings.PftChatSettingsError):
                settings.save_chat_options(self.app, True, node, cli)
        self.assertEqual(self.app.preference.writes, [])
        self.node.chmod(0o600)
        with self.assertRaises(settings.PftChatSettingsError): settings.validate_mcp_paths(str(self.node), str(self.cli))
        self.node.chmod(0o700)
        (self.cli.parent.parent / "package.json").write_text('{"name":"unrelated-package"}')
        with self.assertRaises(settings.PftChatSettingsError): settings.validate_mcp_paths(str(self.node), str(self.cli))

    def test_partial_save_failure_restores_previous_values_and_absence(self):
        self.app.preference.values["NodeExecutable"] = "previous-node"
        before = dict(self.app.preference.values)
        self.app.preference.fail = "MCPCLIEntry"
        with self.assertRaises(settings.PftChatSettingsError): settings.save_chat_options(self.app, True, str(self.node), str(self.cli))
        self.assertEqual(self.app.preference.values, before)

    def test_disabling_never_requires_resources_and_does_not_start_anything(self):
        options = settings.save_chat_options(self.app, False, "", "")
        self.assertEqual(options, {"enabled": False, "desk_enabled": False, "node_executable": "", "cli_entry": ""})
        self.assertIsNone(settings.build_chat_mcp_config(options, {"running": True, "port": 9876}))

    def test_desk_requires_explicit_main_tools_and_freezes_validated_paths_without_probe(self):
        desk_root = self.root.resolve() / "desk"
        (desk_root / "scripts").mkdir(parents=True)
        desk_cli = desk_root / "scripts/desk-web-service.mjs"
        desk_cli.write_text("neutral fixture")
        desk = {"node_executable": str(self.node.resolve()), "cli_entry": str(desk_cli),
                "state_dir": str(self.root.resolve() / "desk-state")}
        options = settings.save_chat_options(self.app, True, str(self.node), str(self.cli), desk_enabled=True)
        self.assertTrue(options["desk_enabled"])
        frozen = settings.build_chat_mcp_config(options, {"running": True, "port": 9876}, desk)
        self.assertEqual(frozen["desk_config"], desk)
        desk["state_dir"] = "/neutral/changed-state"
        self.assertNotEqual(frozen["desk_config"], desk)
        with self.assertRaises(settings.PftChatSettingsError):
            settings.save_chat_options(self.app, False, "", "", desk_enabled=True)
        for invalid in (None, {}, {"enabled": True}, {**frozen["desk_config"], "extra": "rejected"}):
            with self.subTest(invalid=invalid), self.assertRaises(settings.PftChatSettingsError):
                settings.build_chat_mcp_config(options, {"running": True, "port": 9876}, invalid)
        with patch.dict(sys.modules, {"pft_desk_controller": types.SimpleNamespace(
                load_desk_configuration=Mock(return_value=frozen["desk_config"]))}):
            self.assertEqual(settings.load_chat_desk_configuration(self.app), frozen["desk_config"])
        self.assertFalse((self.root.resolve() / "desk-state").exists())

    def test_desk_invalid_bool_and_stale_dependency_fail_closed(self):
        for value in (None, 1, "1", {}, []):
            with self.subTest(value=value), self.assertRaises(settings.PftChatSettingsError):
                settings.save_chat_options(self.app, False, "", "", desk_enabled=value)
        self.assertEqual(self.app.preference.writes, [])
        self.app.preference.values.update(EnableFreeCADTools=False, EnableDeskDataTools=True)
        self.assertFalse(settings.load_chat_options(self.app)["desk_enabled"])
        for options in ({"enabled": False, "desk_enabled": True}, {"enabled": True, "desk_enabled": "1"}):
            with self.assertRaises(settings.PftChatSettingsError): settings.build_chat_mcp_config(options, None)

    def test_tool_options_require_native_persistence_and_restore_failed_disk_save(self):
        self.app.preference.values["NodeExecutable"] = "previous"
        before = dict(self.app.preference.values)
        calls = []
        def save(group):
            calls.append(dict(self.app.preference.values))
            if len(calls) == 1: raise RuntimeError("/neutral/private/native-settings")
        self.app.saveParameter = save
        with self.assertRaisesRegex(settings.PftChatSettingsError, "原配置已恢复并保存") as failure:
            settings.save_chat_options(self.app, False, "", "")
        self.assertNotIn("native-settings", str(failure.exception))
        self.assertEqual(self.app.preference.values, before)
        self.assertEqual(calls[-1], before)
        self.assertEqual(len(calls), 2)
        self.app.saveParameter = None
        self.app.preference.writes.clear()
        with self.assertRaises(settings.PftChatSettingsError): settings.save_chat_options(self.app, False, "", "")
        self.assertEqual(self.app.preference.writes, [])


class ChatPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.app = App()

    def test_legacy_mcp_configuration_and_chat_preferences_are_independent(self):
        legacy = {"EnableFreeCADTools": True, "NodeExecutable": "old-node", "MCPCLIEntry": "old-cli"}
        self.app.preference.values.update(legacy)
        expected_mcp = settings.load_chat_options(self.app)
        self.assertEqual(settings.load_chat_preferences(self.app), {"model_id": "", "font_point_size": 0})
        self.assertEqual(self.app.preference.writes, [])
        result = settings.save_chat_preferences(self.app, "自定义/试验_α", 14)
        self.assertEqual(result, {"model_id": "自定义/试验_α", "font_point_size": 14})
        self.assertEqual(settings.load_chat_preferences(self.app), result)
        self.assertEqual(settings.load_chat_options(self.app), expected_mcp)
        self.assertEqual({key: self.app.preference.values[key] for key in legacy}, legacy)
        self.assertEqual(self.app.preference.writes, ["ModelId", "FontPointSize"])
        self.assertEqual(set(self.app.groups), {settings.PREFERENCE_PATH})
        settings.save_chat_options(self.app, False, "", "")
        self.assertEqual(settings.load_chat_preferences(self.app), result)

    def test_model_and_font_defaults_and_boundaries_round_trip(self):
        for model, font in (("", 0), ("a" * 256, 8), ("模型 e\u0301 / v2", 32)):
            with self.subTest(model=model, font=font):
                expected = {"model_id": model, "font_point_size": font}
                self.assertEqual(settings.save_chat_preferences(self.app, model, font), expected)
                self.assertEqual(settings.load_chat_preferences(self.app), expected)

    def test_invalid_choices_are_rejected_before_accessing_preferences(self):
        invalid_models = (None, 123, True, [], "a" * 257, " leading", "trailing ",
                          "\u00a0model", "a\nmodel", "a\u202emodel", "a\ud800model")
        for model in invalid_models:
            with self.subTest(model=model), self.assertRaises(settings.PftChatSettingsError):
                settings.save_chat_preferences(self.app, model, 12)
        for font in (None, True, False, 12.0, "12", -1, 1, 7, 33):
            with self.subTest(font=font), self.assertRaises(settings.PftChatSettingsError):
                settings.save_chat_preferences(self.app, "arbitrary-model-id", font)
        self.assertEqual(self.app.groups, [])
        self.assertEqual(self.app.preference.writes, [])

    def test_load_defaults_only_the_damaged_field(self):
        cases = (({"ModelId": "retained", "FontPointSize": True}, {"model_id": "retained", "font_point_size": 0}),
                 ({"ModelId": "retained", "FontPointSize": 33}, {"model_id": "retained", "font_point_size": 0}),
                 ({"ModelId": False, "FontPointSize": 16}, {"model_id": "", "font_point_size": 16}),
                 ({"ModelId": "broken\u202e", "FontPointSize": 16}, {"model_id": "", "font_point_size": 16}))
        for stored, expected in cases:
            with self.subTest(stored=stored):
                self.app.preference.values = stored
                self.assertEqual(settings.load_chat_preferences(self.app), expected)
        self.assertEqual(self.app.preference.writes, [])

    def test_load_read_failure_is_isolated_and_group_failure_uses_defaults(self):
        self.app.preference.values = {"ModelId": "retained", "FontPointSize": 16}
        original = self.app.preference.GetString
        self.app.preference.GetString = lambda *args: (_ for _ in ()).throw(RuntimeError("read failure"))
        self.assertEqual(settings.load_chat_preferences(self.app), {"model_id": "", "font_point_size": 16})
        self.app.preference.GetString = original
        self.app.preference.GetInt = lambda *args: (_ for _ in ()).throw(RuntimeError("read failure"))
        self.assertEqual(settings.load_chat_preferences(self.app), {"model_id": "retained", "font_point_size": 0})
        self.app.ParamGet = lambda group: (_ for _ in ()).throw(RuntimeError("group failure"))
        self.assertEqual(settings.load_chat_preferences(self.app), {"model_id": "", "font_point_size": 0})
        self.assertEqual(self.app.preference.writes, [])

    def test_second_write_or_readback_failure_restores_values_and_missing_keys(self):
        states = ({}, {"ModelId": "previous"}, {"FontPointSize": 16},
                  {"ModelId": "previous", "FontPointSize": 16})
        for state in states:
            for failure in ("write", "read", "mismatch", "wrong_type"):
                with self.subTest(state=state, failure=failure):
                    app = App()
                    app.preference.values.update(state)
                    app.preference.values["MCPCLIEntry"] = "untouched"
                    before = dict(app.preference.values)
                    if failure == "write":
                        app.preference.fail = "FontPointSize"
                    else:
                        original = app.preference.GetInt
                        def readback(key, default=0):
                            if key == "FontPointSize" and key in app.preference.writes:
                                if failure == "read":
                                    raise RuntimeError("synthetic readback failure")
                                return 13 if failure == "mismatch" else 12.0
                            return original(key, default)
                        app.preference.GetInt = readback
                    with self.assertRaises(settings.PftChatSettingsError):
                        settings.save_chat_preferences(app, "new-id", 12)
                    self.assertEqual(app.preference.values, before)

    def test_snapshot_failure_does_not_start_a_partial_save(self):
        self.app.preference.values = {"ModelId": "previous", "FontPointSize": 16}
        self.app.preference.GetInt = lambda *args: (_ for _ in ()).throw(RuntimeError("snapshot read failure"))
        with self.assertRaises(settings.PftChatSettingsError):
            settings.save_chat_preferences(self.app, "new-id", 12)
        self.assertEqual(self.app.preference.values, {"ModelId": "previous", "FontPointSize": 16})
        self.assertEqual(self.app.preference.writes, [])

    def test_key_value_enumerations_and_no_enumerator_restore_missing_state(self):
        for enumeration in ("pairs", "absent"):
            with self.subTest(enumeration=enumeration):
                app = App()
                app.preference.values["ModelId"] = "previous"
                if enumeration == "pairs":
                    app.preference.GetStrings = lambda: [("ModelId", "previous")]
                    app.preference.GetInts = lambda: []
                else:
                    app.preference.GetStrings = app.preference.GetInts = None
                app.preference.fail = "FontPointSize"
                with self.assertRaises(settings.PftChatSettingsError):
                    settings.save_chat_preferences(app, "new-id", 12)
                self.assertEqual(app.preference.values, {"ModelId": "previous"})

    def test_rollback_failure_is_reported(self):
        self.app.preference.fail = "FontPointSize"
        self.app.preference.RemString = lambda key: (_ for _ in ()).throw(RuntimeError("restore failure"))
        with self.assertRaisesRegex(settings.PftChatSettingsError, "部分原配置无法恢复"):
            settings.save_chat_preferences(self.app, "new-id", 12)
        self.assertNotIn("FontPointSize", self.app.preference.values)


    def test_model_font_native_save_and_restore_persistence_are_checked(self):
        self.app.preference.values.update(ModelId="previous", FontPointSize=14)
        before = dict(self.app.preference.values)
        settings.save_chat_preferences(self.app, "chosen", 16)
        self.assertEqual(self.app.persisted[-1], self.app.preference.values)
        self.app.preference.values = dict(before)
        self.app.saveParameter = lambda scope: (_ for _ in ()).throw(RuntimeError("/neutral/private/disk"))
        with self.assertRaisesRegex(settings.PftChatSettingsError, "落盘无法确认") as failure:
            settings.save_chat_preferences(self.app, "chosen", 16)
        self.assertNotIn("/neutral", str(failure.exception))
        self.assertEqual(self.app.preference.values, before)


if __name__ == "__main__": unittest.main()
