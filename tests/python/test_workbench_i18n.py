# SPDX-License-Identifier: Apache-2.0
"""Interface language, local preferences and cancelled dialogs without Qt/FreeCAD."""
import ast
import copy
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / 'addon/PhoenixFreeCADMCP'
_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import phoenix_workbench_i18n as i18n
    import phoenix_workbench_settings as settings
    import phoenix_workbench_views as views
finally:
    sys.path[:] = _path


class Preferences:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes = []
        self.fail_write = False

    def GetString(self, key, default=''):
        return self.values.get(key, default)

    def SetString(self, key, value):
        if self.fail_write:
            raise RuntimeError('fixture write failure')
        self.writes.append((key, value))
        self.values[key] = value


class App:
    def __init__(self, preferences):
        self.preferences = preferences
        self.groups = []

    def ParamGet(self, group):
        self.groups.append(group)
        return self.preferences

    @property
    def ActiveDocument(self):
        raise AssertionError('language settings must not inspect documents')


class Signal:
    def connect(self, callback): self.callback = callback


class Button:
    def __init__(self, text=''):
        self.text = text
        self.clicked = Signal()

    def setText(self, value): self.text = value
    def setEnabled(self, value): self.enabled = value
    def setToolTip(self, value): self.tooltip = value


class FakeWidgets:
    def __init__(self):
        self.dialogs, self.combos, self.tables, self.boxes, self.messages = [], [], [], [], []
        owner = self

        class Dialog:
            Accepted = 1
            result = 0
            on_exec = None

            def __init__(self, parent): owner.dialogs.append(self)
            def setWindowTitle(self, title): self.title = title
            def resize(self, *args): pass
            def accept(self): pass
            def reject(self): pass
            def exec(self):
                if type(self).on_exec:
                    type(self).on_exec()
                return type(self).result

        class Layout:
            def __init__(self, *args): self.children = []
            def addWidget(self, widget): self.children.append(widget)
            def addLayout(self, layout): self.children.append(layout)

        class Label:
            def __init__(self, text): self.text = text
            def setWordWrap(self, value): pass

        class Combo:
            def __init__(self):
                self.items, self.index = [], 0
                owner.combos.append(self)
            def addItem(self, title, data): self.items.append((title, data))
            def setCurrentIndex(self, index): self.index = index
            def currentData(self): return self.items[self.index][1]

        class ButtonBox:
            Ok, Cancel = 1, 2
            def __init__(self, flags):
                self.buttons = {1: Button(), 2: Button()}
                self.accepted, self.rejected = Signal(), Signal()
                owner.boxes.append(self)
            def button(self, identifier): return self.buttons[identifier]

        class Item:
            def __init__(self, text): self.value = text
            def text(self): return self.value
            def setText(self, text): self.value = text
            def flags(self): return 3
            def setFlags(self, value): pass
            def setToolTip(self, text): self.tooltip = text

        class Table:
            def __init__(self, rows, columns):
                self.items = {}
                owner.tables.append(self)
            def setHorizontalHeaderLabels(self, labels): self.headers = labels
            def setItem(self, row, column, item): self.items[row, column] = item
            def item(self, row, column): return self.items[row, column]
            def resizeColumnsToContents(self): pass
            def horizontalHeader(self): return types.SimpleNamespace(setStretchLastSection=lambda value: None)

        class CheckBox(Button):
            def setChecked(self, value): self.checked = value
            def isChecked(self): return self.checked

        self.QDialog, self.QVBoxLayout, self.QHBoxLayout = Dialog, Layout, Layout
        self.QLabel, self.QComboBox, self.QDialogButtonBox = Label, Combo, ButtonBox
        self.QTableWidgetItem, self.QTableWidget = Item, Table
        self.QCheckBox, self.QPushButton = CheckBox, Button
        self.QMessageBox = types.SimpleNamespace(
            warning=lambda parent, title, message: self.messages.append(('warning', title, message)),
            information=lambda parent, title, message: self.messages.append(('information', title, message)))


