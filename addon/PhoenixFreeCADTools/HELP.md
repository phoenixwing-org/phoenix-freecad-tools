# Phoenix Tools 帮助

Phoenix Tools 是 FreeCAD 工作台、原生 AI 聊天入口及可选 MCP/Desk 连接工具的集合。当前源码候选版本为 **0.4.1（待发布）**，最后已发布版本仍为 **0.3.2**；版本号不代表已经正式发布或完成所有平台验收。本文件随 Addon 分发，也是仓库内整个工具集的规范帮助文档。

常用入口位于 **工具 → Phoenix**，可打开聊天、当前文档工具、设置、Desk及帮助。Phoenix工作台的一级菜单默认隐藏，可在设置中开启 **显示 Phoenix 一级菜单**；开发界面重载命令合并在Phoenix菜单中。聊天内的文档、轴测Fit和外观图标复用同一组FreeCAD原生命令。外观预览保留自己的快照和事务，确定提交，取消或Esc恢复，不与聊天共享预览状态。

从 Phoenix Tools 菜单或主工具条点击 **帮助**，可离线阅读、复制和搜索本文。再次点击会显示同一个非模态窗口。代码和 JSON 示例仅供阅读，不会执行；内嵌图片和其他资源不加载，外部 HTTPS 链接仅在你明确点击后由系统浏览器打开。

本文描述当前源码的功能与限制。合成测试验证合同、状态、输入与异常分支；原生 GUI、实际投影、排版、撤销/重做、文件输出和目标平台兼容性需要分别验收。以实际连接声明、工具返回值、原生界面和候选的验证记录判断当前可用性。

未发布候选增加三个图纸PDF导出工具，基础完整/聊天为28/22；正式0.3.2安装包仍为25/19。macOS FreeCAD1.1.4受限单页已通过正式MCP和原生确认链自动验收，接口存在不等于任意图纸可保真导出。

## 从哪里开始

1. 在 FreeCAD 选择 **Phoenix Tools** 工作台。当前文档、视图、外观、命名、链接和零件属性可直接使用，无需 Node、MCP 或聊天登录。
2. 需要 AI 操作 FreeCAD 时，先检查目标文档，再启动工作台的本机 MCP 桥接；另行配置 Node MCP 和聊天客户端。
3. 使用内置 **AI 聊天** 时，在齿轮中配置执行文件与工具选项，明确点击连接；未登录时按界面提示使用 ChatGPT 登录。打开面板和帮助均不自动连接或登录。
4. **Desk Tools** 是可选连接。先配置兼容的已构建后台，再检查或明确启动；它的数据与 FreeCAD 当前未保存的内存文档分别报告。

## MCP 连接设置

在工作台齿轮“设置”中点击 **MCP 连接设置…**，可查看当前用户共享的配置位置、保存首选桥接端口，并明确勾选“自动发现本机 FreeCAD 实例”。此窗口不修改 CAD，也不启停或重连服务。配置缺失时默认直连9876，原有v1按保存的首选端口直连且保持发现关闭；打开、取消、Esc和关闭不创建或改写文件。保存只影响下一次普通桥接启动和下一次Node连接，现有连接保持原目标。

开启发现保存严格v2：`{"schema_version":2,"bridge":{"preferred_port":9876},"discovery":{"enabled":true}}`。旧v1不自动迁移；已有v2取消勾选后保留v2的false。保存使用原读取etag，外部更改、损坏、未知版本或不安全权限均拒绝，不自动修复；写后结果不确定时先重新检查，不重复保存。

Node 显式 `PHOENIX_FREECAD_URL` 固定直连优先，并且不读取共享配置或实例登记；显式FreeCAD端口和独立开发端口也保持其选择。发现开启后，普通桥接使用动态回环端口，完整模式通过 `list_freecad_instances {}` 列出登记并握手的实例，再以 `select_freecad_instance {instanceId,serviceId}` 明确选择。初次仅有一个ready实例可自动使用，多实例不猜窗口；绑定丢失或服务重启后不自动换到其他实例。同步调用期间拒绝切换，不扫描端口、不启动或接管未知进程。

发现只在完整模式增加两个工具，共30项；可选Desk后35项。聊天22项与专属worker4项保持固定连接；无显式URL却要求发现时拒绝。请求ID和预览token绑定原实例与服务，切换不迁移或取消原生确认，不自动重发未知结果。列表来源为登记和状态握手，不表示读取了CAD。

