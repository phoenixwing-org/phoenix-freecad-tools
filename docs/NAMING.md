# 内部命名统一

项目根目录已改为 `phoenix-freecad-tools`，作为整体工具集合；npm 包和 CLI 仍是 `phoenix-freecad-mcp`，作为 MCP 接入模块。工作台显示名采用 `Phoenix FreeCAD`，供后续承载聊天及其他 FreeCAD 工具；MCP 服务保留独立技术身份。

| 范围 | 旧值 | 当前值 |
| --- | --- | --- |
| 工作台显示名 | `Phoenix FreeCAD MCP` | `Phoenix FreeCAD` |
| 插件目录 | `addon/PhoenixMCP` | `addon/PhoenixFreeCADMCP` |
| 工作台类 | `PhoenixMCPWorkbench` | `PhoenixFreeCADMCPWorkbench` |
| 服务命令 | `PhoenixMCP_Start/Stop` | `PhoenixTools_StartMCP` / `PhoenixTools_StopMCP` |
| Python 模块 | `phoenix_bridge.py` | `phoenix_freecad_bridge.py` |
| 安装标记 addon 字段 | `PhoenixMCP` | `PhoenixFreeCADMCP` |

工作台业务工具与 MCP 接入可分别扩展；`phoenix-freecad-workbench` 更强调界面插件，本项目采用 `tools` 表达更宽泛的工具集合。

保持不变：npm 包和 CLI `phoenix-freecad-mcp`、Codex 服务条目、环境变量、安装标记文件名及 owner、HTTP 方法、协议版本和本机端口 9876。

安装器只为识别已有安装而保留旧目录常量。它会核对安装标记与完整文件指纹，把旧目录移到 Mod 以外备份后安装新目录；新旧目录同时存在、文件被修改或出现未知文件时拒绝迁移。不提供旧运行命令或模块的兼容别名。

历史 Git 标签和归档保持原样，不作为新安装的执行来源。当前 FreeCAD 内存中的旧工作台需要注销并加载新工作台；既有控制台历史不会被修改。无法卸载的旧命令会在当前会话禁用，正常重启后只加载新命令。

工作台仍只显示服务启动/停止。fit 和轴测作为 MCP 能力保留，通过 FreeCAD 原生 API 执行。

通用工作命令统一使用 `PhoenixTools_` 前缀，例如将来的 `PhoenixTools_CheckPartNames`。`PhoenixTools_Axonometric` 是可采用的命名示例，本版未注册该 GUI 命令；轴测仍通过 MCP 的 `set_axonometric_view` 调用。目录与工作台类属于插件技术身份，不要求和业务命令前缀相同。
