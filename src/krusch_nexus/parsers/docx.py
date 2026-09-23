"""
src/krusch_nexus/parsers/docx.py
================================
Native XML/docx parser extracting heading hierarchies and in-order tables without synthetic page numbers.
"""

import re
import zipfile
import logging
import xml.etree.ElementTree as ET
from typing import List, Optional

from ..models import PageData, ParserResult, StructuredLocator
from ..exceptions import ParseError

logger = logging.getLogger("krusch_nexus.parsers.docx")


def parse_docx(
    file_path: str,
    filename: str,
    file_hash: Optional[str] = None,
    detected_mime: Optional[str] = None
) -> ParserResult:
    """
    Parse DOCX files by reading word/document.xml in single-pass natural order.
    Emits page_number=None, maintaining a hierarchical heading stack.
    """
    pages_data: List[PageData] = []
    warnings: List[str] = []
    heading_stack: List[str] = []

    try:
        with zipfile.ZipFile(file_path, "r") as docx_zip:
            if "word/document.xml" not in docx_zip.namelist():
                raise ParseError("Invalid DOCX: missing word/document.xml")

            xml_content = docx_zip.read("word/document.xml")
            root = ET.fromstring(xml_content)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            body = root.find(f"{{{ns['w']}}}body", ns)

            if body is not None:
                document_elements: List[str] = []

                redline_changes: List[dict] = []
                for child in body:
                    tag = child.tag

                    # Case 1: Paragraph / Heading (<w:p>)
                    if tag == f"{{{ns['w']}}}p":
                        # Track revisions (<w:del> and <w:ins>)
                        for d in child.iter(f"{{{ns['w']}}}del"):
                            del_str = "".join(t.text for t in d.iter(f"{{{ns['w']}}}delText") if t.text)
                            if del_str:
                                redline_changes.append({
                                    "type": "deletion",
                                    "text": del_str,
                                    "author": d.attrib.get(f"{{{ns['w']}}}author")
                                })
                        for ins in child.iter(f"{{{ns['w']}}}ins"):
                            ins_str = "".join(t.text for t in ins.iter(f"{{{ns['w']}}}t") if t.text)
                            if ins_str:
                                redline_changes.append({
                                    "type": "insertion",
                                    "text": ins_str,
                                    "author": ins.attrib.get(f"{{{ns['w']}}}author")
                                })

                        p_style = child.find(f".//{{{ns['w']}}}pStyle", ns)
                        is_heading = False
                        heading_level = 1
                        if p_style is not None:
                            val = p_style.attrib.get(f"{{{ns['w']}}}val", "")
                            if "heading" in val.lower():
                                is_heading = True
                                match = re.search(r'\d+', val)
                                if match:
                                    heading_level = int(match.group(0))

                        texts = [t.text for t in child.iter(f"{{{ns['w']}}}t") if t.text]
                        p_text = "".join(texts).strip()
                        if not p_text:
                            continue

                        if is_heading:
                            clean_h = p_text.strip()
                            idx = max(0, min(heading_level - 1, len(heading_stack)))
                            heading_stack = heading_stack[:idx] + [clean_h]
                            document_elements.append(f"{'#' * min(heading_level, 4)} {clean_h}")
                        else:
                            document_elements.append(p_text)

                    # Case 2: Table (<w:tbl>) in natural document order
                    elif tag == f"{{{ns['w']}}}tbl":
                        table_rows = []
                        for row in child.iter(f"{{{ns['w']}}}tr"):
                            cells = []
                            for cell in row.iter(f"{{{ns['w']}}}tc"):
                                c_texts = [t.text for t in cell.iter(f"{{{ns['w']}}}t") if t.text]
                                cells.append("".join(c_texts).strip())
                            if any(cells):
                                table_rows.append("| " + " | ".join(cells) + " |")

                        if table_rows:
                            if len(table_rows) > 1:
                                col_count = len(table_rows[0].split('|')) - 2
                                divider = "| " + " | ".join(["---"] * max(1, col_count)) + " |"
                                table_rows.insert(1, divider)
                            document_elements.append("\n".join(table_rows))

                full_body = "\n\n".join(document_elements).strip()
                loc = " > ".join(heading_stack) if heading_stack else "General"
                struct_loc = StructuredLocator(kind="heading", page=None, path=list(heading_stack), formatted=loc)
                page_extra = {}
                if redline_changes:
                    page_extra["redline_changes"] = redline_changes
                pages_data.append(PageData(
                    index=None,  # No fake page numbers!
                    locator=loc,
                    structured_locator=struct_loc,
                    text=full_body,
                    digital_text=full_body,
                    char_count=len(full_body),
                    extra=page_extra
                ))

    except Exception as e:
        logger.error(f"Error parsing DOCX {filename}: {e}")
        warnings.append(str(e))
        pages_data = [PageData(index=None, locator="Error", text=f"[Error parsing DOCX: {e}]")]

    if not pages_data:
        pages_data = [PageData(index=None, locator="General", text="[Empty DOCX document]")]

    return ParserResult(
        filename=filename,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        detected_mime=detected_mime or "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_hash=file_hash or "",
        parser_name="docx-xml",
        parser_version="docx-xml@2.0",
        pages=pages_data,
        warnings=warnings
    )
