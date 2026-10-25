# FreeCAD 桥接协议 v1 / 实现0.3.2候选

版本说明：当前统一候选为0.3.2；本页0.4.0–0.10.0均为未发布内部迭代标签，保留当时代码、原生版本及归档事实，不能视为多个正式发行或本轮最终验收。详见[版本约定](VERSIONING.md)。

客户端通过官方 TypeScript SDK 的 **MCP stdio** 协议连接 Node 服务。Node 再用本节定义的本机 JSON HTTP 协议连接 FreeCAD 插件。桥接协议版本仍为 `1`，不等同于实现版本`0.3.2`；内部 HTTP 接口不是 Streamable HTTP MCP 服务。历史版本的证据和归档不改写为本版结果。

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
| 三个文档/对象读取及 `inspect_design` | 各次完整 JSON 上限 128 KiB，超出返回 `READ_RESPONSE_TOO_LARGE`，不截断为成功 |
| 业务预览 | 完整序列化 JSON 上限 128 KiB，超出拒绝且不创建 token；不截断可应用差异 |
| 业务token | 内存容量32，可确认期300秒；终结状态摘要保留至淘汰，不持久化，不与脚本历史混用 |
| 原生确认状态与重算结果数据体 | Node分别限制4 KiB；完整严格校验，不截断为成功 |
| 正式属性文本 | 每值最多 2048 字符，单行，禁止控制字符、代理项、路径及模板源码 |

普通 MCP 成功结果同时提供 JSON 文本和 `structuredContent`，格式为 `{ok:true,source_kind:"live_freecad_gui",observed_at:...,data:...}`。`observed_at` 是 Node 的观测时间，不是模型修订令牌。工具失败使用 `isError:true`，内容为 `{ok:false,error:{code,message}}`；输入 schema 错误可在 SDK 校验阶段直接拒绝，不派发到插件。离线能力清单的 `source_kind` 为 `server_manifest`。

## 完整模式 25 个 MCP 工具

下表参数为 MCP 层参数，`?` 表示可省略。Node 将 `document` 映射为 `docName`、`object` 映射为 `objectName`、`objects` 映射为 `objectNames`、`page` 映射为 `pageName`；其余参数名保持原样。文档、对象和图纸均使用内部 **Name**，不是显示 Label。只有标注可省略的文档参数才默认使用活动文档；新通用操作和业务预览要求显式文档，不从磁盘打开工程。

| MCP 工具 | 内部桥接方法 | MCP 参数与结果范围 |
| --- | --- | --- |
| `get_capabilities` | 无，离线清单 | `{}`；Node 注册的工具、分组、作用范围及限制 |
| `get_connection_status` | `get_status` | `{}`；插件版本、协议、服务状态、GUI 调度状态、`supported_methods`、`script_session_id`、`read_provenance`、`recompute_result`、`part_approvals` |
| `list_documents` | `list_documents` | `{}`；当前已打开文档的内部 Name 数组 |
| `fit_view` | `view_fit` | `{document?}`；当前三维视图适合窗口 |
| `set_axonometric_view` | `view_axonometric` | `{document?,fit?:false}`；轴测图，可同时适合窗口 |
| `get_document_info` | `get_document_info` | `{document?,expectedDocumentInstanceToken?,expectedRevision?}`；文档状态、对象数、单位与活动 Body 摘要 |
| `list_objects` | `list_objects` | `{document?,offset?:0,limit?:50,typeId?,expectedDocumentInstanceToken?,expectedRevision?}`；分页对象摘要，可按精确 TypeId 筛选 |
| `get_object_info` | `get_object_info` | `{document?,object,properties?,expectedDocumentInstanceToken?,expectedRevision?}`；对象摘要、指定公开属性和现有几何摘要 |
| `get_selection` | `get_selection` | `{document?}`；当前选择、子元素和拾取点 |
| `measure_geometry` | `measure_geometry` | `{document?,object,subelement?}`；现有对象或子元素的几何测量 |
| `inspect_design` | `inspect_design` | `{document?,object,constraintOffset?:0,constraintLimit?:50,expectedDocumentInstanceToken?,expectedRevision?}`；Body、草图及特征的缓存设计诊断 |
| `list_drawing_pages` | `list_drawing_pages` | `{document?}`；TechDraw 页摘要 |
| `inspect_drawing_page` | `inspect_drawing_page` | `{document?,page}`；模板、视图、标注及来源引用摘要 |
| `set_view` | `view_standard` | `{document?,view,fit?:false}`；标准三维视图 |
| `set_appearance` | `execute_script`，固定有类型脚本 | `{document,objects,operation,transparency?,requestId}`；明确对象的颜色或透明度，事务可撤销，不开放任意代码参数 |
| `capture_view` | `capture_view` | `{document?,width?:800,height?:600}`；当前三维视图 PNG 与元数据 |
| `set_selection` | `set_selection` | `{document,objects}`；替换目标活动文档的完整对象选择，允许空数组 |
| `set_visibility` | `set_visibility` | `{document,objects,visible}`；明确对象的统一显隐，事务可撤销 |
| `recompute_document` | `recompute_document` | `{document}`；显式重算空闲活动文档并检查原生结果和对象错误 |
| `prepare_part_changes` | `prepare_part_changes` | `{document,objects,operation,values?,body?,lengthMm?}`；按操作严格校验，完整差异预览和短期 token，不修改模型 |
| `request_part_change_approval` | `request_part_change_approval` | `{token}`；打开或复用非模态完整预览确认窗口，返回内存状态，不等于执行 |
| `get_part_change_status` | `get_part_change_status` | `{token}`；纯内存查询，不读CAD、不打开窗口、不执行修改 |
| `apply_part_changes` | `apply_part_changes` | `{token}`；完整模式复核并应用未进入原生确认链的预览，或返回仍保留的历史成功摘要 |
| `execute_script` | `execute_script` | `{code,requestId}`；显式授权的 Python 脚本执行与请求状态 |
| `get_script_status` | `get_script_status` | `{requestId}`；当前桥接服务会话中的请求状态，不执行脚本 |

