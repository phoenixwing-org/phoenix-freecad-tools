# SPDX-License-Identifier: Apache-2.0
"""Target switching, cancellation and staged edits through the business views."""
import copy
import types
import unittest
from unittest.mock import patch

from test_pft_workbench_i18n import FakeWidgets, views, i18n
import test_pft_workbench_targets as targets


class TargetDialogTests(unittest.TestCase):
    def setUp(self):
        previous = i18n.get_language()
        self.addCleanup(i18n.set_language, previous)
        i18n.set_language('zh')
        self.fixture = targets.TargetContextTests()
        self.fixture.setUp()
        self.app, self.gui = self.fixture.app, self.fixture.gui
        self.gui.getMainWindow = lambda: object()
        self.widgets = FakeWidgets()
        core = types.SimpleNamespace(Qt=types.SimpleNamespace(ItemIsEditable=1, ScrollBarAsNeeded=3))
        qt = patch.object(views, '_qt', return_value=(core, self.widgets))
        qt.start()
        self.addCleanup(qt.stop)

    def targets(self):
        return {check.object_name[len('PhoenixTarget_'):]: check for check in self.widgets.checks
                if getattr(check, 'object_name', '').startswith('PhoenixTarget_')}

    def only(self, name):
        for key, check in self.targets().items(): check.setChecked(key == name)

    def test_compact_business_dialogs_keep_full_table_values_and_internal_scrolling(self):
        self.fixture.body.Label = 'Long public fixture label ' * 25
        for show, size in ((views.show_rename_dialog, (700, 480)),
                           (views.show_link_labels_dialog, (700, 480)),
                           (views.show_properties_dialog, (820, 500))):
            with self.subTest(tool=show.__name__):
                show(self.app, self.gui)
                self.assertEqual(self.widgets.dialogs[-1].initial_size, size)
                table = self.widgets.tables[-1]
                self.assertEqual(table.minimum_width, 0)
                self.assertEqual(table.sizePolicy().horizontal, 13)
                self.assertEqual(table.size_adjust_policy, 0)
                self.assertEqual(table.horizontal_scroll_policy, 3)
                for (row, column), item in table.items.items():
                    if item.text() == self.fixture.body.Label:
                        self.assertEqual(item.tooltip, self.fixture.body.Label)
                self.assertEqual(self.fixture.selection.records, [])
                self.assertFalse(self.widgets.messages)
        grid = next(layout for layout in self.widgets.layouts if isinstance(layout, self.widgets.QGridLayout))
        self.assertEqual(len(grid.cells), len(views.BOM_FIELDS))
        self.assertEqual({column for widget, row, column in grid.cells}, {0, 1})
        self.assertEqual(max(row for widget, row, column in grid.cells), 2)

    def test_cancel_without_tree_selection_never_applies_any_business_operation(self):
        for show, apply_name in ((views.show_rename_dialog, 'apply_rename'),
                                 (views.show_properties_dialog, 'apply_properties'),
                                 (views.show_link_labels_dialog, 'apply_link_labels')):
            with self.subTest(tool=show.__name__), patch.object(views.controller, apply_name) as apply:
                before = self.fixture.body.Label
                show(self.app, self.gui)
                self.assertTrue(self.widgets.dialogs)
                self.assertTrue(self.targets())
                apply.assert_not_called()
                self.assertEqual(self.fixture.body.Label, before)
                self.assertEqual(self.fixture.selection.records, [])
                self.assertFalse(self.widgets.messages)

    def test_rename_switch_rebuilds_preview_and_applies_only_displayed_target(self):
        self.widgets.QDialog.result = 1
        self.widgets.QDialog.on_exec = lambda: self.only('Body')
        with patch.object(views.controller, 'apply_rename', return_value=1) as apply:
            views.show_rename_dialog(self.app, self.gui)
        self.assertEqual(apply.call_count, 1)
        args = apply.call_args.args
        self.assertEqual([obj.Name for obj in args[3]], ['Body'])
        self.assertEqual([row['name'] for row in args[5]], ['Body'])
        self.assertEqual(args[5][0]['new_label'], '5410.003-Example part')
        self.assertEqual(self.fixture.selection.records, [])

    def test_clearing_every_target_disables_confirmation_and_forced_accept_cannot_apply(self):
        self.widgets.QDialog.result = 1
        self.widgets.QDialog.on_exec = lambda: self.only('absent')
        for show, apply_name in ((views.show_rename_dialog, 'apply_rename'),
                                 (views.show_properties_dialog, 'apply_properties'),
                                 (views.show_link_labels_dialog, 'apply_link_labels')):
            with self.subTest(tool=show.__name__), patch.object(views.controller, apply_name) as apply:
                show(self.app, self.gui)
                self.assertFalse(self.widgets.boxes[-1].button(1).enabled)
                apply.assert_not_called()

    def test_property_preview_and_apply_use_only_checked_targets_and_their_original_snapshots(self):
        self.widgets.QDialog.result = 1
        def edit():
            self.only('Imported')
            table = self.widgets.tables[-1]
            for row in range(len(self.targets())):
                if table.item(row, 1).text() == 'Imported':
                    table.item(row, 3).setText('NEW-NUMBER')
        self.widgets.QDialog.on_exec = edit
        with patch.object(views, '_confirm_property_changes', return_value=True) as confirm, \
             patch.object(views.controller, 'apply_properties', return_value=3) as apply:
            views.show_properties_dialog(self.app, self.gui)
        self.assertEqual(apply.call_count, 1, self.widgets.messages)
        args = apply.call_args.args
        self.assertEqual([obj.Name for obj in args[3]], ['Imported'])
        self.assertEqual([snapshot['name'] for snapshot in args[4]], ['Imported'])
        self.assertEqual(set(args[6]), {'Imported'})
        self.assertEqual(args[6]['Imported']['PartNumber'], 'NEW-NUMBER')
        self.assertEqual({row['name'] for row in confirm.call_args.args[-1]}, {'Imported'})
        self.assertNotIn('PartNumber', self.fixture.imported.PropertiesList)

    def test_property_suggestion_changes_only_checked_staged_rows(self):
        self.fixture.body.Label = '7777.004-Suggested name'
        before = copy.deepcopy(self.fixture.body.__dict__['PropertiesList'])
        def recommend():
            self.only('Body')
            table = self.widgets.tables[-1]
            old = {(row, col): table.item(row, col).text()
                   for row in range(len(self.targets())) for col in range(3, 8)}
            button = next(button for button in self.widgets.buttons if button.text == '名称推荐')
            button.clicked.emit()
            for row in range(len(self.targets())):
                if table.item(row, 1).text() != 'Body':
                    for col in range(3, 8): self.assertEqual(table.item(row, col).text(), old[row, col])
        self.widgets.QDialog.on_exec = recommend
        views.show_properties_dialog(self.app, self.gui)
        self.assertFalse(self.widgets.messages)
        self.assertEqual(self.fixture.body.PropertiesList, before)
        self.assertEqual(self.fixture.body.PartNumber, '5410')

    def test_empty_candidates_report_without_opening_an_ambiguous_dialog(self):
        self.fixture.doc.Objects = [self.fixture.origin, self.fixture.page]
        for show in (views.show_rename_dialog, views.show_properties_dialog, views.show_link_labels_dialog):
            with self.subTest(tool=show.__name__):
                show(self.app, self.gui)
                self.assertEqual(self.widgets.dialogs, [])
                self.assertEqual(self.widgets.messages[-1][0], 'warning')


if __name__ == '__main__':
    unittest.main()
