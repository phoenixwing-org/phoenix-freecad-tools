# FreeCAD Tools 发布步骤（个人维护、组织发布）

本文说明如何由个人维护者发布 Phoenix FreeCAD Tools，npm 发布到组织 `@phoenix-wing`。流程核对日期：2026-11-06。当前仓库记录为 **0.3.2 本地源码候选，尚未正式发布**；本文是操作步骤，不是发布回执。

发布门槛以[开发与发布计划](RELEASE-PLAN.md)为准，版本身份见[版本约定](VERSIONING.md)，用户安装步骤见[安装包说明](ADDON-INSTALL.md)。

## 1. 确定维护身份、组织包名与发布渠道

GitHub Release 和 npm 支持个人账号发布，FreeCAD 官方索引要求有活跃维护者。使用这些分发渠道不要求先成立公司；项目可以继续使用 Phoenix FreeCAD Tools 名称，由个人负责维护。

| 渠道 | 交付内容 | 用户用途 |
| --- | --- | --- |
| GitHub Release | 工作台安装 ZIP、SHA-256 校验文件、发行说明 | 下载后手工安装到 FreeCAD 的 Mod 目录 |
| npm | `@phoenix-wing/phoenix-freecad-mcp` Node 包 | 为聊天客户端提供 MCP 服务 |
| FreeCAD Addon Manager | 申请官方 Addon Index 收录 | 在 FreeCAD 内搜索和安装工作台 |

首轮按 **GitHub Release → npm** 的顺序发布；取得用户测试反馈并达到收录要求后，再申请 Addon Manager 收录。普通工作台工具不需要安装 Node MCP。

准备以下公开信息：

