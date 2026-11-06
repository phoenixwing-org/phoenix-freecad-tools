# SPDX-License-Identifier: Apache-2.0
"""Select bounded system/manual proxy data for a newly requested chat child.

No network request, credential store, user history or global environment mutation.
macOS' _scproxy omits PAC metadata and labels SOCKS endpoints as HTTP URLs,
so the adapter reads the fixed scutil proxy snapshot instead.
"""
import ipaddress
import os
import re
import selectors
import subprocess
import sys
import time
import unicodedata
from urllib.parse import urlsplit

PFT_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                      "http_proxy", "https_proxy", "all_proxy", "no_proxy")
_LIMIT = 64 * 1024
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


def _system_bypass(value):
    if not _text(value, 1024) or not value or value != value.strip():
        _fail("CHAT_PROXY_UNSUPPORTED")
    if value == "*":
        return value
    # macOS accepts abbreviated IPv4 network expressions such as 192.0/16.
    if re.fullmatch(r"\d+(?:\.\d+){0,3}(?:/\d+)?", value):
        address, slash, prefix = value.partition("/")
        octets = address.split(".")
        if any(int(part) > 255 for part in octets):
            _fail("CHAT_PROXY_UNSUPPORTED")
        bits = int(prefix) if slash else len(octets) * 8
        try:
            network = ipaddress.IPv4Network(".".join(octets + ["0"] * (4 - len(octets))) + "/" + str(bits), strict=False)
        except ValueError:
            _fail("CHAT_PROXY_UNSUPPORTED")
        return str(network.network_address) if bits == 32 else str(network)
    try:
        if "/" in value:
            return str(ipaddress.ip_network(value, strict=False))
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    domain = value[1:] if value.startswith("*.") else value
    if "*" in domain or any(char in domain for char in "/:[]?@<>"):
        _fail("CHAT_PROXY_UNSUPPORTED")
    try:
        ascii_domain = domain.lstrip(".").encode("idna").decode("ascii")
    except UnicodeError:
        _fail("CHAT_PROXY_UNSUPPORTED")
    if (not ascii_domain or len(ascii_domain) > 253 or
            not all(re.fullmatch(r"[A-Za-z0-9_-]+", label) for label in ascii_domain.rstrip(".").split("."))):
        _fail("CHAT_PROXY_UNSUPPORTED")
    return ("." if domain.startswith(".") else "") + ascii_domain


def build_chat_proxy_environment(environment, system_reader=None, platform=None):
    """Explicit proxy variables replace the system policy as a whole.

    Lowercase takes precedence. NO_PROXY alone retains system selection; local
    bridge hosts always bypass proxies. All returned values stay private data.
    """
    if not isinstance(environment, dict) and not hasattr(environment, "get"):
        _fail()
    explicit = any(key in environment for key in PFT_PROXY_ENV_KEYS if key.lower() != "no_proxy")
    no_proxy = _selected(environment, "NO_PROXY")
    if no_proxy is None and ("NO_PROXY" in environment or "no_proxy" in environment):
        _fail()
    proxies, bypass = {}, _no_proxy(no_proxy)
    if explicit:
        for scheme in ("http", "https", "all"):
            value = _selected(environment, scheme.upper() + "_PROXY")
            if scheme.upper() + "_PROXY" in environment or scheme + "_proxy" in environment:
                proxies[scheme] = _proxy(value)
    else:
        try:
            snapshot = (system_reader() if system_reader is not None else
                        read_system_proxy_configuration(platform))
        except PftChatNetworkError:
            raise
        except Exception:
            _fail("CHAT_PROXY_READ_FAILED")
        required = {"proxies", "bypass", "exclude_simple", "pac_enabled", "auto_detect"}
        if (not isinstance(snapshot, dict) or set(snapshot) != required or
                not isinstance(snapshot["proxies"], dict) or set(snapshot["proxies"]) - {"http", "https", "all"} or
                not isinstance(snapshot["bypass"], (list, tuple)) or len(snapshot["bypass"]) > 128 or
                any(type(snapshot[key]) is not bool for key in ("exclude_simple", "pac_enabled", "auto_detect"))):
            _fail("CHAT_PROXY_READ_FAILED")
        if snapshot["pac_enabled"] or snapshot["auto_detect"] or snapshot["exclude_simple"]:
            _fail("CHAT_PROXY_UNSUPPORTED")
        proxies = {scheme: _proxy(value) for scheme, value in snapshot["proxies"].items()}
        bypass.extend(_system_bypass(value) for value in snapshot["bypass"])
    result, merged = {}, []
    for item in bypass + list(_LOOPBACK):
        if item not in merged:
            merged.append(item)
    joined = ",".join(merged)
    if len(joined) > 16384:
        _fail()
    for scheme, value in dict(proxies, no=joined).items():
        name = scheme.upper() + "_PROXY"
        result[name] = result[name.lower()] = value
    return result


