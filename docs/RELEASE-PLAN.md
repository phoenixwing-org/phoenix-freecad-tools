# 开发与发布计划

当前源码版本为0.3.2，npm源码已采用`@phoenix-wing/phoenix-freecad-mcp`；实际发行以GitHub Release与npm Registry各自的回执为准。能力与待验范围见[TODO](TODO.md)；验证方法见[验证与验收](VERIFICATION.md)。源码实现、运行中模块、实际归档和正式发行分别核对，不继承旧运行回执作为新候选证据。

个人维护者的具体操作见 [FreeCAD 发布步骤](FREECAD-RELEASE-STEPS.md)，覆盖账号与元数据、实际打包、GitHub Release、npm 和 Addon Manager 收录。

本轮基础稳定交付以 MCP 接入和常用工作台按钮为范围。复杂 Desk Tools 接入按实验能力单独标注，不作为这批基础发布的阻塞项。

## 分支约定

| 分支 | 用途 | 进入条件 |
| --- | --- | --- |
| master | 正式主分支 | 从develop提交发布PR，通过发布门槛后合入 |
| develop | 稳定开发 | 本批范围清楚，保持可构建、可测试、可演示，相关审阅通过 |
| codex/下的本地临时分支 | 功能、修复或受审候选 | 小步开发；通过审查后按计划进入稳定开发 |

临时分支默认本地保留，不暂存或提交其它并行工作。develop到master发布走PR；紧急修复也回到develop。若既有历史未通过隐私检查，先准备独立干净候选，不能用普通合并把未审祖先带回发布历史；任何远端重写单独评估，不自动force push。

## 稳定开发门槛

1. 差异与目标一致，MCP名称/参数/预算/失败和重试边界清楚，GUI命令和安装身份一致。
2. 构建与适用测试通过；涉及模型或界面的变更还需独立原生验收。mock不替代GUI，接口成功不代替几何、布局或恢复正确。
3. 改名/属性先完整预览和确认；核对快照失效、事务所有权、取消、失败恢复和撤销重做，不保存用户CAD。
4. 当前源码、可达历史、实际包及法律文件分别通过审阅，不含私有项目、讨论、实际使用过程或其摘要。
5. 保留并行修改及仓库外私有恢复副本。用户现有会话、文档、服务和配置不能作为测试靶。

基础命令：

```sh
npm run typecheck
npm run build
npm test
npm run test:python
npm run addon:pack
npm pack --dry-run --json
```

干净依赖安装需另按锁文件验收；dry-run清单不能代替实际npm产物检查。已加载的Python回调和Node schema不会因磁盘更新自动替换，正常升级按安装说明进行，不承诺自动热更新。

## 发布PR与实际产物

- 功能冻结，Node、锁文件、Python及package.xml版本一致。原生待验项目明确保留，不能以归档结束代替功能完成。
- 从受审提交构建源码ZIP、Addon ZIP、Node tgz及必要的干净Git bundle；绑定来源、文件清单和SHA-256，逐成员核对内容与元数据。
- 手工ZIP和受管理安装分别验收新装、升级、卸载、失败与备份恢复；不覆盖未知目录或用户修改，普通按钮不依赖Node。
- 逐平台验证GUI、自动启动和延迟回调；没有证据的平台写明待验。Actions、Release、npm与Addon Manager分别验证，不从工作流文件存在推断执行成功。
- 核对Apache-2.0、组织Logo、公开样例及依赖归属；公开维护者联系方式明确提供，不从本机账户推导。
- PR合入后复核实际合并内容和版本，再按授权创建/推送正式标签及发布附件。已公开标签和产物不覆盖；不批量推送未审旧标签。

Addon ZIP只含固定Python/图标/元数据/法律文件与自足说明；Node tgz不含Node或FreeCAD。源码ZIP不含.git；候选bundle只能带受审历史。手工ZIP没有受管理安装所有权标记，不伪造或复制旧标记。完整安装边界见[安装包说明](ADDON-INSTALL.md)。

## GitHub自动发行与npm顺序

首轮顺序为 **GitHub Release → npm**，两者分别验证并记录实际回执。源码采用组织 npm 包名，CLI 命令和 MCP 服务身份继续使用 `phoenix-freecad-mcp`；不能从源码身份准备完成推断 Registry 已发布。

- [release.yml](../.github/workflows/release.yml) 在 `codex/npm-release` 分支运行候选构建、测试及清单校验，上传 Actions 候选附件，不创建或发布 Release。现有 [package.yml](../.github/workflows/package.yml) 保持手动构建用途。
- 为受审提交创建并推送 `v*` 标签是明确的发布动作。标签必须等于源码版本的 `v` 前缀形式，并解析到同一受审提交；不能把标签触发当作无发布副作用的试运行。
- [`.github/releases/0.3.2.json`](../.github/releases/0.3.2.json) 随源码审查，保存两个真实候选包的预期 SHA-256。进入制品的源码或文档变化后，先重建并审定新摘要，再冻结来源提交；不能把失败产物摘要直接当作新的批准值。
- 标签工作流构建和校验两个包，发布作业再次验证后创建草稿。仅上传 Addon ZIP、组织 Node tgz 及 `SHA256SUMS`；核对新草稿和附件身份，重新下载并验证字节，最后复核标签提交再正式发布。
- 既有 Release、标签和附件不覆盖；失败草稿保留供审查，不自动接管续写。自动流程启用后，不并行手动创建同一发行。手动发布仍可作为本次明确选定的替代路径，同样通过受审提交、资产清单和下载校验门槛。

Actions候选、草稿、正式Release、npm Registry发布及Registry干净安装是独立状态；只有相应真实回执支持相应结论。该工作流不自动发布npm；具体手动及自动操作见[FreeCAD发布步骤](FREECAD-RELEASE-STEPS.md)。

## Desk与后续分发

Desk为可选独立后台，正式CLI的status/ensure和身份合同决定ready；它不代表用户工作空间、页面视觉或复杂Native已验收。关闭面板/浏览器不停止后台，不能按端口或PID接管未知实例。

下载部署先具备正式版本、平台资产、可信摘要、完整依赖和许可，以及数据迁移/升级/回滚合同。下载、安装/编译、启动分别授权；源码候选和旧体积测量不替代发行包。轻量Addon保持独立，简单规则复用，复杂任务通过正式公共接口交接，详见[Desk集成](DESK-INTEGRATION.md)。
