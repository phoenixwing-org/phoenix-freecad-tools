# FreeCAD 桥接协议 v1 / 实现 0.3.0

客户端通过官方 TypeScript SDK 的 **MCP stdio** 协议连接 Node 服务。Node 再用本节定义的本机 JSON HTTP 协议连接 FreeCAD 插件。桥接协议版本仍为 `1`，不等同于实现版本 `0.3.0`；内部 HTTP 接口不是 Streamable HTTP MCP 服务。历史版本的证据和归档不改写为本版结果。

## HTTP 与结果封装

默认地址为 `http://127.0.0.1:9876/rpc`，仅接受 `POST` 和 `Content-Type: application/json`。插件仅绑定 IPv4 loopback，检查 Host，拒绝带 Origin 或 Sec-Fetch 请求头的浏览器请求。Node 使用 `node:http`，不附加 Sec-Fetch 头。请求 JSON 不接受重复键、非有限数值、未知方法或额外参数。

```json
{"method":"view_axonometric","params":{"docName":"ExampleDocument","fit":true}}
```

```json
{"ok":true,"data":{"document":"ExampleDocument","view":"axonometric","fit":true}}
```

```json
{"ok":false,"error":{"code":"DOCUMENT_NOT_FOUND","message":"指定文档未打开。"}}
```

| 边界 | 当前限制 |
| --- | --- |
| 插件接收的 HTTP 请求体 | 256 KiB，按编码后的字节数计算 |
| Node 接收的 HTTP 响应体 | 1 MiB，超出返回 `RESPONSE_TOO_LARGE` |
| `execute_script.code` | 最多 32768 字符，禁止 NUL 和孤立代理项；仍受请求体字节上限约束 |
| 解码后的 PNG | 600 KiB；Base64 扩张后的响应仍受 1 MiB 上限约束 |
| 图片宽、高 | 各 128–1024 整数，默认 800 × 600 |
| 业务预览 | 完整序列化 JSON 上限 128 KiB，超出拒绝且不创建 token；不截断可应用差异 |
| 业务 token | 内存容量 32，有效期 300 秒；不持久化，不与脚本历史容量混用 |
| 正式属性文本 | 每值最多 2048 字符，单行，禁止控制字符、代理项、路径及模板源码 |

普通 MCP 成功结果同时提供 JSON 文本和 `structuredContent`，格式为 `{ok:true,source_kind:"live_freecad_gui",observed_at:...,data:...}`。`observed_at` 是 Node 的观测时间，不是模型修订令牌。工具失败使用 `isError:true`，内容为 `{ok:false,error:{code,message}}`；输入 schema 错误可在 SDK 校验阶段直接拒绝，不派发到插件。离线能力清单的 `source_kind` 为 `server_manifest`。

## 21 个 MCP 工具

下表参数为 MCP 层参数，`?` 表示可省略。Node 将 `document` 映射为 `docName`、`object` 映射为 `objectName`、`objects` 映射为 `objectNames`、`page` 映射为 `pageName`；其余参数名保持原样。文档、对象和图纸均使用内部 **Name**，不是显示 Label。只有标注可省略的文档参数才默认使用活动文档；新通用操作和业务预览要求显式文档，不从磁盘打开工程。

