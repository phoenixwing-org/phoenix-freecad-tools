# SPDX-License-Identifier: Apache-2.0
"""Explicit, small shared-connection settings view; no CAD or service operations."""
import pft_mcp_settings as settings
from pft_workbench_i18n import tr


def show_mcp_settings_dialog(gui, bridge_snapshot=None, *, options=None):
    # Imports are lazy so importing the addon has no Qt/file/process effects.
    from pft_workbench_views import _qt, _plain_label, _shrink_horizontal, _dialog_exec, _report
    options = {} if options is None else dict(options)
    try:
        record = settings.read_config(**options)
        core, widgets = _qt()
        dialog = widgets.QDialog(gui.getMainWindow())
        dialog.setObjectName("PhoenixTools_McpSettingsDialog")
        dialog.setWindowTitle(tr("Phoenix：MCP 连接设置"))
        dialog.resize(500, 320)
        layout = widgets.QVBoxLayout(dialog)
        layout.addWidget(_plain_label(widgets, core, tr("当前用户共享的 MCP 连接配置。")))
        layout.addWidget(_plain_label(widgets, core, tr("配置文件")))
        location = _shrink_horizontal(widgets.QLineEdit())
        location.setObjectName("PhoenixTools_McpConfigPath")
        location.setReadOnly(True)
        location.setText(str(record["config_path"]))
        layout.addWidget(location)
        # A shrinkable QLabel beside a spin box and stretch can receive zero
        # width from QHBoxLayout. Keep the field label on its own wrapped row.
        layout.addWidget(_plain_label(widgets, core, tr("首选桥接端口")))
        row = widgets.QHBoxLayout()
        port = widgets.QSpinBox()
        port.setObjectName("PhoenixTools_McpPreferredPort")
        port.setRange(1024, 65535)
        port.setValue(record["settings"]["bridge"]["preferred_port"])
        row.addWidget(port)
        row.addStretch(1)
        layout.addLayout(row)
        discovery = widgets.QCheckBox(tr("自动发现本机 FreeCAD 实例"))
        discovery.setObjectName("PhoenixTools_McpInstanceDiscovery")
        discovery.setChecked(record["settings"].get("discovery", {}).get("enabled", False))
        layout.addWidget(discovery)
        layout.addWidget(_plain_label(widgets, core, tr("启用后普通桥接使用动态端口；首选端口仍用于直接连接。")))
        source = tr("已保存的共享配置") if record["source"] == "user_config" else tr("缺省配置，尚未创建文件")
        layout.addWidget(_plain_label(widgets, core, tr("配置来源：%s") % source))
        snapshot = bridge_snapshot if type(bridge_snapshot) is dict else {}
        if snapshot.get("running") is True:
            layout.addWidget(_plain_label(widgets, core, tr("当前运行端口：%s") % snapshot.get("port", tr("未知"))))
        if snapshot.get("listener_configuration", {}).get("source") == "development":
            layout.addWidget(_plain_label(widgets, core, tr("当前开发实例保留独立端口，不采用共享端口。")))
        layout.addWidget(_plain_label(widgets, core, tr("保存只影响下次启动桥接和下次连接的 Node MCP；当前服务及已连接聊天不会自动切换。显式地址配置继续优先。")))
        buttons = widgets.QDialogButtonBox(widgets.QDialogButtonBox.Ok | widgets.QDialogButtonBox.Cancel)
        save = buttons.button(widgets.QDialogButtonBox.Ok)
        save.setText(tr("保存连接设置"))
        save.setAutoDefault(False)
        save.setDefault(False)
        cancel = buttons.button(widgets.QDialogButtonBox.Cancel)
        cancel.setText(tr("取消"))
        cancel.setAutoDefault(True)
        cancel.setDefault(True)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if _dialog_exec(dialog) != widgets.QDialog.Accepted:
            return None
        enabled = discovery.isChecked()
        version = 2 if record["settings"]["schema_version"] == 2 or enabled else 1
        selected = {"schema_version": version, "bridge": {"preferred_port": port.value()}}
        if version == 2:
            selected["discovery"] = {"enabled": enabled}
        result = settings.save_config(selected, expected_etag=record["etag"], **options)
        _report(gui, tr("共享连接配置已保存；未启停服务。现有 Node MCP 需重新连接后采用新配置。"))
        return result
    except settings.PftMcpSettingsError as error:
        if error.may_have_saved:
            message = tr("连接配置写入后的持久状态无法确认；请重新读取检查，不要直接重复保存。")
        else:
            message = tr("连接设置未保存（%s）。") % error.code
        _report(gui, message, error=True)
        return None
    except Exception as error:
        _report(gui, tr("连接设置操作失败（%s）；请重新读取配置检查结果。") % type(error).__name__, error=True)
        return None
