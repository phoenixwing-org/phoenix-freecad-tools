# SPDX-License-Identifier: Apache-2.0
"""Native material value and transaction semantics without FreeCAD or Qt."""

import copy
import importlib.util
from pathlib import Path
import struct
import threading
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
spec = importlib.util.spec_from_file_location("pft_visual_tools_tests", ADDON / "pft_visual_tools.py")
visual = importlib.util.module_from_spec(spec)
spec.loader.exec_module(visual)


def single(value):
    return struct.unpack("f", struct.pack("f", value))[0]


class Material:
    def __init__(self, **changes):
        values = {"DiffuseColor": (0.2, 0.4, 0.6, 1.0), "AmbientColor": (0.1, 0.2, 0.3, 1.0),
                  "SpecularColor": (0.6, 0.5, 0.4, 1.0), "EmissiveColor": (0.0, 0.1, 0.0, 1.0),
                  "Shininess": 42.0, "Transparency": 0.0}
        values.update(changes)
        for key, value in values.items():
            setattr(self, key, tuple(single(n) for n in value) if isinstance(value, (tuple, list)) else single(value))


class View:
    def __init__(self, materials=None, link=False):
        self._appearance = materials or [Material()]
        self._transparency = round(self._appearance[0].Transparency * 100)
        self.OverrideMaterial, self.ShapeMaterial = False, Material(DiffuseColor=(0.9, 0.8, 0.1, 1.0))
        self.MaterialList, self.OverrideMaterialList, self.OverrideColorList = [], [], []
        self.assignments, self.scalar_assignments = 0, 0
        self.fail_next, self.coerce_next, self.pollute = False, False, None

    def getTypeIdOfProperty(self, name):
        return "App::PropertyMaterialList" if name == "ShapeAppearance" else "App::PropertyInteger"

    @property
    def ShapeAppearance(self): return self._appearance

    @ShapeAppearance.setter
    def ShapeAppearance(self, materials):
        self.assignments += 1
        self._appearance = list(materials)
        self._transparency = round(materials[0].Transparency * 100)
        if self.coerce_next:
            self.coerce_next = False
            self._appearance[0].Shininess = 0.0
        if self.pollute is not None:
            self.pollute()
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("synthetic write failure with private details")

    @property
    def Transparency(self): return self._transparency

    @Transparency.setter
    def Transparency(self, value):
        self.scalar_assignments += 1
        self._transparency = value

    def snapshot(self):
        return copy.deepcopy((self._appearance, self._transparency, self.OverrideMaterial, self.ShapeMaterial))

    def restore(self, state):
        self._appearance, self._transparency, self.OverrideMaterial, self.ShapeMaterial = copy.deepcopy(state)


class Object:
    def __init__(self, document, name, materials=None, source=None):
        self.Document, self.Name, self.Label = document, name, "Label " + name
        self.TypeId = "App::Link" if source is not None else "Part::Feature"
        self.Shape = types.SimpleNamespace(isNull=lambda: False)
        self.ViewObject = View(materials, source is not None)
        if source is not None:
            self.LinkedObject, self.ElementCount, self.ElementList = source, 0, []


class Document:
    Name, UndoMode = "SyntheticDoc", 1

    def __init__(self):
        self.objects, self.opened, self.commits, self.aborts = {}, 0, 0, 0
        self.undo_stack, self.redo_stack = [], []
        self.current = None

    def add(self, name, materials=None, source=None):
        obj = Object(self, name, materials, source)
        self.objects[name] = obj
        return obj

    def getObject(self, name): return self.objects.get(name)
    def snapshot(self): return {name: obj.ViewObject.snapshot() for name, obj in self.objects.items()}
    def restore(self, state):
        for name, before in state.items(): self.objects[name].ViewObject.restore(before)
    def openTransaction(self, title):
        self.opened += 1
        self.current, self.app.transaction = self.snapshot(), True
    def commitTransaction(self):
        self.commits += 1
        self.undo_stack.append((self.current, self.snapshot()))
        self.current, self.app.transaction = None, False
    def abortTransaction(self):
        self.aborts += 1
        self.restore(self.current)
        self.current, self.app.transaction = None, False
    def undo(self):
        before, after = self.undo_stack.pop()
        self.restore(before)
        self.redo_stack.append((before, after))
    def redo(self):
        before, after = self.redo_stack.pop()
        self.restore(after)
        self.undo_stack.append((before, after))
    def save(self): raise AssertionError("save forbidden")
    def recompute(self): raise AssertionError("recompute forbidden")


