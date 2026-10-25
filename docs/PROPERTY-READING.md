# 当前文档属性读取合同

属性设置只作用于当前工具面板，最多20个明确内部字段名。空清单不读取属性；缺省仍只读五个正式字符串BOM字段，不能因为显式模式扩展而把默认BOM改成枚举或数量聚合。不创建字段、不调用setter、不重算、不保存。

## 类型和值

| 原生类型 | 显式读取值 |
| --- | --- |
| PropertyString | 有界字符串；空字符串为empty |
| PropertyInteger / Float / Bool | 类型严格匹配的有限数值或布尔；0与false是有效值 |
| PropertyEnumeration | 当前选中字符串；不调用getEnumerationsOfProperty、不取索引或全部选项 |
| PropertyQuantity | 原生Value与有效八维Signature；组合维度按内部基底表达 |
| PropertyLength / Distance / Angle | 另核对长度或角度的预期维度 |
| PropertyArea / Volume / Mass / Density | 另核对面积、体积、质量或密度的预期维度 |
| PropertySpeed / Acceleration / Force / Pressure / Frequency / Power / Temperature | 另核对对应物理预期维度 |
| 其它类型 | unsupported；不读取其值或用str/repr展开对象 |

以上均为`App::`类型。枚举的普通Python getter与setter、`.Enum/.All`路径接口不同；只处理选中值。[FreeCAD 1.1实现](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyStandard.cpp#L446-L453)

## 内部单位，不猜显示换算

`pft_units.py`是无FreeCAD/Qt/文件访问的纯格式化模块，当前文档工具与MCP原生序列化器共享。它仅接受实际list/tuple中的八个非bool整数，指数范围−128…127；基底顺序为`mm,kg,s,A,K,mol,cd,deg`。0次幂省略，1次幂不写指数，组合以`*`连接，全0为`1`。例如密度为`mm^-3*kg`，力为`mm*kg*s^-2`，压力为`mm^-1*kg*s^-2`，功率为`mm^2*kg*s^-3`。

当前文档属性读取保持原生内部Value，不读取UserString、Unit.Type、显示单位名称或原生调试字符串。MCP序列化器原有Unit.Type和无效维度的诊断文字保持兼容；这些诊断不用于选择内部单位。不能把原始Value直接标N、Pa、W或摄氏度；本合同不提供显示单位转换。[Quantity getter](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyUnits.cpp#L46-L61)、[Value接口](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Base/QuantityPyImp.cpp#L623-L648)、[内部单位方案](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Base/UnitsSchemasData.h#L81-L174)

固定Property类型与其原生Unit类别的绑定来自[PropertyUnits](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyUnits.cpp#L213-L790)。代码中的预期Signature按物理定义设定，并不代替每个原生类型的运行验证；只接受实际读取维度与该预期相同的值。通用PropertyQuantity不猜物理种类，保留其有效Signature。

## 状态、预算与来源

- 字段不在成功读取的PropertiesList中为missing；清单失败为read_failed，不伪装成缺失。
- 原生值为None、量值元数据缺失/畸形、非有限数值或固定类型维度矛盾为unknown，不发布猜测数值。
- Value、Unit或Signature出现非AttributeError读取异常，字段及对象行保留read_failed；失败后不为补信息继续读取其它量值getter。宿主读层把AttributeError观测为unavailable，因此这类结果为unknown；它不能证明是API缺失还是getter内部的同类错误，不通过额外原生反射猜测。
- 枚举/字符串保留2KiB UTF-8、控制字符净化和截断标记；整笔属性仍为128KiB，超限整笔拒绝，不静默裁剪成完整结果。
- 只读明确对象/字段；不显式解析Link来源。原生Link属性可能由宿主报告继承值，本地或继承来源仍为not_verified，不能声称已经证明是实例本地属性。
- 格式化与Markdown使用同一值/类型/状态，文本转义不等于匿名；属性和导出内容可能包含用户信息，不记录进源码或分发包。

独立测试覆盖单位、枚举、嵌套异常、未知、默认BOM边界和明确读取范围。它们不代替FreeCAD原生类型、界面、单位显示或端到端验收；原生运行仍须按对应提交和明确授权的人工模型分别验证。
