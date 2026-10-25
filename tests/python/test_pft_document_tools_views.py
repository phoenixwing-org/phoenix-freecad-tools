# SPDX-License-Identifier: Apache-2.0
"""Native panel behavior through a minimal injected Qt adapter, without GUI use."""
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from test_pft_document_tools import PftDoc, PftApp, PftGui, PftObject

ADDON = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools"
_saved_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_document_tools_views as views
    import pft_workbench_i18n as i18n
finally:
    sys.path[:] = _saved_path


class PftSignal:
    def __init__(self): self.slots = []
    def connect(self, slot): self.slots.append(slot)
    def emit(self, *args):
        for slot in list(self.slots): slot(*args)


class PftWidgets:
    def __init__(self):
        owner = self
        self.objects, self.messages, self.clipboard_values = [], [], []
        self.clipboard_calls, self.save_calls, self.exec_calls = 0, 0, 0
        self.save_choice, self.preview_result, self.on_preview = "", 0, None
        self.int_choice, self.int_calls, self.on_int = (50, True), [], None
        class SizePolicy:
            Expanding, Fixed, Preferred, Ignored = 7, 0, 5, 13
            Policy = types.SimpleNamespace(Expanding=7, Fixed=0, Preferred=5, Ignored=13)
            def __init__(self, horizontal=5, vertical=5): self.horizontal, self.vertical = horizontal, vertical
            def setHorizontalPolicy(self, value): self.horizontal = value
            def horizontalPolicy(self): return self.horizontal
        class Base:
            def __init__(self, *args):
                self._parent = next((arg for arg in args if isinstance(arg, Base)), None)
                self._name, self.enabled, self.visible, self.deleted = "", True, False, False
                self.destroyed = PftSignal()
                self._size_policy, self._width, self._height = SizePolicy(), 320, 320
                owner.objects.append(self)
            def setObjectName(self, value): self._name = value
            def objectName(self): return self._name
            def parent(self): return self._parent
            def setEnabled(self, value): self.enabled = value
            def setWindowTitle(self, value): self.title = value
            def resize(self, width, height):
                self._width, self._height = width, height
                self.resizeEvent(None)
            def resizeEvent(self, _event): pass
            def contentsRect(self):
                return types.SimpleNamespace(width=lambda: self._width, height=lambda: self._height)
            def setAttribute(self, *_): pass
            def setTextFormat(self, *_): pass
            def setToolTip(self, value): self.tooltip = value
            def setWordWrap(self, value): self.word_wrap = value
            def setVisible(self, value): self.visible = bool(value)
            def setMinimumWidth(self, value): self.minimum_width = value
            def setMinimumHeight(self, value): self.minimum_height = value
            def setMaximumHeight(self, value): self.maximum_height = value
            def setSizePolicy(self, *value):
                self.size_policy = value
                self._size_policy = value[0] if len(value) == 1 else SizePolicy(*value)
            def sizePolicy(self): return self._size_policy
            def show(self): self.visible = True
            def raise_(self): pass
            def activateWindow(self): pass
            def close(self):
                self.deleted, self.visible = True, False
                self.destroyed.emit(self)
            def findChildren(self, kind, name):
                return [item for item in owner.objects if isinstance(item, kind) and item._parent is self and item._name == name and not item.deleted]
            def findChild(self, kind, name):
                found = self.findChildren(kind, name)
                return found[0] if found else None
        class Dialog(Base):
            Accepted = 1
            def accept(self): self.result = 1
            def reject(self): self.result = 0
            def exec(self):
                owner.exec_calls += 1
                if owner.on_preview: owner.on_preview()
                return owner.preview_result
        class Dock(Base):
            def setMinimumWidth(self, value): self.minimum_width = value
            def setWidget(self, value): self.content = value
        class Scroll(Base):
            AdjustIgnored = 0
            def setWidgetResizable(self, value): self.widget_resizable = value
            def setFrameShape(self, value): self.frame = value
            def setHorizontalScrollBarPolicy(self, value): self.horizontal_scroll_policy = value
            def setWidget(self, value): self.content = value; value._parent = self
        class Layout:
            def __init__(self, *_): pass
            def addWidget(self, *_): pass
            def addLayout(self, *_): pass
            def setContentsMargins(self, *_): pass
        class Form(Layout):
            WrapLongRows, AllNonFixedFieldsGrow = 1, 2
            def setRowWrapPolicy(self, value): self.wrap = value
            def setFieldGrowthPolicy(self, value): self.grow = value
            def addRow(self, *args): self.row = args
        class Label(Base):
            def __init__(self, text, *args): super().__init__(*args); self.text = text
            def setText(self, value): self.text = value
        class Button(Base):
            def __init__(self, text="", *args): super().__init__(*args); self.text, self.default = text, False; self.clicked = PftSignal()
            def setText(self, value): self.text = value
            def setDefault(self, value): self.default = value
        class ToolButton(Button):
            def __init__(self, *args):
                super().__init__("", *args)
                self.checked, self.checkable, self.toggled = False, False, PftSignal()
                self.clicked.connect(lambda *_: self.setChecked(not self.checked) if self.checkable else None)
            def setCheckable(self, value): self.checkable = value
            def setChecked(self, value):
                changed = self.checked != bool(value)
                self.checked = bool(value)
                if changed: self.toggled.emit(self.checked)
            def isChecked(self): return self.checked
            def setArrowType(self, value): self.arrow = value
            def setToolButtonStyle(self, value): self.style = value
            def setIcon(self, value): self.icon = value
        class Check(Button):
            def __init__(self, *args): super().__init__("", *args); self.checked = False; self.stateChanged = PftSignal()
            def setChecked(self, value):
                changed = self.checked != value
                self.checked = value
                if changed: self.stateChanged.emit(int(value))
            def isChecked(self): return self.checked
        class Text(Base):
            def setReadOnly(self, value): self.readonly = value
            def setPlainText(self, value): self.text = value
            def toPlainText(self): return getattr(self, "text", "")
        class Combo(Base):
            AdjustToMinimumContentsLengthWithIcon = 3
            def setMinimumContentsLength(self, value): self.minimum_contents_length = value
            def setSizeAdjustPolicy(self, value): self.size_adjust_policy = value
            def __init__(self, *args): super().__init__(*args); self.rows, self.index = [], -1; self.currentIndexChanged = PftSignal()
            def clear(self): self.rows, self.index = [], -1
            def addItem(self, label, data): self.rows.append((label, data))
            def count(self): return len(self.rows)
            def currentData(self): return self.rows[self.index][1] if 0 <= self.index < len(self.rows) else None
            def setCurrentIndex(self, index):
                changed = self.index != index
                self.index = index
                if changed: self.currentIndexChanged.emit(index)
        class Table(Base):
            def __init__(self, rows, columns, *args): super().__init__(*args); self.rows, self.columns, self.cells = rows, columns, {}
            def setHorizontalHeaderLabels(self, labels): self.headers = labels
            def setRowCount(self, value): self.rows = value; self.cells = {}
            def setCellWidget(self, row, col, widget): self.cells[(row, col)] = widget
            def setItem(self, row, col, item): self.cells[(row, col)] = item
            def setSizeAdjustPolicy(self, value): self.size_adjust_policy = value
            def resizeColumnsToContents(self):
                self.widths = {column: max([len(self.headers[column]) * 14 + 20]
                    + [len(item.text) * 14 + 20 for (row, col), item in self.cells.items()
                       if col == column and hasattr(item, "text")]) for column in range(self.columns)}
            def columnWidth(self, column): return self.widths[column]
            def setColumnWidth(self, column, value): self.widths[column] = value
        class Item:
            def __init__(self, text): self.text, self._flags = text, 7
            def setToolTip(self, value): self.tooltip = value
            def flags(self): return self._flags
            def setFlags(self, value): self._flags = value
        class Tabs(Base):
            def __init__(self, *args): super().__init__(*args); self.tabs = []
            def addTab(self, editor, label): self.tabs.append((editor, label))
            def setUsesScrollButtons(self, value): self.scroll_buttons = value
            def setElideMode(self, value): self.elide = value
        class Box(Base):
            Ok, Cancel = 1, 2
            def __init__(self, *args):
                super().__init__(*args)
                self.accepted, self.rejected = PftSignal(), PftSignal()
                self.buttons = {1: Button(), 2: Button()}
            def button(self, key): return self.buttons[key]
        class FileDialog:
            DontConfirmOverwrite = 4
            @staticmethod
            def getSaveFileName(*args, **kwargs):
                owner.save_calls += 1
                owner.save_options = kwargs
                owner.save_arguments = args
                return owner.save_choice, "Markdown"
        class InputDialog:
            @staticmethod
            def getInt(parent, title, label, value, minimum, maximum, step):
                owner.int_calls.append((title, label, value, minimum, maximum, step))
                if owner.on_int: owner.on_int()
                return owner.int_choice
        class Application:
            @staticmethod
            def clipboard():
                owner.clipboard_calls += 1
                return types.SimpleNamespace(setText=owner.clipboard_values.append)
        class Messages:
            @staticmethod
            def warning(*args): owner.messages.append(("warning", args[-1]))
            @staticmethod
            def information(*args): owner.messages.append(("information", args[-1]))
        self.QDialog, self.QDockWidget, self.QWidget = Dialog, Dock, Base
        self.QVBoxLayout, self.QHBoxLayout, self.QFormLayout = Layout, Layout, Form
        self.QLabel, self.QPushButton, self.QCheckBox, self.QPlainTextEdit = Label, Button, Check, Text
        self.QToolButton = ToolButton
        self.QSizePolicy = SizePolicy
        self.QAbstractScrollArea, self.QScrollArea = Scroll, Scroll
        self.QFrame = types.SimpleNamespace(NoFrame=0)
        self.QComboBox, self.QTableWidget, self.QTableWidgetItem = Combo, Table, Item
        self.QTabWidget, self.QDialogButtonBox, self.QFileDialog = Tabs, Box, FileDialog
        self.QApplication, self.QMessageBox, self.Base = Application, Messages, Base
        self.QInputDialog = InputDialog
    def named(self, name): return next(item for item in reversed(self.objects) if item.objectName() == name and not item.deleted)
    def click(self, name): self.named(name).clicked.emit(False)


