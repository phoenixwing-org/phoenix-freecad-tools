# Desk Tools 集成与分发边界

Phoenix Tools 的 Desk 连接面板用于保存明确配置、检查状态、显式启动或复用兼容后台，再请求系统浏览器打开 Web。本文描述源码接口与验收要求，不记录宿主配置、后台实况或联试过程。操作步骤见[Desk 使用说明](DESK-USAGE.md)。

普通 FreeCAD 按钮、FreeCAD MCP、聊天客户端与 Desk 后台分别管理生命周期。Desk browser-host 合同由单个 Node 后台提供同源 API 和 Web，系统浏览器作为客户端，不依赖 FreeCAD WebEngine 或 Tauri。Desk 不承载聊天服务。

## 接口分工

| 本项目模块 | 职责 |
| --- | --- |
| [pft_desk_service.py](../addon/PhoenixFreeCADTools/pft_desk_service.py) | 路径、固定参数、JSON/退出码、身份和 URL 校验；不导入 Qt 或 FreeCAD |
| [pft_desk_controller.py](../addon/PhoenixFreeCADTools/pft_desk_controller.py) | 配置保存、独立动作编排、单次执行和过期回调隔离 |
| [pft_desk_qprocess.py](../addon/PhoenixFreeCADTools/pft_desk_qprocess.py) | 异步 CLI helper、输出预算、超时及环境白名单 |
| [pft_desk_views.py](../addon/PhoenixFreeCADTools/pft_desk_views.py) | 原生配置面板、状态显示和显式浏览器请求；不拥有 Desk daemon |

`PhoenixTools_DeskTools`打开可复用的非模态面板；打开或保存配置不运行 CLI。`status`、`ensure`及打开 Web 是不同动作，打开前必须重新检查状态。

Wing 的通用规则、契约和界面复用见[复用边界](WING-REUSE.md)。Qt、FreeCAD 与进程生命周期留在宿主适配层，MCP 领域适配见[Desk 能力共享](DESK-MCP-CONTRACT.md)。

## 配置与可信入口

面板只接受 Node 执行文件、Desk CLI 文件和私有状态目录三个路径字段，不接受附加 argv、shell 片段、端口扫描或任意启动命令。Node 与`scripts/desk-web-service.mjs`须为已存在的普通文件，Node须有执行权限，路径及祖先不能经过符号链接。

所选 Desk 构建必须与插件支持的协议、平台和资源合同兼容。实际 Node 版本、二进制摘要、架构及构建资源由可信 CLI 根据 manifest 核验；不能仅凭路径或 Node 主版本判断兼容。插件不自动换 Node、安装依赖、编译源码、启动开发服务器或 Tauri。

选择 CLI 是对本机可执行代码的显式配置。Python 层校验配置及有界 DTO，不再次探测 HTTP，也不提供第二套身份认证。manifest、登记、存活身份、无重定向探测及资源核验归可信 CLI 负责；HTML、health、PID 或端口响应不能单独证明兼容。

私有状态目录由用户明确选择，须独立、持久、真实且仅当前用户可访问。不得使用根目录、用户主目录、Desk 源码及其父范围、插件目录、工程或业务数据目录。缺失目录在检查时不创建；不修权限、不迁移数据，也不通过另选目录绕过未知所有者。

## 状态与身份合同

连接适配消费`browser-service-v1`合同及受支持的 service/browser-host 协议；双方协议与版本必须由正式兼容校验确认，不能用产品版本号相近替代。

| CLI 状态 | 工作台处理 |
| --- | --- |
| `ready` | 兼容身份已由 CLI 核验，可提供独立打开动作 |
| `absent` | 当前明确状态目录没有可复用服务；只有显式启动动作才允许进入 ensure |
| `unavailable` | 登记、服务或响应暂不能确认；保持原状，不自动重试启动 |
| `incompatible` | 候选、Node、登记或协议不兼容；不接管、替换或迁移 |

