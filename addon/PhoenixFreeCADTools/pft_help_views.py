# SPDX-License-Identifier: Apache-2.0
"""One nonmodal local Markdown viewer; no document or service access."""
import threading

from pft_help import PftHelpError, help_link, read_help
from pft_workbench_i18n import tr

DIALOG_NAME = 'PhoenixTools_HelpDialog'
_DIALOG = None


def _qt():
    from PySide import QtCore, QtGui
    try:
        from PySide import QtWidgets
    except ImportError:
        from PySide import QtGui as QtWidgets
    return QtCore, QtGui, QtWidgets


def _enum(owner, group, name):
    value = getattr(owner, name, None)
    return value if value is not None else getattr(getattr(owner, group), name)


def show_help_dialog(gui):
    global _DIALOG
    if threading.current_thread() is not threading.main_thread():
        raise PftHelpError(tr('帮助窗口必须在 GUI 主线程打开。'))
    window = gui.getMainWindow()
    if _DIALOG is not None:
        try:
            if _DIALOG.parent() is window:
                _DIALOG.show()
                _DIALOG.raise_()
                _DIALOG.activateWindow()
                return _DIALOG
        except RuntimeError:
            _DIALOG = None
    markdown = read_help()
    core, qtgui, widgets = _qt()
    try:
        features = (_enum(qtgui.QTextDocument, 'MarkdownFeature', 'MarkdownDialectGitHub')
                    | _enum(qtgui.QTextDocument, 'MarkdownFeature', 'MarkdownNoHTML'))
        if not callable(getattr(qtgui.QTextDocument, 'setMarkdown', None)):
            raise AttributeError()
    except AttributeError:
        raise PftHelpError(tr('当前 Qt 不支持 Markdown 帮助显示，请更新兼容 FreeCAD。')) from None

    class LocalHelpBrowser(widgets.QTextBrowser):
        def loadResource(self, resource_type, url):
            # Block every embedded resource: file/qrc/data/network images, CSS,
            # stylesheets and documents. setMarkdown receives in-memory text.
            # An invalid/null QVariant permits QTextDocument to fall back to
            # file/data loading or a global resource provider. Nonempty inert
            # bytes stop those fallbacks without providing an image or CSS.
            return core.QByteArray(b'pft-help-resource-blocked')

    dialog = widgets.QDialog(window)
    dialog.setObjectName(DIALOG_NAME)
    dialog.setWindowTitle(tr('Phoenix Tools 帮助'))
    dialog.setModal(False)
    dialog.setAttribute(_enum(core.Qt, 'WidgetAttribute', 'WA_DeleteOnClose'), True)
    dialog.setMinimumWidth(0)
    dialog.resize(880, 680)
    layout = widgets.QVBoxLayout(dialog)
    search_row = widgets.QHBoxLayout()
    search_row.addWidget(widgets.QLabel(tr('查找：'), dialog))
    search = widgets.QLineEdit(dialog)
    search.setObjectName('PhoenixTools_HelpSearch')
    search.setPlaceholderText(tr('搜索帮助文字'))
    search.setMaxLength(256)
    search.setMinimumWidth(0)
    search_row.addWidget(search, 1)
    previous = widgets.QPushButton(tr('上一处'), dialog)
    following = widgets.QPushButton(tr('下一处'), dialog)
    for button in (previous, following):
        button.setAutoDefault(False)
        button.setDefault(False)
        search_row.addWidget(button)
    layout.addLayout(search_row)
    browser = LocalHelpBrowser(dialog)
    browser.setObjectName('PhoenixTools_HelpText')
    browser.setMinimumWidth(0)
    browser.setReadOnly(True)
    browser.setOpenLinks(False)
    browser.setOpenExternalLinks(False)
    browser.setSearchPaths([])
    browser.document().setMarkdown(markdown, features)
    layout.addWidget(browser, 1)
    footer = widgets.QHBoxLayout()
    status = widgets.QLabel('', dialog)
    status.setObjectName('PhoenixTools_HelpStatus')
    status.setWordWrap(True)
    footer.addWidget(status, 1)
    close = widgets.QPushButton(tr('关闭'), dialog)
    close.setAutoDefault(False)
    close.setDefault(False)
    footer.addWidget(close)
    layout.addLayout(footer)

    def find(backwards=False):
        query = search.text()
        if not query:
            status.setText('')
            return
        def attempt():
            return (browser.find(query, _enum(qtgui.QTextDocument, 'FindFlag', 'FindBackward'))
                    if backwards else browser.find(query))
        found = attempt()
        if not found:
            browser.moveCursor(_enum(qtgui.QTextCursor, 'MoveOperation', 'End' if backwards else 'Start'))
            found = attempt()
        status.setText('' if found else tr('未找到匹配文字。'))

    def clicked(url):
        target = help_link(url.toString())
        if target is None:
            status.setText(tr('仅支持本页目录和 HTTPS 帮助链接。'))
        elif target[0] == 'anchor':
            browser.scrollToAnchor(target[1])
            status.setText('')
        elif not qtgui.QDesktopServices.openUrl(core.QUrl(target[1])):
            status.setText(tr('无法打开链接，请使用系统浏览器。'))
        else:
            status.setText('')

    def released(*_):
        global _DIALOG
        if _DIALOG is dialog:
            _DIALOG = None

    previous.clicked.connect(lambda *_: find(True))
    following.clicked.connect(lambda *_: find(False))
    search.returnPressed.connect(lambda: find(False))
    browser.anchorClicked.connect(clicked)
    close.clicked.connect(lambda *_: dialog.close())
    dialog.destroyed.connect(released)
    _DIALOG = dialog
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