内嵌聊天注册精确19个工具：基础读取、视图/外观和脚本状态16项，加上`prepare_part_changes`、`request_part_change_approval`和`get_part_change_status`。其余6项`capture_view`、`execute_script`、`set_selection`、`set_visibility`、`recompute_document`、`apply_part_changes`不注册。Label/BOM/Link修改只能从完整预览进入FreeCAD原生确认窗口，模型不能提交布尔值来确认；有限首个Pad Length复用此链且必须原生人工确认，详见[Pad合同](PAD-LENGTH-EDIT.md)；原生端到端尚待验收。配置和实际清单必须精确匹配；开启可选Desk时再增加5个只读工具。

内部另有 `ping {}`，返回 `{pong:true}`，没有注册为 MCP 工具。

所有 MCP 参数对象均严格校验，不接受额外字段。名称长 1–256 字符，禁止 Unicode 控制字符和代理项。`offset` 为 0–2147483647 整数，`limit` 为 1–100 整数；`typeId` 符合 `^[A-Za-z_][A-Za-z0-9_:]{0,127}$`。`properties` 最多 20 个不同的公开标识符，每项长 1–128 字符，不能以下划线开头或使用属性路径；属性还必须存在于对象的 `PropertiesList`。未指定时读取有限的常用属性。

`subelement` 仅接受 `FaceN`、`EdgeN`、`VertexN`，N 为 1–7 位正整数。`view` 仅接受 `front`、`rear`、`left`、`right`、`top`、`bottom`、`axonometric`。`fit` 必须是布尔值。

`objects` 为不重复的内部名称数组，最多 100 项；仅 `set_selection` 允许为空。`visible` 必须为布尔值。业务 `token` 符合 `^[A-Za-z0-9_-]{16,128}$`，只能使用本次预览实际返回的值。

## 能力发现与兼容

`get_status` 返回 `protocol_version:1`，当前插件还返回 `supported_methods` 数组和 `script_session_id`。Node 校验协议版本及能力数组格式。`get_capabilities` 只说明 Node 的离线工具清单，其 `connection_checked`、`runtime_supported_checked` 均为 `false`；运行时支持情况必须检查连接状态。

最初的五个 MCP 工具（清单、连接、文档列表、适合窗口、轴测图）兼容未返回 `supported_methods` 的协议 v1 插件。其余工具通过适配器先读取状态，只有插件明确列出相应桥接方法时才派发；`set_appearance` 检查其实际使用的 `execute_script` 能力。缺少声明返回 `UNSUPPORTED_CAPABILITY`，不会尝试调用该方法。工具在 Node 清单中可见，不代表当前运行的插件已经支持它。0.3.0 的能力分组新增 `general_actions` 与 `phoenix`，不改变旧方法名及协议版本。

