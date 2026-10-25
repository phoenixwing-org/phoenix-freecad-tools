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

ADDON = Path(__file__).resolve().parents[2] / 'addon/PhoenixFreeCADTools'
_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_workbench_i18n as i18n
    import pft_workbench_settings as settings
    import pft_workbench_views as views
finally:
    sys.path[:] = _path


class Preferences:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.bools = {}
        self.writes = []
        self.fail_write = False

    def GetString(self, key, default=''):
        return self.values.get(key, default)

    def SetString(self, key, value):
        if self.fail_write:
            raise RuntimeError('fixture write failure')
        self.writes.append((key, value))
        self.values[key] = value

    def GetBool(self, key, default=False): return self.bools.get(key, default)
    def SetBool(self, key, value):
        if self.fail_write:
            raise RuntimeError('fixture write failure')
        self.writes.append((key, value))
        self.bools[key] = value
    def GetStrings(self): return list(self.values)
    def GetBools(self): return list(self.bools)
    def RemString(self, key): self.values.pop(key, None)
    def RemBool(self, key): self.bools.pop(key, None)


class App:
    def saveParameter(self, name):
        if name != 'User parameter':
            raise ValueError('Unexpected preference scope')
        return None

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
    def __init__(self): self.callbacks = []
    def connect(self, callback): self.callbacks.append(callback)
    def emit(self, *args):
        for callback in self.callbacks: callback(*args)


class Button:
    def __init__(self, text=''):
        self.text = text
        self.clicked = Signal()

    def setObjectName(self, name): self.object_name = name
    def setText(self, value): self.text = value
    def setEnabled(self, value): self.enabled = value
    def setToolTip(self, value): self.tooltip = value
    def setDefault(self, value): self.default = value
    def setAutoDefault(self, value): self.auto_default = value


class SizingPolicy:
    Ignored = 13
    def __init__(self): self.horizontal = 5
    def setHorizontalPolicy(self, value): self.horizontal = value


class SizingWidget:
    def setMinimumWidth(self, value): self.minimum_width = value
    def sizePolicy(self):
        if not hasattr(self, 'policy'): self.policy = SizingPolicy()
        return self.policy
    def setSizePolicy(self, value): self.policy = value


