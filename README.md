# Phoenix FreeCAD Tools

![Phoenix](addon/PhoenixFreeCADMCP/Resources/icons/phoenix-wing-mark.svg)

让现有聊天客户端通过 MCP 理解和操作 FreeCAD，并提供可独立使用的中英文工作台工具。当前代码版本 **0.3.0**，采用 Node.js / TypeScript 服务与轻量 Python 插件，应用代码使用 **Apache-2.0**。

聊天继续由客户端管理。FreeCAD 中的 **Phoenix Tools** 提供普通工具、语言设置和桥接启停；Node 服务提供 21 个 MCP 工具，不要求在插件内填写聊天 API Key。

[接入说明](docs/CODEX.md) · [接口协议](docs/PROTOCOL.md) · [架构](docs/ARCHITECTURE.md) · [能力计划](docs/CAPABILITY-PLAN.md) · [验证记录](docs/VERIFICATION.md)

## 当前结果与边界

| 项目 | 当前状态 |
| --- | --- |
| 0.3.0 MCP | 已实现 21 个工具；新增选择、显隐、显式重算及复用 MVC 的业务预览/应用，最终真实验收见验证记录 |
| 参数化零件 | 已实测新建 60 × 40 mm 板、4 × Ø6 mm 通孔，保留 Body、全约束草图和 Pad/Pocket；厚度从 6 改为 8 mm 后校核几何 |
| STEP | 已实测已知参数样例的导出、导入、参数化重建及几何对比；不代表任意 STEP 能自动恢复原始特征历史 |
| TechDraw | 四视图和关联实际几何的 6 mm 孔径尺寸已验收；PDF/SVG/PNG/FCStd 导出、独立重开、模板/坐标/尺寸保留及缩略图检查通过，PDF 渲染目视确认无视图遮挡和越界 |
| 工作台 | MVC 分层、命名修正、正式属性填写、Link 名称同步和单按钮 MCP 启停；0.3.0 新增本机中文/English 设置 |
| 本机接入清理 | 原桥接已停用并备份移出 FreeCAD 加载目录，对应 Codex 旧服务条目已移除 |

接口实现、自动化测试和真实场景验收分别记录。测试数量、0.3.0 新接口/语言的实际结果和归档状态以 [验证记录](docs/VERIFICATION.md) 为准；既有公开人工样例不代表任意工程均已验证，也不代替生产图纸审签。0.2.0/0.2.1 的证据与归档保留原样。内嵌聊天、大型 Tool 服务集成及语言覆盖扩展仍是后续任务。

## 安装与启动

需要 Node.js 22+、npm 和带 GUI 的 FreeCAD。当前真实联调使用 FreeCAD 1.1.0；其他版本及 Windows/Linux 仍需实际安装验收。

```bash
cd /absolute/path/to/phoenix-freecad-tools
npm ci
npm run build
npm run addon:install -- --mod-dir "/absolute/path/to/FreeCAD/Mod" --dry-run
npm run addon:install -- --mod-dir "/absolute/path/to/FreeCAD/Mod"
```

先在 FreeCAD 中核实 Mod 目录。省略 `--mod-dir` 时，安装器只自动选取已存在的受支持 macOS 目录，其他情况提示候选位置。插件目录为 `PhoenixFreeCADMCP`；安装器校验本项目标记和文件内容，保护已有修改，并保留 Mod 目录外的恢复副本。

插件加载后，在工作台下拉框选择 **Phoenix Tools**，使用同一个 MCP 按钮：

- 绿色三角形：**启动 MCP · 已停止**。
- 红色方块：**停止 MCP · 运行中**。
- 停止期间：**MCP 正在停止**，等待完成后再启动。

命令 ID 为 `PhoenixTools_ToggleMCP`，按钮同时显示图标和状态文字。选择工作台不会自动启动服务，重启 FreeCAD 后需再次启动桥接。Node MCP 由聊天客户端启动，工作台按钮启动的是 FreeCAD 内的本机 HTTP 桥接，两者职责不同。

当前 FreeCAD 有未保存修改时，可以在核实安装位置后，通过 Python 控制台启动桥接，无需为连接检查重启：

```python
import os, sys, FreeCAD as App
addon_dir = os.path.join(App.getUserAppDataDir(), "Mod", "PhoenixFreeCADMCP")
if addon_dir not in sys.path:
    sys.path.insert(0, addon_dir)
import phoenix_freecad_bridge
phoenix_freecad_bridge.start_server()
```

如果安装到自定义 Mod 目录，应使用已核实的插件目录。该方式只启动桥接，不补注册尚未加载的工作台菜单。升级后，磁盘文件不会自动替换已加载模块；应先停止本服务并确认请求结束，再按当前会话情况重新加载，或在保存工作后正常重启。

