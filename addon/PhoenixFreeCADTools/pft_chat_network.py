# SPDX-License-Identifier: Apache-2.0
"""Validate explicit proxy variables for the dedicated official Codex child.

The official client's per-URL system resolver owns OS/PAC/bypass semantics.
Do not flatten those rules into NO_PROXY or read system settings in the UI.
All values are private process data; no network, files or global mutation.
"""
import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

PFT_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                      "http_proxy", "https_proxy", "all_proxy", "no_proxy")
_LOOPBACK = ("localhost", "127.0.0.1", "::1")
_MESSAGES = {
    "CHAT_PROXY_READ_FAILED": "系统代理读取失败；请检查系统网络设置后重新连接。",
    "CHAT_PROXY_INVALID": "代理配置无效；请检查环境变量或系统网络设置后重新连接。",
    "CHAT_PROXY_UNSUPPORTED": "当前系统代理规则无法由聊天连接可靠表达；请改用明确的代理环境变量后重新连接。",
}


class PftChatNetworkError(RuntimeError):
    def __init__(self, code):
        self.code, self.message = code, _MESSAGES[code]
        super().__init__(self.message)


def _fail(code="CHAT_PROXY_INVALID"):
    raise PftChatNetworkError(code) from None


def _text(value, maximum=8192):
    return (isinstance(value, str) and len(value) <= maximum and
            not any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value))


def _proxy(value):
    if not _text(value) or value != value.strip() or any(char.isspace() for char in value):
        _fail()
    if value == "":
        return value  # An explicitly empty variable disables that override.
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https", "socks5", "socks5h") or not parsed.hostname or
                parsed.path not in ("", "/") or parsed.query or parsed.fragment or
                parsed.netloc.count("@") > 1 or parsed.port is not None and not 1 <= parsed.port <= 65535):
            _fail()
        host = parsed.hostname
        if ":" in host:
            ipaddress.IPv6Address(host)
        else:
            ascii_host = host.encode("idna").decode("ascii")
            if not re.fullmatch(r"[A-Za-z0-9._-]+", ascii_host):
                _fail()
    except (ValueError, UnicodeError):
        _fail()
    return value


def _selected(environment, name):
    lower = name.lower()
    return environment[lower] if lower in environment else environment.get(name)


def _no_proxy(value):
    if value is None:
        return []
    if not _text(value, 16384):
        _fail()
    items = [item.strip() for item in value.split(",") if item.strip()]
    if len(items) > 256 or any(len(item) > 1024 or any(char.isspace() for char in item) for item in items):
        _fail()
    return items


def build_chat_proxy_environment(environment):
    """Return validated explicit data, preserving lowercase precedence.

    No HTTP(S)/ALL override means the controller enables the official native
    system resolver. Explicit overrides select the official environment path;
    empty strings retain that client's semantics, not a direct-route promise.
    NO_PROXY alone does not disable native system routing. Loopback additions
    protect environment-based transports; the native client owns OS bypasses.
    """
    if not isinstance(environment, dict) and not hasattr(environment, "get"):
        _fail()
    no_proxy = _selected(environment, "NO_PROXY")
    if no_proxy is None and ("NO_PROXY" in environment or "no_proxy" in environment):
        _fail()
    bypass = _no_proxy(no_proxy)
    result = {}
    for scheme in ("http", "https", "all"):
        key = scheme.upper() + "_PROXY"
        if key in environment or key.lower() in environment:
            value = _proxy(_selected(environment, key))
            result[key] = result[key.lower()] = value
    merged = list(dict.fromkeys(bypass + list(_LOOPBACK)))
    joined = ",".join(merged)
    if len(joined) > 16384:
        _fail()
    result["NO_PROXY"] = result["no_proxy"] = joined
    return result
