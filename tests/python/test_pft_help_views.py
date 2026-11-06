# SPDX-License-Identifier: Apache-2.0
"""Help viewer navigation and passive singleton with a minimal Qt adapter."""
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / 'addon/PhoenixFreeCADTools'
_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_help_views as views
finally:
    sys.path[:] = _path


class Signal:
    def __init__(self): self.slots = []
    def connect(self, slot): self.slots.append(slot)
    def emit(self, *args):
        for slot in self.slots: slot(*args)


class FakeQt:
    def __init__(self):
        owner = self
        self.widgets, self.opened, self.find_results = [], [], []
        self.open_result = True
        class TextDocument:
            MarkdownDialectGitHub, MarkdownNoHTML, FindBackward = 1, 2, 4
            def __init__(self): self.calls = []
            def setMarkdown(self, text, features=1):
                self.markdown, self.features = text, features
                self.calls.append((text, features))
        class Widget:
            def __init__(self, *args):
                self._parent = args[-1] if args else None
                self.text_value = args[0] if args and isinstance(args[0], str) else ''
                self.destroyed, self.clicked, self.returnPressed, self.anchorClicked = Signal(), Signal(), Signal(), Signal()
                self.name, self.visible, self.show_count = '', False, 0
                owner.widgets.append(self)
            def parent(self): return self._parent
            def setObjectName(self, value): self.name = value
            def setWindowTitle(self, value): self.title = value
            def setModal(self, value): self.modal = value
            def setAttribute(self, *value): self.attribute = value
            def setMinimumWidth(self, value): pass
            def resize(self, *value): self.size = value
            def setPlaceholderText(self, value): self.placeholder = value
            def setMaxLength(self, value): self.maximum = value
            def setAutoDefault(self, value): self.auto_default = value
            def setDefault(self, value): self.default = value
            def setReadOnly(self, value): self.read_only = value
            def setOpenLinks(self, value): self.open_links = value
            def setOpenExternalLinks(self, value): self.open_external = value
            def setSearchPaths(self, value): self.paths = value
            def setWordWrap(self, value): pass
            def show(self): self.visible = True; self.show_count += 1
            def raise_(self): pass
            def activateWindow(self): pass
            def close(self): self.visible = False; self.destroyed.emit(self)
            def text(self): return self.text_value
            def setText(self, value): self.text_value = value
            def moveCursor(self, value): self.moved_to = value
            def find(self, query, flags=0):
                self.last_find = query, flags
                return owner.find_results.pop(0) if owner.find_results else False
            def scrollToAnchor(self, value): self.anchor = value
        class Dialog(Widget): pass
        class Browser(Widget):
            def __init__(self, *args):
                super().__init__(*args)
                self._document, self.markdown_calls = TextDocument(), []
            def document(self): return self._document
            def setMarkdown(self, text):
                # Match real QTextEdit/QTextBrowser: convenience API takes one
                # argument, while QTextDocument accepts MarkdownFeatures.
                self.markdown_calls.append(text)
                self._document.setMarkdown(text)
        class Line(Widget): pass
        class Label(Widget): pass
        class Button(Widget): pass
        class Layout:
            def __init__(self, *args): pass
            def addWidget(self, *args): pass
            def addLayout(self, *args): pass
        class Url:
            def __init__(self, value): self.value = value
            def toString(self): return self.value
        def open_url(url): owner.opened.append(url.toString()); return owner.open_result
        self.core = types.SimpleNamespace(Qt=types.SimpleNamespace(WA_DeleteOnClose=1), QUrl=Url, QByteArray=bytes)
        self.gui = types.SimpleNamespace(QTextDocument=TextDocument, QTextCursor=types.SimpleNamespace(Start=1, End=2),
            QDesktopServices=types.SimpleNamespace(openUrl=open_url))
        self.widgets_module = types.SimpleNamespace(QDialog=Dialog, QTextBrowser=Browser, QLineEdit=Line,
            QLabel=Label, QPushButton=Button, QVBoxLayout=Layout, QHBoxLayout=Layout)
        self.Dialog, self.Browser = Dialog, Browser

    def named(self, name): return next(widget for widget in self.widgets if widget.name == name)
    def button(self, title): return next(widget for widget in self.widgets if widget.text_value == title)


