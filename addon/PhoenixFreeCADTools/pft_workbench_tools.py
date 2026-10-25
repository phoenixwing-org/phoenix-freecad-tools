# SPDX-License-Identifier: Apache-2.0
"""Compatibility facade: preserve workbench entry points while delegating model, controller and view duties."""

from pft_workbench_model import (
    BOM_FIELDS, BOM_GROUP, PftToolError, compose_bom_label, _property_snapshot,
    _expression_entries, _ensure_writable, _snapshot, build_rename_preview,
    recommend_fields_from_label, recommend_property_values, build_link_label_preview,
)
from pft_workbench_controller import (
    _ensure_idle, _selected_context, _ensure_snapshot, _run_transaction,
    apply_rename, apply_properties,
    build_property_preview, apply_link_labels,
)


def show_rename_dialog():
    import FreeCAD as app
    import FreeCADGui as gui
    from pft_workbench_views import show_rename_dialog as show
    return show(app, gui)


def show_properties_dialog():
    import FreeCAD as app
    import FreeCADGui as gui
    from pft_workbench_views import show_properties_dialog as show
    return show(app, gui)


def show_link_labels_dialog():
    import FreeCAD as app
    import FreeCADGui as gui
    from pft_workbench_views import show_link_labels_dialog as show
    return show(app, gui)


def _qt():
    from pft_workbench_views import _qt as get_qt
    return get_qt()


def _dialog_exec(dialog):
    from pft_workbench_views import _dialog_exec as execute
    return execute(dialog)


def _table_item(widgets, core, text, editable=False):
    from pft_workbench_views import _table_item as make_item
    return make_item(widgets, core, text, editable)


def _report(gui, message, error=False):
    from pft_workbench_views import _report as report
    return report(gui, message, error)


def show_settings_dialog(on_applied=None):
    import FreeCAD as app
    import FreeCADGui as gui
    import pft_bridge as bridge
    from pft_workbench_views import show_settings_dialog as show
    return show(app, gui, on_applied, bridge_snapshot=dict(bridge.get_status()))


def show_mcp_status_dialog(snapshot):
    import FreeCADGui as gui
    from pft_workbench_views import show_mcp_status_dialog as show
    return show(gui, dict(snapshot))


def confirm_mcp_stop():
    import FreeCADGui as gui
    from pft_workbench_views import confirm_mcp_stop as confirm
    return confirm(gui)


def show_desk_dialog():
    import FreeCAD as app
    import FreeCADGui as gui
    from pft_desk_views import show_desk_dialog as show
    return show(app, gui)


def show_chat_connection_dialog():
    import FreeCAD as app
    import FreeCADGui as gui
    import pft_bridge as bridge
    from pft_chat_views import show_chat_connection_dialog as show
    return show(app, gui, bridge.get_status)


def show_document_tools_dialog():
    import FreeCAD as app
    import FreeCADGui as gui
    from pft_document_tools_views import show_document_tools_dialog as show
    return show(app, gui)


def run_axonometric_fit(app=None, gui=None):
    """Change only the active 3D camera, using native APIs even during editing."""
    if app is None:
        import FreeCAD as app
    if gui is None:
        import FreeCADGui as gui
    from pft_workbench_controller import _ensure_main_thread
    _ensure_main_thread()
    document = app.ActiveDocument
    if document is None:
        raise PftToolError("当前没有打开的文档。")
    try:
        gui_document = gui.getDocument(document.Name)
        if gui_document is None or gui.activeDocument() is not gui_document:
            raise ValueError()
        view = gui_document.activeView()
        if not all(callable(getattr(view, name, None)) for name in ("viewAxonometric", "fitAll")):
            raise ValueError()
    except Exception:
        raise PftToolError("没有可确定的活动三维视图，请先激活目标文档的三维视图。") from None
    try:
        view.viewAxonometric()
        view.fitAll()
    except Exception:
        raise PftToolError("原生视图操作失败。") from None
    return {"document": document.Name, "action": "axonometric_fit", "saved": False, "recomputed": False}


def _appearance_selection(gui, document):
    """Read unresolved whole-object identities; never substitute a Link source."""
    import pft_document_tools as document_tools
    try:
        selection = gui.Selection
        extended = getattr(selection, "getSelectionEx", None)
        records = []
        if callable(extended):
            try:
                records = list(extended("", 0))
            except TypeError:
                records = list(extended())
        if records:
            objects = []
            for record in records:
                if getattr(record, "SubElementNames", ()):
                    continue
                owner = getattr(record, "DocumentName", None)
                if owner is not None and owner != document.Name:
                    continue
                name = getattr(record, "ObjectName", None)
                obj = (document.getObject(name) if isinstance(name, str) and name
                       else getattr(record, "Object", None))
                if document_tools._live(document, obj):
                    objects.append(obj)
            return objects, True
        plain = getattr(selection, "getSelection", None)
        if not callable(plain):
            return [], True
        try:
            objects = list(plain("", False))
        except TypeError:
            objects = list(plain())
        return [obj for obj in objects if document_tools._live(document, obj)], bool(objects)
    except Exception:
        return [], True


def appearance_scope(app, gui):
    """Use whole GUI selection, otherwise existing visible part recommendations.

    Candidate discovery reads metadata only. An unsuitable raw selection never
    broadens to document defaults, and Link sources are never resolved here.
    """
    import pft_document_tools as document_tools
    import pft_visual_tools as visual
    from pft_workbench_controller import _ensure_main_thread
    _ensure_main_thread()
    try:
        document = document_tools._document(app, None)
        if document is None:
            raise PftToolError("当前没有打开的文档。")
        selected, had_raw = _appearance_selection(gui, document)
        if had_raw:
            names = [obj.Name for obj in selected]
        else:
            snapshot = document_tools.read_document_tools(app, gui, target_names=[])
            names = [row["Name"] for row in snapshot["candidates"] if row["recommended"]]
    except document_tools.PftDocumentToolsError as error:
        raise PftToolError(error.message) from None
    names = list(dict.fromkeys(names))
    if not names or len(names) > visual.PFT_MAX_VISUAL_TARGETS:
        raise PftToolError("请明确勾选最多100个不重复的完整对象。")
    return document.Name, names


def show_appearance_dialog():
    import FreeCAD as app
    import FreeCADGui as gui
    import pft_visual_tools as visual
    from pft_appearance_controls import show_appearance_panel
    try:
        return show_appearance_panel(app, gui, parent=gui.getMainWindow(), get_scope=lambda: appearance_scope(app, gui))
    except visual.PftVisualToolsError as error:
        raise PftToolError(error.message) from None
