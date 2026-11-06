# SPDX-License-Identifier: Apache-2.0
"""Synthetic proxy policies and a stubbed macOS adapter; never read system data."""
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
spec = importlib.util.spec_from_file_location("pft_chat_network", ADDON / "pft_chat_network.py")
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)


def policy(**changes):
    result = {"proxies": {}, "bypass": [], "exclude_simple": False,
              "pac_enabled": False, "auto_detect": False}
    result.update(changes)
    return result


def mac_snapshot(extra="", host="proxy.example.invalid"):
    return ("<dictionary> {\n HTTPEnable : 1\n HTTPProxy : " + host + "\n HTTPPort : 8080\n"
            " HTTPSEnable : 1\n HTTPSProxy : " + host + "\n HTTPSPort : 8080\n"
            " SOCKSEnable : 1\n SOCKSProxy : " + host + "\n SOCKSPort : 1080\n" + extra + "}\n").encode()


class ProxyEnvironmentTests(unittest.TestCase):
    def assert_error(self, code, callback):
        with self.assertRaises(network.PftChatNetworkError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("example.invalid", str(caught.exception))
        self.assertNotIn("neutral-secret", repr(caught.exception))

    def test_system_endpoints_and_bypass_become_only_standard_proxy_variables(self):
        environment = network.build_chat_proxy_environment({"OPENAI_API_KEY": "never", "NODE_OPTIONS": "never"},
            system_reader=lambda: policy(proxies={"http": "http://proxy.example.invalid:8080",
                "https": "http://secure.example.invalid:8080", "all": "socks5h://socks.example.invalid:1080"},
                bypass=["*.example.invalid", "192.0/16", "198.51.100.0/24", "2001:db8::/32"]))
        self.assertEqual(set(environment), set(network.PFT_PROXY_ENV_KEYS))
        self.assertEqual(environment["HTTPS_PROXY"], "http://secure.example.invalid:8080")
        self.assertEqual(environment["all_proxy"], "socks5h://socks.example.invalid:1080")
        self.assertEqual(environment["NO_PROXY"], ".example.invalid,192.0.0.0/16,198.51.100.0/24,2001:db8::/32,localhost,127.0.0.1,::1")
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            self.assertEqual(environment[key], environment[key.lower()])

    def test_any_explicit_proxy_overrides_the_entire_system_policy_without_reading_it(self):
        reader = Mock(side_effect=AssertionError("System reader must not run"))
        url = "http://neutral-user:neutral-secret@proxy.example.invalid:8080"
        environment = network.build_chat_proxy_environment({"HTTPS_PROXY": url, "NO_PROXY": "example.invalid"}, reader)
        self.assertEqual(environment["https_proxy"], url)
        self.assertNotIn("HTTP_PROXY", environment)
        self.assertNotIn("ALL_PROXY", environment)
        self.assertEqual(environment["no_proxy"], "example.invalid,localhost,127.0.0.1,::1")
        reader.assert_not_called()

    def test_lowercase_wins_and_explicit_empty_proxy_does_not_fall_back_to_system(self):
        reader = Mock(side_effect=AssertionError("No system fallback"))
        result = network.build_chat_proxy_environment({"HTTPS_PROXY": "http://upper.example.invalid:8080",
            "https_proxy": "", "NO_PROXY": "upper.example.invalid", "no_proxy": "lower.example.invalid"}, reader)
        self.assertEqual(result["HTTPS_PROXY"], "")
        self.assertEqual(result["https_proxy"], "")
        self.assertEqual(result["NO_PROXY"], "lower.example.invalid,localhost,127.0.0.1,::1")
        reader.assert_not_called()

    def test_no_proxy_alone_keeps_system_selection_and_local_bypass(self):
        reader = Mock(return_value=policy(proxies={"http": "http://proxy.example.invalid:8080"},
                                          bypass=["localhost", "*.example.invalid"]))
        source = {"NO_PROXY": "198.51.100.0/24,127.0.0.1"}
        before = dict(source)
        result = network.build_chat_proxy_environment(source, reader)
        reader.assert_called_once_with()
        self.assertEqual(source, before)
        self.assertEqual(result["NO_PROXY"], "198.51.100.0/24,127.0.0.1,localhost,.example.invalid,::1")
        self.assertIn("HTTP_PROXY", result)

    def test_disabled_system_proxy_is_direct_with_only_local_bypass(self):
        result = network.build_chat_proxy_environment({}, lambda: policy())
        self.assertEqual(result, {"NO_PROXY": "localhost,127.0.0.1,::1", "no_proxy": "localhost,127.0.0.1,::1"})

    def test_legal_authenticated_and_ipv6_proxy_urls_are_data(self):
        for url in ("https://neutral-user:neutral%40secret@proxy.example.invalid:8080/",
                    "http://[2001:db8::1]:8080", "socks5h://proxy.example.invalid:1080"):
            with self.subTest(url=url):
                self.assertEqual(network.build_chat_proxy_environment({"ALL_PROXY": url})["ALL_PROXY"], url)

    def test_invalid_proxy_values_are_rejected_without_raw_details(self):
        for value in (None, 1, " proxy.example.invalid", "proxy.example.invalid:8080", "file:///neutral",
                      "http://proxy.example.invalid:70000", "http://proxy.example.invalid:0", "http://",
                      "http://proxy.example.invalid/path", "http://proxy.example.invalid?neutral-secret",
                      "http://neutral-secret@one@proxy.example.invalid", "http://proxy.example.invalid\nneutral-secret",
                      "http://proxy.example.invalid:\N{ARABIC-INDIC DIGIT ONE}", "http://proxy.example.invalid/\x00"):
            with self.subTest(value=value):
                self.assert_error("CHAT_PROXY_INVALID", lambda: network.build_chat_proxy_environment({"HTTPS_PROXY": value}))

    def test_pac_auto_discovery_and_simple_host_rules_are_not_silently_direct(self):
        for flag in ("pac_enabled", "auto_detect", "exclude_simple"):
            with self.subTest(flag=flag):
                self.assert_error("CHAT_PROXY_UNSUPPORTED", lambda: network.build_chat_proxy_environment({},
                    lambda: policy(**{flag: True})))
        # Explicit data is a deliberate replacement even if system PAC would fail.
        reader = Mock(side_effect=network.PftChatNetworkError("CHAT_PROXY_UNSUPPORTED"))
        network.build_chat_proxy_environment({"HTTP_PROXY": "http://proxy.example.invalid:8080"}, reader)
        reader.assert_not_called()

    def test_unrepresentable_system_bypass_is_rejected_instead_of_dropped(self):
        for rule in ("api.*.example.invalid", "example.invalid:8080", "<local>", "198.51/99", "999.1/16", "http://example.invalid"):
            with self.subTest(rule=rule):
                self.assert_error("CHAT_PROXY_UNSUPPORTED", lambda: network.build_chat_proxy_environment({},
                    lambda: policy(bypass=[rule])))

    def test_system_reader_errors_and_malformed_policies_are_fixed_and_bounded(self):
        def broken(): raise RuntimeError("neutral-secret@proxy.example.invalid")
        self.assert_error("CHAT_PROXY_READ_FAILED", lambda: network.build_chat_proxy_environment({}, broken))
        for value in (None, {}, policy(auto_detect="1"), policy(bypass=["example.invalid"] * 129),
                      policy(proxies={"unknown": "http://proxy.example.invalid:8080"})):
            with self.subTest(value=value):
                self.assert_error("CHAT_PROXY_READ_FAILED", lambda: network.build_chat_proxy_environment({}, lambda: value))
        self.assert_error("CHAT_PROXY_INVALID", lambda: network.build_chat_proxy_environment({"NO_PROXY": "x" * 16385}, lambda: policy()))

    def test_other_platforms_require_explicit_proxy_configuration(self):
        self.assert_error("CHAT_PROXY_UNSUPPORTED", lambda: network.build_chat_proxy_environment({}, platform="neutral-platform"))
        result = network.build_chat_proxy_environment({"HTTP_PROXY": "http://proxy.example.invalid:8080"}, platform="neutral-platform")
        self.assertIn("HTTP_PROXY", result)


class MacProxyAdapterTests(unittest.TestCase):
    def assert_error(self, code, callback):
        with self.assertRaises(network.PftChatNetworkError) as caught: callback()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("example.invalid", str(caught.exception))

    def test_fixed_snapshot_preserves_protocols_and_exception_policy(self):
        raw = mac_snapshot(" ExceptionsList : <array> {\n  0 : *.example.invalid\n  1 : 198.51.100.0/24\n }\n ProxyAutoConfigEnable : 0\n ProxyAutoDiscoveryEnable : 0\n")
        reader = Mock(return_value=raw)
        result = network.read_system_proxy_configuration("darwin", reader)
        reader.assert_called_once_with()
        self.assertEqual(result, policy(proxies={"http": "http://proxy.example.invalid:8080",
            "https": "http://proxy.example.invalid:8080", "all": "socks5h://proxy.example.invalid:1080"},
            bypass=["*.example.invalid", "198.51.100.0/24"]))

    def test_enabled_pac_and_discovery_are_retained_for_selection_rejection(self):
        for key, flag in (("ProxyAutoConfigEnable", "pac_enabled"), ("ProxyAutoDiscoveryEnable", "auto_detect")):
            raw = mac_snapshot(" " + key + " : 1\n ProxyAutoConfigURLString : https://pac.example.invalid/neutral.pac\n")
            result = network.read_system_proxy_configuration("darwin", lambda: raw)
            self.assertTrue(result[flag])
            self.assertNotIn("pac.example.invalid", repr(result))
            self.assert_error("CHAT_PROXY_UNSUPPORTED", lambda: network.build_chat_proxy_environment({}, lambda: result))

    def test_only_equivalent_scoped_policies_can_use_one_child_environment(self):
        raw = b"<dictionary> {\n HTTPEnable : 1\n HTTPProxy : proxy.example.invalid\n HTTPPort : 8080\n __SCOPED__ : <dictionary> {\n  neutral0 : <dictionary> {\n   HTTPEnable : 1\n   HTTPProxy : proxy.example.invalid\n   HTTPPort : 8080\n  }\n }\n}\n"
        result = network.read_system_proxy_configuration("darwin", lambda: raw)
        self.assertEqual(result["proxies"], {"http": "http://proxy.example.invalid:8080"})
        mismatch = raw.replace(b"   HTTPPort : 8080", b"   HTTPPort : 8081")
        self.assert_error("CHAT_PROXY_UNSUPPORTED", lambda: network.read_system_proxy_configuration("darwin", lambda: mismatch))
        supplemental = mac_snapshot(" SupplementalMatchDomains : <array> {\n  0 : example.invalid\n }\n")
        self.assert_error("CHAT_PROXY_UNSUPPORTED", lambda: network.read_system_proxy_configuration("darwin", lambda: supplemental))
        matched = mac_snapshot(" __MATCHED__ : <dictionary> {\n  neutral : 1\n }\n")
        self.assert_error("CHAT_PROXY_UNSUPPORTED", lambda: network.read_system_proxy_configuration("darwin", lambda: matched))

    def test_malformed_or_excessive_adapter_output_is_sanitized(self):
        for raw in (b"", b"neutral-secret", b"<dictionary> {\n HTTPEnable : 2\n}\n", b"<dictionary> {\n HTTPEnable : 1\n}\n",
                    b"<dictionary> {\n HTTPEnable : 0\n HTTPEnable : 0\n}\n", b"x" * (network._LIMIT + 1),
                    b"<dictionary> {\n ExceptionsList : <array> {\n  1 : example.invalid\n }\n}\n", b"\xff"):
            with self.subTest(length=len(raw)):
                self.assert_error("CHAT_PROXY_READ_FAILED", lambda: network.read_system_proxy_configuration("darwin", lambda: raw))

    def runner(self, chunks, events=True, exit_code=0):
        process = types.SimpleNamespace(stdout=Mock(), code=None, kills=0)
        process.stdout.fileno.return_value = 123
        process.poll = lambda: process.code
        def wait(timeout): process.code = exit_code; return exit_code
        def kill(): process.kills += 1; process.code = -9
        process.wait, process.kill = Mock(side_effect=wait), Mock(side_effect=kill)
        selector = Mock(); selector.__enter__ = Mock(return_value=selector); selector.__exit__ = Mock(return_value=False)
        selector.select.return_value = [(None, None)] if events else []
        patches = (patch.object(network.subprocess, "Popen", return_value=process),
            patch.object(network.selectors, "DefaultSelector", return_value=selector),
            patch.object(network.os, "read", side_effect=chunks), patch.object(network.time, "monotonic", return_value=0.0))
        return process, patches

    def test_reader_has_fixed_argv_sanitized_environment_and_no_shell_or_stderr_capture(self):
        process, patches = self.runner([b"<dictionary> {\n}\n", b""])
        with patches[0] as spawn, patches[1], patches[2], patches[3]:
            result = network._run_scutil()
        self.assertEqual(result, b"<dictionary> {\n}\n")
        args, options = spawn.call_args
        self.assertEqual(args, (["/usr/sbin/scutil", "--proxy"],))
        self.assertIs(options["shell"], False)
        self.assertEqual(set(options["env"]), {"PATH", "LANG", "LC_ALL"})
        self.assertEqual(options["stderr"], network.subprocess.DEVNULL)
        self.assertEqual(process.kills, 0)
        process.stdout.close.assert_called_once()

    def test_timeout_output_limit_and_exit_failure_stop_only_the_owned_reader(self):
        for chunks, events, exit_code in (([], False, 0), ([b"x" * 4096] * 16 + [b"x"], True, 0),
                                           ([b"",], True, 9)):
            with self.subTest(events=events, exit_code=exit_code, chunks=len(chunks)):
                process, patches = self.runner(chunks, events, exit_code)
                with patches[0], patches[1], patches[2], patches[3]:
                    self.assert_error("CHAT_PROXY_READ_FAILED", network._run_scutil)
                self.assertEqual(process.kills, 0 if exit_code else 1)
                process.stdout.close.assert_called_once()

    def test_adapter_and_cleanup_errors_remain_fixed(self):
        def unavailable(): raise RuntimeError("neutral-secret@proxy.example.invalid")
        self.assert_error("CHAT_PROXY_READ_FAILED", lambda: network.read_system_proxy_configuration("darwin", unavailable))
        process, patches = self.runner([b"<dictionary> {\n}\n", b""])
        process.stdout.close.side_effect = RuntimeError("neutral-secret@proxy.example.invalid")
        with patches[0], patches[1], patches[2], patches[3]:
            self.assert_error("CHAT_PROXY_READ_FAILED", network._run_scutil)

    def test_spawn_failure_never_exposes_exception_or_reads_environment(self):
        with patch.object(network.subprocess, "Popen", side_effect=OSError("neutral-secret@proxy.example.invalid")):
            self.assert_error("CHAT_PROXY_READ_FAILED", network._run_scutil)


if __name__ == "__main__":
    unittest.main()
