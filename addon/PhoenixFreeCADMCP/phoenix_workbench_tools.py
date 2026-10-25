# SPDX-License-Identifier: Apache-2.0
"""Compatibility facade: preserve workbench entry points while delegating model, controller and view duties."""

from phoenix_workbench_model import (
    BOM_FIELDS, BOM_GROUP, ToolError, compose_bom_label, _property_snapshot,
    _expression_entries, _ensure_writable, _snapshot, build_rename_preview,
    recommend_fields_from_label, recommend_property_values, build_link_label_preview,
)
from phoenix_workbench_controller import (
    _ensure_idle, _selected_context, _ensure_snapshot, _run_transaction,
    apply_rename, apply_properties,
    build_property_preview, apply_link_labels,
)


def show_rename_dialog():
    import FreeCAD as app
    import FreeCADGui as gui
    from phoenix_workbench_views import show_rename_dialog as show
    return show(app, gui)


def show_properties_dialog():
    import FreeCAD as app
    import FreeCADGui as gui
    from phoenix_workbench_views import show_properties_dialog as show
    return show(app, gui)


def show_link_labels_dialog():
    import FreeCAD as app
    import FreeCADGui as gui
    from phoenix_workbench_views import show_link_labels_dialog as show
    return show(app, gui)


def _qt():
    from phoenix_workbench_views import _qt as get_qt
    return get_qt()


def _dialog_exec(dialog):
    from phoenix_workbench_views import _dialog_exec as execute
    return execute(dialog)


def _table_item(widgets, core, text, editable=False):
    from phoenix_workbench_views import _table_item as make_item
    return make_item(widgets, core, text, editable)


def _report(gui, message, error=False):
    from phoenix_workbench_views import _report as report
    return report(gui, message, error)
