# Phoenix FreeCAD Tools

![Phoenix](addon/PhoenixFreeCADTools/Resources/icons/phoenix-wing-mark.svg)

让现有聊天客户端通过 MCP 理解和操作 FreeCAD，并提供可独立使用的中英文工作台工具。当前统一源码候选 **0.3.2**（以0.3.1为归并基线，尚未正式发布），采用 Node.js / TypeScript 服务与轻量 Python 插件，应用代码使用 **Apache-2.0**。

FreeCAD 中的 **Phoenix Tools** 提供普通工具、当前文档面板、语言设置和桥接启停；Node 服务提供25个 MCP 工具，内嵌聊天使用受控19工具模式。原生 Codex 聊天通过官方 ChatGPT 登录，无需 Platform API Key；提供消息收发、中断和受控 MCP 工具接入。

[版本约定](docs/VERSIONING.md) · [设计诊断](docs/DESIGN-INSPECTION.md) · [当前文档工具](docs/CURRENT-DOCUMENT-TOOLS.md) · [原生聊天](docs/CHAT-USAGE.md) · [原生修改确认](docs/NATIVE-PART-APPROVAL.md) · [有限Pad编辑](docs/PAD-LENGTH-EDIT.md) · [安装包说明](docs/ADDON-INSTALL.md) · [命名迁移](docs/RENAME-ADDON.md) · [Wing 复用](docs/WING-REUSE.md) · [循环迭代计划](docs/NEXT-ITERATION.md) · [开发与发布计划](docs/RELEASE-PLAN.md) · [MCP 调研](docs/MCP-RESEARCH.md) · [Desk 集成计划](docs/DESK-INTEGRATION.md) · [接入说明](docs/CODEX.md) · [接口协议](docs/PROTOCOL.md) · [架构](docs/ARCHITECTURE.md) · [能力计划](docs/CAPABILITY-PLAN.md) · [验证记录](docs/VERIFICATION.md)

## 当前结果与边界

| 项目 | 当前状态 |
| --- | --- |
| MCP | 完整25工具、聊天19工具；四种读取提供来源、实例、部分事件修订和 expected 校验；原有业务预览/应用保持 |
| 设计诊断 | 提供 Body Tip、草图约束/自由度、有限特征参数和表达式的有界读取；不求解、不重算 |
| 参数化零件 | 有限首个Pad的Length编辑源码：完整预览、原生人工确认、自有事务、一次局部重算和几何反验；范围及原生待验见[合同](docs/PAD-LENGTH-EDIT.md)。另有独立人工板/Pad/Pocket示例 |
| STEP | 提供已知参数样例的导出、导入、参数化重建与几何对比脚本，不承诺恢复任意原始特征历史 |
| TechDraw | 提供四视图、关联尺寸和导出演示脚本；排版、重开、模板和尺寸须在独立文档中逐项验收 |
| 工作台 | MVC 分层、命名修正、正式属性填写、Link 名称同步；窗口内多选目标，独立状态圆点、灰色启停、齿轮设置和默认取消的停止确认 |
| 当前文档工具 | 读取未保存内存信息、缓存尺寸；默认五个BOM字段，齿轮可选择最多20个有界常规属性（含枚举/常用量值）；默认跟随活动文档，也可下拉固定查看；窄侧栏可收缩/滚动、对象范围可折叠；Markdown/PNG显式导出，缩略图恢复原相机；轴测与外观统一放在工具条 |
| 聊天界面 | 顶部文档工具/轴测Fit/外观图标及齿轮；配置、模型/字号、帮助和诊断收进设置；Label/BOM/Link修改的完整预览与原生确认源码已接入、原生端到端待验；官方model/list、显式选模及实际型号核对，字号8–32或继承FreeCAD；不自动重置已有会话 |
| 可选连接 | Desk 配置与异步状态/启动复用/Web入口；独立聊天Dock、受控MCP清单与合成验证，更多CAD操作继续分项验收 |