## 0.4.0 文档实例与部分事件诊断

`get_document_info`、`list_objects`、`get_object_info` 的成功 `data` 共同包含 `document_instance_token`（及同值 `instance_token`）、`revision`、`revision_scope`、`revision_coverage`、`revision_status` 和 `provenance`。实例 token 为当前观察器内存生成的 32 字符不透明标识，不含文件路径；同名关闭重开后会变，模块重载也不能假定保持。

`revision_scope` 固定为 `observed_document_events_since_registration`。观察器成功注册时 `revision_status:observing`、`revision_coverage:partial`，计数为观察到的原生事件数，初值 0 只表示注册后尚未观察到事件。它不是保存次数、完整文件历史或内容哈希，也不保证捕获所有原生/GUI/外部变化。观察器缺失或注册失败时返回 `revision:null`、`revision_coverage:unknown`，状态分别为 `observer_unavailable` 或 `registration_failed`。同名旧 helper 缓存若来源文件不匹配则返回 `STATE_SOURCE_MISMATCH`，不执行未知缓存。

`provenance` 声明 `source:live_native_document`、`geometry_freshness:not_verified`、`recomputed:false`、`write_guard:false`。读取不为了获取诊断而重算、求解、切换活动对象、修改选择、打开外部文档或保存。计数不能替代业务应用前的实例/完整快照/来源复核。

三工具都接受 `expectedDocumentInstanceToken`；`expectedRevision` 为非负安全整数，必须同时提供 token。读取前后都核对可观察条件：实例不符返回 `DOCUMENT_INSTANCE_MISMATCH`，修订不符返回 `DOCUMENT_REVISION_MISMATCH`，无可用观察器却要求修订返回 `DOCUMENT_REVISION_UNAVAILABLE`。失败后重新读取并判断实际变化，不把不同版本分页结果拼为一个一致快照。

`get_document_info.context` 分别报告活动文档、活动 Body 和编辑状态；每项附 `value`、`source`、`status`。可可靠读取但不存在时允许 `available + null`，无法读取则是 `unavailable` 或 `read_failed`，不猜测活动 Body。`modified` 优先使用原生 GUI 文档 Modified，`touched` 使用原生文档状态，分别附来源与状态。`bodies` 最多 50 项，带 total/truncated/limit、Body 引用、Tip 引用和对象状态；不读取 Shape 来推断这些上下文。

`get_status.read_provenance` 为 `{version:1,methods:[...],revision_coverage:partial,write_guard:false}`；0.9.0 methods 包含上述三个读取和 `inspect_design`。Node 逐次核对运行时声明、来源 schema 和完整 UTF-8 预算。旧插件仍可普通读取，新增诊断字段降为 null/unknown/`legacy_plugin`；若要求 expected 条件而运行时未声明该能力，在派发前返回 `UNSUPPORTED_CAPABILITY`。这些诊断不新增通用写入令牌；0.9.0 的 `inspect_design` 复用相同文档实例与部分事件来源合同，协议仍为 v1。

## 0.9.0 只读设计诊断 `inspect_design`

输入使用明确的对象内部 `Name`，文档省略时使用当前活动文档。`constraintOffset` 为零基非负整数（最大 2147483647），`constraintLimit` 默认 50、范围 1–100；它们只分页草图约束，Body 成员和参数另有固定预算。

```json
{"document":"ExampleDocument","object":"Sketch","constraintOffset":0,"constraintLimit":50}
```

成功结果包含文档实例、部分事件修订和来源，以及以下分节；Node 同时校验分节、类型、状态、页索引和总 UTF-8 预算，不把畸形或不匹配数据当成功。

