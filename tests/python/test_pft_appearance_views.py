# SPDX-License-Identifier: Apache-2.0
"""Complete native appearance preview/apply flow through injected Qt and core."""
import copy
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

from test_pft_document_tools_views import PftSignal, PftWidgets

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
_old_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    import pft_appearance_views as views
    import pft_workbench_i18n as i18n
finally:
    sys.path[:] = _old_path


class PftAppearanceDialogTests(unittest.TestCase):
    def setUp(self):
        self.old_language = i18n.get_language(); i18n.set_language("zh")
        self.addCleanup(i18n.set_language, self.old_language)
        self.widgets = PftWidgets()
        owner = self.widgets
        owner.dialogs, owner.deletions = [], 0
        class Dialog(owner.QDialog):
            def __init__(self, parent):
                super().__init__(parent); self.result = 0; owner.dialogs.append(self)
            def deleteLater(self): owner.deletions += 1; self.deleted = True
            def resize(self, width, height): self.requested_size = (width, height)
            def exec(self):
                owner.exec_calls += 1
                if owner.on_appearance_exec: owner.on_appearance_exec(self)
                return self.result
        class Integer(owner.Base):
            def __init__(self, parent):
                super().__init__(parent)
                self.minimum, self.maximum, self._value = 0, 100, 0
                self.valueChanged = PftSignal()
            def setRange(self, minimum, maximum): self.minimum, self.maximum = minimum, maximum
            def setSingleStep(self, value): self.step = value
            def setSuffix(self, value): self.suffix = value
            def setOrientation(self, value): self.orientation = value
            def value(self): return self._value
            def setValue(self, value):
                value = max(self.minimum, min(self.maximum, value))
                if self._value != value:
                    self._value = value; self.valueChanged.emit(value)
        owner.QDialog, owner.QSpinBox, owner.QSlider = Dialog, Integer, Integer
        owner.on_appearance_exec = None
        self.core = types.SimpleNamespace(Qt=types.SimpleNamespace(PlainText=0, Horizontal=1,
            ArrowType=types.SimpleNamespace(RightArrow=41, DownArrow=42),
            ToolButtonStyle=types.SimpleNamespace(ToolButtonTextBesideIcon=51)))
        handle = patch.object(views, "_qt", return_value=(self.core, owner)); handle.start(); self.addCleanup(handle.stop)
        class VisualError(RuntimeError):
            def __init__(self, code, message): super().__init__(message); self.code, self.message = code, message
        self.names = ["PartOne", "PartTwo"]
        self.inspection = {"document": "PublicFixture", "rows": [
            {"Name": name, "Label": "Public label " + name, "kind": "shape", "status": "supported", "reason": None,
             "global_transparency_percent": 0, "material_transparency_percent": [0.0, 50.0],
             "mixed_material_transparency": True, "global_material_mismatch": True}
            for name in self.names], "warnings": ["属性读取不等于屏幕像素验证。"]}
        self.base_plan = {"token": "public-fixture-plan", "document": "PublicFixture", "action": "random_color", "seed": 42,
            "target_count": len(self.names), "can_apply": True, "expires_in_seconds": 300,
            "targets": [{"Name": name, "Label": "Public label " + name,
                         "before": {"kind": "shape", "Transparency": 0, "ShapeAppearance": [{"Transparency": n / 256} for n in range(256)]},
                         "after": {"kind": "shape", "Transparency": 50, "ShapeAppearance": [{"Transparency": 0.5} for _ in range(256)]}}
                        for name in self.names],
            "warnings": ["完整注意事项一。", "完整注意事项二。", "完整注意事项三。", "完整注意事项四。"]}
        self.plans = []
        def prepare(app, gui, document, names, action, **kwargs):
            plan = copy.deepcopy(self.base_plan)
            plan["action"] = action
            plan["seed"] += len(self.plans)
            if action == "set_transparency": plan["transparency"] = kwargs["transparency"]
            self.plans.append(plan)
            return plan
        self.visual = types.SimpleNamespace(PftVisualToolsError=VisualError,
            inspect_appearance=Mock(side_effect=lambda *args: copy.deepcopy(self.inspection)),
            prepare_appearance=Mock(side_effect=prepare),
            apply_appearance=Mock(side_effect=lambda app, gui, plan: {"document": "PublicFixture", "action": plan["action"],
                "changed_count": 2, "target_count": 2, "saved": False, "recomputed": False, "replayed": False}))
        handle = patch.object(views, "_visual_tools", return_value=self.visual); handle.start(); self.addCleanup(handle.stop)
        self.app, self.gui, self.parent = object(), object(), owner.QWidget()

    def named(self, suffix): return self.widgets.named("PhoenixTools_Appearance" + suffix)
    def generate(self): self.widgets.click("PhoenixTools_AppearanceGenerate")
    def apply(self): self.named("Buttons").accepted.emit()
    def show(self, action="random_color"):
        return views.show_appearance_dialog(self.app, self.gui, self.parent, "PublicFixture", self.names, action)

    def test_open_diagnoses_all_targets_but_cancel_does_not_prepare_or_apply(self):
        def check(dialog):
            self.visual.inspect_appearance.assert_called_once_with(self.app, self.gui, "PublicFixture", self.names)
            self.assertEqual(json.loads(self.named("Inspection").toPlainText()), self.inspection)
            self.assertTrue(self.named("Generate").enabled)
            self.assertFalse(self.named("Apply").enabled)
            self.assertFalse(self.named("Apply").default)
            self.assertTrue(self.named("Cancel").default)
            self.named("Buttons").rejected.emit()
        self.widgets.on_appearance_exec = check
        self.assertIsNone(self.show())
        self.visual.prepare_appearance.assert_not_called(); self.visual.apply_appearance.assert_not_called()
        self.assertEqual((self.widgets.clipboard_calls, self.widgets.save_calls), (0, 0))

    def test_accepted_dialog_without_a_successful_apply_cannot_return_success(self):
        self.widgets.on_appearance_exec = lambda dialog: dialog.accept()
        self.assertIsNone(self.show())
        self.visual.apply_appearance.assert_not_called()

    def test_random_preview_keeps_every_before_after_and_warning_and_apply_uses_same_fixed_plan(self):
        def act(dialog):
            self.generate()
            self.assertEqual(json.loads(self.named("Preview").toPlainText()),
                             {key: value for key, value in self.plans[0].items() if key not in ("token", "seed")})
            self.assertEqual(len(json.loads(self.named("Preview").toPlainText())["targets"][1]["before"]["ShapeAppearance"]), 256)
            self.assertIn("完整注意事项四。", self.named("Warnings").toPlainText())
            visible = json.loads(self.named("Preview").toPlainText())
            self.assertNotIn("token", visible); self.assertNotIn("seed", visible)
            self.assertNotIn("public-fixture-plan", self.named("Preview").toPlainText())
            self.assertTrue(self.named("Apply").enabled)
            self.apply()
            self.assertIs(self.visual.apply_appearance.call_args.args[2], self.plans[0])
            self.assertEqual(self.plans[0]["seed"], 42)
        self.widgets.on_appearance_exec = act
        summary = self.show()
        self.assertEqual(summary["changed_count"], 2)
        self.assertFalse(summary["saved"])
        self.assertEqual(summary["warnings"], self.inspection["warnings"] + self.base_plan["warnings"])
        self.assertEqual(self.visual.prepare_appearance.call_count, 1)
        self.assertEqual(self.visual.apply_appearance.call_count, 1)

    def test_a_new_random_plan_requires_an_explicit_generate_click_and_apply_never_redraws(self):
        def act(dialog):
            self.generate(); self.generate()
            self.assertEqual([plan["seed"] for plan in self.plans], [42, 43])
            self.apply()
            self.assertIs(self.visual.apply_appearance.call_args.args[2], self.plans[1])
        self.widgets.on_appearance_exec = act
        self.assertIsInstance(self.show(), dict)
        self.assertEqual(self.visual.prepare_appearance.call_count, 2)

    def test_transparency_spin_slider_sync_and_option_changes_invalidate_without_preparing(self):
        def act(dialog):
            spin, slider = self.named("TransparencySpin"), self.named("TransparencySlider")
            self.assertEqual((spin.minimum, spin.maximum, spin.step, spin.value(), slider.value()), (0, 100, 5, 50, 50))
            self.assertFalse(self.named("Apply").enabled)
            self.generate()
            self.visual.prepare_appearance.assert_called_once_with(self.app, self.gui, "PublicFixture", self.names,
                                                                   "set_transparency", transparency=50)
            spin.setValue(75)
            self.assertEqual(slider.value(), 75)
            self.assertFalse(self.named("Apply").enabled)
            self.assertEqual(self.named("Preview").toPlainText(), "尚未生成修改预览。")
            self.assertEqual(self.named("Changes").toPlainText(), "尚未生成修改预览。")
            self.assertIn("旧预览失效", self.named("Status").text)
            slider.setValue(25)
            self.assertEqual(spin.value(), 25)
            self.apply()
            self.visual.apply_appearance.assert_not_called()
            self.assertEqual(self.visual.prepare_appearance.call_count, 1)
        self.widgets.on_appearance_exec = act
        self.assertIsNone(self.show("set_transparency"))

    def test_transparency_changed_value_requires_new_preview_before_single_apply(self):
        def act(dialog):
            self.generate()
            self.named("TransparencySlider").setValue(0)
            self.generate(); self.apply()
            self.assertIs(self.visual.apply_appearance.call_args.args[2], self.plans[-1])
        self.widgets.on_appearance_exec = act
        summary = self.show("set_transparency")
        self.assertEqual(summary["action"], "set_transparency")
        self.assertEqual(self.plans[-1]["transparency"], 0)
        self.assertEqual(self.visual.prepare_appearance.call_count, 2)
        self.assertEqual(self.visual.apply_appearance.call_count, 1)

    def test_prepare_error_is_displayed_without_partial_plan_or_apply(self):
        self.visual.prepare_appearance.side_effect = self.visual.PftVisualToolsError("DOCUMENT_BUSY", "当前文档正在编辑或已有未完成事务，请先完成或取消。")
        def act(dialog):
            self.generate(); self.apply()
            self.assertEqual(self.named("Status").text, "当前文档正在编辑或已有未完成事务，请先完成或取消。")
            self.assertFalse(self.named("Apply").enabled)
            self.assertEqual(self.named("Preview").toPlainText(), "尚未生成修改预览。")
        self.widgets.on_appearance_exec = act
        self.assertIsNone(self.show())
        self.visual.apply_appearance.assert_not_called()

    def test_expired_apply_invalidates_plan_and_never_reports_success_or_auto_retries(self):
        self.visual.apply_appearance.side_effect = self.visual.PftVisualToolsError("VISUAL_PLAN_INVALID", "外观预览已失效或变化，请重新预览。")
        def act(dialog):
            self.generate(); self.apply(); self.apply()
            self.assertEqual(self.named("Status").text, "外观预览已失效或变化，请重新预览。")
            self.assertFalse(self.named("Apply").enabled)
            self.assertTrue(self.named("Generate").enabled)
        self.widgets.on_appearance_exec = act
        self.assertIsNone(self.show())
        self.assertEqual(self.visual.apply_appearance.call_count, 1)
        self.assertEqual(self.visual.prepare_appearance.call_count, 1)

    def test_unknown_errors_do_not_expose_details_or_claim_application(self):
        self.visual.apply_appearance.side_effect = RuntimeError("private/path/credential detail")
        def act(dialog):
            self.generate(); self.apply()
            self.assertNotIn("private", self.named("Status").text)
            self.assertIn("外观应用失败", self.named("Status").text)
        self.widgets.on_appearance_exec = act
        self.assertIsNone(self.show())

    def test_unsupported_inspection_remains_complete_and_blocks_preview_controls(self):
        self.inspection["rows"][1].update(status="unsupported", reason="不支持的材质。")
        def act(dialog):
            self.assertEqual(json.loads(self.named("Inspection").toPlainText()), self.inspection)
            self.assertFalse(self.named("Generate").enabled)
            self.assertFalse(self.named("TransparencySpin").enabled)
            self.generate(); self.apply()
        self.widgets.on_appearance_exec = act
        self.assertIsNone(self.show("set_transparency"))
        self.visual.prepare_appearance.assert_not_called(); self.visual.apply_appearance.assert_not_called()

    def test_can_apply_false_keeps_complete_text_visible_and_rejects_forced_apply(self):
        self.base_plan["can_apply"] = False
        def act(dialog):
            self.generate()
            self.assertEqual(json.loads(self.named("Preview").toPlainText()),
                             {key: value for key, value in self.plans[0].items() if key not in ("token", "seed")})
            self.assertFalse(self.named("Apply").enabled)
            self.apply()
        self.widgets.on_appearance_exec = act
        self.assertIsNone(self.show())
        self.visual.apply_appearance.assert_not_called()

    def test_busy_generation_and_apply_ignore_reentrant_signals(self):
        prepare = self.visual.prepare_appearance.side_effect
        apply = self.visual.apply_appearance.side_effect
        def preparing(*args, **kwargs):
            self.assertFalse(self.named("Generate").enabled); self.assertFalse(self.named("Apply").enabled)
            self.generate(); self.apply()
            return prepare(*args, **kwargs)
        def applying(*args, **kwargs):
            self.assertFalse(self.named("Generate").enabled); self.assertFalse(self.named("Apply").enabled)
            self.apply(); self.generate()
            return apply(*args, **kwargs)
        self.visual.prepare_appearance.side_effect = preparing
        self.visual.apply_appearance.side_effect = applying
        self.widgets.on_appearance_exec = lambda dialog: (self.generate(), self.apply())
        self.assertIsInstance(self.show(), dict)
        self.assertEqual(self.visual.prepare_appearance.call_count, 1)
        self.assertEqual(self.visual.apply_appearance.call_count, 1)

    def test_non_dict_apply_result_is_unconfirmed_and_returns_none(self):
        self.visual.apply_appearance.side_effect = None; self.visual.apply_appearance.return_value = None
        def act(dialog):
            self.generate(); self.apply()
            self.assertIn("结果无法确认", self.named("Status").text)
            self.assertFalse(self.named("Apply").enabled)
        self.widgets.on_appearance_exec = act
        self.assertIsNone(self.show())

    def test_inspection_failure_uses_safe_message_and_disables_generation(self):
        self.visual.inspect_appearance.side_effect = RuntimeError("private trace")
        def act(dialog):
            self.assertFalse(self.named("Generate").enabled)
            self.assertEqual(self.named("Status").text, "外观检查失败；未修改 CAD。")
            self.generate(); self.apply()
        self.widgets.on_appearance_exec = act
        self.assertIsNone(self.show())
        self.visual.prepare_appearance.assert_not_called(); self.visual.apply_appearance.assert_not_called()

    def test_cancelled_and_successful_dialogs_are_released_once_on_exit(self):
        self.assertIsNone(self.show())
        self.assertEqual(self.widgets.deletions, 1)
        self.assertTrue(self.widgets.dialogs[-1].deleted)
        self.widgets.on_appearance_exec = lambda dialog: (self.generate(), self.apply())
        self.assertIsInstance(self.show(), dict)
        self.assertEqual(self.widgets.deletions, 2)
        self.assertTrue(all(dialog.deleted for dialog in self.widgets.dialogs))

    def test_dialog_is_released_even_when_modal_execution_raises(self):
        def fail(dialog): raise RuntimeError("synthetic execution failure")
        self.widgets.on_appearance_exec = fail
        with self.assertRaisesRegex(RuntimeError, "synthetic execution failure"):
            self.show()
        self.assertEqual(self.widgets.deletions, 1)
        self.assertTrue(self.widgets.dialogs[-1].deleted)
        self.visual.prepare_appearance.assert_not_called(); self.visual.apply_appearance.assert_not_called()

    def test_wrong_target_count_order_or_identity_is_rejected_before_display_and_apply(self):
        targets = copy.deepcopy(self.base_plan["targets"])
        changed = copy.deepcopy(targets); changed[1]["Name"] = "DifferentFixtureObject"
        for supplied in (targets[:1], list(reversed(targets)), changed):
            self.visual.prepare_appearance.side_effect = None
            self.visual.prepare_appearance.return_value = {**self.base_plan, "targets": supplied}
            def check(dialog):
                self.generate()
                self.assertFalse(self.named("Apply").enabled)
                self.assertEqual(self.named("Preview").toPlainText(), "尚未生成修改预览。")
                self.apply()
            self.widgets.on_appearance_exec = check
            self.assertIsNone(self.show())
        self.visual.apply_appearance.assert_not_called()

    def test_default_human_summary_and_changes_keep_json_collapsed_and_folding_is_inert(self):
        def act(dialog):
            self.assertEqual(dialog.requested_size, (760, 680))
            self.assertIn("全局透明度：0%", self.named("Summary").toPlainText())
            self.assertIn("全局与逐材质不一致：是", self.named("Summary").toPlainText())
            self.assertFalse(self.named("DataSection").visible)
            self.assertFalse(self.named("DataToggle").isChecked())
            self.assertEqual(self.named("DataToggle").style, 51)
            self.generate()
            self.assertIn("Public label PartTwo", self.named("Changes").toPlainText())
            self.assertIn("材质 256：透明度", self.named("Changes").toPlainText())
            before = {name: self.named(name).toPlainText() for name in ("Summary", "Changes", "Inspection", "Preview")}
            self.widgets.click("PhoenixTools_AppearanceDataToggle")
            self.assertTrue(self.named("DataSection").visible)
            self.assertEqual(self.named("DataToggle").arrow, 42)
            self.widgets.click("PhoenixTools_AppearanceDataToggle")
            self.assertFalse(self.named("DataSection").visible)
            self.assertEqual(self.named("DataToggle").arrow, 41)
            self.assertEqual(before, {name: self.named(name).toPlainText() for name in before})
            self.assertEqual(self.visual.inspect_appearance.call_count, 1)
            self.assertEqual(self.visual.prepare_appearance.call_count, 1)
            self.visual.apply_appearance.assert_not_called()
        self.widgets.on_appearance_exec = act
        self.assertIsNone(self.show())

    def test_current_transparency_formatter_lists_every_material_and_distinguishes_link_source(self):
        inspection = {"rows": [self.inspection["rows"][0], {
            "Name": "Instance", "Label": "Public instance", "kind": "link", "status": "supported",
            "override_material": False, "material_transparency_percent": [10.0],
            "reported_material_transparency_percent": [37.0, 80.0], "reported_mixed_material_transparency": True,
            "source": {"Name": "SourcePart", "global_transparency_percent": 37,
                "material_transparency_percent": [37.0, 80.0], "mixed_material_transparency": True,
                "global_material_mismatch": True}}]}
        before = copy.deepcopy(inspection)
        text = views.format_current_transparency(inspection)
        for expected in ("PartOne", "Instance", "材质 2 透明度：50%", "全局与逐材质不一致：是",
                         "Link 实例覆盖：否", "材质 1 透明度：10%", "报告材质 2 透明度：80%",
                         "来源对象：SourcePart", "来源材质 1 透明度：37%", "来源逐材质透明度不同：是"):
            self.assertIn(expected, text)
        self.assertEqual(inspection, before)
        self.visual.inspect_appearance.assert_not_called()

    def test_change_formatter_covers_all_materials_override_local_source_and_extra_changed_fields(self):
        plan = {"targets": [{"Name": "Instance", "Label": "Public instance",
            "before": {"kind": "link", "OverrideMaterial": False,
                "ShapeMaterial": {"Transparency": 0.0, "DiffuseColor": [1, 0, 0, 1], "Shininess": 2}},
            "after": {"kind": "link", "OverrideMaterial": True,
                "ShapeMaterial": {"Transparency": 0.37, "DiffuseColor": [0, 0, 1, 1], "Shininess": 10}},
            "effective_material_before": {"Transparency": 0.8}}]}
        before = copy.deepcopy(plan)
        text = views.format_appearance_changes(plan)
        for expected in ("Link 实例材质覆盖：否 → 是", "实例本地材质属性的修改：",
                         "材质 1：透明度 0% → 37%", "报告外观透明度：80% → 37%",
                         "颜色 RGBA：[1, 0, 0, 1] → [0, 0, 1, 1]", "Shininess：2 → 10",
                         "来源对象不在本次写入范围内。", "未列出的材质字段保持不变。"):
            self.assertIn(expected, text)
        self.assertEqual(plan, before)
        unknown = views.format_current_transparency({"rows": [{"Name": "Unknown", "kind": "shape", "status": "supported"}]})
        self.assertIn("全局透明度：未知", unknown)
        self.assertNotIn("全局透明度：0%", unknown)

    def test_grouped_qt_enums_are_supported_without_legacy_aliases(self):
        self.core.Qt = types.SimpleNamespace(TextFormat=types.SimpleNamespace(PlainText=0),
                                             Orientation=types.SimpleNamespace(Horizontal=7))
        def act(dialog): self.assertEqual(self.named("TransparencySlider").orientation, 7)
        self.widgets.on_appearance_exec = act
        self.assertIsNone(self.show("set_transparency"))


if __name__ == "__main__":
    unittest.main()
