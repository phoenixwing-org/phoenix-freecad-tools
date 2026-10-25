# SPDX-License-Identifier: Apache-2.0
"""FreeCAD workbench entry point; loading does not automatically start HTTP."""

import os
import sys

import FreeCAD as App
import FreeCADGui as Gui

try:
    _addon_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # Some FreeCAD loaders execute InitGui.py without assigning __file__.
    # Its compiled frame still records the addon file's actual path.
    import inspect
    _addon_dir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
if _addon_dir not in sys.path:
    sys.path.insert(0, _addon_dir)

import phoenix_freecad_bridge


def _feedback(message, error=False):
    printer = App.Console.PrintError if error else App.Console.PrintMessage
    printer("[Phoenix FreeCAD MCP] " + message + "\n")
    Gui.getMainWindow().statusBar().showMessage(message, 12000)


class _Command:
    def __init__(self, title, tooltip, callback):
        self.title = title
        self.tooltip = tooltip
        self.callback = callback

    def GetResources(self):
        return {"MenuText": self.title, "ToolTip": self.tooltip}

    def IsActive(self):
        return True

    def Activated(self):
        try:
            result = self.callback()
            if isinstance(result, dict) and "running" in result:
                if result["running"]:
                    _feedback("服务已启动：127.0.0.1:%s；可返回聊天检测连接。" % result["port"])
                else:
                    _feedback("服务已停止或正在停止。")
            else:
                _feedback("视图操作已完成。")
        except phoenix_freecad_bridge.BridgeError as error:
            _feedback(error.message, error=True)
        except Exception as error:
            _feedback("操作失败（%s）。" % type(error).__name__, error=True)


class PhoenixFreeCADMCPWorkbench(Workbench):
    Icon = os.path.join(_addon_dir, "Resources", "icons", "phoenix-wing-mark.svg")
    MenuText = "Phoenix FreeCAD"
    ToolTip = "Phoenix 的 FreeCAD 工具入口；通过 MCP 提供聊天控制能力。"

    def Initialize(self):
        definitions = [
            ("PhoenixTools_StartMCP", "启动 MCP 服务", "启动本机 9876 端口的 Phoenix 服务。", phoenix_freecad_bridge.start_server),
            ("PhoenixTools_StopMCP", "停止 MCP 服务", "停止 Phoenix 服务；不会关闭或保存模型。", phoenix_freecad_bridge.stop_server),
        ]
        commands = []
        for identifier, title, tooltip, callback in definitions:
            Gui.addCommand(identifier, _Command(title, tooltip, callback))
            commands.append(identifier)
        self.appendMenu("Phoenix FreeCAD", commands)
        self.appendToolbar("Phoenix FreeCAD", commands)

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(PhoenixFreeCADMCPWorkbench())
