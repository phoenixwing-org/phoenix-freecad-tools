# SPDX-License-Identifier: Apache-2.0
"""Render in-memory chat messages for Qt rich text without active content or I/O."""
from functools import lru_cache
from html import escape
from html.parser import HTMLParser
import re


class _PftMarkdownFragment(HTMLParser):
    """Keep Qt formatting from Markdown while discarding all resource attributes."""
    _TAGS = frozenset(("p", "br", "pre", "code", "span", "strong", "b", "em", "i", "u",
                      "s", "blockquote", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
                      "hr", "table", "thead", "tbody", "tr", "th", "td"))
    _VOID = frozenset(("br", "hr"))

    def __init__(self, qt_html, fixed_font=None):
        super().__init__()
        self.in_body = False
        self.fixed_font = fixed_font
        self.parts = []
        self.feed(qt_html)

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.in_body = True
        if not self.in_body or tag not in self._TAGS:
            return
        attribute = ""
        if tag == "table":
            attribute = ' width="100%" border="1" cellspacing="0" cellpadding="4"'
        elif tag == "span":
            source = dict(attrs).get("style") or ""
            supported = ("font-weight:600", "font-weight:700", "font-style:italic",
                         "text-decoration: underline", "text-decoration: line-through")
            styles = [style for style in supported if style in source]
            if "font-family:" in source and self.fixed_font:
                styles.append("font-family:'" + self.fixed_font + "'")
            if styles:
                attribute = ' style="' + "; ".join(styles) + '"'
        elif tag == "pre":
            attribute = ' style="white-space:pre-wrap;"'
        self.parts.append("<" + tag + attribute + ">")

    def handle_endtag(self, tag):
        if tag == "body":
            self.in_body = False
        elif self.in_body and tag in self._TAGS and tag not in self._VOID:
            self.parts.append("</" + tag + ">")

    def handle_data(self, value):
        if self.in_body:
            self.parts.append(escape(value))


def make_chat_markdown_formatter(qtgui, block_resource):
    document_type = getattr(qtgui, "QTextDocument", None)
    if not callable(getattr(document_type, "setMarkdown", None)):
        return None
    dialect = getattr(document_type, "MarkdownFeature", document_type)
    try:
        features = dialect.MarkdownDialectGitHub | dialect.MarkdownNoHTML
    except AttributeError:
        return None

    fixed_font = qtgui.QFontDatabase.systemFont(qtgui.QFontDatabase.SystemFont.FixedFont).family()
    if not re.fullmatch(r"[\w .-]{1,128}", fixed_font) or fixed_font.casefold() == "monospace":
        fixed_font = None

    class MarkdownDocument(document_type):
        def loadResource(self, _kind, _url):
            return block_resource()

    @lru_cache(maxsize=16)
    def format_message(text):
        if len(text) > 256 * 1024:
            return ""
        document = MarkdownDocument()
        document.setMarkdown(text, features)
        return "".join(_PftMarkdownFragment(document.toHtml(), fixed_font).parts)

    return format_message


def render_chat_transcript(rows, user_label="你", message_times=(), dark=False,
                           interrupted_label="已停止", error_label="未完成", markdown=None):
    """Use native Qt tables for right-aligned, content-sized user bubbles."""
    foreground, muted = (("#e5e9ef", "#a3adbb") if dark else ("#20252b", "#66717f"))
    user_background = "#29384b" if dark else "#edf3fc"
    parts = ['<html><body style="color: %s; margin: 0;">' % foreground]
    for index, row in enumerate(rows):
        is_user = row["role"] == "user"
        label = escape(user_label if is_user else "AI")
        stamp = escape(message_times[index]) if index < len(message_times) else ""
        outcome = ({"interrupted": interrupted_label, "error": error_label}.get(row.get("status"), "")
                   if not is_user else "")
        metadata = " ".join(value for value in (label, stamp, escape(outcome)) if value)
        text = ('<p align="left" style="margin-top: 0; margin-bottom: 4px; '
                'font-size: small; color: %s;">%s</p>' % (muted, metadata))
        body = markdown(row["text"]) if not is_user and markdown is not None else ""
        text += body or ('<p align="left" style="white-space: pre-wrap; margin-top: 0; margin-bottom: %s;">%s</p>'
                         % ("0" if is_user else "12px", escape(row["text"])))
        if is_user:
            # Qt rich text supports these table widths/alignment. The inner
            # table has no fixed width: short messages shrink to their text,
            # and long messages wrap within the right-hand 80% cell.
            text = (
                '<table width="100%%" cellspacing="0" cellpadding="0"><tr>'
                '<td width="20%%"></td><td width="80%%">'
                '<table align="right" cellspacing="0" cellpadding="8" bgcolor="%s">'
                '<tr><td>%s</td></tr></table></td></tr></table>'
                % (user_background, text))
        parts.append(text + '<p style="margin: 0; font-size: 4pt;"><br></p>')
    parts.append('</body></html>')
    return "".join(parts)
