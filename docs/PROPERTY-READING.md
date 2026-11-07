# 当前文档属性读取合同

属性设置只作用于当前工具面板，最多20个明确内部字段名。空清单不读取属性；缺省仍只读五个正式字符串BOM字段，不能因为显式模式扩展而把默认BOM改成枚举或数量聚合。不创建字段、不调用setter、不重算、不保存。

## 类型和值

| 原生类型 | 显式读取值 |
| --- | --- |
| PropertyString | 有界字符串；空字符串为empty |
| PropertyInteger / Float / Bool | 类型严格匹配的有限数值或布尔；0与false是有效值 |
| PropertyIntegerConstraint / Percent | 原生getter当前整数；不读取或推断约束边界 |
| PropertyFloatConstraint / Precision | 原生getter有限float；不把Precision猜成长度或mm |
| PropertyStringList / IntegerList / FloatList / BoolList | 明确选择的同类型原始值列表；保留次序、重复、0、false、空字符串与空列表；BoolList原生tuple复制为列表 |
| PropertyVector | 原生有限浮点xyz；单位及坐标系未声明，不推断为长度或方向 |
| PropertyVectorDistance / Position | 原生有限浮点xyz，类型声明内部长度单位mm；不推断装配世界坐标 |
| PropertyVectorList | 原始向量列表；保留次序、重复和零向量，单位及坐标系未声明 |
| PropertyRotation | 原生四元数 x/y/z/w，w为实部；原值无量纲，不改符号或归一化 |
| PropertyPlacement | 原生Base xyz与Rotation四元数；平移内部mm、旋转无量纲，不应用变换 |
| PropertyPlacementList | 明确选择的原生位姿列表，逐项复制Base xyz与Rotation四元数，保留顺序、重复及单位位姿；后续修正，尚未发布 |
| PropertyEnumeration | 当前选中字符串；不调用getEnumerationsOfProperty、不取索引或全部选项 |
| PropertyQuantity / QuantityConstraint | 原生Value与有效八维Signature；组合维度按内部基底表达 |
| PropertyLength / Distance / Angle | 另核对长度或角度的预期维度 |
| PropertyArea / Volume / Mass / Density | 另核对面积、体积、质量或密度的预期维度 |
| PropertySpeed / Acceleration / Force / Pressure / Frequency / Power / Temperature | 另核对对应物理预期维度 |
| 其它类型 | unsupported；不读取其值或用str/repr展开对象 |

