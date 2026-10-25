# 0.3.0 架构

Phoenix FreeCAD Tools 同时提供独立工作台按钮与聊天工具。Node.js / TypeScript 负责 MCP，Python 负责 FreeCAD 原生执行；普通工作台功能不依赖 MCP 或大型业务服务。

```text
聊天客户端 → MCP stdio → Node 工具/schema → 本机 JSON HTTP
                                             ↓
                                   Python bridge / Qt 调度
                                      ↓              ↓
                            operations / runtime / business
                                      ↓              ↓
                               FreeCAD GUI     共享 Model/Controller

工作台按钮 → View → Controller → Model / FreeCAD 事务
                  Facade 组装与稳定入口
```

## 工作台 MVC

| 层 | 文件 | 职责 |
| --- | --- | --- |
| Model | `phoenix_workbench_model.py` | 正式 BOM 字段、命名规则、快照和纯校验；不导入 Qt 或 FreeCAD |
| Controller | `phoenix_workbench_controller.py` | 选择和编辑状态、预览后变化校验、确认后的事务与结果核对；注入 App/Gui，不创建窗口 |
| View | `phoenix_workbench_views.py` | 中英文弹窗、预填、差异和确认；经 Controller 写入，不直接改模型 |
| Facade | `phoenix_workbench_tools.py` | 组装依赖，提供普通工具的稳定入口 |
| GUI | `phoenix_workbench_gui.py` | 注册 Phoenix Tools、三个普通工具、一个 MCP 启停命令和仅菜单的设置入口 |
| 引导 | `InitGui.py` | 在普通模块命名空间加载 GUI，兼容 FreeCAD 分离 globals/locals 的入口执行方式 |
| 翻译 | `phoenix_workbench_i18n.py` | 纯 catalog / `tr`，中文原文作键，格式化前翻译；不导入 FreeCAD 或 Qt |
| 设置 Controller | `phoenix_workbench_settings.py` | 注入 App，仅读写本插件本机语言偏好；不访问文档或 GUI |

普通按钮只处理明确选择的对象，修改前重新检查上下文和快照；写入使用命名事务，不默认保存。Link 属性转发、Label 表达式依赖及冲突均需明确处理，不能只因 API 未抛异常就报告写入符合预览。0.3.0 的 `prepare_part_changes` / `apply_part_changes` 已复用相同 Model/Controller；MCP 使用明确文档和对象，不借用 GUI 当前选择作为目标。

工作台显示名为 **Phoenix Tools**。`PhoenixTools_ToggleMCP` 是唯一启停命令，按实际状态显示绿色三角启动、红色方块停止和状态文字；只对自身按钮设置图标旁文字。选中工作台不自动启动服务。`PhoenixTools_Settings` 只增加菜单入口，确认语言后刷新本工作台已有 actions，不重新注册命令、重建定时器、重启服务或改变模型。

中文为默认，用户可明确选择 English，本机偏好位于本插件专用参数组。界面文本和技术标识分开，数据值不翻译；新打开的业务对话框采用当前语言。本轮不跟随 FreeCAD 语言，动态业务校验/原生错误的完整翻译留待后续，设置界面已说明。

## MCP 与原生执行

| 模块 | 职责 |
| --- | --- |
| `src/server.ts` | 注册当前 21 个工具、严格 schema、标题和副作用声明 |
| `src/adapters/freecad.ts` | 本机 HTTP、参数映射、`supported_methods` 检查、响应与 PNG 边界 |
| `phoenix_freecad_bridge.py` | 协议 v1 白名单、请求校验、状态、Qt GUI 调度和手动启停 |
| `phoenix_freecad_operations.py` | 有限量读取、相机，以及 `general_actions` 选择/显隐/显式重算 |
| `phoenix_freecad_runtime.py` | 显式脚本、会话内去重/状态、有限量结果和三维视图捕获 |
| `phoenix_freecad_business.py` | `phoenix` 业务分组：完整预览、内存 token、实例/快照/来源复核和共享 Controller 应用 |

Node 与客户端走 MCP stdio；Node 与插件走本机 JSON HTTP。两种协议不同，端口不提供 Streamable HTTP MCP。离线清单不代表运行时能力，新工具必须由插件明确声明；完整接口见 [协议](PROTOCOL.md)。

常用读取不重算、不保存或修改模型；相机接口改变视角。单面体积不适用，返回 `null`；包围盒仅代表内核包络，不能作精确间隙证据。截图只导出当前三维视图，临时文件读取后清理。显式脚本在 GUI 主线程执行，与 Python 控制台同权限，不是沙箱，不保证异常回滚。脚本状态查询不进 GUI 队列；请求记录属于桥接服务会话，不能跨模块重载或 FreeCAD 重启保证去重。

新通用操作均要求显式空闲活动文档。选择只替换目标文档的完整对象选择；显隐使用可撤销事务；显式重算可能更新缓存，不是只读检查，也不自动重试或保存。

业务预览模型只读但创建内存 token，`idempotentHint:false`。完整 JSON 预览超过 128 KiB 拒绝，不能截断差异后应用；token 五分钟有效、容量 32。应用复核同一文档实例、对象和来源，再调用工作台 Controller。成功 token 的重复请求只返回仍保留的历史摘要，undo 后不会 redo，也不代表对象当前状态；失败、过期或未知记录先核对模型，再重新预览。完整合同见 [协议](PROTOCOL.md)。

## 当前实现与后续封装

参数化板、受控 STEP 往返及已知参数重建已用显式脚本实测。TechDraw 四视图、6 mm 关联尺寸、文件导出和独立重开已通过，最终 PDF 渲染目视确认布局分离、无视图遮挡和越界。这些公开案例证明对应流程可行，不等于已经提供通用结构化建模、文件或出图接口，也不代替生产图纸审签。

通用脚本覆盖尚未封装的原生 API；当前已封装通用操作和小型 Phoenix 业务，高频建模/出图及文件流程继续分项提炼。业务 token 的快照绑定不等于通用文档修订令牌，不能将普通读取摘要作为任意写入的并发保护。

大型 Tool 的下载、编译和启动尚未实现。未来经独立适配层调用正式公共 TS 包或 HTTP 接口，先探测已有实例，服务位置、版本和进程生命周期单独管理。小型正式规则已保留来源声明，见 [NOTICE](../NOTICE)。

## 迭代与验收

每批定义一个可演示闭环，写清读取、相机、模型和文件副作用；先做相关单元/契约测试，再经真实 MCP 检查目标结果。建模核对几何与可编辑性，图纸检查渲染和副本重开，未执行项目保留待验。最终测试数、隐私和包清单确认后再做本地归档，远程发布另行安排。当前事实见 [验证记录](VERIFICATION.md)，后续范围见 [能力计划](CAPABILITY-PLAN.md)。
