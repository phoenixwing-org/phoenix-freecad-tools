# 发布点检表

每次发布从本页开始。按实际结果勾选，不复制上次的通过结论；空项、失败和未验平台须如实说明。版本、源码、制品、安装和公开发行是不同状态。

## 1. 范围与工作树

- [ ] 确定版本、发布范围、基点和目标分支，核实远端当前提交；分清已推送与本地提交。
- [ ] 工作树位于 `phoenix/worktrees/<仓库名>-working`，检查 `git status`、暂存区及并行任务；保留未完成改动。
- [ ] 压缩前在 Git 外保存可恢复的提交、补丁和未跟踪文件备份；不把运行目录、认证或聊天记录打入备份附件。
- [ ] 只合并指定未推送范围。最终节点的父提交等于约定基点，树内容等于已审候选；中文提交说明写清最终功能、修复、测试和限制。
- [ ] 归档后检查分支图、提交差异及工作区；不强推、不覆盖公开标签，不删除其他工作树。

## 2. 隐私、来源与许可

- [ ] 检查相对发布基点的完整差异、最终受跟踪源码，以及实际 ZIP/tgz 全部成员。
- [ ] 排除私有项目资料、使用讨论及摘要、真实业务样例、个人绝对路径、凭据、token、账户、数据库、浏览器或聊天状态、日志、备份和开发 profile。
- [ ] 中性 fixture 必须独立构造；去标识或改名不改变私有来源。图片检查内容和文本/EXIF 元数据。
- [ ] 检查 `.gitignore`、打包允许清单、Apache-2.0、NOTICE、图标/样例及第三方依赖许可；不将源目录检查冒充实际包检查。
- [ ] 结论注明覆盖的基点、最终树和制品摘要；只审本批不代表整个 Git 历史已审。原始材料和私有核验清单留在 Git 外。

## 3. 功能和回归

- [ ] 完成适用的类型检查、构建、Node 和 Python 测试；记录真实退出码、测试数、平台及源码指纹。
- [ ] GUI 修改用真实 FreeCAD 复测；自动化须有界，记录实际点击和结果，mock 通过不替代原生验证。
- [ ] 共享小工具条：两个面板使用同一个组件和已注册命令；外观确定/取消/Esc后再次点击、GC 后重复操作、齿轮和工作台切换正常。
- [ ] 聊天改动：官方登录、发送/换行、中断、错误显示/复制与实际 MCP 调用；不读取或复制其他客户端凭据。
- [ ] 多实例相关修改验证目标身份绑定、拒绝串台、断连/重启后的身份变化；只打开多个窗口不等于同时聊天已验。
- [ ] 核实相机、选择、预览恢复、撤销和不自动保存的行为；不以用户真实 CAD 为自动测试 fixture。
- [ ] 若用户要求先试用，交付带真实版本和用途标题的独立窗口，等待这批修复的试用确认；旧版本的确认不能代替新修改。

## 4. 版本与候选包

- [ ] `package.json`、lockfile 根、Addon `package.xml`、bridge、Node 服务、兼容门禁、工作进程和文档身份一致；以 `docs/VERSIONING.md` 为准。
- [ ] README、HELP 和 `docs/RELEASE-<版本>.md` 描述最终行为、安装方法、已验平台及限制；无私有经过或夸大完成度。
- [ ] 从同一干净冻结树构建 `dist`，生成真实 Addon ZIP 和 npm tgz；不从脏工作区打包，不把未安装开发迭代逐次升号。
- [ ] ZIP 的文件清单、内部 manifest、源码摘要正确；tgz 含可运行 `dist/cli.js`，名称为 `@phoenix-wing/phoenix-freecad-mcp`。
- [ ] 在隔离目录验证实际包的安装及 CLI/MCP 清单；不覆盖现有 FreeCAD 用户配置、文档或未知目录。
- [ ] 审查实际字节后填写 `.github/releases/<版本>.json` 的两个附件及 SHA-256；生成并验证 `SHA256SUMS`。
- [ ] 进入制品的任何源码或文档变化后重新构建和审核。不得把失败产物摘要写回清单来绕过检查。

