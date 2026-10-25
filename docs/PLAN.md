# 0.1.0 最小验证计划（历史）

本页保留初版范围，不代表 0.2.0 的当前能力。当前目标见 [GOALS](GOALS.md)，实际结果见 [验证记录](VERIFICATION.md)，后续范围见 [能力计划](CAPABILITY-PLAN.md)。

项目目录：`phoenix-freecad-tools`；当前 MCP 包：`phoenix-freecad-mcp`。主服务使用 TypeScript/Node.js，项目采用 Apache-2.0。

## 首版闭环

1. 初始化独立项目，锁定官方 MCP SDK，提供 stdio 启动命令。
2. 提供独立 FreeCAD 工作台 `Phoenix FreeCAD`，界面只提供本机服务启动/停止；具体 FreeCAD 能力由 MCP 调用。
3. Node 服务公开 5 个工具：能力清单、连接检测、文档列表、适合窗口、轴测图。
4. 用官方 MCP 客户端完成握手、工具发现和真实工具调用。
5. 使用 `phoenix-freecad-study` 的公开连接板验证视图操作，记录相机结果与源文件指纹。

## 验收条件

- `npm ci`、构建、Node 测试与 Python 测试通过。
- 安装器只管理 `PhoenixFreeCADMCP`，不修改其他插件；服务固定使用本机 9876 端口。
- FreeCAD 插件启动后，`npm run check` 能读到协议版本 1。
- 样例打开后，`npm run verify:mcp -- --document <内部Name>` 可执行轴测图和 fit。
- 模型文件未写入，当前其他文档不关闭、不保存；视图操作只改变样例相机。
- 服务未启动时仍能发现工具；连接调用返回明确错误。

## 首版边界

这是验证通信、安装和 GUI 调度的最小示例。模型创建/修改、任意 Python 执行、截图、文件保存、自动启动、Tool 业务接入和远程连接留在后续阶段。

## 后续阶段

后续按 [零件与图纸设计能力计划](CAPABILITY-PLAN.md) 推进：

1. 先补文档、对象、选中项、几何测量、图像和 TechDraw 只读上下文。
2. 增加事务、操作结果查询和状态校验，完成参数化连接板设计。
3. 完成 TechDraw 页面、标准视图、基本标注和 PDF/SVG 导出。
4. 再扩展复杂特征、装配 Link 与高级图纸；现有 Tool/Wing 集成后置。

GUI 忙碌状态、跨平台安装与公开用例验收贯穿各阶段。内嵌聊天另行调查，不作为设计能力前置条件。

初版工作目录、测试结果与真实联调结论见历史标签及 `verification.json`。不得把未执行的验收标为完成。

## 初版结果

首版5项闭环已完成，真实样例和自动化结果见 VERIFICATION.md。Codex配置已添加；初版归档时，客户端重载后的会话验证尚未进行；后续结果见当前验证记录。聊天面板需求见 ROADMAP.md。本次仅本地归档，不创建远端仓库或推送。
