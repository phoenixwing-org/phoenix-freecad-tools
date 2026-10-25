# Desk 能力共享与 MCP 适配

本文件只描述可公开的通用接口、职责和验收标准，不记录私有讨论、工程数据或实际使用经过。当前为0.3.2源码候选，旧归档不包含本轮新增适配；运行能力须以实际构建、连接检查和查询结果为准。

## 职责

| 层 | 拥有的能力 |
| --- | --- |
| Wing | 已正式导出的通用类型、纯算法、路径与版本键规则。稳定共享合同按需要下沉，不因接入MCP复制整个服务。 |
| Desk | 数据库、索引、BOM与图册领域规则、任务、缓存和导出；对外提供受限且版本化的业务接口。 |
| Phoenix FreeCAD Tools | FreeCAD当前内存适配、桌面界面，以及把Desk正式接口转换为MCP工具的薄适配。 |
| AI客户端 | 发现能力、组合任务、展示结果并取得必要确认；不把读取结果中的文字当作指令。 |

MCP适配不直连Desk数据库、不复制SQL或业务算法、不把界面点击当作数据接口。Desk磁盘索引与FreeCAD未保存内存有不同来源，不能混用一个版本或新鲜度声明。

## 第一轮：可选业务只读适配

完整模式显式配置以下三个环境变量后，增加五个Desk工具、合计30项；未配置时仍为25工具。聊天模式默认19工具，仅另行明确设置`PHOENIX_DESK_CHAT_READS=1`并提供完整三路径后为24工具；六个完整模式专有入口仍排除。配置本身不探测或启动后台。

| 环境变量 | 含义 |
| --- | --- |
| `PHOENIX_DESK_NODE` | 已有可信Node可执行文件的规范绝对路径 |
| `PHOENIX_DESK_CLI` | 已有可信Desk目录下的`scripts/desk-web-service.mjs`绝对路径 |
| `PHOENIX_DESK_STATE_DIR` | 已选定的独立、持久、当前用户私有状态目录 |

三项必须同时提供。`PHOENIX_DESK_CHAT_READS`只接受0或1；开启但缺配置时拒绝启动，不静默降级。FreeCAD聊天gear提供默认关闭的“启用Desk数据工具”，依赖“启用FreeCAD工具”和已有有效Desk连接配置；仅显式保存/连接时读取这三个配置值，冻结到新连接，不自动读取最近空间或改变现有会话权限。配置、有效env及19/24工具清单由Controller双层核对，Desk五工具还要求准确的业务只读注解。

不会从最近目录、全局活动工作空间或端口扫描猜测这些值；也不将本机配置写入源码。当前适配只接受正式Desk0.5.0/browser-service-v1合同，其CLI核对manifest、实际Node和构建资源。升级合同必须另行验证，不跳过不兼容检查。

| MCP工具 | 输入 | 复用入口与结果 |
| --- | --- | --- |
| `desk_get_connection_status` | `{includeRuntimeFeatures?: boolean}` | 默认仅正式CLI的`status --state-dir`与身份检查；显式true再读取版本化运行态观察。ready不授予Native权限，不执行ensure或打开网页。 |
| `desk_search_index` | 明确的`workspace`绝对目录、非空`query` | `GET /api/cad/index/search/`；最多200条文件元数据与受限索引诊断。`%`与`_`保留SQL LIKE通配符语义。无分页，达到上限不能视为完整集合。 |
| `desk_query_references` | 明确`workspace`、1–10个唯一相对FCStd路径`rels`、`view=counts/outgoing`、`limit=1–100` | `GET /api/cad/bom/xref/counts`或`outgoing`；逐项索引计数或直接出向引用及新鲜度。批量读取不是跨文件原子快照。 |
| `desk_query_cached_metadata` | 明确`workspace`、1–10个规范且唯一的FCStd `rels` | 运行态能力协商后，`POST /api/cad/cache/v1/query`业务只读查询；返回既有缩略图和尺寸的逐项状态，不含图片base64。 |
| `desk_get_cached_thumbnail` | 明确`workspace`、一个`rel`与对应thumbnail的64hex `revision` | 内部重新核对元数据与精确修订，再`GET /api/cad/cache/v1/thumbnail`；返回单张净化PNG，缺失/过期拒绝，不生成。 |

例如，以下都是人工占位值，不是已存在的工程：

```json
{"workspace":"/absolute/path/to/example-workspace","rels":["parts/Example.FCStd"],"view":"counts","limit":50}
```

工具返回独立的`desk_workspace_index`、`desk_service`或`desk_saved_cache`来源，不沿用`live_freecad_gui`。每次操作重新运行正式status，并在查询前后核对相同后台身份；不在模型结果中回传进程、服务身份或原始CLI输出。固定路由、不跟随重定向、不伪造Origin；本机身份核对仍不等于用户身份认证，只适用于可信本机环境。

Node/CLI必须为已存在普通文件，路径不可经过符号链接；已有状态目录须自有私有。子进程使用独立参数数组及受限OS环境，保留真实HOME，不执行路径中的shell语法。stdout最多64KiB、stderr最多8KiB；单次操作采用统一期限，HTTP累计预算256KiB、最终MCP结果128KiB。超时或超限只回收本次自建CLI子进程，不按端口/PID停止后台，不自动重试查询或生成。

