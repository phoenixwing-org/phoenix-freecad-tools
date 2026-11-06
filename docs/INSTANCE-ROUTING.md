# 多实例连接：实现合同与简要调研

适用源码候选 **0.3.2**，桥接协议仍为 **v1**。本轮已接入严格配置、私有实例登记、身份握手、发现与选择以及请求归属检查；macOS 独立双实例已通过正式 SDK/stdio 和原生控件自动化；该结果不代替用户手工、任意模型或跨平台验收。最终结果见[验证记录](VERIFICATION.md)。

## 成熟项目怎样做

| 参考 | 已有做法 | 可以借鉴的部分 |
| --- | --- | --- |
| [Blender MCP](https://github.com/ahujasid/mcp-for-blender#environment-variables) | 每个 Blender 使用不同端口，客户端分别配置连接项 | 简单、明确；保留手动指定地址作为兼容方式 |
| [Unity MCP 多实例路由](https://coplaydev.github.io/unity-mcp/guides/multi-instance) | 发现实例列表，`set_active_instance` 为当前 MCP 会话选择目标；单实例自动使用，多实例未选择时报出列表 | 统一入口、显式选择、会话内固定目标，以及单次请求可明确指定实例 |
| [Jupyter Server 运行登记](https://github.com/jupyter-server/jupyter_server/blob/main/jupyter_server/serverapp.py) | 服务写入运行信息 JSON，`list_running_servers` 从运行目录枚举登记 | 固定发现位置与实际监听地址分开；本实现另加协议和实例身份握手，不仅检查 PID，也不照搬其项目路径或凭据字段 |
| [MCP 标准传输](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) | stdio 由客户端启动子进程，通过输入输出管道通信；HTTP 使用已知服务地址 | 本地入口继续使用 stdio，避免增加公共 TCP 端口 |

采用 Unity 的使用流程，沿用现有 stdio 入口；不要求用户为每个 FreeCAD 手工增加 MCP 启动项。发现是 PhoenixTools 的本地约定，不是 MCP 标准自带的全机端口发现协议。

## 使用文案与选择规则

**AI 只配置一个 Phoenix FreeCAD MCP stdio 入口。** 在工作台“设置 → MCP 连接设置…”明确开启“自动发现本机 FreeCAD 实例”并保存后，下一次普通桥接启动使用系统分配的回环端口，发布自己的实例登记；下一次启动的完整模式 Node MCP 读取同一配置并发现目标。

- 尚未绑定目标时，只有一个 `ready` 实例可以自动使用；多个 `ready` 实例返回 `INSTANCE_SELECTION_REQUIRED`，先列出再明确选择；没有可用实例则报错。
- `list_freecad_instances {}` 只读登记并握手，不选择或启动 FreeCAD，不读取文档。结果分别标为 `ready`、`unavailable`、`incompatible`、`identity_mismatch`；显示名为 `FreeCAD`、实例短标识与 PID，不使用文档名或工程路径。
- `select_freecad_instance {instanceId,serviceId}` 使用列表中的两个完整标识选择后续调用。每个 MCP 会话各自保存选择，不以窗口前后、活动文档名称或 PID 猜测目标。
- 一次工具调用从能力检查到实际派发始终使用同一客户端。同步调用尚未返回时拒绝切换；选择过程尚未完成时拒绝派发新操作。
- 已绑定实例丢失、登记改变、服务重启或身份不符时停止，不因另一个实例成为唯一可用目标就自动转过去。核实后明确重新选择；结果未知的写入不自动重发。

选择不会打开工作空间、激活文档，也不会取消、转移或替用户确认旧实例中的原生审批窗口。列表和选择结果的来源为 `instance_registry_and_status_handshake`，不标成模型读取或几何新鲜证明。

## 配置：默认直连，显式开启发现

缺失配置和原有 v1 都保持发现关闭，不在读取时创建目录、改写文件或自动升级：

```json
{"schema_version":1,"bridge":{"preferred_port":9876}}
```

开启发现使用严格 v2；`enabled:false` 也是合法值：

```json
{"schema_version":2,"bridge":{"preferred_port":9876},"discovery":{"enabled":true}}
```

两端仅接受对应版本的完整字段集合，拒绝未知字段、未知版本、重复解码键、非布尔开关、非整数端口、非法 UTF-8 和超过 8 KiB 的文件。首选端口范围为 1024–65535。没有公共 HTTP 路由端口、`endpoint.json` 或自动端口回退配置。

设置窗口只在明确保存时写入：原有或缺省 v1 且未勾选仍保存 v1；明确勾选保存 v2；已有 v2 取消勾选保存 v2 的 `false`，不降级。保存绑定原读取 etag，不覆盖外部更改；写后持久状态不确定时报告重新检查，不自动重试。打开、取消、Esc 和关闭不写配置。已有损坏、不可读或不安全配置不修复、不忽略、不换目录规避。

保存只影响下一次普通桥接启动和下一次 Node MCP 连接，不启停当前服务、不重连或切换现有聊天。FreeCAD 显式端口和独立开发端口保持其选择，不读取共享配置；普通发现模式才使用动态端口和登记。

**`PHOENIX_FREECAD_URL` 始终固定直连优先。** 设置它后，Node 不读取共享配置和实例登记，不混用发现模式；地址不可用时明确报错。未指定 URL 时才读取共享配置：发现关闭使用首选端口，开启后只路由登记实例，不能把保留的首选 URL 当作发现失败时的备选目标。

连接状态中的 `listener_configuration` 是本次或上次桥接启动的配置观察；`mcp_connection_configuration` 是Node启动时冻结的配置来源和模式。发现模式后者为 `version:2`、`scope:instance_discovery`、`discovery_supported:true`，包含实际 `runtime_directory`，`target_url:null` 不代表已选中某个实例。具体监听身份仍须看已握手的 `instance_routing`，不能把配置观察当作当前模型状态。

| Node 模式 | 工具数 | 发现行为 |
| --- | --- | --- |
| 普通完整模式 | 25；可选 Desk 后 30 | 固定直连，原有清单不变 |
| 明确开启发现的完整模式 | 27；可选 Desk 后 32 | 基础清单加列表、选择两个工具 |
| 受控聊天 | 19；明确启用 Desk 数据后 24 | 固定地址；无显式 URL 却要求发现时拒绝启动 |
| 专属缩略图 worker | 4 | 独立作用范围，不参与自动发现 |

离线 `get_capabilities` 只声明本次 Node 注册清单与发现合同，不证明任何 FreeCAD 已连接。独立 CLI 的 `--check`、`--demo` 在发现模式下也不猜测多实例目标；需要检查指定实例时使用显式 URL。

## 稳定配置与私有运行目录

| 系统 | 稳定设置文件 | 本机实例登记目录 |
| --- | --- | --- |
| macOS | `~/Library/Application Support/PhoenixTools/mcp/config.json` | `~/Library/Application Support/PhoenixTools/mcp/runtime/instances/` |
| Windows 布局约定 | `%LOCALAPPDATA%/PhoenixTools/mcp/config.json` | `%LOCALAPPDATA%/PhoenixTools/mcp/runtime/instances/`；本轮发现读写明确拒绝，尚无真实 ACL 适配器 |
| Linux | 合法绝对的`$XDG_CONFIG_HOME/PhoenixTools/mcp/config.json`，未采用时用`~/.config/PhoenixTools/mcp/config.json` | 已设置且存在、安全的 `$XDG_RUNTIME_DIR/phoenix-tools/mcp/instances/`；否则使用 `$XDG_STATE_HOME/PhoenixTools/mcp/runtime/instances/`，后者未设置时用 `~/.local/state/PhoenixTools/mcp/runtime/instances/` |

Linux 目录分类参考 [XDG Base Directory 规范](https://specifications.freedesktop.org/basedir/0.8/)。运行发现的显式根必须为合法绝对路径；`XDG_RUNTIME_DIR` 未设置或根不存在时才允许上述状态目录规则。相对路径、畸形路径或已经存在但权限不安全的运行根报错，不静默改用另一命名空间。Node 与 Python 使用相同目录合同；启动它们的环境不同可能使采用的目录不同，应检查实际配置来源与运行目录。

Unix 的受管后缀目录须归当前用户且为 0700，登记文件须为 0600、自有普通文件、无符号链接或额外硬链接，并复核祖先及文件身份。读取缺失目录返回空列表，不创建或 chmod；发布只创建规定的私有后缀。Windows 共享配置缺失时仍可按原规则使用默认直连；已有共享配置读取/保存以及发现功能，在没有实际 ACL 验证器时拒绝，不声称跨平台通过。

## 登记与握手合同

每个文件名为 `<instance_id>.json`，内容严格只有以下八项；示例标识为中性示意：

```json
{"schema_version":1,"owner":"phoenix-freecad-tools","instance_id":"11111111111111111111111111111111","service_id":"22222222222222222222222222222222","pid":123,"url":"http://127.0.0.1:12345/","protocol_version":1,"addon_version":"0.3.2"}
```

`instance_id` 标识当前 Addon 实例，`service_id` 每次监听启动重新生成；均为 32 位小写十六进制随机标识。PID 只用于诊断，不证明所有权或进程仍存活。URL 限规范数字 IPv4 回环 HTTP 根地址，端口 1024–65535，不含凭据、路径、查询或片段。

登记不存文档名、工程路径、项目、账户、凭据、聊天或进程参数。最多 100 份登记、每份 8 KiB，目录枚举最多 200 项；仅认可固定暂存文件名，未知、损坏或权限不符的条目使整次发现失败。仅连接验证过的登记地址，候选握手并发最多 4，不扫描端口、不探测 PID、不启动服务、不清除不可达实例。

发布采用独占原子登记，不覆盖未知已有文件；停止时只移除本次捕获的精确文件字节、文件与目录身份。清理不确定时保留错误并停止自动处理，不依据 PID、时间或文件名删除旧登记。

`get_status` 增加 `instance_routing={version:1,instance_id,service_id,pid,guard_required}`；停止后为 `null`。状态接口不读取 CAD、共享配置或实例登记。发现请求携带匹配 `target={instance_id,service_id}`，桥接在接收和 GUI 执行前复核同一服务与 dispatcher；旧监听器闭包、排队任务不能借新监听器继续派发。新直连客户端首次读取状态也固定观察到的身份，之后不因端口复用或服务重启静默绑定到另一个服务。旧无路由能力的 v1 插件仍保留原直连兼容路径。

这些身份字段用于关联与防止误路由，不构成本机进程认证或恶意同权限进程隔离。内部桥接继续只监听可信本机回环地址。

## 请求、预览与服务重启

脚本及 `set_appearance` 的 `requestId`，以及业务预览、审批和状态查询的 token，绑定原 `instance_id + service_id`。切换目标后重复使用已知旧标识返回 `INSTANCE_AFFINITY_MISMATCH`；不会移植预览、移动原生确认窗口或把未知结果当成未执行而重新提交。

Node 为两类关联各保留最多 4096 项，不通过淘汰允许标识在另一个目标重新执行；桥接也保留进程内的来源记录。服务停止再启动后，即使脚本模块的 `script_session_id` 没变，旧服务的请求 ID 和 token 也不能交给新监听器认领；带身份的业务 token 必须来自该服务的桥接预览。整个进程或模块重载后的内存去重边界仍须单独核对，不能承诺跨进程永久去重。

停止只取消尚未开始的排队任务和仍可取消的审批；已经执行的 CAD 操作不被强制中断。结果未知时先核对原目标和文档实际状态，不自动换目标、换 ID 或重放写入。

## 验收边界

macOS 独立双实例使用相同文档与对象内部名称，通过正式 SDK/stdio 到实际构建服务验证：多实例未选择时拒绝、精确读取及相机、完整属性预览与原生确认按钮、撤销重做、切换不迁移原生审批或请求、快照冲突、原端口重启需重选、新 MCP 会话也不能接管旧服务请求或 token、目标退出不自动换到剩余实例、失效登记只报告而不接管或删除。

另一个独立空文档实例通过原生复选框和保存按钮写入 v2，确认保存不启停服务；普通启动按配置使用动态端口并发布私有登记。实际 CLI 及正式 SDK 读取同一配置并核对 27 工具与唯一 ready 初始选择。配置位置仅通过读取器参数及子进程 homedir 解析注入专属目录，未重定向真实 HOME；保存后的通知框使用 QA 回调，原生设置控件和磁盘保存均实际执行。

以上均未保存 CAD；独立实例及自建文档已关闭。Linux 原生、Windows ACL、平台安装、真实用户手工和未知服务端口冲突等更多场景仍待分别验收；Windows 发现尚未开放，不能用 macOS 结果代替。运行回执和图片留在 Git 外。

本页只记录通用能力和公开参考，不收录本机实例、真实项目、运行回执或使用讨论。
