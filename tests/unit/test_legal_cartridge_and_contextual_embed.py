"""
Unit & regression tests for LegalBench-RAG x KruschNexus empirically discovered remediations:
1. FTS5 boolean/proximity operator escaping (`AND`, `OR`, `NOT`, `NEAR`, punctuation) in `legal_cartridge.py`
2. `ORDER BY bm25(chunks_fts) ASC` candidate pool ranking eliminating insertion-order starvation (>300 chunks)
3. Contextual `Document` + `Section` hierarchy embedding formatting (`format_chunk_for_embedding`) without
   mutating `Chunk.text`, `Chunk.raw_text`, `Chunk.source_hash`, or stored `DocumentChunk.content`
"""

import json
import struct
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from krusch_nexus import (
    DocType,
    LegalAuthorityCartridge,
    NexusConfig,
    PageData,
    build_safe_fts5_match_query,
    chunk_document_pages,
    format_chunk_for_embedding,
)
from krusch_nexus.chunking import compute_chunk_hash
from krusch_nexus.embeddings import generate_deterministic_vector
from krusch_nexus.ingest.pipeline import IngestPipeline
from krusch_nexus.parsers import ParsedPage
from krusch_nexus.store import DocumentChunk, get_engine, get_session_factory, init_db


def _pack_vec(vec):
    return struct.pack(f"{len(vec)}f", *vec)