class HelpViewsTests(unittest.TestCase):
    def setUp(self):
        self.qt = FakeQt()
        self.window = object()
        self.gui = types.SimpleNamespace(getMainWindow=lambda: self.window)
        self.addCleanup(setattr, views, '_DIALOG', None)
        views._DIALOG = None
        self.patches = [patch.object(views, '_qt', return_value=(self.qt.core, self.qt.gui, self.qt.widgets_module)),
                        patch.object(views, 'read_help', return_value='# Local help\n\n`command`'),
                        patch.object(views, 'tr', side_effect=lambda text: text)]
        for item in self.patches: item.start(); self.addCleanup(item.stop)

    def test_help_is_nonmodal_singleton_without_cad_or_automatic_browser_access(self):
        first = views.show_help_dialog(self.gui)
        second = views.show_help_dialog(self.gui)
        self.assertIs(first, second)
        self.assertEqual(len([widget for widget in self.qt.widgets if isinstance(widget, self.qt.Dialog)]), 1)
        self.assertFalse(first.modal)
        self.assertEqual(first.show_count, 2)
        browser = self.qt.named('PhoenixTools_HelpText')
        self.assertTrue(browser.read_only)
        self.assertFalse(browser.open_links)
        self.assertFalse(browser.open_external)
        self.assertEqual((browser.paths, browser.document().features), ([], 3))
        self.assertEqual(browser.document().calls, [('# Local help\n\n`command`', 3)])
        self.assertEqual(browser.markdown_calls, [])
        self.assertEqual(self.qt.opened, [])
        self.qt.button('关闭').clicked.emit(False)
        self.assertIsNone(views._DIALOG)

    def test_resources_return_non_null_inert_bytes_to_stop_qt_fallback_loaders(self):
        views.show_help_dialog(self.gui)
        browser = self.qt.named('PhoenixTools_HelpText')
        for url in ('file:///tmp/example.png', 'data:image/png;base64,AA==', 'qrc:/icon', 'https://example.com/image'):
            self.assertEqual(browser.loadResource(2, self.qt.core.QUrl(url)), b'pft-help-resource-blocked')
        self.assertEqual(self.qt.opened, [])

    def test_links_only_follow_in_page_anchors_or_explicit_https_clicks(self):
        views.show_help_dialog(self.gui)
        browser, status = self.qt.named('PhoenixTools_HelpText'), self.qt.named('PhoenixTools_HelpStatus')
        browser.anchorClicked.emit(self.qt.core.QUrl('#tools'))
        self.assertEqual(browser.anchor, 'tools')
        for url in ('file:///tmp/example', 'javascript:example()', 'http://example.com', 'HELP.md'):
            browser.anchorClicked.emit(self.qt.core.QUrl(url))
            self.assertIn('HTTPS', status.text_value)
        self.assertEqual(self.qt.opened, [])
        browser.anchorClicked.emit(self.qt.core.QUrl('https://example.com/help'))
        self.assertEqual(self.qt.opened, ['https://example.com/help'])
        self.qt.open_result = False
        browser.anchorClicked.emit(self.qt.core.QUrl('https://example.com/help'))
        self.assertEqual(status.text_value, '无法打开链接，请使用系统浏览器。')

    def test_search_wraps_forward_and_backward_without_navigation_or_commands(self):
        views.show_help_dialog(self.gui)
        search, browser = self.qt.named('PhoenixTools_HelpSearch'), self.qt.named('PhoenixTools_HelpText')
        search.text_value = 'MCP'
        self.qt.find_results = [False, True]
        search.returnPressed.emit()
        self.assertEqual((browser.moved_to, browser.last_find), (1, ('MCP', 0)))
        self.qt.find_results = [False, True]
        self.qt.button('上一处').clicked.emit(False)
        self.assertEqual((browser.moved_to, browser.last_find), (2, ('MCP', 4)))
        self.qt.button('下一处').clicked.emit(False)
        self.assertEqual(self.qt.named('PhoenixTools_HelpStatus').text_value, '未找到匹配文字。')
        self.assertEqual(self.qt.opened, [])

    def test_missing_markdown_support_fails_without_constructing_a_dialog(self):
        del self.qt.gui.QTextDocument.MarkdownNoHTML
        with self.assertRaises(views.PftHelpError): views.show_help_dialog(self.gui)
        self.assertEqual(self.qt.widgets, [])

    def test_document_markdown_api_is_required_before_constructing_a_dialog(self):
        del self.qt.gui.QTextDocument.setMarkdown
        with self.assertRaises(views.PftHelpError): views.show_help_dialog(self.gui)
        self.assertEqual(self.qt.widgets, [])

    def test_widget_markdown_convenience_method_is_not_required(self):
        del self.qt.Browser.setMarkdown
        dialog = views.show_help_dialog(self.gui)
        self.assertTrue(dialog.visible)
        self.assertEqual(self.qt.named('PhoenixTools_HelpText').document().features, 3)

    def test_worker_thread_fails_before_reading_help_or_accessing_gui(self):
        failures = []
        with patch.object(views, 'read_help', side_effect=AssertionError('must not read')):
            def work():
                try: views.show_help_dialog(types.SimpleNamespace())
                except views.PftHelpError as error: failures.append(error)
            thread = threading.Thread(target=work)
            thread.start(); thread.join()
        self.assertEqual(len(failures), 1)
        self.assertEqual(self.qt.widgets, [])

    def test_facade_passes_only_gui_and_preserves_help_error_as_tool_feedback(self):
        _path = list(sys.path)
        try:
            sys.path.insert(0, str(ADDON))
            import pft_workbench_tools as tools
        finally:
            sys.path[:] = _path
        with patch.dict(sys.modules, {'FreeCADGui': self.gui}), patch.object(views, 'show_help_dialog', return_value='dialog') as show:
            self.assertEqual(tools.show_help_dialog(), 'dialog')
            show.assert_called_once_with(self.gui)
        with patch.dict(sys.modules, {'FreeCADGui': self.gui}), patch.object(views, 'show_help_dialog', side_effect=views.PftHelpError('帮助文档无法读取，请检查插件安装。')):
            with self.assertRaisesRegex(tools.PftToolError, '帮助文档无法读取'): tools.show_help_dialog()


if __name__ == '__main__': unittest.main()
