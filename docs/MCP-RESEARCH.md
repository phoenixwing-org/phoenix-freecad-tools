# MCP 实现研究与后续计划

检视日期：**2026-10-25**。本文只读检查官方规范及公开项目的主分支源码，没有安装或运行第三方服务，没有验证其客户端兼容性，也没有复制第三方代码。主分支会继续变化；链接说明本次检视入口，不是已经锁定的依赖版本或运行验收记录。

目标是完善 Phoenix 的上下文、工具契约和执行闭环，不以工具数量或更换实现语言作为完成度。以下里程碑是可调整的计划，不代表已经实现或承诺发布时间。

## 当前已实现的边界

当前基础完整模式有 **25 个 MCP 工具**，受控聊天模式有19个；可选Desk分别增加5项。Node.js / TypeScript 负责协议和参数，Python 负责 FreeCAD 原生执行，Qt 调度负责将原生操作送到 GUI 线程。独立工作台按钮不要求 Node MCP 或 Desk 服务运行。

| 分组 | 已实现工具 |
| --- | --- |
| 连接与清单 | `get_capabilities`、`get_connection_status`、`list_documents` |
| 设计上下文 | `get_document_info`、`list_objects`、`get_object_info`、`get_selection` |
| 几何与诊断 | `measure_geometry`、`inspect_design` |
| 图纸读取 | `list_drawing_pages`、`inspect_drawing_page` |
| 相机 | `fit_view`、`set_axonometric_view`、`set_view` |
| 通用操作 | `set_selection`、`set_visibility`、`recompute_document` |
| Phoenix 业务 | `prepare_part_changes`、`request_part_change_approval`、`get_part_change_status`；完整模式另有`apply_part_changes` |
| 外观 | `set_appearance` |
| 图像 | `capture_view` |
| 高级脚本 | `execute_script`、`get_script_status` |

现有读取已有限量序列化与分页；业务预览有完整 JSON 的 128 KiB 上限、五分钟有效期、最多 32 个内存 token、实例和快照复核，以及调用共享 Controller 的撤销事务。Label/BOM/Link修改先完整预览，再由原生窗口人工确认；状态查询不读取CAD；有限Pad Length源码复用同一链且始终拒绝直接apply，范围见[合同](PAD-LENGTH-EDIT.md)，原生端到端验收待完成，见[原生确认链](NATIVE-PART-APPROVAL.md)。脚本请求有会话内去重与状态查询。以上并不等于所有结果已有输出 schema、统一上下文快照、MCP resources/prompts、通用任务系统或结构化图纸导出接口。

通用读取不隐式重算或保存。显式脚本与 FreeCAD Python 控制台同权限，不是沙箱，不承诺异常自动回滚。TechDraw、STEP 和参数化零件的公开脚本案例，也不等于所有相关功能已成为独立结构化工具。实际能力与验收以 [协议](PROTOCOL.md)、[架构](ARCHITECTURE.md) 和 [验证记录](VERIFICATION.md) 为准。

## 五项 Python / TypeScript 源码对照

下表中的“可采纳”都是设计建议，尚未在 Phoenix 中实现或验收。

