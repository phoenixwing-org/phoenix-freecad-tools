# Wing 与 FreeCAD 工具的复用边界

更新日期：2026-10-25。先核对 Wing 已有的算法、契约和界面，再增加本项目适配；通用核心按实际消费者需求进入 Wing，避免复制成另一套规则。

| 层 | 已核实的现有能力 | 本项目使用方式 |
| --- | --- | --- |
| @phoenix-wing/cad-core | BOM 文件名/标签规则、文档类型、相对路径；Document.xml/XLink 解析、引用解析状态、入向引用和装配树 ViewModel；工作区索引候选 | Node 文件/批量能力优先消费正式导出，不重新实现；纯计算与文件/FreeCAD 宿主操作分层 |
| @phoenix-wing/cad-contracts | 版本化 CAD/native/provider/query/workspace-index 契约 | 接口确实匹配时复用，旧磁盘/native 契约不冒充当前 GUI 会话状态 |
| Wing 工作台 UI | PnwWorkbenchShell/Layout、Header、TabBar、Home、设置和相关 Vue 组合能力 | Desk Web 继续复用这些能力；FreeCAD 原生 Qt 按钮不直接运行 Vue 组件，复杂页面通过 Desk 浏览器访问 |
| 本项目 | 当前 GUI 文档实例、事件修订诊断、Qt 主线程操作、事务及 FreeCAD 对象类型 | 保留在 Pft 宿主适配层；不将 FreeCAD/Qt 依赖移入 Wing 的纯 TypeScript core |

源码导出存在不等于已经发布；cad-core 的工作区索引 README 明确标注为本地候选，采用前核对实际 tarball。当前轻量 Python BOM 规则已有独立移植和 NOTICE；未来 Node 对应规则改为正式公共包复用时需做同输入对照与许可核对。

本轮不为一次 FreeCAD 专属读取创建新的公共库。后续有跨宿主需求的纯 DTO、预算/协议校验或规则，应先核对 Wing 现有模块，再连同真实消费者、契约测试和发布边界提升；Wing 公共名称继续使用 Pnw/pnw/PNW_，本插件类使用 Pft。

消费者按锁定 Registry 版本正常安装；本地 Wing 调试遵循 Wing 的正式方法，不写个人 file:/link: 依赖，不修改 node_modules 或发布矩阵。本轮只读核对 Wing，未改变其源码或版本。