ready DTO须与退出码0一致，并包含应用版本、runtime、instance、PID、同源 loopback API origin与根 Web URL，以及互斥的created/reused标记。合法非就绪结果与非零退出码对应，不能携带可打开 URL 或进程控制权限；畸形、多个 JSON、HTML及超预算输出拒绝为错误。

Desk登记与存活身份须绑定 schema/service、协议、应用版本、instance/runtime、API origin、Web URL与能力。`/api/desk-service`用于完整身份合同；其他 health 接口只说明对应子系统状态。旧 API-only登记、未知锁或身份不一致均保持原状。

打开操作使用当前配置重新执行status；仅fresh ready允许向系统浏览器提交已核验的HTTP loopback根URL，不接受凭据、重定向、额外路径、查询或片段。浏览器请求被接受不等于页面加载或视觉验收通过。

## 生命周期与失败处理

ensure须先验证已有实例，兼容时复用，只有确认absent才可能启动后台并有界等待ready。创建状态目录、日志、锁、登记及进程均是显式启动的写入副作用。状态变化或结果不确定时重新检查，不盲目重复ensure。

CLI helper采用固定参数数组和必要OS环境白名单，不继承`NODE_OPTIONS`、`NODE_PATH`等注入项。单次超时15秒，stdout最多64KiB、stderr最多8KiB；超时或超限只回收本次自建helper，不能据登记PID或端口停止daemon。ensure可能已启动后台，失败不证明后台不存在。

当前面板没有Desk停止操作。关闭面板、浏览器或FreeCAD MCP均不停止Desk后台。忙时禁用冲突动作；失败不自动修复登记、清锁、换命名空间或接管未知进程。

## Native执行与领域共享

Web/API ready不授予FreeCAD原生渲染或其他复杂任务的执行权限。Native能力须有独立版本化运行态合同，以及所有宿主共用的协调域；不能按workspace、端口或state-dir分别开门。未知宿主、残留作业和锁不自动清理，实际child退出才满足相应生命周期条件。

MCP适配只消费Desk正式CLI和领域接口，不直连数据库或复制SQL。索引、BOM与缓存来自已保存数据，不能代替FreeCAD未保存内存。文件去重、引用计数、BOM行与实物数量须保持各自口径；缺失、过期与未知结果不能补零或冒充完整。

磁盘Link修复、缓存生成、图册任务和导出需要独立写入合同：明确输入、预览hash、备份、授权、任务状态和逐文件结果。`dry_run`名称不能代替副作用审查；不能通过旧磁盘接口后台替换FreeCAD中已打开且可能未保存的文件。后续接口及数量语义见[能力共享](DESK-MCP-CONTRACT.md)。

## 分发与验收要求

默认分别分发轻量Addon、Node MCP及可选Desk后台。自动下载、部署与合包仍为后续目标，不能把源码目录、manifest或服务ready当作正式发行资产。

分发前须取得固定版本和平台资产、可信摘要、实际体积、完整运行依赖及LICENSE/NOTICE；检查安全解包、失败恢复、自有版本目录、数据位置、升级迁移及每个平台的安装边界。下载、安装/编译和启动分别由用户明确触发。

验证状态机时使用独立合成fixture，分别覆盖缺失目录、兼容复用、不兼容/损坏登记、超时、迟到回调、打开前状态变化和后台生命周期。原生UI、浏览器页面、复杂Native及平台安装须各自验收；源码合同或注入测试不代替这些结果。

待分发文件排除用户模型、工作空间、数据库、偏好、最近目录、锁/登记、日志、凭据、运行回执、恢复备份和机器路径。私有配置、讨论、使用经过及其去标识摘要不进入源码、文档或包。具体审查步骤见[隐私边界](PRIVACY-REVIEW.md)，后续工作见[TODO](TODO.md)。

公开源码入口：[Phoenix Desk Tools](https://gitee.com/phoenixwing/phoenix-desk-tools)。本文不声明任何本机实例、历史验收或正式部署已经完成。
