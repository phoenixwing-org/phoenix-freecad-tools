# Addon 身份统一迁移

版本说明：当前统一候选为0.3.2；本页0.4.0–0.10.0均为未发布内部迭代标签，保留当时代码、原生版本及归档事实，不能视为多个正式发行或本轮最终验收。详见[版本约定](VERSIONING.md)。

更新日期：2026-10-25。项目定位为通用 FreeCAD 工具，插件技术名称为 `PhoenixFreeCADTools`。仓库根目录仍为 `phoenix-freecad-tools`，工作台界面仍为 Phoenix Tools。此次迁移覆盖源码、安装、元数据、运行时和当前说明，不改写历史提交、标签或归档。

## 迁移矩阵

| 合同 | 旧值 | 新值/决定 | 验证 |
| --- | --- | --- | --- |
| Addon 源码/安装目录 | PhoenixFreeCADMCP | PhoenixFreeCADTools | 路径、安装/ZIP 顶层及引导测试 |
| 工作台类/注册 ID | PhoenixFreeCADMCPWorkbench | PftWorkbench | package.xml、引导注册、原生激活 |
| 桥接 addon_name / HTTP 标识 / 工作线程名 | PhoenixFreeCADMCP | PhoenixFreeCADTools | 实时 status、HTTP/线程源码 |
| 安装所有者 | phoenix-freecad-mcp | phoenix-freecad-tools | 精确标记和文件指纹校验 |
| 安装标记 | .phoenix-freecad-mcp-install.json | .phoenix-freecad-tools-install.json | 新安装、旧迁移、拒绝未知目录 |
| 备份根目录 | .phoenix-freecad-mcp | .phoenix-freecad-tools | 新备份位于 Mod 外；旧备份不搬动 |
| npm 包 / CLI / MCP server.name | phoenix-freecad-mcp | 保留，专指 Node MCP 服务 | Node 元数据和启动合同 |
| GUI 名称与命令 | Phoenix Tools / PhoenixTools_ | 保持 | 用户入口不变 |
| Python 自有模块 | phoenix_freecad_* / phoenix_workbench_* | pft_* / pft_workbench_* | 导入和文件名回归 |
| 环境变量 / 协议 | PHOENIX_FREECAD_URL 等 / v1 | 保持 | 配置和协议回归 |

## 内部前缀

Python 自有模块迁为 pft_bridge、pft_operations、pft_runtime、pft_business、pft_document_state 及 pft_workbench_*；测试文件为 test_pft_*。自有类用 Pft 前缀，FreeCAD 入口 Init.py / InitGui.py 保留。TypeScript 适配器采用 pft-freecad-client.ts、PftFreeCadClient / PftBridgeError；标准 CLI 入口和 Node 服务包名保持。

模块导入、按文件名加载、打包白名单、测试路径和当前使用说明同时更新。不为旧 Python 模块注册运行别名；历史证据中的旧模块名表达当时事实。

## 旧名称的限定保留

安装器仅识别已经由本项目管理的 `PhoenixFreeCADMCP` 和更早的 `PhoenixMCP`；必须核对旧 owner、旧标记和完整内容，再将整个旧目录备份到 Mod 外并安装新目录。新旧或两个旧目录并存时拒绝自动选择；未知、用户修改、附加文件或符号链接均受保护。

不注册旧工作台运行别名，不同时保留两份加载目录。历史验证文档、机器回执和旧包保留原名以表达当时事实。Node 服务名中的 mcp 描述其功能，不是过期的工作台产品名。

## 验收范围

改名后须重新构建并扫描当前源码、安装包、工作台注册和消费者。磁盘改名不会替换内存模块；实际会话需在请求空闲时受控卸载旧注册、从新位置加载并核对身份，或保存工作后正常重启。不得为改名重启未保存会话、清除未知目录或改写历史证据。

迁移验收需分别核对安装目录、运行时身份、按钮回调及业务窗口。FreeCAD 原生命令对象可能保留原注册回调，不能把临时重绑视为产品自动热更新机制；常规更新以重新加载后的运行时核验或正常重启为准。独立fixture的验收范围见 [验证记录](VERIFICATION.md)。
