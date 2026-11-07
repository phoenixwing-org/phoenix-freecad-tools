# SPDX-License-Identifier: Apache-2.0
"""Private proxy data validation; no OS settings, files, credentials or network."""
import importlib.util
from pathlib import Path
import unittest

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
spec = importlib.util.spec_from_file_location("pft_chat_network", ADDON / "pft_chat_network.py")
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)


class ProxyEnvironmentTests(unittest.TestCase):
    def assert_error(self, callback):
        with self.assertRaises(network.PftChatNetworkError) as caught:
            callback()
        self.assertEqual(caught.exception.code, "CHAT_PROXY_INVALID")
        self.assertNotIn("example.invalid", str(caught.exception))
        self.assertNotIn("neutral-secret", repr(caught.exception))

    def test_default_leaves_os_resolution_to_official_client_without_synthetic_proxy(self):
        result = network.build_chat_proxy_environment({"OPENAI_API_KEY": "never", "NODE_OPTIONS": "never"})
        self.assertEqual(result, {"NO_PROXY": "localhost,127.0.0.1,::1", "no_proxy": "localhost,127.0.0.1,::1"})

    def test_explicit_values_stay_private_data_without_copying_other_environment(self):
        url = "http://neutral-user:neutral-secret@proxy.example.invalid:8080"
        source = {"HTTPS_PROXY": url, "NO_PROXY": "example.invalid", "HOME": "/neutral", "OPENAI_API_KEY": "never"}
        before = dict(source)
        result = network.build_chat_proxy_environment(source)
        self.assertEqual(source, before)
        self.assertEqual(result, {"HTTPS_PROXY": url, "https_proxy": url,
            "NO_PROXY": "example.invalid,localhost,127.0.0.1,::1", "no_proxy": "example.invalid,localhost,127.0.0.1,::1"})

    def test_lowercase_precedence_and_explicit_empty_are_preserved(self):
        result = network.build_chat_proxy_environment({"HTTPS_PROXY": "http://upper.example.invalid:8080",
            "https_proxy": "", "NO_PROXY": "upper.example.invalid", "no_proxy": "lower.example.invalid"})
        self.assertEqual(result["HTTPS_PROXY"], "")
        self.assertEqual(result["https_proxy"], "")
        self.assertEqual(result["NO_PROXY"], "lower.example.invalid,localhost,127.0.0.1,::1")
        self.assertNotIn("HTTP_PROXY", result)
        self.assertNotIn("ALL_PROXY", result)

    def test_all_schemes_keep_protocol_ipv6_and_userinfo_without_expansion(self):
        values = {"HTTP_PROXY": "http://[2001:db8::1]:8080", "HTTPS_PROXY": "https://neutral-user:neutral%40secret@proxy.example.invalid:8080/",
                  "ALL_PROXY": "socks5h://proxy.example.invalid:1080"}
        result = network.build_chat_proxy_environment(values)
        self.assertEqual(set(result), set(network.PFT_PROXY_ENV_KEYS))
        for key, value in values.items():
            self.assertEqual(result[key], value)
            self.assertEqual(result[key.lower()], value)

    def test_no_proxy_only_keeps_native_selection_and_deduplicates_loopbacks(self):
        result = network.build_chat_proxy_environment({"NO_PROXY": "198.51.100.0/24, 127.0.0.1,localhost,127.0.0.1"})
        self.assertEqual(set(result), {"NO_PROXY", "no_proxy"})
        self.assertEqual(result["NO_PROXY"], "198.51.100.0/24,127.0.0.1,localhost,::1")
        self.assertEqual(result["no_proxy"], result["NO_PROXY"])

    def test_standard_no_proxy_wildcard_is_not_reinterpreted(self):
        result = network.build_chat_proxy_environment({"HTTPS_PROXY": "http://proxy.example.invalid:8080", "NO_PROXY": "*"})
        self.assertEqual(result["NO_PROXY"], "*,localhost,127.0.0.1,::1")

    def test_invalid_proxy_values_are_rejected_without_raw_details(self):
        for value in (None, 1, " proxy.example.invalid", "proxy.example.invalid:8080", "file:///neutral",
                      "http://proxy.example.invalid:70000", "http://proxy.example.invalid:0", "http://",
                      "http://proxy.example.invalid/path", "http://proxy.example.invalid?neutral-secret",
                      "http://neutral-secret@one@proxy.example.invalid", "http://proxy.example.invalid\nneutral-secret",
                      "http://proxy.example.invalid:\N{ARABIC-INDIC DIGIT ONE}", "http://proxy.example.invalid/\x00"):
            with self.subTest(value=value):
                self.assert_error(lambda: network.build_chat_proxy_environment({"HTTPS_PROXY": value}))

    def test_bypass_type_size_and_control_character_limits_are_retained(self):
        for value in (None, 1, "x" * 16385, "neutral-secret\nexample.invalid", "has space.example.invalid",
                      ",".join("neutral" for _ in range(257)), "x" * 1025):
            with self.subTest(value=value):
                self.assert_error(lambda: network.build_chat_proxy_environment({"NO_PROXY": value}))

    def test_bypass_limit_includes_added_loopbacks(self):
        value = ",".join(("n" * 63 + str(i)) for i in range(240))
        self.assertLess(len(value), 16384)
        self.assert_error(lambda: network.build_chat_proxy_environment({"NO_PROXY": value + "z" * (16384-len(value))}))

    def test_non_mapping_and_lowercase_invalid_values_do_not_fall_back(self):
        self.assert_error(lambda: network.build_chat_proxy_environment(None))
        self.assert_error(lambda: network.build_chat_proxy_environment({"HTTPS_PROXY": "http://proxy.example.invalid", "https_proxy": None}))


if __name__ == "__main__":
    unittest.main()
