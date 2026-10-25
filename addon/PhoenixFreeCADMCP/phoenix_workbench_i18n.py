# SPDX-License-Identifier: Apache-2.0
"""Pure interface translations; preferences and GUI refresh live elsewhere.

Chinese source text is the message ID. Translate before applying %-formatting.
Unknown messages remain untouched, including native/domain error descriptions.
"""

LANGUAGES = ("zh", "en")
_language = "zh"

ENGLISH = {
    "设置 / Settings": "Settings / 设置",
    "Phoenix：设置": "Phoenix: Settings",
    "界面语言": "Interface language",
    "简体中文": "简体中文",
    "English": "English",
    "配置 Phoenix Tools 的界面语言。": "Choose the Phoenix Tools interface language.",
    "语言设置仅影响 Phoenix Tools 界面；业务校验和原生错误可能仍使用原文，不改变模型、MCP 状态或机器标识。": "This setting changes only the Phoenix Tools interface. Business validation and native errors may retain their original text. Models, MCP state and technical identifiers stay unchanged.",
    "保存并应用": "Save and apply",
    "语言设置已更新。": "Interface language updated.",
    "无法读取语言设置，已使用简体中文。": "Could not read the language preference; Simplified Chinese is in use.",
    "语言设置保存失败；尚未切换界面语言。": "Could not save the language preference; the interface language has not changed.",
    "不支持的界面语言。": "Unsupported interface language.",
    "设置操作失败（%s）。": "Settings failed (%s).",
    "MCP 操作失败（%s）。": "MCP operation failed (%s).",
    "MCP 服务已停止。": "MCP service stopped.",
    "MCP 服务已启动：127.0.0.1:%s。": "MCP service started: 127.0.0.1:%s.",
    "MCP 服务正在停止。": "MCP service is stopping.",
    "MCP 正在停止": "MCP stopping",
    "Phoenix 的 FreeCAD 工具入口；通过 MCP 提供聊天控制能力。": "Phoenix tools for FreeCAD, with chat control through MCP.",
    "停止 MCP · 运行中": "Stop MCP · Running",
    "同步链接名称": "Sync link labels",
    "启动 MCP · 已停止": "Start MCP · Stopped",
    "命名修正": "Normalize labels",
    "填写零件属性": "Edit part properties",
    "当前 MCP 已停止；点击启动本机服务（127.0.0.1:9876）。": "MCP is stopped. Click to start the local service (127.0.0.1:9876).",
    "当前 MCP 正在后台停止；完成后可再次启动。": "MCP is stopping in the background. Wait before starting it again.",
    "当前 MCP 运行中；点击停止本机 Phoenix MCP 服务。": "MCP is running. Click to stop the local Phoenix MCP service.",
    "操作失败（%s）。": "Operation failed (%s).",
    "编辑所选对象的五个 BOM 字符串字段，确认后写入。": "Edit the five BOM text fields of selected objects and confirm before writing.",
    "预览所选 App::Link 与直接来源的名称差异，确认后仅修改链接实例 Label。": "Preview selected App::Link labels against their direct sources, then change only the instance labels.",
    "预览所选对象的 Label 命名修正，确认后可撤销。": "Preview label corrections for selected objects. Confirmed changes can be undone.",
    "<缺失字段>": "<missing field>",
    "Phoenix：同步链接名称预览": "Phoenix: Preview link labels",
    "Phoenix：命名修正预览": "Phoenix: Preview label corrections",
    "Phoenix：填写零件属性": "Phoenix: Edit part properties",
    "Phoenix：确认零件属性差异": "Phoenix: Confirm property changes",
    "仅准备创建缺失的五个正式字符串字段，默认空值；现有属性保持原值。": "Stage missing BOM text fields with empty values. Keep existing properties unchanged.",
    "从当前 Label 推荐": "Suggest from current label",
    "保留现值": "Keep current value",
    "修改": "Change",
    "关闭": "Close",
    "内部 Name": "Internal Name",
    "创建字段": "Create field",
    "初始化缺失字段": "Initialize missing fields",
    "勾选要写入的字段并编辑值。初始化只补缺失字段；Label 推荐仅填空值并保留现有内容。写入前会逐项展示差异。": "Select the fields to write and edit their values. Initialization adds only missing fields; label suggestions fill only empty values. Review every change before writing.",
    "原 Label": "Previous Label",
    "取消": "Cancel",
    "只接受明确完整的编号.版本-名称；仅补空值，不推测类型码和型号。": "Accept only an explicit number.version-name label. Fill empty values without guessing a type code or model series.",
    "可修改": "Ready to change",
    "字段均已存在，保留现值": "All fields exist; keep their current values",
    "字段缺失；勾选后将在 CustomDefine 组中创建字符串属性。": "Missing field. Select it to create a text property in CustomDefine.",
    "将写入 %s 个属性值；请核对现有值与待写入值。": "%s property values will be written. Review the current and proposed values.",
    "将所选 App::Link 实例的 Label 同步为直接来源的 Label。同一来源的实例允许同名；源对象保持原值。确认后可撤销。": "Copy direct-source labels to the selected App::Link instances. Instances sharing a source may share its label. Source objects stay unchanged; confirmed changes can be undone.",
    "属性": "Property",
    "属性已一致，无需写入。": "Properties already match; nothing to write.",
    "已一致，无需修改": "Already matches",
    "已修改 %s 个所选对象的 Label。可按撤销恢复；尚未保存文件。": "Changed the labels of %s selected objects. You can undo the changes; the file has not been saved.",
    "已写入 %s 个属性值。可按撤销恢复；尚未保存文件。": "Wrote %s property values. You can undo the changes; the file has not been saved.",
    "当前 Label": "Current Label",
    "待写入值": "Proposed value",
    "待初始化为空：": "Will initialize as empty: ",
    "待编辑": "Ready to edit",
    "推荐 / 检查": "Suggestion / Check",
    "操作": "Action",
    "操作失败（%s）；未提交的工具事务已取消。": "Operation failed (%s); uncommitted tool changes were cancelled.",
    "新 Label": "Proposed Label",
    "检查结果": "Check result",
    "现有值": "Current value",
    "直接来源引用": "Direct source reference",
    "确认修改（可撤销）": "Confirm changes (undoable)",
    "确认写入（可撤销）": "Confirm write (undoable)",
    "规则：零件号.零件版本-零件名称。仅修改所选对象的 Label。确认后可撤销。": "Format: part number.version-part name. Change only the selected objects' labels. Confirmed changes can be undone.",
    "预览写入差异": "Preview changes",
    "零件号": "Part number",
    "零件版本": "Part version",
    "类型码": "Type code",
    "型号系列": "Model series",
    "零件名称": "Part name",
    "、": ", ",
    # Reusable entry point for unchanged domain controllers. Dynamic messages
    # that were formatted before reaching the view intentionally stay intact.
    "请先打开文档，并在树中选中要处理的对象。": "Open a document and select the objects to process in the tree.",
    "请先在树中选中对象；本工具不会自动处理整个文档。": "Select objects in the tree first. This tool does not process the whole document automatically.",
    "请只选择当前活动文档中的对象。": "Select only objects in the active document.",
    "请选择树中的完整对象，不要选择面、边或顶点。": "Select complete objects in the tree, rather than faces, edges or vertices.",
    "当前文档正在编辑，请先完成或取消编辑。": "Finish or cancel the current document edit first.",
    "活动文档已变化，请重新选择并打开工具。": "The active document changed. Select the target again and reopen the tool.",
    "请至少勾选一个支持的字段。": "Select at least one supported field.",
}


def get_language():
    return _language


def set_language(language):
    global _language
    if language not in LANGUAGES:
        raise ValueError("Unsupported interface language")
    _language = language


def tr(source):
    return ENGLISH.get(source, source) if _language == "en" else source
