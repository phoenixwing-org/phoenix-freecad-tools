# SPDX-License-Identifier: Apache-2.0
import importlib.util
import gc
import pathlib
import types
import unittest
import weakref

ROOT = pathlib.Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
spec = importlib.util.spec_from_file_location("pft_global_menu_test", ROOT / "pft_global_menu.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Leaf:
    def __init__(self, title):
        self.title = title

    def GetResources(self):
        return {"MenuText": self.title, "ToolTip": self.title + " tip", "CmdType": "NoTransaction"}


class Action:
    def __init__(self, identifier):
        self.identifier = identifier
        self.values = {}

    def property(self, name):
        return self.identifier if name == "CommandName" else None

    def setText(self, value): self.values["text"] = value
    def setToolTip(self, value): self.values["tooltip"] = value
    def setStatusTip(self, value): self.values["status"] = value
    def setIconText(self, value): self.values["icontext"] = value


class Gui:
    def __init__(self):
        self.commands = {}
        self.actions = {}
        self.calls = []
        self.Command = types.SimpleNamespace(get=self.get)

    def get(self, identifier):
        if identifier not in self.commands:
            return None
        return types.SimpleNamespace(getAction=lambda: self.actions.get(identifier, ()))

    def addCommand(self, identifier, command):
        self.calls.append(("command", identifier, command))
        self.commands[identifier] = command

    def addWorkbenchManipulator(self, value):
        self.calls.append(("add", value))

    def removeWorkbenchManipulator(self, value):
        self.calls.append(("remove", value))


class GlobalMenuTests(unittest.TestCase):
    def setUp(self):
        self.gui = Gui()
        self.commands = {identifier: Leaf(identifier) for identifier in module.ENTRY_COMMANDS}
        self.gui.commands.update(self.commands)
        self.workbench = types.SimpleNamespace(_registered_commands=self.commands)
        self.runtime = module.PftGlobalMenuRuntime(self.gui, self.workbench, ROOT, "neutral-icon.svg")

    def test_native_descriptor_is_fixed_and_has_no_after_key(self):
        self.assertEqual(self.runtime._manipulator.modifyMenuBar(),
                         [{"insert": module.GROUP_ID, "menuItem": "Std_Measure"}])

    def test_group_delegates_without_python_activated(self):
        self.assertEqual(self.runtime._group.GetCommands(), module.ENTRY_COMMANDS)
        self.assertFalse(hasattr(self.runtime._group, "Activated"))
        self.assertTrue(self.runtime._group.GetResources()["DropDownMenu"])

    def test_install_is_idempotent_and_carries_exact_owner(self):
        self.assertTrue(self.runtime.install())
        self.assertFalse(self.runtime.install())
        self.assertEqual([call[0] for call in self.gui.calls], ["command", "add"])
        self.assertIs(self.gui.calls[1][1], self.runtime._manipulator)

    def test_remove_reinstall_reuses_group_and_exact_manipulator(self):
        self.runtime.install()
        self.assertTrue(self.runtime.remove())
        self.assertFalse(self.runtime.remove())
        self.assertTrue(self.runtime.install())
        self.assertEqual([call[0] for call in self.gui.calls], ["command", "add", "remove", "add"])
        self.assertIs(self.gui.calls[2][1], self.gui.calls[3][1])

    def test_missing_api_fails_before_any_registration(self):
        self.gui.addWorkbenchManipulator = None
        with self.assertRaises(module.PftGlobalMenuError): self.runtime.install()
        self.assertEqual(self.gui.calls, [])

    def test_missing_or_transactional_leaf_fails_closed(self):
        del self.commands[module.ENTRY_COMMANDS[0]]
        with self.assertRaises(module.PftGlobalMenuError): self.runtime.install()
        self.assertEqual(self.gui.calls, [])

    def test_unowned_existing_group_is_not_replaced(self):
        self.gui.commands[module.GROUP_ID] = object()
        with self.assertRaises(module.PftGlobalMenuError): self.runtime.install()
        self.assertEqual(self.gui.calls, [])

    def test_refresh_targets_only_own_group_action_command_ids(self):
        self.runtime.install()
        own = [Action(identifier.encode("ascii")) for identifier in module.ENTRY_COMMANDS]
        foreign = Action(b"Other_Command")
        self.gui.actions[module.GROUP_ID] = own + [foreign]
        self.commands[module.ENTRY_COMMANDS[0]].title = "Neutral translated label"
        self.assertEqual(self.runtime.refresh_language(), 5)
        self.assertEqual(own[0].values["text"], "Neutral translated label")
        self.assertEqual(foreign.values, {})

    def test_fresh_runtime_cannot_duplicate_previous_registration(self):
        self.runtime.install()
        other = module.PftGlobalMenuRuntime(self.gui, self.workbench, ROOT, "neutral-icon.svg")
        with self.assertRaises(module.PftGlobalMenuError): other.install()
        self.assertEqual(len(self.gui.calls), 2)

    def test_session_reuses_exact_owner_but_rejects_an_unknown_or_other_source_owner(self):
        self.assertIsNone(module.get_session_runtime(self.gui, ROOT))
        module.retain_session_runtime(self.gui, self.runtime)
        self.assertIs(module.get_session_runtime(self.gui, ROOT), self.runtime)
        with self.assertRaises(module.PftGlobalMenuError):
            module.get_session_runtime(self.gui, ROOT / "other")
        setattr(self.gui, module._SESSION_ATTRIBUTE, object())
        with self.assertRaises(module.PftGlobalMenuError):
            module.get_session_runtime(self.gui, ROOT)

    def test_missing_or_unmaterialized_group_actions_are_safe(self):
        self.assertEqual(self.runtime.refresh_language(), 0)
        self.runtime.install()
        self.assertEqual(self.runtime.refresh_language(), 0)

    def primary_fixture(self):
        owned = object()
        class Menu:
            def __init__(self): self.value, self.calls = "Phoenix", []
            def title(self): self.calls.append("title"); return self.value
            def actions(self): self.calls.append("actions"); return [owned]
            def menuAction(self): return self.action_ref()
            def setTitle(self, value): self.calls.append("setTitle"); self.value = value
        class MenuAction:
            def __init__(self, menu): self.menu_ref, self.visible, self.valid = weakref.ref(menu), True, True
            def menu(self):
                if not self.valid: raise RuntimeError("C++ object has been deleted")
                return self.menu_ref()
            def setVisible(self, value): self.visible = value
        menu = Menu()
        action = MenuAction(menu)
        menu.action_ref = weakref.ref(action)
        bar = types.SimpleNamespace(refs=[weakref.ref(action)])
        bar.actions = lambda: [value for ref in bar.refs if (value := ref()) is not None]
        self.gui.getMainWindow = lambda: types.SimpleNamespace(menuBar=lambda: bar)
        return owned, menu, action, bar

    def test_current_validated_primary_wrappers_survive_gc_and_release_after_host_replacement(self):
        owned, menu, action, bar = self.primary_fixture()
        menu_ref, action_ref = weakref.ref(menu), weakref.ref(action)
        self.assertTrue(self.runtime.refresh_primary_menu([owned], False))
        del menu, action
        gc.collect()
        self.assertIsNotNone(menu_ref())
        self.assertFalse(action_ref().visible)
        self.assertTrue(self.runtime.refresh_primary_menu([owned], True))
        self.assertTrue(action_ref().visible)
        bar.refs = []
        self.assertFalse(self.runtime.refresh_primary_menu([owned], False))
        gc.collect()
        self.assertIsNone(menu_ref())
        self.assertIsNone(action_ref())

    def test_deleted_current_wrapper_is_not_reinserted_or_used_through_cached_binding(self):
        owned, menu, action, _ = self.primary_fixture()
        self.runtime.refresh_primary_menu([owned], False)
        calls = list(menu.calls)
        action.valid = False
        self.assertFalse(self.runtime.refresh_primary_menu([owned], True))
        self.assertIsNone(self.runtime._primary_menu_binding)
        self.assertEqual(menu.calls, calls)


if __name__ == "__main__":
    unittest.main()
