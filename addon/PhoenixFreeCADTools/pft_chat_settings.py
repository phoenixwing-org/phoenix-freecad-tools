# SPDX-License-Identifier: Apache-2.0
"""Local chat preferences and explicit MCP configuration for the chat client."""
import json
import os
from pathlib import Path
import unicodedata

from pft_preferences import PftPreferenceError, save_parameter_changes

PREFERENCE_PATH = "User parameter:BaseApp/Preferences/Mod/PhoenixTools/Chat"
_KEYS = {"enabled": ("Bool", "EnableFreeCADTools", False),
         "desk_enabled": ("Bool", "EnableDeskDataTools", False),
         "node_executable": ("String", "NodeExecutable", ""),
         "cli_entry": ("String", "MCPCLIEntry", "")}
_CHAT_PREFERENCE_KEYS = {"model_id": ("String", "ModelId", ""),
                         "font_point_size": ("Int", "FontPointSize", 0)}


class PftChatSettingsError(ValueError):
    pass


def _absolute_file(value, executable=False):
    if (not isinstance(value, str) or not value or len(value) > 4096 or value != value.strip()
            or not os.path.isabs(value)
            or any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value)):
        raise PftChatSettingsError("MCP 配置必须使用有效的绝对路径。")
    target = Path(os.path.realpath(value))
    if not target.is_file() or (executable and not os.access(target, os.X_OK)):
        raise PftChatSettingsError("Node 或 MCP CLI 文件不存在，或无法使用。")
    return str(target)


def validate_mcp_paths(node_executable, cli_entry):
    node = _absolute_file(node_executable, executable=True)
    cli = _absolute_file(cli_entry)
    # Only a built Phoenix MCP package is accepted. No download/build is run.
    package = Path(cli).parent.parent / "package.json"
    try:
        if (Path(cli).name != "cli.js" or Path(cli).parent.name != "dist"
                or not package.is_file() or package.stat().st_size > 65536):
            raise ValueError()
        metadata = json.loads(package.read_text(encoding="utf-8"))
        if metadata.get("name") != "phoenix-freecad-mcp":
            raise ValueError()
    except (OSError, ValueError, TypeError):
        raise PftChatSettingsError("请选择已构建 Phoenix MCP 包中的 dist/cli.js。") from None
    return {"node_executable": node, "cli_entry": cli}


def load_chat_options(app):
    try:
        preference = app.ParamGet(PREFERENCE_PATH)
        value = {name: getattr(preference, "Get" + kind)(key, default)
                 for name, (kind, key, default) in _KEYS.items()}
        if any(type(value[key]) is not bool for key in ("enabled", "desk_enabled")) or any(not isinstance(value[key], str) for key in ("node_executable", "cli_entry")):
            raise ValueError()
        if not value["enabled"]:
            value["desk_enabled"] = False
        return value
    except Exception:
        return {name: default for name, (_, _, default) in _KEYS.items()}


def _save_chat_values(app, keys, values):
    changes = tuple((kind, key, default, values[name])
                    for name, (kind, key, default) in keys.items())
    try:
        save_parameter_changes(app, PREFERENCE_PATH, changes)
    except PftPreferenceError as error:
        messages = {
            "PREFERENCE_WRITE_FAILED": "聊天设置保存失败，原配置已恢复并保存。",
            "PREFERENCE_PERSIST_UNAVAILABLE": "当前 FreeCAD 无法持久保存聊天设置。",
            "PREFERENCE_SNAPSHOT_UNAVAILABLE": "无法读取原聊天设置，未修改配置。",
            "PREFERENCE_ROLLBACK_UNVERIFIED": "聊天设置保存失败，部分原配置无法恢复。",
            "PREFERENCE_PERSIST_RESTORE_UNVERIFIED": "聊天设置保存失败，原配置恢复后的落盘无法确认。",
            "PREFERENCE_PERSIST_FAILED": "聊天设置未能持久保存，原配置已恢复并保存。",
        }
        raise PftChatSettingsError(messages.get(error.code, "聊天设置保存失败，请检查原配置与持久保存状态。")) from None
    return values