def plain_snapshot(document):
    return {name: {"ShapeAppearance": [vars(m).copy() for m in obj.ViewObject.ShapeAppearance],
                   "Transparency": obj.ViewObject.Transparency, "OverrideMaterial": obj.ViewObject.OverrideMaterial,
                   "ShapeMaterial": vars(obj.ViewObject.ShapeMaterial).copy()} for name, obj in document.objects.items()}


class VisualToolsTests(unittest.TestCase):
    def setUp(self):
        visual._plans.clear()
        self.doc = Document()
        self.app = types.SimpleNamespace(Material=Material, ActiveDocument=self.doc, transaction=False)
        self.app.getDocument = lambda name: self.doc if name == self.doc.Name else None
        self.app.getActiveTransaction = lambda: self.app.transaction
        self.doc.app = self.app
        self.editing, self.commands = None, []
        self.gui_doc = types.SimpleNamespace(getInEdit=lambda: self.editing,
            activeView=lambda: types.SimpleNamespace(fitAll=lambda: None, viewAxonometric=lambda: None))
        self.gui = types.SimpleNamespace(getDocument=lambda name: self.gui_doc,
            activeDocument=lambda: self.gui_doc, listCommands=lambda: list(visual._VIEW_COMMANDS.values()),
            runCommand=lambda command, argument: self.commands.append((command, argument)))
        self.addCleanup(visual._plans.clear)

    def prepare(self, names, action="random_color", transparency=None):
        return visual.prepare_appearance(self.app, self.gui, self.doc.Name, names, action, transparency=transparency)

    def apply(self, plan): return visual.apply_appearance(self.app, self.gui, plan)

    def assert_error(self, code, callback):
        with self.assertRaises(visual.PftVisualToolsError) as caught: callback()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), caught.exception.message)
        return caught.exception

    def test_random_prepare_is_readonly_and_apply_preserves_full_face_materials_and_undo_redo(self):
        obj = self.doc.add("Part", [Material(Transparency=i / 10, Shininess=20 + i) for i in range(6)])
        original_materials = list(obj.ViewObject.ShapeAppearance)
        before = plain_snapshot(self.doc)
        plan = self.prepare(["Part"])
        self.assertEqual(plain_snapshot(self.doc), before)
        self.assertEqual(self.doc.opened, 0)
        self.assertIsInstance(plan["seed"], int)
        after = plan["targets"][0]["after"]["ShapeAppearance"]
        for index, material in enumerate(after):
            for key in visual._MATERIAL_FIELDS:
                if key != "DiffuseColor": self.assertEqual(material[key], before["Part"]["ShapeAppearance"][index][key])
        with patch.object(visual.random, "Random", side_effect=AssertionError("no redraw during apply")):
            self.assertEqual(self.apply(plan)["changed_count"], 1)
        self.assertEqual(self.doc.commits, 1)
        self.assertEqual(obj.ViewObject.scalar_assignments, 0)
        self.assertEqual([vars(m) for m in original_materials], before["Part"]["ShapeAppearance"])
        committed = plain_snapshot(self.doc)
        self.doc.undo(); self.assertEqual(plain_snapshot(self.doc), before)
        self.doc.redo(); self.assertEqual(plain_snapshot(self.doc), committed)

    def test_opacity_updates_every_material_even_when_global_already_zero(self):
        obj = self.doc.add("Part", [Material(Transparency=0.0), Material(Transparency=0.7)])
        before = plain_snapshot(self.doc)
        self.assertEqual(obj.ViewObject.Transparency, 0)
        self.apply(self.prepare(["Part"], "opaque"))
        self.assertEqual([m.Transparency for m in obj.ViewObject.ShapeAppearance], [0, 0])
        for actual, original in zip(obj.ViewObject.ShapeAppearance, before["Part"]["ShapeAppearance"]):
            for key in visual._MATERIAL_FIELDS:
                if key != "Transparency": self.assertEqual(getattr(actual, key), original[key])
        self.apply(self.prepare(["Part"], "half_transparent"))
        self.assertEqual([m.Transparency for m in obj.ViewObject.ShapeAppearance], [0.5, 0.5])
        self.assertEqual(obj.ViewObject.Transparency, 50)
        self.assertEqual(obj.ViewObject.scalar_assignments, 0)

    def test_percent_transparency_roundtrips_all_face_materials_and_undo_redo(self):
        obj = self.doc.add("Part", [Material(Transparency=i / 10, Shininess=20 + i) for i in range(6)])
        for percent in (0, 1, 37, 50, 99, 100):
            with self.subTest(percent=percent):
                before = plain_snapshot(self.doc)
                plan = self.prepare(["Part"], "set_transparency", percent)
                self.assertEqual(plan["transparency"], percent)
                self.assertEqual(plain_snapshot(self.doc), before)
                self.assertEqual(plan["targets"][0]["after"]["Transparency"], percent)
                expected_alpha = Material(Transparency=percent / 100.0).Transparency
                summary = self.apply(plan)
                self.assertEqual(summary["action"], "set_transparency")
                self.assertEqual(obj.ViewObject.Transparency, percent)
                for actual, original in zip(obj.ViewObject.ShapeAppearance, before["Part"]["ShapeAppearance"]):
                    self.assertEqual(actual.Transparency, expected_alpha)
                    for key in visual._MATERIAL_FIELDS:
                        if key != "Transparency": self.assertEqual(getattr(actual, key), original[key])
                after = plain_snapshot(self.doc)
                self.doc.undo(); self.assertEqual(plain_snapshot(self.doc), before)
                self.doc.redo(); self.assertEqual(plain_snapshot(self.doc), after)
        self.assertEqual(obj.ViewObject.scalar_assignments, 0)
        self.assertEqual((self.doc.opened, self.doc.commits, self.doc.aborts), (6, 6, 0))

    def test_link_percent_transparency_uses_full_effective_material_without_source_change(self):
        source = self.doc.add("Source", [Material(DiffuseColor=(0.1, 0.7, 0.2, 1), Shininess=72)])
        link = self.doc.add("Link", source=source)
        self.doc.add("OtherLink", source=source)
        for percent in (0, 37, 100):
            with self.subTest(percent=percent):
                before = plain_snapshot(self.doc)
                self.apply(self.prepare(["Link"], "set_transparency", percent))
                after = plain_snapshot(self.doc)
                self.assertTrue(link.ViewObject.OverrideMaterial)
                self.assertEqual(link.ViewObject.ShapeMaterial.Transparency, Material(Transparency=percent / 100.0).Transparency)
                for key in visual._MATERIAL_FIELDS:
                    if key != "Transparency":
                        self.assertEqual(getattr(link.ViewObject.ShapeMaterial, key), getattr(source.ViewObject.ShapeAppearance[0], key))
                self.assertEqual(after["Source"], before["Source"])
                self.assertEqual(after["OtherLink"], before["OtherLink"])
                self.doc.undo(); self.assertEqual(plain_snapshot(self.doc), before)
                self.doc.redo(); self.assertEqual(plain_snapshot(self.doc), after)

    def test_percent_parameter_is_required_strict_and_validated_before_native_access(self):
        with patch.object(self.app, "getDocument", side_effect=AssertionError("invalid parameters must not read CAD")):
            for percent in (None, True, False, -1, 101, 37.0, float("nan"), float("inf"), "37", [], {}):
                with self.subTest(percent=percent):
                    self.assert_error("INVALID_TRANSPARENCY", lambda: self.prepare(["Part"], "set_transparency", percent))
            for action in ("random_color", "half_transparent", "opaque"):
                self.assert_error("INVALID_TRANSPARENCY", lambda: self.prepare(["Part"], action, 37))
        self.assertEqual(self.doc.opened, 0)
        self.assertEqual(visual._plans, {})

    def test_percent_plan_tampering_is_rejected_before_transaction(self):
        self.doc.add("Part")
        plan = self.prepare(["Part"], "set_transparency", 37)
        changed = copy.deepcopy(plan); changed["transparency"] = 38
        self.assert_error("VISUAL_PLAN_INVALID", lambda: self.apply(changed))
        self.assertEqual(self.doc.opened, 0)

    def test_shared_material_objects_are_not_mutated(self):
        shared = Material()
        chosen = self.doc.add("Chosen", [shared])
        other = self.doc.add("Other", [shared])
        before = copy.deepcopy(vars(shared))
        self.apply(self.prepare(["Chosen"], "half_transparent"))
        self.assertEqual(vars(shared), before)
        self.assertIs(other.ViewObject.ShapeAppearance[0], shared)
        self.assertIsNot(chosen.ViewObject.ShapeAppearance[0], shared)

    def test_link_uses_effective_source_material_instead_of_latent_default_and_preserves_source(self):
        source = self.doc.add("Source", [Material(DiffuseColor=(0.1, 0.7, 0.2, 1), Shininess=72)])
        link = self.doc.add("Link", source=source)
        self.doc.add("OtherLink", source=source)
        before = plain_snapshot(self.doc)
        plan = self.prepare(["Link"], "half_transparent")
        self.assertTrue(plan["targets"][0]["enables_link_override"])
        self.apply(plan)
        after = plain_snapshot(self.doc)
        self.assertTrue(link.ViewObject.OverrideMaterial)
        self.assertEqual(link.ViewObject.ShapeMaterial.DiffuseColor, source.ViewObject.ShapeAppearance[0].DiffuseColor)
        self.assertEqual(link.ViewObject.ShapeMaterial.Shininess, 72)
        self.assertEqual(link.ViewObject.ShapeMaterial.Transparency, 0.5)
        self.assertEqual(after["Source"], before["Source"])
        self.assertEqual(after["OtherLink"], before["OtherLink"])
        self.doc.undo(); self.assertEqual(plain_snapshot(self.doc), before)
        self.doc.redo(); self.assertEqual(plain_snapshot(self.doc), after)

    def test_existing_link_override_keeps_its_own_non_target_fields(self):
        source = self.doc.add("Source", [Material(), Material(Shininess=73)])
        link = self.doc.add("Link", source=source)
        link.ViewObject.OverrideMaterial = True
        link.ViewObject.ShapeMaterial = Material(Shininess=81, Transparency=0.3)
        before = vars(link.ViewObject.ShapeMaterial).copy()
        self.apply(self.prepare(["Link"]))
        for key in visual._MATERIAL_FIELDS:
            if key != "DiffuseColor": self.assertEqual(getattr(link.ViewObject.ShapeMaterial, key), before[key])

    def test_complex_links_and_selected_link_source_are_rejected_without_transaction(self):
        source = self.doc.add("Source")
        link = self.doc.add("Link", source=source)
        for field, value in (("ElementCount", 2), ("ElementList", [source]), ("LinkedObject", (source, ["Face1"])), ("TypeId", "App::LinkElement")):
            with self.subTest(field=field):
                original = getattr(link, field)
                setattr(link, field, value)
                self.assert_error("LINK_UNSUPPORTED", lambda: self.prepare(["Link"]))
                setattr(link, field, original)
        outer = self.doc.add("Outer", source=link)
        self.assert_error("LINK_UNSUPPORTED", lambda: self.prepare([outer.Name]))
        self.assert_error("LINK_SOURCE_SELECTED", lambda: self.prepare(["Source", "Link"]))
        source.ViewObject.ShapeAppearance = [Material(), Material(Shininess=73)]
        self.assert_error("LINK_MATERIAL_UNSUPPORTED", lambda: self.prepare(["Link"], "opaque"))
        self.assertEqual(self.doc.opened, 0)

    def test_complete_target_material_and_identity_snapshots_are_checked_before_opening_transaction(self):
        obj = self.doc.add("Part")
        for mutate in (lambda: setattr(obj.ViewObject.ShapeAppearance[0], "AmbientColor", (0.8, 0.2, 0.1, 1)),
                       lambda: setattr(obj, "ViewObject", View()),
                       lambda: self.doc.objects.update(Part=Object(self.doc, "Part"))):
            with self.subTest(mutate=mutate):
                self.doc.objects["Part"] = obj
                obj.ViewObject = View()
                plan = self.prepare(["Part"])
                mutate()
                self.assert_error("VISUAL_SNAPSHOT_CHANGED", lambda: self.apply(plan))
        self.assertEqual(self.doc.opened, 0)

    def test_link_source_material_and_topology_changes_are_stale(self):
        source = self.doc.add("Source")
        link = self.doc.add("Link", source=source)
        plan = self.prepare(["Link"])
        source.ViewObject.ShapeAppearance[0].EmissiveColor = Material(EmissiveColor=(0.8, 0.1, 0.2, 1)).EmissiveColor
        self.assert_error("VISUAL_SNAPSHOT_CHANGED", lambda: self.apply(plan))
        plan = self.prepare(["Link"])
        link.LinkedObject = self.doc.add("Replacement")
        self.assert_error("VISUAL_SNAPSHOT_CHANGED", lambda: self.apply(plan))
        self.assertEqual(self.doc.opened, 0)

    def test_write_failure_aborts_every_target_and_failed_plan_cannot_reapply(self):
        self.doc.add("First")
        second = self.doc.add("Second")
        before = plain_snapshot(self.doc)
        plan = self.prepare(["First", "Second"], "half_transparent")
        second.ViewObject.fail_next = True
        error = self.assert_error("VISUAL_APPLY_FAILED", lambda: self.apply(plan))
        self.assertNotIn("private", error.message)
        self.assertEqual(plain_snapshot(self.doc), before)
        self.assertEqual((self.doc.opened, self.doc.aborts, self.doc.commits), (1, 1, 0))
        self.assert_error("VISUAL_PLAN_USED", lambda: self.apply(plan))

    def test_silent_native_coercion_is_verified_and_aborted(self):
        obj = self.doc.add("Part")
        before = plain_snapshot(self.doc)
        plan = self.prepare(["Part"])
        obj.ViewObject.coerce_next = True
        self.assert_error("VISUAL_APPLY_FAILED", lambda: self.apply(plan))
        self.assertEqual(plain_snapshot(self.doc), before)
        self.assertEqual(self.doc.aborts, 1)

    def test_link_final_verification_detects_source_pollution_and_aborts(self):
        source = self.doc.add("Source")
        self.doc.add("Link", source=source)
        before = plain_snapshot(self.doc)
        plan = self.prepare(["Link"], "half_transparent")
        def faulty_material(**values):
            source.ViewObject.ShapeAppearance[0].Shininess = 0.0
            return Material(**values)
        with patch.object(self.app, "Material", side_effect=faulty_material):
            self.assert_error("VISUAL_APPLY_FAILED", lambda: self.apply(plan))
        self.assertEqual(plain_snapshot(self.doc), before)
        self.assertEqual(self.doc.aborts, 1)

    def test_abort_failure_is_reported_without_claiming_restoration(self):
        obj = self.doc.add("Part")
        plan = self.prepare(["Part"])
        obj.ViewObject.fail_next = True
        def fail_abort(): raise RuntimeError("synthetic abort failure")
        with patch.object(self.doc, "abortTransaction", side_effect=fail_abort):
            self.assert_error("VISUAL_ROLLBACK_UNVERIFIED", lambda: self.apply(plan))

    def test_successful_plan_replay_never_reexecutes_even_after_undo(self):
        self.doc.add("Part")
        plan = self.prepare(["Part"])
        self.apply(plan)
        self.doc.undo()
        before = plain_snapshot(self.doc)
        self.assertTrue(self.apply(plan)["replayed"])
        self.assertEqual(plain_snapshot(self.doc), before)
        self.assertEqual(self.doc.opened, 1)

    def test_tampered_and_expired_plans_never_open_transaction(self):
        self.doc.add("Part")
        plan = self.prepare(["Part"])
        tampered = copy.deepcopy(plan)
        tampered["targets"][0]["after"]["ShapeAppearance"][0]["Shininess"] = 99
        self.assert_error("VISUAL_PLAN_INVALID", lambda: self.apply(tampered))
        visual._plans[plan["token"]]["created"] -= 301
        self.assert_error("VISUAL_PLAN_INVALID", lambda: self.apply(plan))
        self.assertEqual(self.doc.opened, 0)

    def test_edit_transaction_active_document_and_undo_guards_are_failclosed(self):
        self.doc.add("Part")
        self.editing = object()
        self.assert_error("DOCUMENT_BUSY", lambda: self.prepare(["Part"]))
        self.editing = None
        self.app.transaction = True
        self.assert_error("DOCUMENT_BUSY", lambda: self.prepare(["Part"]))
        self.app.transaction = False
        self.app.ActiveDocument = None
        self.assert_error("DOCUMENT_NOT_ACTIVE", lambda: self.prepare(["Part"]))
        self.app.ActiveDocument = self.doc
        self.doc.UndoMode = 0
        self.assert_error("UNDO_DISABLED", lambda: self.prepare(["Part"]))
        self.assertEqual(self.doc.opened, 0)

    def test_invalid_targets_action_and_oversized_material_preview_are_rejected(self):
        obj = self.doc.add("Part")
        for names in (None, [], ["Part", "Part"], ["Part"] * 101, [None]):
            self.assert_error("VISUAL_TARGETS_REQUIRED", lambda names=names: self.prepare(names))
        self.assert_error("INVALID_VISUAL_ACTION", lambda: self.prepare(["Part"], []))
        obj.ViewObject._appearance = [Material()] * 257
        self.assert_error("VISUAL_PREVIEW_TOO_LARGE", lambda: self.prepare(["Part"]))
        obj.ViewObject._appearance = [Material(Transparency=i / 256) for i in range(256)]
        self.assert_error("VISUAL_PREVIEW_TOO_LARGE", lambda: self.prepare(["Part"]))
        self.assertEqual(self.doc.opened, 0)

    def test_view_commands_use_verified_ids_and_never_activate_or_change_selection(self):
        for action, command in visual._VIEW_COMMANDS.items():
            self.assertEqual(visual.run_view_action(self.app, self.gui, self.doc.Name, action)["command"], command)
        self.assertEqual(self.commands, [("Std_ViewFitAll", 0), ("Std_ViewIsometric", 0)])
        self.assertEqual(self.doc.opened, 0)
        self.gui.listCommands = lambda: []
        self.assert_error("VIEW_COMMAND_UNAVAILABLE", lambda: visual.run_view_action(self.app, self.gui, self.doc.Name, "fit"))
        self.gui_doc.activeView = lambda: object()
        self.assert_error("VIEW_UNAVAILABLE", lambda: visual.run_view_action(self.app, self.gui, self.doc.Name, "fit"))

    def test_combined_axonometric_fit_validates_both_commands_before_dispatch(self):
        result = visual.run_view_action(self.app, self.gui, self.doc.Name, "axonometric_fit")
        self.assertEqual(result["commands"], ["Std_ViewIsometric", "Std_ViewFitAll"])
        self.assertEqual(self.commands, [("Std_ViewIsometric", 0), ("Std_ViewFitAll", 0)])
        self.assertFalse(result["saved"]); self.assertFalse(result["recomputed"])
        self.assertEqual(self.doc.opened, 0)
        for available in (("Std_ViewIsometric",), ("Std_ViewFitAll",), ()):
            with self.subTest(available=available):
                self.commands.clear(); self.gui.listCommands = lambda: available
                self.assert_error("VIEW_COMMAND_UNAVAILABLE", lambda: visual.run_view_action(self.app, self.gui, self.doc.Name, "axonometric_fit"))
                self.assertEqual(self.commands, [])
        self.gui.listCommands = lambda: ["Std_ViewIsometric", "Std_ViewFitAll"]
        self.editing = object()
        self.assert_error("DOCUMENT_BUSY", lambda: visual.run_view_action(self.app, self.gui, self.doc.Name, "axonometric_fit"))
        self.assertEqual(self.commands, [])
        self.assertEqual(self.doc.opened, 0)

    def inspect(self, names):
        return visual.inspect_appearance(self.app, self.gui, self.doc.Name, names)

    def test_inspection_reports_every_material_and_global_mismatch_without_writes_or_plan(self):
        obj = self.doc.add("Part", [Material(Transparency=0), Material(Transparency=0.37)])
        before = plain_snapshot(self.doc)
        self.doc.UndoMode = 0
        report = self.inspect(["Part"])
        row = report["rows"][0]
        self.assertEqual((row["Name"], row["Label"], row["kind"], row["status"], row["reason"]),
                         ("Part", "Label Part", "shape", "supported", None))
        self.assertEqual(row["global_transparency_percent"], 0)
        self.assertEqual(row["material_transparency_percent"], [0, single(0.37) * 100])
        self.assertEqual(row["reported_material_transparency_percent"], row["material_transparency_percent"])
        self.assertTrue(row["mixed_material_transparency"])
        self.assertTrue(row["global_material_mismatch"])
        self.assertEqual(plain_snapshot(self.doc), before)
        self.assertEqual((obj.ViewObject.assignments, obj.ViewObject.scalar_assignments, self.doc.opened), (0, 0, 0))
        self.assertEqual(visual._plans, {})
        self.assertTrue(all(text in visual.TRANSLATABLE_STRINGS for text in report["warnings"]))

    def test_inspection_float_roundtrip_does_not_create_false_global_mismatch(self):
        self.doc.add("Part", [Material(Transparency=0.37), Material(Transparency=0.37)])
        row = self.inspect(["Part"])["rows"][0]
        self.assertEqual(row["global_transparency_percent"], 37)
        self.assertFalse(row["mixed_material_transparency"])
        self.assertFalse(row["global_material_mismatch"])
        obj = self.doc.objects["Part"]
        obj.ViewObject = types.SimpleNamespace(ShapeAppearance=[Material(Transparency=0.5)],
                                               getTypeIdOfProperty=lambda name: "App::PropertyMaterialList")
        row = self.inspect(["Part"])["rows"][0]
        self.assertEqual(row["status"], "supported")
        self.assertIsNone(row["global_transparency_percent"])
        self.assertIsNone(row["global_material_mismatch"])

    def test_link_inspection_separates_latent_local_reported_and_source_materials(self):
        source = self.doc.add("Source", [Material(Transparency=0.2), Material(Transparency=0.6)])
        source.FileName = "/private/never-report-source-path"
        link = self.doc.add("Link", source=source)
        link.ViewObject.ShapeMaterial = Material(Transparency=0.42)
        # A Link attribute with uncertain provenance is deliberately not called global.
        link.ViewObject._transparency = 81
        before = plain_snapshot(self.doc)
        report = self.inspect(["Link"])
        row = report["rows"][0]
        self.assertEqual(row["status"], "supported")
        self.assertFalse(row["override_material"])
        self.assertEqual(row["material_transparency_percent"], [single(0.42) * 100])
        self.assertEqual(row["local_shape_material_transparency_percent"], single(0.42) * 100)
        self.assertEqual(row["source"]["Name"], "Source")
        self.assertEqual(row["reported_material_transparency_percent"], [single(0.2) * 100, single(0.6) * 100])
        self.assertFalse(row["mixed_material_transparency"])
        self.assertTrue(row["reported_mixed_material_transparency"])
        self.assertTrue(row["source"]["mixed_material_transparency"])
        self.assertIsNone(row["global_transparency_percent"])
        self.assertIsNone(row["global_material_mismatch"])
        self.assertEqual(plain_snapshot(self.doc), before)
        self.assertNotIn("/private", str(report))
        self.assertEqual(len(report["warnings"]), 3)
        link.ViewObject.OverrideMaterial = True
        row = self.inspect(["Link"])["rows"][0]
        self.assertEqual(row["reported_material_transparency_percent"], row["material_transparency_percent"])
        self.assertFalse(row["reported_mixed_material_transparency"])
        self.assertEqual(visual._plans, {})
        self.assertEqual(self.doc.opened, 0)

    def test_inspection_preserves_unsupported_missing_and_complex_targets_in_requested_order(self):
        self.doc.add("Good")
        bad = self.doc.add("Unsupported"); bad.ViewObject._appearance = []
        source = self.doc.add("Source")
        array = self.doc.add("Array", source=source); array.ElementCount = 2
        external = self.doc.add("External", source=source); external.LinkedObject = types.SimpleNamespace(Document=object(), Name="ExternalSource")
        names = ["Good", "Missing", "Unsupported", "Array", "External"]
        rows = self.inspect(names)["rows"]
        self.assertEqual([row["Name"] for row in rows], names)
        self.assertEqual([row["status"] for row in rows], ["supported", "unsupported", "unsupported", "unsupported", "unsupported"])
        for row in rows[1:]:
            self.assertIn(row["reason"], visual.TRANSLATABLE_STRINGS)
            self.assertIsNone(row["global_material_mismatch"])
            self.assertEqual(row["reported_material_transparency_percent"], [])
        self.assertEqual(self.doc.opened, 0)

    def test_inspection_invalid_material_values_are_explicit_unsupported_without_raw_errors(self):
        obj = self.doc.add("Part")
        for value in (None, True, -0.1, 1.1, float("nan"), float("inf"), "0.5"):
            with self.subTest(value=value):
                obj.ViewObject._appearance[0].Transparency = value
                row = self.inspect(["Part"])["rows"][0]
                self.assertEqual(row["status"], "unsupported")
                self.assertIn(row["reason"], visual.TRANSLATABLE_STRINGS)
                self.assertEqual(row["material_transparency_percent"], [])
        obj.Label = "invalid\ud800label"
        row = self.inspect(["Part"])["rows"][0]
        self.assertEqual(row["Label"], "Part")
        self.assertEqual(row["status"], "unsupported")

    def test_inspection_256_material_total_budget_is_atomic_and_does_not_build_plans(self):
        obj = self.doc.add("Part", [Material(Transparency=0.5)] * 256)
        self.assertEqual(len(self.inspect(["Part"])["rows"][0]["material_transparency_percent"]), 256)
        self.doc.add("Other")
        self.assert_error("VISUAL_INSPECTION_TOO_LARGE", lambda: self.inspect(["Part", "Other"]))
        obj.ViewObject._appearance = [Material()] * 257
        self.assert_error("VISUAL_INSPECTION_TOO_LARGE", lambda: self.inspect(["Part"]))
        self.assertEqual(visual._plans, {})
        self.assertEqual(self.doc.opened, 0)

    def test_inspection_counts_link_local_and_source_materials_and_caps_utf8_total(self):
        source = self.doc.add("Source", [Material()] * 255)
        self.doc.add("Link", source=source)
        self.doc.add("OtherLink", source=source)
        self.assertEqual(len(self.inspect(["Link"])["rows"][0]["source"]["material_transparency_percent"]), 255)
        self.assert_error("VISUAL_INSPECTION_TOO_LARGE", lambda: self.inspect(["Link", "OtherLink"]))
        source.ViewObject._appearance = [Material()]
        source.Label = "界" * (44 * 1024)
        self.assert_error("VISUAL_INSPECTION_TOO_LARGE", lambda: self.inspect(["Source"]))
        source.Label = "界" * (22 * 1024)
        other = self.doc.add("Other"); other.Label = source.Label
        self.assert_error("VISUAL_INSPECTION_TOO_LARGE", lambda: self.inspect(["Source", "Other"]))
        self.assertEqual(visual._plans, {})
        self.assertEqual(self.doc.opened, 0)

    def test_inspection_strict_target_scope_and_editing_guards(self):
        self.doc.add("Part")
        for names in (None, [], ["Part", "Part"], ["Part"] * 101, [None], ["bad\x00name"]):
            with self.subTest(names=names):
                with patch.object(self.app, "getDocument", side_effect=AssertionError("invalid names must not read native state")):
                    self.assert_error("VISUAL_TARGETS_REQUIRED", lambda: self.inspect(names))
        self.editing = object(); self.assert_error("DOCUMENT_BUSY", lambda: self.inspect(["Part"]))
        self.editing = None; self.app.transaction = True; self.assert_error("DOCUMENT_BUSY", lambda: self.inspect(["Part"]))
        self.app.transaction = False; self.app.ActiveDocument = None
        self.assert_error("DOCUMENT_NOT_ACTIVE", lambda: self.inspect(["Part"]))
        errors = []
        def worker():
            try: self.inspect(["Part"])
            except visual.PftVisualToolsError as error: errors.append(error.code)
        thread = threading.Thread(target=worker); thread.start(); thread.join()
        self.assertEqual(errors, ["MAIN_THREAD_REQUIRED"])
        self.assertEqual(self.doc.opened, 0)
        self.assertEqual(visual._plans, {})

    def test_background_thread_is_rejected_before_native_access(self):
        errors = []
        worker = threading.Thread(target=lambda: self._background(errors))
        worker.start(); worker.join()
        self.assertEqual(errors, ["MAIN_THREAD_REQUIRED"])

    def _background(self, errors):
        try: self.prepare(["Part"])
        except visual.PftVisualToolsError as error: errors.append(error.code)


if __name__ == "__main__":
    unittest.main()