| 分节 | 返回范围与未知边界 |
| --- | --- |
| `object` | `Name`、有限 Label/TypeId、`kind`、对象缓存 `State` 及 `has_error_marker`。状态不可读时错误标记为 null；false 只表示所读完整状态未出现错误标记，不证明几何有效 |
| `body` | 仅 Body 非 null；Tip 的引用及独立状态、最多 100 个当前文档成员引用、完整成员数及 `members_truncated`。已知无 Tip 可为 `available + null`；无效或外部引用省略并明确不完整，不沿来源展开 |
| `sketch` | 仅草图非 null；几何数、缓存自由度及来源、`FullyConstrained`，分别附 `value/status/source`。自由度为 0、全约束为 false 都是有效已知值，不能与 null 混淆 |
| `sketch.constraints` | 总数、请求的 offset/limit、实际条目、has_more/next_offset。每条 index 为零基原生约束索引，保留类型/名称/拓扑索引，`getDriving` 与 `getActive` 各自独立读取，`getDatum` 返回量值；某项失败不伪造其余项 |
| `parameters` | 只读有限常用公开参数：Length、Length2、Radius、Angle、Midplane、Reversed、Type、Profile、Width、Height、Thickness、Offset。按实际属性类型返回量值、数值、布尔、文本或当前文档引用，不计算表达式 |
| `parameters_status/source` | 仅实际读取 `PropertiesList` 后才可将空数组解释为“有限白名单内没有参数”；不可读时 parameters 仍为空，但 status 非 available、source 为 null，不能解释为完整空清单 |
| `expressions` | 最多 50 个未求值的 ExpressionEngine 文本条目，附实际总数和 truncated。敏感或不支持文本以 null/unsupported 明示；清单不可读时 total_count 为 null，不以 0 替代 |

`getDatum` 和特征 Quantity 以 FreeCAD 内部 `Value` 返回，八维 `unit_signature` 顺序为长度、质量、时间、电流、温度、物质的量、光强、角度，对应 `mm/kg/s/A/K/mol/cd/deg`。`unit` 按该维度构造，`unit_system:freecad_internal`；维度不可用时单位及体系为 null，不猜单位。Quantity 角度为 deg，不能套用 Placement Rotation.Angle 的 rad。

分页时保存前页返回的 `document_instance_token` 和可用的 `revision`，下一页用 `expectedDocumentInstanceToken`、`expectedRevision` 与 `next_offset` 继续。修订未知时不能强行传 expectedRevision；实例变化、观察到的修订变化或读取期间对象替换会拒绝。分页互验不提供冻结快照，partial 事件修订仍不是完整写入保护。

运行插件必须在 `supported_methods` 及 `read_provenance.methods` 同时声明 `inspect_design`，读取来源能力版本必须为 1；旧插件不降级伪造诊断，返回 `UNSUPPORTED_CAPABILITY`。状态还报告 `design_inspection` 版本、零基约束及预算说明。读取在 GUI 主线程完成，**不求解、不重算、不读取 Shape、不校验几何、不打开外部文件或返回文件路径**，不改变活动文档、选择或模型。App::Link 本身返回 unsupported / `link_source_not_inspected`，不读取其来源设计数据。

本轮新增源码、跨层 schema 和自动化测试已完成；实际安装后的原生读取及新聊天会话的真实 AI 工具调用尚待联验，以 [验证记录](VERIFICATION.md) 后续证据为准。设计诊断没有专属按钮；当前工作台统一轴测Fit及非模态外观入口，文档Dock不再重复快捷区。

## 通用操作 `general_actions`

三个工具均要求目标已是活动文档，并能确认没有未完成事务或编辑状态；不自动激活文档。所有对象先解析和校验，再产生影响，不自动保存。

- `set_selection` 只替换此文档的完整对象选择，保留其他文档选择；不接受 Face/Edge 子元素参数。空数组清除此文档选择。修改失败时尝试恢复原选择，包括原有子元素；恢复也失败时明确返回 `SELECTION_RESTORE_FAILED`。它不修改模型，也不使用文档撤销事务。
- `set_visibility` 要求启用撤销和全部目标存在可用 Visibility。只改变指定对象的显隐；有变化时一笔事务，写后校验，失败撤销，不改 Label 或其他属性。
- `recompute_document` 先核对`recompute_result` v1能力、明确空闲及未冻结状态，再调用一次原生重算。结果分别报告`recompute_invoked`、整数`native_object_count`和完整对象状态；原生完成、几何有效及新鲜度仍为`not_verified`，不再用布尔值推断成功。GUI侧原生返回或后验状态不可确认时返回`RECOMPUTE_UNVERIFIED`；Node能力缺失为`UNSUPPORTED_CAPABILITY`，畸形能力/结果为`INVALID_RESPONSE`，派发后的格式或传输失败均明确提示可能已经执行。GUI侧无效对象为`RECOMPUTE_FAILED`，待处理为`RECOMPUTE_INCOMPLETE`。不保存、不承诺回滚或回调隔离，`idempotentHint:false`且不得因超时重试。完整输出、预算与原生API依据见[重算合同](RECOMPUTE-CONTRACT.md)。

