# SPDX-License-Identifier: Apache-2.0
"""Desk panel intent and lifecycle without importing Qt or touching a CAD model."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
prior_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_desk_views as views
    import pft_desk_prompt as prompt_module
finally:
    sys.path[:] = prior_path


class PftSignal:
    def __init__(self): self.callbacks = []
    def connect(self, callback): self.callbacks.append(callback)
    def emit(self, *args):
        for callback in self.callbacks: callback(*args)


class PftDeskError(RuntimeError):
    def __init__(self, code, message): self.code, self.message = code, message


class PftFakeWidgets:
    def __init__(self):
        self.objects, self.layouts = [], []
        self.clipboard_writes = []
        self.QApplication = types.SimpleNamespace(clipboard=lambda: types.SimpleNamespace(setText=self.clipboard_writes.append))
        owner = self
        class PftPolicy:
            Ignored = 13
            def __init__(self): self.horizontal = 5
            def setHorizontalPolicy(self, value): self.horizontal = value
        class PftWidget:
            def __init__(self, parent=None):
                self._parent, self._name, self.enabled, self.deleted = parent, '', True, False
                owner.objects.append(self)
            def setObjectName(self, name): self._name = name
            def objectName(self):
                if self.deleted: raise RuntimeError('deleted QObject')
                return self._name
            def parent(self): return self._parent
            def setEnabled(self, value): self.enabled = value
            def setMinimumWidth(self, value): self.minimum_width = value
            def setMinimumHeight(self, value): self.minimum_height = value
            def sizePolicy(self):
                if not hasattr(self, 'policy'): self.policy = PftPolicy()
                return self.policy
            def setSizePolicy(self, value): self.policy = value
        class PftDialog(PftWidget):
            def __init__(self, parent):
                super().__init__(parent)
                self.destroyed, self.visible, self.raised = PftSignal(), False, 0
            def setWindowTitle(self, title): self.title = title
            def resize(self, *args): self.initial_size = args
            def setAttribute(self, *args): pass
            def show(self): self.visible = True
            def raise_(self): self.raised += 1
            def activateWindow(self): pass
            def closeEvent(self, event): pass
            def close(self):
                event = types.SimpleNamespace(ignored=False)
                event.ignore = lambda: setattr(event, 'ignored', True)
                self.closeEvent(event)
                if not event.ignored:
                    self.visible, self.deleted = False, True
                    self.destroyed.emit(self)
            def reject(self): self.close()
        class PftLayout:
            def __init__(self, *args): self.children = []; owner.layouts.append(self)
            def addWidget(self, *args): self.children.append(args)
            def addLayout(self, *args): self.children.append(args)
            def setSpacing(self, value): self.spacing = value
        class PftForm(PftLayout):
            WrapLongRows = 1
            def __init__(self, *args): super().__init__(*args); self.rows = []
            def setRowWrapPolicy(self, value): self.wrap = value
            def addRow(self, *args): self.rows.append(args)
        class PftLabel(PftWidget):
            def __init__(self, text): super().__init__(); self.text = text
            def setWordWrap(self, value): self.word_wrap = value
            def setTextFormat(self, value): self.format = value
        class PftLineEdit(PftWidget):
            def __init__(self): super().__init__(); self._text, self.textChanged = '', PftSignal()
            def text(self): return self._text
            def setText(self, value):
                if value != self._text:
                    self._text = value
                    self.textChanged.emit(value)
        class PftButton(PftWidget):
            def __init__(self, text): super().__init__(); self.text, self.clicked = text, PftSignal()
            def click(self):
                if self.enabled: self.clicked.emit(False)
        class PftPlainText(PftWidget):
            def setPlainText(self, text): self.text = text
            def setReadOnly(self, value): self.read_only = value
        self.QDialog, self.QVBoxLayout, self.QHBoxLayout = PftDialog, PftLayout, PftLayout
        self.QGridLayout, self.QFormLayout = PftLayout, PftForm
        self.QLabel, self.QLineEdit, self.QPushButton, self.QPlainTextEdit = PftLabel, PftLineEdit, PftButton, PftPlainText
        self.QFileDialog = types.SimpleNamespace(getOpenFileName=lambda *args: ('/opt/example/node', ''),
            getExistingDirectory=lambda *args: '/var/example/private-state')
    def find(self, name): return next(obj for obj in reversed(self.objects) if obj._name == name and not obj.deleted)


class PftDeskViewTests(unittest.TestCase):
    def setUp(self):
        self.widgets = PftFakeWidgets()
        self.window = types.SimpleNamespace()
        self.window.findChild = lambda kind, name: next((obj for obj in self.widgets.objects
            if isinstance(obj, kind) and obj._name == name and not obj.deleted), None)
        self.gui = types.SimpleNamespace(getMainWindow=lambda: self.window)
        self.user_directory_reads = []
        owner = self
        class PftNoCadApp:
            @property
            def ActiveDocument(self): raise AssertionError('Desk must not read CAD state')
            def getUserAppDataDir(self):
                owner.user_directory_reads.append(True)
                return '/var/example/freecad-user'
        self.app = PftNoCadApp()
        self.config = {'node_executable': '/opt/example/node', 'cli_entry': '/opt/desk/scripts/desk-web-service.mjs', 'state_dir': '/var/example/desk-space'}
        self.saves, self.runs, self.controllers, self.opens = [], [], [], []
        owner = self
        class PftController:
            def __init__(self, runner, changed, open_url):
                self.busy, self.snapshot, self.changed = False, None, changed
                self.invalidations, self.open_url = 0, open_url
                owner.controllers.append(self)
            def invalidate(self):
                self.invalidations += 1
                self.snapshot = None
                self.changed({'busy': self.busy, 'snapshot': None, 'error': None})
            def run(self, config, action):
                owner.runs.append((dict(config), action))
                self.busy = True
                self.changed({'busy': True, 'snapshot': None, 'error': None})
            def complete(self, snapshot):
                self.busy, self.snapshot = False, snapshot
                self.changed({'busy': False, 'snapshot': snapshot, 'error': None})
        controller_module = types.SimpleNamespace(PftDeskError=PftDeskError, PftDeskController=PftController,
            load_desk_configuration=lambda app: dict(owner.config),
            save_desk_configuration=lambda app, config: owner.saves.append(dict(config)) or dict(config))
        process_module = types.SimpleNamespace(PftDeskProcess=lambda core, parent: object())
        core = types.SimpleNamespace(Qt=types.SimpleNamespace(WidgetAttribute=types.SimpleNamespace(WA_DeleteOnClose=55),
            TextFormat=types.SimpleNamespace(PlainText=0)))
        self.patches = [patch.object(views, '_qt', return_value=(core, self.widgets)),
            patch.object(views, '_open_url', side_effect=lambda core, url: owner.opens.append(url) or True),
            patch.dict(sys.modules, {'pft_desk_controller': controller_module, 'pft_desk_qprocess': process_module})]
        for item in self.patches: item.start(); self.addCleanup(item.stop)
        views._pft_dialog = None
        self.addCleanup(lambda: setattr(views, '_pft_dialog', None))
    def show(self): return views.show_desk_dialog(self.app, self.gui)
    def control(self, suffix): return self.widgets.find('PhoenixTools_Desk' + suffix)
    def ready(self): return {'state': 'ready', 'reason': '<b>plain text only</b>', 'web_url': 'http://127.0.0.1:54321/', 'reused': True}

    def test_compact_defaults_keep_long_paths_and_actions_within_resizable_layouts(self):
        self.config = {key: value + '/public-fixture-directory' * 30 for key, value in self.config.items()}
        dialog = views.show_desk_dialog(self.app, self.gui)
        self.assertEqual(dialog.initial_size, (680, 480))
        for suffix in ('NodeExecutable', 'CliEntry', 'StateDirectory', 'StatusText'):
            widget = self.control(suffix)
            self.assertEqual(widget.minimum_width, 0)
            self.assertEqual(widget.sizePolicy().horizontal, 13)
        form = next(layout for layout in self.widgets.layouts if isinstance(layout, self.widgets.QFormLayout))
        self.assertEqual(form.wrap, self.widgets.QFormLayout.WrapLongRows)
        self.assertEqual(len(form.rows), 3)
        action_names = {'PhoenixTools_Desk' + suffix for suffix in (
            'SaveConfiguration', 'Status', 'Ensure', 'OpenWeb', 'CopySetupPrompt')}
        cells = [args for layout in self.widgets.layouts for args in layout.children
                 if len(args) == 5 and getattr(args[0], '_name', '') in action_names]
        self.assertEqual({args[0].objectName() for args in cells}, action_names)
        self.assertEqual({args[1] for args in cells}, {0, 1})
        self.assertEqual(self.runs, [])
        self.assertEqual(self.saves, [])
        self.assertEqual(self.opens, [])

    def test_open_is_non_modal_does_not_save_or_start_and_reuses_live_dialog(self):
        first = self.show()
        self.assertTrue(first.visible)
        self.assertEqual(first.objectName(), 'PhoenixTools_DeskDialog')
        self.assertFalse(self.control('OpenWeb').enabled)
        self.assertEqual(self.saves, [])
        self.assertEqual(self.runs, [])
        self.assertIs(self.show(), first)
        views._pft_dialog = None  # The parent lookup also prevents duplicates.
        self.assertIs(self.show(), first)
        self.assertEqual(len(self.controllers), 1)

    def test_save_is_independent_and_close_does_not_save_or_call_service(self):
        dialog = self.show()
        self.control('SaveConfiguration').click()
        self.assertEqual(self.saves, [self.config])
        self.assertEqual(self.runs, [])
        self.assertIn('已保存', self.control('StatusText').text)
        dialog.close()
        self.assertTrue(dialog.deleted)
        self.assertEqual(self.runs, [])
        self.assertIsNone(views._pft_dialog)

    def test_ready_is_invalidated_by_field_edit_and_open_routes_through_controller(self):
        self.show()
        self.control('Status').click()
        self.controllers[-1].complete(self.ready())
        self.assertTrue(self.control('OpenWeb').enabled)
        self.assertIn('<b>plain text only</b>', self.control('StatusText').text)
        self.control('StateDirectory').setText('/var/example/another-space')
        self.assertFalse(self.control('OpenWeb').enabled)
        self.control('StateDirectory').setText(self.config['state_dir'])
        self.assertFalse(self.control('OpenWeb').enabled)
        self.control('Ensure').click()
        self.controllers[-1].complete(self.ready())
        self.control('OpenWeb').click()
        self.assertEqual([action for config, action in self.runs], ['status', 'ensure', 'open'])
        self.assertEqual(self.opens, [])  # Only controller completion may open.

    def test_busy_disables_edits_and_rejects_close_and_escape_without_stopping_service(self):
        dialog = self.show()
        self.control('Ensure').click()
        for suffix in ('NodeExecutable', 'CliEntry', 'StateDirectory', 'SaveConfiguration', 'Status', 'Ensure', 'OpenWeb', 'CopySetupPrompt'):
            self.assertFalse(self.control(suffix).enabled)
        self.control('CopySetupPrompt').click()
        self.assertEqual(self.widgets.clipboard_writes, [])
        self.assertEqual(self.user_directory_reads, [])
        dialog.close()
        dialog.reject()
        self.assertFalse(dialog.deleted)
        self.assertIn('请等待', self.control('StatusText').text)
        self.assertEqual([action for config, action in self.runs], ['ensure'])
        self.controllers[-1].complete({'state': 'absent', 'reason': 'Not running'})
        dialog.reject()
        self.assertTrue(dialog.deleted)
        self.assertEqual([action for config, action in self.runs], ['ensure'])

    def test_destroyed_reference_is_not_reused(self):
        first = self.show()
        first.close()
        views._pft_dialog = first  # Simulate a stale wrapper surviving reload.
        second = self.show()
        self.assertIsNot(second, first)
        self.assertEqual(len(self.controllers), 2)

    def test_programmatic_edit_while_busy_cannot_reenable_open_from_old_completion(self):
        self.show()
        self.control('Status').click()
        self.control('StateDirectory').setText('/var/example/edited-while-busy')
        self.controllers[-1].complete(self.ready())
        self.assertFalse(self.control('OpenWeb').enabled)
        self.assertEqual(self.saves, [])

    def test_copy_is_explicit_uses_live_fields_and_keeps_ready_snapshot(self):
        self.config['state_dir'] = ''
        self.show()
        self.assertEqual(self.widgets.clipboard_writes, [])
        self.assertEqual(self.user_directory_reads, [])
        self.control('NodeExecutable').setText('/opt/another/node')
        self.control('Status').click()
        controller = self.controllers[-1]
        snapshot = self.ready()
        controller.complete(snapshot)
        invalidations = controller.invalidations
        self.assertTrue(self.control('OpenWeb').enabled)
        self.control('CopySetupPrompt').click()
        self.assertEqual(len(self.widgets.clipboard_writes), 1)
        text = self.widgets.clipboard_writes[0]
        self.assertIn('/opt/another/node', text)
        self.assertIn('/var/example/freecad-user/PhoenixTools/DeskBrowser', text)
        self.assertNotIn(snapshot['web_url'], text)
        self.assertIs(controller.snapshot, snapshot)
        self.assertEqual(controller.invalidations, invalidations)
        self.assertTrue(self.control('OpenWeb').enabled)
        self.assertEqual([action for config, action in self.runs], ['status'])
        self.assertEqual(self.saves, [])
        self.assertIn('已复制', self.control('StatusText').text)

    def test_invalid_path_does_not_copy_or_expose_the_value(self):
        self.show()
        invalid = 'relative-private-path'
        self.control('NodeExecutable').setText(invalid)
        self.control('CopySetupPrompt').click()
        self.assertEqual(self.widgets.clipboard_writes, [])
        self.assertIn('未复制', self.control('StatusText').text)
        self.assertNotIn(invalid, self.control('StatusText').text)
        self.assertEqual(self.runs, [])
        self.assertEqual(self.saves, [])

    def test_copy_failure_is_plain_and_does_not_leak_native_error(self):
        self.show()
        def fail(): raise RuntimeError('/private/example/secret-native-error')
        self.widgets.QApplication.clipboard = fail
        self.control('CopySetupPrompt').click()
        self.assertIn('无法复制', self.control('StatusText').text)
        self.assertNotIn('secret-native-error', self.control('StatusText').text)
        self.assertEqual(self.widgets.clipboard_writes, [])
        self.assertEqual(self.runs, [])
        self.assertEqual(self.saves, [])


if __name__ == '__main__': unittest.main()
