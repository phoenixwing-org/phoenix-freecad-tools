# Codex 接入与本机调用（0.3.2候选）

版本说明：当前统一候选为0.3.2；本页0.4.0–0.10.0均为未发布内部迭代标签，保留当时代码、原生版本及归档事实，不能视为多个正式发行或本轮最终验收。详见[版本约定](VERSIONING.md)。

本服务将当前 FreeCAD 会话作为工具提供给 Codex。聊天和模型选择仍由客户端管理，FreeCAD 插件不需要填写聊天 API Key。项目目录为 `phoenix-freecad-tools`，npm 包和启动命令仍为 `phoenix-freecad-mcp`，示例 Codex 服务 ID 为 `phoenix-freecad`。

## 安装、启动与注册

在项目目录执行，Node.js 需为 22 或更高版本：

```bash
cd /absolute/path/to/phoenix-freecad-tools
npm ci
npm run build
npm run addon:install -- --mod-dir /absolute/path/to/FreeCAD/Mod
```

`--mod-dir` 使用在 FreeCAD 中核实的 Mod 目录；可先加 `--dry-run` 查看安装计划。安装器默认仅自动选取已存在的受支持 macOS 目录，其他情况会提示候选目录，不盲选版本。已有不同或已改动的目录会受到保护。

插件加载后，在 FreeCAD 工作台下拉框选择 **Phoenix Tools**，点击“启动 MCP”。这是同一个启停按钮，命令 ID 为 `PhoenixTools_ToggleMCP`；服务运行后显示“停止 MCP”。选择工作台本身不会自动启动服务。默认每次重新启动 FreeCAD 后需手动启动桥接；设置可明确开启下次进程自动启动。插件更新后的加载方式见 [README](../README.md)，不要为了刷新聊天工具列表而直接关闭未保存文档。

使用本机 Node 和项目的绝对路径注册 stdio 服务：

```bash
codex mcp add phoenix-freecad -- /absolute/path/to/node /absolute/path/to/phoenix-freecad-tools/dist/cli.js
codex mcp get phoenix-freecad --json
codex mcp list
```

