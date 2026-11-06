# 单页 TechDraw 导出实施合同草案

状态：**原生技术探针与方案，MCP/内置聊天导出尚未实现**。版本保持0.3.2，默认完整25/聊天19工具不变。本合同衔接已实现的草稿和关联尺寸；不把单页试验当成[批量输出](BATCH-EXPORT-PLAN.md)或制造批准。

## 已确认的原生行为

FreeCAD1.1.0的`TechDrawGui.exportPageAsPdf/Svg(page,path)`都直接写文件，正常返回None，部分失败也没有可靠返回信号。非空场景对象不证明图页已打开或投影完成。独立中性样件已通过正式MCP进入原生GUI生成两个格式，并分别用PDF解析/渲染和SVG XML检查；这些仅是底层探针，尚无产品导出确认链。

PDF先绘制输出，随后`redrawCommand()`会重算本页视图。因此刚写出的PDF并不因后置刷新而自动包含最新投影。图框和关联尺寸可变为Touched；仅GUI Modified标记相同不能证明对象状态保持。SVG会清全局选择、重新处理图框并回填宽高/方向。图框标记可能删除重建，不能保存旧Qt item指针再恢复。

这类捕获不属于严格只读：普通查询、prepare和打开设置不能隐式调用它。原生确认必须明确说明本页刷新/重算、图框及选择影响；不保存CAD、不调用文档Undo来回滚磁盘文件。没有公开可靠的HLR完成检查，继续保留`projection_verification:not_checked`与人工复核要求。

## 计划中的领域边界

拟新增三个独立入口：`prepare_drawing_export`、`request_drawing_export_approval`、`get_drawing_export_status`。它们不复用零件修改的apply入口、changed_count或CAD撤销语义；在真正实现前不加入当前工具清单。

- prepare只绑定明确文档/单页/格式、创建有界内存计划；不打开图页、不渲染、不选择目标或写用户文件。实例/partial revision用于早期拒绝，不能替代内部完整快照。
- request只打开或复用本token的非模态原生交互。原生保存框使用异步open/信号分阶段处理，不能在零件确认回调里运行嵌套事件循环。AI不能提供确认布尔值、目标路径或覆盖授权。
- 用户明确触发捕获后，原生只写本次私有staging。绑定原场景和支持的节点/来源，前后核对几何、布局、引用、字体、颜色、线宽与显示属性；不能直接将关联尺寸快照当成完整渲染快照。已知原生渲染输入还包括纸面颜色、LightOnDark/Monochrome、PDF版本、纸幅、DPI及字体，须纳入冻结和验收。
- 文件选择返回后和最终确认前重验实例、token、期限和快照。完整预览展示同一份冻结bytes、实际大小/摘要、目标与发布动作；最终只发布这些bytes，不重新导出。
- status只读内存。重复请求复用原窗口/终态，去重与请求归属跨超时保持；结果未知时不换token重做。已发布回执是历史，不证明文件现在仍存在。

第一轮节点profile须明确列出支持类型、依赖与显示属性；未覆盖的剖面/局部视图、Proxy、Link、跨文档引用或资源不能因原生函数能写出文件就接受。已打开单页、默认名及支持格式都需运行态核实，不能借此宣称任意已有Drawing保真导出。

## 预览、解析与输出验证

PDF预览/解析组件不能假设随FreeCAD安装；QtSvg不等于QtPdf。独立QA解析器可作验收证据，不能成为未声明的产品运行依赖。需先落实可分发、版本与许可明确的预览器/解析器，或明确报告能力不可用，不自动安装组件或偷偷改成SVG转PDF。

PDF须核真实单页、MediaBox与纸面单位、实际渲染及关键符号；SVG须有界解析XML、真实width/height/viewBox、模板和独立DrawingContent，并拒绝脚本、实体与可加载或执行的外部资源引用（如href/image/style url/DTD）；命名空间和许可元数据URL本身不是渲染依赖，不能简单按http字符串一概拒绝。解析成功、文件非空或有图框不等于无裁切、最新投影或尺寸完整。低/高DPI、横/竖/自定义幅面、颜色、线宽及字体替代分别验收，原生PDF生成时间等元数据不承诺逐字节确定。

内置ISO图框的裁切标记和已渲染纸色都属于原页面内容，默认保持；另选白纸或单色须单独明确策略，不自动删黑角或白化。

原生捕获可能清选择或改标记。必须先确定可核实的范围与恢复办法；不能全局clear覆盖外来选择，不能重用已删除的marker对象。恢复无法证明时不得把文件发布状态和CAD保护状态混成一个成功结论。

## 文件发布与异常终态

文件发布核心只处理冻结bytes和明确目标，不依赖FreeCAD/Qt；格式校验在调用方完成。复用当前PNG的祖先/父目录身份、有限目标指纹、目录fd、排他暂存、写后回读、发布前后身份/摘要检查与仅清理自有stage。新文件用独占发布；若支持覆盖，必须明确普通指纹检查加replace不是跨进程CAS，也不承诺掉电持久性。

进入原子发布后尚未完成最终核验为unknown；不得自动删除最终文件或重试。已验证发布但清理失败，仍保留published证据，另报cleanup unknown。文件取消不等于回滚已发布输出，CAD撤销也不能恢复或删除导出文件。

## 实施与验收顺序

1. 通用文件发布核心已接现有Markdown，补齐暂存和最终字节反验、发布未知及清理终态；原生确认及分发验收分别记录，PNG行为保持独立回归。
2. 完成原生捕获的显示/选择/标记恢复与输入profile，落实PDF/SVG验证和完整预览组件。
3. 接独立MCP/聊天合同、能力门禁与实例归属，验证取消、过期、重入、停服务、晚回调、目标变化及历史查询。
4. 用独立样件完成真实文件框、冻结预览/发布、单位与内容渲染、源模型/选择/状态分项检查，随后审实际包、隐私和许可。

原生探针只覆盖实际记录的FreeCAD1.1.0；1.1.4及26.3须独立建立版本兼容验收，不能按版本号更大便推定API/图形行为相同。批量、多页合并、DXF、自动制图/补尺寸、公差、制造批准和跨平台验收继续分别推进。运行材料和图像保留仓库外，公开示例不来自真实使用讨论或工程。

原生依据：[PDF/SVG入口](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/Gui/AppTechDrawGuiPy.cpp#L157-L227)、[PDF绘制及后置重绘](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/Gui/PagePrinter.cpp#L313-L358)、[图页重算](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/App/DrawPage.cpp#L320-L354)、[SVG导出](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/Gui/QGSPage.cpp#L969-L1131)、[选择同步](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/Gui/MDIViewPage.cpp#L749-L781)、[纸面绘制](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/Gui/QGISVGTemplate.cpp#L211-L229)、[异步文件框](https://doc.qt.io/qt-6/qfiledialog.html#open)。