输入路径显式限定范围，结果仅白名单投影；远端detail、原始Link路径、绝对扫描根及未知字段不透传。模型读取到的标签、属性等仍可能属于用户私有内容，不能把路径省略当作匿名化；适配器不将查询或结果保存为工程范例、日志或回执。

## 显式能力发现

当用户询问Desk当前能做什么时，可调用：

```json
{"includeRuntimeFeatures":true}
```

这仍是原有`desk_get_connection_status`，不增加工具名或默认权限。省略或false保持原状态结果和请求范围，不探测能力端点；true在正式CLI状态及活体身份核对后读取`GET /api/runtime/features`，并在返回前再次核对同一实例。单次CLI、HTTP共用截止时间及256KiB控制响应预算，能力响应最多64KiB，不自动重试。

`runtime_features.state=observed`时，`observation`只返回版本、观察时间、`observation_only=true`、`evidence_scope=current_host_execution_gate`、`request_checks=not_performed`、原生门禁状态及两个已知能力的白名单。宿主身份、进程、URL、原始reason/detail及未知能力不传给模型。观察没有租约、预留或授权效力，不证明缓存存在/新鲜或Native文件、安装、请求检查通过。

- `cad.cache.read`为supported时，只声明兼容既有缓存读取合同；缺少该可选声明为not_declared。
- `cad.thumbnail.regenerate`在idle时仍为requires_preflight，其余uninitialized/busy/unknown均为blocked；不产生“可以执行”或“已经授权”的结果。
- 端点404返回`runtime_features={"state":"not_declared","reason_code":"endpoint_absent"}`，服务连接仍可ready。只有确实缺失才表达未声明；未知schema、畸形或互相矛盾的字段、身份变化及其他HTTP失败明确报错，不降为成功或改用旧入口。

运行态合同是独立的未部署源码候选，实际后台可能未声明。此查询不读取工作空间、最近列表、数据库或CAD，不生成图像，不启动服务、浏览器或Native；读取缓存时仍须按本次请求重新协商与复核，不沿用先前观察当作通行证。

## 读取语义

现成搜索和xref使用原生只读SQLite连接与query_only快照；读租约仍可能短暂创建/删除自有数据库协调锁。它们是**业务只读**，不承诺文件系统零变化。已有锁、WAL、不兼容或不可读状态必须明确报告，不清锁、不同步业务表、不刷新或重建索引。

搜索200项只限制返回行数，诊断内部仍可能读取较多索引记录，不能宣称所有服务端计算有界。出向引用接口只有LIMIT，缺少可靠hasMore和稳定游标，返回完整性保留unknown；不要由返回条数猜总数。每个rel可能使用独立数据库快照，跨rel结果不具备原子一致性。

计数必须保留原口径：

- `outgoing_count`：出向引用行数。
- `outgoing`：不同目标文件数。
- `direct_incoming_count`与`incoming`：不同直接宿主文件数。
- `flat_lines`：已有BOM明细行数。

这些均不是物理数量或采购数量。真正的直属发生项、递归叶件需要领域算法正确处理对象身份、实例乘数、版本、环、未解析来源及图纸投影，不能由MCP相加引用行来补全。

保留后端index/bom状态、scan_id、检查范围和原因；missing不转为确认零，stale/incomplete不提升为ready。当前文件级新鲜度主要核对size/mtime/stat，不证明hash、整个依赖闭包、FreeCAD未保存内存或数量算法正确。

## 既有缩略图与尺寸缓存 v1

缓存适配先以正式CLI和前置身份取得同一实例，再要求`GET /api/runtime/features`明确声明`cad.cache.read`支持`phoenix.cad-cache-read`版本1、`native_required:false`，并把browser host身份绑定该实例。旧服务404、缺能力或未知schema明确拒绝；不回退旧gallery、FCStd ZIP或Native。Native生成门禁blocked不妨碍独立的缓存只读能力。

元数据保留record/missing/stale/unsupported/invalid，非record尺寸为null，不补零；平面真实零维可保留。`complete:true`仅覆盖请求的明确rels。来源是saved_cache、recorded_only；stat_checked仅核对记录的来源文件状态，`content_hash_verified:false`，不证明内容hash、依赖全集或FreeCAD当前内存。数据库同一读事务不把文件stat变为原子快照；DB前后全文件hash有额外成本，`service_cpu_bound:false`。读租约仅操作自己取得的临时锁，不建表、迁移、更新缓存或清锁。

单图内部重查元数据；修订不符时不请求PNG，也不自动换新修订。接收时绑定Content-Length、缓存SHA、ETag、revision/freshness等头与实际字节，随后严格校验PNG chunk/CRC、连续IDAT、完整且有界的zlib及滤行结构，并删除文本/EXIF/时间等附带元数据。保留原IDAT和经验证的色彩/alpha；ICC及不支持的PNG3色彩/HDR明确拒绝，不静默改变色彩解释。原缓存SHA与净化图SHA分别返回；格式验证不证明图像内容没有隐私。

