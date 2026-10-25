# SPDX-License-Identifier: Apache-2.0
"""Shared native dock placement without changing FreeCAD's Tasks panel."""

_PFT_TASK_DOCK_NAMES = frozenset(("Tasks", "TaskPanel"))


def _qt():
    from PySide import QtCore
    try:
        from PySide import QtWidgets
    except ImportError:
        from PySide import QtGui as QtWidgets
    return QtCore, QtWidgets


def _enum(owner, group, name):
    value = getattr(owner, name, None)
    if value is not None:
        return value
    return getattr(getattr(owner, group), name)


def _task_dock(window, widgets, panel):
    """Only a unique known native Tasks dock can be a tabification target."""
    try:
        children = window.findChildren(widgets.QDockWidget)
    except (AttributeError, RuntimeError, ReferenceError):
        return None
    candidates = []
    for child in children:
        if child is panel or any(child is existing for existing in candidates):
            continue
        try:
            if child.objectName() in _PFT_TASK_DOCK_NAMES:
                candidates.append(child)
        except (RuntimeError, ReferenceError):
            continue
    return candidates[0] if len(candidates) == 1 else None


def mount_panel_dock(dock, window, content):
    """Mount one movable, closable, floatable panel on the right.

    Existing Tasks content and placement remain owned by FreeCAD. If its sole
    known Tasks dock is already docked on the right, share that tab group.
    Call this only for a new panel; use present_panel when reopening one.
    """
    core, widgets = _qt()
    flag = lambda name: _enum(widgets.QDockWidget, "DockWidgetFeature", name)
    area = lambda name: _enum(core.Qt, "DockWidgetArea", name)
    right = area("RightDockWidgetArea")
    dock.setFeatures(flag("DockWidgetClosable") | flag("DockWidgetMovable") | flag("DockWidgetFloatable"))
    dock.setAllowedAreas(area("LeftDockWidgetArea") | right)
    dock.setWidget(content)
    window.addDockWidget(right, dock)
    tasks = _task_dock(window, widgets, dock)
    if tasks is not None:
        try:
            if not tasks.isFloating() and window.dockWidgetArea(tasks) == right:
                window.tabifyDockWidget(tasks, dock)
        except (AttributeError, RuntimeError, ReferenceError):
            # A native dock may disappear during a workbench/layout change.
            pass
    return dock


def present_panel(dock):
    """Show the panel without activating the main window for a docked panel."""
    dock.show()
    dock.raise_()
    if dock.isFloating():
        dock.activateWindow()
    return dock
