# 命名约定

整体项目是 FreeCAD 工具集合，界面名称与 MCP 技术身份分别保留：

| 范围 | 当前值 |
| --- | --- |
| 项目目录 | `phoenix-freecad-tools` |
| 项目标题 | Phoenix FreeCAD Tools |
| FreeCAD 工作台显示 | **Phoenix Tools** |
| npm 包 / CLI / MCP 服务名 | `phoenix-freecad-mcp` |
| 示例客户端服务条目 | `phoenix-freecad` |
| 插件源码 / 安装目录 | `addon/PhoenixFreeCADMCP` / `PhoenixFreeCADMCP` |
| 工作台类 | `PhoenixFreeCADMCPWorkbench` |
| Python 桥接模块 | `phoenix_freecad_bridge.py` |
| 安装标记 | `.phoenix-freecad-mcp-install.json` |
| 本机桥接 | 默认端口 `9876`，协议版本 `1` |

实现版本为 `0.3.0`，桥接协议仍为 `1`，两者不混用。环境变量使用 `PHOENIX_FREECAD_URL` 和 `PHOENIX_TIMEOUT_MS`。项目目录改名不要求同步修改稳定的 npm、CLI、安装标记 owner 或 HTTP 合同。

## GUI 命令

| 命令 ID | 显示与作用 |
| --- | --- |
| `PhoenixTools_ToggleMCP` | 唯一 MCP 启停命令；绿色三角“启动 MCP · 已停止”、红色方块“停止 MCP · 运行中”，停止中显示“MCP 正在停止” |
| `PhoenixTools_NormalizeNames` | 命名修正 |
| `PhoenixTools_EditPartProperties` | 填写零件属性 |
| `PhoenixTools_SyncLinkLabels` | 同步链接名称 |
| `PhoenixTools_Settings` | 设置 / Settings，仅增加菜单入口，不增加工具栏按钮 |

普通工作台命令统一使用 `PhoenixTools_` 前缀。fit、轴测和标准视图保留为 MCP 能力，不重复添加演示按钮。内部类与目录沿用插件技术身份，不要求与用户显示名称相同。

MCP 方法与参数是稳定机器标识；文档、对象和图纸参数使用 FreeCAD 内部 `Name`，显示 `Label` 是另一字段。现有普通命名工具只修改选中对象 Label，不重命名内部 Name、文件或目录。

正式 BOM 字段为 `PartNumber`、`PartVersion`、`TypeCode`、`ModelSeries`、`PartName`，属性组为 `CustomDefine`。业务示例使用虚构值；公开文档不记录实际零件、生产型号、个人路径或聊天 ID。

## 安装与升级

安装器保留一次旧目录迁移识别，校验标记和完整文件指纹后备份到 Mod 目录外，再安装当前目录。冲突目录、未知文件、修改或符号链接触发保护，不提供旧运行命令或模块别名。历史标签和归档不改写为新版本。

当前本机原桥接已停用、备份并移出加载目录，对应旧客户端条目已移除；这不意味着新安装器会自动卸载任意其他插件。已加载模块也不会因磁盘改名自动更新，升级须明确停止并重新加载或正常重启。

## 界面语言与业务标识

0.3.0 已实现中文 / English 本机设置，默认中文。偏好组为 `User parameter:BaseApp/Preferences/Mod/PhoenixTools`，字符串键 `Language` 使用 `zh` / `en`。确认后保存并刷新已有界面，不重启、不切 MCP、不修改文档；当前不提供跟随 FreeCAD 选项。

翻译使用中文原文作 catalog key，仅影响面向用户的界面；原命令、21 个 MCP 工具名、参数键、`Name`、正式字段及其用户值不翻译。动态业务校验和原生错误可能保留原文，完整覆盖见 [TODO](TODO.md)。

能力分组的机器键为 `general_actions`（选择/显隐/重算）和 `phoenix`（业务预览/应用）。业务操作使用稳定的 `normalize_names`、`sync_link_labels`、`initialize_properties`、`recommend_properties`、`set_properties`，不随界面语言变化。`token` 是短期预览标识，`requestId` 是高级脚本请求标识，两者合同不同，见 [协议](PROTOCOL.md)。