| MCP 工具 | 内部桥接方法 | MCP 参数与结果范围 |
| --- | --- | --- |
| `get_capabilities` | 无，离线清单 | `{}`；Node 注册的工具、分组、作用范围及限制 |
| `get_connection_status` | `get_status` | `{}`；插件版本、协议、服务状态、GUI 调度状态、`supported_methods`、`script_session_id` |
| `list_documents` | `list_documents` | `{}`；当前已打开文档的内部 Name 数组 |
| `fit_view` | `view_fit` | `{document?}`；当前三维视图适合窗口 |
| `set_axonometric_view` | `view_axonometric` | `{document?,fit?:false}`；轴测图，可同时适合窗口 |
| `get_document_info` | `get_document_info` | `{document?}`；文档状态、对象数、单位与活动 Body 摘要 |
| `list_objects` | `list_objects` | `{document?,offset?:0,limit?:50,typeId?}`；分页对象摘要，可按精确 TypeId 筛选 |
| `get_object_info` | `get_object_info` | `{document?,object,properties?}`；对象摘要、指定公开属性和现有几何摘要 |
| `get_selection` | `get_selection` | `{document?}`；当前选择、子元素和拾取点 |
| `measure_geometry` | `measure_geometry` | `{document?,object,subelement?}`；现有对象或子元素的几何测量 |
| `list_drawing_pages` | `list_drawing_pages` | `{document?}`；TechDraw 页摘要 |
| `inspect_drawing_page` | `inspect_drawing_page` | `{document?,page}`；模板、视图、标注及来源引用摘要 |
| `set_view` | `view_standard` | `{document?,view,fit?:false}`；标准三维视图 |
| `capture_view` | `capture_view` | `{document?,width?:800,height?:600}`；当前三维视图 PNG 与元数据 |
| `set_selection` | `set_selection` | `{document,objects}`；替换目标活动文档的完整对象选择，允许空数组 |
| `set_visibility` | `set_visibility` | `{document,objects,visible}`；明确对象的统一显隐，事务可撤销 |
| `recompute_document` | `recompute_document` | `{document}`；显式重算空闲活动文档并检查原生结果和对象错误 |
| `prepare_part_changes` | `prepare_part_changes` | `{document,objects,operation,values?}`；完整差异预览和短期 token，不修改模型 |
| `apply_part_changes` | `apply_part_changes` | `{token}`；复核并应用预览，或返回仍保留的历史成功摘要 |
| `execute_script` | `execute_script` | `{code,requestId}`；显式授权的 Python 脚本执行与请求状态 |
| `get_script_status` | `get_script_status` | `{requestId}`；当前桥接服务会话中的请求状态，不执行脚本 |

内部另有 `ping {}`，返回 `{pong:true}`，没有注册为 MCP 工具。

所有 MCP 参数对象均严格校验，不接受额外字段。名称长 1–256 字符，禁止 Unicode 控制字符和代理项。`offset` 为 0–2147483647 整数，`limit` 为 1–100 整数；`typeId` 符合 `^[A-Za-z_][A-Za-z0-9_:]{0,127}$`。`properties` 最多 20 个不同的公开标识符，每项长 1–128 字符，不能以下划线开头或使用属性路径；属性还必须存在于对象的 `PropertiesList`。未指定时读取有限的常用属性。

`subelement` 仅接受 `FaceN`、`EdgeN`、`VertexN`，N 为 1–7 位正整数。`view` 仅接受 `front`、`rear`、`left`、`right`、`top`、`bottom`、`axonometric`。`fit` 必须是布尔值。

`objects` 为不重复的内部名称数组，最多 100 项；仅 `set_selection` 允许为空。`visible` 必须为布尔值。业务 `token` 符合 `^[A-Za-z0-9_-]{16,128}$`，只能使用本次预览实际返回的值。

## 能力发现与兼容

`get_status` 返回 `protocol_version:1`，当前插件还返回 `supported_methods` 数组和 `script_session_id`。Node 校验协议版本及能力数组格式。`get_capabilities` 只说明 Node 的离线工具清单，其 `connection_checked`、`runtime_supported_checked` 均为 `false`；运行时支持情况必须检查连接状态。

最初的五个 MCP 工具（清单、连接、文档列表、适合窗口、轴测图）兼容未返回 `supported_methods` 的协议 v1 插件。其余 16 个工具每次先读取状态，只有插件明确列出相应桥接方法时才派发；缺少声明返回 `UNSUPPORTED_CAPABILITY`，不会尝试调用该方法。工具在 Node 清单中可见，不代表当前运行的插件已经支持它。0.3.0 的能力分组新增 `general_actions` 与 `phoenix`，不改变旧方法名及协议版本。

