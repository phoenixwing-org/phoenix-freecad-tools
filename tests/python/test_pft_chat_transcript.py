# SPDX-License-Identifier: Apache-2.0
"""Qt-rich-text presentation cannot interpret message content as HTML/resources."""
from html.parser import HTMLParser
from pathlib import Path
import sys
import unittest

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
_previous_path = list(sys.path)
try:
    sys.path.insert(0, str(ADDON))
    from pft_chat_transcript import render_chat_transcript
finally:
    sys.path[:] = _previous_path


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.tags, self.attributes, self.text = [], [], []
        self.tables, self.text_tables = [], []
        self.feed(source)
    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)
        if tag == "table":
            self.tables.append(dict(attrs))
    def handle_endtag(self, tag):
        if tag == "table":
            self.tables.pop()
    def handle_data(self, data):
        self.text.append(data)
        self.text_tables.append((data, list(self.tables)))


class ChatTranscriptTests(unittest.TestCase):
    def test_message_label_and_time_are_text_not_markup_or_resources(self):
        body = '<img src="file:///not-a-resource"> & <script>sample()</script>\n  indented'
        source = render_chat_transcript([{"role": "user", "text": body}],
                                        '<b>Me</b>', ['<a href="https://example.invalid">time</a>'])
        document = Document(source)
        text = "".join(document.text)
        self.assertIn(body, text)
        self.assertIn('<b>Me</b>', text)
        self.assertIn('<a href="https://example.invalid">time</a>', text)
        self.assertFalse(set(document.tags) & {"img", "script", "a", "link", "style", "iframe"})
        self.assertFalse(any(key in ("src", "href") for key, _ in document.attributes))

    def test_local_times_align_with_messages_and_missing_times_stay_empty(self):
        rows = [{"role": "user", "text": "Example input"},
                {"role": "assistant", "text": "Example response"},
                {"role": "assistant", "text": "More response"}]
        source = render_chat_transcript(rows, "Me", ["09:10", "09:11"])
        text = "".join(Document(source).text)
        self.assertLess(text.index("09:10"), text.index("Example input"))
        self.assertLess(text.index("09:11"), text.index("Example response"))
        self.assertEqual(source.count("09:10"), 1)
        self.assertEqual(source.count("09:11"), 1)
        self.assertEqual(Document(source).tags.count("table"), 2)
        self.assertEqual(source, render_chat_transcript(rows, "Me", ["09:10", "09:11"]))

    def test_only_user_messages_have_content_sized_right_bubbles(self):
        rows = [{"role": "user", "text": "Neutral short request"},
                {"role": "assistant", "text": "Neutral plain response"},
                {"role": "user", "text": "Neutral longer request " * 40}]
        for dark, background in ((False, "#edf3fc"), (True, "#29384b")):
            with self.subTest(dark=dark):
                document = Document(render_chat_transcript(rows, message_times=["10:20", "10:21", "10:22"], dark=dark))
                for row in rows:
                    ancestors = next(tables for text, tables in document.text_tables if text == row["text"])
                    if row["role"] == "user":
                        self.assertEqual(len(ancestors), 2)
                        outer, bubble = ancestors
                        self.assertEqual(outer["width"], "100%")
                        self.assertEqual(bubble["align"], "right")
                        self.assertEqual(bubble["bgcolor"], background)
                        self.assertNotIn("width", bubble, "Short bubbles must size to their text")
                    else:
                        self.assertEqual(ancestors, [], "AI text must remain in the left document flow")
                self.assertNotIn("border-radius", render_chat_transcript(rows, dark=dark))


    def test_incomplete_ai_outcomes_are_compact_metadata_not_success_marks(self):
        for status, marker in (('interrupted', '已停止'), ('error', '未完成'),
                               ('complete', ''), ('streaming', '')):
            with self.subTest(status=status):
                rows = [{'role': 'assistant', 'text': 'Neutral answer', 'status': status}]
                document = Document(render_chat_transcript(rows, message_times=['09:11']))
                text = ''.join(document.text)
                self.assertIn('AI 09:11' + (' ' + marker if marker else ''), text)
                self.assertEqual(text.count('Neutral answer'), 1)
                self.assertFalse(set(document.tags) & {'img', 'a', 'script'})
                if not marker:
                    self.assertNotIn('已停止', text)
                    self.assertNotIn('未完成', text)
                    self.assertNotIn('成功', text)

    def test_localized_outcome_labels_are_escaped_and_preserve_user_bubbles(self):
        rows = [{'role': 'user', 'text': 'Neutral request', 'status': 'interrupted'},
                {'role': 'assistant', 'text': 'Neutral stopped output', 'status': 'interrupted'},
                {'role': 'assistant', 'text': 'Neutral failed output', 'status': 'error'}]
        for dark in (False, True):
            document = Document(render_chat_transcript(rows, 'Me', ['10:20', '10:21', '10:22'], dark=dark,
                interrupted_label='<b>Stopped</b>', error_label='<img src="file:///not-loaded">'))
            text = ''.join(document.text)
            self.assertIn('Me 10:20', text)
            self.assertNotIn('Me 10:20 <b>', text)
            self.assertIn('AI 10:21 <b>Stopped</b>', text)
            self.assertIn('AI 10:22 <img src="file:///not-loaded">', text)
            self.assertFalse(set(document.tags) & {'b', 'img', 'a', 'script'})
            for row in rows:
                tables = next(tables for content, tables in document.text_tables if content == row['text'])
                self.assertEqual(len(tables), 2 if row['role'] == 'user' else 0)

    def test_later_success_does_not_hide_an_earlier_interrupted_message_marker(self):
        rows = [{'role': 'assistant', 'text': 'Earlier partial answer', 'status': 'interrupted'},
                {'role': 'user', 'text': 'Next request', 'status': 'complete'},
                {'role': 'assistant', 'text': 'Later complete answer', 'status': 'complete'}]
        text = ''.join(Document(render_chat_transcript(rows, message_times=['10:20', '10:21', '10:22'])).text)
        self.assertEqual(text.count('已停止'), 1)
        self.assertLess(text.index('已停止'), text.index('Earlier partial answer'))
        self.assertNotIn('AI 10:22 已停止', text)


if __name__ == "__main__":
    unittest.main()
