# Phoenix FreeCAD Tools 0.3.2

本页说明0.3.2的交付内容及已验范围。下载地址、附件校验值及npm可用性分别以[实际发行页](https://github.com/phoenixwing-org/phoenix-freecad-tools/releases)和[npm Registry](https://www.npmjs.com/package/@phoenix-wing/phoenix-freecad-mcp)为准；文档内容不代替发布与安装验证。

本次收敛0.3.1之后的改进，基础稳定范围是 **MCP接入和常用工作台按钮**。普通工具可独立使用；内置聊天和Desk为可选入口，其额外限制不阻塞基础使用。应用代码保持Apache-2.0。

## 常用工具与文档输出

- 工作台业务按钮配有图标，默认图标加文字，设置可切换仅图标；状态、启停和设置三个MCP控件保持仅图标。停止桥接需要确认，自动启动默认关闭。
- 当前文档面板默认跟随活动文档，也可固定查看指定文档；读取未保存的内存信息与缓存尺寸。默认显示五个BOM字段，设置可明确选择最多20个受支持属性。
- 轴测Fit合并为一个按钮。外观使用紧凑非模态窗口，随机配色及透明度实时预览，提供0%、25%、50%、75%快捷值并保留0–100%调节；外观窗口内也可轴测Fit。确定合为一次撤销，取消、Esc或关闭在事务仍归本窗口且没有外来修改时恢复；归属不明时停止，不擅自关闭其他事务。
- Markdown和PNG均冻结本次输出内容，再按各自流程选择路径并确认预览与输出；已保存CAD默认建议同目录、同名文件。覆盖、取消、目录身份变化及发布失败分别处理，不因导出保存CAD。缩略图捕获后恢复原相机；输出不代表内部缓存或批量图册。
- 命名修正、Link名称同步与零件属性使用对象范围及完整差异预览；写后核对实际值，支持事务撤销，不自动保存文件。

相关用法见[当前文档工具](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/v0.3.2/docs/CURRENT-DOCUMENT-TOOLS.md)、[原生修改确认](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/v0.3.2/docs/NATIVE-PART-APPROVAL.md)和[安装包说明](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/v0.3.2/docs/ADDON-INSTALL.md)。

## MCP与受控设计能力

默认完整模式提供25个工具，内置聊天模式保持19个，桥接协议仍为v1。连接、文档/对象、选择、几何缓存、设计诊断、图纸检查、相机和明确对象外观使用同一套接口。读取注明来源与未知状态，不把缓存值当作重算完成的证明。高级脚本仅在完整模式开放，具有FreeCAD Python控制台同等权限，不是沙箱。

业务修改先完整预览，再通过FreeCAD原生窗口确认。有限首个Pad Length已有独立正式MCP与原生按钮自动化验证；它仅支持合同限定的未附着草图和首个Pad，不能据此修改任意参数。Document.undo/redo可恢复相应参数与缓存几何，但Body/Pad的Touched完成状态及更多撤销路径仍待专项验收，见[有限Pad合同](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/v0.3.2/docs/PAD-LENGTH-EDIT.md)。

TechDraw新增受限A4三视图草稿，以及既有单页的Distance、DistanceX、DistanceY、Diameter、Radius关联尺寸。尺寸引用真实投影Edge，确认后回读原生量值；支持范围内的Body、父容器变换、不同Scale和撤销重做已有独立原生检查。每次结果仍明确要求人工复核投影；不自动判断标注完整、布局、公差、螺纹或制造可用性。**单页PDF/SVG和批量PDF/DXF仍是方案，未作为产品导出能力开放。** 见[绘图草稿](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/v0.3.2/docs/TECHDRAW-DRAFT.md)、[关联尺寸](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/v0.3.2/docs/TECHDRAW-DIMENSIONS.md)和[单页导出方案](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/v0.3.2/docs/TECHDRAW-EXPORT-PLAN.md)。

显式启用多实例发现时，完整模式增加列表与选择两项，共27工具；多实例须明确选目标，已绑定目标退出后不会自动改控另一窗口，不迁移预览、审批或脚本请求。默认仍是单桥接直连，聊天与专属worker不参与自动发现，见[多实例连接](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/v0.3.2/docs/INSTANCE-ROUTING.md)。

## 安装与接入

普通工作台下载Release附件`phoenix-freecad-tools-addon-0.3.2.zip`，按同批`SHA256SUMS`核对后，解压为`Mod/PhoenixFreeCADTools`；正常启动FreeCAD并选择 **Phoenix Tools**。GitHub自动生成的源码ZIP不等同于Addon安装ZIP。普通按钮无需Node或聊天账号；更新前保存工作、正常退出，并将旧完整目录移到Mod之外保留。安装与恢复详见[安装包说明](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/v0.3.2/docs/ADDON-INSTALL.md)。

MCP需Node.js 22+。组织npm包名为 **`@phoenix-wing/phoenix-freecad-mcp`**；CLI命令和MCP服务名仍为 **`phoenix-freecad-mcp`**。目标版本发布后可全局安装：

```bash
npm install --global @phoenix-wing/phoenix-freecad-mcp@0.3.2
```

也可在支持stdio的客户端使用固定版本`npx`，例如Codex：

```bash
codex mcp add phoenix-freecad -- npx --yes --package @phoenix-wing/phoenix-freecad-mcp@0.3.2 phoenix-freecad-mcp
```

先启动FreeCAD工作台中的MCP桥接，客户端连接后用`get_connection_status`核对运行版本、协议和实际支持方法。已有源码/本机绝对路径接入方式继续支持；完整步骤见[接入说明](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/v0.3.2/docs/CODEX.md)。Node预构建包为`phoenix-wing-phoenix-freecad-mcp-0.3.2.tgz`，不包含Node或FreeCAD，亦不作为完整源码重建输入。

## 已验证范围与保留限制

验证以macOS、Node.js 22系列及独立人工构造样件为主；自动化、原生GUI/正式MCP调用和实际发布分别记录，详见[验证与验收](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/v0.3.2/docs/VERIFICATION.md)。FreeCAD 1.1.0相关原生检查覆盖文档读取、常用按钮、Body/Pad/Pocket及受限Link外观、修改确认链、基础图页和关联尺寸；FreeCAD 1.1.4 / Qt 6.8.3另有文件发布与Markdown六场景验证，不能据此把所有功能改记为该版本已验。本轮另用实际Addon ZIP和组织npm包做FreeCAD 1.1.4的干净安装检查，覆盖25工具握手、文档读取、轴测Fit、当前文档面板、外观取消及原生Undo七项；该结果不扩大为全部设计功能已在1.1.4验证。图像目视核对和原生按钮自动化不代替用户人工批准或生产图审签。

- Windows、Linux及其它FreeCAD版本仍需分别完成原生安装与功能验收。需要POSIX权限/锁且尚无对应适配器的聊天和发现路径会明确拒绝；不影响独立普通工具的定位。
- 内置聊天有左右消息、时间/等待状态、中断与脱敏错误提示、模型/字号设置和专属进程登记。当前限定Codex CLI 0.160.0；官方账号登录、模型服务请求及真实跨进程持久恢复尚未完成本轮端到端验收。只恢复明确登记的插件自有会话候选，不复用桌面已有聊天；详见[聊天说明](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/v0.3.2/docs/CHAT-USAGE.md)。
- Desk配置、索引/缓存读取及专属缩略图worker属于可选实验范围。现有worker仅支持受限自包含输入，不等同于完整装配依赖、BOM数量、批量制造图或正式Desk部署/升级能力。更多集成见[Desk合同](https://github.com/phoenixwing-org/phoenix-freecad-tools/blob/v0.3.2/docs/DESK-MCP-CONTRACT.md)。
- FreeCAD Addon Manager索引收录尚未完成；此版手工安装包不能宣称已在插件管理器上架。

公开源码与发行材料只包含通用能力、独立中性示例及适用许可；真实工程、账户、聊天记录和运行回执不随包分发。
