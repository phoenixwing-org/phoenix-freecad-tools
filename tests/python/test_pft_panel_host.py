# SPDX-License-Identifier: Apache-2.0
"""Native panel placement and focus rules using Qt-free fakes."""

import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
spec = importlib.util.spec_from_file_location("pft_panel_host_tests", ADDON / "pft_panel_host.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class FakeDock:
    DockWidgetClosable, DockWidgetMovable, DockWidgetFloatable = 1, 2, 4

    def __init__(self, name="PhoenixTools_TestDock", floating=False, deleted=False):
        self.name, self.floating, self.deleted = name, floating, deleted
        self.features = self.areas = self.content = None
        self.events = []

    def objectName(self):
        if self.deleted:
            raise RuntimeError("deleted native dock")
        return self.name

    def isFloating(self):
        if self.deleted:
            raise RuntimeError("deleted native dock")
        return self.floating

    def setFeatures(self, value): self.features = value
    def setAllowedAreas(self, value): self.areas = value
    def setWidget(self, content): self.content = content
    def show(self): self.events.append("show")
    def raise_(self): self.events.append("raise")
    def activateWindow(self): self.events.append("activate")


class FakeWindow:
    def __init__(self, children=(), areas=None):
        self.children, self.areas = list(children), dict(areas or {})
        self.added, self.tabified = [], []

    def findChildren(self, kind):
        return [child for child in self.children if isinstance(child, kind)]

    def addDockWidget(self, area, dock):
        self.added.append((area, dock))
        self.areas[dock] = area
        self.children.append(dock)

    def dockWidgetArea(self, dock): return self.areas.get(dock, 0)
    def tabifyDockWidget(self, first, second): self.tabified.append((first, second))


class PanelHostTests(unittest.TestCase):
    def setUp(self):
        self.core = types.SimpleNamespace(Qt=types.SimpleNamespace(LeftDockWidgetArea=8, RightDockWidgetArea=16))
        self.widgets = types.SimpleNamespace(QDockWidget=FakeDock)
        self.qt = patch.object(host, "_qt", return_value=(self.core, self.widgets))
        self.qt.start()
        self.addCleanup(self.qt.stop)

    def test_mount_sets_native_features_content_and_right_area_without_presenting(self):
        dock, content, window = FakeDock(), object(), FakeWindow()
        self.assertIs(host.mount_panel_dock(dock, window, content), dock)
        self.assertEqual(dock.features, 7)
        self.assertEqual(dock.areas, 24)
        self.assertIs(dock.content, content)
        self.assertEqual(window.added, [(16, dock)])
        self.assertEqual(window.tabified, [])
        self.assertEqual(dock.events, [])

    def test_only_unique_known_right_docked_tasks_can_be_tabified(self):
        for name in ("Tasks", "TaskPanel"):
            with self.subTest(name=name):
                tasks, dock = FakeDock(name), FakeDock()
                original_content = object()
                tasks.content = original_content
                window = FakeWindow([tasks], {tasks: 16})
                host.mount_panel_dock(dock, window, object())
                self.assertEqual(window.tabified, [(tasks, dock)])
                self.assertIs(tasks.content, original_content)
                self.assertIsNone(tasks.features)
                self.assertIsNone(tasks.areas)
                self.assertEqual(tasks.events, [])

    def test_native_tasks_on_left_or_floating_are_not_moved_or_changed(self):
        for area, floating in ((8, False), (16, True), (0, False)):
            with self.subTest(area=area, floating=floating):
                tasks, dock = FakeDock("Tasks", floating), FakeDock()
                window = FakeWindow([tasks], {tasks: area})
                host.mount_panel_dock(dock, window, object())
                self.assertEqual(window.tabified, [])
                self.assertEqual(window.areas[tasks], area)
                self.assertEqual(tasks.floating, floating)
                self.assertEqual(tasks.events, [])

    def test_titles_and_unknown_object_names_never_identify_tasks(self):
        native, dock = FakeDock("Combo View"), FakeDock()
        native.windowTitle = lambda: "Tasks"
        window = FakeWindow([native], {native: 16})
        host.mount_panel_dock(dock, window, object())
        self.assertEqual(window.tabified, [])

    def test_ambiguous_known_task_docks_are_not_tabified_even_if_one_is_right(self):
        for names in (("Tasks", "Tasks"), ("Tasks", "TaskPanel")):
            with self.subTest(names=names):
                first, second, dock = FakeDock(names[0]), FakeDock(names[1]), FakeDock()
                window = FakeWindow([first, second], {first: 16, second: 8})
                host.mount_panel_dock(dock, window, object())
                self.assertEqual(window.tabified, [])

    def test_deleted_native_dock_is_ignored(self):
        tasks, dock = FakeDock("Tasks", deleted=True), FakeDock()
        window = FakeWindow([tasks], {tasks: 16})
        self.assertIs(host.mount_panel_dock(dock, window, object()), dock)
        self.assertEqual(window.tabified, [])

    def test_optional_native_dock_lookup_or_tabification_failure_keeps_right_panel(self):
        for operation in ("findChildren", "tabifyDockWidget"):
            with self.subTest(operation=operation):
                tasks, dock = FakeDock("Tasks"), FakeDock()
                window = FakeWindow([tasks], {tasks: 16})
                def deleted(*_args): raise RuntimeError("native dock disappeared")
                setattr(window, operation, deleted)
                self.assertIs(host.mount_panel_dock(dock, window, object()), dock)
                self.assertEqual(window.added, [(16, dock)])

    def test_qt6_scoped_enums_work_without_qt5_flat_aliases(self):
        class ScopedDock:
            DockWidgetFeature = types.SimpleNamespace(DockWidgetClosable=1, DockWidgetMovable=2, DockWidgetFloatable=4)
        core = types.SimpleNamespace(Qt=types.SimpleNamespace(DockWidgetArea=types.SimpleNamespace(LeftDockWidgetArea=8, RightDockWidgetArea=16)))
        dock, window = FakeDock(), FakeWindow()
        with patch.object(host, "_qt", return_value=(core, types.SimpleNamespace(QDockWidget=ScopedDock))):
            host.mount_panel_dock(dock, window, object())
        self.assertEqual((dock.features, dock.areas), (7, 24))
        self.assertEqual(window.added, [(16, dock)])

    def test_present_docked_panel_does_not_activate_main_window(self):
        dock = FakeDock(floating=False)
        self.assertIs(host.present_panel(dock), dock)
        self.assertEqual(dock.events, ["show", "raise"])

    def test_present_floating_panel_can_activate_its_own_window(self):
        dock = FakeDock(floating=True)
        self.assertIs(host.present_panel(dock), dock)
        self.assertEqual(dock.events, ["show", "raise", "activate"])


if __name__ == "__main__":
    unittest.main()
