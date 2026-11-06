# 基础 TechDraw 图纸草稿

当前候选保持0.3.2。`create_drawing` 是已有 `prepare_part_changes` 的新操作，不增加工具名，完整/聊天基础清单仍为25/19；两种客户端共用原生确认链。部署、实际投影、排版和导出分别验收，源码与合成测试不代表生产图纸已完成。

## 调用与结果

```json
{"document":"ExampleDocument","objects":["ExampleSource"],"operation":"create_drawing"}
```

明确指定一个活动文档的内部Name和一个受支持来源。首版读取原生`Part::Feature`或`PartDesign::Body`的有效单实体缓存；来源须完成且可核对，Body还绑定当前Tip。未重算来源、Link、Python代理、复杂现有图纸或未知状态先报告不支持，不解除附着或修改来源来凑成功。

Node先核`drawing_create` v1及原生确认能力，再派发一次预览。预览列出五个预定节点、来源、比例和固定布局；内置A4横向模板不接受外部路径。模板内容hash、文档/来源/Shape及Tip身份、对象集合和名称冲突留在内存计划，确认前再次核对。来源可位于最多16层原生`App::Part`中；父对象身份、完整Group隶属及局部/全局Placement同样冻结。歧义、多父、循环、代理及不支持容器拒绝；预览后父变换或同变换重挂也使旧计划失效。

可应用时，用原token调用`request_part_change_approval`，等待用户在FreeCAD原生窗口点击确认。直接`apply_part_changes`始终拒绝图纸，包括成功后的重放。取消或预览阶段不创建节点。应用只新增页面、模板、主视、俯视和轴测三视图，第一角俯视图在主视图下方；从全局放置后的缓存包围盒估算投影跨度并选保守比例。有父容器时只在独立Shape副本上用全局Placement替换局部Placement，不再叠乘局部变换，不写来源。三个新视图明确设置并回读`CoarseView=False`。单事务可撤销，仅重算新增节点，不整文档重算，不自动保存CAD、导出或覆盖已有对象。

用原token查询`get_part_change_status`。`applied`报告五个图纸节点已创建；`result.design_result`包含页面/模板/来源、三个视图名、比例及投影方式。`projection_verification="not_checked"`与`manual_review_required=true`明确表示异步投影与最终排版仍需检查。使用`inspect_drawing_page`读取返回页面，并在FreeCAD打开图页核对；重算计数、对象创建成功和AI文字都不能证明图纸完整。

## 原生验证与边界

独立公开两孔板（60×40×8 mm）的 FreeCAD 1.1 / OCCT 7.8.1 检查已走通完整 MCP stdio、五行预览、原生按钮测试回调及原 token 查询，实际创建五节点；源模型保持未保存。三视图已观察到精确投影边，打开图页后核对第一角布局与内置图框，并验证一次原生撤销/重做。自动化点击不冒充使用者人工批准，也不证明任意模型或平台通过。

另外使用独立构造的Sketch→Pad→Pocket Body核对普通、小型、大型尺寸，以及旋转父容器中的长条和双层父容器/来源局部旋转Body。五组正式MCP预览、原生确认按钮自动化、实际精确投影边界、首次打开图页位置及原生撤销/重做均通过，源参数、Tip、几何与可见性保持；父移动后的旧计划返回`DRAWING_PLAN_STALE`，零图纸节点。验收派生图像已检查标题栏避让，CAD未保存。这不是任意装配、尺寸完整性或批量PDF/DXF验收。

开启事务与建立文档撤销记录是两个阶段：首次写入前允许自有数字 ID 对应的惰性空事务，写入后必须有待提交记录；不以空事务规则处理写后丢失状态。固定布局给标题栏预留80 mm，预览显示各视图中心。

创建前在 GUI 线程对固定1 mm内存立方体做一次精确同步投影，再重新检查计划。这缓解 OCCT 首次并发初始化共享平面的风险；不创建辅助文档对象，不改线程池或降为粗略视图，也不证明所有原生并发问题均已消除。依据：[OCCT BRepLib共享平面](https://github.com/Open-Cascade-SAS/OCCT/blob/V7_8_1/src/BRepLib/BRepLib.cxx)、[FreeCAD异步精确投影](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/App/DrawViewPart.cpp)。初始化失败在开启写事务前返回固定错误码。

原生SVG导出需要先打开图页，使GUI建立模板图形；只创建页面和可见投影边不等于导出图框已经准备好。导出仍是后续独立显式操作，当前创建接口不自动打开、保存或导出页面。

## 下一步

- 已完成上述独立Body、不同尺寸和有限父容器检查；继续更多来源、偏好、平台和使用者逐图复核。
- 已接入明确投影Edge引用的五类[关联尺寸与原生回读](TECHDRAW-DIMENSIONS.md)；继续更多引用/类型、自动布局、孔槽/螺纹与尺寸完整性。
- PDF/SVG导出作为独立显式文件操作，检查目标冲突、投影完成与导出内容。
- 图纸预览、修订与布局扩展，逐项核验，不能替代生产图纸审签。

API依据：[TechDraw页面和视图](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/Gui/Command.cpp)、[原生投影实现](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/App/DrawViewPart.cpp)。内核投影可异步执行；本轮安装版本首次打开图页的位置保持。实现不在提交后定时改写布局，其他安装及偏好仍需核验。父容器定位依据：[ShapeExtractor全局Placement](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/App/ShapeExtractor.cpp#L421-L429)。
