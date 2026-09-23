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


def create_mixed_pdf(output_path: str):
    """Generate a mixed PDF with both digital text pages and a scanned exhibit page."""
    import subprocess
    target_dir = os.path.dirname(output_path)
    p1 = os.path.join(target_dir, "sample_contract.pdf")
    p2 = os.path.join(target_dir, "scanned_page.pdf")
    if not os.path.exists(p1):
        create_pdf(p1)
    if not os.path.exists(p2):
        create_scanned_pdf(p2)
    subprocess.run(["pdfunite", p1, p2, output_path], check=True)


def create_heldout_fixtures(target_dir: str):
    """Generate held-out documents not used during chunker/retrieval tuning."""
    bylaws_content = (
        "AMENDED AND RESTATED BYLAWS OF KRUSCH ENTERPRISES INC.\n\n"
        "Article I: Stockholders and Governance\n"
        "Section 1.1 Annual Meeting\n"
        "The annual meeting of stockholders shall be held on the third Tuesday of May each calendar year.\n\n"
        "Section 1.2 Special Meetings\n"
        "Special meetings of the stockholders may be called only by the Chairman of the Board or Chief Executive Officer.\n\n"
        "Article II: Board of Directors\n"
        "Section 2.1 Number and Qualifications\n"
        "The Board shall consist of not less than five (5) nor more than nine (9) directors.\n\n"
        "Section 2.4 Quorum and Voting\n"
        "A majority of the total number of authorized directors shall constitute a quorum for the transaction of business."
    )
    with open(os.path.join(target_dir, "heldout_bylaws.txt"), "w", encoding="utf-8") as f:
        f.write(bylaws_content)

    note_content = (
        "SECURED COMMERCIAL PROMISSORY NOTE\n\n"
        "Section 1. Principal and Interest\n"
        "Borrower promises to pay to the order of Lender the principal sum of Two Million Five Hundred Thousand Dollars ($2,500,000).\n"
        "Interest shall accrue on unpaid principal at an annual fixed rate of 6.75% calculated on a 360-day year.\n\n"
        "Section 2. Maturity and Amortization Schedule\n"
        "The entire outstanding balance together with all accrued and unpaid interest shall be due and payable on December 31, 2030.\n\n"
        "Section 3. Events of Default and Acceleration\n"
        "Failure to make any installment within ten (10) calendar days of the due date constitutes an immediate Event of Default.\n"
        "Upon an Event of Default, Lender may declare the entire balance immediately due and payable without presentment."
    )
    with open(os.path.join(target_dir, "heldout_promissory_note.txt"), "w", encoding="utf-8") as f:
        f.write(note_content)


    employment_content = (
        "EXECUTIVE EMPLOYMENT AGREEMENT\n\n"
        "Article 1. Position and Duties\n"
        "Section 1.1 Principal Office and Responsibilities\n"
        "Executive shall serve as Chief Technology Officer reporting exclusively to the Chief Executive Officer.\n\n"
        "Article 2. Compensation and Benefits\n"
        "Section 2.1 Base Salary\n"
        "Employer shall pay Executive an annual base salary of $375,000 payable in semi-monthly installments.\n\n"
        "Section 2.3 Severance Upon Termination Without Cause\n"
        "If Executive is terminated without Cause, Employer shall pay twelve (12) months base salary continuation.\n\n"
        "Article 3. Restrictive Covenants\n"
        "Section 3.1 Non-Competition and Non-Solicitation\n"
        "During the term and for one year thereafter, Executive shall not solicit employees or customers of Employer."
    )
    with open(os.path.join(target_dir, "heldout_employment_agreement.txt"), "w", encoding="utf-8") as f:
        f.write(employment_content)

    lease_amend_content = (
        "FIRST AMENDMENT TO COMMERCIAL LEASE AGREEMENT\n\n"
        "Recital A. Existing Lease Background\n"
        "Landlord and Tenant entered into that certain Commercial Lease dated January 15, 2024.\n\n"
        "Section 1. Expansion Premises\n"
        "Commencing October 1, 2026, the leased premises shall include Suite 400 comprising 4,500 rentable square feet.\n\n"
        "Section 2. Base Rent Adjustment\n"
        "Monthly Base Rent for the Expansion Premises shall be $18,000 per month with 3% annual escalation.\n\n"
        "Section 3. Tenant Improvement Allowance\n"
        "Landlord shall provide a construction allowance of $45.00 per rentable square foot for interior alterations."
    )
    with open(os.path.join(target_dir, "heldout_lease_amendment.txt"), "w", encoding="utf-8") as f:
        f.write(lease_amend_content)

    license_content = (
        "ENTERPRISE SOFTWARE LICENSE AND SERVICE LEVEL AGREEMENT\n\n"
        "Section 1. Grant of License\n"
        "Vendor grants Customer a non-exclusive, perpetual license to deploy the software on up to 50 server nodes.\n\n"
        "Section 2. Service Level Commitments and Penalties\n"
        "Vendor warrants 99.95% monthly service availability. In the event of Priority 1 outages exceeding 30 minutes,\n"
        "Customer shall receive a 10% credit against the annual subscription fee.\n\n"
        "Section 3. Limitation of Liability and Indemnification Cap\n"
        "Except for gross negligence or willful misconduct, total aggregate liability shall not exceed fees paid in prior 12 months."
    )
    with open(os.path.join(target_dir, "heldout_software_license.txt"), "w", encoding="utf-8") as f:
        f.write(license_content)


