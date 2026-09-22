import os
import shutil
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

os.environ["DATABASE_URL"] = "postgresql://kdcode:password@localhost:5432/krusch_nexus_db"
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:11434"
os.environ["OLLAMA_EMBED_HOST"] = "http://127.0.0.1:11434"
os.environ["OLLAMA_EMBED_MODEL"] = "bge-large"
os.environ["OLLAMA_LLM_MODEL"] = "qwen2.5-coder:7b"
os.environ["EMBEDDING_PROVIDER"] = "ollama"

from src.backend.db import SessionLocal, Workspace, Document, DocumentChunk
from src.backend.parsers import (
    parse_document, parse_pdf, parse_docx, parse_eml, parse_html, parse_plain_or_code, compute_file_hash
)
from src.backend.chunking import chunk_document_pages, chunk_llama_documents, deduplicate_chunks
from src.backend.ingest_daemon import process_file
from src.backend.rag_engine import retrieve_hybrid_document_chunks, verify_quote_grounding, query_knowledge_base


import uuid

class TestClosedLoopIngestion(unittest.TestCase):
    """Comprehensive test suite for KruschNexus closed-loop document ingestion and RAG."""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="nexus_ingest_test_")
        cls.ws_name = f"TestWS_{uuid.uuid4().hex[:8]}"
        cls.pdf_path = os.path.join(cls.test_dir, "sample_deal.pdf")
        pdf_content = (
            b"%PDF-1.4\n"
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
            b"4 0 obj << /Length 44 >> stream\n"
            b"BT /F1 12 Tf 100 700 Td (Project Alpha Merger Deal) Tj ET\n"
            b"endstream\nendobj\n"
            b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
            b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000266 00000 n \n0000000360 00000 n \n"
            b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n437\n%%EOF\n"
        )
        with open(cls.pdf_path, "wb") as f:
            f.write(pdf_content)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_01_pdf_parsing_with_page_preservation(self):
        """Verify PDF parser extracts text and preserves 1-based page numbers."""
        docs = parse_document(self.pdf_path, "sample_deal.pdf")
        self.assertGreater(len(docs), 0)
        first_doc = docs[0]
        self.assertEqual(first_doc.metadata["page_number"], 1)
        self.assertEqual(first_doc.metadata["filename"], "sample_deal.pdf")
        self.assertTrue(len(first_doc.metadata["file_hash"]) == 64)
        self.assertIn("Project Alpha Merger Deal", first_doc.text)

    def test_02_docx_parsing(self):
        """Verify DOCX parser parses paragraphs and headings from XML structure."""
        docx_path = os.path.join(self.test_dir, "test_contract.docx")
        # Build a minimal valid DOCX file in memory
        with zipfile.ZipFile(docx_path, "w") as zf:
            xml_data = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p>
                  <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
                  <w:r><w:t>Section 1: Master Services Agreement</w:t></w:r>
                </w:p>
                <w:p>
                  <w:r><w:t>The vendor agrees to deliver software services within 30 business days.</w:t></w:r>
                </w:p>
              </w:body>
            </w:document>
            """
            zf.writestr("word/document.xml", xml_data)

        docs = parse_document(docx_path, "test_contract.docx")
        self.assertEqual(len(docs), 1)
        self.assertIn("Section 1: Master Services Agreement", docs[0].text)
        self.assertIn("30 business days", docs[0].text)

    def test_03_eml_email_parsing(self):
        """Verify RFC822 EML email parser extracts headers and body."""
        eml_path = os.path.join(self.test_dir, "client_email.eml")
        eml_content = (
            "From: counsel@firm.com\r\n"
            "To: client@corp.com\r\n"
            "Subject: Confidential Settlement Offer - Matter 2026-B\r\n"
            "Date: Mon, 22 Sep 2026 12:00:00 -0700\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\n"
            "We have received the revised settlement terms for $250,000.\r\n"
        )
        with open(eml_path, "wb") as f:
            f.write(eml_content.encode("utf-8"))

        docs = parse_document(eml_path, "client_email.eml")
        self.assertEqual(len(docs), 1)
        self.assertIn("Confidential Settlement Offer", docs[0].text)
        self.assertIn("$250,000", docs[0].text)
        self.assertEqual(docs[0].metadata.get("doc_type"), "email")

    def test_04_html_and_csv_parsing(self):
        """Verify HTML and CSV parsing with table structures."""
        csv_path = os.path.join(self.test_dir, "expenses.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("Date,Description,Amount\n2026-09-01,Filing Fee,$435\n2026-09-15,Expert Deposition,$1200\n")

        docs = parse_document(csv_path, "expenses.csv")
        self.assertEqual(len(docs), 1)
        self.assertIn("Filing Fee", docs[0].text)
        self.assertIn("$1200", docs[0].text)

    def test_05_structural_chunking_and_deduplication(self):
        """Verify structural chunker attaches page numbers, heading context, and hashes."""
        docs = parse_document(self.pdf_path, "sample_deal.pdf")
        chunks = chunk_llama_documents(docs)
        self.assertGreater(len(chunks), 0)
        c = chunks[0]
        self.assertEqual(c.page_number, 1)
        self.assertEqual(c.filename, "sample_deal.pdf")
        self.assertTrue(len(c.source_hash) == 64)
        self.assertIn("[sample_deal.pdf - p.1]", c.text)

        # Verify chunk deduplication
        deduped = deduplicate_chunks(chunks + chunks)
        self.assertEqual(len(deduped), len(chunks))

    def test_06_ingest_daemon_safe_archival_and_reporting(self):
        """Verify ingest_daemon archives to .ingested/ and generates an Ingest Report."""
        drop_dir = os.path.join(self.test_dir, "drop_workspace")
        os.makedirs(drop_dir, exist_ok=True)
        test_file = os.path.join(drop_dir, "policy_doc.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("Company Policy Section 4.1: Remote work requires VPN authentication at all times.")

        report = process_file(test_file, self.ws_name, "policy_doc.txt")
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["workspace"], self.ws_name)
        self.assertEqual(report["pages_in"], 1)
        self.assertEqual(report["chunks_out"], 1)

        # Verify source file was archived rather than deleted
        archived_dir = os.path.join(drop_dir, ".ingested")
        self.assertTrue(os.path.exists(archived_dir))
        self.assertFalse(os.path.exists(test_file))  # Original moved, not deleted

        # Verify duplicate drop handling
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("Company Policy Section 4.1: Remote work requires VPN authentication at all times.")
        dup_report = process_file(test_file, self.ws_name, "policy_doc.txt")
        self.assertEqual(dup_report["status"], "skipped_duplicate")

    def test_07_hybrid_search_and_quote_verification(self):
        """Verify hybrid vector + lexical search and verbatim quote verification."""
        db = SessionLocal()
        ws = db.query(Workspace).filter(Workspace.name == self.ws_name).first()
        self.assertIsNotNone(ws)

        # Test hybrid chunk retrieval
        chunks = retrieve_hybrid_document_chunks("VPN authentication remote work", workspace_id=ws.id, limit=3)
        self.assertGreater(len(chunks), 0)
        top_chunk = chunks[0]
        self.assertIn("policy_doc.txt", top_chunk["filename"])
        self.assertIn("VPN authentication", top_chunk["content"])

        # Test quote verification
        valid_response = 'The policy mandates that "Remote work requires VPN authentication at all times."'
        is_grounded, unverified, _ = verify_quote_grounding(valid_response, chunks)
        self.assertTrue(is_grounded)
        self.assertEqual(len(unverified), 0)

        # Test hallucinated quote detection
        hallucinated_response = 'The policy mandates that "All employees must report to the office on Fridays."'
        is_grounded_bad, unverified_bad, advisory = verify_quote_grounding(hallucinated_response, chunks)
        self.assertFalse(is_grounded_bad)
        self.assertIn("All employees must report to the office on Fridays", unverified_bad[0])
        self.assertIn("Verification Advisory", advisory)
        db.close()

    def test_08_scanned_pdf_ocr_fallback(self):
        """Verify scanned PDFs (raster images with no text layer) trigger local Tesseract OCR."""
        from PIL import Image, ImageDraw
        scan_img = Image.new("RGB", (1200, 1600), color="white")
        draw = ImageDraw.Draw(scan_img)
        draw.text((100, 200), "CONFIDENTIAL SETTLEMENT EXHIBIT", fill="black")
        draw.text((100, 300), "Section 9.1: Mutual Release Agreement", fill="black")
        draw.text((100, 400), "Settlement Amount: $85,000 USD", fill="black")

        scan_pdf_path = os.path.join(self.test_dir, "scanned_release.pdf")
        scan_img.save(scan_pdf_path, "PDF", resolution=150.0)

        # Parse document
        docs = parse_document(scan_pdf_path, "scanned_release.pdf")
        self.assertGreater(len(docs), 0)
        self.assertTrue(docs[0].metadata.get("ocr_applied", False))
        self.assertTrue(docs[0].metadata.get("has_images", False))
        self.assertIn("SETTLEMENT", docs[0].text.upper())
        self.assertIn("MUTUAL RELEASE", docs[0].text.upper())


if __name__ == "__main__":
    unittest.main()
