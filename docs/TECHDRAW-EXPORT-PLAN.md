# 单页 TechDraw 导出实施合同草案

状态：**PDF预览组件及macOS受限单页的独立MCP/原生确认链已有自动化证据，仍为未发布候选**。版本保持0.3.2；正式发行仍为默认完整25/聊天19工具，候选源码新增三个导出入口后为完整28/聊天22。既有文件选择验收使用默认Cocoa配置，但由QA预填并调用Qt `accept()`，尚未验证系统保存面板的Save/Cancel动作和完成回调；不称用户手工验收。官方模型聊天调用及更多平台仍待验。本合同衔接已实现的草稿和关联尺寸；不把单页试验当成[批量输出](BATCH-EXPORT-PLAN.md)或制造批准。

## 已确认的原生行为

FreeCAD1.1.0的`TechDrawGui.exportPageAsPdf/Svg(page,path)`都直接写文件，正常返回None，部分失败也没有可靠返回信号。非空场景对象不证明图页已打开或投影完成。独立中性样件已通过正式MCP进入原生GUI生成两个格式，并分别用PDF解析/渲染和SVG XML检查；这些仅是底层探针，尚无产品导出确认链。

PDF先绘制输出，随后`redrawCommand()`会重算本页视图。因此刚写出的PDF并不因后置刷新而自动包含最新投影。图框和关联尺寸可变为Touched；仅GUI Modified标记相同不能证明对象状态保持。SVG会清全局选择、重新处理图框并回填宽高/方向。图框标记可能删除重建，不能保存旧Qt item指针再恢复。

这类捕获不属于严格只读：普通查询、prepare和打开设置不能隐式调用它。原生确认必须明确说明本页刷新/重算、图框及选择影响；不保存CAD、不调用文档Undo来回滚磁盘文件。没有公开可靠的HLR完成检查，继续保留`projection_verification:not_checked`与人工复核要求。

## 领域边界

候选源码新增三个独立入口：`prepare_drawing_export`、`request_drawing_export_approval`、`get_drawing_export_status`。它们不复用零件修改的apply入口、changed_count或CAD撤销语义；接口已接入不等于原生完整导出验收通过。

- prepare只绑定明确文档/单页/格式、创建有界内存计划；不打开图页、不渲染、不选择目标或写用户文件。实例/partial revision用于早期拒绝，不能替代内部完整快照。
- request只打开或复用本token的非模态原生交互。原生保存框使用异步open/信号分阶段处理，不能在零件确认回调里运行嵌套事件循环。AI不能提供确认布尔值、目标路径或覆盖授权。
- 用户明确触发捕获后，原生只写本次私有staging。绑定原场景和支持的节点/来源，前后核对几何、布局、引用、字体、颜色、线宽与显示属性；不能直接将关联尺寸快照当成完整渲染快照。已知原生渲染输入还包括纸面颜色、LightOnDark/Monochrome、PDF版本、纸幅、DPI及字体，须纳入冻结和验收。
- 文件选择返回后和最终确认前重验实例、token、期限和快照。完整预览展示同一份冻结bytes、实际大小/摘要、目标与发布动作；最终只发布这些bytes，不重新导出。
- status只读内存。重复请求复用原窗口/终态，去重与请求归属跨超时保持；结果未知时不换token重做。已发布回执是历史，不证明文件现在仍存在。