## Phoenix 业务预览与应用

`prepare_part_changes` 复用工作台 Model/Controller，要求明确的空闲活动文档与 1–100 个不同对象。它**模型只读，但会创建内存 token**，因此 `readOnlyHint:true`、`idempotentHint:false`；不是完全无状态的读取。

| `operation` | 差异范围 |
| --- | --- |
| `normalize_names` | 按正式属性预览 Label 命名修正，不改内部 Name 或文件名 |
| `sync_link_labels` | 预览 App::Link 实例与直接来源的 Label 同步，不改变来源 |
| `initialize_properties` | 只初始化缺失的五个正式字符串字段为空，不覆盖已有值 |
| `recommend_properties` | 从明确完整的编号.版本-名称推荐正式值，只补空值并保留现值；歧义不猜测 |
| `set_properties` | 设置明确提供的正式字段，每个对象使用相同字段集，可使用不同值 |
| `set_pad_length` | 一个受支持的首个且最终Pad；明确`body`和mm单位的`lengthMm`，不接受`values`；必须原生人工确认 |

正式字段为 `PartNumber`、`PartVersion`、`TypeCode`、`ModelSeries`、`PartName`，属性组为 `CustomDefine`。属性操作不向 App::Link 来源转发写入。仅 `set_properties` 必须且允许提供 `values`：键必须与 `objects` 精确对应，每个对象至少一个字段，各对象字段集完全相同，不接受额外字段。空字符串可表示明确空值，仍需完整预览；文本及 HTTP 总字节边界均适用。

预览返回 `state:prepared`、`token`、`ttl_seconds:300`、`expires_at`、`can_apply`、`changed_count`、完整 `rows` 及 `requires_confirmation:true`。每行包括 `object`、`field`、`old`、`new` 和 `status`（`change` / `unchanged` / `blocked`），必要时附原因或来源。不可完整、安全显示的值会阻止应用；完整响应超过 128 KiB 返回 `PART_PREVIEW_TOO_LARGE`，未创建 token，不会截断后应用。

调用方先展示完整差异，再用原token请求`request_part_change_approval`。服务建立或复用非模态原生窗口后返回，不等待用户操作；`awaiting_confirmation`不表示已确认或写入。只有窗口的人工确认回调可执行这条链的写入，之后用`get_part_change_status`查询原token；两个新方法仅接受token，额外确认标志拒绝。Node先校验精确`part_approvals` v1与两个方法，旧插件不降级；完整状态合同见[原生确认链](NATIVE-PART-APPROVAL.md)。

完整模式另保留旧五操作的`apply_part_changes`；Pad token无论状态均拒绝此入口。旧操作仅在客户端获得明确授权、token尚未进入原生确认链时直接应用，此入口本身不弹出确认窗口。聊天不注册它，原生待确认token也不能借此应用。两种应用均重新检查同一文档实例、对象快照、编辑状态和Link来源，再经Controller事务写入和核对结果；成功可撤销、不保存。冲突、文档重开或来源改变会拒绝，不能用相同内部Name假定仍为原对象。

直接apply成功返回`state:applied`、`changed_count`、`replayed:false`、`saves_document:false`；原生确认状态另使用`native_confirmation`和`result`，不包含`replayed`。同一token在成功摘要仍保留时返回 `replayed:true` 的**历史成功摘要**，不重新执行，不核验当前对象是否仍保持该状态。用户 undo 后重放也不会 redo；当前状态必须另用 `get_object_info` 核对。

token仅存于桥接模块内存，可确认期五分钟、容量32；请求、复用或查询不续期，已开始的应用不因到期强行中止。终结后释放预览和原生引用，仅保留有界摘要直至淘汰；全部为待处理可用预览时返回`PART_PREVIEW_CAPACITY`。模块重载或重启使记录失效。过期、未知或失败token须先检查对象，再重新预览；不要换token盲目重复应用。应用失败或正在处理的token不再执行。Node不自动重试；超时可能已经处理，应查询原token并核对对象，不把未知状态当成未执行。

