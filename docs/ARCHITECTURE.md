# Phoenix Tools 架构（0.3.2候选）

版本说明：当前统一候选为0.3.2；本页0.4.0–0.10.0均为未发布内部迭代标签，保留当时代码、原生版本及归档事实，不能视为多个正式发行或本轮最终验收。详见[版本约定](VERSIONING.md)。

Phoenix FreeCAD Tools 同时提供独立工作台按钮与聊天工具。Node.js / TypeScript 负责 MCP，Python 负责 FreeCAD 原生执行；普通工作台功能不依赖 MCP 或大型业务服务。

```text
聊天客户端 → MCP stdio → Node 工具/schema → 本机 JSON HTTP
                                             ↓
                                   Python bridge / Qt 调度
                                      ↓              ↓
                            operations / runtime / business
                                      ↓              ↓
                               FreeCAD GUI     共享 Model/Controller

工作台按钮 → View → Controller → Model / FreeCAD 事务
                  Facade 组装与稳定入口
```

## 工作台 MVC

| 层 | 文件 | 职责 |
| --- | --- | --- |
| Model | `pft_workbench_model.py` | 正式 BOM 字段、命名规则、快照和纯校验；不导入 Qt 或 FreeCAD |
| Controller | `pft_workbench_controller.py` | 选择和编辑状态、预览后变化校验、确认后的事务与结果核对；注入 App/Gui，不创建窗口 |
| View | `pft_workbench_views.py` | 中英文弹窗、预填、差异和确认；经 Controller 写入，不直接改模型 |
| Facade | `pft_workbench_tools.py` | 组装依赖，提供普通工具的稳定入口 |
| GUI | `pft_workbench_gui.py` | 注册状态/启停/设置及文档、轴测Fit、外观、普通业务、Desk和AI聊天入口 |
| 引导 | `InitGui.py` | 在普通模块命名空间加载 GUI，兼容 FreeCAD 分离 globals/locals 的入口执行方式 |
| 翻译 | `pft_workbench_i18n.py` | 纯 catalog / `tr`，中文原文作键，格式化前翻译；不导入 FreeCAD 或 Qt |
| 设置 Controller | `pft_workbench_settings.py` | 注入 App，仅读写本插件语言、AutoStartMCP及ToolbarButtonStyle偏好；不访问文档或 GUI |

普通按钮由 Controller 提供当前文档的候选与默认目标，View 表内复选框明确本次对象，不写 GUI 树选择。仅完全无原始选择时推荐零件级节点；有选择但全部不适用时留空。Link 同步只接受实例，属性候选排除 Link；内部特征不自动推荐。旧控制台选择入口仍严格要求树选择，三个 GUI 入口改用显式 document/objects。修改前重新检查上下文和快照；写入使用命名事务，不默认保存。Link 属性转发、Label 表达式依赖及冲突均需明确处理，不能只因 API 未抛异常就报告写入符合预览。0.3.0 的 `prepare_part_changes` / `apply_part_changes` 已复用相同 Model/Controller；MCP 使用明确文档和对象，不借用 GUI 当前选择作为目标。

工作台显示名为 **Phoenix Tools**。`PhoenixTools_MCPStatus` 只读状态圆点，`PhoenixTools_ToggleMCP` 用灰色三角/方块启停，手动停止始终经 View 确认并在确认后重查状态；`PhoenixTools_Settings` 为灰色齿轮和菜单。前三项固定icon-only；全部业务入口用原创图标，默认icon+text，ToolbarButtonStyle可选icon，保存后立即刷新。同一既有状态定时器维护精确自有按钮，菜单/tooltip保留文字，不改全局样式。选中工作台不会反复启动服务。设置默认关闭开机自启；手动停止固定确认，不再读取或写入旧 ConfirmStopMCP 偏好；确认保存语言后刷新现有 actions，不重启服务或改变模型。开机自启仅在后续 FreeCAD GUI 引导时单次延迟尝试，不重启手工停止的服务。

