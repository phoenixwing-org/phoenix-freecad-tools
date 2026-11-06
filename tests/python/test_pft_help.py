# SPDX-License-Identifier: Apache-2.0
"""Installed help input boundary and inventory drift, without FreeCAD or Qt."""
import importlib.util
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / 'addon/PhoenixFreeCADTools'
spec = importlib.util.spec_from_file_location('pft_help_model_test', ADDON / 'pft_help.py')
help_model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(help_model)


class HelpModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.help_file = Path(self.temp.name) / 'HELP.md'
        self.path_patch = patch.object(help_model, '_HELP_FILE', self.help_file)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def test_bounded_utf8_markdown_is_read_without_rewriting_the_file(self):
        text = '# 帮助\n\n`example()` 只展示，不执行。\n'
        self.help_file.write_text(text, encoding='utf-8')
        before = self.help_file.stat()
        self.assertEqual(help_model.read_help(), text)
        after = self.help_file.stat()
        self.assertEqual((before.st_ino, before.st_size, before.st_mtime_ns),
                         (after.st_ino, after.st_size, after.st_mtime_ns))

    def test_missing_empty_oversized_invalid_utf8_and_nul_fail_with_fixed_error(self):
        for contents in (None, b'', b' ' * (help_model.MAX_HELP_BYTES + 1), b'\xff', b'# help\0'):
            with self.subTest(contents_type=type(contents).__name__):
                if self.help_file.exists(): self.help_file.unlink()
                if contents is not None: self.help_file.write_bytes(contents)
                with self.assertRaisesRegex(help_model.PftHelpError, '^帮助文档无法读取，请检查插件安装。$'):
                    help_model.read_help()

    def test_symlink_directory_and_fifo_are_rejected_before_open(self):
        target = self.help_file.with_name('other.md')
        target.write_text('# Not the installed file')
        for kind in ('link', 'directory', 'fifo'):
            with self.subTest(kind=kind):
                if kind == 'link': self.help_file.symlink_to(target)
                elif kind == 'directory': self.help_file.mkdir()
                elif hasattr(os, 'mkfifo'): os.mkfifo(self.help_file)
                else: continue
                with patch.object(help_model.os, 'open', side_effect=AssertionError('must not open')):
                    with self.assertRaises(help_model.PftHelpError): help_model.read_help()
                if kind == 'directory': self.help_file.rmdir()
                else: self.help_file.unlink()

    def test_exchange_between_lstat_and_open_is_rejected_by_native_identity(self):
        self.help_file.write_text('# Original')
        replacement = self.help_file.with_name('replacement.md')
        replacement.write_text('# Replaced')
        native_open = os.open
        def exchanged(*args, **kwargs):
            os.replace(replacement, self.help_file)
            return native_open(*args, **kwargs)
        with patch.object(help_model.os, 'open', side_effect=exchanged):
            with self.assertRaises(help_model.PftHelpError): help_model.read_help()

    def test_only_in_page_anchors_and_explicit_https_destinations_are_allowed(self):
        self.assertEqual(help_model.help_link('#tools'), ('anchor', 'tools'))
        self.assertEqual(help_model.help_link('https://example.com/help#tools'),
                         ('external', 'https://example.com/help#tools'))
        for value in ('http://example.com', 'file:///tmp/example', 'qrc:/help', 'data:text/plain,test',
                      'javascript:alert(1)', 'mailto:user@example.com', '//example.com', 'HELP.md', '#',
                      'https://user:secret@example.com', 'https://example.com:80/help',
                      'https://example.com/\nhelp', 'https://example.com/\u200bhelp',
                      'https://example.com\\help', ' https://example.com', 'https://[invalid', None):
            with self.subTest(value=value): self.assertIsNone(help_model.help_link(value))

    def test_canonical_help_matches_full_chat_and_optional_desk_machine_names(self):
        markdown = (ADDON / 'HELP.md').read_text(encoding='utf-8')
        server = (ROOT / 'src/server.ts').read_text()
        def exported(name, source):
            body = re.search(r'export const ' + name + r' = \[(.*?)\] as const;', source, re.S).group(1)
            return re.findall(r"'([^']+)'", body)
        rows = re.findall(r'^\| `([^`]+)` \| (是|否) \|', markdown, re.M)
        self.assertEqual([name for name, chat in rows], exported('TOOL_NAMES', server))
        self.assertEqual({name for name, chat in rows if chat == '是'}, set(exported('CHAT_TOOL_NAMES', server)))
        self.assertEqual((len(rows), sum(chat == '是' for name, chat in rows)), (25, 19))
        desk = (ROOT / 'src/pft-desk-tools.ts').read_text()
        self.assertEqual(re.findall(r'^\| `(desk_[^`]+)` \|', markdown, re.M), exported('PFT_DESK_TOOL_NAMES', desk))
        self.assertLess(len(markdown.encode('utf-8')), help_model.MAX_HELP_BYTES)


if __name__ == '__main__': unittest.main()