def save_chat_options(app, enabled, node_executable, cli_entry, desk_enabled=False):
    if type(enabled) is not bool or type(desk_enabled) is not bool:
        raise PftChatSettingsError("MCP 开关必须使用布尔值。")
    if desk_enabled and not enabled:
        raise PftChatSettingsError("启用 Desk 数据工具前，请先启用 FreeCAD 工具。")
    paths = validate_mcp_paths(node_executable, cli_entry) if enabled else {
        "node_executable": node_executable, "cli_entry": cli_entry}
    if any(not isinstance(value, str) or len(value) > 4096 for value in paths.values()):
        raise PftChatSettingsError("MCP 配置必须使用有效的绝对路径。")
    return _save_chat_values(app, _KEYS, {"enabled": enabled, "desk_enabled": desk_enabled, **paths})


def _validate_model_id(value):
    if (not isinstance(value, str) or len(value) > 256 or value != value.strip()
            or any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value)):
        raise PftChatSettingsError("模型 ID 必须为空或至多 256 个字符，且不能包含控制字符或首尾空白。")
    return str(value)


def _validate_font_point_size(value):
    if type(value) is not int or (value != 0 and not 8 <= value <= 32):
        raise PftChatSettingsError("聊天字号必须为 0（继承宿主字体）或 8 至 32 的整数。")
    return value


_CHAT_PREFERENCE_VALIDATORS = {"model_id": _validate_model_id,
                              "font_point_size": _validate_font_point_size}


def load_chat_preferences(app):
    """Read independent preferences; empty model and zero font inherit defaults."""
    values = {name: default for name, (_, _, default) in _CHAT_PREFERENCE_KEYS.items()}
    try:
        preference = app.ParamGet(PREFERENCE_PATH)
    except Exception:
        return values
    for name, (kind, key, default) in _CHAT_PREFERENCE_KEYS.items():
        try:
            values[name] = _CHAT_PREFERENCE_VALIDATORS[name](
                getattr(preference, "Get" + kind)(key, default))
        except Exception:
            # A damaged or unreadable field must not discard the other one.
            pass
    return values


def save_chat_preferences(app, model_id, font_point_size):
    """Persist validated choices through the native user-parameter save API."""
    values = {"model_id": _validate_model_id(model_id),
              "font_point_size": _validate_font_point_size(font_point_size)}
    return _save_chat_values(app, _CHAT_PREFERENCE_KEYS, values)


def load_chat_desk_configuration(app):
    """Explicit enable/connect only: read three existing paths, never probe Desk."""
    try:
        from pft_desk_controller import load_desk_configuration
        from pft_desk_service import validate_configuration
        return validate_configuration(load_desk_configuration(app))
    except Exception:
        raise PftChatSettingsError("请先在 Desk 连接设置中配置有效的 Node、CLI 和私有状态目录。") from None


def build_chat_mcp_config(options, bridge_status, desk_configuration=None):
    """Resolve only explicit paths and a running local bridge; never start it."""
    desk_enabled = options.get("desk_enabled", False)
    if type(desk_enabled) is not bool or (desk_enabled and options.get("enabled") is not True):
        raise PftChatSettingsError("启用 Desk 数据工具前，请先启用 FreeCAD 工具。")
    if options.get("enabled") is not True:
        return None
    paths = validate_mcp_paths(options.get("node_executable"), options.get("cli_entry"))
    if not isinstance(bridge_status, dict) or bridge_status.get("running") is not True or bridge_status.get("stopping"):
        raise PftChatSettingsError("请先启动 Phoenix MCP 服务，再连接带工具的聊天。")
    port = bridge_status.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise PftChatSettingsError("无法确定 Phoenix MCP 的本机端口。")
    result = {**paths, "bridge_url": "http://127.0.0.1:%s/" % port}
    if desk_enabled:
        try:
            from pft_desk_service import validate_configuration
            result["desk_config"] = dict(validate_configuration(desk_configuration))
        except Exception:
            raise PftChatSettingsError("请先在 Desk 连接设置中配置有效的 Node、CLI 和私有状态目录。") from None
    return result
