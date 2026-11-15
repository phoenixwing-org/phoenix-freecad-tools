# Phoenix FreeCAD Tools 0.4.2 本地候选

本地归档，待人工试用。MCP 包为 `@phoenix-wing/phoenix-freecad-mcp`，命令仍为 `phoenix-freecad-mcp`。以归档中的提交、包清单和 SHA-256 区分同版本的本地构建。

- 固定端口被占用时提示切换为自动发现实例，或取消本次启动。内嵌聊天核对当前窗口的桥接和受限工具清单。见[多实例连接](INSTANCE-ROUTING.md#042-固定端口冲突与聊天连接)。
- 受控建模可以从空白 FreeCAD 窗口预览平垫、矩形和圆形拉伸。原生确认后才创建可编辑草图和 Pad，并反验结果。撤销移除建模节点，空文档壳可能保留。CAD 文件由用户保存。
- 原生聊天保留 ChatGPT 登录，加入 Codex · OpenAI API Key 和 DeepSeek API 直连。两种 Key 按服务商和地址分别保存，可清除。见[聊天说明](CHAT-USAGE.md)。
- 当前文档面板可按显式字段读取标准 Link 系列引用，保留原生对象和子引用身份。范围见[属性读取合同](PROPERTY-READING.md)。

手工安装前查阅[安装说明](ADDON-INSTALL.md)，试用步骤见[内置帮助](../addon/PhoenixFreeCADTools/HELP.md)与[验收计划](COMMON-CAD-TOOLS-PLAN.md)。

当前评分 **68/100**。macOS 的独立 FreeCAD 1.1.4 验证覆盖受控建模和受限 MCP，实际账号、凭据隔离与人工验收的待验范围见计划。Windows DPAPI 测试需要 Windows 环境。此次归档用于本机恢复和试用。