def _run_scutil():
    """Read only this fixed owned helper, with time and output bounds."""
    process = None
    try:
        process = subprocess.Popen(["/usr/sbin/scutil", "--proxy"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
            env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"})
        deadline, output = time.monotonic() + 2.0, bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    _fail("CHAT_PROXY_READ_FAILED")
                chunk = os.read(process.stdout.fileno(), min(4096, _LIMIT + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > _LIMIT:
                    _fail("CHAT_PROXY_READ_FAILED")
        if process.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
            _fail("CHAT_PROXY_READ_FAILED")
        return bytes(output)
    except PftChatNetworkError:
        raise
    except Exception:
        _fail("CHAT_PROXY_READ_FAILED")
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=0.2)
                if process.stdout is not None:
                    process.stdout.close()
            except Exception:
                _fail("CHAT_PROXY_READ_FAILED")


def _parse_scutil(raw):
    if not isinstance(raw, bytes) or len(raw) > _LIMIT:
        _fail("CHAT_PROXY_READ_FAILED")
    try:
        lines = [line.strip() for line in raw.decode("utf-8", errors="strict").splitlines() if line.strip()]
    except UnicodeError:
        _fail("CHAT_PROXY_READ_FAILED")
    offset = [0]
    def container(kind, depth=0):
        if depth > 8:
            _fail("CHAT_PROXY_READ_FAILED")
        result = {} if kind == "dictionary" else []
        while offset[0] < len(lines):
            line = lines[offset[0]]; offset[0] += 1
            if line == "}":
                return result
            if " : " not in line or len(result) >= 256:
                _fail("CHAT_PROXY_READ_FAILED")
            key, value = line.split(" : ", 1)
            if len(key) > 128 or len(value) > 8192:
                _fail("CHAT_PROXY_READ_FAILED")
            if value in ("<dictionary> {", "<array> {"):
                value = container("dictionary" if value.startswith("<dictionary>") else "array", depth + 1)
            if kind == "dictionary":
                if key in result:
                    _fail("CHAT_PROXY_READ_FAILED")
                result[key] = value
            else:
                if key != str(len(result)):
                    _fail("CHAT_PROXY_READ_FAILED")
                result.append(value)
        _fail("CHAT_PROXY_READ_FAILED")
    if not lines or lines[0] != "<dictionary> {":
        _fail("CHAT_PROXY_READ_FAILED")
    offset[0] = 1
    result = container("dictionary")
    if offset[0] != len(lines):
        _fail("CHAT_PROXY_READ_FAILED")
    return result


def _mac_policy(settings):
    if not isinstance(settings, dict):
        _fail("CHAT_PROXY_READ_FAILED")
    def enabled(key):
        value = settings.get(key, "0")
        if value not in ("0", "1"):
            _fail("CHAT_PROXY_READ_FAILED")
        return value == "1"
    proxies = {}
    for prefix, scheme, protocol in (("HTTP", "http", "http"), ("HTTPS", "https", "http"), ("SOCKS", "all", "socks5h")):
        if enabled(prefix + "Enable"):
            host, port = settings.get(prefix + "Proxy"), settings.get(prefix + "Port")
            if (not _text(host, 1024) or not host or any(char in host for char in "@/?#") or
                    not isinstance(port, str) or not port.isascii() or not port.isdigit()):
                _fail("CHAT_PROXY_READ_FAILED")
            host = "[" + host + "]" if ":" in host and not host.startswith("[") else host
            proxies[scheme] = _proxy(protocol + "://" + host + ":" + port)
    bypass = settings.get("ExceptionsList", [])
    if not isinstance(bypass, list):
        _fail("CHAT_PROXY_READ_FAILED")
    return {"proxies": proxies, "bypass": bypass,
            "exclude_simple": enabled("ExcludeSimpleHostnames"),
            "pac_enabled": enabled("ProxyAutoConfigEnable"),
            "auto_detect": enabled("ProxyAutoDiscoveryEnable")}


def read_system_proxy_configuration(platform=None, command_reader=None):
    if (sys.platform if platform is None else platform) != "darwin":
        _fail("CHAT_PROXY_UNSUPPORTED")
    try:
        raw = (command_reader or _run_scutil)()
    except PftChatNetworkError:
        raise
    except Exception:
        _fail("CHAT_PROXY_READ_FAILED")
    settings = _parse_scutil(raw)
    policy = _mac_policy(settings)
    # Interface-specific copies are representable only when they agree with
    # the global policy; domain-specific supplemental routing is not.
    for key, value in settings.items():
        if value and (key.startswith("Supplemental") or key.startswith("__") and key != "__SCOPED__"):
            _fail("CHAT_PROXY_UNSUPPORTED")
    scoped = settings.get("__SCOPED__", {})
    if not isinstance(scoped, dict) or len(scoped) > 32:
        _fail("CHAT_PROXY_READ_FAILED")
    for scope in scoped.values():
        if not isinstance(scope, dict):
            _fail("CHAT_PROXY_READ_FAILED")
        if (any(value and (key.startswith("Supplemental") or key.startswith("__"))
                for key, value in scope.items()) or _mac_policy(scope) != policy):
            _fail("CHAT_PROXY_UNSUPPORTED")
    return policy