class InterfaceLanguageTests(unittest.TestCase):
    def setUp(self):
        previous = i18n.get_language()
        self.addCleanup(i18n.set_language, previous)
        i18n.set_language('zh')
        self.preferences = Preferences()
        self.app = App(self.preferences)

    def test_default_is_chinese_and_missing_preference_does_not_write(self):
        self.assertEqual(settings.load_language(self.app), 'zh')
        self.assertEqual(i18n.tr('命名修正'), '命名修正')
        self.assertEqual(self.preferences.writes, [])
        self.assertEqual(self.app.groups, [settings.PREFERENCE_PATH])

    def test_persisted_english_round_trip_uses_only_own_preference_group(self):
        settings.save_language(self.app, 'en')
        self.assertEqual(i18n.tr('命名修正'), 'Normalize labels')
        i18n.set_language('zh')
        self.assertEqual(settings.load_language(self.app), 'en')
        self.assertEqual(self.preferences.values, {'Language': 'en'})
        self.assertEqual(set(self.app.groups), {settings.PREFERENCE_PATH})

    def test_invalid_stored_or_requested_language_never_silently_changes_settings(self):
        for value in ('', 'unknown', 'ENGLISH'):
            self.preferences.values['Language'] = value
            self.assertEqual(settings.load_language(self.app), 'zh')
        with self.assertRaises(settings.SettingsError): settings.save_language(self.app, 'invalid')
        with self.assertRaises(ValueError): i18n.set_language('invalid')
        self.assertEqual(self.preferences.writes, [])
        self.assertEqual(i18n.get_language(), 'zh')

    def test_failed_persistence_does_not_switch_active_language(self):
        self.preferences.fail_write = True
        with self.assertRaises(settings.SettingsError): settings.save_language(self.app, 'en')
        self.assertEqual(i18n.get_language(), 'zh')
        self.assertEqual(self.preferences.values, {})

    def test_failed_read_has_explicit_chinese_fallback(self):
        i18n.set_language('en')
        self.app.ParamGet = lambda group: (_ for _ in ()).throw(RuntimeError('fixture'))
        with self.assertRaises(settings.SettingsError): settings.load_language(self.app)
        self.assertEqual(i18n.get_language(), 'zh')

    def test_catalog_preserves_format_placeholders_and_unknown_technical_identifiers(self):
        placeholder = r'%(?:\([^)]+\))?[#0 +\-]*\d*(?:\.\d+)?[a-zA-Z%]'
        for source, translated in i18n.ENGLISH.items():
            self.assertEqual(re.findall(placeholder, source), re.findall(placeholder, translated), source)
        i18n.set_language('en')
        self.assertIn('3 selected objects', i18n.tr('已修改 %s 个所选对象的 Label。可按撤销恢复；尚未保存文件。') % 3)
        for value in ('PartNumber', 'CustomDefine', 'PhoenixTools_ToggleMCP', 'set_view', 'Face1', '用户的零件名'):
            self.assertEqual(i18n.tr(value), value)

    def test_catalog_and_settings_have_no_freecad_or_qt_imports(self):
        for filename in ('phoenix_workbench_i18n.py', 'phoenix_workbench_settings.py'):
            tree = ast.parse((ADDON / filename).read_text())
            for node in ast.walk(tree):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                self.assertFalse(any(name.startswith(('FreeCAD', 'PySide')) for name in names))

    def test_fixed_interface_messages_are_catalogued(self):
        for filename in ('phoenix_workbench_gui.py', 'phoenix_workbench_views.py', 'phoenix_workbench_settings.py'):
            tree = ast.parse((ADDON / filename).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search('[\u3400-\u9fff]', node.value):
                    self.assertIn(node.value, i18n.ENGLISH, (filename, node.lineno, node.value))


class SettingsDialogTests(unittest.TestCase):
    def setUp(self):
        previous = i18n.get_language()
        self.addCleanup(i18n.set_language, previous)
        i18n.set_language('zh')
        self.widgets = FakeWidgets()
        self.core = types.SimpleNamespace(Qt=types.SimpleNamespace(ItemIsEditable=1))
        self.patch = patch.object(views, '_qt', return_value=(self.core, self.widgets))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.preferences = Preferences()
        self.app = App(self.preferences)
        self.gui = types.SimpleNamespace(getMainWindow=lambda: object())
        self.refreshed = []

    def choose_english(self):
        self.widgets.combos[-1].setCurrentIndex(1)

    def test_cancelled_language_choice_does_not_persist_refresh_or_touch_documents(self):
        self.widgets.QDialog.on_exec = self.choose_english
        views.show_settings_dialog(self.app, self.gui, lambda: self.refreshed.append(True))
        self.assertEqual(self.preferences.writes, [])
        self.assertEqual(i18n.get_language(), 'zh')
        self.assertEqual(self.refreshed, [])
        self.assertEqual(self.widgets.messages, [])

    def test_confirmed_language_choice_persists_then_refreshes_without_document_context(self):
        self.widgets.QDialog.result = 1
        self.widgets.QDialog.on_exec = self.choose_english
        views.show_settings_dialog(self.app, self.gui, lambda: self.refreshed.append(i18n.get_language()))
        self.assertEqual(self.preferences.values, {'Language': 'en'})
        self.assertEqual(self.refreshed, ['en'])
        self.assertEqual(self.widgets.messages[-1][-1], 'Interface language updated.')

    def test_failed_save_is_reported_without_refresh_or_language_switch(self):
        self.widgets.QDialog.result = 1
        self.widgets.QDialog.on_exec = self.choose_english
        self.preferences.fail_write = True
        views.show_settings_dialog(self.app, self.gui, lambda: self.refreshed.append(True))
        self.assertEqual(i18n.get_language(), 'zh')
        self.assertEqual(self.refreshed, [])
        self.assertEqual(self.widgets.messages[-1][0], 'warning')

    def test_new_english_label_dialog_translates_chrome_and_keeps_user_values(self):
        i18n.set_language('en')
        row = {'name': 'PublicObject', 'old_label': '修改', 'new_label': '取消', 'error': ''}
        with patch.object(views.controller, 'prepare_rename_context', return_value=(object(), [], [], [row])):
            views.show_rename_dialog(self.app, self.gui)
        self.assertEqual(self.widgets.dialogs[-1].title, 'Phoenix: Preview label corrections')
        self.assertEqual(self.widgets.tables[-1].headers, ['Internal Name', 'Previous Label', 'Proposed Label', 'Check result'])
        self.assertEqual(self.widgets.tables[-1].item(0, 1).text(), '修改')
        self.assertEqual(self.widgets.tables[-1].item(0, 2).text(), '取消')
        self.assertEqual(self.widgets.boxes[-1].button(1).text, 'Confirm changes (undoable)')

    def test_english_property_dialog_keeps_field_identifiers_and_values_unchanged(self):
        i18n.set_language('en')
        fields = {field: {'value': '取消', 'exists': True} for field, _ in views.BOM_FIELDS}
        snapshots = [{'name': 'PublicObject', 'label': '修改', 'fields': fields}]
        before = copy.deepcopy(snapshots)
        with patch.object(views.controller, 'prepare_properties_context', return_value=(object(), [], snapshots)):
            views.show_properties_dialog(self.app, self.gui)
        table = self.widgets.tables[-1]
        self.assertEqual(table.headers, ['Internal Name', 'Current Label'] + [field for field, _ in views.BOM_FIELDS] + ['Suggestion / Check'])
        self.assertEqual(table.item(0, 1).text(), '修改')
        self.assertEqual(table.item(0, 2).text(), '取消')
        self.assertEqual(snapshots, before)
        self.assertEqual(self.preferences.writes, [])


if __name__ == '__main__':
    unittest.main()