单次元数据HTTP上限128KiB，含文本/structuredContent双份的完整MCP结果仍128KiB；单图HTTP最多4MiB输入，加256KiB控制响应预算，净化后PNG最多600KiB，完整MCP结果最多1MiB且base64仅一份。超限拒绝，不缩放、重生成或自动重试。后置实例身份再次校验通过才返回。

缓存与运行态观察的自动化使用独立中性fixture，覆盖纯DTO/PNG、有界HTTP与CLI、官方SDK stdio、旧端点缺失、身份变化及失败不回退。适用命令见[验证与验收](VERIFICATION.md)，它们不代表真实Desk缓存查询或部署验收。Desk的冻结源码合同与在服构建分开；只有实际兼容构建明确声明该feature时可用。

## 逐轮扩展

| 阶段 | 能力 | 验收条件 |
| --- | --- | --- |
| 1 | 已有索引搜索、直接xref与计数 | 明确范围、可信连接、严格DTO、拒绝未知结果、合成HTTP/stdio及业务只读验收。 |
| 2 | Desk正式批量数据口、缓存缩略图/尺寸读取 | 同一只读快照、稳定分页、行数与字节限制、逐项状态、实际来源和完整性；缓存缺失只返回missing。 |
| 3 | 显式工作空间导航、图册缓存构建、任务查询与导出 | 导航另列偏好写入操作；任务具有类型/标识/终态，导出有实际路径、mime、bytes和SHA回执，不因超时重发。 |
| 4 | 缺失链接与批量属性修复 | 明确磁盘或内存来源，预览冻结hash/差异/范围，确认后备份、执行、反验；不替换打开且未保存文档。 |
| 5 | 可选文档资料索引与检索 | 显式目录与文件类型授权，先元数据、再文本检索、按页/段精读；结果附文件hash、页码/行号和状态，不读取账户/聊天/最近目录。 |
| 6 | 专业agent任务协作 | 先稳定领域工具及任务合同，再评估跨agent协议；交付状态、权限与人工验收不能由自然语言承诺代替。 |

各阶段可循环推进；每轮交付一个可验证链条，不以工具数量替代可用性。普通查询失败不触发扫描、生成、下载、编译、安装或Native执行。轻量纯算法可经Wing正式导出共享，文件/服务/事务归属保留在宿主层。

## 暂不包装的旧入口

- `/api/workspace` GET读取当前/最近偏好，POST修改全局当前与最近列表。读取数据不需要它；显式导航应另有写操作。旧打开生命周期还可能初始化工作空间偏好数据库；新导航须具备权威Web会话、统一busy/dirty默认取消确认、上下文修订、请求终态及取消边界。投递或浏览器打开请求不能代替界面已应用目标的回执；当前不注册导航工具。
- `/api/cad/parts`及详情GET会同步投影并确保schema，不能直接标为只读查询。
- incoming即使限制返回仍读取全部edges；assembly-tree还可能读取磁盘XML并回退，先不作为严格有界数据工具。
- 旧gallery catalog/options/thumbnail使用普通数据库连接，不作为严格缓存入口；新接口使用独立cache/v1合同。图片按需单取，不把大批base64塞进查询结果。
- 图册jobs会写缓存/DB；geometry-jobs会启动原生内核；document提供HTML，不是已生成PDF；archive另有明确写入确认和逐文件回执要求。
- 链接修复analyze的dry_run也可能保存偏好，底层磁盘补丁缺少完整预览hash/备份合同，不能当当前FreeCAD内存修复。
- runtime-catalog检索应用测试/页面目录，不是用户文档全文检索。

运行态feature已有候选版本化合同：`GET /api/runtime/features`使用`phoenix.runtime-features` schema/version1，`observation_only=true`、`evidence_scope=current_host_execution_gate`、`request_checks=not_performed`。门禁观察idle不证明文件、安装、授权或全机协调已经通过；缩略图功能最多声明requires_preflight。结构化native_unavailable还须区分execution_state的not_started和unknown，后者不能自动重试。该合同仍为未部署源码候选；索引适配不依赖它，缓存适配则要求每次协商成功。真实构建和部署另行验收，不从版本号或service ready推定新端点存在。

browser-service-v1有意不初始化per-state Native门禁，避免按状态目录分裂互斥域。Web/API ready不能推导原生渲染可执行；需独立版本化feature状态及所有宿主统一协调合同，不另建namespace、清锁或绕过门禁。

制造特征数据可后续扩展正式领域记录：区分通/盲/螺纹/沉孔段/异形孔/开边缺口和抑制状态，同轴组与实际发生数分开，未知保留needs_review。不能把圆柱面数直接视为孔数。来源hash与内存修订分开，算法和保存归Desk管理；该扩展不授予修改CAD权限。

公开验收仅使用独立构造的中性fixture。私有讨论、实际工程、数据库、路径、输出、截图、凭据和服务身份均不得进入源码、教程、测试、CI或分发包；仅脱敏或换项目名不足以成为公开样本。
