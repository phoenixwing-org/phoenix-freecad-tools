# Codex 优先接入

本原型优先验证本机 Codex 的 stdio MCP 配置。它不依赖特定模型 ID；其他支持该传输和协议版本的 MCP 客户端可另行验证。

## 注册

先执行 `npm ci`、`npm run build`，安装并启动 FreeCAD 插件。然后用本机 Node 的绝对路径和项目绝对路径注册：

```bash
codex mcp add phoenix-freecad -- /absolute/path/to/node /absolute/path/to/phoenix-freecad-tools/dist/cli.js
codex mcp get phoenix-freecad --json
codex mcp list
```

此配置增加独立服务条目，不替换已有 FreeCAD MCP。需要客户端重新连接或重新加载 MCP 后才能发现新工具；注册成功不等于当前既有聊天已经刷新工具清单。

## 演示问法

- “通过 phoenix-freecad 检查 FreeCAD 是否已连接。”
- “列出当前打开文档的内部名称。”
- “把指定的公开连接板文档切换到轴测图并适合窗口。”

请使用新服务的 `set_axonometric_view` 和 `fit_view`；不需要任意 Python 代码。打开样例仍由 FreeCAD 文件菜单完成。

## 已执行与待验证

本机已增加 `phoenix-freecad` Codex 配置，并用 `codex mcp get` 确认启用。官方 TypeScript MCP 客户端已完成真实 stdio 握手、发现工具并调用正在运行的 FreeCAD 插件。尚未另开一次 Codex 模型会话测试自然语言选工具，不把协议测试表述成这一项已完成。

参考：[官方 Codex MCP 命令说明](https://learn.chatgpt.com/docs/developer-commands#codex-mcp)。本机核对的 CLI 为 0.160.0；CLI 的 `mcp add --help` 确认支持 stdio。