macOS登记位于用户级`PhoenixTools/mcp/runtime/instances/`，Linux按安全的XDG运行/状态目录规则；损坏或不安全登记停止发现，不换目录规避。Windows尚无真实ACL适配器，已有共享配置读取/保存及发现读写明确拒绝；配置缺失仍可默认直连或使用明确URL。本轮已在macOS独立双实例通过正式SDK和FreeCAD原生控件自动化；实际用户手工、Linux、Windows及平台安装仍须分别验收。完整路径与合同见[多实例连接](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/master/docs/INSTANCE-ROUTING.md)。

## 当前文档与视图

**当前文档工具**读取当前内存文档的信息、对象候选、缓存包围尺寸和选定属性。可切换已打开文档、勾选目标、选择属性字段、复制文本，并在预览后明确导出 Markdown。打开或刷新面板不打开磁盘 CAD、不重算、不创建属性、不保存模型，也不改变选择。

尺寸来自对象报告的 `Shape.BoundBox`，不是重新计算的制造尺寸或精确间隙；不额外叠加 Placement，不展开装配世界变换。Link 仅报告实例自身可观察内容。属性显示区分缺失、空字符串、0、false 和未知值，不把未知值补零；默认五字段 BOM 字符串，也可明确选择受支持的标量、枚举、量值、原始向量和字符串/整数/浮点/布尔/向量列表。受限整数/浮点、Percent、Precision及QuantityConstraint可显式读取当前值；不展开约束边界、不把Precision猜成mm。向量仅读原始xyz：偏移/位置采用类型声明的内部mm，普通向量单位和坐标系未声明，不应用装配变换。Rotation/Placement可显式读取原生xyzw四元数与Base平移，PlacementList逐项读取同样的位姿；平移内部mm、旋转无量纲，保留列表次序、重复和空列表。不相乘、不换算坐标或归一化，完整来源随冻结值显示。列表最多100项、完整值2 KiB；任何坏成员或超限均不返回部分值，不展开引用或任意嵌套对象。

缩略图应按界面明确操作获取，图像可能包含模型内容。当前三维视图图像与 Desk 的磁盘缓存缩略图具有不同来源，不能相互当成几何新鲜证明。

**轴测并适合视图**只改变当前三维相机；标准视图和 Fit 同样不保存模型。图纸页不是三维视图，不能把相机切换当成生成 2D 图纸。

## 配色与透明度

聊天与当前文档面板顶部均有紧凑图标栏：文档工具、轴测并适合窗口、对象外观。图标悬停显示名称；文档面板右侧齿轮打开读取属性设置，聊天面板右侧齿轮打开聊天与工具设置。

**对象外观**对明确的完整对象预览随机颜色和 0–100% 透明度：0 为不透明，100 为完全透明。优先采用完整对象选择；没有选择时使用当前文档中推荐的可见外层对象，不把面/边选择或不适用选择扩大成整个文档。

随机配色右侧的立方体加号图标一键完成轴测与 Fit，只改变当前三维相机，可在外观预览期间使用。随机色在本次预览中固定。滑条、数值和预设实时预览；点 **确定**保留，**取消**、Esc 或关闭恢复。写入使用原生材质快照与受控事务，可撤销，不重算或保存，不修改 Link 来源。复杂 Link、未知材质或外部事务冲突会拒绝或停止，不自动处理其他人的事务。Body 与 Tip 的显示关系需以原生视图检查，不能只看属性值推断渲染结果。

MCP 的 `set_appearance`直接提交其明确目标的外观事务，与本地可取消的预览窗不同。必须提供 `requestId`；返回 queued、starting 或 running 时，继续查询原 ID 的 `get_script_status`，不能换 ID 重复写入。 直接应用绑定原生返回的事务ID并阻止同计划重入；归属变化或提交结果不明时停止，不自动提交／取消其它事务。此检查不构成任意原生回调隔离。

## 命名、链接与零件属性

- **命名修正**按五字段中的编号、版本及名称规则预览目标 `Label`；内部 `Name` 是工具引用的身份，不随显示名称修正而替换。
- **链接同步**预览所选 `App::Link` 与直接来源的名称差异，确认后仅修改链接实例的 `Label`。来源不可读或链接关系不在支持范围时拒绝，不修改来源模型。
- **零件属性**编辑 `PartNumber`、`PartVersion`、`TypeCode`、`ModelSeries`、`PartName` 五个 BOM 字符串字段。初始化、推荐与明确赋值均先预览，冲突会显示，确认后才写入。

