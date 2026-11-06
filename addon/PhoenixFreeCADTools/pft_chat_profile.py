# SPDX-License-Identifier: Apache-2.0
"""Local profile adapter for the separate, user-triggered FreeCAD chat client.

This module never reads existing Codex credentials or conversation stores.
"""
import json
import os
from pathlib import Path
import shutil
import stat
import unicodedata


class PftChatProfileError(ValueError):
    pass


# Stable local prototype configuration. CLI feature flags add a second layer.
PROFILE_CONFIG = '''# Managed by Phoenix FreeCAD Tools: dedicated chat profile only.
model_provider = "openai"
forced_login_method = "chatgpt"
cli_auth_credentials_store = "file"
approval_policy = "never"
sandbox_mode = "read-only"
web_search = "disabled"

[features]
shell_tool = false
unified_exec = false
apply_patch_freeform = false
apps = false
hooks = false
multi_agent = false
remote_plugin = false
plugins = false

[analytics]
enabled = false

[feedback]
enabled = false

[otel]
exporter = "none"
log_user_prompt = false
'''
_PROFILE_MARKER = {"owner": "phoenix-freecad-tools", "schema_version": 1,
                   "purpose": "dedicated-native-chat"}


def validate_executable(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or value != value.strip():
        raise PftChatProfileError("请选择有效的 Codex 绝对路径。")
    if any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value) or not os.path.isabs(value):
        raise PftChatProfileError("请选择有效的 Codex 绝对路径。")
    resolved = os.path.realpath(value)
    if not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        raise PftChatProfileError("Codex 文件不存在或不能执行。")
    return resolved


def detect_codex_executable():
    """Only inspect known executables; no subprocess, user config or account reads."""
    candidates = [shutil.which("codex"),
                  "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
                  "/Applications/Codex.app/Contents/Resources/codex"]
    for candidate in candidates:
        if candidate:
            try:
                return validate_executable(candidate)
            except PftChatProfileError:
                pass
    return ""


def _private_directory(path, create=False):
    try:
        if create:
            path.mkdir(mode=0o700)
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise PftChatProfileError("聊天目录不是安全的本机目录。")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise PftChatProfileError("聊天目录不属于当前用户。")
        if info.st_mode & 0o077:
            raise PftChatProfileError("聊天目录必须仅当前用户可访问。")
    except FileExistsError:
        return _private_directory(path)
    except OSError:
        raise PftChatProfileError("无法创建或读取专属聊天目录。") from None


def prepare_chat_profile(user_data_directory):
    """Explicit Connect action only. Create/reuse our exact private profile.

    Existing unrecognized directories/configuration are retained and rejected.
    The official app-server owns any auth/session data it later creates here.
    """
    if not isinstance(user_data_directory, str) or not os.path.isabs(user_data_directory):
        raise PftChatProfileError("无法确定 FreeCAD 用户数据目录。")
    data = Path(os.path.realpath(user_data_directory))
    if not data.is_dir():
        raise PftChatProfileError("无法确定 FreeCAD 用户数据目录。")
    phoenix = data / "PhoenixTools"
    try:
        phoenix.mkdir(mode=0o700, exist_ok=True)
        info = phoenix.lstat()
        if phoenix.is_symlink() or not stat.S_ISDIR(info.st_mode) or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
            raise PftChatProfileError("聊天目录不是安全的本机目录。")
        base = phoenix / "CodexChat"
        fresh = not base.exists() and not base.is_symlink()
        _private_directory(base, create=fresh)
        home, workspace = base / "home", base / "workspace"
        marker = base / "profile.json"
        if fresh:
            _private_directory(home, create=True)
            _private_directory(workspace, create=True)
            for path, text in ((home / "config.toml", PROFILE_CONFIG),
                               (marker, json.dumps(_PROFILE_MARKER, sort_keys=True) + "\n")):
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(text)
        else:
            _private_directory(home)
            _private_directory(workspace)
            if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 4096:
                raise PftChatProfileError("已有聊天目录未识别；未覆盖任何配置。")
            if json.loads(marker.read_text(encoding="utf-8")) != _PROFILE_MARKER:
                raise PftChatProfileError("已有聊天目录未识别；未覆盖任何配置。")
            config = home / "config.toml"
            if config.is_symlink() or not config.is_file() or config.stat().st_size > 16384 or config.read_text(encoding="utf-8") != PROFILE_CONFIG:
                raise PftChatProfileError("专属聊天配置已变化；请核对后再连接。")
        if any(workspace.iterdir()):
            raise PftChatProfileError("专属聊天工作目录不为空；未清理或覆盖任何文件。")
        return {"home": str(home), "cwd": str(workspace)}
    except PftChatProfileError:
        raise
    except (OSError, ValueError):
        raise PftChatProfileError("无法创建或读取专属聊天目录。") from None
