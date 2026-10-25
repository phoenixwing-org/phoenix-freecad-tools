# 聊天驱动零件工具的原生确认链

改名、Link名称同步、BOM属性初始化/推荐/填写复用已有工作台Model/Controller。新增链路让内嵌聊天生成完整差异，再交给FreeCAD原生窗口由人工决定。有限`set_pad_length`另有精确能力门禁、typed快照及重算反验，见[Pad编辑合同](PAD-LENGTH-EDIT.md)。所有操作均不自动保存；此窗口不是桌面已有聊天的镜像。

## 调用顺序

1. `prepare_part_changes`明确活动文档和完整对象内部Name，返回所有字段的旧/新值、冲突、来源和短期token。不修改CAD；完整预览超过128KiB整批拒绝。
2. 将完整预览展示给用户。仅当can_apply且有变更时，用同一token调用`request_part_change_approval`。请求在建立或复用非模态窗口后返回，不等待人工操作，窗口展示同一份完整预览；返回awaiting_confirmation不表示已授权或写入。
3. 用户在FreeCAD窗口点击“确认修改”。服务的私有GUI回调重新核对文档实例、对象/来源快照、可写性、编辑/事务和撤销状态后，才执行既有可撤销事务。模型无法通过confirmed、approved、grant等参数授予确认。
4. `get_part_change_status`查询原token的纯内存状态。它不进入GUI队列，不读取CAD、不打开窗口或执行写入。不要循环轮询；用户回应后再查询。

两个新方法都只接受token，额外字段拒绝。Node在派发前要求插件声明精确`part_approvals` v1能力及两个方法；旧插件不能降级为直接apply。基础完整模式25、聊天19工具；显式配置Desk后完整模式30项，聊天另行启用Desk数据后24项。聊天没有`apply_part_changes`、任意脚本、独立重算或文件写入工具；有限Pad编辑只能在原生人工确认后由Controller执行一次受限重算。

完整模式仍保留原有高级`apply_part_changes`与显式脚本，客户端负责其授权。Pad token在所有状态均拒绝直接apply；旧操作已请求原生确认的token也不能从直接apply入口确认；这不是抵抗同权限本机Python的安全沙箱。

## 状态与时限

| state | 含义 |
| --- | --- |
| prepared | 已生成预览，未请求原生确认 |
| awaiting_confirmation | 等待原生窗口的人工操作，尚未写入 |
| applying | 已进入应用过程，结果尚未确定 |
| applied | Controller已返回应用结果；不表示已保存 |
| cancelled | 已取消，不执行晚到确认 |
| failed | 未确认完成，仅报告固定error_code，先检查对象再决定下一步 |
| expired | 预览可确认期已结束，不能继续应用 |

预览有效期5分钟，自创建开始，不因请求确认、复用窗口或查询而延长。已开始的应用不会因到期而强制中断；终结后仅保留有界纯摘要，在32个计划容量内等待淘汰。未知/被淘汰token不能重新执行。成功状态的重复请求/查询也不重复写入。

状态含document、operation、token、state、changed_count、requires_confirmation:true、saves_document:false、storage:memory_only、native_confirmation、result和error_code。只有applied有result，只有failed有固定error_code；其余二者为null。native_confirmation仅由原生GUI确认回调设置；prepared、awaiting_confirmation、cancelled和expired必须为false，applying、failed、applied分别报告实际布尔值（完整模式直接apply可为false）。它与应用结果分开，不能从applied猜测确认来源。Node检查4KiB数据体预算、token绑定、全部字段和状态关系。Pad的applied结果还包含绑定Body/Pad/长度的`design_result`，单独列出重算计数和反验后的几何；计数不等于几何成功。Pad的applying/applied必须具有真实原生确认来源。

同一token请求只复用自己的窗口；最多一个等待确认的计划。默认取消，Esc/关闭取消。窗口用内存查询检查过期，不为了检查而读模型。停止MCP会取消尚未开始的原生确认并禁用新的原生确认请求，晚到回调不写入；服务下一次启动才恢复这条入口，已经applying的事务不被强行关闭。关闭聊天本身不代替审批窗口的取消；要撤销待确认请求，请关闭其窗口或停止对应MCP。

## 结果未知与隐私

超时/断连不证明请求未处理，先查原token；不换token重复潜在写入，不自动重试或保存。状态只报告有界元数据与固定错误码，不回显native异常或完整属性。预览的文档名、Label及属性值仍可能含用户内容，只在明确请求的流程中读取/传递，不写CAD、偏好、日志、Git或分发包作为记录。完成/取消后释放预览、原生对象及快照引用。

## 验收要求

独立fixture须覆盖未确认零写入、完整预览、默认取消、Esc/关闭/过期、同token复用、伪造参数、旧能力拒绝、过期/错误线程/迟到回调、外部apply不能绕过等待状态、确认前对象变化、一次应用及撤销、异常回滚和foreign事务保护。真实FreeCAD界面、人工点击、实际撤销重做和聊天工具事件仍须在明确授权的独立配置/人工文档中验收；当前尚未完成这条链的原生端到端验收，源码与注入测试不代替原生CAD验收。
