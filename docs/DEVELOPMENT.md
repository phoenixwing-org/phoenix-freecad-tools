# 独立开发 FreeCAD

在仓库根目录使用 Node.js 22+：

```bash
npm run addon:dev
```

Git开发工作树统一放在 `phoenix/worktrees/<仓库名>-working`；本仓使用 `phoenix/worktrees/phoenix-freecad-tools-working`。这与下述运行profile是两个目录，不把运行配置或登录数据放入Git工作树。

默认使用 macOS `/Applications/FreeCAD.app`；启动器以独立参数数组启动现有应用，不下载或编译 FreeCAD。`--app` 可指定其他已安装的应用或可执行文件，`--profile` 可指定专属开发目录，`--port` 可指定本机开发端口，`--purpose` 标注本次实例用途：

```bash
node scripts/dev-freecad.mjs --prepare
node scripts/dev-freecad.mjs --app /Applications/FreeCAD.app --port 9877 --purpose "通用测试"
```

`--prepare` 只准备和校验开发配置，不启动进程。默认目录为当前用户 `~/Library/Application Support/PhoenixFreeCADTools/Development`，专属目录使用0700权限，入口通过符号链接加载当前仓库的 `addon/PhoenixFreeCADTools`。已有不匹配的目录、标记、宏或入口不会被覆盖。启动器保留专属配置，不复制工作实例的配置、最近文档或聊天登录数据，也不调用常规安装器。

开发窗口标题为 `Phoenix <实际插件版本> 开发｜<用途>｜<端口> — <FreeCAD原生标题>`；版本来自实际加载的插件，用途默认“通用”，原生文档名称和修改星号保持显示但不写入就绪回执。工作台为 `Phoenix Tools`；启动后不打开CAD、不自动启动MCP。需要连接时在该窗口点击启动按钮，默认监听 `127.0.0.1:9877`。普通安装默认端口9876。

开发实例使用自己的 user/system配置；FreeCAD原生窗口设置通过专属vendor/application命名域隔离。Qt默认INI设置重定向至开发配置目录。macOS原生设置可能保存在系统Preferences下的专属开发命名域，不能把开发profile视为所有Qt写入的完整沙箱。

开发启动器保留父进程明确提供的HTTP/HTTPS/ALL/NO_PROXY及小写形式，只传给本次子进程；代理值不写入启动清单或输出，也不修改全局环境。没有显式值时不制造默认值，内置聊天按[系统代理策略](CHAT-USAGE.md#聊天连接与系统代理后续修正尚未发布)选择官方解析。此项为后续源码修正，正式0.3.2包尚不包含。

## 热重载

在开发实例的Phoenix菜单或“Phoenix 开发”工具条点击“重载开发界面”，再打开相应面板。该入口只出现在显式开发模式。

固定可重载范围是共享小工具条、面板宿主、当前文档、缩略图、外观、聊天显示、设置和Desk界面模块。只接受同一源码目录的模块。重载会关闭空闲文档面板和已断开的聊天面板，不会主动中止连接、编辑、外观预览或待确认修改。

有事务、编辑、可用的外观预览、业务计划、运行中的工具请求、连接中的聊天或打开的对话框时会拒绝重载；先在界面自行完成或取消相关操作。桥接、协议、控制器、事务、依赖和开发启动参数变更须重启开发实例，不能用界面重载替代。

启动器发现该profile已有存活进程时拒绝重复启动，不终止它。异常遗留启动锁会明确报错，需要先确认所属开发进程状态；启动器不自动删除未知登记或接管其他实例。此环境仅用于可信本机开发，MCP不是权限沙箱。
