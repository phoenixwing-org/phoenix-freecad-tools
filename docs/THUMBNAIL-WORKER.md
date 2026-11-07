# 常驻 FreeCAD 缩略图 worker

状态：独立开发候选，已通过 macOS 独立原生实例的20项连续渲染、去重、取消及结果过期检查。它给普通程序提供有类型 MCP，调用方不需要 AI。默认完整25项、聊天19项工具保持原合同；`PHOENIX_FREECAD_SCOPE=worker` 单独注册下列4项。普通用户 FreeCAD 桥接不启用 worker 生命周期，不能因为窗口空闲就接管。

## 启动与连接

macOS 开发入口为 `node scripts/thumbnail-worker.mjs --profile <独立私有绝对目录>`；`--prepare` 仅准备，不启动进程。运行需要已有 Node 22+、FreeCAD GUI 和本项目完整源码/受管理资源，不下载或安装。此开发入口复用专属开发 profile 的配置隔离，尚不是 Desk 正式发行版的 worker 管理器；其他平台原生启动尚未验收。

profile 位于源码和工程以外，必须归当前用户所有且私有。每个 profile 有稳定 `worker_id`，每次启动有新的 `session_id`；原生服务在操作系统分配的 loopback 端口监听。启动器给出本次私有 ready 回执位置；客户端核对启动记录、进程、两项身份、协议与实际地址后，给 Node MCP 设置 `PHOENIX_FREECAD_URL` 和 `PHOENIX_FREECAD_SCOPE=worker`。不扫描端口，不把 UUID 当认证，不将这个专用动态端口写回普通桥接首选端口。

开发启动器的 Mod 指向所选源码/冻结资源。调用方必须让该资源在整个 worker 进程存活期保持有效且内容固定：`withFrozenResource` 的回调须等到自有 child 真正退出后才结束，或先通过正式安装器部署到自有持久版本目录。不能在 launch 返回后立即销毁快照、更新正在使用的资源或删除进程仍依赖的 Addon。MCP stdio 客户端也须在结束资源租约前关闭自己的进程。

ready 回执不代表已渲染、CAD 已验证或 Desk 缓存已登记。已有手工 profile、未知锁、残留 staging 或归属不符时停止；不清锁、迁移数据或采用新命名空间绕过不确定状态。关闭普通面板不控制 worker；独立 worker 的进程归调用方管理，不能按登记 PID 终止其他实例。

## 可消费的固定资源

`npm run desk:pack` 生成独立的完整Addon、安装/启动脚本、自包含`dist/cli.mjs`、依赖许可和固定manifest/SHA，无node_modules。Desk真实loader和frozen目录已消费该产物，并通过实际MCP/原生同PID连续任务验收；构建、接入及边界见[资源合同](DESK-RESOURCE.md)。这补齐分发资源，不把当前开发profile启动器变成正式安装profile管理器；Desk队列与缓存登记仍另行实现。

## 调用合同

| MCP 工具 | 输入 | 结果 |
| --- | --- | --- |
| `get_thumbnail_worker_status` | 无 | 本次身份、ready/busy/quarantined/stopped、活动任务、输入/图像/尺寸预算与渲染 profile。 |
| `render_document_thumbnail` | `workerId`、`sessionId`、`jobId`、明确绝对 `file`、获批 `expectedSha256`，可选 `width/height` 默认512 | 立即接受为queued或返回同请求已有状态；不等待整项完成。 |
| `get_thumbnail_job` | 上述三项身份 | 当前阶段或终态；成功后返回PNG及来源摘要。 |
| `cancel_thumbnail_job` | 上述三项身份 | 标记取消；在安全阶段清理，不把收到取消说成已终止原生打开。 |

身份为规范小写 UUID，摘要为64位小写 SHA256。每个 worker 一次只接受一个文件；由 Desk 排队并显示进度。相同 session/job 与相同参数只返回原状态；异参拒绝。每会话最多接受256个任务，最近20份结果保留内存，过期ID返回`JOB_RESULT_EXPIRED`且不重新打开。重启清空账本并更换 session；首版没有跨重启恢复或自动重放。

阶段为`queued → freezing → opening → rendering → closing → done/failed/cancelled`。HTTP 超时或原生打开阻塞不等于未执行；先按原身份查询，不换实例重试。原生打开期间取消只记录请求，等待原生返回后再恢复和关闭；当前不承诺中断任意 native 回调或图形驱动。

## 首版输入与输出

