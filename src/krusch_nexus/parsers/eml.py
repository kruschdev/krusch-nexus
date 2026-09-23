"""
src/krusch_nexus/parsers/eml.py
===============================
RFC822 / RFC2047 email message parser with header decoding and attachment sections.
"""

import email
import logging
from email.header import decode_header, make_header
from typing import List, Optional

from ..models import PageData, ParserResult, StructuredLocator
from .html import extract_html_text

logger = logging.getLogger("krusch_nexus.parsers.eml")


def decode_mime_header(header_value: Optional[str]) -> str:
    """Decode RFC2047 MIME encoded-word strings (e.g. =?utf-8?B?...?=)."""
    if not header_value:
        return ""
    try:
        decoded_parts = decode_header(header_value)
        return str(make_header(decoded_parts)).strip()
    except Exception:
        return str(header_value).strip()


def parse_eml(
    file_path: str,
    filename: str,
    file_hash: Optional[str] = None,
    detected_mime: Optional[str] = None
) -> ParserResult:
    """
    Parse RFC822 EML email files, decoding MIME headers, recording Message-ID,
    and capturing attachments as distinct sections without synthetic pages.
    """
    with open(file_path, "rb") as f:
        msg = email.message_from_binary_file(f)

    headers = []
    subj = decode_mime_header(msg.get("Subject"))
    sender = decode_mime_header(msg.get("From"))
    recipient = decode_mime_header(msg.get("To"))
    cc = decode_mime_header(msg.get("Cc"))
    date = decode_mime_header(msg.get("Date"))
    msg_id = decode_mime_header(msg.get("Message-ID"))

    if subj:
        headers.append(f"Subject: {subj}")
    if sender:
        headers.append(f"From: {sender}")
    if recipient:
        headers.append(f"To: {recipient}")
    if cc:
        headers.append(f"Cc: {cc}")
    if date:
        headers.append(f"Date: {date}")
    if msg_id:
        headers.append(f"Message-ID: {msg_id}")

    header_block = "\n".join(headers)
    body_parts = []
    attachment_sections: List[PageData] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdispo = str(part.get("Content-Disposition") or "")
            fname = part.get_filename()

            if fname:
                att_name = decode_mime_header(fname)
                att_payload = part.get_payload(decode=True)
                if att_payload:
                    try:
                        att_text = att_payload.decode("utf-8", errors="replace")
                        if len(att_text.strip()) > 0:
                            attachment_sections.append(PageData(
                                index=None,
                                locator=f"Attachment: {att_name}",
                                structured_locator=StructuredLocator(kind="heading", page=None, path=["Attachment", att_name], formatted=f"Attachment: {att_name}"),
                                text=f"# Attachment: {att_name}\n\n{att_text.strip()}",
                                digital_text=att_text.strip(),
                                char_count=len(att_text)
                            ))
                    except Exception:
                        pass
                continue

            if "attachment" in cdispo:
                continue

            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body_parts.append(payload.decode("utf-8", errors="replace"))
            elif ctype == "text/html" and not body_parts:
                payload = part.get_payload(decode=True)
                if payload:
                    html_str = payload.decode("utf-8", errors="replace")
                    body_parts.append(extract_html_text(html_str))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body_parts.append(payload.decode("utf-8", errors="replace"))

    body_text = "\n\n".join(body_parts).strip()
    full_email = f"{header_block}\n\n---\n\n{body_text}".strip()

    pages = [PageData(
        index=None,
        locator=f"Email: {subj or 'Untitled'}",
        structured_locator=StructuredLocator(kind="heading", page=None, path=["Email", subj or "Untitled"], formatted=f"Email: {subj or 'Untitled'}"),
        text=full_email,
        digital_text=full_email,
        char_count=len(full_email)
    )]
    pages.extend(attachment_sections)

    return ParserResult(
        filename=filename,
        mime="message/rfc822",
        detected_mime=detected_mime or "message/rfc822",
        file_hash=file_hash or "",
        parser_name="eml-rfc822",
        parser_version="eml-rfc822@2.0",
        pages=pages
    )
