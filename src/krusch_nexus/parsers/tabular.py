"""
src/krusch_nexus/parsers/tabular.py
===================================
CSV, TSV, and tabular data parser with row-group chunking and replayed headers.
"""

import csv
from typing import List, Optional

from ..models import PageData, ParserResult, StructuredLocator


def parse_csv(
    file_path: str,
    filename: str,
    rows_per_group: int = 30,
    file_hash: Optional[str] = None,
    detected_mime: Optional[str] = None
) -> ParserResult:
    """
    Parse CSV files by grouping rows with header replay, emitting page_number=None
    and locator='Rows X-Y' to maintain citation truth.
    """
    pages: List[PageData] = []
    content = ""
    for enc in ["utf-8", "utf-8-sig", "latin-1"]:
        try:
            with open(file_path, "r", encoding=enc) as f:
                content = f.read()
            break
        except UnicodeDecodeError:
            continue

    reader = list(csv.reader(content.splitlines()))
    if not reader:
        return ParserResult(
            filename=filename,
            mime="text/csv",
            detected_mime=detected_mime or "text/csv",
            file_hash=file_hash or "",
            parser_name="csv-rowgroup",
            parser_version="csv-rowgroup@2.0",
            pages=[PageData(index=None, locator="Empty", text="[Empty CSV]", digital_text="")]
        )

    headers = [c.strip() for c in reader[0]]
    header_line = "| " + " | ".join(headers) + " |"
    divider_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    data_rows = reader[1:]

    if not data_rows:
        table_text = f"{header_line}\n{divider_line}"
        pages.append(PageData(
            index=None,
            locator="Headers Only",
            structured_locator=StructuredLocator(kind="row_range", page=None, path=["Headers Only"], formatted="Headers Only"),
            text=table_text,
            digital_text=table_text,
            char_count=len(table_text)
        ))
    else:
        for idx in range(0, len(data_rows), rows_per_group):
            group = data_rows[idx:idx + rows_per_group]
            start_num = idx + 1
            end_num = idx + len(group)
            loc = f"Rows {start_num}-{end_num}"

            row_lines = [header_line, divider_line]
            for r in group:
                row_lines.append("| " + " | ".join(c.strip() for c in r) + " |")

            group_text = "\n".join(row_lines)
            pages.append(PageData(
                index=None,
                locator=loc,
                structured_locator=StructuredLocator(kind="row_range", page=None, path=[loc], formatted=loc),
                text=group_text,
                digital_text=group_text,
                char_count=len(group_text)
            ))

    return ParserResult(
        filename=filename,
        mime="text/csv",
        detected_mime=detected_mime or "text/csv",
        file_hash=file_hash or "",
        parser_name="csv-rowgroup",
        parser_version="csv-rowgroup@2.0",
        pages=pages
    )