def create_adversarial_fixtures(target_dir: str):
    """Generate adversarial documents for stress-testing parsers, chunkers, and retrievers."""
    twocolumn = (
        "CALIFORNIA COMMERCIAL CODE - DIVISION 9 SECURED TRANSACTIONS\n\n"
        "| COLUMN A: STATUTORY TEXT | COLUMN B: OFFICIAL COMMENTS |\n"
        "| --- | --- |\n"
        "| Section 9-102. Definitions and Index. | Comment 1: This section provides definitions. |\n"
        "| (a) In this division: | Comment 2: Subsection (a) defines terms. |\n"
        "| (1) 'Accession' means goods that are physically | Comment 3: Goods installed in other goods. |\n"
        "| united with other goods. | Comment 4: Accessions retain identity. |\n"
        "| (2) 'Account' means a right to payment of a monetary | Comment 5: Monetary obligations whether or not |\n"
        "| obligation for property that has been or is to be sold. | earned by performance. |\n"
    )
    with open(os.path.join(target_dir, "adversarial_twocolumn.txt"), "w", encoding="utf-8") as f:
        f.write(twocolumn)

    redline = (
        "SETTLEMENT AGREEMENT (CONFIDENTIAL REDLINE - DRAFT 4)\n\n"
        "Article 3: Mutual Release of Claims\n"
        "3.1 Release by Plaintiff\n"
        "Plaintiff hereby releases Defendant from all claims [DELETED: including unknown claims under Section 1542]\n"
        "[ADDED: provided that this release specifically excludes indemnification obligations under Exhibit D].\n\n"
        "Article 4: Non-Disclosure\n"
        "4.1 Confidential Treatment\n"
        "The settlement consideration [DELETED: of $1,000,000] [ADDED: of $1,250,000] shall be held in strict confidence."
    )
    with open(os.path.join(target_dir, "adversarial_redline.txt"), "w", encoding="utf-8") as f:
        f.write(redline)

    blank_img = Image.new("RGB", (800, 600), color="white")
    blank_path = os.path.join(target_dir, "adversarial_blank_scan.pdf")
    blank_img.save(blank_path, "PDF", resolution=300.0)

    # 1. Real Scan PDF with simulated Fax Transmission header and red FILED stamp
    fax_img = Image.new("RGB", (1200, 1600), color="white")
    f_draw = ImageDraw.Draw(fax_img)
    f_draw.text((40, 20), "FAX TRANSMISSION: 2026-09-22 14:30 EST   FROM: LEGAL DEPT   TO: 555-0199   PAGE 1/1", fill="gray")
    f_draw.line([(30, 45), (1170, 45)], fill="gray", width=2)
    f_draw.text((80, 100), "CONFIDENTIAL SETTLEMENT RELEASE AND COVENANT NOT TO SUE", fill="black")
    f_draw.text((80, 180), "Section 12.4 Indemnification and Defense Obligations", fill="black")
    f_draw.text((80, 240), "Indemnifying party agrees to defend, indemnify, and hold harmless all indemnitees.", fill="black")
    f_draw.text((80, 300), "Section 12.5 Limitation of Liability", fill="black")
    f_draw.text((80, 360), "Total aggregate liability shall not exceed fifty thousand dollars ($50,000).", fill="black")
    # Red FILED / RECEIVED stamp
    f_draw.rectangle([(850, 70), (1120, 170)], outline="red", width=3)
    f_draw.text((870, 90), "RECEIVED & FILED", fill="red")
    f_draw.text((900, 125), "SEP 22 2026", fill="red")
    fax_img.save(os.path.join(target_dir, "adversarial_fax_stamp.pdf"), "PDF", resolution=300.0)

    # 2. Real Two-Column PDF
    col_img = Image.new("RGB", (1400, 1800), color="white")
    c_draw = ImageDraw.Draw(col_img)
    c_draw.text((80, 40), "COMMERCIAL CODE - TWO COLUMN STATUTORY DRAFT", fill="black")
    c_draw.line([(80, 70), (1320, 70)], fill="black", width=2)
    # Column A
    c_draw.text((80, 100), "COLUMN A: STATUTORY TEXT", fill="black")
    c_draw.text((80, 140), "Section 9.102 Definitions", fill="black")
    c_draw.text((80, 180), "Accession means goods physically united with other goods.", fill="black")
    c_draw.text((80, 220), "Account means right to payment of monetary obligation.", fill="black")
    # Vertical dividing line
    c_draw.line([(680, 90), (680, 1700)], fill="gray", width=1)
    # Column B
    c_draw.text((720, 100), "COLUMN B: OFFICIAL COMMENTS", fill="black")
    c_draw.text((720, 140), "Comment 1: Definitions are comprehensive.", fill="black")
    c_draw.text((720, 180), "Comment 2: Accessions retain identity after installation.", fill="black")
    c_draw.text((720, 220), "Comment 3: Monetary rights apply to goods sold.", fill="black")
    col_img.save(os.path.join(target_dir, "adversarial_twocolumn.pdf"), "PDF", resolution=300.0)

    # 3. Real Redline PDF with strikethroughs and revisions
    rl_img = Image.new("RGB", (1200, 1600), color="white")
    r_draw = ImageDraw.Draw(rl_img)
    r_draw.text((80, 50), "SETTLEMENT AGREEMENT (CONFIDENTIAL REDLINE)", fill="black")
    r_draw.text((80, 120), "Article 3: Mutual Release of Claims", fill="black")
    r_draw.text((80, 180), "Section 3.1 Plaintiff Release", fill="black")
    r_draw.text((80, 230), "Plaintiff releases Defendant from all known and unknown claims", fill="black")
    r_draw.line([(80, 238), (700, 238)], fill="red", width=2)  # Strikethrough
    r_draw.text((80, 270), "[ADDED: provided that indemnification under Exhibit D is retained]", fill="blue")
    r_draw.text((80, 340), "Section 4.1 Settlement Consideration", fill="black")
    r_draw.text((80, 390), "The settlement consideration is $1,000,000", fill="black")
    r_draw.line([(340, 398), (470, 398)], fill="red", width=2)  # Strikethrough
    r_draw.text((500, 390), "[ADDED: $1,250,000]", fill="blue")
    rl_img.save(os.path.join(target_dir, "adversarial_redline.pdf"), "PDF", resolution=300.0)


def main():
    target_dir = os.path.dirname(__file__)
    create_scanned_pdf(os.path.join(target_dir, "scanned_page.pdf"))
    create_docx(os.path.join(target_dir, "policy_manual.docx"))
    create_eml(os.path.join(target_dir, "deal_memo.eml"))
    create_municipal_code(os.path.join(target_dir, "municipal_code.txt"))
    create_vendor_matrix(os.path.join(target_dir, "vendor_matrix.csv"))
    create_pdf(os.path.join(target_dir, "sample_contract.pdf"))
    create_encrypted_pdf(os.path.join(target_dir, "encrypted_sample.pdf"))
    create_mixed_pdf(os.path.join(target_dir, "mixed_sample.pdf"))
    create_negative_fixtures(target_dir)
    create_heldout_fixtures(target_dir)
    create_adversarial_fixtures(target_dir)
    print("All fixtures generated successfully in tests/fixtures/")


if __name__ == "__main__":
    main()
