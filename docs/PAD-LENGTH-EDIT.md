# 有限 Pad Length 编辑

当前为0.3.2源码候选；已有合成测试与独立人工样件的MCP/原生确认按钮自动化，完整验收及部署仍待。不增加MCP工具名，完整/聊天基础清单仍为25/19。目标是一个可核对的有限设计操作，不能据此宣称支持任意Pad或任意参数写入。

## 输入与确认

通过现有`prepare_part_changes`提交，例如以下独立虚构名称：

```json
{"document":"ExampleDocument","objects":["Pad"],"operation":"set_pad_length","body":"Body","lengthMm":15}
```

`objects`必须只有一个Pad，`body`为同文档内部Name，`lengthMm`为0.01–10000的有限数字，单位mm；不接受`values`。Node在派发前核对精确`pad_length_write` v1、原生确认能力和方法。旧插件或未知声明拒绝，不退回脚本、任意setter或整文档重算。

预览模型只读，完整列出Body/Pad、Length旧值/新值、mm、重算根、来源、范围及副作用说明。新几何尚未计算，不伪造几何预览。可应用且有变更时，以原token请求`request_part_change_approval`；只有用户在FreeCAD原生窗口点击确认才写入。完整模式的`apply_part_changes`也始终拒绝Pad token，包括成功后的重复调用。状态查询仍只读有界内存，不接触CAD。

## 首版范围

- 仅一个打开的文档，明确为活动且空闲文档，撤销启用、没有外来事务、没有冻结重算。
- 恰好11个受支持原生对象：一个Body、一个Sketch、一个Pad、一个Origin及其三线/三平面/一点。Body的成员仅Sketch/Pad，Tip为此Pad；依赖图全部在该有限集合内。
- 首个且最终Pad；无BaseFeature、表达式、外部几何、额外消费者或Python代理。未附着草图，Body/Sketch/Pad为identity Placement，Pad单侧Length、+Z、零锥度/偏移，无自定义方向或UpTo目标。
- 草图最多100项几何、200项受支持约束；几何仅原生线段、圆和圆弧。逐项typed快照保留方向、参数区间、construction及约束字段，不使用整对象序列化。附着到XY平面或面的常见草图当前也不支持，不能自动解除附着。
- 预览时相关对象必须处于可确认的完成状态，Pad/Body缓存均为有效单实体，有限正体积/尺寸且匹配该范围。无法读取或无法界定即拒绝，不猜测默认值。

依赖快照保留原生边的顺序和重复数量；`Refine`与常驻附件引擎、反向、路径参数及偏移均逐项typed绑定。动态Base附件配置明确拒绝，不读取或创建它们，也不读取已迁移的旧`Support`字段。参数定义见[Refine](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/PartDesign/App/FeatureRefine.cpp#L39-L52)与[Attachment](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/Part/App/AttachExtension.cpp#L87-L143)。

FreeCAD 1.1 新建未附着 Sketch 的原生默认值可能是 `AttacherType=Attacher::AttachEnginePlane`、`AttacherEngine=Engine 3D`：[Part2DObject 构造](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/Part/App/Part2DObject.cpp#L58-L64)安装 Plane 引擎，而 [setAttacher](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/Part/App/AttachExtension.cpp#L258-L282)不改枚举；枚举默认 3D，其同步另见[文档恢复阶段](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/Part/App/AttachExtension.cpp#L658-L678)。仅在既有未附着、空 support、无动态 Base 条件下额外接受这一精确组合；标准对应组合继续支持，其他错配或未知值拒绝。两字段保留原值并完整绑定预览，不调用 setter 修正；预览后任一值变化仍拒绝。此兼容处理已有合成回归覆盖，尚不代表完整 Pad 原生写入验收通过。

`ExternalTypes` 是外部引用的类型元数据，`0` 表示 projection，不是引用数量或“无引用”标志。FreeCAD 1.1 的[原生初始化](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/Sketcher/App/SketchObject.cpp#L137-L146)与[整数列表的 scalar setter](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/Property.h#L959-L997)可留下默认单零列表；[外部几何处理](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/Sketcher/App/SketchObject.cpp#L7275-L7279)另按实际引用对象读取类型。仅在 `ExternalGeometry` 与 `Exports` 均为空时接受空类型列表或单个精确整数 `[0]`；布尔、浮点、其他值和多元素形态仍拒绝。类型列表原值独立绑定快照，`[]` 与 `[0]` 之间变化也使预览失效，不清空或归一化原生字段。

这些条件限制目标和依赖，不是未知原生扩展、观察者或同步回调的安全沙箱。同ID事务也不证明第三方没有修改；完整快照、归属检查和写后反验仍不可省略。

## 应用与结果

人工确认后重新核对同一文档/对象实例、完整typed配置、依赖图及原Shape身份。在自有事务内执行一次Length setter和一次`document.recompute([body], False, True)`；逐阶段检查事务归属。重算整数计数仅作观察，0也不能直接当失败或成功。

成功还要求参数保持请求值、相关对象完成且有效、Pad/Body为新的有效单实体，体积符合长度比例、+Z包围盒符合预期，并在提交前后复核。`applied.result.design_result`绑定Body/Pad/长度，分别报告重算计数、检查对象数、Pad/Body体积和三维尺寸。Node检查精确DTO结构及原token，拒绝额外字段和非有限数；Controller及原生确认窗口另将Body/Pad/长度与原请求绑定复核。结构校验本身不证明原生几何正确。

失败经自有事务恢复并核对原快照；恢复或提交不确定时报告固定错误，不重复关闭事务、不触碰外来事务、不自动重试。状态未知先查原token。所有阶段不保存CAD；撤销/重做与实际几何行为仍须独立原生验收。

## 依据与待验

按FreeCAD 1.1.0官方实现核对：[Pad参数](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/PartDesign/App/FeaturePad.cpp)、[Extrude耦合](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/PartDesign/App/FeatureExtrude.cpp)、[recompute入口](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/DocumentPyImp.cpp#L675-L723)、[文档重算流程](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/Document.cpp#L2574-L2771)、[Body Shape来自Tip](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/PartDesign/App/Body.cpp#L371-L417)。受限根仍会执行原生钩子和耦合，不承诺观察者隔离。

合成测试分别覆盖typed快照、stale、默认取消、确认前零写入、直接apply拒绝、一次应用、Shape未更新、错误尺寸/体积、事务归属、回滚和提交不确认。它们不证明真实FreeCAD可用。原生验收应使用独立配置和人工模型，分别核对支持范围、人工确认、一次重算、几何、undo/redo、故障恢复和未保存状态；不得用已有用户工程代替。

## 独立原生样件的验证范围

人工构造的30×20 mm矩形首个Pad已走通正式MCP预览、直接apply拒绝、原生按钮测试确认、一次局部重算及Body/Pad几何反验：Length从12改为18 mm，单实体体积从7200变为10800 mm³，不保存CAD。该测试点击不代表使用者人工批准。

原生`Document.undo/redo`恢复了对应参数及缓存实体，但Body/Pad仍报告`Touched`；另行显式重算后再检查完成状态。不能把参数和缓存恢复说成撤销后的完整完成状态已通过。GUI撤销命令、取消/失效拒绝的原生链、更多草图和完整聊天操作仍待专项验收。