命令格式已按 [OpenAI 官方 Codex MCP 命令说明](https://learn.chatgpt.com/docs/developer-commands#codex-mcp) 和本机 `codex mcp add --help` 核对。启动入口直接使用 Node 与 `dist/cli.js`，MCP stdio 的 stdout 用于协议消息。

注册成功不等于既有聊天已刷新工具清单。让客户端重新连接或重新加载 MCP 后，先调用 `get_connection_status`，确认 `running`、协议 `1`、插件实际加载版本（新候选为0.3.2，勿用磁盘版本代替内存读回）、实际 `supported_methods` 与 `script_session_id`。`get_capabilities` 是离线清单，不证明插件已连接或支持新功能。模型 ID 不写在 MCP 配置中。

## 可选 Desk 数据工具

服务启动环境同时提供`PHOENIX_DESK_NODE`、`PHOENIX_DESK_CLI`、`PHOENIX_DESK_STATE_DIR`三个经核实的规范绝对路径后，full模式额外注册`desk_get_connection_status`、`desk_search_index`、`desk_query_references`、`desk_query_cached_metadata`、`desk_get_cached_thumbnail`，合计30项。未配置仍25项，chat默认19项；chat另行明确设置`PHOENIX_DESK_CHAT_READS=1`并提供完整三路径时增加五个只读工具为24项，仍排除完整权限入口。路径属于用户的本机执行配置，不填写到公开示例或Git；不会自动复制FreeCAD面板设置、读取最近工作空间或启动后台。

现有`scripts/call-tool.mjs`会把这三个显式环境变量传给自己的stdio服务，离线get_capabilities可检查注册结果。先调用Desk连接检查；需要知道当前声明能力时，显式传`{"includeRuntimeFeatures":true}`，区分连接ready、缓存支持和Native仍需预检。默认不多读，404仅报告未声明，未知版本或身份变化报错；不读取当前/最近空间。再以明确workspace及查询/相对路径请求已有索引。缓存工具还要求运行态cad.cache.read v1；先取元数据，再用对应thumbnail的精确revision按需取图；缺失/过期不生成，旧构建不支持则拒绝。查询与当前FreeCAD内存分开；完整合同与后续计划见[Desk能力共享](DESK-MCP-CONTRACT.md)。客户端仍需重新连接才能发现新增工具，不要因此重启含未保存文档的FreeCAD。

## 既有聊天未刷新时：真正的 stdio 调用

项目提供 `scripts/call-tool.mjs`。它使用官方 MCP 客户端启动 `dist/cli.js`，完成 stdio 握手并调用工具，再由 Node 连接 FreeCAD；因此可在当前聊天尚未出现原生工具时继续核验。它不直接绕过 MCP 访问内部 HTTP 接口。需先完成包含开发依赖的 `npm ci` 和构建。

用法为：

```bash
node /absolute/path/to/phoenix-freecad-tools/scripts/call-tool.mjs <工具名> <参数JSON文件> [新建PNG输出文件]
```

例如，将以下内容保存为 `/absolute/path/to/connection.args.json`：

```json
{}
```

调用连接检查：

```bash
node /absolute/path/to/phoenix-freecad-tools/scripts/call-tool.mjs get_connection_status /absolute/path/to/connection.args.json
```

同一个空参数文件也可用于 `get_capabilities` 或 `list_documents`。工具调用失败时脚本以非零状态退出；成功时输出工具结果。调用 helper 不会替当前聊天刷新原生 MCP 工具清单。

## 读取与视图示例

当前0.3.2完整模式25工具、聊天模式19工具，协议v1：

- 连接：`get_capabilities`、`get_connection_status`、`list_documents`。
- 读取：`get_document_info`、`list_objects`、`get_object_info`、`get_selection`、`measure_geometry`、`list_drawing_pages`、`inspect_drawing_page`。
- 设计诊断：`inspect_design`。
- 相机：`fit_view`、`set_axonometric_view`、`set_view`。
- 外观：`set_appearance`。
- 通用操作 `general_actions`：`set_selection`、`set_visibility`、`recompute_document`。
- Phoenix 业务 `phoenix`：`prepare_part_changes`、`apply_part_changes`。
- 图像：`capture_view`。
- 高级脚本：`execute_script`、`get_script_status`。

先列出实际文档和对象，再使用其内部 Name。以下名称均为虚构示例，需替换为当前会话实际名称。把参数保存为 JSON 文件，再将工具名和文件路径传给 helper；也可由已连接的客户端原生调用。

```json
{"document":"ExampleDocument","offset":0,"limit":50,"typeId":"PartDesign::Body"}
```

以上对应 `list_objects`。对象属性读取 `get_object_info` 示例：

```json
{"document":"ExampleDocument","object":"ExampleBody","properties":["Label","Tip"]}
```

切换标准视图 `set_view` 示例：

```json
{"document":"ExampleDocument","view":"axonometric","fit":true}
```

可在聊天中说：“通过 phoenix-freecad 检查连接，列出打开文档，再读取指定对象的属性。”或“把 ExampleDocument 切换为轴测图并适合窗口。”读取工具不修改、不重算、不保存模型；视图工具只改变相机。几何测量和 TechDraw 检查报告当前状态，不证明模型已更新、装配变换已展开或尺寸引用全部有效。参数、错误和边界详见 [协议说明](PROTOCOL.md)。

## 跨页读取时核对文档实例

先调用 `get_document_info`，保存本次返回的 `document_instance_token` 和非空 `revision`。后续 `list_objects` 或 `get_object_info` 可传入这两个实际值：

```json
{"document":"ExampleDocument","offset":50,"limit":50,"expectedDocumentInstanceToken":"REPLACE_WITH_ACTUAL_INSTANCE_TOKEN","expectedRevision":0}
```

以上 token 和修订只是占位示意，须替换为刚才实际返回值。`expectedRevision` 必须配合 token。旧实例、修订变化或无法观察会明确报错；重新读取并判断变化后再继续，避免混用分页。普通读取也会返回来源和部分修订状态，未知不能当作 0。

计数从观察器注册开始，只覆盖部分原生事件，`write_guard:false`；它不是完整几何或文件修订。读取不会自动重算或保存，`geometry_freshness:not_verified` 说明 Shape 可能尚未更新。旧插件无此能力时带 expected 条件会在派发前拒绝，普通读取仅给 unknown。完整字段和错误见 [协议](PROTOCOL.md#040-文档实例与部分事件诊断)。

## 明确目标的通用操作

先在 FreeCAD 中激活目标文档并结束草图编辑或未完成事务。这些工具要求显式 `document`，不依赖省略文档来猜目标，也不自动切换活动文档。以下均为虚构对象，先读取实际内部 Name。

`set_selection` 参数示例；只替换此文档的完整对象选择，其他文档选择保留：

```json
{"document":"ExampleDocument","objects":["ExamplePart"]}
```

将 `objects` 改为 `[]` 可清除此文档选择。`set_visibility` 示例，显隐修改有文档事务，可撤销且不保存：

```json
{"document":"ExampleDocument","objects":["ExamplePart"],"visible":false}
```

需要用户明确要求重算时，`recompute_document` 使用：

```json
{"document":"ExampleDocument"}
```

重算会执行原生特征求解并检查失败状态，可能更新缓存几何；它不是普通只读检查，失败或超时不证明未执行，也不自动保存或重试。可用 helper 按工具名和对应参数文件调用这些接口。

## 业务操作：先看差异，再原生确认

预览、请求确认、查询状态和完整模式的直接apply共四个MCP工具，复用普通工作台的Model/Controller。预览要求目标已经是空闲的活动文档；它不改变选择或模型，但会创建短期内存 token。使用步骤如下：

1. 用 `get_object_info` 查看实际对象和正式属性，明确本次要改什么。
2. 调用 `prepare_part_changes`，把完整旧值、新值、冲突和 `can_apply` 展示给用户。
3. 有blocked行、`can_apply:false`或没有变更时先处理原因；可应用时以原token调用`request_part_change_approval`，打开或复用FreeCAD原生窗口。
4. 用户在窗口核对并点击“确认修改”后，以原token调用`get_part_change_status`，再读取对象核对结果。等待确认或正在应用均不是完成；不要自动轮询、提交确认标志或替换token重复操作。

此链覆盖Label命名、直接Link实例名称同步、五个BOM字符串字段及[有限Pad Length](PAD-LENGTH-EDIT.md)；Pad必须原生人工确认，不能使用直接apply；当前为源码能力，原生聊天、人工确认及实际撤销重做的端到端验收尚未完成。完整状态和边界见[原生确认链](NATIVE-PART-APPROVAL.md)。

例如，保存以下参数为 `/absolute/path/to/prepare.args.json`。所有名称和值均为虚构示例；仅 `set_properties` 需要且允许 `values`，对象键须与 `objects` 精确对应，本批所有对象采用相同字段集。

```json
{
  "document": "ExampleDocument",
  "objects": ["ExamplePart"],
  "operation": "set_properties",
  "values": {
    "ExamplePart": {
      "PartNumber": "EXAMPLE",
      "PartVersion": "001",
      "PartName": "演示板"
    }
  }
}
```

```bash
node /absolute/path/to/phoenix-freecad-tools/scripts/call-tool.mjs prepare_part_changes /absolute/path/to/prepare.args.json
```

正式字段另包括 `TypeCode`、`ModelSeries`；每个值为不超过 2048 字符的单行文本，不接受路径或模板源码。其他操作不传 `values`，例如：

```json
{"document":"ExampleDocument","objects":["ExamplePart"],"operation":"normalize_names"}
```

其余操作为 `sync_link_labels`（仅 Link 实例 Label）、`initialize_properties`（缺失字段初始化为空）、`recommend_properties`（明确 Label 推荐，只填空值）。预览会检查来源、正式字段和冲突，不改变文件名或内部 Name，不向 Link 来源转发填写属性。

预览返回 `rows`、`changed_count`、`can_apply`、`token` 和过期时间。完整预览超过 128 KiB 会拒绝，请减少对象/字段；不能截断差异后继续应用。核对后将实际token填入 `/absolute/path/to/approval.args.json`：

```json
{"token":"REPLACE_WITH_TOKEN_FROM_PREVIEW"}
```

以上 token 是占位文本，不能直接使用。调用：

```bash
node /absolute/path/to/phoenix-freecad-tools/scripts/call-tool.mjs request_part_change_approval /absolute/path/to/approval.args.json
```

请求返回`awaiting_confirmation`时尚未写入。用户在FreeCAD窗口确认或取消后，再显式查询同一token：

```bash
node /absolute/path/to/phoenix-freecad-tools/scripts/call-tool.mjs get_part_change_status /absolute/path/to/approval.args.json
```

确认回调重新核对文档实例、快照及来源，再以事务写入，可撤销但不保存。`applied`才有实际结果；`native_confirmation`单独表示是否经过原生确认。可确认期自预览起五分钟，容量32；终结状态摘要在淘汰前保留，重载或重启使记录失效。

完整模式另保留`apply_part_changes`，仅在客户端已获得明确授权且token尚未进入原生确认链时使用；它不弹人工确认窗口，聊天不注册此入口。原生待确认token不能借此确认或应用。

完整模式的直接apply结果中，`replayed:true`表示返回历史成功摘要，**不代表重新执行或对象当前状态**。用户 undo 后重复 token 不会 redo，需 `get_object_info` 核验。超时后先检查对象，再判断是否取回同 token 的缓存结果；失败、过期、未知或已消费 token 需重新预览并重新核对差异，不盲目换 token 自动再做一次。

## 高级脚本与超时恢复

仅在用户明确授权的任务中使用 `execute_script`。脚本与 FreeCAD Python 控制台同权限，在 GUI 主线程执行，**不是沙箱**；可以修改模型、读写文件、关闭文档或自行保存。框架不额外保存，不保证异常自动回滚。常用读取优先使用已有封装工具。

先记录连接状态的 `script_session_id`。以下只读脚本参数可保存为 `/absolute/path/to/read-script.args.json`：

```json
{
  "requestId": "example-read-001",
  "code": "result = {'active_document': App.ActiveDocument.Name if App.ActiveDocument else None}"
}
```

```bash
node /absolute/path/to/phoenix-freecad-tools/scripts/call-tool.mjs execute_script /absolute/path/to/read-script.args.json
```

需要返回的数据赋给 `result`，标准输出和错误输出也会有限量收集。每次新任务应使用新的请求 ID；相同请求在同一桥接服务会话中重放时，必须使用完全相同的 ID 和代码。

超时后先保存以下内容为 `/absolute/path/to/script-status.args.json` 并查询：

```json
{"requestId":"example-read-001"}
```

```bash
node /absolute/path/to/phoenix-freecad-tools/scripts/call-tool.mjs get_script_status /absolute/path/to/script-status.args.json
```

`queued`、`starting`、`running` 都不是完成状态；`starting` 不能证明没有执行。终态为 `done`、`failed`、`cancelled`，其中 `cancelled` 表示确认在开始前取消。状态查询成功仍须检查 `state` 和 `error`，不能把 `failed` 记录当作脚本成功。

会话仅保留最近 20 份完整终态结果、最多 4096 个历史请求 ID。结果过期后原 ID 仍拒绝重执行；`SCRIPT_NOT_FOUND` 也不证明从未执行。重载桥接模块或重启 FreeCAD 会清空记录并改变 `script_session_id`。如果会话已变、结果过期或执行结果不确定，先核对实际文档状态，不要换一个新 ID 盲目重试。

Node 默认等待 15 秒，可通过 `PHOENIX_TIMEOUT_MS` 调整为 100–120000 ms；插件 GUI 调度仍有自己的 15 秒等待上限。超时不会强制终止已开始的脚本，也不会自动重试。连接地址默认 `http://127.0.0.1:9876/`，可通过 `PHOENIX_FREECAD_URL` 指定允许的本机 HTTP 根地址。helper 会将这两个变量传给 Node 服务。

## 明确请求后获取三维图像

`capture_view` 要求目标文档的三维视图处于前台，FreeCAD 窗口可见并已渲染；后台文档或前台 TechDraw 页会返回 `VIEW_UNAVAILABLE`，需先明确激活已有三维视图。它不截图桌面、不改变视角、不保存模型；插件生成的临时 PNG 读取后清理。图像可能包含模型内容，应由用户明确请求。把以下参数保存为 `/absolute/path/to/capture.args.json`：

```json
{"document":"ExampleDocument","width":800,"height":600}
```

```bash
node /absolute/path/to/phoenix-freecad-tools/scripts/call-tool.mjs capture_view /absolute/path/to/capture.args.json /absolute/path/to/new-view.png
```

可选输出文件必须尚不存在，helper 用独占创建防止覆盖；省略输出文件时只打印元数据和图片数量，不打印 Base64。原生 MCP 调用直接得到图片内容块。PNG 解码上限为 600 KiB，宽高各为 128–1024；过大时降低分辨率。HTTP 请求体上限 256 KiB，Node HTTP 响应体上限 1 MiB。

## 界面语言与验收范围

Phoenix Tools 中的“命名修正”“零件属性”“链接同步”仍可独立使用，不依赖 MCP 启动。0.3.0 新增的两个业务 MCP 工具调用同一规则和 Controller，避免维护两套业务实现。

从 **Phoenix Tools → 设置** 选择简体中文或 English，点击“应用设置”。默认中文；确认后保存本机偏好并立即刷新现有菜单、按钮、tooltip 和 MCP 状态文字，无需重启，不启停 MCP 或修改文档。新打开的业务对话框使用所选语言。`PhoenixTools_Settings` 同时提供菜单入口和工具栏灰色齿轮。设置还提供 `AutoStartMCP`（默认关闭，下次 FreeCAD 启动时生效）；手动停止确认固定启用，旧 `ConfirmStopMCP` 不再使用，保存不会当场启停。状态圆点 `PhoenixTools_MCPStatus` 只查看详情；相邻灰色启停按钮 `PhoenixTools_ToggleMCP` 负责操作，手动停止始终确认。

语言设置保持 MCP 工具名、参数和命令 ID 不变。本轮没有跟随 FreeCAD 语言，动态业务校验和原生错误可能仍保留原文，设置中有明确说明。

参数化、受控STEP与TechDraw示例保留，历史运行回执不随源码分发。通用/业务接口和语言设置的验收要求及保护边界见[验证与验收](VERIFICATION.md)。已有聊天的原生工具清单是否刷新需在客户端观察；helper 提供真正 stdio 链路的可核验调用。

0.3.1 是分发与界面补丁，沿用 0.3.0 的 21 个工具和协议 v1；历史原生验收不改写为新版本安装结果。

## 界面仍被旧窗口占用时

如果检查报告未完成事务，先查看 FreeCAD 是否仍打开旧的警告、任务或编辑窗口，正常关闭提示或完成/取消当前操作后再调用。Qt 模态窗口仍可处理桥接事件，不代表原命令已经结束；不要为了让测试通过而在 Controller 里清除或绕过用户事务。

脚本状态查询或相同 requestId 的重放会返回原记录。重新观察状态应发送新的只读请求；执行超时则先查询原请求，不能把需要防重复的写入换 ID 重做。

## 从工作台查看聊天与 Desk 接入

**Phoenix Tools → AI 聊天** 是独立原生客户端，用户点击连接及官方登录后收发消息。工具设置启用时注册受控19工具 MCP，显示真实工具调用状态；纯聊天模式不注册 MCP。不自动读取桌面聊天或 CAD，详细配置见[原生聊天](CHAT-USAGE.md)。绿色桥接状态不证明聊天工具已连接，发送前还会核对有效配置及精确清单。

**Phoenix Tools → Desk Tools** 是独立后台/Web 连接面板，使用说明见 [Desk 接入操作](DESK-USAGE.md)。它不要求 QtWebEngine，不创建聊天会话，也不将磁盘 Desk 信息冒充 FreeCAD 未保存内存数据。0.5.0 继续使用原 21 个 MCP 工具，不把两个 GUI 入口计算为新 MCP 工具。

## 0.6.0 外观与内嵌聊天范围

0.6.0 新增 `set_appearance`；0.9.0 再增加只读 `inspect_design`，当前完整25工具、内嵌19工具，桥接协议v1保持。参数示例：

```json
{"document":"ExampleDocument","objects":["ExamplePart"],"operation":"half_transparent","requestId":"appearance_example_01"}
```

operation 还支持 `random_color`、`opaque`，以及配合整数 `transparency:0..100` 的 `set_transparency`。必须明确活动且空闲文档及完整对象内部 Name。固定原生模块执行事务，不接收任意 Python；返回原脚本任务状态，queued/running不等于完成。超时只用原 requestId 查询，不换ID重复写。Link 支持限制及一键行为见[当前文档工具](CURRENT-DOCUMENT-TOOLS.md)。

内嵌客户端设置 `PHOENIX_FREECAD_TOOL_SCOPE=chat`，只注册19工具。任意脚本、截图、显隐/选择、重算和直接apply不在内嵌白名单；Label/BOM/Link及有限Pad Length修改经完整预览与原生人工确认，Pad始终拒绝直接apply，范围见[合同](PAD-LENGTH-EDIT.md)。外部完整MCP客户端保留原有高级能力；文件沙箱不约束MCP自身的写入，不自动保存工程。原生确认链的端到端验收仍待完成。

## 0.9.0 设计前只读诊断

先核对连接和目标文档/对象内部 Name，再调用 `inspect_design`。例：

```json
{"document":"ExampleDocument","object":"ExampleSketch","constraintOffset":0,"constraintLimit":50}
```

返回 Body Tip/成员、草图缓存自由度和约束页、固定特征参数与表达式；不求解、不重算、不改选择或活动文档。角度 datum 是 FreeCAD 内部度数 deg，不读取约束原始弧度值充当角度。分页使用首读返回的实例 token 与 revision 继续核对，修订仅覆盖部分事件，不是完整写保护。旧插件未声明能力时明确拒绝；更多字段、限制及提示词见[设计诊断](DESIGN-INSPECTION.md)。

内部迭代0.10.0形成并纳入统一0.3.2的工作台原生聊天模型目录与字号设置，MCP仍完整25/聊天19项、协议v1；选模不改变外部MCP配置。模型取官方客户端实际列表并核回实际型号，已有会话不会自动切换；详见[聊天设置](CHAT-USAGE.md)。

当前主工具条业务入口均有原创图标，设置可选图标+文字或仅图标；前三个MCP控件固定图标。原生聊天首行当前文档/轴测Fit/外观图标和齿轮复用这些命令，路径、工具、模型/字号与诊断在齿轮内；这些GUI布局调整不改变MCP工具名称、数量、参数或审批边界。