class FakeWidgets:
    def __init__(self):
        self.dialogs, self.combos, self.tables, self.boxes, self.messages, self.labels, self.checks, self.buttons = [], [], [], [], [], [], [], []
        owner = self
        self.layouts = []

        class Dialog:
            Accepted = 1
            result = 0
            on_exec = None

            def __init__(self, parent): owner.dialogs.append(self)
            def setWindowTitle(self, title): self.title = title
            def resize(self, *args): self.initial_size = args
            def accept(self): pass
            def reject(self): pass
            def exec(self):
                if type(self).on_exec:
                    type(self).on_exec()
                return type(self).result

        class Layout:
            def __init__(self, *args): self.children = []; owner.layouts.append(self)
            def addWidget(self, widget, *args): self.children.append(widget)
            def addLayout(self, layout): self.children.append(layout)
            def addStretch(self, value=0): self.children.append(("stretch", value))

        class Grid(Layout):
            def __init__(self, *args): super().__init__(*args); self.cells = []
            def addWidget(self, widget, row, column): self.cells.append((widget, row, column)); super().addWidget(widget)

        class Label(SizingWidget):
            def __init__(self, text): self.text = text; owner.labels.append(self)
            def setWordWrap(self, value): self.word_wrap = value
            def setText(self, text): self.text = text

        class Combo(SizingWidget):
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

        class Table(SizingWidget):
            def __init__(self, rows, columns):
                self.items, self.cells = {}, {}
                owner.tables.append(self)
            def setSizeAdjustPolicy(self, value): self.size_adjust_policy = value
            def setHorizontalScrollBarPolicy(self, value): self.horizontal_scroll_policy = value
            def setHorizontalHeaderLabels(self, labels): self.headers = labels
            def setCellWidget(self, row, column, widget): self.cells[row, column] = widget
            def cellWidget(self, row, column): return self.cells[row, column]
            def setItem(self, row, column, item): self.items[row, column] = item
            def item(self, row, column): return self.items[row, column]
            def resizeColumnsToContents(self): pass
            def horizontalHeader(self): return types.SimpleNamespace(setStretchLastSection=lambda value: None)

        class CheckBox(Button):
            def __init__(self, text=''):
                super().__init__(text)
                owner.checks.append(self)
                self.checked = False
                self.toggled, self.stateChanged = Signal(), Signal()
            def setChecked(self, value):
                changed = value != self.checked
                self.checked = value
                if changed:
                    self.toggled.emit(value)
                    self.stateChanged.emit(2 if value else 0)
            def isChecked(self): return self.checked

        self.QDialog, self.QVBoxLayout, self.QHBoxLayout = Dialog, Layout, Layout
        self.QGridLayout = Grid
        self.QAbstractScrollArea = types.SimpleNamespace(AdjustIgnored=0)
        self.QLabel, self.QComboBox, self.QDialogButtonBox = Label, Combo, ButtonBox
        self.QTableWidgetItem, self.QTableWidget = Item, Table
        class PushButton(Button):
            def __init__(self, text=''):
                super().__init__(text)
                owner.buttons.append(self)
        self.QCheckBox, self.QPushButton = CheckBox, PushButton
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
        with self.assertRaises(settings.PftSettingsError): settings.save_language(self.app, 'invalid')
        with self.assertRaises(ValueError): i18n.set_language('invalid')
        self.assertEqual(self.preferences.writes, [])
        self.assertEqual(i18n.get_language(), 'zh')

    def test_failed_persistence_does_not_switch_active_language(self):
        self.preferences.fail_write = True
        with self.assertRaises(settings.PftSettingsError): settings.save_language(self.app, 'en')
        self.assertEqual(i18n.get_language(), 'zh')
        self.assertEqual(self.preferences.values, {})

    def test_failed_read_has_explicit_chinese_fallback(self):
        i18n.set_language('en')
        self.app.ParamGet = lambda group: (_ for _ in ()).throw(RuntimeError('fixture'))
        with self.assertRaises(settings.PftSettingsError): settings.load_language(self.app)
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
        for filename in ('pft_workbench_i18n.py', 'pft_workbench_settings.py'):
            tree = ast.parse((ADDON / filename).read_text())
            for node in ast.walk(tree):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                self.assertFalse(any(name.startswith(('FreeCAD', 'PySide')) for name in names))

    def test_fixed_interface_messages_are_catalogued(self):
        for filename in ('pft_workbench_gui.py', 'pft_workbench_views.py', 'pft_workbench_settings.py'):
            tree = ast.parse((ADDON / filename).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search('[\u3400-\u9fff]', node.value):
                    self.assertIn(node.value, i18n.ENGLISH, (filename, node.lineno, node.value))

    def test_native_chat_and_document_tools_fixed_messages_have_real_translations(self):
        filenames = ('pft_chat_views.py', 'pft_chat_settings.py', 'pft_chat_profile.py', 'pft_chat_controller.py', 'pft_chat_qprocess.py',
                     'pft_document_tools.py', 'pft_document_tools_views.py', 'pft_panel_host.py',
                     'pft_visual_tools.py', 'pft_appearance_views.py', 'pft_appearance_controls.py', 'pft_thumbnail.py',
                     'pft_thumbnail_capture.py', 'pft_thumbnail_views.py')
        for filename in filenames:
            source = ADDON / filename
            if filename == 'pft_panel_host.py' and not source.exists():
                continue  # The shared host has no required UI strings of its own.
            tree = ast.parse(source.read_text())
            excluded = {id(value) for node in ast.walk(tree) if isinstance(node, ast.Dict)
                        for key, value in zip(node.keys, node.values)
                        if isinstance(key, ast.Constant) and key.value == 'developerInstructions'}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str) and
                        re.search('[\u3400-\u9fff]', node.value) and id(node) not in excluded):
                    self.assertIn(node.value, i18n.ENGLISH, (filename, node.lineno, node.value))
                    translated = i18n.ENGLISH[node.value]
                    self.assertNotEqual(translated, node.value)
                    self.assertNotRegex(translated, '[\u3400-\u9fff]')
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'TRANSLATABLE_STRINGS' for target in node.targets):
                    for message in ast.literal_eval(node.value):
                        if re.search('[\u3400-\u9fff]', message):
                            self.assertIn(message, i18n.ENGLISH, (filename, message))
                        else:
                            self.assertEqual(i18n.tr(message), message)

    def test_native_chat_translation_does_not_rewrite_input_paths_models_or_ids(self):
        i18n.set_language('en')
        self.assertEqual(i18n.tr('正在回复…'), 'Replying…')
        self.assertEqual(i18n.tr('目标：%s') % '/example/当前文档.md', 'Target: /example/当前文档.md')
        user_text = '用户消息：请保留我的零件名称与原始大小写。'
        for value in (user_text, '/example/专属目录/codex', 'fixture-model-id', 'thread-private-fixture'):
            self.assertEqual(i18n.tr(value), value)
        source = ast.parse((ADDON / 'pft_workbench_i18n.py').read_text())
        catalog = next(node.value for node in source.body if isinstance(node, ast.Assign) and
                       any(isinstance(target, ast.Name) and target.id == 'ENGLISH' for target in node.targets))
        keys = [ast.literal_eval(key) for key in catalog.keys]
        self.assertEqual(len(keys), len(set(keys)), 'Duplicate message keys hide earlier translations')


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
        self.widgets.combos[0].setCurrentIndex(1)

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
        self.assertEqual(self.preferences.values, {'Language': 'en', 'ToolbarButtonStyle': 'icon_text'})
        self.assertEqual(self.refreshed, ['en'])
        self.assertEqual(self.widgets.messages[-1][-1], 'Workbench settings updated.')

    def test_failed_save_is_reported_without_refresh_or_language_switch(self):
        self.widgets.QDialog.result = 1
        self.widgets.QDialog.on_exec = self.choose_english
        self.preferences.fail_write = True
        views.show_settings_dialog(self.app, self.gui, lambda: self.refreshed.append(True))
        self.assertEqual(i18n.get_language(), 'zh')
        self.assertEqual(self.refreshed, [])
        self.assertEqual(self.widgets.messages[-1][0], 'warning')

    def test_settings_default_flags_cancel_and_restart_notice(self):
        views.show_settings_dialog(self.app, self.gui)
        self.assertEqual([check.checked for check in self.widgets.checks], [False])
        self.assertEqual(len(self.widgets.checks), 1)
        self.assertNotIn('停止 MCP 桥接前确认', [check.text for check in self.widgets.checks])
        self.assertTrue(any('下次 FreeCAD 启动' in label.text for label in self.widgets.labels))
        self.assertEqual(self.preferences.values, {})
        self.assertEqual(self.preferences.bools, {})
        self.assertEqual(self.preferences.writes, [])

    def test_settings_read_only_bridge_details_use_loaded_snapshot_version(self):
        snapshot = {'host': '127.0.0.1', 'port': 12345, 'addon_version': 'loaded-fixture'}
        views.show_settings_dialog(self.app, self.gui, bridge_snapshot=snapshot)
        labels = [label.text for label in self.widgets.labels]
        self.assertIn('桥接地址：127.0.0.1:12345', labels)
        self.assertIn('已加载插件版本：loaded-fixture', labels)
        self.assertEqual(len(self.widgets.combos), 2)
        self.assertEqual(len(self.widgets.checks), 1)
        self.assertEqual(self.preferences.writes, [])

    def test_settings_confirm_saves_flags_and_language_without_documents_or_mcp(self):
        self.widgets.QDialog.result = 1
        def choose():
            self.choose_english()
            self.widgets.combos[1].setCurrentIndex(1)
            self.widgets.checks[0].setChecked(True)
        self.widgets.QDialog.on_exec = choose
        views.show_settings_dialog(self.app, self.gui, lambda: self.refreshed.append(True))
        self.assertEqual(self.preferences.values, {'Language': 'en', 'ToolbarButtonStyle': 'icon'})
        self.assertEqual(self.preferences.bools, {'AutoStartMCP': True})
        self.assertEqual(self.refreshed, [True])

    def test_settings_dialog_uses_compact_defaults_and_wrapped_shrinkable_labels(self):
        views.show_settings_dialog(self.app, self.gui)
        self.assertEqual(self.widgets.dialogs[-1].initial_size, (520, 360))
        for label in self.widgets.labels:
            self.assertTrue(label.word_wrap)
            self.assertEqual(label.minimum_width, 0)
            self.assertEqual(label.sizePolicy().horizontal, 13)
        self.assertEqual(self.preferences.writes, [])
        self.assertEqual(self.refreshed, [])

    def test_toolbar_style_defaults_to_text_and_icons_and_cancel_preserves_preferences(self):
        def choose():
            self.assertEqual(self.widgets.combos[1].items, [("图标和文字", "icon_text"), ("仅图标", "icon")])
            self.assertEqual(self.widgets.combos[1].currentData(), "icon_text")
            self.widgets.combos[1].setCurrentIndex(1)
        self.widgets.QDialog.on_exec = choose
        views.show_settings_dialog(self.app, self.gui, lambda: self.refreshed.append(True))
        self.assertIn("工具条显示", [label.text for label in self.widgets.labels])
        self.assertEqual(self.preferences.writes, [])
        self.assertEqual(self.refreshed, [])

    def test_toolbar_style_save_is_available_to_refresh_callback_immediately(self):
        self.preferences.values["ToolbarButtonStyle"] = "icon"
        self.widgets.QDialog.result = 1
        def choose():
            self.assertEqual(self.widgets.combos[1].currentData(), "icon")
            self.widgets.combos[1].setCurrentIndex(0)
        self.widgets.QDialog.on_exec = choose
        views.show_settings_dialog(self.app, self.gui,
            lambda: self.refreshed.append(settings.load_toolbar_button_style(self.app)))
        self.assertEqual(self.preferences.values["ToolbarButtonStyle"], "icon_text")
        self.assertEqual(self.refreshed, ["icon_text"])
        self.assertEqual(self.preferences.bools, {"AutoStartMCP": False})

    def test_status_dialog_uses_supplied_snapshot_and_explains_chat_connection_boundary(self):
        snapshot = {'running': True, 'stopping': True, 'host': '127.0.0.1', 'port': 12345,
                    'addon_version': 'fixture', 'protocol_version': 1, 'unrelated_private_key': '/private/fixture'}
        before = copy.deepcopy(snapshot)
        views.show_mcp_status_dialog(self.gui, snapshot)
        kind, title, message = self.widgets.messages[-1]
        self.assertEqual(title, 'Phoenix：MCP 桥接状态')
        self.assertIn('正在停止', message)
        self.assertIn('127.0.0.1:12345', message)
        self.assertIn('fixture', message)
        self.assertIn('Node MCP', message)
        self.assertNotIn('/private/fixture', message)
        self.assertEqual(snapshot, before)
        i18n.set_language('en')
        views.show_mcp_status_dialog(self.gui, {**snapshot, 'stopping': False})
        self.assertEqual(self.widgets.messages[-1][1], 'Phoenix: MCP bridge status')
        self.assertIn('Running', self.widgets.messages[-1][2])
        self.assertIn('chat client connection separately', self.widgets.messages[-1][2])

    def test_stop_confirmation_defaults_cancel_and_warns_started_operations_continue(self):
        for language in ('zh', 'en'):
            with self.subTest(language=language):
                i18n.set_language(language)
                self.assertFalse(views.confirm_mcp_stop(self.gui))
                stop, cancel = self.widgets.boxes[-1].button(1), self.widgets.boxes[-1].button(2)
                self.assertTrue(cancel.default)
                self.assertFalse(stop.default)
                self.assertFalse(stop.auto_default)
                expected = '已经开始的操作不会被取消' if language == 'zh' else 'already started will not be cancelled'
                self.assertIn(expected, self.widgets.labels[-1].text)
        self.widgets.QDialog.result = 1
        self.assertTrue(views.confirm_mcp_stop(self.gui))

    def test_new_english_label_dialog_translates_chrome_and_keeps_user_values(self):
        i18n.set_language('en')
        row = {'name': 'PublicObject', 'old_label': '修改', 'new_label': '取消', 'error': ''}
        obj = types.SimpleNamespace(Name='PublicObject', Label='修改')
        doc = types.SimpleNamespace(Name='ExampleDoc', Label='Example document')
        with patch.object(views.controller, 'prepare_target_options', return_value=(doc, [obj], [obj.Name])), \
             patch.object(views.controller, 'prepare_rename_context', return_value=(doc, [obj], [], [row])):
            views.show_rename_dialog(self.app, self.gui)
        self.assertEqual(self.widgets.dialogs[-1].title, 'Phoenix: Preview label corrections')
        self.assertEqual(self.widgets.tables[-1].headers, ['Process', 'Internal Name', 'Previous Label', 'Proposed Label', 'Check result'])
        self.assertEqual(self.widgets.tables[-1].item(0, 2).text(), '修改')
        self.assertEqual(self.widgets.tables[-1].item(0, 3).text(), '取消')
        self.assertEqual(self.widgets.boxes[-1].button(1).text, 'Confirm changes')

    def test_english_property_dialog_keeps_field_identifiers_and_values_unchanged(self):
        i18n.set_language('en')
        fields = {field: {'value': '取消', 'exists': True} for field, _ in views.BOM_FIELDS}
        snapshots = [{'name': 'PublicObject', 'label': '修改', 'fields': fields}]
        before = copy.deepcopy(snapshots)
        obj = types.SimpleNamespace(Name='PublicObject', Label='修改')
        doc = types.SimpleNamespace(Name='ExampleDoc', Label='Example document')
        with patch.object(views.controller, 'prepare_target_options', return_value=(doc, [obj], [obj.Name])), \
             patch.object(views.controller, 'prepare_properties_context', return_value=(doc, [obj], snapshots)):
            views.show_properties_dialog(self.app, self.gui)
        table = self.widgets.tables[-1]
        self.assertEqual(table.headers, ['Process', 'Internal Name', 'Current Label'] + [field for field, _ in views.BOM_FIELDS] + ['Suggestion / Check'])
        self.assertEqual(table.item(0, 2).text(), '修改')
        self.assertEqual(table.item(0, 3).text(), '取消')
        self.assertEqual(snapshots, before)
        self.assertEqual(self.preferences.writes, [])


if __name__ == '__main__':
    unittest.main()
