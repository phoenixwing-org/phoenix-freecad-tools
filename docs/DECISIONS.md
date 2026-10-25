# 讨论与架构决定

日期：2026-10-24。记录已明确的项目方向和适用边界，不复制会话中的私有工程信息。

## 名称

项目目录采用 `phoenix-freecad-tools`，表达可持续扩展的 FreeCAD 工具集合；npm 包和 CLI 仍是 `phoenix-freecad-mcp`，作为 MCP 接入模块。FreeCAD 插件目录是 `PhoenixFreeCADMCP`，工作台显示 `Phoenix FreeCAD`。

工作台通用命令采用 `PhoenixTools_` 前缀。当前已注册 `PhoenixTools_StartMCP`、`PhoenixTools_StopMCP`；普通业务工具后续使用同一前缀，不要求依赖 MCP。插件技术类名保留 `PhoenixFreeCADMCPWorkbench`。完整映射与迁移约定见 [命名](NAMING.md)。

## 为什么以 Node.js / TypeScript 为主

本项目由独立 Node MCP 服务向聊天客户端注册工具。选 TS 是为了后续共用 Phoenix Tool/Wing 的类型、协议与业务算法。本项目采用独立的 Node 服务与固定命令 Python 桥，不从其他 MCP 服务派生代码。

FreeCAD 的模型、视图与 TechDraw 操作继续由 Python 执行。Python 插件保持小而明确的固定命令接口，GUI 操作排回 Qt 主线程。Node 不直接代替 FreeCAD 几何内核，也不通过模拟点击实现操作。

## 服务边界

应用代码独立实现，采用官方 MCP SDK 的标准用法以及通用 GUI 线程、超时和宿主边界设计。桥接服务使用 127.0.0.1:9876。

## 最小示例范围

先验证安装、启动、连接、工具发现、文档列表、fit 和轴测图。使用 `phoenix-freecad-study` 的公开连接板模型作为真实 FreeCAD 样例，私有生产工程不进入测试或文档。

视图调整改变相机，因此 MCP 工具不标为严格只读；它们不保存、关闭或修改模型。首版不开放任意 Python、文件写入、模型修改、截图、远程监听与自动启动。GUI 执行超时不代表已经取消，客户端不自动重试。

## 后续如何复用 Tool

先完成 FreeCAD 原生设计上下文、参数化零件和 TechDraw 出图，再接 Tool/Wing，阶段见 [能力计划](CAPABILITY-PLAN.md)。

- 纯 TS 规则与 DTO：通过正式公共包依赖复用 Wing，例如 `cad-core`、`cad-contracts`，实际接入时核对并锁定版本。
- 图纸检查、预览、任务与数据库：初期通过 Tool HTTP 接口调用，由原服务统一管理存储和队列。
- 需要独立发行的业务服务：再抽成可发布模块，通过文件系统、配置、数据库和原生执行端口注入，共用一份算法。
- FreeCAD Python：保留自有进程与当前 GUI 的边界。面向独立批量进程的脚本不能原样用于用户未保存的会话。

已有保存 XML/缓存检查不能宣称是当前 GUI 的实时状态；物性模型与 Native 测量试点也不能直接宣称为完整装配质量服务。

## 许可决定

本项目原创应用代码采用 **Apache-2.0**，完整文本见根目录 LICENSE。参考来源见 NOTICE。官方 MCP SDK 的实际依赖许可证随依赖保留。

未来复用第三方代码时，须保留其适用的版权、许可证和来源声明。Study 样例是外部 MIT 资源，仅从用户已有公开仓库打开，没有复制进本项目。

参考：[Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0)、[Apache 对 MIT 等第三方许可的说明](https://www.apache.org/legal/resolved.html#category-a)。

## 界面与能力的区别

首版工作台界面只负责服务管理，fit 和轴测属于 MCP 真实能力，不在工具栏重复提供示例按钮。后续可加入独立业务按钮，与 MCP 共用实现。

`fit_view` → `view_fit` → FreeCAD `activeView().fitAll()`；`set_axonometric_view` → `view_axonometric` → FreeCAD `activeView().viewAxonometric()`。连接板仅为验证样例，工具可对指定的其他已打开文档工作，未把样例名写进业务代码。

当前工作台仅提供服务管理，视图工具保留为MCP能力。
