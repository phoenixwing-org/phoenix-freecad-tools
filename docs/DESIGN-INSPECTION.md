# 设计前读取状态

版本说明：当前统一候选为0.3.2；本页0.4.0–0.10.0均为未发布内部迭代标签，保留当时代码、原生版本及归档事实，不能视为多个正式发行或本轮最终验收。详见[版本约定](VERSIONING.md)。

0.9.0 新增只读 MCP 工具 `inspect_design`，完整外部模式和内嵌聊天都可使用。它补齐 AI 做设计前的状态读取，不增加工作台快捷按钮。当前0.3.2工具条统一轴测Fit与外观入口，文档Dock不重复快捷区。

可以粘贴给 AI：

> 请通过 phoenix-freecad MCP 检查连接，确认目标文档、活动 Body、Tip、修改/编辑状态和读取来源。对我明确指定的草图或特征调用 inspect_design，报告缓存的自由度、约束（区分驱动与激活）、参数和错误标记；未知值明确说未知。不要自动求解、重算或保存；修改另行完整预览并确认。

参数使用文档和对象内部 Name，省略 document 时读取活动文档：

```json
{"document":"ExampleDocument","object":"ExampleSketch","constraintOffset":0,"constraintLimit":50}
```

- Body：Tip、成员及读取状态；无 Tip 可以是已知空值。
- Sketch：缓存几何数、自由度、FullyConstrained，以及零基约束分页。默认50条，最多100条；`getDriving` 与 `getActive` 分别表示驱动和激活，互不替代。
- 特征：固定12个常用参数候选，按属性类型返回值、单位或同文档引用。PropertiesList读取失败不冒充没有参数。
- 表达式：最多50条缓存文本，不执行。保守隐藏私有路径、源码和无法识别的内容。当前仅接受简单算术子集；函数、字符串、变量分母等仍返回 unsupported，不代表支持整个 FreeCAD 表达式语言。

约束 datum 通过原生 `getDatum` 取带单位 Quantity。内部单位维度顺序是 mm、kg、s、A、K、mol、cd、deg；角度值使用 deg。不要把约束原始 Value 的弧度值当度数。

每页返回实例token和部分事件修订；后续请求可同时提供 `expectedDocumentInstanceToken` 与 `expectedRevision`，变化时拒绝读取。它不证明缓存最新、几何有效或完整写入保护。跨页时也不自动求解或重算。

仅报告当前原生缓存，不切换活动文档、不改选择、不读取文件或 Link 来源；App::Link 明确返回 unsupported。未知值是 null，0/false/空列表有各自读取状态。结果整笔最多128 KiB，超限明确拒绝；旧插件没有声明新方法和读取来源能力时拒绝降级。

聊天内Label/BOM/Link的完整预览与原生确认已有源码，原生端到端尚未验收，见[原生确认链](NATIVE-PART-APPROVAL.md)。有限首个Pad Length另有[写合同](PAD-LENGTH-EDIT.md)及源码实现，原生验收、更广参数修改和草图编辑仍在后续计划；不把只读诊断称为完成通用建模。实际验收范围见[验证记录](VERIFICATION.md)。