| 项目与实际检视文件 | 源码可观察到的实现 | 可采纳 | 不照搬及差异 |
| --- | --- | --- | --- |
| **官方 Python SDK**：[server.py](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/mcpserver/server.py)、[func_metadata.py](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/mcpserver/utilities/func_metadata.py)、[context.py](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/mcpserver/context.py)、[tools/base.py](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/mcpserver/tools/base.py) | 当前主分支提供 `MCPServer`、资源模板、prompts、返回模型和 progress。返回模型参与成功结果的结构校验；只有手工 schema、没有输出模型时，不应假设自动验证。同步函数通过线程执行。 | 稳定输出模型、显式上下文资源、少量流程 prompts、进度消息。 | SDK 不负责 FreeCAD 事务、上下文字节预算或任务恢复。同步 handler 的工作线程不能直接操作 FreeCAD GUI；保留现有 Qt 队列。没有为了采用 Python SDK 而替换当前 Node 协议层的计划。 |
| **官方 TypeScript SDK**：[mcp.ts](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/packages/server/src/server/mcp.ts)、[项目说明](https://github.com/modelcontextprotocol/typescript-sdk) | 高层服务注册工具、资源和 prompts；工具配置了 `outputSchema` 时，会验证成功结果的 `structuredContent`，错误结果不按成功 schema 校验。当前主分支采用拆分的 v2 包。 | 为现有工具逐批增加输出 schema、统一成功与失败合同，并用真实 stdio 客户端做契约测试。 | 主分支能力不等于本项目锁定版本已具备同样行为；以安装依赖和测试为准。不能把 annotations 或类型声明当成运行时权限检查。 |
| **Python Blender MCP**：[server.py](https://github.com/ahujasid/blender-mcp/blob/main/src/blender_mcp/server.py)、[safe_mode.py](https://github.com/ahujasid/blender-mcp/blob/main/src/blender_mcp/safe_mode.py)、[addon.py](https://github.com/ahujasid/blender-mcp/blob/main/addon.py) | 对象、材质、集合资源和资源链接；截图序号与场景核对；生成请求键、未完成任务句柄、progress；socket 串行及超时处理。部分接口有结构化错误，部分仍返回普通错误字符串。 | 对象资源链接、图像与采集场景绑定、长任务重查而不重做。 | 可选 AST 白名单只覆盖特定执行入口，不是进程沙箱。Blender 场景操作不等于 FreeCAD 的文档事务、表达式引用和 Link 转发语义；不复制其业务工具或混合错误格式。 |
| **TypeScript Playwright MCP**：[源码位置说明](https://github.com/microsoft/playwright-mcp/blob/main/src/README.md)、主仓库中的 [tools.ts](https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/tools/backend/tools.ts)、[snapshot.ts](https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/tools/backend/snapshot.ts)、[find.ts](https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/tools/backend/find.ts)、[response.ts](https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/tools/backend/response.ts)、[config.d.ts](https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/tools/mcp/config.d.ts) | 按能力筛选工具；快照可指定目标和深度；搜索返回周边片段；图像可选择是否返回；响应区分结果和错误，并管理产物文件。配置区分操作和导航超时。 | 先获取紧凑上下文，再搜索、展开目标；少量明确分组；按需图像；将上下文范围和输出大小做成可观察合同。 | `outputMaxSize` 对应产物磁盘清理，不能当成模型响应字节上限。浏览器点击、导航和取消不具有 CAD 撤销事务语义。配置中的文件访问护栏也不是通用安全沙箱。 |
| **TypeScript 官方 Filesystem Server**：[server.ts](https://github.com/modelcontextprotocol/servers/blob/main/src/filesystem/server.ts)、[lib.ts](https://github.com/modelcontextprotocol/servers/blob/main/src/filesystem/lib.ts)、[index.ts](https://github.com/modelcontextprotocol/servers/blob/main/src/filesystem/index.ts) | 显式目录白名单、路径校验、局部读取、dry-run 差异、输出 schema；入口可服务新旧协议客户端。 | 后续导出目录范围、真实路径检查、覆盖保护；读取先筛选再展开；保留预览与应用的区别。 | 文件 dry-run 不绑定一次性预览 token，也不等于 FreeCAD 的快照复核和事务撤销。不能把整个文件管理器权限或全文输出方式直接搬进工作台。 |

## 按能力取舍

### 可观察上下文、分页与裁剪

建议增加一次 GUI 调度内采集的 `get_document_context`：明确文档和对象范围，返回必要的树结构、选择、类型、依赖摘要和编辑状态，支持树深、字段筛选及分页。保留 `unknown`，不伪造文档修订号，也不把一次读取摘要当任意写入的并发令牌。

读取结果应有明确字节和项目预算，返回是否裁剪及继续读取方式。超长读取可以分页；需要人工确认的修改预览必须完整，超限拒绝，不能裁掉差异后继续应用。截图仍按明确请求返回，后续可增加采集标识及文档绑定，防止把旧图当当前模型。

### 工具分组、资源与 prompts

继续保留连接、读取、相机、通用操作、Phoenix 业务、图像和高级脚本的分组。后续若提供工具配置档，在启动时确定并公开实际清单；不能仅修改标题或 annotations 却让未开放动作仍可执行。

上下文采集成熟后，可复用为 `phoenix://documents/{name}/context` 等只读资源模板，使用内部标识，不暴露真实文件路径。资源由客户端选择和使用，不能假设所有聊天客户端都会自动载入。prompts 可提供“检查后修改”“规范预览”“出图前核对”等步骤，但不自动授权写入、保存或导出。

### 输出 schema 与错误

为现有工具分批定义成功结果模型。协议错误、工具执行错误、业务冲突、原生状态未知应有明确区别；工具失败使用一致的 `isError` 和机器错误码，不以普通成功字符串承载错误。schema 验证不能替代原生写后核对，也不能证明几何或工程图质量。

### 进度、长任务和重试

继续区分排队取消与已经开始的执行。GUI 原生求解开始后，客户端超时不等于工作停止，也不能立即用新请求重做。未来长任务应明确请求键、任务状态、输出页、保留期限及恢复边界；重查同一请求只返回状态或已有结果。

进度只能报告实际阶段和可测数量，不能编造百分比或预计完成时间。FreeCAD GUI 不能通过强杀线程安全取消；任务取消必须按所用原生 API 的能力说明。任意脚本不因增加 job 句柄就具有自动回滚保证。

### 权限、Qt 与 CAD 事务

保留本机地址和请求来源限制、严格参数白名单、明确活动文档、编辑与事务守卫。业务修改继续走共享 Model/Controller，预览后复核文档实例、对象、属性和 Link 来源；写后核对实际结果，再确认事务成功。

所有 FreeCAD 原生访问继续在受控 GUI 调度中执行。SDK 的异步接口、线程池和取消信号不能替换 Qt 线程约束。文件导出和外部服务调用也不在 FreeCAD 文档撤销事务内，必须单独说明副作用、目标路径和失败状态。

## 官方新规范：先验证兼容，暂不升级

检视当日，[官方 latest](https://modelcontextprotocol.io/specification/latest) 指向 **2026-07-28**。[变更说明](https://modelcontextprotocol.io/specification/2026-07-28/changelog) 包括逐请求协议与能力信息、`server/discover`、显式状态句柄，以及资源缓存合同；Tasks 已移为可选扩展。不能把旧版初始化或 Tasks 示例当成最新版合同。

[工具规范](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) 说明状态可通过显式句柄传递，并应明确寿命和过期错误；[资源规范](https://modelcontextprotocol.io/specification/2026-07-28/server/resources) 说明资源与模板；[Tasks 扩展](https://modelcontextprotocol.io/extensions/tasks/overview) 要求双方明确支持，取消是协作式。当前内存预览和脚本记录不能直接宣传为跨进程重启可恢复的持久任务。

本计划**不要求现在升级 SDK 或协议**。先锁定当前依赖、保留可工作的 stdio 链路，补客户端发现、新旧协议和错误合同测试，再单独评估迁移。MCP 协议版本与本机 FreeCAD JSON HTTP 的 `protocol_version:1` 是两层合同，不混用。

## 可调整的迭代里程碑

| 计划版本/阶段 | 建议范围 | 结束条件 |
| --- | --- | --- |
| **0.3.2：输出合同与预算** | 先为高频读取及业务 prepare/apply 补输出 schema；统一错误、返回预算和裁剪说明；长输出分页设计；增加新旧客户端兼容测试，不以升级协议为前提。 | 参数、成功输出、原生失败和超限都有契约测试；真实 MCP 抽样核验；不改变既有模型/文件副作用。 |
| **0.4：上下文与只读诊断** | 聚合文档上下文、范围/深度/筛选/分页；只读文档错误、依赖、草图约束及 BOM/Link 规范检查；成熟后复用为资源模板和少量流程 prompts。 | 同一次 GUI 采集边界清楚；大文档预算可测；检查不隐式求解、修复、保存；真实公开样例通过。 |
| **Desk 服务可选集成** | 先确定真正需要复用的业务，探测已有服务和版本，经公开 TS 包或 HTTP 适配层调用；服务地址和进程生命周期独立管理。 | 没有 Desk 时普通工作台和现有 MCP 仍可使用；缺服务、超时及版本不兼容有明确结果；不重复实现大型业务算法。 |
| **后续：受控图纸与导出** | 从公开 TechDraw/STEP 案例提炼有限工具；明确模板、源对象、单位、输出目录、覆盖策略和产物清单；按需要引入可恢复长任务。 | 导出前可审阅目标及副作用；写后检查实际文件；图纸经渲染和副本重开核验；不自动保存生产文档，不把文件输出宣称为可撤销文档事务。 |

每批只做一个可演示闭环：先定义读取与写入边界，测试相关失败路径，再通过真实 MCP 对照原生结果。第三方代码中可见某项能力，不代表已适配 Phoenix，也不代替本项目的 FreeCAD 实测。