中文为默认，用户可明确选择 English，本机偏好位于本插件专用参数组。界面文本和技术标识分开，数据值不翻译；新打开的业务对话框采用当前语言。本轮不跟随 FreeCAD 语言，动态业务校验/原生错误的完整翻译留待后续，设置界面已说明。

## MCP 与原生执行

| 模块 | 职责 |
| --- | --- |
| `src/server.ts` | 注册完整25/聊天19个工具、严格 schema、标题和副作用声明 |
| `src/adapters/pft-freecad-client.ts` | 本机 HTTP、参数映射、`supported_methods` 检查、响应与 PNG 边界 |
| `src/pft-part-approval.ts` | 原生确认能力与状态schema、token绑定、4 KiB数据体预算及单次请求，无外部确认参数 |
| `src/pft-recompute.ts` | 原生重算计数与完成/几何结论分开；兼容能力门禁、严格结果与一次调用 |
| `src/pft-read-provenance.ts` | 三个文档/对象读取及设计诊断来源schema、实例/修订条件及 128 KiB 预算合同 |
| `pft_document_state.py` | 原生文档观察器、会话实例和部分事件计数、显式来源/未知状态；不读取 Shape 或改模型 |
| `pft_bridge.py` | 协议 v1 白名单、请求校验、状态、Qt GUI 调度和手动启停 |
| `pft_operations.py` | 有限量读取、相机，以及 `general_actions` 选择/显隐/显式重算 |
| `pft_runtime.py` | 显式脚本、会话内去重/状态、有限量结果和三维视图捕获 |
| `pft_business.py` | `phoenix` 业务分组：完整预览、内存token/原生私有确认授权、状态查询、实例/快照/来源复核与共享Controller应用 |
| `pft_part_approval_views.py` | 非模态完整预览、默认取消及人工确认回调；状态定时器仅读内存，不直接读写CAD |

Node 与客户端走 MCP stdio；Node 与插件走本机 JSON HTTP。两种协议不同，端口不提供 Streamable HTTP MCP。离线清单不代表运行时能力，新工具必须由插件明确声明；完整接口见 [协议](PROTOCOL.md)。

常用读取不重算、不保存或修改模型；相机接口改变视角。单面体积不适用，返回 `null`；包围盒仅代表内核包络，不能作精确间隙证据。截图只导出当前三维视图，临时文件读取后清理。显式脚本在 GUI 主线程执行，与 Python 控制台同权限，不是沙箱，不保证异常回滚。脚本状态查询不进 GUI 队列；请求记录属于桥接服务会话，不能跨模块重载或 FreeCAD 重启保证去重。

新通用操作均要求显式空闲活动文档。选择只替换目标文档的完整对象选择；显隐使用可撤销事务；显式重算可能更新缓存，不是只读检查，也不自动重试或保存。

业务预览模型只读但创建内存 token，`idempotentHint:false`。完整 JSON 预览超过 128 KiB 拒绝，不能截断差异后应用；token 可确认期五分钟、容量32；终结后仅保留有界纯状态摘要直至淘汰。应用复核同一文档实例、对象和来源，再调用工作台 Controller。成功 token 的重复请求只返回仍保留的历史摘要，undo 后不会 redo，也不代表对象当前状态；失败、过期或未知记录先核对模型，再重新预览。聊天先预览，再通过原生窗口确认Label/BOM/Link修改；完整模式另保留受明确授权的直接apply，但已进入原生确认链的token不能从外部apply。状态查询不进入GUI队列。有限Pad Length独立typed快照与事务模块复用该确认链；Pad token始终拒绝直接apply，不增加工具名，原生端到端尚待验收。详见[Pad合同](PAD-LENGTH-EDIT.md)。完整合同见[原生确认链](NATIVE-PART-APPROVAL.md)与[协议](PROTOCOL.md)。

## 当前实现与后续封装

examples 提供独立人工参数板、受控 STEP 往返、已知参数重建和 TechDraw 四视图脚本。它们演示流程，不等于通用结构化建模、文件或出图接口；几何、排版、文件重开及图纸审签需要分别验收。

