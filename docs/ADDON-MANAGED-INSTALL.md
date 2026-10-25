# Phoenix Tools — npm 受管理安装

本说明随 Node 包安装器放入 `PhoenixFreeCADTools/README.md`，对应带 `.phoenix-freecad-tools-install.json` 标记的安装。普通工作台工具在 FreeCAD 内运行；Node 服务与聊天客户端另行配置。版本见同目录 `package.xml`，本说明不表示正式 Release 或跨平台原生验收已完成。

## 安装与升级

先在目标 FreeCAD 的 Python 控制台核实用户 Mod 目录：

```python
import os
import FreeCAD as App
print(os.path.join(App.getUserAppDataDir(), "Mod"))
```

安装文件前，等待桥接请求结束，保存工作并正常退出 FreeCAD。不要强制关闭未保存的会话。以下命令在取得的 Node 包或源码包根目录执行；用已核实的目录替换占位值：

```sh
node scripts/install-addon.mjs --mod-dir "/absolute/path/to/FreeCAD/Mod" --dry-run
node scripts/install-addon.mjs --mod-dir "/absolute/path/to/FreeCAD/Mod"
```

安装器只管理其标记、目录及文件指纹全部一致的安装。相同内容重装不改动目录；更新和旧名称迁移保留 Mod 外的完整备份。未知目录、符号链接、多个安装身份或用户修改会阻止替换。不要伪造标记或删除文件来绕过校验。

默认分发来源会一并安装本 Addon 的 `LICENSE`、`NOTICE` 和本说明，三者纳入同一指纹清单。Node 包根的 NOTICE 涵盖不同分发内容，不作为纯 Python Addon 的声明。法律材料或说明缺失、为空、超限、冲突或含符号链接时，安装在创建目标与备份目录前停止。

安装后正常启动 FreeCAD，选择 **Phoenix Tools**。复制文件不会替换当前进程中已加载的 Python 模块。安装器不重启 FreeCAD、不修改或保存 CAD，也不启动 Desk、聊天或 MCP 后台。

## 卸载与恢复

保存工作并正常退出 FreeCAD后，在原 Node 包或源码包根目录执行：

```sh
node scripts/uninstall-addon.mjs --mod-dir "/absolute/path/to/FreeCAD/Mod" --dry-run
node scripts/uninstall-addon.mjs --mod-dir "/absolute/path/to/FreeCAD/Mod"
```

卸载会把核验通过的完整插件移到 Mod 外，保留恢复副本；不会删除 CAD、Desk 数据、聊天配置或 FreeCAD 偏好。修改过的 `LICENSE`、`NOTICE`、`README.md` 也会触发文件保护。

恢复前退出 FreeCAD，核对备份完整且 Mod 内没有新旧插件并存，再将整份备份恢复到原位置。不要向不同版本目录合并文件。

## 与手工 ZIP 的区别

独立 Addon ZIP 不带安装器所有权标记，按其手工说明安装；不要把本安装的标记复制给手工目录。切换安装方式时，先将旧整目录移到 Mod 外保留，再执行所选方式的全新安装。

Node tgz 是预构建 MCP 运行包，不是完整源码构建输入；重建 TypeScript 使用包含 lockfile 和 tsconfig 的源码包。指向其他来源目录的显式 `sourceDir` 开发接口只复制调用方指定来源，不拼接本包法律材料；调用方负责其来源、许可及自足说明。

源码与使用说明：[Phoenix FreeCAD Tools](https://github.com/phoenixwing-org/phoenix-freecad-tools)。许可见同目录 `LICENSE`，归属声明见 `NOTICE`。FreeCAD 与 Node.js 不随插件目录分发。