输入限定为调用方明确授权的**可信、原生、自包含 FCStd**。打开前有界检查归档、Document.xml 与可选 GuiDocument.xml 的原生类型/属性、链接、扩展和序列化文件引用；Python/Proxy、非空表达式、外部依赖和未知序列化类型拒绝。有限允许列表含 Part::Feature、Body/Sketch/Pad/Pocket 等，但某个 TypeId 在列表中不代表其所有工作台属性都受支持；例如含 Python GUI 状态的 Sketch 仍可能拒绝。

若归档缺少`GuiDocument.xml`，预检另冻结每个原生对象的内部Name、TypeId及明确`Visibility`布尔值；缺失、重复或畸形值拒绝，不猜默认。打开自有副本后，先绑定整组对象和ViewProvider身份，再按原值恢复true或false，逐步复核归属、取消、事务和最终值；不强制显示全部、不改材质或源文件。已有GUI状态完全沿用，不执行这一步。FreeCAD 1.1的[GUI恢复入口](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Gui/Document.cpp)和[恢复期间隐藏ViewProvider](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Gui/ViewProviderDocumentObject.cpp)解释了该适配的来源；实际支持范围由独立原生对照验证。

这是受支持格式检查，不是不可信 FCStd 沙箱。FreeCAD restore/close 可以执行 native 回调；打开后再检查对象不足以防止恢复副作用。装配依赖 manifest、工作台/Proxy profile 和 TechDraw 图纸渲染留待独立实现。

源文件先验证真实普通文件、祖先、归属/链接和有界读取，再把**原始字节**冻结到任务自有私有目录。打开的是副本，不使用 FreeCAD 保存副本；源与副本的 stat 身份和 SHA256 在阶段边界反验。源压缩上限64 MiB、展开总量256 MiB、XML16 MiB、归档10000项；不解包模型到工程目录。

渲染固定为白底、轴测Fit，复用完整相机恢复和反验。有GUI状态时沿用保存的材质与可见性；无GUI材质状态时由FreeCAD原生ViewProvider使用默认材质，只恢复明确记录的可见性，不伪称读取过保存的GUI材质。全部明确隐藏的文件仍可生成白图；格式正确和任务done都不证明视觉内容合格。尺寸1–1024且最多1048576像素，净化 PNG 最多4 MiB；结果附`input_revision`、图像SHA256/尺寸/字节数、`camera_restored=true`、`documents_closed=true`、`saved_cad=false`与`worker_reusable=true`。不接受脚本、任意输出目录、材质覆盖或保存开关。Node 重新验证PNG、CRC、压缩数据、元数据净化与摘要；worker控制响应预算6 MiB，PNG Base64只出现在structuredContent一份，文本只含摘要。

共享捕获器已修正透视构图的裁切并兼容Pivy的原生SbBool整数；默认图像后端在独立内存样件通过三种画幅与五种mapping的图像检查，另有隐藏、点线面、恢复回调及`FramebufferObject`的有限补验，详细范围见[验证记录](VERIFICATION.md#透视缩略图构图与原生布尔兼容)。`GrabFramebuffer`的轴测Fit明确拒绝；macOS显式选择`CoinOffscreenRenderer`时，无论Fit开关均在捕获前拒绝，避免已观察到的原生退出，不自动换后端。这批是直接捕获及预览按钮验收，不把它外推为更新后的worker实际批次、其它平台或Coin渲染已通过。

成功必须发生在源反验、关闭任务文档和清理副本之后。创建文档观察器在打开前安装；打开异常也只能关闭仍匹配任务副本与对象身份的文档。未知文档、编辑/事务、身份丢失或清理失败使 worker 隔离，不执行close-all，不关闭外来文档，不继续下一文件。

Desk 接收成功PNG后，另行原子发布外部缓存并登记来源。缓存生成不调用旧`regenerateThumbnail()`写回包装器；FCStd内嵌缩略图与CAD保存是另一项明确授权的写盘操作。这里没有缓存数据库或工作空间导航能力。

## 验收边界

纯输入测试、主机模拟的生命周期测试、官方SDK/stdio测试和独立FreeCAD原生渲染分别验收。自动化不证明所有文件可恢复，PNG格式正确也不证明图像视觉正确。跨平台、外链装配、原生卡死恢复、正式部署及Desk批次/缓存闭环仍须继续完成。所有验收使用独立中性fixture；实际工程、路径、截图、回执和使用讨论不进入Git或包。