## 聊天客户端接入

启动 FreeCAD 桥接后运行 `npm run check`，检查 `protocol_version:1`、`running:true`、插件版本和 `supported_methods`。离线工具清单不证明当前内存中的插件支持所有新增能力。

支持 stdio MCP 的客户端使用以下配置，并替换为本机绝对路径：

```json
{
  "mcpServers": {
    "phoenix-freecad": {
      "command": "/absolute/path/to/node",
      "args": ["/absolute/path/to/phoenix-freecad-tools/dist/cli.js"]
    }
  }
}
```

具体 Codex 命令见 [接入说明](docs/CODEX.md)。客户端需重新连接或重新加载 MCP；既有聊天工具清单尚未刷新时，可用真正走 MCP stdio 的 helper：

```bash
node scripts/call-tool.mjs get_connection_status /absolute/path/to/connection.args.json
```

示例参数文件内容为 `{}`。helper 不直接绕过 MCP 调内部 HTTP，也不会替当前聊天刷新原生工具清单。

默认桥接地址为 `http://127.0.0.1:9876/`；`PHOENIX_FREECAD_URL` 可指定允许的本机 HTTP 根地址，`PHOENIX_TIMEOUT_MS` 可调整 Node 等待时间。程序不自动读取 `.env`。

## 21 个 MCP 工具

| 分组 | 工具 |
| --- | --- |
| 连接 | `get_capabilities`、`get_connection_status`、`list_documents` |
| 设计上下文 | `get_document_info`、`list_objects`、`get_object_info`、`get_selection` |
| 几何 | `measure_geometry` |
| 图纸检查 | `list_drawing_pages`、`inspect_drawing_page` |
| 相机 | `fit_view`、`set_axonometric_view`、`set_view` |
| 通用操作 `general_actions` | `set_selection`、`set_visibility`、`recompute_document` |
| Phoenix 业务 `phoenix` | `prepare_part_changes`、`apply_part_changes` |
| 图像 | `capture_view` |
| 高级脚本 | `execute_script`、`get_script_status` |

文档、对象和页面参数使用内部 `Name`，不是显示 `Label`。常用读取不重算、不保存、不修改模型；测量只报告当前 Shape，不证明缓存最新、完整装配变换已展开或工程图检查通过。单面体积返回 `null` 且 `volume_applicable:false`；包围盒仅为内核包络，不能用来证明精确间隙。相机工具改变视角；`capture_view` 要求目标文档的三维视图处于前台，只在明确要求图像时导出，不截图桌面、不改变相机，其临时文件读取后清理。

`execute_script` 在 GUI 主线程执行，与 FreeCAD Python 控制台同权限，**不是沙箱**。脚本可修改模型、访问文件、自行保存或关闭文档，调用必须符合具体任务授权。框架不额外保存，也不承诺异常自动回滚。

脚本使用 `requestId` 去重和查询；超时不代表取消。先查询原 ID，重放使用相同 ID 和相同代码。记录仅限当前桥接服务会话，以 `script_session_id` 识别；重载桥接模块或重启 FreeCAD 会重置。完整终态结果保留最近 20 份，最多记住 4096 个历史 ID；结果过期的 ID 在同一会话中仍拒绝重新执行。状态和容量边界见 [协议](docs/PROTOCOL.md)。

### 通用操作与业务差异预览

三个通用操作均要求明确指定已经活动且空闲的文档。`set_selection` 只替换该文档的完整对象选择，空数组清除此文档选择，保留其他文档；`set_visibility` 对明确对象设显隐，事务可撤销；`recompute_document` 显式执行原生重算并检查失败状态，可能更新几何缓存。三者均不自动保存，也不自动切换目标文档。

Phoenix 业务通过 `prepare_part_changes` / `apply_part_changes` 复用工作台 Model/Controller。支持命名修正、Link Label 同步、属性初始化/推荐和正式字段填写。先准备完整差异，例如对虚构对象预览命名：

```json
{"document":"ExampleDocument","objects":["ExamplePart"],"operation":"normalize_names"}
```

将返回的 `rows` 中旧值、新值、冲突及 `can_apply` 展示给用户，核对本次授权后才应用返回的 token。预览不修改模型，但会创建内存记录；有效期五分钟，最多保留 32 份。完整预览超过 128 KiB 会拒绝，不会截断差异后仍允许应用。

应用前重新验证文档实例、快照和来源，写入可撤销、不保存。成功 token 在仍保留且未过期时可返回 `replayed:true` 的历史摘要，**不会再次执行，也不会在用户撤销后自动重做**；该摘要不证明对象当前状态，应以 `get_object_info` 核对。超时、失败、过期或未知 token 不盲目重放或换 token 自动应用，先检查结果，再重新预览。操作示例和细则见 [接入说明](docs/CODEX.md) 与 [协议](docs/PROTOCOL.md)。

