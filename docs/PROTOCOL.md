# 本机 FreeCAD 桥接协议 v1

MCP stdio 协议由官方 TypeScript SDK 实现。本文件描述 Node 与 FreeCAD 之间的内部 JSON HTTP 协议，两者不同。

默认地址：`http://127.0.0.1:9876/rpc`。仅 `POST`，`Content-Type: application/json`，请求体上限 64 KiB。桥接拒绝浏览器 Origin/Sec-Fetch 请求，仅绑定 IPv4 loopback，检查 Host。Node 使用 `node:http`，不会附加浏览器风格的 Sec-Fetch 请求头。

```json
{"method":"view_axonometric","params":{"docName":"SampleDocument","fit":true}}
```

```json
{"ok":true,"data":{"document":"SampleDocument","view":"axonometric","fit":true}}
```

```json
{"ok":false,"error":{"code":"DOCUMENT_NOT_FOUND","message":"指定文档未打开。"}}
```

| 方法 | 参数 | 结果 |
| --- | --- | --- |
| `ping` | `{}` | `{pong:true}` |
| `get_status` | `{}` | 版本、running、GUI 调度状态；不依赖 GUI 线程 |
| `list_documents` | `{}` | 文档内部 Name 数组 |
| `view_fit` | `{docName?:string}` | 对目标文档执行 fitAll |
| `view_axonometric` | `{docName?:string,fit?:boolean}` | 轴测图，fit 默认 false |

未知方法或参数均拒绝。省略 docName 使用当前活动文档。GUI 请求通过 Qt queued signal 排入主线程；排队超时在开始前取消，已经开始的操作超时只返回状态，不强制取消。Node 等待超时同样不自动重试。

本地程序可访问该端口，首版没有身份认证或多用户权限隔离；只用于可信本机环境。更高权限或远程功能需另行设计。插件菜单提供手动启停，不自动加载外部脚本。
