# Desk 自包含 MCP 资源

适用源码候选 **0.3.2**。这是给 Desk 等可信本机宿主消费的固定资源目录，正式发布、默认部署及用户安装另行验收；不分发 Node 或 FreeCAD 可执行文件。

## 构建与内容

已有 Node 22+ 和锁定开发依赖时，在源码根执行：

```sh
npm run desk:pack -- --output-dir /absolute/path/to/pft-desk-resource
```

构建不下载、安装、启动 FreeCAD，也不读取用户配置或工程。esbuild 0.28.2 为精确开发依赖；SDK 和 Zod 版本与锁文件一致。构建器从正式 Addon 白名单、所需安装文档和启动脚本生成资源，自包含 ESM MCP 入口为 `dist/cli.mjs`，运行时不需要 `node_modules`。输出不含源码映射、机器路径或构建时间；相同输入的文件与清单字节一致。

| 内容 | 用途 |
| --- | --- |
| `addon/PhoenixFreeCADTools/` | 完整 Python Addon、图标及纯Addon的LICENSE/NOTICE/受管理安装说明 |
| `scripts/install-addon.mjs` | 正式受管理安装器，显式目标、预览、归属保护及备份 |
| `scripts/dev-freecad.mjs`、`scripts/thumbnail-worker.mjs` | 当前macOS独立开发worker的准备入口，保持专属profile合同 |
| `dist/cli.mjs` | 实际完整MCP入口；默认25、聊天19、独立worker4，显式发现完整27；可选Desk合同保持 |
| 根LICENSE/NOTICE、`licenses/` | 本项目和实际捆绑SDK server/core、Zod的完整许可；另附构建工具许可 |
| `resource.json`、`.sha256`伴随文件 | 固定白名单、每文件SHA256及清单摘要 |

Addon的README与安装器所需的受管理安装材料保持逐字节一致；手工ZIP使用的安装说明不混入这一合同。可移动整个资源目录；相对图标、脚本和Desk路径保护已单独校验。完整依赖许可不能由构建工具名或仓库根LICENSE代替。

输出目录仅在完整内存构建和校验后创建，资源清单最后发布。已有目录仅在完整文件、目录和字节一致时复用；未知或修改内容保留并拒绝。构建或发布失败不能当作可用资源；没有清单的残留不自动接管，先检查。临时stage不进入分发，目的目录的异常清理只针对仍匹配的自有文件和目录。

## 宿主接入

宿主固定两项部署参数，不从HTTP、最近空间或工程目录接收资源位置：

```text
PHOENIX_FREECAD_MCP_RESOURCE_MANIFEST=/absolute/path/to/pft-desk-resource/resource.json
PHOENIX_FREECAD_MCP_RESOURCE_SHA256=<resource.json 的64字符SHA256>
```

清单使用严格 `schema_version:1`、`plugin_version`、`addon_dir`、`installer_entry`、`mcp_entry`、`files`。Desk真实loader会逐文件核对、冻结白名单，再从冻结目录启动MCP；清单存在或摘要正确不证明FreeCAD已经运行，也不授予Native执行权限。

完整模式和worker使用同一自包含入口。worker必须有本次受控启动得到的明确URL，另设 `PHOENIX_FREECAD_SCOPE=worker`；它只注册4项类型化任务工具。普通25/聊天19或发现27工具不能被当作专属worker。见[worker合同](THUMBNAIL-WORKER.md)。

当前worker启动器仍是macOS独立开发profile适配。宿主必须在整段会话保持冻结资源，直到GUI与MCP两个实际子进程都发出close；超时、收到取消或kill返回均不能代替退出证明。宿主沿自己的Native门禁保有进程和资源，不接管用户窗口、不换profile绕过未知状态。常驻可在同一进程串行处理多个明确文件；跨批次复用、正式安装profile、崩溃恢复和其它平台另验。

Desk负责文件清单授权、任务队列、进度、外部PNG缓存和数据库登记。worker只返回已校验的任务结果；不写Desk数据库或FCStd。宿主仍须复核原始输入修订、完整PNG和自身任务状态，再原子发布缓存；不调用会保存CAD的旧缩略图写回包装器。本资源不意味着Desk设置、批次和缓存闭环已完成。

## 已验证范围

自动化使用真实构建资源在另一目录、没有node_modules的条件下连接官方SDK，核对full25/chat19/worker4及worker状态；实际安装器在独立临时Mod完成dry-run、安装、幂等重装和可恢复卸载。未知内容、发布失败、输出祖先为符号链接以及资源内部Desk状态目录均有拒绝回归。它们不修改用户安装或运行实例。

macOS原生验收由Desk实际loader/frozen资源、实际启动脚本、实际自包含MCP与官方SDK串联：同一自有FreeCAD连续渲染两份独立构造的自包含Part::Feature，源SHA保持；同任务结果复用、异参冲突、取消、错误源SHA拒绝、空staging与双进程实际close后释放资源均已检查。两张PNG已目视核对轴测、Fit和白底。没有写入Desk缓存，也没有自动保存CAD。

这不替代Desk普通批次入口、真实工作空间缓存/数据库、用户手工、全新依赖安装、跨平台或正式Release验收。临时资源路径、实际摘要、样件、图像和运行回执留在Git外。参见[验证记录](VERIFICATION.md)与[Desk能力共享](DESK-MCP-CONTRACT.md)。
