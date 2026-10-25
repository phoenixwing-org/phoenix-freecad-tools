# 原生重算与写入前置合同

`recompute_document`是明确的写操作：调用原生特征求解，可能更新几何或执行回调；不自动保存，不承诺异常回滚、执行隔离或跨文档无副作用。聊天默认工具清单不包含它。有限参数编辑仍须另建完整预览、人工确认、受控依赖和事务链；本合同不把通用重算称为参数化设计工具。

## 能力门禁与一次调用

新Node客户端在调用前核对插件`get_status.recompute_result`的version 1合同、对象预算和原生结果种类。旧插件未声明兼容合同，或声明畸形时，在重算前拒绝，不降级到旧返回格式。工具名称及完整25/聊天19的基础数量不变。

FreeCAD GUI侧要求明确活动文档、可读取且无应用事务、文档`HasPendingTransaction`严格为False、`getInEdit()`严格为None。文档重算冻结状态必须可读取且为False；不解除用户冻结，不启动或取消外来事务。对象集合须完整可读取、身份一致且不超过自有10000对象预算。

通过前置检查后只调用一次原生`document.recompute()`。调用异常、超时、断连或结果畸形时，可能已有修改；先检查文档状态，不能自动再次重算。HTTP返回失败不证明原生动作未执行。

## 返回观察值，不猜测完成或几何

FreeCAD 1.1把原生处理计数包装为Python int。0不能直接解释为失败，正数也不证明成功；失败、中止和再次处理都可能影响计数。[Python包装](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/DocumentPyImp.cpp#L675-L725)、[原生执行](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/Document.cpp#L2574-L2772)

新结果为严格对象：

| 字段 | 含义 |
| --- | --- |
| document | 与请求相同的内部文档Name |
| saved:false | 本工具没有调用保存 |
| recompute_invoked:true | 原生调用已返回；不等于全部工作完成 |
| native_object_count | 安全非负整数形式的原生计数；不要求小于文档对象数 |
| checked_object_count | 后验完整核对的本目标文档对象数 |
| object_status:no_invalid_or_pending | 这些对象的原生State/isValid未报告无效或待处理标志 |
| native_completion:not_verified | 不从计数或无异常推断未取消/全部完成 |
| geometry_validity:not_verified | 未调用Shape有效性、实体或尺寸检查 |
| geometry_freshness:not_verified | 不证明全部依赖的缓存都已更新 |

结果不再含`native_success`或`recomputed`成功暗示。Node结果数据体预算为4KiB，并校验字段、计数、标志和文档绑定；畸形响应不会触发第二次调用。

原生State的九种标志为Touched、Invalid、Recompute、Recompute2、Restore、Expanded、Partial、Importing、Up-to-date。仅接受Up-to-date，或它与Expanded的组合；Partial等可与Up-to-date同时出现，不能只查包含后者。任何状态或对象枚举读取失败均为未知，不伪装成空列表；`isValid()`只检查对象错误位，不等于Shape有效。[State](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/DocumentObjectPyImp.cpp#L212-L249)、[对象有效状态](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/DocumentObject.h#L195-L232)

观察结束再次核对目标文档、对象身份/集合、冻结、编辑和事务状态。变化返回`RECOMPUTE_UNVERIFIED`；已知无效对象为`RECOMPUTE_FAILED`，待处理/部分状态为`RECOMPUTE_INCOMPLETE`。这些后验不构成任意回调或其它文档的副作用隔离。

## 普通业务的原生可写性

改名、Link名称同步和BOM属性预览/应用复用相同可写性检查。FreeCAD 1.1的`getEditorMode()`返回字符串列表，`getPropertyStatus()`返回字符串或整数位序列表；整数24表示静态只读。它不是整数掩码，也不能仅比较`==1`。

工具必须读取并验证两种原生表示，拒绝ReadOnly、Immutable、LockDynamic或静态只读24。隐藏本身不等于只读；合法动态属性和其它已知原生位保留。缺API、异常、未知或畸形标志均停止。原生Python setter主要拒绝Immutable，不能靠setter成功来证明可写。[状态/编辑模式](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyContainerPyImp.cpp#L207-L377)、[setter](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyContainerPyImp.cpp#L680-L698)

预览后再次核对可写性及明确空闲/撤销状态，才进入自有事务。无法确认时不写、不关闭外来事务，也不更改重复Label等偏好。[事务API](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/ApplicationPy.cpp#L1126-L1145)、[文档事务](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/DocumentPyImp.cpp#L591-L594)、[编辑态](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Gui/DocumentPyImp.cpp#L160-L172)

## 验收边界

独立Python fixture和HTTP/stdio测试分别核对原生返回形状、状态未知、旧能力拒绝及单次调用；它们不代替FreeCAD原生CAD验收。后续须在明确授权的独立配置和人工文档中核对实际原生字段、冻结/事务、错误与中止、几何及撤销链，不使用用户文档作为测试对象。
