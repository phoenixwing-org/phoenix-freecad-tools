# SPDX-License-Identifier: Apache-2.0
"""Native global menu registration without changing the current workbench."""

import os

GROUP_ID = "PhoenixTools_GlobalMenu"
ENTRY_COMMANDS = (
    "PhoenixTools_ChatConnection",
    "PhoenixTools_DocumentTools",
    "PhoenixTools_Settings",
    "PhoenixTools_DeskTools",
    "PhoenixTools_Help",
)
QUICK_COMMANDS = (
    "PhoenixTools_DocumentTools", "PhoenixTools_AxonometricFit", "PhoenixTools_Appearance",
)
BOOTSTRAP_COMMANDS = ENTRY_COMMANDS + tuple(identifier for identifier in QUICK_COMMANDS if identifier not in ENTRY_COMMANDS)


_SESSION_ATTRIBUTE = "_phoenix_tools_global_menu_runtime"


class PftGlobalMenuError(RuntimeError):
    """A fixed failure for an unavailable or conflicting extension contract."""

    def __init__(self, message, code="PFT_CHAT_SHORTCUT_UNAVAILABLE"):
        super().__init__(message)
        self.message, self.code = message, code


class _PftGlobalMenuGroup:
    """FreeCAD dispatches the native group directly to its existing children."""

    def __init__(self, icon_path):
        self._icon_path = icon_path

    def GetCommands(self):
        return ENTRY_COMMANDS

    def GetResources(self):
        return {"MenuText": "Phoenix", "ToolTip": "Phoenix",
                "Pixmap": self._icon_path, "DropDownMenu": True,
                "CmdType": "NoTransaction"}

    def IsActive(self):
        return True

    # Deliberately no Activated: the native PythonGroupCommand invokes each
    # existing child command, including its NoTransaction execution contract.


class _PftGlobalMenuManipulator:
    def modifyMenuBar(self):
        # This is a command anchor, not a translated menu label. Do not ask
        # listMenus here: that method itself evaluates the manipulators.
        return [{"insert": GROUP_ID, "menuItem": "Std_Measure"}]


class PftGlobalMenuRuntime:
    """One explicit, strongly held native registration owner per GUI session.

    Creating a new runtime when GROUP_ID already exists is rejected. The host
    must retain and reuse this exact owner on reload. remove() changes future
    normal workbench menu rebuilds; it does not reload or switch a workbench.
    """

    def __init__(self, gui, workbench, source_root, icon_path):
        self._gui = gui
        self.workbench = workbench
        self._source_root = os.path.realpath(source_root)
        self._commands = workbench._registered_commands
        self._group = _PftGlobalMenuGroup(icon_path)
        self._manipulator = _PftGlobalMenuManipulator()
        self._group_registered = False
        self._installed = False
        self.workbench_registered = False
        self._primary_menu_binding = None

    def install(self):
        if self._installed:
            return False
        for method in ("addWorkbenchManipulator", "removeWorkbenchManipulator", "addCommand"):
            if not callable(getattr(self._gui, method, None)):
                raise PftGlobalMenuError("Phoenix global menu API is unavailable.")
        for identifier in ENTRY_COMMANDS:
            command = self._commands.get(identifier)
            if (command is None or self._gui.Command.get(identifier) is None
                    or command.GetResources().get("CmdType") != "NoTransaction"):
                raise PftGlobalMenuError("Phoenix entry commands are unavailable.")
        if not self._group_registered:
            if self._gui.Command.get(GROUP_ID) is not None:
                raise PftGlobalMenuError("Phoenix global menu registration conflicts.")
            self._gui.addCommand(GROUP_ID, self._group)
            self._group_registered = True
        self._gui.addWorkbenchManipulator(self._manipulator)
        self._installed = True
        return True

    def remove(self):
        if not self._installed:
            return False
        self._gui.removeWorkbenchManipulator(self._manipulator)
        self._installed = False
        return True

    def refresh_language(self):
        """Update only this native group's materialized child actions.

        Group children are QAction copies, not the ordinary leaf command's
        actions. FreeCAD exposes these through the group's getAction().
        Phoenix itself is invariant across the supported interface languages.
        """
        command = self._gui.Command.get(GROUP_ID)
        actions = command.getAction() if command is not None else ()
        if hasattr(actions, "setText"):
            actions = (actions,)
        updated = 0
        for action in actions or ():
            identifier = action.property("CommandName")
            if not isinstance(identifier, str):
                # QByteArray is the actual 1.1.4 property type; Qt6 also permits
                # the native Python bytes conversion. Do not match by label.
                try:
                    identifier = bytes(identifier).decode("ascii", errors="strict")
                except (TypeError, ValueError, UnicodeError):
                    continue
            if identifier not in ENTRY_COMMANDS:
                continue
            resources = self._commands[identifier].GetResources()
            action.setText(resources["MenuText"])
            action.setToolTip(resources["ToolTip"])
            action.setStatusTip(resources["ToolTip"])
            if callable(getattr(action, "setIconText", None)):
                action.setIconText(resources["MenuText"])
            updated += 1
        return updated

    def refresh_primary_menu(self, owned_actions, visible):
        """Retain only the current native menu validated by its exact actions.

        MenuManager can replace C++ menus on a workbench switch. Rediscover from
        the current menu bar each time; never call a previously retained wrapper
        or reinsert an action that the host has removed.
        """
        try:
            matches = []
            for action in self._gui.getMainWindow().menuBar().actions():
                menu = action.menu()
                if (menu is not None and menu.title().replace("&", "") in ("Phoenix", "Phoenix Tools")
                        and any(item in owned_actions for item in menu.actions())):
                    menu_action = menu.menuAction()
                    if menu_action is action or menu_action == action:
                        matches.append((menu, menu_action))
            if len(matches) != 1:
                self._primary_menu_binding = None
                return False
            # Keep both Python wrappers before changing native visibility. This
            # is a lifetime safeguard, not a replacement for the host menu tree.
            self._primary_menu_binding = matches[0]
            menu, action = self._primary_menu_binding
            menu.setTitle("Phoenix")
            action.setVisible(visible)
            return True
        except (AttributeError, RuntimeError):
            self._primary_menu_binding = None
            return False


def get_session_runtime(gui, source_root):
    """Reuse only our exact session owner; never clean up an unknown object."""
    runtime = getattr(gui, _SESSION_ATTRIBUTE, None)
    if runtime is not None and (
            not isinstance(runtime, PftGlobalMenuRuntime)
            or runtime._gui is not gui
            or runtime._source_root != os.path.realpath(source_root)):
        raise PftGlobalMenuError("Phoenix global menu belongs to another addon.")
    return runtime


def retain_session_runtime(gui, runtime):
    existing = get_session_runtime(gui, runtime._source_root)
    if existing is not None and existing is not runtime:
        raise PftGlobalMenuError("Phoenix global menu registration conflicts.")
    setattr(gui, _SESSION_ATTRIBUTE, runtime)
