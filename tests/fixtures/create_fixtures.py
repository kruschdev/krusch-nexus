"""
Generate test fixtures for KruschNexus test suite and citation evaluation.
"""

import os
import zipfile
from PIL import Image, ImageDraw, ImageFont


def create_scanned_pdf(output_path: str):
    """Generate an image-only PDF that requires OCR to extract text."""
    img = Image.new("RGB", (800, 600), color="white")
    draw = ImageDraw.Draw(img)
    # Draw clear text that Tesseract OCR can easily read
    draw.text((50, 50), "EXHIBIT B: SCANNED SETTLEMENT RELEASE", fill="black")
    draw.text((50, 120), "Section 14.1 Liquidated Damages", fill="black")
    draw.text((50, 180), "The parties agree that liquidated damages shall be exactly fifty thousand dollars.", fill="black")
    draw.text((50, 240), "Executed this twenty-second day of September 2026.", fill="black")
    img.save(output_path, "PDF", resolution=150.0)


def create_docx(output_path: str):
    """Generate a valid DOCX file with headings and paragraph structures."""
    xml_data = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p>
          <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
          <w:r><w:t>Section 1: Fleet Information Security Policy</w:t></w:r>
        </w:p>
        <w:p>
          <w:r><w:t>All homelab nodes must operate completely air-gapped without unauthenticated ingress.</w:t></w:r>
        </w:p>
        <w:p>
          <w:pPr><w:pStyle w:val="Heading2"/></w:pPr>
          <w:r><w:t>Section 1.2: Backup Retention Standards</w:t></w:r>
        </w:p>
        <w:p>
          <w:r><w:t>Document archives in .ingested/ must be retained for a minimum of 7 years.</w:t></w:r>
        </w:p>
      </w:body>
    </w:document>
    """
    with zipfile.ZipFile(output_path, "w") as zf:
        zf.writestr("word/document.xml", xml_data)


def create_eml(output_path: str):
    """Generate an RFC822 email file with headers and body."""
    eml_content = (
        "From: general.counsel@krusch.dev\n"
        "To: executive@krusch.dev\n"
        "Subject: Privileged - Acquisition Review Protocol\n"
        "Date: Tue, 22 Sep 2026 10:00:00 -0400\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Dear Executive Team,\n\n"
        "Under Section 4.5 of the Purchase Agreement, closing conditions require regulatory clearance.\n"
        "Please review the attached closing certificate before tomorrow's filing deadline.\n\n"
        "Best regards,\nGeneral Counsel"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(eml_content)


def create_municipal_code(output_path: str):
    """Generate a statutory text file with numbered section headings."""
    content = (
        "OAKLAND MUNICIPAL CODE\n\n"
        "§ 1950.5 Security Deposits and Tenant Protections\n"
        "A landlord may not demand or receive security, however denominated, in an amount or value in excess of\n"
        "one month's rent for an unfurnished residential property.\n\n"
        "Section 8.22.030 Rent Adjustment Program Notice\n"
        "Landlords must provide tenants with written notice of the Rent Adjustment Program at the commencement of tenancy.\n"
        "Failure to serve this notice tolls the statute of limitations on rent disputes.\n\n"
        "Section 8.22.360 Just Cause for Eviction Ordinance\n"
        "A landlord shall not endeavor to recover possession of a rental unit except upon one of the enumerated Just Cause grounds.\n"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)


def create_pdf(output_path: str):
    """Generate multi-page synthetic text PDF."""
    pdf_content = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        b"4 0 obj << /Length 72 >> stream\n"
        b"BT /F1 12 Tf 50 700 Td (COMMERCIAL LEASE AGREEMENT) Tj 0 -30 Td (Section 8.22 Permitted Use of Premises) Tj ET\n"
        b"endstream\nendobj\n"
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"6 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 7 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        b"7 0 obj << /Length 75 >> stream\n"
        b"BT /F1 12 Tf 50 700 Td (Section 19.3 Termination for Breach) Tj 0 -30 Td (Tenant shall cure within thirty days.) Tj ET\n"
        b"endstream\nendobj\n"
        b"xref\n0 8\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000123 00000 n \n0000000274 00000 n \n0000000397 00000 n \n0000000473 00000 n \n0000000624 00000 n \n"
        b"trailer << /Size 8 /Root 1 0 R >>\nstartxref\n750\n%%EOF\n"
    )
    with open(output_path, "wb") as f:
        f.write(pdf_content)


def main():
    target_dir = os.path.dirname(__file__)
    create_scanned_pdf(os.path.join(target_dir, "scanned_page.pdf"))
    create_docx(os.path.join(target_dir, "policy_manual.docx"))
    create_eml(os.path.join(target_dir, "deal_memo.eml"))
    create_municipal_code(os.path.join(target_dir, "municipal_code.txt"))
    create_pdf(os.path.join(target_dir, "sample_contract.pdf"))
    print("All fixtures generated successfully in tests/fixtures/")


if __name__ == "__main__":
    main()