以上均为`App::`类型。枚举的普通Python getter与setter、`.Enum/.All`路径接口不同；只处理选中值。[FreeCAD 1.1实现](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyStandard.cpp#L446-L453)

五种受限类型只在显式字段模式处理。IntegerConstraint/FloatConstraint的四元tuple是setter输入，getter继承Integer/Float并返回当前标量；Percent/Precision使用同一原生读取路径。约束可被宿主修改，读取不强加0–100或非负范围，不读取min/max/step，不把属性名当单位。QuantityConstraint沿用Quantity的Value/Signature合同，不预设物理种类。[原生声明及getter继承](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyStandard.h#L264-L354)、[Precision定义](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyStandard.h#L669-L685)、[QuantityConstraint](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyUnits.h#L102-L142)

独立FreeCAD1.1原生检查已确认IntegerConstraint为int、FloatConstraint为float、Percent为int、Precision为float、QuantityConstraint为Base.Quantity且Value为float、Signature为八个整数；读取保持修改/待重算状态与事务，未保存CAD。同一组独立样件已通过本工具实际Qt属性设置读取信息和Markdown，五字段均available；复制处理器输出与冻结Markdown一致（使用QA内存接收器，不读写系统剪贴板），原生导出确认后文件与完整预览逐字节一致。读取及导出保持源状态和事务，不保存CAD；系统剪贴板和其它类型/平台另验。

## 原始值列表与整数精度

只接受普通list或tuple，不遍历自定义容器、嵌套结构或原生对象。StringList每项须为str，IntegerList为非bool的int，FloatList为有限float，BoolList为bool；不把不同类型互相转换。LinkList仍在读取getter之前拒绝；VectorList及PlacementList按下文独立合同处理。不展开引用或数量聚合。

每字段最多100项；清洗后的完整紧凑JSON列表最多2 KiB UTF-8，包含引号、转义和分隔符。超限时整字段`value:null/status:unknown/truncated:true`，保留`item_count`及`limit_reason:item_limit/utf8_budget`，不发布误导性的列表前缀。空列表是`empty`，与None、缺失及读取失败不同。

本地Integer及IntegerList保留有符号64位整数；信息和Markdown由Python直接格式化冻结快照，不经过JavaScript解析，不为JS安全范围缩小原生读取。需要经MCP传输的调用须使用对应传输合同，不能假定裸JSON大整数在所有客户端精确；通用脚本结果的带类型十进制编码见[脚本协议](PROTOCOL.md)。

独立人工两孔板的原生检查已覆盖四种列表、BoolList tuple、0/false/空列表、缺失与不支持类型、条数/UTF-8超限、实际属性设置对话框、信息/Markdown，以及另一次元数据修改后的刷新和原快照冻结。读取未改变几何、选择、材质、相机或修改状态，不保存CAD。范围仅为该人工Part::Feature样件，不能替代其它原生类型和平台验收。

## 向量与坐标来源

仅在明确选择字段后读取`App::PropertyVector/PropertyVectorDistance/PropertyPosition/PropertyVectorList`。标量与列表成员须为原生`Base.Vector`，逐个复制小写`x/y/z`的有限float，保持原生浮点值、负零和精度；不迭代或字符串化任意对象，不调用Length/Unit/Proxy或推导变换。[标量及列表getter](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyGeo.cpp)、[浮点分量getter](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Base/VectorPyImp.cpp)

`VectorDistance/Position`由原生属性类型声明长度，采用`unit:mm`、`unit_signature:[1,0,0,0,0,0,0,0]`、`unit_system:freecad_internal`。普通Vector与VectorList没有固定单位声明，使用`unit:null/unit_signature:null/unit_system:not_declared`；不将其猜成mm或无量纲。类型定义见[PropertyGeo.h](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyGeo.h)。

标量DTO为`kind:vector/value:{x,y,z}`，列表为`kind:vector_list/value:[{x,y,z},…]`。单位及坐标元数据在字段级保存一次：`unit_source:native_property_type`、`coordinate_scope:reported_property_value`、`coordinate_frame:not_declared_by_property_type`、`placement_applied_by_tool:false`、`link_source_resolved:false`。信息与Markdown格式化同一冻结值并显示这些来源，不把普通向量、位置属性与缓存包围盒尺寸混为一谈，也不宣称本地/继承来源已经验证。

VectorList仅接受原生getter返回的普通list；空`[]`为empty，零向量为有效值。坏成员、缺失分量或非有限数值均不发布部分列表。最多100项且完整紧凑xyz JSON列表最多2 KiB，二者取先到的上限；100项不是保证可容纳的数量。单位元数据另计入整笔128 KiB。原生getter会先生成完整PyList，这些上限只限制后续处理和DTO输出，不限制原生getter的内存或耗时。引用和量值列表仍不支持；Rotation与Placement遵循下节独立合同。

macOS独立FreeCAD 1.1人工Part::Feature已核对四种原生类型、浮点分量、零向量与空列表，通过实际Qt属性设置按钮读取信息/Markdown，并验证未保存值刷新与原快照冻结。读取保持几何、材质、选择、稳定后的相机、修改状态且无重算事件；未保存CAD。严格坏值和预算边界另有单元回归，不把此样件检查扩展为任意代理、装配或平台通过。

## 旋转与 Placement

仅明确选择`App::PropertyRotation/PropertyPlacement`时读取，不改变缺省五个BOM字段。Rotation须为原生`Base.Rotation`，只复制`Q`普通tuple中的四个有限float，顺序`x,y,z,w`（w为实部）；单位旋转与负号等价表示保留原值，不转轴角、矩阵或欧拉角，不计算范数、改符号或归一化。全零四元数是未知；其它有限值保留但明确`quaternion_normalization:not_checked/quaternion_normalized_by_tool:false`，不把复制数值当作旋转有效性验证。[顺序定义](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Base/Rotation.pyi#L58-L64)、[Q getter](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Base/RotationPyImp.cpp#L454-L467)

Rotation DTO为`kind:rotation/value:{x,y,z,w}/representation:quaternion_xyzw`，`unit:1/unit_signature:[0,0,0,0,0,0,0,0]/unit_system:freecad_internal`。Placement须为原生`Base.Placement`，仅复制`Base`原生Vector xyz和`Rotation.Q`，DTO为`kind:placement/value:{base:{x,y,z},rotation:{x,y,z,w}}/representation:base_xyz_rotation_quaternion_xyzw`。整个Placement没有统一长度单位，使用`unit:null/unit_signature:null/unit_system:mixed_components`，`component_units.base`声明内部mm与长度维度，`component_units.rotation`声明无量纲。零平移和单位旋转是有效值，不当作空值。[Placement getter](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Base/PlacementPyImp.cpp#L330-L342)、[属性与长度来源](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyGeo.cpp#L750-L765)

两者沿用`unit_source:native_property_type/coordinate_scope:reported_property_value/coordinate_frame:not_declared_by_property_type`，并明确`placement_applied_by_tool:false/link_source_resolved:false`。不解析父对象、Link、装配坐标系或全局位置，不把自定义Placement与对象自身Placement相乘；宿主可能报告继承值，来源locality仍未验证。[原生Placement副本](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyGeo.cpp#L795-L798)、[原生Rotation副本](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyGeo.cpp#L1191-L1194)

畸形类型、缺失或非有限分量为unknown；嵌套读取的非AttributeError异常为read_failed，立即停止此字段，不发布半个Placement或继续读后续getter。完整紧凑value JSON仍限2KiB，单位／来源元数据另计整笔128KiB；超限整字段未知并标记截断。信息及Markdown格式化同一冻结DTO，不重新读取原生值。

macOS独立FreeCAD1.1人工Part::Feature已核对原生Rotation、动态Placement和对象自身Placement的实际类型、tuple4浮点Q、零／非零平移及混合单位，通过真实Qt字段设置读取信息／Markdown，并验证未保存值刷新与旧快照冻结。读取保持几何、材质、选择、相机、修改状态和事务，未发生重算或保存CAD。该独立样件不证明任意代理、装配变换、引用或其它平台均已验收。

### 位姿列表（后续修正，尚未发布）

显式选择`App::PropertyPlacementList`时，仅读原生getter返回的普通list，逐项复制`Base.Placement`的Base xyz和Rotation.Q；不展开引用、父链或装配，也不把列表当作对象实例数量。原生getter构造各Placement的副本，见[FreeCAD1.1.4原生实现](https://github.com/FreeCAD/FreeCAD/blob/1.1.4/src/App/PropertyGeo.cpp#L900-L907)。

DTO为`kind:placement_list/value:[{base:{x,y,z},rotation:{x,y,z,w}},…]`，保留原顺序、重复、零平移、单位旋转和有限浮点精度。字段级单位、表示法及坐标来源沿用单个Placement合同，`component_units.base`为内部mm、rotation无量纲，不声明世界坐标或旋转已经归一化。空list为`value:[]/status:empty/item_count:0`，None保持未知。

最多100项且完整紧凑value JSON不超过2KiB，两者取先到的上限；单位与来源另计入整笔128KiB。坏成员或嵌套读取异常使整个字段未知／读取失败，不发布有效前缀；超限保留`item_count`、`truncated:true`及固定`limit_reason`。这些限制发生在原生getter之后，不保证限制原生完整PyList分配。信息、复制和Markdown复用冻结的原始值，不重读列表、改变默认BOM或写CAD。

独立macOS FreeCAD1.1.4/Qt6.8.3的未保存Part::Feature已完成44项原生检查：真实getter及分量类型、零／单位位姿、非零平移旋转、重复及空列表、实际设置Cancel/OK、信息/Markdown、复制处理器、未保存新值刷新与旧DTO冻结，以及默认五字段恢复。文件选择和复制接收为QA注入，完整Markdown预览及确认采用真实Qt按钮，产物字节精确对应预览。读取阶段几何、材质、选择、相机、修改及待重算状态保持，未发生setter、重算、事务或CAD保存；自有进程正常退出。该样件不证明系统剪贴板、原生文件框、任意代理或跨平台通过。

## 内部单位与可选显示单位

`pft_units.py`是无FreeCAD/Qt/文件访问的纯格式化模块，当前文档工具与MCP原生序列化器共享。它仅接受实际list/tuple中的八个非bool整数，指数范围−128…127；基底顺序为`mm,kg,s,A,K,mol,cd,deg`。0次幂省略，1次幂不写指数，组合以`*`连接，全0为`1`。例如密度为`mm^-3*kg`，力为`mm*kg*s^-2`，压力为`mm^-1*kg*s^-2`，功率为`mm^2*kg*s^-3`。

默认读取保持原生内部Value，不读取UserString、Unit.Type、显示单位名称或原生调试字符串。MCP序列化器原有Unit.Type和无效维度的诊断文字保持兼容；这些诊断不用于选择内部单位。不能把原始Value直接标N、Pa、W或摄氏度；内部数值不会直接用于猜测显示单位转换。[Quantity getter](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyUnits.cpp#L46-L61)、[Value接口](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Base/QuantityPyImp.cpp#L623-L648)、[内部单位方案](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Base/UnitsSchemasData.h#L81-L174)

固定Property类型与其原生Unit类别的绑定来自[PropertyUnits](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/PropertyUnits.cpp#L213-L790)。代码中的预期Signature按物理定义设定，并不代替每个原生类型的运行验证；只接受实际读取维度与该预期相同的值。通用PropertyQuantity不猜物理种类，保留其有效Signature。

## 跟随 FreeCAD 显示单位（后续修正，尚未发布）

“读取属性设置”中的**使用 FreeCAD 显示单位**默认关闭，仅在当前面板中生效；取消保留原设置，关闭重开恢复默认，不改变FreeCAD全局单位方案或CAD属性。开启后保留全部内部值、Signature及缓存尺寸mm值，另外冻结原生`Quantity.getUserPreferred()`返回的完整显示文字、换算因子及单位。格式化不解析本地化数字、分数或温度，不自行乘除因子；由原生单位系统负责对应方案，信息、尺寸、复制与Markdown共用本次冻结结果。[原生接口](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Base/QuantityPyImp.cpp#L158-L170)

量值使用同一个已验证属性副本，DTO位于`field.display_quantity`；缓存包围盒尺寸用原生`Units.Quantity(mm_value,"mm")`构造独立副本，各轴位于`row.display_dimensions`。每项包含`status/text/factor/unit/source:native_getUserPreferred`。原始`field.value`与`length_mm/width_mm/height_mm`不变。成功时显示原生文字；失败保留内部值并明确标出显示不可读取，不能把内部单位冒充显示转换成功。显示缺失、畸形、异常和超限分别保留状态；超限不发布半条数字或单位。净化文本有标记，单项2KiB、全部新增显示数据连同属性汇总受128KiB约束。

同一次快照保持显示文字，即使之后FreeCAD单位方案或内存值发生变化，也不在复制或导出时偷偷重读；用户明确刷新后才能取得新显示。方法可能使用宿主的当前格式、精度和语言设置；显示文字不是无损数值，不保证能被程序反向解析或用作制造尺寸。默认关闭时不调用此接口，不读取全局单位偏好。

独立FreeCAD1.1.4/Qt6.8.3未保存Part::Feature样件完成21项原生验收。Length、Force、Temperature、Area、Volume与无量纲Quantity均为实际Base.Quantity，显示factor为float，完整tuple与每个DTO逐项一致。实际Imperial方案下长度为非mm，信息、尺寸与Markdown由同一DTO格式化；刷新切换至另一个原生方案及自有元数据后读取新显示，旧DTO保持冻结。三个真实复制处理器使用QA内存接收器，完整Markdown预览/确认后发布的UTF-8字节与当时冻结文本一致。该发布不是Imperial长度文件专门验收，温度对照的是原生K文字，不扩大为Celsius/Fahrenheit仿射转换已验。

设置Cancel/OK、字段不变仅改单位、关闭重开恢复默认、只选面不扩大范围及完整对象默认勾选均通过；读取、刷新、复制和导出前后几何、材质、选择、相机、修改/待重算标志、事务与单位偏好保持，原生观察器未见setter、重算或CAD保存。实际文件框路径和剪贴板为QA注入，不能代替系统文件框、系统剪贴板或用户人工验收。初始QA导入与样件前置错误回执保留在仓库外；最终自有FreeCAD正常关闭，全部测试实例确认退出。

官方1.1.0原生声明与注册检查未发现`App::PropertyQuantityList`；`PropertyFloatList`是普通double列表，不能按名称猜为量值列表。引用、更多原生类型与跨平台仍单独推进。

## 状态、预算与来源

- 字段不在成功读取的PropertiesList中为missing；清单失败为read_failed，不伪装成缺失。
- 原生值为None、量值元数据缺失/畸形、非有限数值或固定类型维度矛盾为unknown，不发布猜测数值。
- Value、Unit或Signature出现非AttributeError读取异常，字段及对象行保留read_failed；失败后不为补信息继续读取其它量值getter。宿主读层把AttributeError观测为unavailable，因此这类结果为unknown；它不能证明是API缺失还是getter内部的同类错误，不通过额外原生反射猜测。
- 枚举/字符串保留2KiB UTF-8、控制字符净化和截断标记；整笔属性仍为128KiB，超限整笔拒绝，不静默裁剪成完整结果。
- 只读明确对象/字段；不显式解析Link来源。原生Link属性可能由宿主报告继承值，本地或继承来源仍为not_verified，不能声称已经证明是实例本地属性。
- 格式化与Markdown使用同一值/类型/状态，文本转义不等于匿名；属性和导出内容可能包含用户信息，不记录进源码或分发包。

独立测试覆盖单位、枚举、原始值列表、向量及Rotation/Placement、signed64文本精度、嵌套异常、未知、默认BOM边界和明确读取范围。它们不代替FreeCAD原生类型、界面、单位显示或端到端验收；原生运行仍须按对应提交和明确授权的人工模型分别验证。
