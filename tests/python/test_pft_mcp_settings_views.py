# SPDX-License-Identifier: Apache-2.0
"""Synthetic settings UI and private temporary files; no Qt, CAD or services."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from test_pft_mcp_settings import settings
from test_pft_workbench_i18n import views as workbench_views, Signal, Button

ADDON = Path(__file__).parents[2] / "addon/PhoenixFreeCADTools"
spec = importlib.util.spec_from_file_location("pft_mcp_settings_views_test", ADDON / "pft_mcp_settings_views.py")
view = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"pft_mcp_settings": settings}):
    spec.loader.exec_module(view)


class Widgets:
    def __init__(self):
        self.dialogs, self.spins, self.lines, self.boxes, self.checks = [], [], [], [], []
        self.action = lambda dialog: None
        owner = self

        class Dialog:
            Accepted, Rejected = 1, 0
            def __init__(self, parent):
                self.result = self.Rejected
                owner.dialogs.append(self)
            def setObjectName(self, name): self.object_name = name
            def setWindowTitle(self, title): self.title = title
            def resize(self, *size): self.size = size
            def accept(self): self.result = self.Accepted
            def reject(self): self.result = self.Rejected
            def close(self): self.reject()
            def exec(self):
                owner.action(self)
                return self.result

        class Layout:
            def __init__(self, *args): pass
            def addWidget(self, *args): pass
            def addLayout(self, *args): pass
            def addStretch(self, *args): pass

        class Line:
            def __init__(self): owner.lines.append(self)
            def setObjectName(self, name): self.object_name = name
            def setReadOnly(self, value): self.read_only = value
            def setText(self, text): self.text = text

        class Spin:
            def __init__(self): owner.spins.append(self)
            def setObjectName(self, name): self.object_name = name
            def setRange(self, minimum, maximum): self.range = minimum, maximum
            def setValue(self, value): self.current = value
            def value(self): return self.current

        class Check:
            def __init__(self, text):
                self.text, self.checked = text, False
                owner.checks.append(self)
            def setObjectName(self, name): self.object_name = name
            def setChecked(self, checked): self.checked = checked
            def isChecked(self): return self.checked

        class Box:
            Ok, Cancel = 1, 2
            def __init__(self, flags):
                self.buttons = {1: Button(), 2: Button()}
                self.accepted, self.rejected = Signal(), Signal()
                owner.boxes.append(self)
            def button(self, identifier): return self.buttons[identifier]

        self.QDialog, self.QVBoxLayout, self.QHBoxLayout = Dialog, Layout, Layout
        self.QLineEdit, self.QSpinBox, self.QDialogButtonBox, self.QCheckBox = Line, Spin, Box, Check


class Gui:
    def getMainWindow(self): return object()
    def __getattr__(self, name):
        raise AssertionError("Connection settings must not inspect CAD or services")


class McpSettingsViewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pft-mcp-settings-ui-", dir="/private/tmp")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.options = {"home": self.home, "platform": "darwin", "env": {}}
        self.widgets = Widgets()
        self.messages = []
        self.gui = Gui()
        patches = [
            patch.object(view, "tr", side_effect=lambda text: text),
            patch.object(workbench_views, "_qt", return_value=(object(), self.widgets)),
            patch.object(workbench_views, "_plain_label", side_effect=lambda widgets, core, text: text),
            patch.object(workbench_views, "_shrink_horizontal", side_effect=lambda widget: widget),
            patch.object(workbench_views, "_report",
                         side_effect=lambda gui, text, error=False: self.messages.append((text, error))),
        ]
        for context in patches:
            context.start()
            self.addCleanup(context.stop)

    def payload(self, port=9876):
        return {"schema_version": 1, "bridge": {"preferred_port": port}}

    def run_dialog(self, snapshot=None):
        return view.show_mcp_settings_dialog(self.gui, snapshot, options=self.options)

    def accept(self, value=12345, discovery=None):
        def action(dialog):
            self.widgets.spins[-1].setValue(value)
            if discovery is not None:
                self.widgets.checks[-1].setChecked(discovery)
            self.widgets.boxes[-1].accepted.emit()
        self.widgets.action = action

    def test_import_does_not_read_configuration_or_load_qt(self):
        fresh = importlib.util.module_from_spec(spec)
        with patch.object(settings, "read_config", side_effect=AssertionError("import read config")), \
             patch.object(workbench_views, "_qt", side_effect=AssertionError("import loaded Qt")), \
             patch.dict(sys.modules, {"pft_mcp_settings": settings}):
            spec.loader.exec_module(fresh)

    def test_cancel_escape_and_close_do_not_create_or_save_configuration(self):
        for action in ("cancel", "escape", "close"):
            with self.subTest(action=action):
                def reject(dialog):
                    self.widgets.spins[-1].setValue(12345)
                    self.widgets.checks[-1].setChecked(True)
                    if action == "cancel": self.widgets.boxes[-1].rejected.emit()
                    elif action == "escape": dialog.reject()  # QDialog's native Esc rejection path.
                    else: dialog.close()
                self.widgets.action = reject
                with patch.object(settings, "save_config", wraps=settings.save_config) as save:
                    self.assertIsNone(self.run_dialog())
                    save.assert_not_called()
                self.assertEqual(list(self.home.iterdir()), [])
                self.assertEqual(self.messages, [])

    def test_default_cancel_and_full_read_only_location_are_explicit(self):
        self.assertIsNone(self.run_dialog({"running": True, "port": 12340,
                                          "listener_configuration": {"source": "development"}}))
        box = self.widgets.boxes[-1]
        self.assertTrue(box.button(box.Cancel).default)
        self.assertFalse(box.button(box.Ok).default)
        self.assertFalse(box.button(box.Ok).auto_default)
        self.assertEqual(self.widgets.spins[-1].range, (1024, 65535))
        self.assertFalse(self.widgets.checks[-1].isChecked())
        self.assertEqual(self.widgets.checks[-1].object_name, "PhoenixTools_McpInstanceDiscovery")
        location = self.widgets.lines[-1]
        self.assertTrue(location.read_only)
        self.assertEqual(location.text, str(settings.resolve_config_path(**self.options)))

    def test_accept_persists_selected_port_using_original_etag_once(self):
        before = settings.save_config(self.payload(12340), expected_etag=None, **self.options)
        self.accept(12345)
        with patch.object(settings, "save_config", wraps=settings.save_config) as save:
            result = self.run_dialog({"running": True, "port": 12340})
        self.assertEqual(save.call_count, 1)
        self.assertEqual(save.call_args.args, (self.payload(12345),))
        self.assertEqual(save.call_args.kwargs, {**self.options, "expected_etag": before["etag"]})
        self.assertEqual(result["settings"], self.payload(12345))
        self.assertEqual(settings.read_config(**self.options), result)
        self.assertEqual(len(self.messages), 1)
        self.assertFalse(self.messages[0][1])
        self.assertIn("未启停服务", self.messages[0][0])

    def test_explicit_discovery_optin_saves_v2_once_with_original_etag(self):
        before = settings.save_config(self.payload(12340), expected_etag=None, **self.options)
        self.accept(12345, discovery=True)
        with patch.object(settings, "save_config", wraps=settings.save_config) as save:
            result = self.run_dialog()
        expected = {"schema_version": 2, "bridge": {"preferred_port": 12345}, "discovery": {"enabled": True}}
        self.assertEqual(save.call_count, 1)
        self.assertEqual(save.call_args.args, (expected,))
        self.assertEqual(save.call_args.kwargs["expected_etag"], before["etag"])
        self.assertEqual(result["settings"], expected)

    def test_existing_v2_unchecked_preserves_v2_false_without_downgrade(self):
        for previous_enabled in (True, False):
            with self.subTest(previous_enabled=previous_enabled):
                current = settings.read_config(**self.options)
                selected = {"schema_version": 2, "bridge": {"preferred_port": 12340},
                            "discovery": {"enabled": previous_enabled}}
                before = settings.save_config(selected, expected_etag=current["etag"], **self.options)
                self.accept(12345, discovery=False)
                with patch.object(settings, "save_config", wraps=settings.save_config) as save:
                    result = self.run_dialog()
                self.assertEqual(save.call_count, 1)
                self.assertEqual(save.call_args.kwargs["expected_etag"], before["etag"])
                self.assertEqual(result["settings"], {"schema_version": 2, "bridge": {"preferred_port": 12345},
                                                     "discovery": {"enabled": False}})

    def test_opening_and_cancelling_existing_v2_preserves_exact_bytes_identity_and_mode(self):
        selected = {"schema_version": 2, "bridge": {"preferred_port": 12340}, "discovery": {"enabled": True}}
        record = settings.save_config(selected, expected_etag=None, **self.options)
        path = Path(record["config_path"])
        info = path.stat()
        before = path.read_bytes(), info.st_ino, info.st_mtime_ns, info.st_mode
        with patch.object(settings, "save_config", wraps=settings.save_config) as save:
            self.assertIsNone(self.run_dialog())
            save.assert_not_called()
        self.assertTrue(self.widgets.checks[-1].isChecked())
        info = path.stat()
        self.assertEqual((path.read_bytes(), info.st_ino, info.st_mtime_ns, info.st_mode), before)
        self.assertEqual(self.messages, [])

    def test_bad_existing_configuration_reports_fixed_error_before_opening_dialog(self):
        record = settings.save_config(self.payload(), expected_etag=None, **self.options)
        Path(record["config_path"]).write_bytes(b"synthetic secret invalid JSON")
        self.accept()
        with patch.object(settings, "save_config", wraps=settings.save_config) as save:
            self.assertIsNone(self.run_dialog())
            save.assert_not_called()
        self.assertEqual(self.widgets.dialogs, [])
        self.assertEqual(len(self.messages), 1)
        message, failed = self.messages[0]
        self.assertTrue(failed)
        self.assertIn("MCP_SETTINGS_INVALID", message)
        self.assertNotIn("synthetic secret", message)
        self.assertNotIn(str(self.home), message)

    def test_changed_etag_during_dialog_rejects_without_overwriting_other_writer(self):
        before = settings.save_config(self.payload(), expected_etag=None, **self.options)
        original_save = settings.save_config
        def race(dialog):
            original_save(self.payload(12346), expected_etag=before["etag"], **self.options)
            self.widgets.spins[-1].setValue(12345)
            self.widgets.boxes[-1].accepted.emit()
        self.widgets.action = race
        with patch.object(settings, "save_config", wraps=original_save) as save:
            self.assertIsNone(self.run_dialog())
        self.assertEqual(save.call_count, 1)
        self.assertEqual(settings.read_config(**self.options)["settings"], self.payload(12346))
        self.assertTrue(self.messages[-1][1])
        self.assertIn("MCP_SETTINGS_CHANGED", self.messages[-1][0])
        self.assertFalse(any("已保存" in text and not error for text, error in self.messages))

    def test_uncertain_post_save_result_is_not_success_or_retried(self):
        self.accept(12345, discovery=True)
        original_save = settings.save_config
        def publish_then_uncertain(*args, **kwargs):
            original_save(*args, **kwargs)
            raise settings.PftMcpSettingsError("MCP_SETTINGS_SAVE_UNVERIFIED", True)
        with patch.object(settings, "save_config", side_effect=publish_then_uncertain) as save:
            self.assertIsNone(self.run_dialog())
        self.assertEqual(save.call_count, 1)
        self.assertEqual(settings.read_config(**self.options)["settings"],
                         {"schema_version": 2, "bridge": {"preferred_port": 12345}, "discovery": {"enabled": True}})
        self.assertEqual(len(self.messages), 1)
        self.assertTrue(self.messages[0][1])
        self.assertIn("持久状态无法确认", self.messages[0][0])
        self.assertIn("不要直接重复保存", self.messages[0][0])

    def test_unexpected_save_error_does_not_disclose_native_message_or_claim_success(self):
        self.accept()
        with patch.object(settings, "save_config", side_effect=OSError("synthetic private path or content")) as save:
            self.assertIsNone(self.run_dialog())
        self.assertEqual(save.call_count, 1)
        self.assertTrue(self.messages[-1][1])
        self.assertIn("重新读取配置", self.messages[-1][0])
        self.assertNotIn("synthetic private", self.messages[-1][0])


if __name__ == "__main__":
    unittest.main()
