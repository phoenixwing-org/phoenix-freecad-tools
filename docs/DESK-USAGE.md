# 在 Phoenix Tools 中连接 Desk Tools

Desk连接面板使用系统默认浏览器访问兼容的browser-host后台，无需FreeCAD WebEngine或Tauri。面板不自动下载、安装依赖、编译或启动开发服务器；所选CLI及Web资源必须已构建并符合兼容要求。

## 准备三项配置

| 面板字段 | 填写内容 | 示例占位路径 |
| --- | --- | --- |
| Node 执行文件 | 所选Desk构建要求的真实可执行文件 | `/absolute/path/to/node` |
| Desk CLI 文件 | 可信候选的`scripts/desk-web-service.mjs` | `/absolute/path/to/phoenix-desk-tools/scripts/desk-web-service.mjs` |
| 私有状态目录 | 独立、持久且仅当前用户可访问的目录 | `/absolute/path/to/private-desk-state` |

以上是占位路径，不能直接照抄。三个字段默认留空，不自动猜测系统Node或服务目录。Node与CLI必须已存在，路径及祖先不能经过符号链接；Node还须具有执行权限。

兼容性由所选可信CLI根据manifest核对实际Node版本、二进制摘要、平台、架构及构建资源。主版本相同或文件存在不足以证明兼容；缺资源或不兼容时停止，不自动换Node、下载或编译。

状态目录不能是根目录、用户主目录、Desk源码及其父范围、FreeCAD插件目录、工程或业务数据目录。可填写尚不存在的子目录；检查不创建，显式启动时才可能由Desk创建。插件不修权限、不迁移数据，不通过新目录绕过未知服务。

## 打开面板并保存

1. 选择FreeCAD的 **Phoenix Tools** 工作台，点击 **Desk Tools**。命令为`PhoenixTools_DeskTools`，已有面板会被重新显示。
2. 填写三项路径；已有文件或目录可使用“浏览…”，尚不存在的状态子目录可直接输入。
3. 点击 **保存配置**。成功只表示配置校验并写入本插件偏好，不检查后台、运行CLI或打开浏览器。
4. 独立点击 **检查状态**，再按结果选择后续动作。

保存由正式Controller校验三条绝对路径、写入并回读自有配置，再调用原生`saveParameter('User parameter')`持久化。该API保存用户参数全集，不保存CAD；失败恢复选定键及缺失状态，恢复不确定时明确报错。

只打开面板、选择路径或保存均不启动Desk。字段改变会使旧ready失效，恢复旧文本也不能跳过重新检查。

## 让 AI 帮忙填写

点击 **复制配置提示词**，粘贴给有本机操作权限或已连接phoenix-freecad MCP的AI。提示包含三个候选路径字段；空字段要求查找，状态目录为空时给出FreeCAD用户目录下`PhoenixTools/DeskBrowser`建议。字符串始终作为JSON路径数据处理，不执行shell语法。

生成提示不会查文件、创建目录、读取CAD、保存配置或启动进程。只有点击复制才写剪贴板；复制成功不表示配置已经完成。候选和建议仍须独立核实。

AI应先核对Node、manifest和已构建资源，再通过原生面板或正式`save_desk_configuration(App, config)`入口保存。没有本机或MCP操作权限时，只提供填写建议并说明未写入；不能直接改FreeCAD配置文件或声称配置成功。

随后依次检查status，仅absent时ensure，fresh ready后打开Web。不得读取模型、账户、凭据、聊天或最近工作空间来补全配置。

## 检查、启动与打开

| 动作 | 行为 |
| --- | --- |
| **检查状态** | 调用正式CLI的status；不创建缺失目录、不启动后台、不打开Web |
| **启动 / 复用后台** | 显式ensure，兼容实例直接复用；只有确认absent才可能启动并有界等待ready |
| **打开 Desk Web** | 使用当前配置重新检查status，fresh ready后向系统浏览器提交已核验URL |

ready表示CLI确认兼容身份，不证明页面已经加载或所有业务可用。Web地址由身份合同提供，不手填或猜开发端口；浏览器请求被接受后，页面与视觉结果仍须单独核对。

## 状态与失败

| 状态 | 处理方式 |
| --- | --- |
| **已就绪** / `ready` | 可点击打开；打开动作仍重新检查 |
| **未运行** / `absent` | 核对明确状态目录后，按需要点击启动 |
| **暂不可用** / `unavailable` | 保留现状，重新检查；不反复ensure |
| **不兼容** / `incompatible` | 核对候选、Node、资源与协议，保留原服务、锁和登记 |

Node/CLI不存在、资源不匹配、登记无法确认、无效JSON、超时或超量均不能当作ready。Python层验证可信CLI的有界DTO，不再次执行HTTP身份核验；health正常或端口有响应不足以证明兼容。

面板采用异步QProcess，单次CLI最多15秒，stdout最多64KiB、stderr最多8KiB。忙时配置和动作禁用，关闭须等待在途动作结束。异常不自动重试。

超时或超量只回收面板自己创建的CLI helper，不终止登记中的daemon。ensure可能已启动后台，失败后先重新检查status，不立即重复启动。状态变化时重新评估，不按旧ready继续打开。

面板没有Desk停止按钮。关闭面板、关闭浏览器或停止FreeCAD MCP不会停止Desk后台；PID、端口、实例标识和登记均不授予停服权限。

## CAD、聊天与后续能力

连接和配置不修改或保存CAD，也不改变选择。Desk已保存索引/缓存与FreeCAD未保存内存是不同来源；查询范围须显式指定，不能因连接ready就扫描工作空间或迁移数据。

Desk不是聊天服务。聊天使用独立客户端或FreeCAD原生聊天面板，通过MCP调用明确工具；桌面既有会话共享仍是后续目标。

browser-host ready不授予FreeCAD原生渲染、图库生成或其他复杂任务权限。Native能力需要正式运行态feature、统一宿主协调域及任务授权，不换state-dir、清锁或另起实例绕过门禁。

具体状态、分发和验收要求见[Desk集成边界](DESK-INTEGRATION.md)，数据工具见[能力共享](DESK-MCP-CONTRACT.md)。原生UI、浏览器页面、复杂任务与跨平台安装须分别验收；本文不记录宿主使用经过或宣称这些验收已完成。
