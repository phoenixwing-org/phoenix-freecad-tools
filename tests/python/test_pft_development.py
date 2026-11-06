# SPDX-License-Identifier: Apache-2.0
"""Development UI guards and readiness with synthetic modules/widgets only."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / 'addon' / 'PhoenixFreeCADTools'
spec = importlib.util.spec_from_file_location('pft_development_test_module', ADDON / 'pft_development.py')
development = importlib.util.module_from_spec(spec)
spec.loader.exec_module(development)
NS = types.SimpleNamespace


class Widget:
    def __init__(self, name='', parent=None): self.name, self.owner = name, parent
    def objectName(self): return self.name
    def parent(self): return self.owner
    def isVisible(self): return True


class Dock(Widget):
    def close(self): self.closed = True; return True


class Dialog(Widget):
    pass


class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pft-development-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.children, self.properties = [], {}
        self.window = NS(findChildren=lambda kind: self.children[:], setProperty=self.properties.__setitem__,
                         setWindowTitle=lambda title: setattr(self, 'title', title))
        self.app = NS(getActiveTransaction=lambda: None, listDocuments=lambda: {})
        self.configuration = {}
        self.app.ConfigSet = self.configuration.__setitem__
        self.app.ConfigGet = self.configuration.get
        self.gui = NS(getMainWindow=lambda: self.window)
        self.widgets = NS(QWidget=Widget, QDockWidget=Dock, QDialog=Dialog)
        handle = patch.dict(os.environ, {'PFT_DEVELOPMENT': '1'})
        handle.start(); self.addCleanup(handle.stop)
        handle = patch.dict(sys.modules, {'pft_business': None, 'pft_runtime': None, 'pft_bridge': None})
        handle.start(); self.addCleanup(handle.stop)

    def test_non_development_registration_is_inert(self):
        with patch.dict(os.environ, {'PFT_DEVELOPMENT': '0'}):
            self.assertFalse(development.register(object(), object(), object()))
            with self.assertRaises(development.PftDevelopmentError):
                development.reload_ui(self.app, self.gui, self.widgets)

    def test_transactions_edits_and_previews_refuse_without_closing(self):
        dock = Dock('PhoenixTools_DocumentToolsDock', self.window)
        self.children.append(dock)
        self.app.getActiveTransaction = lambda: ('owned by other', 12)
        with self.assertRaises(development.PftDevelopmentError): development.reload_ui(self.app, self.gui, self.widgets)
        self.assertFalse(hasattr(dock, 'closed'))
        self.app.getActiveTransaction = lambda: None
        self.app.listDocuments = lambda: {'Synthetic': NS(HasPendingTransaction=False)}
        self.gui.getDocument = lambda name: NS(getInEdit=lambda: object())
        with self.assertRaises(development.PftDevelopmentError): development.reload_ui(self.app, self.gui, self.widgets)
        self.app.listDocuments = lambda: {}
        self.children[:] = [NS(_pft_session=NS(state='previewing'))]
        with self.assertRaises(development.PftDevelopmentError): development.reload_ui(self.app, self.gui, self.widgets)

    def test_connected_chat_is_not_read_or_disconnected(self):
        class Controller:
            _connected = True
            _phase = 'ready'
            @property
            def snapshot(self): raise AssertionError('must not read transcript')
            def disconnect(self): raise AssertionError('must not disconnect')
        dock = Dock('PhoenixTools_ChatConnectionDialog', self.window)
        dock._controller, dock._process = Controller(), NS(_session=object())
        self.children.append(dock)
        with self.assertRaises(development.PftDevelopmentError): development.reload_ui(self.app, self.gui, self.widgets)
        self.assertFalse(hasattr(dock, 'closed'))

    def test_reload_is_limited_to_exact_same_source_ui_modules(self):
        filename = self.root / 'pft_appearance_controls.py'
        filename.write_text('VERSION = "new synthetic UI"\n', encoding='utf-8')
        previous = types.ModuleType('pft_appearance_controls')
        previous.__file__ = str(filename)
        backend = NS(sentinel=object())
        with patch.object(development, '_SOURCE', self.root), patch.object(development, 'UI_MODULES', ('pft_appearance_controls',)), \
                patch.dict(sys.modules, {'pft_appearance_controls': previous, 'pft_workbench_controller': backend}):
            result = development.reload_ui(self.app, self.gui, self.widgets)
            self.assertEqual(result['reloaded'], ['pft_appearance_controls'])
            self.assertEqual(sys.modules['pft_appearance_controls'].VERSION, 'new synthetic UI')
            self.assertIs(sys.modules['pft_workbench_controller'], backend)
            previous.__file__ = str(self.root / 'different-source.py')
            sys.modules['pft_appearance_controls'] = previous
            with self.assertRaises(development.PftDevelopmentError): development.reload_ui(self.app, self.gui, self.widgets)

    def test_approval_preview_refuses_without_cancelling(self):
        business = NS(__file__=str(ADDON / 'pft_business.py'), _lock=threading.RLock(),
                      _plans={'synthetic-token': {'state': 'awaiting_confirmation'}})
        with patch.dict(sys.modules, {'pft_business': business}):
            with self.assertRaises(development.PftDevelopmentError): development.reload_ui(self.app, self.gui, self.widgets)
            self.assertEqual(business._plans['synthetic-token']['state'], 'awaiting_confirmation')

    def test_bridge_private_runtime_and_business_helpers_also_block_reload(self):
        runtime = NS(__file__=str(ADDON / 'pft_runtime.py'), _lock=threading.RLock(),
                     _scripts={'synthetic-job': {'state': 'running'}})
        bridge = NS(__file__=str(ADDON / 'pft_bridge.py'), _runtime=runtime)
        with patch.dict(sys.modules, {'pft_bridge': bridge}):
            with self.assertRaises(development.PftDevelopmentError): development.reload_ui(self.app, self.gui, self.widgets)
            runtime._scripts.clear()
            bridge._business = NS(__file__=str(ADDON / 'pft_business.py'), _lock=threading.RLock(),
                                  _plans={'synthetic-plan': {'state': 'prepared'}})
            with self.assertRaises(development.PftDevelopmentError): development.reload_ui(self.app, self.gui, self.widgets)

    def test_startup_receipt_activates_ui_only_and_never_overwrites(self):
        profile = self.root / 'profile'
        profile.mkdir(mode=0o700)
        (profile / 'runtime').mkdir(mode=0o700)
        (profile / 'config').mkdir(mode=0o700)
        (profile / 'config' / 'qt').mkdir(mode=0o700)
        (profile / 'config' / 'qt-system').mkdir(mode=0o700)
        marker = {'owner': 'phoenix-freecad-tools-development', 'uid': os.getuid(),
                  'source': str(ADDON), 'owner_id': 'synthetic-owner'}
        markerfile = profile / '.phoenix-development-profile.json'
        markerfile.write_text(json.dumps(marker), encoding='utf-8'); markerfile.chmod(0o600)
        receipt = profile / 'runtime' / 'readiness-synthetic.json'
        self.gui.activateWorkbench = lambda name: setattr(self, 'activated', name)
        calls = []
        settings = NS(IniFormat='ini', UserScope='user', SystemScope='system',
                      setPath=lambda *args: calls.append(('path', *args)),
                      setDefaultFormat=lambda value: calls.append(('format', value)))
        identity_calls = []
        qcore = NS(setOrganizationName=lambda value: identity_calls.append(('organization', value)),
                   setOrganizationDomain=lambda value: identity_calls.append(('domain', value)),
                   setApplicationName=lambda value: identity_calls.append(('application', value)))
        qt = NS(QtCore=NS(QSettings=settings, QCoreApplication=qcore), QtGui=NS(QGuiApplication=NS(platformName=lambda: 'cocoa')))
        self.visible = False
        self.window.showNormal = lambda: setattr(self, 'visible', True)
        self.window.raise_ = lambda: calls.append(('raise',))
        self.window.activateWindow = lambda: calls.append(('activate',))
        self.window.isVisible = lambda: self.visible
        modules = {name: NS(__file__=str(ADDON / (name + '.py')))
                   for name in ('pft_development', 'pft_workbench_gui', 'pft_bridge')}
        environment = {'PFT_DEVELOPMENT_PROFILE': str(profile), 'PFT_DEVELOPMENT_OWNER_ID': 'synthetic-owner',
                       'PFT_DEVELOPMENT_RECEIPT': str(receipt), 'PFT_DEVELOPMENT_PORT': '9877'}
        with patch.dict(os.environ, environment), patch.dict(sys.modules, {**modules, 'PySide': qt}):
            result = development.write_readiness(self.app, self.gui)
            self.assertEqual(self.activated, 'PftWorkbench')
            self.assertIn('Phoenix Development', self.title)
            self.assertIs(result['automatic_service_start'], False)
            self.assertTrue(self.properties['PhoenixTools_AutoStartSuppressed'])
            self.assertIs(result['ready'], True)
            self.assertIs(result['window_visible'], True)
            self.assertEqual(result['qt_platform'], 'cocoa')
            vendor = 'org.phoenix.freecadtools.development.syntheticowner'
            self.assertEqual(self.configuration, {'ExeVendor': vendor, 'ExeName': 'PhoenixFreeCADDevelopment'})
            self.assertEqual(identity_calls[:3], [('organization', vendor), ('domain', vendor),
                                                  ('application', 'PhoenixFreeCADDevelopment')])
            self.assertEqual(calls[:3], [('path', 'ini', 'user', str(profile / 'config' / 'qt')),
                                        ('path', 'ini', 'system', str(profile / 'config' / 'qt-system')),
                                        ('format', 'ini')])
            self.assertIn(('raise',), calls)
            self.assertIn(('activate',), calls)
            self.assertEqual(json.loads(receipt.read_text())['module_paths'], {name: module.__file__ for name, module in modules.items()})
            with self.assertRaises(FileExistsError): development.write_readiness(self.app, self.gui)
            receipt.unlink()
            self.window.showNormal = lambda: None
            self.visible = False
            self.assertIs(development.write_readiness(self.app, self.gui)['ready'], False)


if __name__ == '__main__':
    unittest.main()
