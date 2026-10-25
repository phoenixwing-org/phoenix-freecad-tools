# 命名约定

版本说明：当前统一候选为0.3.2；本页0.4.0–0.10.0均为未发布内部迭代标签，保留当时代码、原生版本及归档事实，不能视为多个正式发行或本轮最终验收。详见[版本约定](VERSIONING.md)。

整体项目是 FreeCAD 工具集合。Addon 使用完整产品名称，内部 Python 模块/测试与自有类统一使用 pft/Pft 前缀；Node MCP 名称专指服务能力：

| 范围 | 当前值 |
| --- | --- |
| 项目目录 | `phoenix-freecad-tools` |
| 项目标题 | Phoenix FreeCAD Tools |
| FreeCAD 工作台显示 | **Phoenix Tools** |
| npm 包 / CLI / MCP 服务名 | `phoenix-freecad-mcp` |
| 示例客户端服务条目 | `phoenix-freecad` |
| 插件源码 / 安装目录 | `addon/PhoenixFreeCADTools` / `PhoenixFreeCADTools` |
| 工作台类 | `PftWorkbench` |
| Python 桥接模块 | `pft_bridge.py` |
| Python 模块 / 测试 | `pft_*.py` / `test_pft_*.py` |
| 自有公开类 | `Pft...`，例如 `PftWorkbench` / `PftBridgeError` |
| Node 安装器 owner / 备份根目录 | `phoenix-freecad-tools` / `.phoenix-freecad-tools` |
| 安装标记 | `.phoenix-freecad-tools-install.json` |
| 本机桥接 | 默认端口 `9876`，协议版本 `1` |

统一源码候选版本为`0.3.2`，桥接协议仍为 `1`，两者不混用。环境变量使用 `PHOENIX_FREECAD_URL` 和 `PHOENIX_TIMEOUT_MS`。Node npm/CLI 名称保持；本轮同步迁移 Addon 身份、安装标记/owner 和 HTTP 产品标识，协议版本保持 v1，见 [迁移矩阵](RENAME-ADDON.md)。

## GUI 命令

| 命令 ID | 显示与作用 |
| --- | --- |
| `PhoenixTools_MCPStatus` | 状态圆点，绿为运行、红为停止/停止中；点击只查看详情 |
| `PhoenixTools_ToggleMCP` | 灰色三角启动、灰色方块停止，停止始终确认；停止中不可用 |
| `PhoenixTools_NormalizeNames` | 命名修正 |
| `PhoenixTools_EditPartProperties` | 零件属性 |
| `PhoenixTools_SyncLinkLabels` | 链接同步 |
| `PhoenixTools_Settings` | 设置，灰色齿轮和菜单入口 |
| `PhoenixTools_DocumentTools` | 当前文档Dock |
| `PhoenixTools_AxonometricFit` | 轴测并适合视图 |
| `PhoenixTools_Appearance` | 非模态实时外观预览 |
| `PhoenixTools_DeskTools` | Desk连接 |
| `PhoenixTools_ChatConnection` | 独立原生AI聊天 |

普通工作台命令统一使用 `PhoenixTools_` 前缀。轴测+Fit组合入口为`PhoenixTools_AxonometricFit`，外观入口为`PhoenixTools_Appearance`；标准视图MCP合同保持。插件目录为 PhoenixFreeCADTools，工作台类为 PftWorkbench；用户显示名称保持 Phoenix Tools。

MCP 方法与参数是稳定机器标识；文档、对象和图纸参数使用 FreeCAD 内部 `Name`，显示 `Label` 是另一字段。现有普通命名工具只修改选中对象 Label，不重命名内部 Name、文件或目录。

正式 BOM 字段为 `PartNumber`、`PartVersion`、`TypeCode`、`ModelSeries`、`PartName`，属性组为 `CustomDefine`。业务示例使用虚构值；公开文档不记录实际零件、生产型号、个人路径或聊天 ID。

## 安装与升级

安装器仅在升级识别中保留两个历史目录名（PhoenixFreeCADMCP / PhoenixMCP），校验标记和完整文件指纹后备份到 Mod 目录外，再安装当前目录。冲突目录、未知文件、修改或符号链接触发保护，不提供旧运行命令或模块别名。历史标签和归档不改写为新版本。

安装器不会自动卸载任意其他插件。已加载模块不会因磁盘改名自动更新，升级须明确停止并重新加载或正常重启。

## 界面语言与业务标识

0.3.0 已实现中文 / English 本机设置，默认中文。偏好组为 `User parameter:BaseApp/Preferences/Mod/PhoenixTools`，字符串键 `Language` 使用 `zh` / `en`。确认后保存并刷新已有界面，不重启、不切 MCP、不修改文档；当前不提供跟随 FreeCAD 选项。

翻译使用中文原文作 catalog key，仅影响面向用户的界面；原命令、完整25/聊天19个MCP工具名、参数键、`Name`、正式字段及其用户值不翻译。动态业务校验和原生错误可能保留原文，完整覆盖见 [TODO](TODO.md)。

能力分组的机器键为 `general_actions`（选择/显隐/重算）和 `phoenix`（业务预览/应用）。业务操作使用稳定的 `normalize_names`、`sync_link_labels`、`initialize_properties`、`recommend_properties`、`set_properties`，不随界面语言变化。`token` 是短期预览标识，`requestId` 是高级脚本请求标识，两者合同不同，见 [协议](PROTOCOL.md)。

0.3.1 是安装分发补丁，沿用 0.3.0 的 21 个工具和协议 v1；历史原生验收不改写为新版本安装结果。

0.3.1 的前三个工具栏项为状态、启停、设置，均只显示图标；业务按钮仍保留原行为。自有布尔偏好仅为 `AutoStartMCP`（默认 false），语言键不变；手动停止固定确认，旧 `ConfirmStopMCP` 偏好保留但不再使用；自动启动只在后续 FreeCAD 启动时生效。

## 前缀与共享边界

FreeCAD 约定的 Init.py / InitGui.py 和稳定 Node CLI 入口保留；Python 自有模块以 pft_ 开头，测试以 test_pft_ 开头。TypeScript 的本产品适配类使用 Pft，例如 PftFreeCadClient；入口协议、GUI 命令和用户偏好是独立合同，不做无语义的字符串前缀替换。

Wing 公共 API 保持 Pnw / pnw / PNW_ 规范。本插件只命名自己的适配层；已存在的跨宿主 CAD 算法、契约和 UI 通过 Wing 公共接口复用，候选能力与已发布版本分别核实，详见 [Wing 复用边界](WING-REUSE.md)。

0.5.0 的可选入口为 PhoenixTools_DeskTools / PhoenixTools_ChatConnection；模块使用 pft_desk_* / pft_chat_views，控制器及进程适配类为 PftDeskController / PftDeskProcess。Node 服务名不变。

0.3.2当前业务按钮默认图标+文字，自有字符串偏好`ToolbarButtonStyle`为`icon_text`或`icon`；MCP三个控件固定仅图标。该读取API不修改Language。文档和聊天界面保留稳定objectName，聊天顶部三个紧凑图标复用相同工作台命令。