接口实现、自动化测试、独立人工示例和正式发行分别核验，方法及待验范围见[验证与验收](docs/VERIFICATION.md)。历史运行回执不随源码分发；已有示例不代表任意工程均已验证，也不代替图纸审签。现有桌面会话复用、更多 CAD 工具、Desk 下载部署和语言覆盖仍在后续计划中。

## 安装与启动

独立工作台只需要带 GUI 的 FreeCAD，使用其内置 Python。聊天 MCP 另需 Node.js 22+ 和 npm。当前为25个完整 MCP 工具、19个聊天工具和桥接协议 v1；优先适配 FreeCAD 1.1.0，所有宣称平台的原生安装和功能须分别验收。

普通用户可从已发布 Release 的附件下载 `phoenix-freecad-tools-addon-<版本>.zip`，按[安装包说明](docs/ADDON-INSTALL.md)解压到 Mod。正式 Release 尚待发布，GitHub 自动生成的整个仓库 Source ZIP 不等同于工作台安装包。源码安装方式如下：

以下为完整源码包的构建流程。预构建 Node tgz 用于运行服务，不含 lockfile/tsconfig；不要在其解包目录执行源码重建步骤。

```bash
cd /absolute/path/to/phoenix-freecad-tools
npm ci
npm run build
npm run addon:install -- --mod-dir "/absolute/path/to/FreeCAD/Mod" --dry-run
npm run addon:install -- --mod-dir "/absolute/path/to/FreeCAD/Mod"
```

先在 FreeCAD 中核实 Mod 目录。省略 `--mod-dir` 时，安装器只自动选取已存在的受支持 macOS 目录，其他情况提示候选位置。插件目录为 `PhoenixFreeCADTools`；安装器校验本项目标记和文件内容，保护已有修改，并保留 Mod 目录外的恢复副本。 默认包来源的受管理安装还包含 Addon 专用 LICENSE、NOTICE 和说明，三者纳入同一指纹保护；使用边界见[受管理安装说明](docs/ADDON-MANAGED-INSTALL.md)。

插件加载后，在工作台下拉框选择 **Phoenix Tools**。工具条前三个MCP控件固定仅显示图标；后续全部业务功能使用原创图标，默认图标加文字，可在设置中选“仅图标”，立即生效：

- **状态圆点**：绿表示本机桥接运行中，红表示已停止或正在停止；悬停或点击查看准确状态。
- **灰色三角 / 方块**：分别执行启动 / 停止。停止始终弹出确认，取消不启停；已经开始的操作不会因停止桥接自动取消。
- **灰色齿轮**：设置语言、工具条显示方式和下次 FreeCAD 启动时自动启动 MCP。自动启动默认关闭；手动停止固定需要确认，没有关闭确认的选项。

状态仅表示 FreeCAD 本机桥接，不证明 Node 或聊天客户端已连接。机器命令分别为 `PhoenixTools_MCPStatus`、`PhoenixTools_ToggleMCP`、`PhoenixTools_Settings`。这三个控件的菜单、悬停提示和状态详情保留文字；业务图标显示方式只作用于Phoenix工具条，不改变FreeCAD全局样式。配置入口也在Phoenix Tools菜单。

Node MCP 由聊天客户端启动，FreeCAD 按钮只管理本机 HTTP 桥接。选择工作台不自动启动；自动启动设置只在后续 FreeCAD 启动时生效，不会在手工停止后重新拉起。停止期间动作按钮不可用，等待完成后再启动。

当前 FreeCAD 有未保存修改时，可以在核实安装位置后，通过 Python 控制台启动桥接，无需为连接检查重启：

```python
import os, sys, FreeCAD as App
addon_dir = os.path.join(App.getUserAppDataDir(), "Mod", "PhoenixFreeCADTools")
if addon_dir not in sys.path:
    sys.path.insert(0, addon_dir)
import pft_bridge
pft_bridge.start_server()
```

