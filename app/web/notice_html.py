"""Render notice text, turning "a | b | c" lines back into a readable table."""

from __future__ import annotations

import html

from markupsafe import Markup


def notice_to_html(text: str) -> Markup:
    """Notice text as safe HTML: paragraphs for lines, a table for runs of cell lines."""
    blocks: list[str] = []
    table_rows: list[list[str]] = []
    for line in text.splitlines():
        if "|" in line:
            table_rows.append([cell.strip() for cell in line.split("|")])
            continue
        if table_rows:
            blocks.append(_table(table_rows))
            table_rows = []
        if line.strip():
            blocks.append(f"<p>{html.escape(line)}</p>")
    if table_rows:
        blocks.append(_table(table_rows))
    return Markup("".join(blocks))


def _table(rows: list[list[str]]) -> str:
    width = max(len(row) for row in rows)
    body = []
    for row in rows:
        # Rows that continue a merged date cell have one cell fewer, so pad on the left
        cells = [""] * (width - len(row)) + row
        tag = "th" if row is rows[0] else "td"
        body.append(
            "<tr>"
            + "".join(f"<{tag}>{html.escape(cell)}</{tag}>" for cell in cells)
            + "</tr>"
        )
    return '<table class="table boxed notice-table">' + "".join(body) + "</table>"
