# SPDX-License-Identifier: Apache-2.0
"""Local toolbar camera actions and conservative current-document appearance scope."""
import sys
from pathlib import Path
import threading
import types
import unittest
from unittest.mock import patch

from test_pft_document_tools import PftDoc, PftObject, PftApp, PftGui

ADDON = Path(__file__).resolve().parents[2] / "addon/PhoenixFreeCADTools"
_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_workbench_tools as tools
    import pft_visual_tools
finally:
    sys.path[:] = _path


class WorkbenchVisualCommandsTests(unittest.TestCase):
    def setUp(self):
        self.document = PftDoc()
        self.app = PftApp(self.document)
        self.gui = PftGui(self.document)
        self.body = PftObject(self.document, "Body", "PartDesign::Body")
        self.pad = PftObject(self.document, "Pad", "PartDesign::Pad")
        self.sketch = PftObject(self.document, "Sketch", "Sketcher::SketchObject")
        self.hidden = PftObject(self.document, "Hidden", visible=False)
        self.body.Group = [self.pad, self.sketch]
        self.events = []
        self.view = types.SimpleNamespace(
            viewAxonometric=lambda: self.events.append("axonometric"),
            fitAll=lambda: self.events.append("fit"))
        self.gui_document = types.SimpleNamespace(activeView=lambda: self.view,
            getInEdit=lambda: (_ for _ in ()).throw(AssertionError("camera actions need not inspect editing")))
        self.gui.getDocument = lambda name: self.gui_document
        self.gui.activeDocument = lambda: self.gui_document

    def record(self, obj, subnames=()):
        return types.SimpleNamespace(Object=obj, ObjectName=obj.Name, DocumentName=obj.Document.Name,
            SubElementNames=subnames)

    def assert_no_shapes_read(self):
        self.assertTrue(all(obj.reads == 0 for obj in self.document.Objects))

    def test_axonometric_then_fit_use_native_camera_without_model_or_bridge_actions(self):
        self.app.getActiveTransaction = lambda: (_ for _ in ()).throw(AssertionError("must not inspect transactions"))
        result = tools.run_axonometric_fit(self.app, self.gui)
        self.assertEqual(self.events, ["axonometric", "fit"])
        self.assertEqual(result, {"document": self.document.Name, "action": "axonometric_fit", "saved": False, "recomputed": False})
        self.assert_no_shapes_read()
        self.assertIs(self.app.ActiveDocument, self.document)

    def test_no_document_or_non_3d_inactive_view_fails_before_camera_dispatch(self):
        self.app.ActiveDocument = None
        with self.assertRaises(tools.PftToolError): tools.run_axonometric_fit(self.app, self.gui)
        self.app.ActiveDocument = self.document
        for mode in ("inactive", "not_3d", "unavailable"):
            with self.subTest(mode=mode):
                self.gui.activeDocument = lambda: self.gui_document if mode != "inactive" else object()
                self.gui_document.activeView = lambda: self.view if mode == "inactive" else types.SimpleNamespace(fitAll=lambda: None) if mode == "not_3d" else None
                with self.assertRaises(tools.PftToolError): tools.run_axonometric_fit(self.app, self.gui)
        self.assertEqual(self.events, [])

    def test_native_camera_failure_does_not_dispatch_the_second_action(self):
        self.view.viewAxonometric = lambda: (_ for _ in ()).throw(RuntimeError("native view unavailable"))
        with self.assertRaises(tools.PftToolError): tools.run_axonometric_fit(self.app, self.gui)
        self.assertEqual(self.events, [])
        self.assert_no_shapes_read()

    def test_camera_requires_the_gui_thread(self):
        errors = []
        def run():
            try: tools.run_axonometric_fit(self.app, self.gui)
            except tools.PftToolError as error: errors.append(error)
        thread = threading.Thread(target=run)
        thread.start(); thread.join()
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.events, [])

    def test_explicit_appearance_scope_requires_the_gui_thread_before_selection_reads(self):
        self.gui.Selection.records = [self.record(self.body)]
        self.gui.Selection.getSelectionEx = lambda *args: (_ for _ in ()).throw(AssertionError("must not read selection from a worker"))
        errors = []
        def run():
            try: tools.appearance_scope(self.app, self.gui)
            except tools.PftToolError as error: errors.append(error)
        thread = threading.Thread(target=run)
        thread.start(); thread.join()
        self.assertEqual(len(errors), 1)
        self.assert_no_shapes_read()

    def test_no_selection_uses_visible_outer_part_recommendations_only(self):
        self.assertEqual(tools.appearance_scope(self.app, self.gui), (self.document.Name, ["Body"]))
        self.assert_no_shapes_read()
        self.assertEqual(self.gui.Selection.records, [])
        part = PftObject(self.document, "Part", "App::Part")
        part.Group = [self.body]
        self.assertEqual(tools.appearance_scope(self.app, self.gui), (self.document.Name, ["Part"]))
        self.assert_no_shapes_read()

    def test_explicit_complete_selection_wins_including_internal_or_hidden_objects(self):
        self.gui.Selection.records = [self.record(self.pad), self.record(self.hidden), self.record(self.pad)]
        self.assertEqual(tools.appearance_scope(self.app, self.gui), (self.document.Name, ["Pad", "Hidden"]))
        self.assert_no_shapes_read()
        self.assertEqual(len(self.gui.Selection.records), 3)

    def test_raw_link_name_wins_over_resolved_source_without_reading_it(self):
        link = PftObject(self.document, "Link", "App::Link")
        self.gui.Selection.records = [types.SimpleNamespace(Object=self.body, ObjectName=link.Name,
            DocumentName=self.document.Name, SubElementNames=())]
        self.assertEqual(tools.appearance_scope(self.app, self.gui), (self.document.Name, ["Link"]))
        self.assert_no_shapes_read()

    def test_unsuitable_face_foreign_stale_or_failed_selection_never_broadens(self):
        foreign = PftObject(PftDoc("Foreign"), "ForeignBody", "PartDesign::Body")
        stale = PftObject(self.document, "Stale", "PartDesign::Body")
        self.document.Objects.remove(stale)
        for record in (self.record(self.body, ("Face1",)), self.record(foreign), self.record(stale)):
            with self.subTest(record=record.ObjectName):
                self.gui.Selection.records = [record]
                with self.assertRaises(tools.PftToolError): tools.appearance_scope(self.app, self.gui)
        self.gui.Selection.getSelectionEx = lambda *args: (_ for _ in ()).throw(RuntimeError("selection unavailable"))
        with self.assertRaises(tools.PftToolError): tools.appearance_scope(self.app, self.gui)
        self.assert_no_shapes_read()

    def test_foreign_raw_document_cannot_substitute_a_resolved_local_source(self):
        self.gui.Selection.records = [types.SimpleNamespace(Object=self.body, ObjectName="Instance",
            DocumentName="ForeignDocument", SubElementNames=())]
        with self.assertRaises(tools.PftToolError): tools.appearance_scope(self.app, self.gui)
        self.assert_no_shapes_read()

    def test_explicit_selection_does_not_depend_on_document_candidate_scan_limit(self):
        for index in range(2001): PftObject(self.document, "Imported%d" % index)
        selected = self.document.Objects[-1]
        self.gui.Selection.records = [self.record(selected)]
        self.assertEqual(tools.appearance_scope(self.app, self.gui), (self.document.Name, [selected.Name]))
        self.assert_no_shapes_read()

    def test_target_budget_is_rejected_instead_of_silently_truncated(self):
        for index in range(101): PftObject(self.document, "Imported%d" % index)
        with self.assertRaises(tools.PftToolError): tools.appearance_scope(self.app, self.gui)
        self.assert_no_shapes_read()

    def test_panel_entry_is_lazy_so_existing_nonmodal_panel_can_be_reused_first(self):
        window, dialog = object(), object()
        self.gui.getMainWindow = lambda: window
        calls = []
        def show(app, gui, **kwargs):
            calls.append((app, gui, kwargs))
            self.assertEqual(kwargs["get_scope"](), (self.document.Name, ["Body"]))
            return dialog
        with patch.dict(sys.modules, {"FreeCAD": self.app, "FreeCADGui": self.gui,
                "pft_appearance_controls": types.SimpleNamespace(show_appearance_panel=show)}):
            self.assertIs(tools.show_appearance_dialog(), dialog)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][2]["parent"], window)
        self.assertNotIn("scope", calls[0][2])
        self.assert_no_shapes_read()


if __name__ == "__main__": unittest.main()