这些 GUI 工具使用明确目标和可撤销事务，不自动保存。MCP/聊天流程先返回**全部对象、字段、旧值、新值和冲突**；适用时再打开 FreeCAD 原生确认窗口，由你检查完整差异并亲自点击确认。AI 的文字确认、客户端执行审批以及 `awaiting_confirmation` 都不代表写入成功。

预览 token 仅在当前服务内存中保存，通常有效 5 分钟。用原 token 查询 `get_part_change_status`；`applied` 的结果才报告实际修改。超时先查状态，不新建请求绕过未知结果。取消、关闭、Esc、过期或目标变化均不授予确认；已进入原生确认链的 token 不能通过直接 apply 确认。

## 有限 Pad 修改与基础图纸草稿

`prepare_part_changes` 的 `set_pad_length` 是有限的类型化操作：明确指定一个 Pad、所属 Body 和 `lengthMm`（0.01–10000 毫米）。当前限单打开文档、Body 中首个且最终的单侧固定 Length Pad；复杂特征、表达式、未知依赖或不支持状态会拒绝。完整预览后必须原生人工确认，确认后重算并反验几何；不自动保存，不承诺隔离所有原生回调。

```json
{"document":"ExampleDocument","objects":["ExamplePad"],"operation":"set_pad_length","body":"ExampleBody","lengthMm":12}
```

`create_drawing` 也是同一预览工具的操作，不是额外增加的 MCP 工具名。明确指定一个原生 `Part::Feature` 或 `PartDesign::Body` 的已完成有效单实体缓存。`Part::Box`、Link、Python 代理、复杂已有图纸或状态未知的来源不自动转换为支持对象。

```json
{"document":"ExampleDocument","objects":["ExampleSource"],"operation":"create_drawing"}
```

首版固定 **A4 横向、第一角投影、主视图＋下方俯视图＋轴测图**，从来源包围盒选择保守比例。预览列出页面、内置模板和三视图共五个节点，确认前不创建节点；不接受外部模板路径。来源、Body Tip、模板内容、对象集合或预定名称改变时拒绝，不覆盖已有对象。支持最多16层原生App::Part父容器，父身份、隶属和变换一并校验；有父时在Shape副本替换为全局Placement估算比例，不重复叠乘或修改来源。新视图明确使用精确投影。

未发布开发候选另协商`drawing_draft_layout`能力：新客户端默认取得三视图中心、共同模型比例、估算几何框及固定纸面标注带的完整计划图。该图来自来源全局包围盒，不是原生渲染，也没有尺寸；留白不保证任意尺寸批次有解。原调用方未协商时保留原预览和几何草稿流程。新草稿与后续尺寸分别预览、分别原生确认，不自动移动旧图页。独立Part/Body的默认新草稿到五类尺寸已有限验收，仍须人工检查投影、排版和尺寸完整性。

确认后只在一个可撤销事务中新增五个节点，并受限重算新增对象。`drawing_draft`、`projection_verification="not_checked"`、`manual_review_required=true` 明确表示**创建了草稿节点**；原生异步投影、首次打开后的布局、引用和撤销/重做还需检查。用 `inspect_drawing_page` 回读页面并在 FreeCAD 打开图页核对。创建草稿不会自动添加尺寸、孔槽/螺纹、公差、材料或表面处理，不导出 PDF/SVG，也不保存 CAD；不能把它当作尺寸齐全或已经审签的生产图纸。

请求原生确认与查询均只传预览返回的原 token：

```json
{"token":"RETURNED_PREVIEW_TOKEN"}
```

### 关联尺寸

`inspect_drawing_page`明确传`view`可读取该页原生投影边窗口（offset从0、limit最多50、最大Edge2047），以及已有受支持尺寸的`native_measurement`缓存mm量值。**TechDraw Edge0有效**，不能用三维Shape边号或过滤后的可见边次序猜引用；读取不重算，仍不证明投影已更新。

