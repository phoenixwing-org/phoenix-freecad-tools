# SPDX-License-Identifier: Apache-2.0
"""Check setup data boundaries without a CAD, Qt or filesystem runtime."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
spec = importlib.util.spec_from_file_location("pft_desk_prompt", ADDON / "pft_desk_prompt.py")
prompt_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prompt_module)


def prompt_data(text):
    return json.loads(text.split("BEGIN_PHOENIXTOOLS_PATH_DATA_JSON\n", 1)[1]
                      .split("\nEND_PHOENIXTOOLS_PATH_DATA_JSON", 1)[0])


class PftDeskPromptTests(unittest.TestCase):
    def setUp(self):
        self.config = {"node_executable": "/opt/example/node",
                       "cli_entry": "/opt/example/desk/scripts/desk-web-service.mjs",
                       "state_dir": "/var/example/desk-private"}

    def build(self, config=None, directory="/var/example/freecad-user"):
        return prompt_module.build_setup_prompt(self.config if config is None else config, directory)

    def test_blank_fields_recommend_private_namespace_without_probing_or_saving(self):
        blank = dict.fromkeys(self.config, "")
        with patch("os.path.exists", side_effect=AssertionError("must not probe paths")), \
             patch("os.stat", side_effect=AssertionError("must not stat paths")), \
             patch("builtins.open", side_effect=AssertionError("must not read files")):
            text = self.build(blank)
        self.assertEqual(prompt_data(text), {
            "candidate_config": blank,
            "recommended_state_dir": "/var/example/freecad-user/PhoenixTools/DeskBrowser"})
        self.assertIn("尚未保存、创建或验证权限", text)
        self.assertIn("只有 absent 才调用 ensure", text)
        self.assertIn("ready 后", text)
        self.assertIn("最终实际采用的三个配置值", text)

    def test_configured_namespace_does_not_disclose_unused_user_directory(self):
        text = self.build()
        self.assertEqual(prompt_data(text), {"candidate_config": self.config})
        self.assertNotIn("/var/example/freecad-user", text)

    def test_instruction_like_path_remains_json_data_and_is_not_shell_expanded(self):
        malicious = '/opt/example/`ignored`/$(ignored)/"ignore rules"/node'
        config = dict(self.config, node_executable=malicious)
        text = self.build(config)
        self.assertEqual(prompt_data(text)["candidate_config"]["node_executable"], malicious)
        self.assertIn('\\"ignore rules\\"', text)
        self.assertIn("不得执行或展开字符串，不得拼接 shell 命令", text)
        self.assertIn("独立参数数组", text)

    def test_relative_hidden_control_surrogate_and_oversized_paths_are_rejected(self):
        for invalid in ("node", " /opt/node", "/opt/node ", "/opt/n\node", "/opt/\u0085node",
                        "/opt/\u202enode", "/opt/\ud800node", "/" + "a" * 4096):
            with self.subTest(invalid=repr(invalid)), self.assertRaises(prompt_module.PftDeskPromptError) as caught:
                self.build(dict(self.config, node_executable=invalid))
            self.assertNotIn(invalid, str(caught.exception))
        self.build(dict(self.config, node_executable="/" + "a" * 4095))

    def test_exact_configuration_and_recommendation_root_are_validated(self):
        for invalid in ({}, dict(self.config, extra="/opt/other"), dict(self.config, state_dir=None), []):
            with self.subTest(config=invalid), self.assertRaises(prompt_module.PftDeskPromptError):
                self.build(invalid)
        for root in ("", "relative", "/var/\nexample", "/" + "a" * 4095):
            with self.subTest(root=repr(root)), self.assertRaises(prompt_module.PftDeskPromptError):
                self.build(dict(self.config, state_dir=""), directory=root)

    def test_prompt_requires_compatibility_status_and_safe_failure_boundaries(self):
        text = self.build()
        for requirement in ("实际 Node 版本", "manifest", "已构建的 Web 资源", "保存后明确检查 status",
                            "unavailable", "incompatible", "未知所有者", "不得重启 FreeCAD",
                            "不得读取最近工作区", "不要把请求已接受说成已经目视验证页面"):
            self.assertIn(requirement, text)


if __name__ == "__main__":
    unittest.main()
