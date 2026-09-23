"""
Generate test fixtures for KruschNexus test suite and citation evaluation.
Includes:
- Multi-page contract PDF (sample_contract.pdf)
- Scanned settlement release PDF with OCR text (scanned_page.pdf)
- Policy DOCX with an embedded table in the middle of a section (policy_manual.docx)
- Privileged deal memo EML with RFC2047 MIME encoded headers (deal_memo.eml)
- Municipal code text with § 1950.5 and Section 8.22.030 statutory tokens (municipal_code.txt)
- Tabular vendor spend matrix (vendor_matrix.csv)
- Negative fixtures: 0-byte file, invalid extension, html with script tags
"""

import os
import base64
import zipfile
from PIL import Image, ImageDraw


def create_scanned_pdf(output_path: str):
    """Generate an image-only PDF that requires OCR to extract text."""
    img = Image.new("RGB", (1000, 750), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((60, 60), "EXHIBIT B: SCANNED SETTLEMENT RELEASE", fill="black")
    draw.text((60, 140), "Section 14.1 Liquidated Damages", fill="black")
    draw.text((60, 200), "The parties agree that liquidated damages shall be exactly fifty thousand dollars.", fill="black")
    draw.text((60, 260), "Executed this twenty-second day of September 2026.", fill="black")
    img.save(output_path, "PDF", resolution=300.0)


def create_docx(output_path: str):
    """Generate a valid DOCX file with headings, paragraphs, and a table in the middle of a section."""
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
          <w:r><w:t>Document archives in .ingested/ must be retained according to the following schedule:</w:t></w:r>
        </w:p>
        <w:tbl>
          <w:tr>
            <w:tc><w:p><w:r><w:t>Document Class</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Minimum Retention Period</w:t></w:r></w:p></w:tc>
          </w:tr>
          <w:tr>
            <w:tc><w:p><w:r><w:t>Privileged Corporate Paper</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Seven (7) Years</w:t></w:r></w:p></w:tc>
          </w:tr>
        </w:tbl>
        <w:p>
          <w:r><w:t>Purging of records prior to the expiration of seven years constitutes a policy violation.</w:t></w:r>
        </w:p>
      </w:body>
    </w:document>
    """
    with zipfile.ZipFile(output_path, "w") as zf:
        zf.writestr("word/document.xml", xml_data)


def create_eml(output_path: str):
    """Generate an RFC822 email file with MIME encoded headers and acquisition review text."""
    raw_subject = "Privileged - Acquisition Review Protocol"
    encoded_subject = "=?utf-8?B?" + base64.b64encode(raw_subject.encode("utf-8")).decode("ascii") + "?="

    raw_from = "General Counsel <general.counsel@krusch.dev>"
    encoded_from = "=?utf-8?B?" + base64.b64encode("General Counsel".encode("utf-8")).decode("ascii") + "?= <general.counsel@krusch.dev>"

    eml_content = (
        f"From: {encoded_from}\n"
        f"To: executive@krusch.dev\n"
        f"Subject: {encoded_subject}\n"
        "Date: Tue, 22 Sep 2026 10:00:00 -0400\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Dear Executive Team,\n\n"
        "Under Section 4.5 of the Purchase Agreement, acquisition review protocol requires regulatory clearance.\n"
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


def create_vendor_matrix(output_path: str):
    """Generate CSV vendor spend matrix."""
    csv_content = (
        "Vendor Name,Category,SLA Response Hours,Annual Spend,Contact\n"
        "Acme Logistics,Shipping,24,120000,ops@acme.com\n"
        "Apex Cloud,Infrastructure,1,450000,support@apexcloud.io\n"
        "Lexicon Legal,Counsel,4,220000,billing@lexicon.law\n"
        "ByteSafe Systems,Security,2,95000,soc@bytesafe.org\n"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(csv_content)


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


def create_encrypted_pdf(output_path: str):
    """Generate synthetic encrypted PDF to test fail-closed validation."""
    encrypted_pdf_content = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >> endobj\n"
        b"4 0 obj << /Length 10 >> stream\nENCRYPTED!\nendstream\nendobj\n"
        b"5 0 obj << /Filter /Standard /V 2 /R 3 /U (user_pass) /P -60 >> endobj\n"
        b"trailer << /Size 6 /Root 1 0 R /Encrypt 5 0 R >>\nstartxref\n250\n%%EOF\n"
    )
    with open(output_path, "wb") as f:
        f.write(encrypted_pdf_content)


def create_negative_fixtures(target_dir: str):
    """Generate negative test files."""
    # 0-byte file
    with open(os.path.join(target_dir, "empty_file.txt"), "w") as f:
        pass

    # Invalid extension
    with open(os.path.join(target_dir, "malicious_payload.exe"), "wb") as f:
        f.write(b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff")

    # HTML with script tags
    html_with_script = (
        "<!DOCTYPE html><html><head><script>alert('pwned'); document.cookie='secret';</script></head>"
        "<body><h1>Corporate Governance</h1><p>Legitimate content here.</p>"
        "<script>console.log('leaked');</script></body></html>"
    )
    with open(os.path.join(target_dir, "script_injection.html"), "w", encoding="utf-8") as f:
        f.write(html_with_script)


def main():
    target_dir = os.path.dirname(__file__)
    create_scanned_pdf(os.path.join(target_dir, "scanned_page.pdf"))
    create_docx(os.path.join(target_dir, "policy_manual.docx"))
    create_eml(os.path.join(target_dir, "deal_memo.eml"))
    create_municipal_code(os.path.join(target_dir, "municipal_code.txt"))
    create_vendor_matrix(os.path.join(target_dir, "vendor_matrix.csv"))
    create_pdf(os.path.join(target_dir, "sample_contract.pdf"))
    create_encrypted_pdf(os.path.join(target_dir, "encrypted_sample.pdf"))
    create_negative_fixtures(target_dir)
    print("All fixtures generated successfully in tests/fixtures/")


if __name__ == "__main__":
    main()