class PftDocumentPanelTests(unittest.TestCase):
    def setUp(self):
        self.old_dialog = views._pft_dock
        views._pft_dock = None
        self.addCleanup(lambda: setattr(views, "_pft_dock", self.old_dialog))
        self.old_language = i18n.get_language(); i18n.set_language("zh")
        self.addCleanup(i18n.set_language, self.old_language)
        self.widgets = PftWidgets()
        core = types.SimpleNamespace(Qt=types.SimpleNamespace(ItemIsEditable=1, PlainText=0, WA_DeleteOnClose=2,
            ArrowType=types.SimpleNamespace(DownArrow=41, RightArrow=42),
            ToolButtonStyle=types.SimpleNamespace(ToolButtonTextBesideIcon=51),
            TextElideMode=types.SimpleNamespace(ElideRight=1),
            ScrollBarPolicy=types.SimpleNamespace(ScrollBarAlwaysOff=1)))
        class Timer(self.widgets.Base):
            def __init__(self, parent):
                super().__init__(parent)
                self.timeout, self.active = PftSignal(), False
            def start(self, interval): self.interval, self.active = interval, True
            def stop(self): self.active = False
        core.QTimer = Timer
        self.core = core
        self.patch = patch.object(views, "_qt", return_value=(core, self.widgets)); self.patch.start(); self.addCleanup(self.patch.stop)
        icon_patch = patch.object(views, "_settings_icon", return_value="public-gear-icon")
        icon_patch.start(); self.addCleanup(icon_patch.stop)
        self.window = self.widgets.Base()
        self.mounts = []
        def mount(dock, window, content):
            self.mounts.append((dock, window, content)); dock.setWidget(content); return dock
        def present(dock):
            dock.show(); dock.raise_(); return dock
        host = types.SimpleNamespace(mount_panel_dock=mount, present_panel=present)
        host_patch = patch.object(views, "_panel_host", return_value=host)
        host_patch.start(); self.addCleanup(host_patch.stop)
        self.thumbnail = self.widgets.QWidget()
        self.thumbnail.set_document = Mock()
        self.thumbnail_factory = Mock(return_value=self.thumbnail)
        thumbnail_patch = patch.object(views, "_thumbnail_views", return_value=types.SimpleNamespace(
            create_thumbnail_widget=self.thumbnail_factory))
        thumbnail_patch.start(); self.addCleanup(thumbnail_patch.stop)
        self.doc = PftDoc(); self.body = PftObject(self.doc, "Body", "PartDesign::Body"); self.pad = PftObject(self.doc, "Pad", "PartDesign::Pad")
        self.body.Group, self.body.Tip = [self.pad], self.pad
        self.app, self.gui = PftApp(self.doc), PftGui(self.doc)
        self.gui.getMainWindow = lambda: self.window
        self.gui.activeDocument = lambda: types.SimpleNamespace(Document=self.app.ActiveDocument)
        self.temp = tempfile.TemporaryDirectory(prefix="pft-document-view-"); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
    def show(self): return views.show_document_tools_dialog(self.app, self.gui)
    def test_open_and_reuse_have_no_clipboard_or_file_actions(self):
        dialog = self.show()
        self.assertTrue(dialog.visible)
        self.assertIsInstance(dialog, self.widgets.QDockWidget)
        self.assertEqual(dialog.minimum_width, 300)
        self.assertEqual(len(self.mounts), 1)
        self.assertIs(dialog.content.parent(), dialog)
        self.assertIs(self.show(), dialog)
        views._pft_dock = None  # Simulate a module reload: QObject discovery still reuses it.
        self.assertIs(self.show(), dialog)
        self.assertEqual(self.widgets.clipboard_calls, 0)
        self.assertEqual(self.widgets.save_calls, 0)
        self.assertEqual(self.widgets.exec_calls, 0)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertIs(self.app.ActiveDocument, self.doc)
        self.assertEqual(self.gui.Selection.records, [])
    def test_property_settings_are_explicit_cancel_safe_and_preserve_target_scope(self):
        self.show()
        with patch.object(views.tools, "read_document_tools", wraps=views.tools.read_document_tools) as read, \
                patch.object(views, "_choose_property_names", return_value=(False, ("Cost",))) as choose:
            self.widgets.click("PhoenixTools_DocumentPropertySettings")
            choose.assert_called_once()
            read.assert_not_called()
        with patch.object(views.tools, "read_document_tools", wraps=views.tools.read_document_tools) as read, \
                patch.object(views, "_choose_property_names", return_value=(True, ("Cost", "Enabled"))):
            self.widgets.click("PhoenixTools_DocumentPropertySettings")
            self.assertEqual(read.call_args.kwargs["property_names"], ("Cost", "Enabled"))
            self.assertEqual(read.call_args.kwargs["target_names"], ["Body"])
        text = self.widgets.named("PhoenixTools_DocumentInformation").toPlainText()
        self.assertIn("Cost", text)
        self.assertIn("所选对象属性", text)
        self.assertNotIn("（BOM）", text)
        self.widgets.click("PhoenixTools_DocumentRefresh")
        self.assertIn("Cost", self.widgets.named("PhoenixTools_DocumentMarkdown").toPlainText())
        self.assertIs(self.app.ActiveDocument, self.doc)
        self.assertEqual(self.gui.Selection.records, [])
        self.assertEqual(self.widgets.save_calls, 0)
        self.assertEqual(self.widgets.clipboard_calls, 0)

    def test_property_settings_dialog_validates_before_accept_and_restores_default_scope(self):
        self.widgets.preview_result = 1
        def configure():
            editor = self.widgets.named("PhoenixTools_DocumentPropertyNames")
            box = self.widgets.named("PhoenixTools_DocumentPropertySettingsButtons")
            for invalid in ("Cost,Cost", "Display label", "Cost\tEnabled"):
                editor.setPlainText(invalid)
                box.accepted.emit()
                self.assertIn("至多20个唯一", self.widgets.named("PhoenixTools_DocumentPropertySettingsStatus").text)
            editor.setPlainText(" Cost，Enabled\nLength ")
            box.accepted.emit()
        self.widgets.on_preview = configure
        accepted, names = views._choose_property_names(self.core, self.widgets, self.window, None)
        self.assertTrue(accepted)
        self.assertEqual(names, ("Cost", "Enabled", "Length"))
        def defaults():
            self.widgets.click("PhoenixTools_DocumentPropertiesDefault")
            self.widgets.named("PhoenixTools_DocumentPropertySettingsButtons").accepted.emit()
        self.widgets.on_preview = defaults
        self.assertEqual(views._choose_property_names(self.core, self.widgets, self.window, ("Cost",)), (True, None))
        self.widgets.on_preview = lambda: self.widgets.named("PhoenixTools_DocumentPropertyNames").setPlainText("Arbitrary")
        self.assertEqual(views._choose_property_names(self.core, self.widgets, self.window, None), (False, None))

    def test_empty_explicit_property_scope_is_panel_only_and_resets_on_reopen(self):
        self.show()
        with patch.object(views, "_choose_property_names", return_value=(True, ())):
            self.widgets.click("PhoenixTools_DocumentPropertySettings")
        text = self.widgets.named("PhoenixTools_DocumentInformation").toPlainText()
        self.assertNotIn("PartNumber", text)
        self.assertIn("未选择属性字段。", text)
        self.widgets.named("PhoenixTools_DocumentToolsDock").close()
        self.show()
        self.assertIn("PartNumber", self.widgets.named("PhoenixTools_DocumentInformation").toPlainText())

    def test_long_document_and_object_labels_remain_inside_scrollable_panel(self):
        self.doc.Label = "FixtureDocument" * 80
        self.body.Label = "FixtureBody" * 80
        dialog = self.show()
        scroll = self.widgets.named("PhoenixTools_DocumentScroll")
        self.assertIs(dialog.content, scroll)
        self.assertTrue(scroll.widget_resizable)
        self.assertEqual(scroll.horizontal_scroll_policy, 1)
        combo = self.widgets.named("PhoenixTools_DocumentSelector")
        self.assertTrue(any(self.doc.Label in label for label, name in combo.rows))
        self.assertIsNone(combo.currentData())
        self.assertEqual(combo.minimum_contents_length, 1)
        self.assertEqual(combo.size_adjust_policy, 3)
        for suffix in ("Selector", "Information", "Dimensions", "Markdown", "Targets", "Tabs", "Scroll"):
            widget = self.widgets.named("PhoenixTools_Document" + suffix)
            self.assertEqual(widget.sizePolicy().horizontalPolicy(), self.widgets.QSizePolicy.Ignored)
        tabs = self.widgets.named("PhoenixTools_DocumentTabs")
        self.assertTrue(tabs.scroll_buttons)
        table = self.widgets.named("PhoenixTools_DocumentTargets")
        self.assertEqual(table.cells[(0, 2)].text, self.body.Label)
        self.assertEqual(table.cells[(0, 2)].tooltip, self.body.Label)
        self.assertLessEqual(table.widths[2], 200)
        self.assertEqual(table.size_adjust_policy, self.widgets.QAbstractScrollArea.AdjustIgnored)
        self.assertEqual(self.widgets.clipboard_calls + self.widgets.save_calls, 0)
        self.assertIs(self.app.ActiveDocument, self.doc)
        self.assertEqual(self.gui.Selection.records, [])

    def test_thumbnail_tab_receives_scope_without_activating_the_pinned_document(self):
        self.show()
        self.thumbnail_factory.assert_called_once()
        factory_args = self.thumbnail_factory.call_args.args
        self.assertIs(factory_args[0], self.app); self.assertIs(factory_args[1], self.gui)
        self.assertIsNone(factory_args[3]())
        self.thumbnail.set_document.assert_called_with(self.doc.Name, active=True)
        self.assertIn((self.thumbnail, "缩略图"), factory_args[2].tabs)
        second = PftDoc("Second"); self.app.documents[second.Name] = second
        self.widgets.click("PhoenixTools_DocumentRefresh")
        combo = self.widgets.named("PhoenixTools_DocumentSelector")
        combo.setCurrentIndex(next(index for index, row in enumerate(combo.rows) if row[1] == second.Name))
        self.thumbnail.set_document.assert_called_with(second.Name, active=False)
        self.assertIs(self.app.ActiveDocument, self.doc)
        self.app.documents.pop(second.Name)
        self.widgets.click("PhoenixTools_DocumentInformationAction")
        self.thumbnail.set_document.assert_called_with(None, active=False)

    def test_default_follow_updates_only_after_native_active_document_changes(self):
        second = PftDoc("Second"); PftObject(second, "SecondBody", "PartDesign::Body")
        self.app.documents[second.Name] = second
        dock = self.show()
        combo = self.widgets.named("PhoenixTools_DocumentSelector")
        timer = self.widgets.named("PhoenixTools_DocumentFollowTimer")
        self.assertIsNone(combo.currentData())
        self.assertEqual(combo.rows[0], ("（跟随活动文档）", None))
        self.assertIs(timer.parent(), dock)
        self.assertEqual(timer.interval, 400)
        reads = self.body.reads + self.pad.reads
        with patch.object(views.tools, "read_document_tools", wraps=views.tools.read_document_tools) as read:
            for _ in range(5): timer.timeout.emit()
            read.assert_not_called()
            self.assertEqual(self.body.reads + self.pad.reads, reads)
            self.app.ActiveDocument = second
            timer.timeout.emit()
            read.assert_called_once()
        self.assertIsNone(combo.currentData())
        self.assertIn("Second", self.widgets.named("PhoenixTools_DocumentInformation").toPlainText())
        self.thumbnail.set_document.assert_called_with("Second", active=True)
        self.assertIs(self.app.ActiveDocument, second)
        self.assertEqual(self.gui.Selection.records, [])
        self.assertEqual((self.widgets.clipboard_calls, self.widgets.save_calls), (0, 0))

    def test_manual_pin_survives_activation_and_open_set_changes_then_none_resumes_follow(self):
        second = PftDoc("Second"); PftObject(second, "SecondBody", "PartDesign::Body")
        self.app.documents[second.Name] = second
        self.show()
        combo = self.widgets.named("PhoenixTools_DocumentSelector")
        combo.setCurrentIndex(next(i for i, row in enumerate(combo.rows) if row[1] == self.doc.Name))
        self.widgets.named("PhoenixDocumentTarget_Body").setChecked(False)
        self.widgets.named("PhoenixDocumentTarget_Pad").setChecked(True)
        timer = self.widgets.named("PhoenixTools_DocumentFollowTimer")
        self.app.ActiveDocument = second
        editor = self.widgets.named("PhoenixTools_DocumentInformation")
        with patch.object(views.tools, "read_document_tools", wraps=views.tools.read_document_tools) as read, \
             patch.object(editor, "setPlainText", side_effect=AssertionError("pinned editor must keep its scroll position")):
            timer.timeout.emit()
            self.app.documents["Third"] = PftDoc("Third")
            timer.timeout.emit()
            read.assert_not_called()
        self.assertEqual(combo.currentData(), self.doc.Name)
        self.assertFalse(self.widgets.named("PhoenixDocumentTarget_Body").isChecked())
        self.assertTrue(self.widgets.named("PhoenixDocumentTarget_Pad").isChecked())
        self.thumbnail.set_document.assert_called_with(self.doc.Name, active=False)
        combo.setCurrentIndex(0)
        self.assertIn("Second", self.widgets.named("PhoenixTools_DocumentInformation").toPlainText())
        self.thumbnail.set_document.assert_called_with("Second", active=True)
        self.assertIs(self.app.ActiveDocument, second)

    def test_closed_pin_falls_back_to_follow_and_last_document_closure_clears_panel(self):
        second = PftDoc("Second"); PftObject(second, "SecondBody", "PartDesign::Body")
        self.app.documents[second.Name] = second
        self.show()
        combo = self.widgets.named("PhoenixTools_DocumentSelector")
        combo.setCurrentIndex(next(i for i, row in enumerate(combo.rows) if row[1] == "Second"))
        timer = self.widgets.named("PhoenixTools_DocumentFollowTimer")
        del self.app.documents["Second"]
        timer.timeout.emit()
        self.assertIsNone(combo.currentData())
        self.assertIn(self.doc.Name, self.widgets.named("PhoenixTools_DocumentInformation").toPlainText())
        self.app.documents.clear(); self.app.ActiveDocument = None
        timer.timeout.emit()
        self.thumbnail.set_document.assert_called_with(None, active=False)
        self.assertFalse(self.widgets.named("PhoenixTools_DocumentInformationAction").enabled)
        self.assertEqual(self.widgets.named("PhoenixTools_DocumentTargets").rows, 0)

    def test_follow_timer_is_owned_stopped_on_close_and_not_duplicated_on_reuse(self):
        dock = self.show()
        timer = self.widgets.named("PhoenixTools_DocumentFollowTimer")
        self.assertTrue(timer.active)
        self.assertIs(self.show(), dock)
        self.assertEqual(sum(item.objectName() == "PhoenixTools_DocumentFollowTimer" for item in self.widgets.objects), 1)
        dock.close()
        self.assertFalse(timer.active)
        new_dock = self.show()
        self.assertIsNot(new_dock, dock)
        self.assertIsNot(self.widgets.named("PhoenixTools_DocumentFollowTimer"), timer)

    def test_unchanged_follow_tick_reconciles_thumbnail_scope_without_reading_objects(self):
        self.show()
        self.thumbnail.set_document(self.doc.Name, active=False)
        self.thumbnail.set_document.reset_mock()
        with patch.object(views.tools, "read_document_tools", side_effect=AssertionError("no geometry reread")):
            self.widgets.named("PhoenixTools_DocumentFollowTimer").timeout.emit()
        self.thumbnail.set_document.assert_called_once_with(self.doc.Name, active=True)
        self.assertEqual((self.widgets.exec_calls, self.widgets.save_calls), (0, 0))

    def test_same_named_reopened_document_replaces_old_instance_snapshot(self):
        self.show()
        replacement = PftDoc(self.doc.Name)
        PftObject(replacement, "ReplacementBody", "PartDesign::Body")
        self.app.documents[self.doc.Name] = replacement
        self.app.ActiveDocument = replacement
        with patch.object(views.tools, "read_document_tools", wraps=views.tools.read_document_tools) as read:
            self.widgets.named("PhoenixTools_DocumentFollowTimer").timeout.emit()
            read.assert_called_once()
        self.assertIn("ReplacementBody", self.widgets.named("PhoenixTools_DocumentDimensions").toPlainText())
        self.thumbnail.set_document.assert_called_with(replacement.Name, active=True)
        self.assertEqual((self.widgets.exec_calls, self.widgets.save_calls), (0, 0))
        self.assertEqual(self.gui.Selection.records, [])

    def test_explicit_activate_preserves_pin_checks_and_does_not_read_objects(self):
        second = PftDoc("Second")
        PftObject(second, "SecondBody", "PartDesign::Body")
        self.app.documents[second.Name] = second
        self.show()
        combo = self.widgets.named("PhoenixTools_DocumentSelector")
        combo.setCurrentIndex(next(i for i, row in enumerate(combo.rows) if row[1] == second.Name))
        check = self.widgets.named("PhoenixDocumentTarget_SecondBody")
        check.setChecked(False)
        self.app.setActiveDocument = Mock(side_effect=lambda name: setattr(self.app, "ActiveDocument", self.app.getDocument(name)))
        activate = self.thumbnail_factory.call_args.kwargs["on_activate"]
        with patch.object(views.tools, "read_document_tools", side_effect=AssertionError("activation must not read geometry")):
            self.assertTrue(activate(second.Name))
            self.widgets.named("PhoenixTools_DocumentFollowTimer").timeout.emit()
        self.app.setActiveDocument.assert_called_once_with(second.Name)
        self.assertEqual(combo.currentData(), second.Name)
        self.assertFalse(check.isChecked())
        self.thumbnail.set_document.assert_called_with(second.Name, active=True)
        self.assertEqual(self.gui.Selection.records, [])
        self.assertEqual((self.widgets.exec_calls, self.widgets.save_calls), (0, 0))

    def test_activate_requires_both_app_and_gui_document_confirmation(self):
        second = PftDoc("Second")
        self.app.documents[second.Name] = second
        self.show()
        self.app.setActiveDocument = Mock(side_effect=lambda name: setattr(self.app, "ActiveDocument", second))
        self.gui.activeDocument = lambda: types.SimpleNamespace(Document=self.doc)
        with self.assertRaises(views.tools.PftDocumentToolsError):
            self.thumbnail_factory.call_args.kwargs["on_activate"](second.Name)
        self.assertEqual(self.gui.Selection.records, [])
        self.assertEqual((self.widgets.exec_calls, self.widgets.save_calls), (0, 0))

    def test_markdown_default_path_tracks_saved_unsaved_and_pinned_document(self):
        self.doc.FileName = "/fixture/project/Assembly.FCStd"
        self.show()
        self.widgets.click("PhoenixTools_DocumentExportAction")
        self.assertEqual(self.widgets.save_arguments[2], "/fixture/project/Assembly.md")
        self.doc.FileName, self.doc.Label = "", "../Unsaved:Fixture/Name*"
        self.widgets.click("PhoenixTools_DocumentExportAction")
        unsaved = self.widgets.save_arguments[2]
        self.assertFalse(Path(unsaved).is_absolute())
        self.assertTrue(unsaved.endswith(".md"))
        self.assertFalse(any(character in unsaved for character in '/\\:*?'))
        second = PftDoc("Second")
        second.FileName = "/fixture/other/Other.FCStd"
        self.app.documents[second.Name] = second
        self.widgets.click("PhoenixTools_DocumentRefresh")
        combo = self.widgets.named("PhoenixTools_DocumentSelector")
        combo.setCurrentIndex(next(i for i, row in enumerate(combo.rows) if row[1] == second.Name))
        self.widgets.click("PhoenixTools_DocumentExportAction")
        self.assertEqual(self.widgets.save_arguments[2], "/fixture/other/Other.md")
        self.assertEqual(self.widgets.exec_calls, 0)
        self.assertIs(self.app.ActiveDocument, self.doc)

    def test_png_default_path_uses_explicit_target_even_when_another_document_is_active(self):
        second = PftDoc("Second")
        second.FileName = "/fixture/other/Other.FCStd"
        self.app.documents[second.Name] = second
        self.show()
        combo = self.widgets.named("PhoenixTools_DocumentSelector")
        combo.setCurrentIndex(next(i for i, row in enumerate(combo.rows) if row[1] == second.Name))
        suggest = self.thumbnail_factory.call_args.kwargs["get_export_filename"]
        reads = self.body.reads + self.pad.reads
        self.assertEqual(suggest(second.Name), "/fixture/other/Other.png")
        self.assertEqual(self.body.reads + self.pad.reads, reads)
        second.FileName, second.Label = "", "Unsaved/Second"
        self.assertEqual(suggest(second.Name), "Unsaved_Second.png")
        self.assertIs(self.app.ActiveDocument, self.doc)
        with self.assertRaises(views.tools.PftDocumentToolsError):
            suggest(self.doc.Name)
        self.assertEqual((self.widgets.exec_calls, self.widgets.save_calls), (0, 0))

    def test_document_panel_omits_duplicate_view_and_appearance_quick_actions(self):
        self.show()
        removed = {"PhoenixTools_DocumentAxonometricFitAction", "PhoenixTools_DocumentRandomColorAction",
                   "PhoenixTools_DocumentTransparencyAction", "PhoenixTools_DocumentVisualStatus"}
        self.assertFalse(removed.intersection(item.objectName() for item in self.widgets.objects))
        self.assertEqual((self.widgets.exec_calls, self.widgets.clipboard_calls, self.widgets.save_calls), (0, 0, 0))

    def test_initial_recommended_scope_is_collapsed_with_count_and_bounded_table(self):
        self.show()
        toggle = self.widgets.named("PhoenixTools_DocumentTargetToggle")
        section = self.widgets.named("PhoenixTools_DocumentTargetSection")
        table = self.widgets.named("PhoenixTools_DocumentTargets")
        self.assertTrue(toggle.checkable)
        self.assertFalse(toggle.isChecked())
        self.assertFalse(section.visible)
        self.assertEqual(toggle.text, "对象范围（已选 1 / 2）")
        self.assertEqual(toggle.arrow, 42)
        self.assertEqual(toggle.style, 51)
        self.assertIs(table.parent(), section)
        self.assertEqual((table.minimum_height, table.maximum_height), (96, 240))

    def test_pure_fold_only_updates_visibility_and_keeps_checks_and_snapshot_text(self):
        self.show()
        body = self.widgets.named("PhoenixDocumentTarget_Body")
        pad = self.widgets.named("PhoenixDocumentTarget_Pad")
        body.setChecked(False); pad.setChecked(True)
        before = {key: self.widgets.named("PhoenixTools_Document" + key).toPlainText()
                  for key in ("Information", "Dimensions", "Markdown")}
        reads = self.body.reads + self.pad.reads
        with patch.object(views.tools, "read_document_tools") as read, \
             patch.object(views.tools, "prepare_markdown_export") as prepare, \
             patch.object(views.tools, "write_markdown_export") as write:
            self.widgets.click("PhoenixTools_DocumentTargetToggle")
            self.assertTrue(self.widgets.named("PhoenixTools_DocumentTargetSection").visible)
            self.assertEqual(self.widgets.named("PhoenixTools_DocumentTargetToggle").arrow, 41)
            self.widgets.click("PhoenixTools_DocumentTargetToggle")
            read.assert_not_called(); prepare.assert_not_called(); write.assert_not_called()
        self.assertFalse(body.isChecked()); self.assertTrue(pad.isChecked())
        self.assertEqual(self.widgets.named("PhoenixTools_DocumentTargetToggle").text, "对象范围（已选 1 / 2）")
        self.assertEqual(reads, self.body.reads + self.pad.reads)
        self.assertEqual(before, {key: self.widgets.named("PhoenixTools_Document" + key).toPlainText()
                                  for key in before})
        self.assertEqual((self.widgets.clipboard_calls, self.widgets.save_calls, self.widgets.exec_calls), (0, 0, 0))
        self.assertEqual(self.gui.Selection.records, [])
        self.assertIs(self.app.ActiveDocument, self.doc)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_zero_initial_targets_expand_but_later_deselection_does_not_flip_user_choice(self):
        self.gui.Selection.records = [types.SimpleNamespace(Object=self.pad, ObjectName="Pad", DocumentName=self.doc.Name, SubElementNames=["Face1"])]
        self.show()
        toggle = self.widgets.named("PhoenixTools_DocumentTargetToggle")
        self.assertTrue(toggle.isChecked())
        self.assertEqual(toggle.text, "对象范围（已选 0 / 2）")
        self.widgets.click("PhoenixTools_DocumentTargetToggle")
        self.widgets.named("PhoenixDocumentTarget_Pad").setChecked(True)
        self.widgets.named("PhoenixDocumentTarget_Pad").setChecked(False)
        self.assertFalse(toggle.isChecked())
        self.assertFalse(self.widgets.named("PhoenixTools_DocumentTargetSection").visible)
        self.assertEqual(toggle.text, "对象范围（已选 0 / 2）")

    def test_refresh_and_document_switch_keep_user_expansion_and_update_summary(self):
        second = PftDoc("Second"); PftObject(second, "SecondBody", "PartDesign::Body")
        self.app.documents[second.Name] = second
        self.show()
        toggle = self.widgets.named("PhoenixTools_DocumentTargetToggle")
        self.widgets.click("PhoenixTools_DocumentTargetToggle")
        PftObject(self.doc, "Extra")
        self.widgets.click("PhoenixTools_DocumentRefresh")
        self.assertTrue(toggle.isChecked())
        self.assertTrue(self.widgets.named("PhoenixTools_DocumentTargetSection").visible)
        self.assertEqual(toggle.text, "对象范围（已选 2 / 3）")
        combo = self.widgets.named("PhoenixTools_DocumentSelector")
        combo.setCurrentIndex(next(i for i, row in enumerate(combo.rows) if row[1] == "Second"))
        self.assertTrue(toggle.isChecked())
        self.assertEqual(toggle.text, "对象范围（已选 1 / 1）")
        self.widgets.click("PhoenixTools_DocumentTargetToggle")
        self.widgets.click("PhoenixTools_DocumentRefresh")
        self.assertFalse(toggle.isChecked())
        self.assertEqual(toggle.text, "对象范围（已选 1 / 1）")
        self.assertIs(self.app.ActiveDocument, self.doc)

    def test_empty_state_initially_expands_and_english_summary_translates_before_format(self):
        self.app.ActiveDocument = None; self.app.documents = {}
        i18n.set_language("en")
        self.show()
        toggle = self.widgets.named("PhoenixTools_DocumentTargetToggle")
        self.assertTrue(toggle.isChecked())
        self.assertTrue(self.widgets.named("PhoenixTools_DocumentTargetSection").visible)
        self.assertEqual(toggle.text, "Object range (selected 0 / 0)")
        self.widgets.click("PhoenixTools_DocumentTargetToggle")
        self.widgets.click("PhoenixTools_DocumentRefresh")
        self.assertFalse(toggle.isChecked())
        self.assertEqual(toggle.text, "Object range (selected 0 / 0)")

    def test_arrow_uses_legacy_unscoped_qt_enums_when_available(self):
        self.core.Qt.DownArrow, self.core.Qt.RightArrow = 61, 62
        self.show()
        toggle = self.widgets.named("PhoenixTools_DocumentTargetToggle")
        self.assertEqual(toggle.arrow, 62)
        self.widgets.click("PhoenixTools_DocumentTargetToggle")
        self.assertEqual(toggle.arrow, 61)

    def test_checkbox_scope_changes_preview_without_tree_or_document_changes(self):
        self.show()
        self.widgets.named("PhoenixDocumentTarget_Body").setChecked(False)
        self.widgets.named("PhoenixDocumentTarget_Pad").setChecked(True)
        text = self.widgets.named("PhoenixTools_DocumentDimensions").toPlainText()
        self.assertIn("Pad | Pad", text)
        self.assertNotIn("Body | Body", text)
        self.assertEqual(self.gui.Selection.records, [])
        self.assertIs(self.app.ActiveDocument, self.doc)
    def test_switching_document_reads_it_without_changing_active_document(self):
        second = PftDoc("Second"); PftObject(second, "SecondBody", "PartDesign::Body")
        self.app.documents[second.Name] = second
        self.show()
        combo = self.widgets.named("PhoenixTools_DocumentSelector")
        index = next(index for index, row in enumerate(combo.rows) if row[1] == "Second")
        combo.setCurrentIndex(index)
        self.assertIn("Second", self.widgets.named("PhoenixTools_DocumentInformation").toPlainText())
        self.assertIs(self.app.ActiveDocument, self.doc)








    def test_copy_is_explicit_and_markdown_copy_omits_full_saved_path(self):
        self.show()
        self.doc.Label = "Latest unsaved in-memory label"
        self.widgets.click("PhoenixTools_DocumentMarkdownAction")
        self.assertEqual(self.widgets.clipboard_calls, 1)
        self.assertIn("Latest unsaved in", self.widgets.clipboard_values[-1])
        self.assertNotIn(self.doc.FileName, self.widgets.clipboard_values[-1])
        self.widgets.click("PhoenixTools_DocumentInformationAction")
        self.assertIn(self.doc.FileName, self.widgets.clipboard_values[-1])
    def test_file_dialog_cancel_and_preview_cancel_do_not_write(self):
        self.show()
        self.widgets.click("PhoenixTools_DocumentExportAction")
        self.assertEqual(self.widgets.exec_calls, 0)
        target = self.folder / "notes.md"; self.widgets.save_choice = str(target)
        self.widgets.preview_result = 0
        self.widgets.click("PhoenixTools_DocumentExportAction")
        self.assertEqual(self.widgets.exec_calls, 1)
        self.assertFalse(target.exists())
        boxes = [item for item in self.widgets.objects if isinstance(item, self.widgets.QDialogButtonBox)]
        self.assertTrue(boxes[-1].button(2).default)
        self.assertFalse(boxes[-1].button(1).default)
    def test_confirmed_export_writes_markdown_without_saving_cad_and_requires_explicit_overwrite(self):
        self.show()
        target = self.folder / "notes.md"; target.write_text("original")
        self.widgets.save_choice, self.widgets.preview_result = str(target), 0
        self.widgets.click("PhoenixTools_DocumentExportAction")
        self.assertEqual(target.read_text(), "original")
        self.widgets.preview_result = 1
        self.widgets.click("PhoenixTools_DocumentExportAction")
        self.assertIn("# ", target.read_text())
        self.assertNotIn(self.doc.FileName, target.read_text())
        self.assertEqual(self.widgets.messages[-1][0], "information")
        self.assertEqual(self.widgets.save_options, {"options": 4})
        self.assertEqual(list(self.folder.glob(".pft-markdown-*")), [])
    def test_changed_file_during_confirmation_is_retained_and_reports_safe_error(self):
        self.show()
        target = self.folder / "notes.md"; target.write_text("old")
        self.widgets.save_choice, self.widgets.preview_result = str(target), 1
        self.widgets.on_preview = lambda: target.write_text("other user change")
        self.widgets.click("PhoenixTools_DocumentExportAction")
        self.assertEqual(target.read_text(), "other user change")
        self.assertEqual(self.widgets.messages[-1][0], "warning")
    def test_no_document_and_closed_document_disable_writes_and_copies(self):
        self.app.ActiveDocument = None; self.app.documents = {}
        self.show()
        for key in ("Information", "Dimensions", "Markdown", "Export"):
            self.assertFalse(self.widgets.named("PhoenixTools_Document" + key + "Action").enabled)
        self.widgets.click("PhoenixTools_DocumentMarkdownAction")
        self.assertEqual(self.widgets.clipboard_calls, 0)
        self.assertEqual(self.widgets.save_calls, 0)


if __name__ == "__main__": unittest.main()