通用脚本覆盖尚未封装的原生 API；当前已封装通用操作和小型 Phoenix 业务，高频建模/出图及文件流程继续分项提炼。业务 token 的快照绑定不等于通用文档修订令牌。0.4.0 三读取的实例/部分事件条件用于拒绝可观察的旧读，不构成任意写入保护；真实原生验收和未知状态见协议与验证记录。

大型 Tool 的下载、编译和启动尚未实现。未来经独立适配层调用正式公共 TS 包或 HTTP 接口，先探测已有实例，服务位置、版本和进程生命周期单独管理。小型正式规则已保留来源声明，见 [NOTICE](../NOTICE)。

## 迭代与验收

每批定义一个可演示闭环，写清读取、相机、模型和文件副作用；先做相关单元/契约测试，再经真实 MCP 检查目标结果。建模核对几何与可编辑性，图纸检查渲染和副本重开，未执行项目保留待验。最终测试数、隐私和包清单确认后再做本地归档，远程发布另行安排。当前事实见 [验证记录](VERIFICATION.md)，后续范围见 [能力计划](CAPABILITY-PLAN.md)。

## 可选连接适配

pft_desk_service 是无 Qt/FreeCAD 的显式配置、参数数组和有限 DTO 校验适配器；路径检查只读文件元数据，不是纯算法。pft_desk_controller 负责自有偏好的原子保存、单次异步动作及旧回调隔离；pft_desk_qprocess 只管理自己创建的 CLI helper，过滤继承环境并限制输出和时间；pft_desk_views 负责配置与状态展示。打开浏览器总是重新执行 status，不隐式 ensure。后台身份、manifest、HTTP 与 Web 核验由用户选定的可信 Desk CLI 完成，Python 不声称再次执行 HTTP 认证。

pft_chat_views 展示原生聊天和工具配置，协议与认证交给独立官方 app-server Controller，不借用 Desk 服务或从模型属性提取账户信息；不读取其他桌面会话的凭据或历史。用户明确连接、登录和发送，官方进程负责自身账户认证。完整边界分别见 Desk 与聊天文档。

pft_desk_prompt 是纯配置提示生成器：以三个有界候选路径和用户目录生成 JSON 数据及固定步骤，不访问文件或宿主。View 仅在用户点击时读取用户目录并写剪贴板；不复用 ready 触发任何保存/进程动作。

## 原生面板与受控聊天

`pft_document_tools.py` 负责内存读取/格式化和显式 Markdown 输出；`pft_document_tools_views.py` 负责对象范围折叠与界面；`pft_visual_tools.py` 负责完整材质快照和单事务外观修改。两面板通过 `pft_panel_host.py` 复用原生 Dock，不占用建模 Tasks。

聊天分为 `pft_chat_profile.py`（专属 profile）、`pft_chat_settings.py`（本机显式路径/偏好）、`pft_chat_controller.py`（无 Qt 协议/状态）、`pft_chat_qprocess.py`（应用拥有的异步子进程）及 `pft_chat_views.py`。QProcess 生命周期不依赖可删除 Dock。

Node 的 `PHOENIX_FREECAD_TOOL_SCOPE=chat` 从注册阶段限制19工具；完整外部客户端默认25工具。`pft-appearance.ts` 接受类型化参数，生成固定 Python 调用原生材质事务，不接受模型提供任意代码。原生桥接仍协议v1，复用现有脚本任务去重与状态查询；Node 工具数与桥接方法数不是一一对应。

0.9.0 的 `pft_design_inspection.py` 独立负责有界只读设计缓存，`pft-design-inspection.ts` 严格核验输入、DTO、未知状态与分页；通过同一文档状态模块取得原生来源和部分事件修订。桥接只路由，不把草图求解或几何重算混入读取。设计诊断不新增专属按钮；当前文档面板移除重复快捷区，轴测Fit与外观统一由工作台命令提供。

0.10.0 模型目录由无Qt Controller分页读取并归一为四个有界字段；用户选择使用model值，实际thread/start响应再次核对。目录计时属于可选元数据，QProcess允许一次性非致命timeout回调；普通RPC/回复仍保持致命超时和进程所有权。旧计时/目录回包由session generation与catalog attempt双层拒绝。字体只由View设置到两个编辑器，持久偏好独立于MCP配置、原子写回并保留旧缺失状态。

