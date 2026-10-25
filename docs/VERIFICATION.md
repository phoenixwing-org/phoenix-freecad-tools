# 原型验证记录

日期：2026-10-24。平台：macOS，Node 22.23.1，FreeCAD 1.1.0，嵌入 Python 3.11.14。

## 自动化验证

- 在最终项目目录独立执行 `npm ci` 与 TypeScript 构建，通过。
- Node 测试 **10/10 通过**：真实 stdio MCP 握手、工具发现、相机调用、错误回传、连接异常与安装保护。
- Python 测试 **19/19 通过**：协议、HTTP、浏览器请求拦截、视图stub、GUI线程派发、排队取消、运行超时与防重入。
- 发布包清单经复核，排除 Python 缓存、模型文件、个人路径、凭据和本地配置。

## 真实 FreeCAD 联调

独立 `PhoenixFreeCADMCP` 已安装，并在当前 FreeCAD 中启动于 `127.0.0.1:9876`，无重启。工作台注册成功，用户已确认看到界面。工作台界面仅提供服务启动/停止，fit和轴测作为MCP调用能力。

通过官方 TypeScript MCP 客户端完成以下真实链路：

`MCP stdio → Node.js 服务 → JSON HTTP → FreeCAD Qt 主线程`。

已发现5个工具，连接状态与文档查询成功；对指定公开样例执行 `set_axonometric_view` 和 `fit_view`，均成功返回目标文档。随后独立读取相机方向，并检查实际渲染图，确认模型以轴测图适合窗口显示。

公开样例来自 `phoenix-freecad-study`，相对路径为 `04-自动出图/样本/8101.001-H-PX-连接板.FCStd`。样例源文件 SHA256 前后均为：

```text
74741befb86a263fe0e35590651cc01822d98bff682a2a9948dcb16f5ebfbbed
```

未保存或修改模型文件。在验收检查时，预先记录的其他文档对象名称、标签及状态保持一致；不记录那些工程的名称或路径。机器可读结果见 [verification.json](verification.json)。

![公开连接板轴测图](images/study-axonometric.png)

初次以 White 模式导出的预览为空白，未纳入交付。刷新当前视图并使用 Current 背景导出后，图像已人工确认。截图导出不是本版 MCP 工具。

## Codex 适配边界

已添加独立 `phoenix-freecad` 配置，CLI读取确认其为启用的stdio服务，现有MCP配置保留。真实协议与FreeCAD调用通过。尚未另开Codex模型会话验证自然语言选工具，客户端需重新加载MCP后使用。

## 命名迁移复验（2026-10-24）

- 构建通过；Node 16项、Python 19项测试通过，包含新增的安装身份迁移与回滚测试。
- 合法旧安装已完整校验并移到 Mod 外备份。当前 Mod 仅保留新 `PhoenixFreeCADMCP` 安装目录。
- 当前会话未重启，活动工作台为 `PhoenixFreeCADMCPWorkbench`，显示名为 `Phoenix FreeCAD`；仅有 `PhoenixTools_StartMCP` / `PhoenixTools_StopMCP` 服务按钮。
- 新 Python 模块从新安装目录加载，状态返回 `addon_name: PhoenixFreeCADMCP`、`running: true`，端口仍为9876。
- 旧工作台已注销，当前会话遗留的旧命令对象已禁用；历史控制台文本保持不变。
- npm 实际打包清单包含新插件、模块和logo，不含旧目录和Python缓存。历史v0.1.0归档保留原样。
- 项目目录改为 `phoenix-freecad-tools`，npm/CLI 身份保持稳定；Codex 的既有服务条目已指向新目录入口。新增零件与图纸能力仅列入计划，不计为已通过的功能。

## 已知边界

首版只含状态、文档列表和相机操作，不支持模型写入、任意代码、截图、自动启动或内嵌聊天。Windows/Linux 尚无真实GUI安装验收；CI配置已提供，本次未运行远端CI。