## 通用操作 `general_actions`

三个工具均要求目标已是活动文档，并能确认没有未完成事务或编辑状态；不自动激活文档。所有对象先解析和校验，再产生影响，不自动保存。

- `set_selection` 只替换此文档的完整对象选择，保留其他文档选择；不接受 Face/Edge 子元素参数。空数组清除此文档选择。修改失败时尝试恢复原选择，包括原有子元素；恢复也失败时明确返回 `SELECTION_RESTORE_FAILED`。它不修改模型，也不使用文档撤销事务。
- `set_visibility` 要求启用撤销和全部目标存在可用 Visibility。只改变指定对象的显隐；有变化时一笔事务，写后校验，失败撤销，不改 Label 或其他属性。
- `recompute_document` 调用原生重算，检查返回值和对象 error/invalid 状态，失败返回 `RECOMPUTE_FAILED`。可能更新缓存几何或执行特征求解，不是完整几何有效性证明，也不承诺回到重算前状态。它标注 `idempotentHint:false`，不得因超时自动重试。

## Phoenix 业务预览与应用

`prepare_part_changes` 复用工作台 Model/Controller，要求明确的空闲活动文档与 1–100 个不同对象。它**模型只读，但会创建内存 token**，因此 `readOnlyHint:true`、`idempotentHint:false`；不是完全无状态的读取。

| `operation` | 差异范围 |
| --- | --- |
| `normalize_names` | 按正式属性预览 Label 命名修正，不改内部 Name 或文件名 |
| `sync_link_labels` | 预览 App::Link 实例与直接来源的 Label 同步，不改变来源 |
| `initialize_properties` | 只初始化缺失的五个正式字符串字段为空，不覆盖已有值 |
| `recommend_properties` | 从明确完整的编号.版本-名称推荐正式值，只补空值并保留现值；歧义不猜测 |
| `set_properties` | 设置明确提供的正式字段，每个对象使用相同字段集，可使用不同值 |

正式字段为 `PartNumber`、`PartVersion`、`TypeCode`、`ModelSeries`、`PartName`，属性组为 `CustomDefine`。属性操作不向 App::Link 来源转发写入。仅 `set_properties` 必须且允许提供 `values`：键必须与 `objects` 精确对应，每个对象至少一个字段，各对象字段集完全相同，不接受额外字段。空字符串可表示明确空值，仍需完整预览；文本及 HTTP 总字节边界均适用。

预览返回 `state:prepared`、`token`、`ttl_seconds:300`、`expires_at`、`can_apply`、`changed_count`、完整 `rows` 及 `requires_confirmation:true`。每行包括 `object`、`field`、`old`、`new` 和 `status`（`change` / `unchanged` / `blocked`），必要时附原因或来源。不可完整、安全显示的值会阻止应用；完整响应超过 128 KiB 返回 `PART_PREVIEW_TOO_LARGE`，未创建 token，不会截断后应用。

调用方先展示完整差异，确认属于用户授权的操作后再调用 `apply_part_changes`；服务本身不弹出确认窗口。应用重新检查同一文档实例、对象快照、编辑状态和 Link 来源，再经 Controller 事务写入和核对结果；成功可撤销、不保存。冲突、文档重开或来源改变会拒绝，不能用相同内部 Name 假定仍为原对象。

成功返回 `state:applied`、`changed_count`、`replayed:false`、`saves_document:false`。同一 token 在未过期且结果仍保留时返回 `replayed:true` 的**历史成功摘要**，不重新执行，不核验当前对象是否仍保持该状态。用户 undo 后重放也不会 redo；当前状态必须另用 `get_object_info` 核对。