- 你明确选择的维护者姓名或昵称，以及公开联系渠道；不从本机账户、路径或 Git 配置推导身份。
- 项目公开仓库和 Issues 地址。可保留现有组织仓库并注明个人维护，也可选择个人仓库；若迁移地址，需同步元数据和文档链接。
- 复用已有的个人 npm 账号、已验证邮箱及双因素认证，并确认该账号拥有 [phoenix-wing 组织](https://www.npmjs.com/org/phoenix-wing)下目标包的发布权限。个人负责认证，组织 scope 决定 npm 包归属。

正式 npm 包名采用 **`@phoenix-wing/phoenix-freecad-mcp`**，CLI 命令保持 **`phoenix-freecad-mcp`**。`phoenix-freecad-mcp` 是名称主体；加上组织 scope 后才是完整 npm 安装名称。

源码原先使用无 scope 的 `phoenix-freecad-mcp`。发布准备必须将 `package.json` 的 `name` 和 `package-lock.json` 中的根包名称同步为上述组织包名，并核对安装/npx 示例和相关测试。建议设置 `publishConfig.access` 为 `public`、`publishConfig.registry` 为 `https://registry.npmjs.org`。下面的组织 tgz 文件名及发布命令均以完成该身份切换并重新打包为前提；旧无 scope 的 tgz 不能通过改发布命令变成组织包。

参考：[GitHub Release](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)、[npm 组织公共包发布](https://docs.npmjs.com/creating-and-publishing-an-organization-scoped-package/)。

## 2. 冻结候选，补齐元数据

1. 明确本次功能、支持的 FreeCAD 版本和已验平台。未验功能、平台及已知限制写入对应[发行说明](RELEASE-0.3.2.md)，不能从源码测试推断跨平台可用。
2. 核对 `package.json`、`package-lock.json` 的版本，以及 `src/server.ts`、`pft_bridge.py` 和 `package.xml` 的运行版本一致。打包器会检查这些位置。本批未发布候选继续使用 0.3.2，不为补文档单独升号。
3. 核对组织包名与锁文件根包身份，补充 `package.json` 的维护者及 `repository`、`homepage`、`bugs` 等公开元数据；按 FreeCAD manifest 规范补充 `package.xml` 的作者/维护者、兼容范围、日期及仓库地址。
4. 核对 `package.xml` 的仓库 URL 与分支。当前写的是 `develop`；正式分发应按本仓分支约定核实稳定的 `master`，并确保 `branch` 与 manifest 实际所在分支一致。
5. 核对 Apache-2.0、LICENSE、NOTICE、图标和样例归属，保留已有第三方署名。个人维护身份不能代替既有版权归属。
6. 按[隐私审查](PRIVACY-REVIEW.md)分别检查本次 diff、跟踪文件、拟公开的可达历史及实际制品，不包含私有数据、凭据、本机路径或未经授权的使用记录。

`develop` 到 `master` 按[发布计划](RELEASE-PLAN.md)走发布 PR。若历史还未通过审查，先准备干净候选，不通过普通合并带入未审历史。PR 合入后重新核对合并结果并记录提交 SHA，后续构建和标签均绑定该提交。

## 3. 在受审提交上验证

在受审提交的干净源码 checkout 中操作，先核对身份与工作区：

```sh
git status --short
git rev-parse HEAD
```

如有其他未归档修改，先分开处理，不将它们混入发布。不能将含未审修改的工作区直接当作正式发布提交。

从该源码 checkout 执行，每条命令成功后再继续：

```sh
npm ci
npm run typecheck
npm run build
npm test
npm run test:python
npm pack --dry-run --json
```

另行用公开样件或副本进行真实 FreeCAD 验收，覆盖普通按钮、桥接与聊天接入，以及本次修改涉及的能力。手工 ZIP 与受管理安装分别验证新装、升级、卸载和恢复；每个宣称支持的平台分别记录结果。不要使用用户正在编辑的模型作为测试靶。

## 4. 构建并检查实际安装包

以下以 **0.3.2** 为例，命令从仓库根目录执行；未来版本需同步替换命令中的版本。输出目录须为本批新建的空目录。若目录已有产物，保留旧包并换用新的批次目录，不删除或覆盖旧包。

```sh
npm run addon:pack -- --output-dir artifacts/release-0.3.2
npm pack --pack-destination artifacts/release-0.3.2
```

应生成：

- `phoenix-freecad-tools-addon-0.3.2.zip`
- `phoenix-freecad-tools-addon-0.3.2.zip.sha256`
- `phoenix-wing-phoenix-freecad-mcp-0.3.2.tgz`

组织 tgz 的文件名以 `npm pack` 实际输出为准；清单中的 manifest 必须为 `@phoenix-wing/phoenix-freecad-mcp`。`npm pack` 的 `prepack` 会构建当前源码。工作台打包器使用文件白名单，并拒绝覆盖同名不同内容的旧 ZIP。dry-run 清单不能代替实际制品审查。

列出两个实际包的成员，并解压到独立临时目录检查内容、版本、许可和公开边界：

```sh
python3 -m zipfile -l artifacts/release-0.3.2/phoenix-freecad-tools-addon-0.3.2.zip
tar -tzf artifacts/release-0.3.2/phoenix-wing-phoenix-freecad-mcp-0.3.2.tgz
```

Addon ZIP 应只有一个顶层 `PhoenixFreeCADTools/`，入口为其中的 `InitGui.py`，并包含所需 Python 模块、图标、元数据、法律文件和安装说明。Node tgz 应包含可运行的 `dist/cli.js`；它不包含 Node.js 或 FreeCAD，也不含源码重建所需的完整 lockfile/tsconfig。不要在 tgz 解包目录执行 `npm ci` 和源码构建步骤。

在独立环境安装真实 ZIP/tgz，核对安装和运行结果。记录源码提交 SHA、实际成员清单及制品摘要。需要另行提供源码 ZIP 时，从同一受审提交生成；源码 ZIP 与 Addon 安装包分别标注。

生成供 Release 下载者使用的统一校验文件。以下是 macOS/Linux 命令，仅在本批包核对完成后执行：

```sh
(
  cd artifacts/release-0.3.2 || exit 1
  shasum -a 256 phoenix-freecad-tools-addon-0.3.2.zip phoenix-wing-phoenix-freecad-mcp-0.3.2.tgz > SHA256SUMS
)
```

若增加其他附件，将它们的摘要一并纳入 `SHA256SUMS`。Windows 可用 PowerShell `Get-FileHash -Algorithm SHA256` 计算并核对摘要。

现有 [package.yml](../.github/workflows/package.yml) 可手动构建 ZIP、tgz 和校验文件，并上传 Actions artifact；它没有创建 GitHub Release 或执行 npm 发布。使用 Actions 时仍需核对运行对应的受审提交与实际附件。

## 5. 发布 GitHub Release

1. 确认本次受审提交、版本、附件、说明和发布权限，再为该提交创建或选择正式版本标签，例如 `v0.3.2`。不批量推送旧分支或标签，不覆盖已公开标签和产物。
2. 在 GitHub 仓库的 **Releases → Draft a new release** 创建草稿，核对标签目标确为本次受审提交。
3. 填写功能变化、安装方法、已验平台、已知限制，以及普通工作台与聊天 MCP 各自的安装要求。
4. 上传受审的 Addon ZIP、Node tgz、`SHA256SUMS`，按需要附源码 ZIP；核对下载文件名和版本。
5. 完成最终检查并发布。GitHub 自动生成的 **Source code ZIP** 是仓库源码归档，不是工作台安装 ZIP。
6. 从公开 Release 重新下载附件，核对字节摘要与安装结果，记录 Release URL、标签、提交和附件摘要。

可先标为预发布供小范围用户测试；GitHub Release 发布不表示已经进入 npm 或 FreeCAD Addon Manager。操作入口见 [GitHub 官方说明](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)。

## 6. 发布 npm MCP 包

账号、认证和“构建 → 检查实际包 → 发布 → 干净安装验证”的流程可复用。发布客户端按各仓库配置选择：pnpm workspace 项目需要按其规则使用 pnpm 处理 `workspace:`；本仓是使用 `package-lock.json` 的独立 npm 包，按本文使用 npm 构建、打包和发布，不照搬其他仓库的专用脚本，也不因此迁移包管理器。

确认当前个人账号拥有 `phoenix-wing` 组织下目标包的发布权限，以及该版本尚未被占用。以下从源码仓库根目录执行，明确使用 npm 公共 Registry，先核对现有登录：

```sh
npm whoami --registry https://registry.npmjs.org
npm view @phoenix-wing/phoenix-freecad-mcp versions --json --registry https://registry.npmjs.org
```

已有会话对应目标发布账号时直接复用。仅在未登录、会话过期或需要切换账号时执行 `npm login --registry https://registry.npmjs.org`，按 npm 提示完成认证；发布时按提示完成双因素认证。

首次发布时查询可能返回未找到包；需结合账号和名称可用性确认，不能把网络、认证或其他错误当作名称可用。不把 token、验证码或凭据写入仓库、文档或发行说明。

准备正式发布时，直接发布已检查的同一份 tgz：

```sh
npm publish ./artifacts/release-0.3.2/phoenix-wing-phoenix-freecad-mcp-0.3.2.tgz --access public --registry https://registry.npmjs.org
```

如果需要持续迭代预发布，先采用 `0.3.2-rc.1` 等独立候选版本，同步全部版本位置并重新构建验证，再发布对应 tgz，在命令中增加 `--tag next`。同步写明安装的精确版本及测试性质。

`--tag next` 只控制默认分发标签，不改变版本身份：已发布的同一 `name@version` 不能覆盖或重新发布。如果正式版本 `0.3.2` 已用 `next` 发布且制品字节保持不变，后续正式推广只调整 dist-tag；若制品仍需修改，应使用新的版本身份，不能重新发布不同内容的 `0.3.2`。

发布后查询真实 Registry：

```sh
npm view @phoenix-wing/phoenix-freecad-mcp@0.3.2 version dist.integrity dist.tarball --registry https://registry.npmjs.org
```

再在独立目录从 Registry 安装精确版本，验证 CLI 和 MCP 接入，并记录 npm 页面、版本和实际安装结果。查询元数据成功不代替安装验证；本地 tgz 成功也不等于 Registry 已发布。后续自动发布可采用 npm 官方的[可信发布](https://docs.npmjs.com/trusted-publishers/)，另行配置，不在当前打包工作流中假定已启用。

## 7. 申请 FreeCAD Addon Manager 收录

FreeCAD 1.0+ 当前使用 [FreeCAD/Addons](https://github.com/FreeCAD/Addons) 索引；旧 `FreeCAD/FreeCAD-addons` 供早期版本保留，不作为本项目默认申请入口。

按核对日的[官方质量要求](https://freecad.github.io/Addon-Academy/Topics/Addon-Index/Index/Qualities.html)，申请前应具备活跃维护者，仓库和代码至少存在 30 天，主要功能完整可用，有效且准确的 `package.xml`，以及一致的许可证声明。外部连接、服务和数据使用需要明确告知用户；索引不适合作为 alpha 测试渠道。官方同时建议先取得小范围真实用户测试反馈。

完成准备后：

1. 保证源码仓库公开可访问，补齐安装说明、问题反馈入口、兼容范围和用户测试结果；可添加 `freecad`、`addon` 仓库 topics。
2. 验证 Addon Manager 实际使用的仓库布局。当前源码将插件放在 `addon/PhoenixFreeCADTools/`，手工 ZIP 的层级正确不等于整个源码仓库可直接被管理器安装；按 manifest/索引规范核对，必要时准备从受审源码生成的分发分支或仓库，并记录来源和法律文件。
3. 在 [FreeCAD/Addons Issues](https://github.com/FreeCAD/Addons/issues) 选择 **Addon - Addition** 提交申请，提供仓库、版本、维护者、功能范围和测试情况。
4. 配合维护者审查；达到要求后，按审查要求创建新的发行或标签，再等待索引更新。
5. 收录后在真实 FreeCAD 的 Addon Manager 中核对搜索、新装和升级，并记录结果。

申请已提交、审查通过、索引可搜索、实际安装成功分别记录。后续持续维护并响应用户反馈。完整流程见 [FreeCAD 官方收录指南](https://freecad.github.io/Addon-Academy/Guides/Publishing/Indexed)。

## 8. 保存本次发布回执

在对应版本的发行记录中填写实际结果；未执行项保留待办，不预填成功：

- 维护者公开身份、版本、受审提交 SHA 和正式标签。
- 构建及真实 FreeCAD 验收范围，已验平台与保留限制。
- 实际附件名称、成员清单及 SHA-256。
- GitHub Release URL、重新下载校验结果。
- npm 组织包名 `@phoenix-wing/phoenix-freecad-mcp`、精确版本、Registry 查询及独立安装结果；未发布时写明未发布。
- Addon Index 申请地址、审查/收录状态及管理器实际安装结果；未申请时写明未申请。

本地构建、Actions 制品、GitHub Release、npm Registry 和 Addon Manager 是独立状态，分别以真实结果为准。
