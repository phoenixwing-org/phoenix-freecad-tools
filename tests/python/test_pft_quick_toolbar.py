# SPDX-License-Identifier: Apache-2.0
"""Shared shortcuts use synthetic Qt adapters and fixed native command fixtures."""
import gc
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, call, patch

from test_pft_chat_views_compact import ChatQt

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
_old_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_quick_toolbar as toolbar_module
finally:
    sys.path[:] = _old_path


class QuickToolbarTests(unittest.TestCase):
    def setUp(self):
        self.qt = ChatQt()
        self.owner = self.qt.widgets.QWidget()
        self.owner._closed = False
        self.content = self.qt.widgets.QWidget(self.owner)
        self.gui = types.SimpleNamespace(listWorkbenches=Mock(return_value={"PftWorkbench": object()}),
                                         activateWorkbench=Mock())
        self.native = Mock()
        self.host = types.SimpleNamespace(__file__=str(ADDON / "pft_workbench_gui.py"),
                                          run_native_shortcut=self.native)
        self.errors, self.success = [], Mock()
        modules = {"PySide": types.SimpleNamespace(QtCore=self.qt.core, QtGui=self.qt.gui,
                                                   QtWidgets=self.qt.widgets), "pft_workbench_gui": self.host}
        handle = patch.dict(sys.modules, modules)
        handle.start(); self.addCleanup(handle.stop)

    def create(self, **options):
        return toolbar_module.create_quick_toolbar(self.content, self.gui, object_prefix="Fixture_",
            on_error=self.errors.append, on_success=self.success, **options)

    def test_build_is_inert_and_two_hosts_dispatch_the_same_fixed_native_commands(self):
        first, second = self.create(), self.create()
        self.native.assert_not_called()
        self.gui.listWorkbenches.assert_not_called()
        for current in (first, second):
            for key, identifier in (("document", "PhoenixTools_DocumentTools"),
                                    ("axonometric_fit", "PhoenixTools_AxonometricFit"),
                                    ("appearance", "PhoenixTools_Appearance")):
                current.buttons[key].click()
                self.assertEqual(self.native.call_args, call(self.gui, identifier))
        self.assertEqual(self.native.call_count, 6)
        self.assertEqual(self.success.call_count, 6)
        self.assertEqual(self.errors, [])

    def test_buttons_have_stable_names_accessibility_and_window_owned_slot_receivers(self):
        toolbar = self.create(settings_name="Fixture_SettingsToggle", settings_checkable=True)
        self.assertEqual(set(toolbar.buttons), {"document", "axonometric_fit", "appearance", "workbench", "settings"})
        self.assertIs(toolbar.parent(), self.content)
        for key, button in toolbar.buttons.items():
            self.assertIs(button.parent(), toolbar)
            self.assertTrue(button.auto_raise)
            self.assertEqual(button.style, self.qt.core.Qt.ToolButtonIconOnly)
            self.assertEqual(button.tooltip, button.accessible_name)
        self.assertEqual(toolbar.buttons["settings"].objectName(), "Fixture_SettingsToggle")
        self.assertEqual(toolbar.buttons["document"].objectName(), "Fixture_DocumentToolsAction")
        for sender, callback in toolbar._bindings:
            receiver = callback.__self__
            self.assertIsInstance(receiver, self.qt.core.QObject)
            self.assertIs(receiver.parent(), toolbar)
            self.assertIn(receiver, toolbar._receivers)
            self.assertIn(callback.__func__._qt_slot_types, ((bool,), ()))

    def test_weak_signal_storage_and_gc_preserve_repeated_actions(self):
        self.qt.weak_button_callbacks = True
        toolbar = self.create()
        for _ in range(3):
            gc.collect()
            toolbar.buttons["appearance"].click()
        self.assertEqual(self.native.call_count, 3)
        self.assertEqual(self.errors, [])

    def test_checked_settings_binding_can_be_replaced_without_reconnecting(self):
        toolbar = self.create(settings_checkable=True)
        first, second = Mock(), Mock()
        toolbar.bind_settings(first, pass_checked=True)
        toolbar.buttons["settings"].click()
        first.assert_called_once_with(True)
        toolbar.bind_settings(second, pass_checked=True)
        toolbar.buttons["settings"].setChecked(False)
        second.assert_called_once_with(False)
        self.assertEqual(len(toolbar.buttons["settings"].toggled.slots), 1)
        self.native.assert_not_called()
        self.success.assert_not_called()

    def test_unchecked_settings_runs_only_host_display_callback_and_hidden_workbench_stays_hidden(self):
        toolbar = self.create(show_workbench=False)
        callback = Mock()
        toolbar.bind_settings(callback)
        toolbar.buttons["settings"].click()
        callback.assert_called_once_with()
        self.assertFalse(toolbar.buttons["workbench"].visible)
        self.gui.activateWorkbench.assert_not_called()
        self.native.assert_not_called()

    def test_close_ancestor_and_invalidate_reject_queued_actions(self):
        toolbar = self.create()
        callback = Mock()
        toolbar.bind_settings(callback)
        self.owner._closed = True
        for button in toolbar.buttons.values(): button.clicked.emit(True)
        self.native.assert_not_called(); callback.assert_not_called()
        self.gui.activateWorkbench.assert_not_called()
        self.owner._closed = False
        late = [slot for _sender, slot in toolbar._bindings if slot.__func__._qt_slot_types == (bool,)]
        toolbar.invalidate()
        for slot in late: slot(True)
        self.assertEqual(toolbar._bindings, [])
        self.assertEqual(toolbar._receivers, [])
        self.native.assert_not_called(); callback.assert_not_called()

    def test_parent_destroyed_invalidates_before_child_objects_are_deleted(self):
        toolbar = self.create()
        late = toolbar._bindings[0][1]
        self.content.destroyed.emit(self.content)
        late(True)
        self.assertTrue(toolbar._closed)
        self.assertEqual(toolbar._bindings, [])
        self.native.assert_not_called()

    def test_reentrant_click_is_ignored_and_later_independent_click_still_works(self):
        toolbar = self.create()
        self.native.side_effect = lambda *_args: toolbar.buttons["appearance"].clicked.emit(True)
        toolbar.buttons["appearance"].click()
        self.assertEqual(self.native.call_count, 1)
        toolbar.buttons["appearance"].click()
        self.assertEqual(self.native.call_count, 2)
        self.assertEqual(self.success.call_count, 2)

    def test_resolution_is_late_and_rejects_foreign_source_without_import_or_replacement(self):
        toolbar = self.create()
        foreign = types.SimpleNamespace(__file__="/fixture/other-install/pft_workbench_gui.py", run_native_shortcut=Mock())
        with patch.dict(sys.modules, {"pft_workbench_gui": foreign}):
            toolbar.buttons["document"].click()
            self.assertIs(sys.modules["pft_workbench_gui"], foreign)
        foreign.run_native_shortcut.assert_not_called()
        self.assertEqual(self.errors[-1]["code"], "PFT_CHAT_SHORTCUT_SOURCE_MISMATCH")
        self.assertNotIn("fixture", str(self.errors))
        toolbar.buttons["document"].click()
        self.native.assert_called_once_with(self.gui, "PhoenixTools_DocumentTools")

    def test_safe_receipt_codes_map_to_fixed_messages_and_unknown_errors_stay_generic(self):
        class CommandError(RuntimeError):
            def __init__(self, code):
                super().__init__("SYNTHETIC_PRIVATE_DETAIL")
                self.code, self.message = code, "SYNTHETIC_PRIVATE_DETAIL"
        self.host.pft_global_menu = types.SimpleNamespace(PftGlobalMenuError=CommandError)
        toolbar = self.create()
        self.native.side_effect = CommandError("PFT_CHAT_SHORTCUT_NO_3D_VIEW")
        toolbar.buttons["appearance"].click()
        self.assertEqual(self.errors[-1], {"code": "PFT_CHAT_SHORTCUT_NO_3D_VIEW",
            "message": "没有可确定的活动三维视图，请先激活目标文档的三维视图。"})
        self.native.side_effect = CommandError("SYNTHETIC_UNKNOWN_CODE")
        toolbar.buttons["appearance"].click()
        self.assertEqual(self.errors[-1]["code"], "PFT_CHAT_SHORTCUT_ACTION_FAILED")
        self.assertNotIn("PRIVATE", str(self.errors))
        self.assertNotIn("UNKNOWN", str(self.errors))

    def test_workbench_uses_only_registered_id_and_reports_fixed_failures(self):
        toolbar = self.create()
        toolbar.buttons["workbench"].click()
        self.gui.activateWorkbench.assert_called_once_with("PftWorkbench")
        self.native.assert_not_called()
        self.gui.listWorkbenches.return_value = {"OtherWorkbench": object()}
        toolbar.buttons["workbench"].click()
        self.assertEqual(self.errors[-1]["code"], "PFT_CHAT_SHORTCUT_WORKBENCH_UNAVAILABLE")
        self.gui.listWorkbenches.return_value = {"PftWorkbench": object()}
        self.gui.activateWorkbench.side_effect = RuntimeError("SYNTHETIC_PRIVATE_DETAIL")
        toolbar.buttons["workbench"].click()
        self.assertEqual(self.errors[-1]["code"], "PFT_CHAT_SHORTCUT_WORKBENCH_ACTIVATION_FAILED")
        self.assertNotIn("PRIVATE", str(self.errors))


if __name__ == "__main__":
    unittest.main()
