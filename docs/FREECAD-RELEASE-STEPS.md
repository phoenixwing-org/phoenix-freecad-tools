# FreeCAD Tools 发布步骤（个人维护、组织发布）

本文说明如何由个人维护者发布 Phoenix FreeCAD Tools，npm 发布到组织 `@phoenix-wing`。每次开始先执行 [发布点检表](RELEASE-CHECKLIST.md)。本文命令以历史 **0.3.2** 发行作示例；当前候选版本以 `package.json`、README 和本版发行说明为准。npm 包名为 **`@phoenix-wing/phoenix-freecad-mcp`**；实际发行状态以 GitHub Release 与 npm Registry 各自的公开回执为准。本文是操作步骤，不是发布回执。

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

源码已将 `package.json` 的 `name` 和 `package-lock.json` 中的根包名称同步为上述组织包名，并设置 `publishConfig.access=public`、`publishConfig.registry=https://registry.npmjs.org`；CLI 与 MCP 服务名保持不变。安装/npx 示例和实际打包身份须随本次发布检查。此处记录源码准备完成；实际发行以 Release / Registry 回执为准。重新打包后的文件名为 `phoenix-wing-phoenix-freecad-mcp-0.3.2.tgz`；旧无 scope 的 tgz 不能通过改文件名或发布命令变成组织包。

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
python3 scripts/package-addon.py --output-dir artifacts/addon-build-0.3.2
mkdir artifacts/release-0.3.2
cp artifacts/addon-build-0.3.2/phoenix-freecad-tools-addon-0.3.2.zip artifacts/release-0.3.2/
npm pack --pack-destination artifacts/release-0.3.2
```

Release目录应包含两个实际包：

- `phoenix-freecad-tools-addon-0.3.2.zip`
- `phoenix-wing-phoenix-freecad-mcp-0.3.2.tgz`

Addon构建目录中另有`phoenix-freecad-tools-addon-0.3.2.zip.sha256`；该单包侧车文件不复制到Release目录。Release验证器只接受两个包及统一的`SHA256SUMS`。组织 tgz 的文件名以 `npm pack` 实际输出为准；清单中的 manifest 必须为 `@phoenix-wing/phoenix-freecad-mcp`。`npm pack` 的 `prepack` 会构建当前源码。工作台打包器使用文件白名单，并拒绝覆盖同名不同内容的旧 ZIP。dry-run 清单不能代替实际制品审查。

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

当前自动发行严格使用上述两个包和由其生成的 `SHA256SUMS`，不能直接加入其他附件。额外源码归档若需纳入同一发行，须先另行审定清单与验证规则。Windows 可用 PowerShell `Get-FileHash -Algorithm SHA256` 计算并核对摘要。

现有 [package.yml](../.github/workflows/package.yml) 可手动构建 ZIP、tgz 和校验文件，并上传 Actions artifact；它没有创建 GitHub Release 或执行 npm 发布。使用 Actions 时仍需核对运行对应的受审提交与实际附件。

## 5. 发布 GitHub Release

首选 [release.yml](../.github/workflows/release.yml) 的受控自动流程；[package.yml](../.github/workflows/package.yml) 仍是原有的手动构建流程，不创建发行。GitHub Release 完成并读回验证后，再按第 6 节发布 npm；自动 Release 工作流本身不执行 npm 发布。

### 发布清单与提交绑定

[`.github/releases/0.3.2.json`](../.github/releases/0.3.2.json) 是本版受审资产清单，严格记录版本、组织包名，以及两个实际附件的文件名和真实预期 SHA-256：

- `phoenix-freecad-tools-addon-0.3.2.zip`
- `phoenix-wing-phoenix-freecad-mcp-0.3.2.tgz`

清单必须来自已构建、审阅和验收的实际字节，不能使用示例摘要或把当前失败产物的摘要直接写回以绕过检查。清单、发行说明和源码一起接受审查；最终标签必须指向该受审提交。若改变会进入制品的源码或文档，先重新构建、审查和更新预期摘要，再冻结新的来源提交。

本地可先检查身份与实际附件；这些命令只验证或生成校验文件，不创建 Release：

```sh
node .github/scripts/verify-release.mjs --tag v0.3.2 --metadata-only
node .github/scripts/verify-release.mjs --tag v0.3.2 --assets-dir artifacts/release-0.3.2 --write-checksums
```

第二条要求目录严格只有两个候选包，或再含完全一致的 `SHA256SUMS`；已有不同的校验文件不会被覆盖。标签、版本、组织包名、附件名称或字节摘要不符均停止。

### 分支预检与标签发布

1. 推送受审候选到 `codex/npm-release` 时，`release.yml` 只构建、测试并上传候选 Actions artifact，随后校验清单；**分支预检不会创建或发布 Release，也不会发布 npm**。候选附件保留供检查，不等于正式发行。
2. 只有在确认受审提交、资产摘要、权限及发行说明后，才明确授权为该提交创建并推送 `v0.3.2` 标签。推送 `v*` 标签是发布动作，会触发自动发布流程，不是普通构建按钮。标签必须与 `package.json` 版本完全匹配，且解析出的提交须与本次 checkout / 事件提交一致。
3. 标签运行先在该提交构建并验证实际 ZIP、tgz 与已提交的预期 SHA，再将候选附件交给发布作业。发布作业重新验证下载到的构建附件及标签提交，才创建草稿 Release。
4. 草稿只上传两个包和 `SHA256SUMS`。流程核对新草稿身份、目标提交、三份附件的完整上传状态，然后重新下载附件并与受审清单逐项核验，再复核同一草稿、附件身份和远端标签提交，全部通过才解除 draft 并发布。
5. 发现既有同名 Release、已发布附件或标签指向不一致时停止，不覆盖标签、不删除或替换旧发行。失败可能留下草稿供审查；重跑不会自动接管或续写该草稿。先调查实际状态，再决定后续处置。
6. 自动流程启用后，不同时手动创建同一标签的发行，避免两个发布者竞争。工作流文件存在、分支预检通过和正式发行成功分别记录；正式回执仍须核对公开 Release URL、标签、提交以及下载附件的摘要。

若 CI 构建字节与受审清单不一致，保留候选查明平台或输入差异；不能忽略校验、临时换包或覆盖已公开资产。GitHub 自动提供的 **Source code ZIP** 是仓库源码归档，不是工作台安装 ZIP。

### 可选的手动发布路径

仅在本次明确选择手动路径、自动发行未启用或已确认不会并行运行时使用：

1. 确认受审提交与正式标签指向一致，核实不存在需要保留的同名 Release；不批量推送旧标签，不覆盖已公开标签或资产。
2. 在 **Releases → Draft a new release** 创建草稿，填写本次功能、安装方式、已验平台和限制，上传受审的两个包及其 `SHA256SUMS`。
3. 按同一资产清单重新下载并校验附件，确认草稿、标签提交与文件身份均未变化后再发布。不绕过清单检查，也不把自动运行留下的未知草稿当作可直接复用的空位。
4. 从公开 Release 再次下载核对，记录真实 Release URL、标签、来源提交和摘要。可按本次发布决定标为预发布，但 GitHub Release 不代表已进入 npm 或 Addon Manager。

操作入口见 [GitHub 官方说明](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)。

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

如果需要持续迭代预发布，可另行设计 `0.3.2-rc.1` 等独立候选版本及`next`标签流程。当前`verify-release.mjs`与自动Release工作流只接受稳定三段版本，不能直接套用该流程发布rc；预发布须先单独审定版本校验、制品和发布规则。

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