def _create_test_cartridge(db_path: Path, three_col_fts: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE cartridge_manifest (
            pack_id TEXT PRIMARY KEY,
            embed_model TEXT,
            embed_dim INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO cartridge_manifest VALUES ('test-pack', 'snowflake-arctic-embed2:latest', 1024);"
    )
    conn.execute(
        """
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            source_table TEXT,
            title TEXT,
            jurisdiction TEXT,
            court TEXT,
            decision_year INTEGER
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT,
            chunk_index INTEGER,
            citation TEXT,
            heading_path TEXT,
            locator TEXT,
            text TEXT,
            source_hash TEXT,
            char_start INTEGER,
            char_end INTEGER,
            embedding BLOB
        );
        """
    )
    if three_col_fts:
        conn.execute(
            "CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, citation, text);"
        )
    else:
        conn.execute(
            "CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, doc_id, citation, heading_path, text);"
        )
    conn.commit()
    return conn


class TestLegalCartridgeAndContextualEmbed(unittest.TestCase):

    def test_build_safe_fts5_match_query_quotes_operators_and_strips_syntax(self):
        """Uppercase AND/OR/NOT/NEAR and FTS5 punctuation must never appear unquoted."""
        q1 = build_safe_fts5_match_query('Under Section 4 AND Section 5 OR "Confidential" (a)(1)*: - NOT NEAR?')
        self.assertIn('"section"', q1)
        self.assertIn('"confidential"', q1)
        self.assertIn('"4"', q1)
        self.assertIn('"5"', q1)
        # Every operand between ` OR ` separators must be wrapped in double quotes
        parts = q1.split(" OR ")
        self.assertGreaterEqual(len(parts), 3)
        for part in parts:
            self.assertTrue(part.startswith('"') and part.endswith('"'), f"Unquoted FTS5 token: {part}")

        # Stopword-only uppercase operator query falls back safely to quoted lowercase literals
        q2 = build_safe_fts5_match_query("AND OR NOT NEAR")
        self.assertEqual(q2, '"and" OR "or" OR "not" OR "near"')

        # Punctuation-only query returns empty string so search() skips MATCH cleanly
        self.assertEqual(build_safe_fts5_match_query("§ ??? --- ()"), "")
        self.assertEqual(build_safe_fts5_match_query(""), "")

    def test_cartridge_search_survives_adversarial_fts5_queries_across_schemas(self):
        """Both 5-column and 3-column chunks_fts schemas execute adversarial queries with zero syntax crashes."""
        adversarial_queries = [
            "Under Section 4 AND Section 5, what happens?",
            "Shall NOT exceed 30 days OR NEAR termination",
            "AND OR NOT NEAR",
            'What does "Material Adverse Effect" (MAE)* mean: [Section 8.02]?',
            "§ ??? ---",
            "",
        ]
        for three_col in (False, True):
            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = Path(tmpdir) / f"cartridge_{'3col' if three_col else '5col'}.sqlite"
                conn = _create_test_cartridge(db_path, three_col_fts=three_col)
                vec = generate_deterministic_vector("Material Adverse Effect termination notice", dim=1024)
                conn.execute(
                    "INSERT INTO documents VALUES ('d1', 'cases', 'Acme v. Beta', 'Delaware', 'Chancery', 2025);"
                )
                conn.execute(
                    "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                    (
                        "d1_c0",
                        "d1",
                        0,
                        "Acme v. Beta § 8.02",
                        json.dumps(["Section 8.02"]),
                        "Page 1",
                        "Material Adverse Effect termination notice within 30 days.",
                        "h1",
                        0,
                        58,
                        _pack_vec(vec),
                    ),
                )
                if three_col:
                    conn.execute(
                        "INSERT INTO chunks_fts VALUES ('d1_c0', 'Acme v. Beta § 8.02', 'Material Adverse Effect termination notice within 30 days.');"
                    )
                else:
                    conn.execute(
                        "INSERT INTO chunks_fts VALUES ('d1_c0', 'd1', 'Acme v. Beta § 8.02', 'Section 8.02', 'Material Adverse Effect termination notice within 30 days.');"
                    )
                conn.commit()
                conn.close()

                cartridge = LegalAuthorityCartridge(db_path=db_path)
                with patch.object(
                    LegalAuthorityCartridge,
                    "_embed_query",
                    side_effect=lambda q: generate_deterministic_vector(q or "fallback", dim=1024),
                ):
                    for q in adversarial_queries:
                        hits = cartridge.search(q, limit=3)
                        self.assertGreaterEqual(len(hits), 1, f"Expected >=1 hit for query={q!r}")
                        self.assertEqual(hits[0]["chunk_id"], "d1_c0")

    def test_cartridge_bm25_order_prevents_multi_document_insertion_starvation(self):
        """
        Regression test for the 0.25% F1 starvation bug:
        Insert 400 chunks across 10 documents where docs 1..9 (360 chunks > candidate_pool_size=300)
        contain generic boilerplate words ('agreement', 'party', 'notice') and doc 10 (at row 399!)
        contains the specific target clause ('quantum indemnification escrow holdback').
        `ORDER BY bm25(chunks_fts) ASC` must surface `doc_10` at rank #1!
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "multi_doc_starvation.sqlite"
            conn = _create_test_cartridge(db_path, three_col_fts=False)

            docs = []
            chunks = []
            fts_rows = []
            generic_vec = generate_deterministic_vector("generic agreement party notice boilerplate", dim=1024)
            target_text = (
                "The agreement party notice requires a quantum indemnification escrow holdback "
                "of twelve million dollars at closing."
            )
            target_vec = generate_deterministic_vector(target_text, dim=1024)

            for d_idx in range(1, 11):
                doc_id = f"doc_{d_idx}"
                jur = "California" if d_idx == 10 else "New York"
                docs.append((doc_id, "contracts", f"Agreement {d_idx}", jur, "Superior", 2026))
                for c_idx in range(40):
                    chunk_id = f"{doc_id}_c{c_idx}"
                    is_target = d_idx == 10 and c_idx == 38  # Row 399 of 400 (well past LIMIT 300!)
                    txt = target_text if is_target else f"Standard agreement party notice clause filler {d_idx}_{c_idx}."
                    vec = target_vec if is_target else generic_vec
                    cit = f"Agreement {d_idx} § {c_idx}"
                    chunks.append(
                        (
                            chunk_id,
                            doc_id,
                            c_idx,
                            cit,
                            json.dumps([f"Section {c_idx}"]),
                            "Page 1",
                            txt,
                            f"hash_{chunk_id}",
                            0,
                            len(txt),
                            _pack_vec(vec),
                        )
                    )
                    fts_rows.append((chunk_id, doc_id, cit, f"Section {c_idx}", txt))

            conn.executemany("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?);", docs)
            conn.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);", chunks)
            conn.executemany("INSERT INTO chunks_fts VALUES (?, ?, ?, ?, ?);", fts_rows)
            conn.commit()
            conn.close()

            cartridge = LegalAuthorityCartridge(db_path=db_path)
            query = "What is the quantum indemnification escrow holdback notice in the agreement?"
            with patch.object(
                LegalAuthorityCartridge,
                "_embed_query",
                side_effect=lambda q: generate_deterministic_vector(q, dim=1024),
            ):
                hits = cartridge.search(query, limit=5, candidate_pool_size=300)
                self.assertEqual(hits[0]["chunk_id"], "doc_10_c38")
                self.assertEqual(hits[0]["doc_id"], "doc_10")

                # Filtered search (jurisdiction + source_table) also ranks cleanly
                cal_hits = cartridge.search(
                    query,
                    limit=3,
                    jurisdiction="California",
                    source_table="contracts",
                    candidate_pool_size=50,
                )
                self.assertEqual(cal_hits[0]["chunk_id"], "doc_10_c38")

    def test_format_chunk_for_embedding_preserves_raw_text_invariant_and_pipeline_grounding(self):
        """
        `format_chunk_for_embedding` must prepend clean Document + Section hierarchy for vector
        generation WITHOUT mutating `chunk.text`, `chunk.source_hash`, or DB `DocumentChunk.content`.
        """
        page = ParsedPage(
            page_number=7,
            text="Article IV: Data Governance\nSection 8.22: Personal data shall be erased within thirty days.",
        )
        chunks = chunk_document_pages(
            pages=[page],
            filename="Privacy_Policies/Fiverr_Policy.pdf",
            file_hash="hash_fiverr_001",
            max_chars=1000,
            doc_type=DocType.AUTHORITY,
        )
        self.assertEqual(len(chunks), 2)
        for ch in chunks:
            # 1. Raw text & source_hash invariant strictly preserved on every chunk
            self.assertEqual(ch.text, ch.raw_text)
            self.assertNotIn("Fiverr_Policy.pdf", ch.text)
            self.assertNotIn("Document:", ch.text)
            self.assertNotIn("p.7", ch.text)
            self.assertEqual(ch.source_hash, compute_chunk_hash(ch.raw_text))

        c = chunks[1]
        # 2. Contextual embedding string includes clean Document + multi-level Section (filtering 'Page 7')
        contextual = format_chunk_for_embedding(c)
        self.assertTrue(contextual.startswith("Document: Privacy Policies Fiverr Policy\nSection: "))
        section_line = contextual.split("\n")[1]
        self.assertNotIn("Page 7", section_line)
        self.assertIn("Article IV: Data Governance", section_line)
        self.assertIn("Section 8.22", section_line)
        self.assertTrue(contextual.endswith(c.raw_text))
        self.assertEqual(c.format_for_embedding(), contextual)

        # 3. End-to-end IngestPipeline stores raw prose in DocumentChunk.content while embedding contextually
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            db_file = tmp_root / "pipeline_ctx.sqlite"
            doc_a = tmp_root / "Fiverr_Privacy_Policy.txt"
            doc_b = tmp_root / "Acme_Privacy_Policy.txt"
            shared_clause = "Section 9.1: The Company shall purge customer telemetry logs after ninety days."
            doc_a.write_text(
                f"Section 1.1: Marketplace introduction.\n\n{shared_clause}",
                encoding="utf-8",
            )
            doc_b.write_text(
                f"Section 1.1: Hardware catalog introduction.\n\n{shared_clause}",
                encoding="utf-8",
            )

            cfg = NexusConfig(
                database_url=f"sqlite:///{db_file}",
                embedding_provider="dummy",
                embed_backend="dummy",
                allowed_ingest_roots=[str(tmp_root)],
                contextual_embeddings=True,
            )
            engine = get_engine(cfg.database_url)
            init_db(engine)
            pipeline = IngestPipeline(config=cfg, engine=engine)

            rep_a = pipeline.process_file(
                str(doc_a),
                workspace_name="legal_ctx",
                doc_type=DocType.AUTHORITY,
                archive_source=False,
            )
            rep_b = pipeline.process_file(
                str(doc_b),
                workspace_name="legal_ctx",
                doc_type=DocType.AUTHORITY,
                archive_source=False,
            )
            self.assertEqual(rep_a.status, "completed")
            self.assertEqual(rep_b.status, "completed")

            Session = get_session_factory(engine)
            with Session() as db:
                retention_chunks = (
                    db.query(DocumentChunk)
                    .filter(DocumentChunk.content.like("%purge customer telemetry logs%"))
                    .order_by(DocumentChunk.id.asc())
                    .all()
                )
                self.assertEqual(len(retention_chunks), 2)
                # Stored DB prose remains raw text (identical content, no 'Document:' wrapper leaked)
                self.assertEqual(retention_chunks[0].content, retention_chunks[1].content)
                for sc in retention_chunks:
                    self.assertNotIn("Document:", sc.content)
                    self.assertIn("purge customer telemetry logs", sc.content)

                vec_fiverr = (
                    json.loads(retention_chunks[0].embedding)
                    if isinstance(retention_chunks[0].embedding, str)
                    else list(retention_chunks[0].embedding)
                )
                vec_acme = (
                    json.loads(retention_chunks[1].embedding)
                    if isinstance(retention_chunks[1].embedding, str)
                    else list(retention_chunks[1].embedding)
                )
                # Identical boilerplate clauses in different documents now produce distinct contextual vectors!
                self.assertNotEqual(vec_fiverr, vec_acme)

                q_fiverr = generate_deterministic_vector("Fiverr customer telemetry logs retention", dim=1024)
                sim_fiverr = sum(a * b for a, b in zip(q_fiverr, vec_fiverr))
                sim_acme = sum(a * b for a, b in zip(q_fiverr, vec_acme))
                self.assertGreater(sim_fiverr, sim_acme)


if __name__ == "__main__":
    unittest.main()
