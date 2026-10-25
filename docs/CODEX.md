# Codex 接入与本机调用（0.2.1）

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

注册成功不等于既有聊天已刷新工具清单。让客户端重新连接或重新加载 MCP 后，先调用 `get_connection_status`，确认 `running`、协议 `1`、插件版本 `0.2.1`、实际 `supported_methods` 与 `script_session_id`。`get_capabilities` 是离线清单，不证明插件已连接或支持新功能。模型 ID 不写在 MCP 配置中。

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

0.2.1 注册 16 个工具：

- 连接：`get_capabilities`、`get_connection_status`、`list_documents`。
- 读取：`get_document_info`、`list_objects`、`get_object_info`、`get_selection`、`measure_geometry`、`list_drawing_pages`、`inspect_drawing_page`。
- 相机：`fit_view`、`set_axonometric_view`、`set_view`。
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

`capture_view` 仅导出 FreeCAD 当前三维视图，不截图桌面、不改变视角、不保存模型；插件生成的临时 PNG 读取后清理。图像可能包含模型内容，应由用户明确请求。把以下参数保存为 `/absolute/path/to/capture.args.json`：

```json
{"document":"ExampleDocument","width":800,"height":600}
```

```bash
node /absolute/path/to/phoenix-freecad-tools/scripts/call-tool.mjs capture_view /absolute/path/to/capture.args.json /absolute/path/to/new-view.png
```

可选输出文件必须尚不存在，helper 用独占创建防止覆盖；省略输出文件时只打印元数据和图片数量，不打印 Base64。原生 MCP 调用直接得到图片内容块。PNG 解码上限为 600 KiB，宽高各为 128–1024；过大时降低分辨率。HTTP 请求体上限 256 KiB，Node HTTP 响应体上限 1 MiB。

## 本地工具与验收范围

Phoenix Tools 中的“命名修正”“填写零件属性”“同步链接名称”是独立 GUI 功能，不依赖 MCP 启动。前者处理显示 Label；链接同步只修改链接实例的 Label。它们不在上述 16 个 MCP 工具中。

0.2.1 已通过真实 MCP stdio 连接、另一聊天的只读联测，以及新建公开样例的参数化建模、受控 STEP 往返重建和 TechDraw 导出重开验收。具体证据和边界见 [验证记录](VERIFICATION.md)。已有聊天的原生工具清单是否刷新仍需在客户端观察；helper 提供同一 stdio 链路的可核验调用。