`prepare_part_changes`的`add_drawing_dimensions`接收明确document/page/view及1–8项`{edge,kind,xMm,yMm}`。kind为Distance/DistanceX/DistanceY/Diameter/Radius，只接受实体投影直线和闭圆，化妆边、中心线、额外XSource及复杂来源拒绝。偏移是相对视图的纸面mm，最多±1000；从真实读取选择Edge，不接受外部量值或格式覆盖。完整预览后必须原生窗口人工确认；只新增尺寸、局部重算该页和新节点，可撤销，不保存CAD。

未发布开发候选还可明确使用`{edge,kind,placement:"auto"}`，与手动行混合；先核对实际连接的`drawing_dimension_layout`能力，正式0.3.2不支持。它只建议位置，不选择边或替代引用。完整预览含估算图、固定障碍和全部解出坐标，仍由原生窗口确认；任一项无解整批不写入，不能把`design_request:null`当成默认坐标。未知图框/单位/样式或变化后的计划会拒绝，不移动已有视图、旧标注或修改偏好。有限Part/Body已通过正式SDK到原生确认的五类auto/混合写入及单次撤销重做，默认新草稿链另有专项验收；这些有限正例不表示任意图纸、制造完整性或官方模型聊天出图已验。

成功结果逐项回读真实关联及原生量值；图纸Scale不改变raw mm，斜视投影数值也不保证等于三维制造尺寸。显式读取现有尺寸才能观察后续变化，旧token结果是过去应用记录。异步投影、布局、单位显示、两位小数精度、公差及尺寸完整性须人工复核；不自动导出。详见[关联尺寸合同](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/master/docs/TECHDRAW-DIMENSIONS.md)。

### 图纸 PDF 导出（未发布候选）

`prepare_drawing_export`要求明确document/page/format为`pdf`及requestId，不打开图纸、不捕获或写用户文件。`request_drawing_export_approval`只接受原token，显示原生面板；点击捕获后才允许打开图页、刷新并生成私有暂存PDF。核对同一份冻结字节的预览后，在异步文件框选择路径，最后点击发布。默认路径为关联已保存CAD的目录及文件名stem＋`.pdf`；未保存CAD只建议文件名。AI不能提供路径、确认布尔值或覆盖授权。

仅候选明确覆盖的原生单页、1–3个同来源视图及有限关联尺寸可尝试；未覆盖对象、资源或平台明确拒绝，不自动转换、安装依赖或保存CAD。捕获会触发原生页面刷新，不能当作只读查询。投影完成与尺寸完整性仍须人工复核。`get_drawing_export_status`只读内存历史，分别查看capture/publication/cleanup/CAD保护；结果未知时先查原token，不重新捕获或自动重试。发布成功不证明目标文件现在仍存在，CAD撤销不会回滚导出文件。macOS受限单页已通过原生链自动验收；系统文件框的用户手工操作、更多图纸和平台仍待验，见[实施合同](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/master/docs/TECHDRAW-EXPORT-PLAN.md)。

## MCP 基础清单：完整 28 项，聊天 22 项

下面是 Node 服务的基础机器工具名。**聊天**列为“是”的 22 项构成受控聊天清单；普通完整客户端有全部 28 项。明确开启发现的完整模式另注册列表和选择两项，共30项，聊天与worker不扩充。注册了工具不代表运行中的 Addon 支持该方法：先调用 `get_capabilities`，再检查 `get_connection_status` 的 `supported_methods` 及对应能力版本。