token 仅存于桥接模块内存，五分钟过期，容量 32；新预览可淘汰已有成功、失败、过期或不可应用的记录，全部为待处理可用预览时返回 `PART_PREVIEW_CAPACITY`。模块重载或重启使记录失效。过期、未知或失败 token 须先检查对象，再重新预览；不要换 token 盲目重复应用。应用失败或正在处理的 token 不再执行。Node 不自动重试；超时可能已经写入，应先核对结果，再决定是否取回原 token 的缓存结果。

## 读取、视图与图像

常用读取工具读取当前 FreeCAD 会话，不重算、不保存、不修改模型、不改变选择或激活文档。返回值经过限量序列化；文件路径属性会隐藏，不是完整模型导出。Quantity 的 `value` 是 FreeCAD 内部单位数值，`unit` 按原生八维 `unit_signature` 给出同一内部体系的表达，并附 `unit_type`、`unit_system:freecad_internal`；长度为 mm，Quantity 角度为 deg，而 Placement 的 Rotation.Angle 另以 rad 返回。未知维度不推测单位。`modified` 等接口无法可靠取得时返回未知值并附带说明，未提供文档实例或修订令牌，不能据此做写入并发校验。

测量读取当前对象返回的 Shape，未展开完整装配变换链，也未证明几何已重算到最新状态。子元素编号只验证当前存在，不保证编辑前后的拓扑身份稳定。图纸检查只报告现有来源与状态，不修复引用、不重算，也不证明生产尺寸完整或引用有效。

几何体积只对包含实体的 Shape 适用；单面、边等返回 `volume:null`、`volume_applicable:false`。`bounding_box` 来自原生 `Shape.BoundBox`，附 `bounding_box_source:native_shape_bound_box`、`bounding_box_precision:kernel_enclosure_not_exact_clearance` 和未知误差界限 `bounding_box_error_bound_mm:null`。曲面包络可能有近似误差，不可据此证明精确间隙。

相机优先使用目标文档的活动视图。工程图页面在前台时，若该文档只有一个符合操作的已有三维视图则使用它；不会创建或激活视图，多个候选时返回 `VIEW_UNAVAILABLE`。截图要求目标文档的三维视图处于前台，后台或工程图页返回 `VIEW_UNAVAILABLE`，需先显式激活已有三维视图，防止后台 OpenGL 视图导出空白。

`fit_view`、`set_axonometric_view`、`set_view` 改变三维相机，标注为非只读，但不保存或修改模型。读取工具标注 `readOnlyHint:true`。

`capture_view` 只应在用户明确要求查看图像时调用，图像可能包含当前模型信息。它调用 FreeCAD 三维视图的 `saveImage`，不截图桌面、不改变相机、不保存模型。FreeCAD 窗口还需可见并已渲染；隐藏或最小化时部分平台可能生成空白，PNG 格式校验不能代替内容核验。插件在临时目录写入 PNG，读取后自动清理；因此“只读”指模型读取，并非零文件写入。

桥接图片结果为 `{document,mime_type:"image/png",data:<Base64>,width,height}`。Node 校验规范 Base64、解码大小、PNG 魔数和 IHDR 尺寸；MCP 返回一个 `image` 内容块及元数据文本。`structuredContent` 和文本只保留 `{document,mime_type,width,height,capture_scope:"freecad_3d_view",image_in_content:true}` 等元数据，不重复 Base64，也不返回临时文件路径。

## 高级脚本、状态与去重

`execute_script` 在 FreeCAD GUI 主线程执行 Python，注入 `App` / `FreeCAD` 和 `Gui` / `FreeCADGui`。将需要返回的数据赋给 `result`；每次使用新的脚本命名空间，FreeCAD 会话中的实际改动仍会保留。脚本与 FreeCAD Python 控制台具有相同权限，**不是沙箱**：可改模型、关闭文档、读写文件、访问网络及自行保存。框架不额外保存，也不承诺异常自动回滚。客户端只能在用户明确授权的范围内使用该工具；服务本身不弹出授权对话框。