如果安装到自定义 Mod 目录，应使用已核实的插件目录。该方式只启动桥接，不补注册尚未加载的工作台菜单。升级后，磁盘文件不会自动替换已加载模块；应先停止本服务并确认请求结束，再按当前会话情况重新加载，或在保存工作后正常重启。

## 视图、外观与文档输出

工具条的轴测Fit图标一次执行轴测并适合窗口；外观图标打开小型非模态窗口，对打开时明确选定的对象随机配色或调整0–100%透明度，实时显示变化。点击OK将预览合为一次可撤销操作；Cancel、Esc或关闭在事务仍归本窗口且未混入外部修改时恢复初始外观。发现外部操作或事务归属变化时停止处理并报告，不提交或取消其他修改；不自动重算或保存CAD。

当前文档面板默认“跟随活动文档”；下拉选择具体文档可固定查看，再选首项恢复跟随。缩略图目标在后台时提供“激活此文档”显式按钮，返回活动文档后恢复生成预览。已保存CAD的Markdown/PNG保存框默认其目录及同名stem；未保存文档仅建议文件名，由用户选择目录。仍需明确导出与覆盖确认。

## 聊天客户端接入

启动 FreeCAD 桥接后运行 `npm run check`，检查 `protocol_version:1`、`running:true`、插件版本和 `supported_methods`。离线工具清单不证明当前内存中的插件支持所有新增能力。

支持 stdio MCP 的客户端使用以下配置，并替换为本机绝对路径：

```json
{
  "mcpServers": {
    "phoenix-freecad": {
      "command": "/absolute/path/to/node",
      "args": ["/absolute/path/to/phoenix-freecad-tools/dist/cli.js"]
    }
  }
}
```

具体 Codex 命令见 [接入说明](docs/CODEX.md)。客户端需重新连接或重新加载 MCP；既有聊天工具清单尚未刷新时，可用真正走 MCP stdio 的 helper：

```bash
node scripts/call-tool.mjs get_connection_status /absolute/path/to/connection.args.json
```

示例参数文件内容为 `{}`。helper 不直接绕过 MCP 调内部 HTTP，也不会替当前聊天刷新原生工具清单。

内嵌聊天设置 `PHOENIX_FREECAD_TOOL_SCOPE=chat` 仅注册19个工具；默认完整模式保留高级脚本等25工具。

默认桥接地址为 `http://127.0.0.1:9876/`；`PHOENIX_FREECAD_URL` 可指定允许的本机 HTTP 根地址，`PHOENIX_TIMEOUT_MS` 可调整 Node 等待时间。程序不自动读取 `.env`。

## 工作台内选择处理对象

“命名修正”“链接同步”“零件属性”均可直接打开，在表格第一列勾选本次处理对象。优先采用当前活动文档的合适完整选择；树中没有任何选择时，预勾选合适的零件级节点。已有选择但全部不适用时留空，由用户在窗口内明确勾选，不扩大到其他对象。

内部特征可按需手动勾选，但不自动推荐；链接同步只列 Link 实例，零件属性排除 Link 并不向来源转发写入。勾选不改变树选择，清空后无法提交。命名和链接同步实时预览；属性填写仍经逐项差异确认。应用前继续复核目标与快照，事务可撤销，不自动保存。

## 25 个 MCP 工具

聊天模式提供其中19项；显式配置Desk后，完整模式为30项，聊天另行启用Desk数据后为24项。聊天的Label/BOM/Link修改须先展示完整差异，再由用户在FreeCAD原生窗口确认；不开放几何参数编辑或直接apply。原生端到端验收尚未完成，调用与状态规则见[原生确认链](docs/NATIVE-PART-APPROVAL.md)。