## 有类型外观操作 `set_appearance`

必须明确活动文档、1–100 个不同对象及 requestId。`operation` 可为 `random_color`、`set_transparency`，并保留兼容值 `half_transparent`（50%）、`opaque`（0%）。只有 `set_transparency` 需要且允许 `transparency`，为 0–100 整数；0 为不透明，100 为完全透明。参数 JSON 总量最多 20 KiB，固定脚本仍受脚本字符和 HTTP 上限约束。

Node 生成固定的插件调用脚本，经 `execute_script` 复用工作台材质快照、预览/应用规则与单事务；没有用户任意代码字段，不重算、不保存、不修改选择或 Link 来源。复杂 Link 保守拒绝。MCP 调用本身没有原生确认弹窗，使用者须先明确目标和任务授权；当前原生工作台用小型非模态随机色/透明度实时预览，OK提交一次可撤销事务，Cancel/Esc在事务归属有效且没有外部修改时恢复；这不改变MCP单次类型化外观调用的参数与去重合同。

返回脚本任务的真实 `queued/starting/running/done/failed/cancelled` 状态，前三种不表示材质已应用。done 时才验证 document/action/target_count 等操作摘要。超时后使用同一 requestId 查询 `get_script_status`，需要重放时保留完全相同参数与 ID；不要新建 ID 盲目再随机一次。去重仅限同一桥接会话，见下节脚本合同。

## 读取、视图与图像

常用读取工具读取当前 FreeCAD 会话，不重算、不保存、不修改模型、不改变选择或激活文档。返回值经过限量序列化；文件路径属性会隐藏，不是完整模型导出。Quantity 的 `value` 是 FreeCAD 内部单位数值，`unit` 按原生八维 `unit_signature` 给出同一内部体系的表达，并附 `unit_type`、`unit_system:freecad_internal`；长度为 mm，Quantity 角度为 deg，而 Placement 的 Rotation.Angle 另以 rad 返回。未知维度不推测单位。`modified` 等接口无法可靠取得时返回未知值及来源/状态。0.4.0 的三个文档/对象读取提供实例 token 与部分事件修订诊断，仍不能作为任意写入的并发保护。

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

FreeCAD 工作台显示为 **Phoenix Tools**。前三个图标入口分别为只读状态 `PhoenixTools_MCPStatus`、灰色启停 `PhoenixTools_ToggleMCP` 和齿轮 `PhoenixTools_Settings`；菜单和 tooltip 保留文字。手动停止始终确认，确认后再次核对状态。普通按钮独立可用，业务 MCP 通过两个预览/应用工具复用同一规则。设置默认中文，English 保存在本机，确认后立即刷新已有界面；`AutoStartMCP`默认关闭且下次FreeCAD启动生效；业务工具条显示使用`ToolbarButtonStyle=icon_text|icon`，默认图标+文字、保存立即刷新，MCP三控件固定图标；旧 `ConfirmStopMCP` 偏好不再读取或写入，不能关闭手动停止确认，保存不当场启停服务。语言不改变方法名、参数、命令 ID、对象值或正式字段；本轮没有跟随 FreeCAD 语言或业务/原生动态错误的完整翻译。

0.3.1 是分发与界面补丁，沿用 0.3.0 的 21 个工具和协议 v1；历史原生验收不改写为新版本安装结果。

0.5.0 新增的 Desk Tools / 聊天接入是本机 GUI 入口，不是新增 MCP 工具。Desk 使用独立可信 CLI 与状态目录，未把启动、浏览器或下载能力暴露为本桥接方法。后续原生聊天已形成独立专属会话和受控 MCP 连接；它不镜像或接管当前 Codex Desktop 会话。普通命名写入在同一事务内额外复核回调后的目标 Label 和重名集合，派生 Link 来源保守拒绝，继续经原预览/应用合同，不改变协议版本。

原生审批能力`part_approvals` v1与状态DTO、过期/关闭及去重规则见[原生确认链](NATIVE-PART-APPROVAL.md)。预览5分钟限制可确认期；终结后的纯状态摘要在32槽容量内保留，重复查询或请求不重复写入。
