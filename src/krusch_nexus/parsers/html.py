"""
src/krusch_nexus/parsers/html.py
================================
Clean HTML parser using Python stdlib HTMLParser, stripping scripts and preserving table structure.
"""

import re
from html.parser import HTMLParser
from typing import List, Optional

from ..models import PageData, ParserResult, StructuredLocator


class _HTMLTextExtractor(HTMLParser):
    """Clean HTML text extractor converting semantic tags to Markdown and stripping scripts."""
    def __init__(self):
        super().__init__()
        self.result: List[str] = []
        self._current_tag: Optional[str] = None
        self._skip_depth = 0
        self._heading_level = 0
        self._in_table_cell = False
        self._table_row: List[str] = []

    def handle_starttag(self, tag: str, attrs):
        t = tag.lower()
        self._current_tag = t
        if t in ["script", "style", "head", "noscript", "svg", "iframe"]:
            self._skip_depth += 1
        elif t in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            self._heading_level = int(t[1])
            self.result.append(f"\n\n{'#' * self._heading_level} ")
        elif t in ["p", "div"]:
            self.result.append("\n\n")
        elif t == "br":
            self.result.append("\n")
        elif t == "li":
            self.result.append("\n- ")
        elif t == "tr":
            self._table_row = []
        elif t in ["td", "th"]:
            self._in_table_cell = True

    def handle_endtag(self, tag: str):
        t = tag.lower()
        if t in ["script", "style", "head", "noscript", "svg", "iframe"]:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif t in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            self._heading_level = 0
            self.result.append("\n\n")
        elif t in ["p", "div"]:
            self.result.append("\n\n")
        elif t in ["td", "th"]:
            self._in_table_cell = False
        elif t == "tr":
            if self._table_row:
                self.result.append("\n| " + " | ".join(self._table_row) + " |")

    def handle_data(self, data: str):
        if self._skip_depth > 0:
            return
        text = data.strip()
        if not text:
            return
        if self._in_table_cell:
            self._table_row.append(text)
        else:
            self.result.append(text + " ")

    def get_text(self) -> str:
        raw = "".join(self.result)
        raw = re.sub(r'[ \t]+', ' ', raw)
        raw = re.sub(r'\n{3,}', '\n\n', raw)
        return raw.strip()


def extract_html_text(html_content: str) -> str:
    """Extract clean readable text from HTML string with script tags stripped."""
    parser = _HTMLTextExtractor()
    parser.feed(html_content)
    return parser.get_text()


def parse_html(
    file_path: str,
    filename: str,
    file_hash: Optional[str] = None,
    detected_mime: Optional[str] = None
) -> ParserResult:
    """Parse HTML documents using Python stdlib HTMLParser."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()

    clean_text = extract_html_text(html)
    pages = [PageData(
        index=None,
        locator="HTML Document",
        structured_locator=StructuredLocator(kind="heading", page=None, path=["HTML Document"], formatted="HTML Document"),
        text=clean_text,
        digital_text=clean_text,
        char_count=len(clean_text)
    )]
    return ParserResult(
        filename=filename,
        mime="text/html",
        detected_mime=detected_mime or "text/html",
        file_hash=file_hash or "",
        parser_name="html-stdlib",
        parser_version="html-stdlib@2.0",
        pages=pages
    )