| 分组 | 工具 |
| --- | --- |
| 连接 | `get_capabilities`、`get_connection_status`、`list_documents` |
| 设计上下文 | `get_document_info`、`list_objects`、`get_object_info`、`get_selection` |
| 设计诊断 | `inspect_design`（Body Tip、草图约束/自由度、缓存参数与表达式） |
| 几何 | `measure_geometry` |
| 图纸检查 | `list_drawing_pages`、`inspect_drawing_page` |
| 相机 | `fit_view`、`set_axonometric_view`、`set_view` |
| 外观 `appearance` | `set_appearance`（随机色、0–100%透明度；可撤销） |
| 通用操作 `general_actions` | `set_selection`、`set_visibility`、`recompute_document` |
| Phoenix 业务 `phoenix` | `prepare_part_changes`、`request_part_change_approval`、`get_part_change_status`；完整模式另有`apply_part_changes` |
| 图像 | `capture_view` |
| 高级脚本 | `execute_script`、`get_script_status` |

文档、对象和页面参数使用内部 `Name`，不是显示 `Label`。常用读取不重算、不保存、不修改模型；测量只报告当前 Shape，不证明缓存最新、完整装配变换已展开或工程图检查通过。单面体积返回 `null` 且 `volume_applicable:false`；包围盒仅为内核包络，不能用来证明精确间隙。相机工具改变视角；`capture_view` 要求目标文档的三维视图处于前台，只在明确要求图像时导出，不截图桌面、不改变相机，其临时文件读取后清理。

`execute_script` 在 GUI 主线程执行，与 FreeCAD Python 控制台同权限，**不是沙箱**。脚本可修改模型、访问文件、自行保存或关闭文档，调用必须符合具体任务授权。框架不额外保存，也不承诺异常自动回滚。

脚本使用 `requestId` 去重和查询；超时不代表取消。先查询原 ID，重放使用相同 ID 和相同代码。记录仅限当前桥接服务会话，以 `script_session_id` 识别；重载桥接模块或重启 FreeCAD 会重置。完整终态结果保留最近 20 份，最多记住 4096 个历史 ID；结果过期的 ID 在同一会话中仍拒绝重新执行。状态和容量边界见 [协议](docs/PROTOCOL.md)。

### 通用操作与业务差异预览

三个通用操作均要求明确指定已经活动且空闲的文档。`set_selection` 只替换该文档的完整对象选择，空数组清除此文档选择，保留其他文档；`set_visibility` 对明确对象设显隐，事务可撤销；`recompute_document` 显式执行原生重算并检查失败状态，可能更新几何缓存。三者均不自动保存，也不自动切换目标文档。

Phoenix 业务通过 `prepare_part_changes` / `apply_part_changes` 复用工作台 Model/Controller。支持命名修正、Link Label 同步、属性初始化/推荐和正式字段填写；`set_pad_length` 复用预览入口，但必须经原生人工确认，完整模式也不能直接apply，范围见[有限Pad编辑](docs/PAD-LENGTH-EDIT.md)。先准备完整差异，例如对虚构对象预览命名：

```json
{"document":"ExampleDocument","objects":["ExamplePart"],"operation":"normalize_names"}
```

将返回的 `rows` 中旧值、新值、冲突及 `can_apply` 展示给用户，核对本次授权后才应用返回的 token。预览不修改模型，但会创建内存记录；有效期五分钟，最多保留 32 份。完整预览超过 128 KiB 会拒绝，不会截断差异后仍允许应用。

应用前重新验证文档实例、快照和来源，写入可撤销、不保存。旧五操作的直接apply成功 token 在仍保留且未过期时可返回 `replayed:true` 的历史摘要，**不会再次执行，也不会在用户撤销后自动重做**；该摘要不证明对象当前状态，应以 `get_object_info` 核对。超时、失败、过期或未知 token 不盲目重放或换 token 自动应用，先检查结果，再重新预览。操作示例和细则见 [接入说明](docs/CODEX.md) 与 [协议](docs/PROTOCOL.md)。

## 独立工作台工具