本地验证入口（在受审源码根执行，使用本版实际版本和制品目录）：

```sh
npm run typecheck
npm run build
npm test
npm run test:python
python3 scripts/package-addon.py --output-dir <本批Addon构建目录>
mkdir <本批新建的制品目录>
cp <本批Addon构建目录>/phoenix-freecad-tools-addon-<版本>.zip <本批新建的制品目录>/
npm pack --ignore-scripts --pack-destination <本批新建的制品目录>
node .github/scripts/verify-release.mjs --tag v<版本> --metadata-only
node .github/scripts/verify-release.mjs --tag v<版本> --assets-dir <制品目录> --write-checksums
```

以上尖括号须替换为本批实际值。Addon构建目录与Release制品目录分开：打包器还生成单包`.zip.sha256`，该侧车文件保留在构建目录，不进入Release验证目录。Release目录只含两个包及验证器生成的`SHA256SUMS`；不要放宽文件集合检查。

生产依赖和工具链使用 lockfile。离线验证可复用已核对的锁定依赖；`npm ci` 只在已授权联网安装的隔离构建环境执行。`npm pack --ignore-scripts` 前必须已完成同树构建。

## 5. Git、GitHub Release、npm

- [ ] 准备完可审查的最终提交、两份制品、校验文件和发行说明；明确本次是本地归档、准备发布，还是已授权公开发行。
- [ ] 核实 GitHub/npm 的实际权限、远端基点和版本可用性；认证有效不等于拥有目标发布权限，不输出 token。
- [ ] 只推送约定分支并核实远端 SHA、CI；不要批量推送开发分支或旧标签。
- [ ] `v<版本>` 标签指向同一受审提交。推送 `v*` 会触发 `.github/workflows/release.yml` 的公开 Release 流程，是发布动作。
- [ ] 分支预检 `codex/npm-release` 只生成/检查候选；`develop` 推送、候选构建、草稿、正式 Release 各自单独记录，不从工作流文件推断已执行。
- [ ] GitHub Release 按既有流程上传受审 ZIP、tgz、`SHA256SUMS`，重新下载校验相同字节、版本和目标提交，再发布。已有标签/Release/附件不覆盖，失败草稿不自动接管。
- [ ] GitHub Release 验证完成后，发布**同一份已审 tgz**到 npm；工作流当前不自动发布 npm。
- [ ] npm 回读精确版本、`dist.integrity`、tarball 和 dist-tag，并从 Registry 在隔离目录安装精确版本验证 CLI/MCP；本地 pack 不代表已发布。
- [ ] 同一 npm `name@version` 不可用不同字节重发；需要更改时使用新版本。`next` 只影响 dist-tag，不改变版本身份。

```sh
npm whoami --registry https://registry.npmjs.org
npm view @phoenix-wing/phoenix-freecad-mcp versions --json --registry https://registry.npmjs.org
npm publish <已审tgz路径> --access public --registry https://registry.npmjs.org
npm view @phoenix-wing/phoenix-freecad-mcp@<版本> version dist.integrity dist.tarball dist-tags --json --registry https://registry.npmjs.org
```

## 6. 交接回执

- [ ] 分别列出：本地归档、远端 Git、标签/CI、GitHub Release、npm Registry、Registry 安装、FreeCAD 安装的实际结果和未执行项。
- [ ] 公共回执只保留版本、源码 SHA、公开 URL、制品摘要、平台和通用能力结果；本机路径、账户、实例/请求标识及私有业务数据留在 Git 外。
- [ ] Git 外归档保存同树源码、受审制品、校验及必要的脱敏元数据；清理只针对本任务已知临时物，不删除未完成改动、在用实例或其他 working。

详细操作与现有工作流见 [发布步骤](FREECAD-RELEASE-STEPS.md)；版本策略见 [VERSIONING](VERSIONING.md)。此清单是每版模板，本页的未勾选项不代表某个既有发行的状态。
