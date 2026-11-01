"""Turn a brief's markdown into the memo layout: numbered sections, a facts ledger, footnotes."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import markdown

SECTION_STYLES = {
    "Verdict": "verdict",
    "Key facts": "keyfacts",
    "Risk flags": "risk",
    "Company snapshot": "company",
    "Likely rounds and prep": "prep",
    "Resume advice": "resume",
    "Unknown": "unknown",
}
SOURCE_LINK_PATTERN = re.compile(
    r'\(?<a href="(https?://[^"]+)">source</a>\)?', re.IGNORECASE
)
LINK_PATTERN = re.compile(r"\]\((https?://[^)\s]+)\)")
# Any link or image address that is not a web address (javascript:, data:, ...)
UNSAFE_ADDRESS_PATTERN = re.compile(r'\b(href|src)="(?!https?://)[^"]*"', re.IGNORECASE)
LEDGER_ROW_PATTERN = re.compile(
    r"<li>(?:<strong>)?([^:<]{2,40}):(?:</strong>)?\s*(.*?)</li>", re.DOTALL
)


@dataclass(frozen=True)
class Source:
    number: int
    url: str

    @property
    def domain(self) -> str:
        return urlparse(self.url).netloc.removeprefix("www.")


def brief_sources(text: str) -> list[Source]:
    """Every linked source in the brief, numbered in order of first appearance."""
    urls: list[str] = []
    for url in LINK_PATTERN.findall(text or ""):
        if url not in urls:
            urls.append(url)
    return [Source(number, url) for number, url in enumerate(urls, start=1)]


def brief_to_html(text: str) -> str:
    """Render the brief safely; scraped web text in it must never become live HTML."""
    body = markdown.markdown(html.escape(text or "", quote=False), extensions=["extra"])
    body = UNSAFE_ADDRESS_PATTERN.sub(r'\1="#"', body)
    numbers = {source.url: source.number for source in brief_sources(text)}
    body = SOURCE_LINK_PATTERN.sub(lambda match: _citation(match, numbers), body)
    return "".join(_section_html(part) for part in re.split(r"(?=<h3>)", body))


def _citation(match: re.Match, numbers: dict[str, int]) -> str:
    url = match.group(1)
    number = numbers.get(html.unescape(url), "?")
    return (
        f'<sup class="cite"><a href="{url}" target="_blank" rel="noopener">'
        f"[{number}]</a></sup>"
    )


def _section_html(part: str) -> str:
    if not part.strip():
        return ""
    heading = re.match(r"<h3>(.*?)</h3>", part)
    if heading is None:
        return f'<div class="memo-intro">{part}</div>'
    style = SECTION_STYLES.get(html.unescape(heading.group(1)), "")
    if style == "keyfacts":
        part = LEDGER_ROW_PATTERN.sub(
            r'<li><span class="ledger-key">\1</span><span class="ledger-value">\2</span></li>',
            part,
        )
    return f'<section class="memo-section {style}">{part}</section>'