`requestId` 必须符合 `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`。在同一桥接服务会话中，同 ID、同代码不重复执行；同 ID、不同代码返回 `REQUEST_ID_CONFLICT`。工具的 `idempotentHint:true` 仅对应这一条件，另标注 `readOnlyHint:false`、`destructiveHint:true`、`openWorldHint:true`。

执行请求通常等待脚本结束后返回；超时或重复请求可读到中间状态：

| `state` | 含义 |
| --- | --- |
| `queued` | 已登记，等待 GUI 调度 |
| `starting` | 调度出现不确定性，尚未记录为 running；不能据此认定未执行 |
| `running` | 已进入脚本执行 |
| `done` | 脚本完成，可读取 `result`、`stdout`、`stderr` |
| `failed` | 脚本异常，记录 `error`；之前的改动可能已生效 |
| `cancelled` | 已确认在开始前取消，例如 GUI 排队超时或服务停止 |

记录包含 `request_id`、`created_at`、`started_at`、`finished_at`；时间为 Unix 秒。标准输出和错误输出各保留最多 8192 字符，截断由 `output_truncated` 标记。`result` 仅有限量 JSON 摘要：限制递归深度、条目数及字符串长度，序列化超过 128 KiB 时返回截断提示。执行异常返回工具错误，不把异常当成成功；查询一个 `failed` 记录本身可以成功，须继续检查其 `state` 和 `error`。

完整终态结果仅保留最近 20 份；会话内最多记住 4096 个请求 ID 及代码摘要。完整结果过期后，用同 ID 重放返回 `SCRIPT_RESULT_EXPIRED`，不会重新执行；达到历史容量返回 `SCRIPT_HISTORY_FULL`。待执行记录也有数量上限，满时返回 `SCRIPT_QUEUE_FULL`。`get_script_status` 对过期或未知记录返回 `SCRIPT_NOT_FOUND`，这不证明该 ID 从未执行。

记录仅存于桥接模块内存。**重载桥接模块或重启 FreeCAD 会重置记录并生成新的 `script_session_id`**；单纯停止、再启动同一个已加载模块的服务不保证清空记录。先通过连接状态记住会话 ID；超时后先查询原 ID，必要时仅在同一会话重放完全相同的 ID 和代码。会话已变或结果过期时，先核对文档实际状态，再决定下一步，不能依赖去重跨会话生效。

## GUI 调度、超时与本机边界

除 `ping`、`get_status`、`get_script_status` 外，桥接操作通过 Qt queued signal 排入 GUI 主线程。插件默认等待 15 秒：确认尚未开始的排队超时返回 `GUI_QUEUE_TIMEOUT` 并取消；已开始的操作超时返回 `GUI_TIMEOUT`，不强制中断。Node 默认等待 15000 ms，`PHOENIX_TIMEOUT_MS` 可设为 100–120000 的整数；它只改变 Node 等待时长，不改变插件的 GUI 等待上限。`BRIDGE_TIMEOUT` 表示 Node 没有及时取得结果，操作仍可能继续。Node 不自动重试。

`PHOENIX_FREECAD_URL` 默认 `http://127.0.0.1:9876/`，只允许 `localhost` 或 `127.0.0.1` 的 HTTP 根地址，不接受账号密码、查询参数、片段或其他路径。端口无身份认证，也没有本机进程间的用户权限隔离；Origin/Sec-Fetch 检查不能阻止其他本机程序访问。仅用于可信本机环境，不对外监听。

FreeCAD 工作台显示为 **Phoenix Tools**。单个命令 `PhoenixTools_ToggleMCP` 显示实际启停状态，不会因选择工作台就自动启动服务。普通按钮独立可用，业务 MCP 通过两个预览/应用工具复用同一规则。`PhoenixTools_Settings` 仅增加菜单入口；中文默认，English 保存在本机，确认后立即刷新已有界面，不重启或改变 MCP 状态。语言不改变方法名、参数、命令 ID、对象值或正式字段；本轮没有跟随 FreeCAD 语言或业务/原生动态错误的完整翻译。