`pft_units.py`独立校验八维Signature并格式化FreeCAD内部基底；当前文档与原生MCP序列化器按自身源码位置加载同一纯算法，不依赖Node或显示单位设置。枚举和量值读取边界见[属性读取合同](PROPERTY-READING.md)。

## 当前界面与事务分工

`pft_document_tools_views.py`用归属Dock的400ms定时器只比较ActiveDocument.Name及打开集合，变化才更新跟随范围；固定目标不因其他激活而改选择或重置编辑器滚动。`pft_thumbnail_views.py`负责显式激活、可用状态、冻结图像随视口缩放，以及调用保存框前的输出文件名建议；图像只在明确生成时捕获。

`pft_appearance_controls.py`包含紧凑非模态View与`PftAppearancePreviewSession`。完整材质规则继续由`pft_visual_tools.py`提供；实时预览共用一笔应用事务ID，OK提交、Cancel/Esc在归属与快照仍有效时中止恢复；观察到外部修改或ID变化就停止，不关闭或提交其他事务。不把窗口标题当事务所有权。

`pft_chat_views.py`首行图标通过同安装的受校验facade调用既有工作台能力，gear收纳配置、模型/字号和帮助/诊断；按phase显示便捷连接/登录。布局迁移保留原控件objectName、Controller/进程协议和显式操作，不自动重建已有聊天。两个Dock均用原生ScrollArea，最低宽约300，长文本不传播横向最小宽。


## 可选Desk数据适配

`src/adapters/pft-desk-client.ts`只消费正式Desk CLI状态与固定HTTP领域接口；`src/pft-desk-tools.ts`拥有MCP schema、来源标签、结果总预算和错误映射，`server.ts`仅按full+显配条件注册。普通FreeCAD客户端不承担Desk数据库业务；Desk连接失败不隐式改用脚本、目录扫描或其他后台。

默认完整模式25项、chat19项；同时明确三个Desk路径的full模式为30项，chat还须明确opt-in才为24项。离线清单不探测后台，每次操作再fresh status和身份核对。查询不持久记录输入或结果，批量逐项快照不冒充原子读取；文件去重/引用行/BOM行与实物数量严格分开。接口、副作用和后续同快照批量合同见[能力共享](DESK-MCP-CONTRACT.md)。


## 原生偏好保存

`pft_preferences.py`是无Qt/CAD/文件访问的类型化持久化Controller。先快照选定键的值及存在状态，写入并完整回读，再调用原生`saveParameter('User parameter')`并反验。该API保存整个原生用户参数集，不保存CAD；成功返回不是fsync保证。

工作台和聊天使用同一helper，分别映射本域固定错误。失败只恢复一次完整选定范围，确认原值及缺失键都恢复后才保存恢复结果；恢复或后续持久化不确定时明确报错，不重复覆盖。语言切换仅在持久保存成功后发生，模型和工具配置按新连接/新回合既有规则生效。

`pft-desk-runtime.ts`纯解析并白名单投影版本化运行态观察；连接工具仅显式请求时读取，缓存读取仍每次独立协商，不把观察当授权。缓存薄层另以`pft-desk-cache.ts`复用该解析并校验DTO及固定失败，`pft-desk-http.ts`限制固定读取路由与有界POST/PNG，`pft-desk-cache-reader.ts`组织修订和字节核验；不包含数据库布局/SQL/生成算法。`pft-png.ts`仅处理有界PNG格式/净化，不访问磁盘或CAD。原缓存与净化SHA分开；MCP单图只传一份base64，默认JSON预算和单图预算分别校验。


当前文档属性读取由`read_document_tools(..., property_names=None)`组织：None维持五BOM字符串，显式有界字段清单读取受支持标量，空清单跳过属性读取。纯`validate_property_names`在原生访问前拒绝非法输入，格式化保留缺失/空/0/false/unknown与内部单位；View齿轮仅选择本面板范围，不新增偏好写入或MCP工具。更多属性类型和BOM聚合独立扩展。