| 按钮 | 作用 |
| --- | --- |
| 命名修正 | 根据正式属性预览所选对象的 Label，检查冲突后确认修改；不改内部 Name 或文件名 |
| 零件属性 | 编辑正式 BOM 字符串字段，初始化缺失字段或从明确 Label 保守推荐；先列差异，再确认写入 |
| 链接同步 | 将所选 App::Link 实例 Label 同步到直接来源；不修改来源，不自动修复断链或猜测歧义 |

正式字段为 `PartNumber`、`PartVersion`、`TypeCode`、`ModelSeries`、`PartName`，属性组为 `CustomDefine`。推荐不覆盖已有非空值；属性填写不向 Link 来源转发写入。普通按钮经事务修改，默认不保存，不依赖 Node、MCP 或大型 Tool 服务。

实现按 Model、Controller、View、Facade 分工，详见 [架构](docs/ARCHITECTURE.md)。聊天通过两个业务 MCP 工具调用同一份规则和 Controller，普通按钮仍可独立使用。

### 中文 / English 设置

在 **Phoenix Tools** 菜单选择 **设置**，选择“简体中文”或“English”，点击“应用设置”。默认中文，确认后保存本机偏好，立即刷新已有菜单、按钮、提示和 MCP 状态文字，不重启、不启停 MCP、不改模型；新打开的业务对话框使用所选语言。取消不保存。

同一设置窗口的“工具条显示”提供“图标和文字”与“仅图标”，默认前者，保存后立即刷新；MCP状态、启停、设置三个控件仍固定仅图标。设置命令为 `PhoenixTools_Settings`，在菜单和灰色齿轮按钮中均可打开。机器命令、MCP 工具/参数及正式字段名保持不变。本轮未实现跟随 FreeCAD 语言；部分动态业务校验和原生错误仍可能显示原文，设置中已说明。

## 公开演示与验证

所有演示使用新建的公开人工几何，不包含用户工程、真实零件或机器路径。

| 文件 | 范围 |
| --- | --- |
| [parametric_plate.py](examples/parametric_plate.py) | 参数板、约束草图、厚度修改和几何校核示例 |
| [step_parametric.py](examples/step_parametric.py) | STEP 往返与已知参数重建示例，不恢复任意历史 |
| [workbench_tools_demo.py](examples/workbench_tools_demo.py) | 属性初始化与推荐、命名和 Link 同名示例；另验原生撤销重做及取消 |
| [techdraw_plate.py](examples/techdraw_plate.py) | 四视图、关联尺寸、导出与独立重开示例；布局需目视检查，不代替生产图纸审签 |

演示应按文件中的前置条件和任务授权执行，不能对其他工程盲目重放。已经建立演示文档与图纸后，可逐项核验结构化接口：

```bash
node scripts/verify-capabilities.mjs --image /absolute/path/to/new-view.png --report /absolute/path/to/new-report.json
```

该脚本只针对固定公开演示文档读取、测量和检查，再改变其相机并获取图像；不重算或保存模型。输出文件必须尚不存在，默认不向项目写入文件，报告移除机器路径、会话 ID、外部文档信息和 Base64。

## 开发、卸载与后续

```bash
npm run build
npm test
npm run test:python
npm run addon:pack
npm pack --dry-run
```

自动化覆盖协议、schema、安装保护、GUI 调度、MVC 和脚本记录等；真实模型、界面、图纸排版、重开及跨平台安装另行验收。最新测试数量、本地归档与包清单以 [验证记录](docs/VERIFICATION.md) 和 [隐私审查](docs/PRIVACY-REVIEW.md) 为准。

卸载本插件前先停止其服务，再执行：

```bash
npm run addon:uninstall -- --mod-dir "/absolute/path/to/FreeCAD/Mod"
```

