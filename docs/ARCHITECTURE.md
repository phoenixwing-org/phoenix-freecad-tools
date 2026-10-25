# 0.2.1 架构

Phoenix FreeCAD Tools 同时提供独立工作台按钮与聊天工具。Node.js / TypeScript 负责 MCP，Python 负责 FreeCAD 原生执行；普通工作台功能不依赖 MCP 或大型业务服务。

```text
聊天客户端 → MCP stdio → Node 工具/schema → 本机 JSON HTTP
                                             ↓
                                   Python bridge / Qt 调度
                                      ↓              ↓
                               固定能力 operations   高级 runtime
                                      ↓              ↓
                                         FreeCAD GUI

工作台按钮 → View → Controller → Model / FreeCAD 事务
                  Facade 组装与稳定入口
```

## 工作台 MVC

| 层 | 文件 | 职责 |
| --- | --- | --- |
| Model | `phoenix_workbench_model.py` | 正式 BOM 字段、命名规则、快照和纯校验；不导入 Qt 或 FreeCAD |
| Controller | `phoenix_workbench_controller.py` | 选择和编辑状态、预览后变化校验、确认后的事务与结果核对；注入 App/Gui，不创建窗口 |
| View | `phoenix_workbench_views.py` | 当前中文弹窗、预填、差异和确认；经 Controller 写入，不直接改模型 |
| Facade | `phoenix_workbench_tools.py` | 组装依赖，提供普通工具的稳定入口 |
| GUI | `phoenix_workbench_gui.py` | 注册 Phoenix Tools、三个普通工具和一个 MCP 启停命令 |
| 引导 | `InitGui.py` | 在普通模块命名空间加载 GUI，兼容 FreeCAD 分离 globals/locals 的入口执行方式 |

普通按钮只处理明确选择的对象，修改前重新检查上下文和快照；写入使用命名事务，不默认保存。Link 属性转发、Label 表达式依赖及冲突均需明确处理，不能只因 API 未抛异常就报告写入符合预览。当前按钮与原生 MCP 读取分别实现各自职责，普通业务按钮尚未注册成额外 MCP 工具；将来提供聊天入口时应复用现有 Model/Controller，而非复制规则。

工作台显示名为 **Phoenix Tools**。`PhoenixTools_ToggleMCP` 是唯一启停命令，按实际状态显示绿色三角启动、红色方块停止和状态文字。刷新只影响本命令的 action 与按钮，不改变其他工具栏的全局显示方式。选中工作台不自动启动服务，中英文切换仍为 TODO。

## MCP 与原生执行

| 模块 | 职责 |
| --- | --- |
| `src/server.ts` | 注册当前 16 个工具、严格 schema、标题和副作用声明 |
| `src/adapters/freecad.ts` | 本机 HTTP、参数映射、`supported_methods` 检查、响应与 PNG 边界 |
| `phoenix_freecad_bridge.py` | 协议 v1 白名单、请求校验、状态、Qt GUI 调度和手动启停 |
| `phoenix_freecad_operations.py` | 有限量的上下文、选择、几何、图纸读取和标准相机操作 |
| `phoenix_freecad_runtime.py` | 显式脚本、会话内去重/状态、有限量结果和三维视图捕获 |

Node 与客户端走 MCP stdio；Node 与插件走本机 JSON HTTP。两种协议不同，端口不提供 Streamable HTTP MCP。离线清单不代表运行时能力，新工具必须由插件明确声明；完整接口见 [协议](PROTOCOL.md)。

常用读取不重算、不保存或修改模型；相机接口改变视角。单面体积不适用，返回 `null`；包围盒仅代表内核包络，不能作精确间隙证据。截图只导出当前三维视图，临时文件读取后清理。显式脚本在 GUI 主线程执行，与 Python 控制台同权限，不是沙箱，不保证异常回滚。脚本状态查询不进 GUI 队列；请求记录属于桥接服务会话，不能跨模块重载或 FreeCAD 重启保证去重。

## 当前实现与后续封装

参数化板、受控 STEP 往返及已知参数重建已用显式脚本实测。TechDraw 四视图、6 mm 关联尺寸、文件导出和独立重开已通过，最终 PDF 渲染目视确认布局分离、无视图遮挡和越界。这些公开案例证明对应流程可行，不等于已经提供通用结构化建模、文件或出图接口，也不代替生产图纸审签。

通用脚本覆盖尚未封装的原生 API；高频且合同明确的流程再提炼为有类型工具。后续结构化写入需增加目标状态校验、事务、超时结果查询和文件规则，不能将当前读取摘要当成写入并发令牌。

大型 Tool 的下载、编译和启动尚未实现。未来经独立适配层调用正式公共 TS 包或 HTTP 接口，先探测已有实例，服务位置、版本和进程生命周期单独管理。小型正式规则已保留来源声明，见 [NOTICE](../NOTICE)。

## 迭代与验收

每批定义一个可演示闭环，写清读取、相机、模型和文件副作用；先做相关单元/契约测试，再经真实 MCP 检查目标结果。建模核对几何与可编辑性，图纸检查渲染和副本重开，未执行项目保留待验。最终测试数、隐私和包清单确认后再做本地归档，远程发布另行安排。当前事实见 [验证记录](VERIFICATION.md)，后续范围见 [能力计划](CAPABILITY-PLAN.md)。
