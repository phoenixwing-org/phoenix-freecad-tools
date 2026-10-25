# Codex 接入与本机调用（0.3.0）

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

插件加载后，在 FreeCAD 工作台下拉框选择 **Phoenix Tools**，点击“启动 MCP”。这是同一个启停按钮，命令 ID 为 `PhoenixTools_ToggleMCP`；服务运行后显示“停止 MCP”。选择工作台本身不会自动启动服务。重新启动 FreeCAD 后，也需要再次启动桥接。插件更新后的加载方式见 [README](../README.md)，不要为了刷新聊天工具列表而直接关闭未保存文档。

使用本机 Node 和项目的绝对路径注册 stdio 服务：

```bash
codex mcp add phoenix-freecad -- /absolute/path/to/node /absolute/path/to/phoenix-freecad-tools/dist/cli.js
codex mcp get phoenix-freecad --json
codex mcp list
```

命令格式已按 [OpenAI 官方 Codex MCP 命令说明](https://learn.chatgpt.com/docs/developer-commands#codex-mcp) 和本机 `codex mcp add --help` 核对。启动入口直接使用 Node 与 `dist/cli.js`，MCP stdio 的 stdout 用于协议消息。

注册成功不等于既有聊天已刷新工具清单。让客户端重新连接或重新加载 MCP 后，先调用 `get_connection_status`，确认 `running`、协议 `1`、插件版本 `0.3.0`、实际 `supported_methods` 与 `script_session_id`。`get_capabilities` 是离线清单，不证明插件已连接或支持新功能。模型 ID 不写在 MCP 配置中。

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

0.3.0 注册 21 个工具：

- 连接：`get_capabilities`、`get_connection_status`、`list_documents`。
- 读取：`get_document_info`、`list_objects`、`get_object_info`、`get_selection`、`measure_geometry`、`list_drawing_pages`、`inspect_drawing_page`。
- 相机：`fit_view`、`set_axonometric_view`、`set_view`。
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

## 业务操作：先看差异，再应用

这两个 MCP 工具复用普通工作台的 Model/Controller。预览要求目标已经是空闲的活动文档；它不改变选择或模型，但会创建短期内存 token。使用步骤如下：

1. 用 `get_object_info` 查看实际对象和正式属性，明确本次要改什么。
2. 调用 `prepare_part_changes`，把完整旧值、新值、冲突和 `can_apply` 展示给用户。
3. 核对差异和本次授权；有 blocked 行或 `can_apply:false` 时先处理原因，不调用 apply。
4. 使用该预览实际返回的 token 调用 `apply_part_changes`，再读取对象核对结果。

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

预览返回 `rows`、`changed_count`、`can_apply`、`token` 和过期时间。完整预览超过 128 KiB 会拒绝，请减少对象/字段；不能截断差异后继续应用。核对后将实际 token 填入 `/absolute/path/to/apply.args.json`：

```json
{"token":"REPLACE_WITH_TOKEN_FROM_PREVIEW"}
```

以上 token 是占位文本，不能直接使用。调用：

```bash
node /absolute/path/to/phoenix-freecad-tools/scripts/call-tool.mjs apply_part_changes /absolute/path/to/apply.args.json
```

应用会重新核对文档实例、快照及来源，再以事务写入，可撤销但不保存。token 仅在当前桥接内存中保留，五分钟过期、容量 32，新预览可能淘汰旧结果；重载或重启后失效。

`replayed:true` 表示返回历史成功摘要，**不代表重新执行或对象当前状态**。用户 undo 后重复 token 不会 redo，需 `get_object_info` 核验。超时后先检查对象，再判断是否取回同 token 的缓存结果；失败、过期、未知或已消费 token 需重新预览并重新核对差异，不盲目换 token 自动再做一次。

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

Phoenix Tools 中的“命名修正”“填写零件属性”“同步链接名称”仍可独立使用，不依赖 MCP 启动。0.3.0 新增的两个业务 MCP 工具调用同一规则和 Controller，避免维护两套业务实现。

从 **Phoenix Tools → 设置 / Settings** 选择简体中文或 English，点击“保存并应用”。默认中文；确认后保存本机偏好并立即刷新现有菜单、按钮、tooltip 和 MCP 状态文字，无需重启，不启停 MCP 或修改文档。新打开的业务对话框使用所选语言。`PhoenixTools_Settings` 只在菜单中，不增加工具栏按钮。

语言设置保持 MCP 工具名、参数和命令 ID 不变。本轮没有跟随 FreeCAD 语言，动态业务校验和原生错误可能仍保留原文，设置中有明确说明。

0.2.0/0.2.1 的既有公开参数化、受控 STEP 和 TechDraw 证据及归档保留原样。0.3.0 新通用/业务接口和语言设置已实现，真实验收、最终测试和保护边界以 [验证记录](VERIFICATION.md) 为准。已有聊天的原生工具清单是否刷新需在客户端观察；helper 提供真正 stdio 链路的可核验调用。