## 独立工作台工具

| 按钮 | 作用 |
| --- | --- |
| 命名修正 | 根据正式属性预览所选对象的 Label，检查冲突后确认修改；不改内部 Name 或文件名 |
| 填写零件属性 | 编辑正式 BOM 字符串字段，初始化缺失字段或从明确 Label 保守推荐；先列差异，再确认写入 |
| 同步链接名称 | 将所选 App::Link 实例 Label 同步到直接来源；不修改来源，不自动修复断链或猜测歧义 |

正式字段为 `PartNumber`、`PartVersion`、`TypeCode`、`ModelSeries`、`PartName`，属性组为 `CustomDefine`。推荐不覆盖已有非空值；属性填写不向 Link 来源转发写入。普通按钮经事务修改，默认不保存，不依赖 Node、MCP 或大型 Tool 服务。

实现按 Model、Controller、View、Facade 分工，详见 [架构](docs/ARCHITECTURE.md)。聊天通过两个业务 MCP 工具调用同一份规则和 Controller，普通按钮仍可独立使用。

### 中文 / English 设置

在 **Phoenix Tools** 菜单选择 **设置 / Settings**，选择“简体中文”或“English”，点击“保存并应用”。默认中文，确认后保存本机偏好，立即刷新已有菜单、按钮、提示和 MCP 状态文字，不重启、不启停 MCP、不改模型；新打开的业务对话框使用所选语言。取消不保存。

设置命令为 `PhoenixTools_Settings`，只在菜单增加入口，不增加工具栏按钮。机器命令、MCP 工具/参数及正式字段名保持不变。本轮未实现跟随 FreeCAD 语言；部分动态业务校验和原生错误仍可能显示原文，设置中已说明。

## 公开演示与验证

所有演示使用新建的公开人工几何，不包含用户工程、真实零件或机器路径。

| 文件 | 范围 |
| --- | --- |
| [parametric_plate.py](examples/parametric_plate.py) | 已实测参数板、完全约束草图、厚度修改和几何校核 |
| [step_parametric.py](examples/step_parametric.py) | 已实测 STEP 往返与已知参数重建，不恢复任意历史 |
| [workbench_tools_demo.py](examples/workbench_tools_demo.py) | 已实测属性初始化与推荐、命名、两个 Link 同名、原生撤销重做及对话框取消 |
| [techdraw_plate.py](examples/techdraw_plate.py) | 已实测四视图、6 mm 关联尺寸、导出、独立重开及最终布局目视检查；不是生产图纸审签 |

演示应按文件中的前置条件和任务授权执行，不能对其他工程盲目重放。已经建立演示文档与图纸后，可逐项核验结构化接口：

```bash
node scripts/verify-capabilities.mjs --image /absolute/path/to/new-view.png --report /absolute/path/to/new-report.json
```

该脚本只针对固定公开演示文档读取、测量和检查，再改变其相机并获取图像；不重算或保存模型。输出文件必须尚不存在，默认不向项目写入文件，报告移除机器路径、会话 ID、外部文档信息和 Base64。

## 开发、卸载与后续

```bash
npm run build
npm test
npm run test:python
npm pack --dry-run
```

自动化覆盖协议、schema、安装保护、GUI 调度、MVC 和脚本记录等；真实模型、界面、图纸排版、重开及跨平台安装另行验收。最新测试数量、本地归档与包清单以 [验证记录](docs/VERIFICATION.md) 和 [隐私审查](docs/PRIVACY-REVIEW.md) 为准。

卸载本插件前先停止其服务，再执行：

```bash
npm run addon:uninstall -- --mod-dir "/absolute/path/to/FreeCAD/Mod"
```

安装器只管理带本项目标记且内容校验通过的安装，不覆盖未知文件或用户修改。当前未发布远端；发布和收录另行安排。

后续优先扩展真实设计和图纸案例，再封装高频建模/出图写入，并完善业务和语言覆盖，见 [TODO](docs/TODO.md)。内嵌聊天见 [路线图](docs/ROADMAP.md)，长期方向见 [愿景](docs/VISION.md)。大型 Tool 的下载、编译和启动后置，不是当前轻量插件的运行依赖。

源码许可见 [LICENSE](LICENSE)，依赖、移植规则和组织 Logo 来源见 [NOTICE](NOTICE) 与 [品牌资源](docs/BRANDING.md)。桥接仅绑定本机，拒绝浏览器 Origin/Sec-Fetch 请求，但没有本机进程身份认证或多用户隔离，仅用于可信本机环境。

目录、工作台、npm 包与 CLI 的对应关系见 [命名约定](docs/NAMING.md)。