| 机器工具名 | 聊天 | 用途与主要边界 |
| --- | --- | --- |
| `get_capabilities` | 是 | 离线查看 Node 注册清单、范围与合同；不探测 CAD 或证明原生可用。 |
| `get_connection_status` | 是 | 检查桥接版本、协议、GUI 调度及真实支持的方法。 |
| `list_documents` | 是 | 列出当前已打开文档内部 Name，包含未保存文档；不扫描磁盘。 |
| `fit_view` | 是 | 指定文档三维相机适合窗口。 |
| `set_axonometric_view` | 是 | 轴测图，可选同时 Fit；不生成图纸。 |
| `get_document_info` | 是 | 读取会话状态、原生活动上下文和 Body Tip；不返回本机文件路径。 |
| `list_objects` | 是 | 按内部 Name 分页，可按准确 TypeId 筛选；不重算。 |
| `get_object_info` | 是 | 读取缓存状态及最多 20 个明确属性，不执行表达式或任意代码。 |
| `get_selection` | 是 | 读取对象和子元素选择，不改变选择。 |
| `measure_geometry` | 是 | 测量现有对象或 FaceN/EdgeN/VertexN 缓存几何，不修复或重算。 |
| `inspect_design` | 是 | 诊断明确对象的 Body Tip、特征参数、表达式、草图自由度和约束分页，不求解。 |
| `list_drawing_pages` | 是 | 列出现有 TechDraw 页，不生成或更新。 |
| `inspect_drawing_page` | 是 | 查看模板/标注；明确view时读投影Edge0窗口和支持的尺寸缓存量值，不重算或保证新鲜。 |
| `set_view` | 是 | 前/后/左/右/上/下/轴测标准相机，可选 Fit。 |
| `set_appearance` | 是 | 明确文档、完整对象及 requestId，随机色或透明度；事务可撤销，不保存。 |
| `capture_view` | 否 | 仅明确请求图像时导出三维视图 PNG；不截桌面，可能包含模型信息。 |
| `execute_script` | 否 | 高级 Python，与 FreeCAD 控制台同权限，可写模型和文件；不是只读接口。 |
| `get_script_status` | 是 | 查询同一桥接服务会话的原 requestId，不重新执行。 |
| `set_selection` | 否 | 替换明确活动文档的对象选择，空列表清除该文档选择。 |
| `set_visibility` | 否 | 对明确对象统一显隐，事务可撤销，不保存。 |
| `recompute_document` | 否 | 显式执行原生求解，可能改变缓存；返回对象数不证明几何有效或新鲜。 |
| `prepare_part_changes` | 是 | 完整预览命名、链接、五字段属性、有限Pad、A4草稿或关联尺寸，不在预览阶段修改。 |
| `request_part_change_approval` | 是 | 用原 token 打开/复用原生确认窗口，请求不等于授予确认。 |
| `get_part_change_status` | 是 | 查询原 token 的内存状态及实际结果，不打开窗口或应用。 |
| `apply_part_changes` | 否 | 仅原五类零件业务可在未进入确认链时写入，或重放其保留的成功摘要；Pad、草稿与关联尺寸始终拒绝此入口，含成功重放。 |
| `prepare_drawing_export` | 是 | 明确文档、页面、PDF和requestId，准备独立内存计划；不打开图页或捕获。 |
| `request_drawing_export_approval` | 是 | 用原token打开/复用原生导出面板，捕获、选择路径和发布须由用户逐步确认。 |
| `get_drawing_export_status` | 是 | 查询独立导出内存状态，分别报告捕获、发布、清理及CAD保护，不读取目标文件。 |

文档与对象参数使用内部 Name，不能用 Label 猜测。示例为读取一个已打开的中性文档：

```json
{"document":"ExampleDocument","object":"ExampleSource","properties":["Label","PartNumber"]}
```

部分读取接口返回文档实例 token 与观察到的事件修订，可帮助识别同名重开或部分变化；它们不是冻结快照、完整写入保护或缓存几何新鲜证明。未知值保留 null。高级脚本去重仅限当前桥接模块内存，重载模块或重启FreeCAD会改变 `script_session_id`；监听重新启动另有新的 `service_id`，不接管旧服务的请求。失败或超时不会自动承诺回滚。

## 可选 Desk：完整 33 项／发现 35 项，聊天 27 项

提供完整可信 Desk 配置后，完整 MCP 增加下列五个只读数据工具；普通完整模式共33项，发现完整模式共35项。聊天还须明确启用 Desk 数据工具，才从 22 项增加为 27 项；不会自动发现最近工作空间或启动后台。

| 机器工具名 | 用途与主要边界 |
| --- | --- |
| `desk_get_connection_status` | 检查已运行 Desk；可显式请求运行能力观察，不执行 Native 预检或生成。 |
| `desk_search_index` | 搜索明确工作空间的已有文件索引；不扫描 CAD。 |
| `desk_query_references` | 读取明确相对 FCStd 路径的已有引用索引；计数不是实物数量。 |
| `desk_query_cached_metadata` | 读取已有尺寸/缩略图元数据，需 `cad.cache.read` v1；缺失或过期不生成。 |
| `desk_get_cached_thumbnail` | 明确请求图像时按精确 revision 读取既有 PNG；不打开 CAD，不生成或自动换版本。 |