macOS系统保存面板的人工操作须单独验收。测试驱动不能直接调用`NSSavePanel.ok:`模拟保存：[Apple说明](https://developer.apple.com/documentation/appkit/nssavepanel/ok%28_%3A%29)自macOS10.15起不支持这种程序调用。独立驱动曾因此原生异常退出，原失败保留；后续保留真正的系统Save操作及原Qt完成信号，不以Qt `accept()`或手发信号替代。

第一轮节点profile须明确列出支持类型、依赖与显示属性；未覆盖的剖面/局部视图、Proxy、Link、跨文档引用或资源不能因原生函数能写出文件就接受。已打开单页、默认名及支持格式都需运行态核实，不能借此宣称任意已有Drawing保真导出。

后续候选允许已绑定的Page、SVG Template及五类Dimension在准备时带原生`Touched`缓存标记，但精确冻结该初始状态，捕获前自行变化仍使计划失效。View、来源依赖和无关对象继续要求干净；只有已确认的原生捕获阶段允许目标节点的已知缓存标记转换，几何、边缓存、引用、量值、样式、资源与场景语义仍须完全一致。来源及Body Tip复用有界本地无表达式依赖签名，晚换边、跨文档或自定义执行来源明确拒绝。这针对Undo/Redo后Page和尺寸仍Touched的衔接缺口，不手动清状态或在预览时重算；完整实际包PDF链仍待该候选原生验收。

本页节点同时核对原生OutList：Page只指向本页Template与Views成员，Template无对象链接，View只指向同一来源，Dimension只指向已验证的单一References2D视图；XSource、References3D及表达式为空。不接受额外本地链接后再沿其触发未知执行，额外目标在拒绝时也不读取。原生依赖边的实际顺序纳入冻结，但不预设固定排序。依据：[原生依赖汇总](https://github.com/FreeCAD/FreeCAD/blob/1.1.4/src/App/DocumentObject.cpp#L435-L473)、[图页刷新](https://github.com/FreeCAD/FreeCAD/blob/1.1.4/src/Mod/TechDraw/App/DrawPage.cpp#L323-L357)。

后续候选在用户明确触发Capture后，对已打开和未打开的目标页都调用绑定图页的原生显示/刷新，并在下一GUI事件轮绑定场景。它先验证保护字段、资源和受支持的场景节点，再向固定1000×1000、4MB的私有内存画布绘制完整纸面，使可见纸面上的延迟文字和线条在冻结前物化；绘制范围从原生场景的三倍纸幅关系及实际纸幅共同核实，不猜测当前缩放因子。该画布不输出文件，也不替代PDF预览；预览仍来自随后唯一一次原生捕获的冻结PDF。刷新可能打开或激活目标图页、居中视图，属于Capture已说明的界面影响。关闭图页的原生show还会同步其App和ViewProvider的Visibility；候选须仅在这一次已授权show前后核准该目标页两个布尔值同时从False变为True，其余输入精确不变，随后完整冻结新状态。已有图页、单边变化、类型不符及其他节点变化不能借此通过。依据：[目标页显示](https://github.com/FreeCAD/FreeCAD/blob/1.1.4/src/Mod/TechDraw/Gui/ViewProviderPage.cpp#L275-L317)、[原生可见性同步](https://github.com/FreeCAD/FreeCAD/blob/1.1.4/src/Gui/ViewProviderDocumentObject.cpp#L176-L233)。绘制前后仍精确检查所有保护字段和原场景归属，捕获后的场景语义校验不放宽；隐藏或纸外节点若在捕获中继续改变，仍拒绝导出。实际包的完整默认出图链继续单独原生验收。

候选由独立有界内存登记管理request ID、token和生命周期。prepare在GUI派发前原子认领请求，同ID跨排队超时、终态和记录回收保留归属，不自动重新捕获。status仅返回内存历史，区分捕获、文件发布、临时清理和CAD保护结果。原生面板分别确认捕获、冻结字节预览、异步选择路径和最终发布，AI参数没有路径、确认或覆盖入口。最终导出默认沿用已保存文档所在目录和文件stem＋`.pdf`；未保存文档仅建议文件名，不猜测目录。文件发布复用通用目录身份和冻结字节校验核心。

MCP候选要求完整运行态capability、三个方法及严格DTO；实例切换、同端口服务更换和请求绑定失效均拒绝，不降级成脚本。聊天仍由既有明确的FreeCAD工具开关控制；增加三个方法不读取旧账户或迁移旧会话权限，旧工具范围不匹配时保守拒绝恢复。独立自动化已覆盖协议、原生回调重入、取消、到期、晚回调及发布结果未知。macOS FreeCAD1.1.4在独立未保存Part::Feature和Sketch→Pad→Pocket Body、三视图及五类关联尺寸上验证了正式SDK、原生Capture/Publish按钮、Qt文件选择信号与冻结字节发布及内存历史。文件选择使用默认Cocoa配置及QA预填/Qt `accept()`，系统保存面板本身的Save/Cancel和完成回调仍待独立核验；具体范围和遗留失败见[验证记录](VERIFICATION.md)。用户手工操作、更多场景、SVG与跨平台继续单独验收。

## 预览、解析与输出验证

PDF预览/解析组件不能假设随FreeCAD安装；QtSvg不等于QtPdf。 独立检查的macOS FreeCAD1.1.4发行资源虽有PySide6/Qt6.8.3，但没有QtPdf/PdfWidgets绑定或PDF图像插件；QPdfWriter和PrintSupport只有输出能力，不能作为预览依赖。该检查不代表其它平台或同版本其它构建。独立QA解析器可作验收证据，不能成为未声明的产品运行依赖。需先落实可分发、版本与许可明确的预览器/解析器，或明确报告能力不可用，不自动安装组件或偷偷改成SVG转PDF。

PDF须核真实单页、MediaBox与纸面单位、实际渲染及关键符号；SVG须有界解析XML、真实width/height/viewBox、模板和独立DrawingContent，并拒绝脚本、实体与可加载或执行的外部资源引用（如href/image/style url/DTD）；命名空间和许可元数据URL本身不是渲染依赖，不能简单按http字符串一概拒绝。解析成功、文件非空或有图框不等于无裁切、最新投影或尺寸完整。低/高DPI、横/竖/自定义幅面、颜色、线宽及字体替代分别验收，原生PDF生成时间等元数据不承诺逐字节确定。

内置ISO图框的裁切标记和已渲染纸色都属于原页面内容，默认保持；另选白纸或单色须单独明确策略，不自动删黑角或白化。

原生捕获可能清选择或改标记。必须先确定可核实的范围与恢复办法；不能全局clear覆盖外来选择，不能重用已删除的marker对象。恢复无法证明时不得把文件发布状态和CAD保护状态混成一个成功结论。

### 已实现的冻结PDF预览组件（尚未发布）

macOS候选采用系统CoreGraphics，不增加第三方解析器或下载依赖。`pft_pdf_contract.py`定义严格单次JSON协议，输入只有冻结PDF字节、SHA-256和目标像素尺寸，不接受文件路径、命令或捕获操作。输入PDF上限8MiB，单页，原生MediaBox及四种直角旋转，UserUnit必须为1；加密、坏纸幅和不支持的页数明确拒绝。请求上限12MiB，响应上限6MiB，最大100万像素且单轴不超过2048；结果绑定原始摘要、字节数和请求画布。

`pft_pdf_helper.py`在独立的应用内Python进程中解析并绘制，显式声明CoreFoundation/CoreGraphics ABI、逆序释放自有资源，借用的页和字典不释放。白底画布使用完整MediaBox、原生旋转和等比缩放居中，并裁切到纸面；不把PDF流中的越页绘图当成白边内容。位图固定为不透明RGBA8888 premultiplied。进程隔离避免解析器崩溃直接结束GUI，但不构成针对任意恶意PDF的硬内存沙箱；这一组件的用途是本插件生成的有限图纸，不新增任意文件导入接口。

`pft_pdf_qprocess.py`使用`App.getHomePath()`给出的应用运行根查找已存在的Python；`App.getResourceDir()`可能只指向`share`资源子目录，不能代替运行根。以独立参数数组和`-I -S -B`启动同位置固定helper，不搜索PATH或继承账号、代理、Python注入环境。默认15秒截止、stdout/stderr有界；超时或取消仅终止自有QProcess，等待真实终态再发布一次固定结果。Starting期间的迟到启动、关闭面板、旧回调和显式重试分别受生命周期保护；未知状态不能当成退出或允许替换进程。

`pft_pdf_preview_views.py`只接收验证过的像素DTO，再次校验后通过`QImage.copy()`持有图像，支持适合窗口和100%滚动检查。切换显示大小不重新捕获图纸，不读取原PDF路径；清空和关闭清除图像。上层仍须将回调绑定本次请求和窗口寿命。该组件本身不创建导出计划、写用户文件或提供MCP工具；完整原生捕获、确认、文件发布和状态查询链继续按本合同实施。

其它平台仍需实现并原生验收相应后端；不存在时明确不可用，不自动安装依赖。QtPdf在具备正式绑定的构建中仍是后续可选路径，不能用QtSvg或外部QA解析器代替声明的运行依赖。

## 文件发布与异常终态

文件发布核心只处理冻结bytes和明确目标，不依赖FreeCAD/Qt；格式校验在调用方完成。复用当前PNG的祖先/父目录身份、有限目标指纹、目录fd、排他暂存、写后回读、发布前后身份/摘要检查与仅清理自有stage。新文件用独占发布；若支持覆盖，必须明确普通指纹检查加replace不是跨进程CAS，也不承诺掉电持久性。

进入原子发布后尚未完成最终核验为unknown；不得自动删除最终文件或重试。已验证发布但清理失败，仍保留published证据，另报cleanup unknown。文件取消不等于回滚已发布输出，CAD撤销也不能恢复或删除导出文件。

## 实施与验收顺序

1. 通用文件发布核心已接现有Markdown，补齐暂存和最终字节反验、发布未知及清理终态；原生确认及分发验收分别记录，PNG行为保持独立回归。
2. 完成原生捕获的显示/选择/标记恢复与输入profile，落实PDF/SVG验证和完整预览组件。
3. 接独立MCP/聊天合同、能力门禁与实例归属，验证取消、过期、重入、停服务、晚回调、目标变化及历史查询。
4. 用独立样件完成真实文件框、冻结预览/发布、单位与内容渲染、源模型/选择/状态分项检查，随后审实际包、隐私和许可。

最初底层探针覆盖FreeCAD1.1.0；候选PDF产品profile另外在macOS FreeCAD1.1.4独立验收并精确限制该版本。其它构建及26.3须建立各自兼容验收，不能按版本号更大便推定API/图形行为相同。批量、多页合并、SVG、DXF、自动制图/补尺寸、公差、制造批准和跨平台验收继续分别推进。运行材料和图像保留仓库外，公开示例不来自真实使用讨论或工程。

原生依据：[PDF/SVG入口](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/Gui/AppTechDrawGuiPy.cpp#L157-L227)、[PDF绘制及后置重绘](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/Gui/PagePrinter.cpp#L313-L358)、[图页重算](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/App/DrawPage.cpp#L320-L354)、[SVG导出](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/Gui/QGSPage.cpp#L969-L1131)、[选择同步](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/Gui/MDIViewPage.cpp#L749-L781)、[纸面绘制](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/Gui/QGISVGTemplate.cpp#L211-L229)、[异步文件框](https://doc.qt.io/qt-6/qfiledialog.html#open)。
