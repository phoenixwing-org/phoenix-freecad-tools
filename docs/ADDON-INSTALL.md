# Phoenix Tools — 手工安装 / Manual installation

本说明用于0.3.2统一候选的纯 Python Addon 分发包 `phoenix-freecad-tools-addon-0.3.2.zip`，也可直接作为包内README阅读。0.3.1后未发布的内部0.4–0.10改进合并到此候选，版本号不表示已完成正式发布或全平台验收。本地构建可以运行 `npm run addon:pack`，产物位于忽略提交的 `artifacts/`。是否已经发布，以 [GitHub Releases](https://github.com/phoenixwing-org/phoenix-freecad-tools/releases) 为准；实际插件版本见同目录 `package.xml`。

Phoenix Tools 提供命名修正、正式零件属性填写、Link 名称同步和中文 / English 设置。这些普通工作台按钮**不需要 Node.js、聊天客户端或 API Key**。通过聊天操作 FreeCAD 时，还需要单独安装 Node MCP 并配置支持 MCP 的聊天客户端。

## 安装前

- 使用带 GUI 的 FreeCAD。优先适配 FreeCAD 1.1.0；macOS、Windows、Linux及各FreeCAD版本的GUI安装、升级和卸载须分别验收。源码测试不证明平台验收通过，具体支持范围以对应发行说明为准。
- 有对应正式 Release 时从上述页面下载 ZIP 与 `SHA256SUMS`（单独构建时为同名 `.zip.sha256`）。macOS/Linux 可用 `shasum -a 256 phoenix-freecad-tools-addon-0.3.2.zip`，Windows PowerShell 可用 `Get-FileHash .\phoenix-freecad-tools-addon-0.3.2.zip -Algorithm SHA256`，将结果与 `SHA256SUMS` 中的同名条目核对。
- 先保存所有工作。若 Phoenix MCP 正在运行，停止桥接并等待已有请求结束；超时不代表脚本已经取消。确认结束后正常退出 FreeCAD，再安装文件。不要强制关闭仍有未保存修改的会话。

## 找到正确的 Mod 目录

不要根据操作系统或某个 FreeCAD 版本猜路径。在退出前，打开 FreeCAD 的 Python 控制台，运行以下只读代码：

```python
import os
import FreeCAD as App
print(os.path.join(App.getUserAppDataDir(), "Mod"))
```

输出目录就是本说明使用的用户 Mod 目录；需要时创建该目录。`App.getUserAppDataDir()` 可能随版本、安装方式或配置不同，实际输出优先。若使用自定义插件搜索路径，请先在该 FreeCAD 实例核实，不同时往多个位置复制。

## 放入插件并启动

ZIP 必须只有一个顶层目录：

```text
PhoenixFreeCADTools/
├── Init.py
├── InitGui.py
├── pft_*.py
├── package.xml
├── Resources/
│   └── icons/
├── LICENSE
├── NOTICE
├── MANIFEST.json
└── README.md
```

`pft_*.py` 表示包内全部 Python 功能模块，不能只复制入口文件。

1. 将 ZIP 解压到临时目录，检查上述层级。
2. 如果 Mod 内已有 `PhoenixFreeCADTools`，先按下面“升级与恢复”处理，不覆盖合并文件。
3. 把解压得到的整个 **`PhoenixFreeCADTools` 目录**放入已核实的 Mod 目录。
4. 检查最终入口是 **`Mod/PhoenixFreeCADTools/InitGui.py`**。避免多出 `Mod/Mod/`、仓库外层目录、`addon/` 或重复的 `PhoenixFreeCADTools/PhoenixFreeCADTools/` 层级。
5. 正常启动 FreeCAD，在工作台下拉框选择 **Phoenix Tools**。

本包采用手工目录安装，不表示已被 Addon Manager 收录，也不承诺通过 Addon Manager 一键安装此 ZIP。复制新文件不会自动替换当前进程已加载的 Python 模块；安装和升级都按保存后正常重启的流程进行。

## 使用普通工具与语言设置

先在公开测试文档或副本中选择对象，再使用命名修正、零件属性或链接同步。检查预览差异后确认；操作经事务修改，默认不保存文件。撤销与保存仍由用户控制。

在 **Phoenix Tools** 菜单中打开 **设置**，选择“简体中文”或“English”，点击“应用设置”。默认中文，偏好仅保存在本机；确认后立即刷新本工作台已有按钮、菜单、提示和 MCP 状态文字，不需要重启。新打开的业务对话框采用所选语言；部分动态业务校验和 FreeCAD 原生错误仍可能显示原文。本版没有“跟随 FreeCAD 语言”选项。

工具条全部业务功能使用原创图标，默认图标加文字；设置中的“工具条显示”可选“仅图标”，保存后立即应用。前三个MCP状态、启停、齿轮控件固定仅图标，不修改FreeCAD全局样式。当前文档Dock支持默认跟随或下拉固定查看，视图与外观统一放在工具条。

## 需要聊天时

普通按钮可以直接使用。聊天接入则有两个独立进程：

1. 在 Phoenix Tools 中点击灰色三角图标启动 FreeCAD 内的本机桥接。状态圆点转绿，动作按钮变成灰色方块；点击方块默认需要确认停止。圆点只显示状态，点击查看详情，不执行启停。
2. 另行安装 **Node.js 22+ 和 `phoenix-freecad-mcp` Node 服务**，按 [聊天客户端接入说明](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/master/docs/CODEX.md) 配置并重新连接客户端。

桥接启停按钮不会启动聊天客户端或 Node MCP，不创建聊天会话，也不要求填写聊天 API Key。独立的“AI 聊天”入口由用户显式连接官方 app-server；启用工具模式时由该客户端启动 Node MCP，使用专属 ChatGPT 登录。选择工作台不会自动启动桥接；默认在正常重启 FreeCAD 后需要再次点击启动。可在灰色齿轮设置中开启“FreeCAD 启动时自动启动 MCP”，下次启动生效；手动停止始终弹出确认，没有关闭选项。正在停止时圆点为红色、动作按钮不可用，悬停显示具体状态，等待结束后再启动。

桥接仅供可信本机环境使用，默认地址为 `http://127.0.0.1:9876/`，没有本机进程身份认证或多用户隔离。已连接的聊天客户端可以按调用读取文档信息；高级脚本拥有与 FreeCAD Python 控制台相同的权限，调用前应明确任务范围。只安装普通工作台工具时，无需启动桥接。

## 升级与恢复

1. 停止 Phoenix MCP，等待请求结束，保存工作并正常退出 FreeCAD。
2. 将旧的 **整个 `PhoenixFreeCADTools` 目录**移到 **Mod 目录之外**的备份位置，保留全部文件。不要只在 Mod 内改名为 `.bak` 或留一份副本，以免重复加载。
3. 按安装步骤放入新 ZIP 的完整目录。不要向旧目录覆盖解压，不混用不同版本的 Python 模块。
4. 正常启动 FreeCAD，核对 Phoenix Tools、语言设置和普通按钮；需要聊天时再检查桥接与 Node 连接。

升级异常时，保存并退出，先将新目录移到 Mod 外，再将原完整备份恢复为 `Mod/PhoenixFreeCADTools`，然后正常启动。备份核实可用后再自行清理，避免覆盖仅存副本。

### 与 npm 安装器的边界

本手工 ZIP **不带** `.phoenix-freecad-tools-install.json` 所有权标记。npm 安装器只管理自己创建且清单内容一致的目录，因此会拒绝更新或卸载这个手工目录；这是保护已有文件的预期行为。

- 手工升级继续使用上面的完整替换流程。
- 手工安装改用 npm 安装器时，先把手工目录完整移到 Mod 外备份，再执行全新受管理安装。
- 原 npm 安装改用本 ZIP 时，也先完整备份旧目录再安装。保留备份中的标记，但**不要把旧标记复制进新目录，也不要伪造标记**。

安装方式不改变 FreeCAD 模型文件。手工目录中若有自行修改的代码，先在备份中核对，再决定如何迁移；不要直接覆盖新版本模块。

## 手工卸载与常见问题

卸载时停止桥接、等待请求结束、保存工作并正常退出 FreeCAD，再将 `PhoenixFreeCADTools` 整个目录移到 Mod 外保留备份。正常重启后工作台不再加载。另行安装的 Node MCP 和聊天客户端配置需分别移除；本机语言偏好可以保留，不需要清理整个 FreeCAD 配置。

| 现象 | 核对项 |
| --- | --- |
| 没有 Phoenix Tools | 确认操作的是同一 FreeCAD 实例的用户 Mod，检查 `Mod/PhoenixFreeCADTools/InitGui.py` 层级，正常重启；查看报告视图中的 Python 导入错误 |
| 菜单或代码还是旧版 | 当前进程可能仍持有旧模块；保存并正常退出，检查没有重复安装目录后重新启动 |
| 按钮启动了但聊天看不到工具 | 按钮只启动 FreeCAD 桥接；单独安装/配置 Node MCP，并让聊天客户端重新连接或刷新工具列表 |
| 桥接启动失败或端口占用 | 查看报告视图，确认本机没有另一个桥接实例占用地址；不要为排错强制关闭未保存的 FreeCAD |
| npm 安装器拒绝已有目录 | 手工安装没有所有权标记，或受管理文件已修改；先完整备份移出，再选择一种安装方式，不绕过校验 |

## English quick steps

1. Download the official ZIP and `SHA256SUMS` from [GitHub Releases](https://github.com/phoenixwing-org/phoenix-freecad-tools/releases), then verify the checksum.
2. In FreeCAD's Python console, print `os.path.join(App.getUserAppDataDir(), "Mod")` after importing `os` and `FreeCAD as App`. Use the actual output, not a guessed platform path.
3. Stop the bridge, wait for requests to finish, save your work and exit FreeCAD normally. Move any previous addon directory completely outside Mod as a backup.
4. Extract the single top-level `PhoenixFreeCADTools/` directory into Mod. The entry point must be `Mod/PhoenixFreeCADTools/InitGui.py`, without an extra wrapper directory.
5. Restart FreeCAD and choose **Phoenix Tools**. Ordinary workbench tools and language settings work without Node. Chat access requires the separately installed Node MCP service and client configuration.
6. Upgrade by replacing the whole directory, never by merging files. This manual ZIP has no installer ownership marker; the npm installer intentionally refuses to manage it. Do not copy or fabricate a marker.

FreeCAD 1.1.0 is the initial compatibility target. Native installation, upgrade and removal require separate tests on every advertised platform and FreeCAD version. This is a manual addon package, not a claim of Addon Manager listing or one-click ZIP support.

源码与问题反馈：[GitHub 仓库](https://github.com/phoenixwing-org/phoenix-freecad-tools) · [Issues](https://github.com/phoenixwing-org/phoenix-freecad-tools/issues)。许可见包内 `LICENSE`，第三方归属与 Logo 来源见 `NOTICE`；FreeCAD 和 Node.js 不包含在本 ZIP 中。

## 三个业务工具的对象选择

命名修正、链接同步和零件属性可直接打开。表格“处理”列勾选本次对象；没有树选择时预勾合适零件级节点，有选择但全部不适用时留空。内部特征需手动勾选，属性不向 Link 来源转发写入。切换勾选不改变树选择，清空不能提交；确认前查看最终差异，操作可撤销且不自动保存。

## 旧技术名称迁移

当前 Addon 为 PhoenixFreeCADTools，工作台注册类为 PftWorkbench，桥接模块为 pft_bridge。已有受管理的 PhoenixFreeCADMCP 或 PhoenixMCP 可由源码安装器核验后迁移；旧标记和全部文件必须一致，多目录并存或用户修改时保留原状。手工 ZIP 用户先将旧整目录移出 Mod 再放入新目录，禁止两份同时加载。Node MCP 命令仍为 phoenix-freecad-mcp。

## 可选 Desk 与聊天入口

当前0.3.2整合了内部迭代形成的Desk Tools和原生AI聊天入口。Desk 面板不要求 MCP 运行，但检查或启动 Desk 需要用户选定兼容的 Node、已构建 CLI 和私有状态目录；打开面板不会自动运行。详细步骤见 [Desk 操作说明](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/develop/docs/DESK-USAGE.md)。当前仍是本地源码候选，没有 Desk 下载向导或正式捆绑包。

AI聊天是独立原生客户端，明确连接及官方ChatGPT登录后可以发送消息。首行文档/轴测Fit/外观图标旁的齿轮收纳路径、工具、模型/字号、帮助与诊断；主面板按连接状态显示连接或登录，不自动重建已有聊天。命名修正、属性与链接同步仍可独立使用。

当前 Desk 面板可点击“复制配置提示词”，把当前路径或空字段查找要求复制给 AI；该动作不会保存配置或启动后台。详情见 Desk 操作说明。