Desk 数据来自已保存索引/缓存，不等于当前 FreeCAD 未保存内存，不证明全部依赖新鲜。读取可能短暂使用协调租约，失败不清除未知锁。Desk GUI 的配置、检查、启动与打开 Web 页面都是显式操作，MCP 这五项只查询已存在的数据。

## 聊天、登录与状态

内置聊天使用独立配置与官方 app-server 子进程。点击连接才创建本窗口拥有的会话；未登录时点击 **使用 ChatGPT 登录**，取得有效官方链接后才请求系统浏览器。登录等待期间可重开或复制该次链接，完成后收起。插件不复制桌面客户端的凭据、cookies 或聊天历史；桌面已登录不代表本插件的独立配置已登录。

新聊天连接会检查专属进程登记；已有或状态不明的登记会阻止重复启动，不因重启应用、父进程退出或登记时间久而接管。正常自有子进程退出后释放。异常退出后，可在齿轮设置点“检查聊天进程登记”；只有有效登记中的PID已不存在时才允许默认取消的本机确认清理，清理前再次核对原登记和PID。取消、Esc、连接变化或状态不明均不清理；成功后由用户手动连接，不自动启动或重发消息。该锁只覆盖采用新协议的客户端，旧版残留与跨平台边界见[聊天操作](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/master/docs/CHAT-USAGE.md#专属进程登记)。

纯聊天不注册 MCP；启用 FreeCAD 工具后核对准确清单与连接状态，默认 22 项，明确启用 Desk 时 27 项。工具模式需要 Node 22+、已构建 MCP CLI 与运行中的本机桥接。当前候选明确支持 Codex CLI **0.160.0** 和 **0.167.0-alpha.3.2**，连接检查继续拒绝其它版本，不按版本号大小推断兼容；模型列表与权限由当前官方账户返回，不保证固定模型可用。

桥接绿色状态只说明 FreeCAD 本机服务在运行，不证明 Node、工具模式、聊天登录或模型请求成功。消息显示时间与请求耗时，工具区显示实际调用状态；助手文字不能代替应用结果。Enter 发送，Shift+Enter 换行；输入法确认候选不应作为重复发送。停止请求中断，不自动重发。

打开帮助、齿轮、切换字号或 CAD 快捷按钮不自动重建聊天。关闭/断开仅处理本窗口创建的子进程，不停止 Desk 或 FreeCAD 桥接。默认临时聊天；齿轮中的“保留新聊天”和“读取本插件保存的聊天”提供插件自有会话的候选恢复入口，真实官方服务恢复仍待验收。只列本插件登记，由用户选择并连接；完整终态及模型/权限/MCP核对通过才可继续，活动或部分历史只读，不自动重发。关闭保留不删除已有数据，官方持久历史可能含CAD工具返回的数据。当前未提供桌面已有会话共享或任意会话接管；工具读取的模型信息会随请求发送给模型服务，当前只读文件策略不构成完整本机文件隔离。

## 安装、CLI 与开发

普通工具只需兼容 FreeCAD 及本 Addon；Node、FreeCAD 和聊天客户端不随 Addon ZIP 捆绑。手工解压至已核实的 FreeCAD Mod 目录后重启并选择 Phoenix Tools。Node 安装器只管理自己创建且清单一致的安装；未知或修改过的目录保留并拒绝覆盖。更新磁盘文件不会替换当前进程已加载的 Python/Node 模块；含未保存 CAD 的实例不要为刷新工具清单直接强制重启。

Node 包与命令名保持 `phoenix-freecad-mcp`。CLI 的实际参数为：

```text
phoenix-freecad-mcp --help
phoenix-freecad-mcp --check
phoenix-freecad-mcp --demo
```

无参数运行 MCP stdio 服务；`--check` 检查桥接，`--demo` 显式改变轴测相机并 Fit。`--help` 只显示帮助。stdout 用于协议，不能混入普通日志。配置以 CLI/源码合同为准：

| 环境变量 | 含义 |
| --- | --- |
| `PHOENIX_FREECAD_URL` | 明确本机 IPv4 HTTP 根地址，设置后固定直连且不读共享配置/实例登记；未设置时按共享配置，缺省直连 `http://127.0.0.1:9876/`。 |
| `PHOENIX_TIMEOUT_MS` | 100–120000 毫秒，默认 15000。超时后先核状态。 |
| `PHOENIX_FREECAD_TOOL_SCOPE` | `full`（默认）或 `chat`，基础28/22项；发现仅完整模式额外2项。 |
| `PHOENIX_FREECAD_SCOPE` | `worker`选择专属4项缩略图工具，不参与自动发现。 |
| `PHOENIX_DESK_NODE`、`PHOENIX_DESK_CLI`、`PHOENIX_DESK_STATE_DIR` | 三项须同时提供已核实的规范绝对路径；不自动探测或启动。 |
| `PHOENIX_DESK_CHAT_READS` | `1` 明确启用聊天的五项 Desk 读取，须已有完整三路径；默认关闭。 |

本机桥接没有进程身份认证或多用户隔离，仅用于可信本机环境。完整外部 MCP 的高级脚本拥有控制台权限，使用时要明确范围；内置受控聊天不注册这个入口。

开发源码可使用 `npm run addon:dev -- --prepare` 只准备专属开发 profile，去掉 `--prepare` 才启动独立开发 FreeCAD。开发目录有归属标记和私有权限，拒绝仓库/源码子树、未知安装和不匹配入口；不复制常规实例的配置、最近文档或登录数据，不修改正常安装副本，也不自动启动 MCP。开发端口默认 9877，与正常默认 9876 分开。

**重载开发界面**仅在开发实例出现，只重载固定 UI 模块。存在编辑、事务、外观/零件/图纸预览、运行中的请求或聊天连接时拒绝；不会替你取消业务。桥接、协议、控制器和事务实现变更需要重新启动开发实例。默认构造的 Qt 设置使用开发私有 INI；显式 NativeFormat 使用独立开发域，不承诺所有 Qt 文件都位于 profile 内。

## 导出、撤销与验收边界

当前文档 Markdown 导出需选择目标、完整预览，并在冲突时明确确认覆盖；导出文本与保存 CAD 是不同操作。 发布结果不确定，或已写出但暂存清理未确认时，界面会提示先检查目标、不要重复导出，不自动重试或删除最终文件。已有目标超过1 MiB时拒绝覆盖。三维截图或 Desk 缩略图仅在明确请求图像时读取。PDF/SVG 及 CAD 文件保存不是基础出图草稿的自动后续步骤，高级脚本有文件能力也不表示已获得任意导出授权。

有修改的普通工具使用可撤销事务；外观取消仅恢复自己拥有的预览。Undo/Redo 后应检查实际原生属性和显示。TechDraw 节点创建、重算计数、合法 DTO、投影边线、屏幕排版和最终制造尺寸完整性属于不同检查，不能相互替代。

源码与合成测试已覆盖上述接口、有限输入、状态链和多数异常分支；真实安装、当前发行版 Qt/FreeCAD、登录回调、显示材质、异步投影、首次打开布局及文件输出需按候选分别记录。待扩展包括完整尺寸/孔槽/螺纹、公差与审签、更多建模操作、图纸导出流程、聊天持久历史的官方服务恢复及更广平台验收。

进一步阅读：[源码与安装说明](https://github.com/phoenixwing-org/phoenix-freecad-tools)、[MCP 客户端接入](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/master/docs/CODEX.md)、[当前文档工具](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/master/docs/CURRENT-DOCUMENT-TOOLS.md)、[聊天操作](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/master/docs/CHAT-USAGE.md)、[基础图纸草稿](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/master/docs/TECHDRAW-DRAFT.md)、[开发环境](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/master/docs/DEVELOPMENT.md)、[验证记录](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/master/docs/VERIFICATION.md)。这些链接由系统浏览器打开；本帮助不在线更新或读取你的项目。

专属常驻缩略图 worker 是独立开发模式（4项工具），不在普通完整28/聊天22项中启用。它为明确授权的可信原生自包含文件输出PNG，不保存CAD；Desk批次与正式部署见仓库 docs/THUMBNAIL-WORKER.md。

刷新当前文档工具保留同一文档实例的勾选范围（含全不选），新增对象不自动加入；切换文档后按新实例推荐。Markdown在打开保存框之前冻结文件名及正文，确认期间切换文档不会换用另一份正文。聊天回复的“已停止／未完成”保留本回合终态；服务终结失败会停止本窗口连接并显示脱敏原因，不自动重发。
