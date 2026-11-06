# SPDX-License-Identifier: Apache-2.0
"""Render in-memory chat messages for Qt rich text without active content or I/O."""
from html import escape


def render_chat_transcript(rows, user_label="你", message_times=(), dark=False,
                           interrupted_label="已停止", error_label="未完成"):
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
        text = (
            '<p align="left" style="margin-top: 0; margin-bottom: 4px; '
            'font-size: small; color: %s;">%s</p>'
            '<p align="left" style="white-space: pre-wrap; margin-top: 0; margin-bottom: %s;">%s</p>'
            % (muted, metadata, "0" if is_user else "12px", escape(row["text"])))
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