安装器只管理带本项目标记且内容校验通过的安装，不覆盖未知文件或用户修改。ZIP 手工安装不带该标记，升级须先备份旧目录后完整替换。当前统一版本为0.3.2本地候选，正式Release和Addon Manager收录仍待完成。实际归档仅按包旁清单的提交和摘要识别，旧包、旧标签与历史回执不代表本次放行，见[版本约定](docs/VERSIONING.md)。

后续优先扩展真实设计和图纸案例，再封装高频建模/出图写入，并完善业务和语言覆盖，见 [TODO](docs/TODO.md)。内嵌聊天见 [路线图](docs/ROADMAP.md)，长期方向见 [愿景](docs/VISION.md)。大型 Tool 的下载、编译和启动后置，不是当前轻量插件的运行依赖。

源码许可见 [LICENSE](LICENSE)，依赖、移植规则和组织 Logo 来源见 [NOTICE](NOTICE) 与 [品牌资源](docs/BRANDING.md)。桥接仅绑定本机，拒绝浏览器 Origin/Sec-Fetch 请求，但没有本机进程身份认证或多用户隔离，仅用于可信本机环境。

目录、工作台、npm 包与 CLI 的对应关系见 [命名约定](docs/NAMING.md)。

### 读取一致性诊断

内部迭代0.4.0形成并纳入0.3.2的 get_document_info / list_objects / get_object_info 共同返回文档实例 token 和自观察器注册以来的部分事件计数。跨页可传 expectedDocumentInstanceToken 与 expectedRevision（修订条件须同时带 token）；同名重开、可观察事件变化或观察器不可用会明确拒绝旧条件。

计数覆盖不完整，不能证明几何最新，也不能代替业务写入预览的快照。Modified/Touched/活动 Body/编辑态分别报告来源与 unknown；Body 摘要最多50项，三类读取结果 UTF-8 上限128 KiB。旧插件普通读取保留，但来源标记 legacy_plugin/unknown，带 expected 条件时能力不足会拒绝。

## 可选 Desk 与原生聊天

Phoenix Tools 新增 **Desk Tools** 与 **聊天接入**。Desk 面板可配置已有 Node/已构建可信 CLI/私有状态目录，独立保存、异步检查、明确启动或复用后台，并在重新检查后用系统浏览器打开 Web；不下载/编译，不启动 Tauri，不提供后台停止按钮。普通 CAD 工具和 MCP 不依赖 Desk。具体步骤见 [Desk 接入操作](docs/DESK-USAGE.md)。

可选Desk业务只读MCP已形成源码候选：明确配置三个Desk路径后，完整模式增加连接、索引/引用及既有缩略图/尺寸缓存五个工具、共30项；默认25/聊天19保持，内嵌聊天明确启用Desk数据后为24项。连接工具可显式设`includeRuntimeFeatures=true`核对已声明能力；默认不多读，门禁idle仍须预检，不表示可生成。缓存另需运行态cad.cache.read v1，旧构建不支持时明确拒绝。只消费正式CLI/领域API，不复制SQL、不读最近空间、不自动启动/生成或修改CAD；全图册与修复写入仍待。配置、限额、数量语义和后续计划见[能力共享](docs/DESK-MCP-CONTRACT.md)。

原生聊天窗口通过官方 Codex app-server 提供独立会话，可使用 ChatGPT 浏览器登录并连接受控19工具 MCP。使用步骤和权限边界见[聊天操作说明](docs/CHAT-USAGE.md)。现有桌面会话复用仍待调查，见[聊天调查](docs/EMBEDDED-CHAT-RESEARCH.md)。代码约定见 [开发规范](docs/CODING.md)。

内部迭代0.5.1形成并纳入0.3.2的 Desk 连接面板提供 **复制配置提示词**。无需先填完字段：空值会要求 AI 查找，状态目录给出 FreeCAD 专用目录建议；已填值仅作为待核实的路径数据。用户粘贴给有本机/MCP 权限的 AI 后，按校验、保存、检查、必要时启动及打开 Web 的步骤执行。复制本身不保存、不启动服务。
