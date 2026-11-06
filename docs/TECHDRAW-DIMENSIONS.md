# TechDraw 关联尺寸与投影引用

当前候选保持0.3.2。完整与聊天模式使用同一条原生确认链；默认完整/聊天工具数量仍为25/19。`add_drawing_dimensions` 是 `prepare_part_changes` 的新操作，和基础 `create_drawing` 分开，不在预览时创建临时对象。

## 先读取真实引用

先检查连接、活动文档、Body/Tip、修改和编辑状态及读取来源。已有页面用 `list_drawing_pages` 定位，再明确指定页和原生视图：

```json
{"document":"ExampleDocument","page":"ExamplePage","view":"ExampleTop","offset":0,"limit":12}
```

这是 `inspect_drawing_page` 的可选扩展。只接受当前页成员中的原生 `TechDraw::DrawViewPart`，不激活窗口、改变选择、重算或保存。`drawing_references` v1与原生读取来源声明都须匹配；旧插件缺能力即停止，不退回脚本或猜索引。不传view保持原来的图页摘要。

`projected_geometry.items`逐项返回原生投影索引：**TechDraw从Edge0开始**，与三维 `Shape.Edge1` 不同。窗口最多50项，索引范围0–2047；缺边、读取失败、不支持曲线分别表达。过滤后的 `getVisibleEdges()`列表顺序不是Edge编号，不能自行enumerate后拼名称。窗口没有可确认总数，`total_count:null/enumeration_complete:false`不表示完整枚举。

可用直线返回未缩放端点及长度，闭圆返回中心、半径和周长；单位mm，坐标是视图局部、未缩放的Qt Y向下投影坐标，不是全局三维位置。线长或圆直径是**投影**测量，不承诺斜视图中的数值等于三维制造尺寸。不得再次乘除图纸Scale。

显式view查询还给受支持的既有尺寸返回`native_measurement`：原生`getRawValue()`的缓存mm值、类型和状态。只读五种Projected尺寸且Arbitrary=False；零、非有限或未知状态不伪装成有效量值，角度、真实三维测量和人为显示值覆盖暂不支持。调用量值getter前先核对所有引用属于该文档的注册实例；跨文档、未注册或不支持引用不读量值。它不读取屏幕标签，也不证明投影已更新。

## 预览并人工确认

从本次实际读取的边中选择，以下内部名称和Edge0仅作合同示例，不能不读模型便照抄：

```json
{
  "document": "ExampleDocument",
  "operation": "add_drawing_dimensions",
  "page": "ExamplePage",
  "view": "ExampleTop",
  "dimensions": [
    {"edge":"Edge0","kind":"DistanceX","xMm":0,"yMm":30}
  ]
}
```

同一页/视图一次1–8项；支持`Distance/DistanceX/DistanceY/Diameter/Radius`。线性尺寸只接受真实直线，圆尺寸只接受闭合圆；不把椭圆平均半轴当直径，不支持样条、圆弧、化妆边或中心线。图页各投影视图须引用同一完整原生单实体来源，额外XSource、Proxy、复杂来源和未知状态拒绝。同页既有尺寸也须属于上述五种Projected、Arbitrary=False、空References3D，并且仅引用本页同源原生视图的一条受支持Edge；含角度、True测量、多引用或其他不支持的既有尺寸时，整批预览拒绝，不修改这些尺寸。

图框须为本页独占、无表达式的原生SVG Template，身份、原生文件/嵌入引用、文本、宽高和方向均冻结，不额外打开模板文件。首次打开图页可能因FreeCAD回填图框属性而产生`Touched`；允许保留这个标记，不清状态或自动更新整个文档。确认前图框内容或状态变化仍使旧计划失效；只在本次受控局部重算后，允许其他冻结值完全相同时由原生清除图框的`Touched`。这不证明投影已完成，其他对象的原有状态检查保持。

`xMm/yMm`是FreeCAD尺寸对象相对视图中心的**纸面mm偏移**，沿用原生X/Y方向，和未缩放投影端点不同。位置须有限且在±1000 mm内，不自动布局或避让。重复的(edge,kind)拒绝；预定新名已存在也拒绝，不修改已有尺寸来凑成功。

预览列出每个新节点、原投影引用、类型、缓存几何估算值和偏移。完整预览展示给用户后，用原token调用`request_part_change_approval`，等待用户亲自在FreeCAD确认。外部`apply_part_changes`始终拒绝此操作，包括已成功token的重放；等待或请求已接受不表示写入。

来源Shape/Tip/父链、页/视图身份和成员、视图参数及所选边指纹均冻结。请求确认、点击确认及写入阶段再次核对；变化使旧计划失效。状态完整结果超过4 KiB时在存token前拒绝；不截断一批结果后写入。

确认后只新增尺寸，修改该页成员，并对该页和新尺寸做一次局部重算；不重算来源或投影视图，不保存CAD。单个原生分配ID事务支持撤销/重做。固定`MeasureType=Projected`、`Arbitrary=False`、空References3D及真实References2D，回读原视图和Edge、偏移、状态及原生量值。线性格式为`%.2f`，直径`⌀%.2f`，半径`R%.2f`；不接受外部格式或数字覆盖。显示单位、舍入和字体仍受FreeCAD设置影响，原生raw mm不是屏幕文字，固定两位小数不构成公差或精度要求。

用原token查询`get_part_change_status`。成功结果`kind=drawing_dimensions`包含本次提交后实际回读的`value_mm`，不是复制预览期望值；`measurement_verification=native_reference_and_value_checked`只确认该次关联引用及量值。`projection_verification=not_checked/manual_review_required=true`继续保留。状态结果是本次应用的记录；以后模型变化要重新读取现有尺寸，不能把旧结果当实时值。

## 原生证据与边界

独立自建Body/Pad/Pocket在Scale 1、0.5、0.1和0.05下，通过正式SDK/stdio→HTTP→GUI主线程、完整预览和原生按钮自动化创建五类尺寸，首次打开图页后，回读真实引用/数值/格式，并检查取消、单次撤销/重做、成功后禁止外部apply、过期视图及化妆边拒绝。已有原生尺寸的页面再追加另一类尺寸也已检查。源模型保持，CAD未保存。另一个独立Part::Feature的关联尺寸在QA明确修改来源后，由原生异步投影从600更新为800；没有重发创建或伪造数值；同一正式MCP检查接口读回800，旧摘要路由仍不读取量值。图框和五类符号已在QA导出图像中目视检查，该图像未进入源码包。验收按钮自动化不代替实际用户批准。

FreeCAD Python没有公开完整HLR pending状态；非空边、Up-to-date或两次相同缓存都不能证明当前投影完成。这里不泵GUI事件、不嵌套等待投影或自动重试未知写入。新增尺寸后的人工检查、标注完整性、关联拓扑在编辑后的稳定性、图纸布局、孔槽/螺纹、公差、导出和各平台仍分别验收。批量PDF/DXF不在本操作内。

依据：[投影边索引与缩放](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/App/DrawViewPartPyImp.cpp#L786-L857)、[原生关联尺寸](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/App/DrawViewDimension.cpp#L462-L594)、[原生量值接口](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/App/DrawViewDimensionPyImp.cpp#L36-L45)、[化妆边/中心线加入缓存](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/App/CosmeticExtension.cpp)、[首次绘制解析图框](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/Gui/QGISVGTemplate.cpp#L199-L209)、[图框宽高和方向回填](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/App/DrawSVGTemplate.cpp#L164-L189)。
