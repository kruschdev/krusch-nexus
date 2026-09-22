import os
import time
import json
import shutil
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from .db import SessionLocal, Workspace, Document, DocumentChunk
from .parsers import parse_document, compute_file_hash, ParsedPage
from .chunking import chunk_document_pages, Chunk
from .embeddings import get_embeddings_batch

logger = logging.getLogger("krusch_nexus.ingest_daemon")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Resolve default watch directory: inside container (/app/ingest_watch) or relative to repo
DEFAULT_WATCH_DIR = "/app/ingest_watch" if os.path.exists("/app/ingest_watch") else os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "ingest_watch")
)
WATCH_DIR = os.getenv("WATCH_DIR", DEFAULT_WATCH_DIR)

ALLOWED_EXT = (
    '.pdf', '.docx', '.doc', '.eml', '.msg', '.txt',
    '.md', '.csv', '.json', '.html', '.htm',
    '.py', '.js', '.ts', '.yaml', '.yml', '.sql'
)


def classify_document_type(filename: str, sample_text: str = "") -> str:
    """Classify document into Authority, Work Product, Fact Narrative, or General."""
    combined = (filename + " " + sample_text[:1000]).lower()
    if any(k in combined for k in ["ordinance", "statute", "code", "regulation", "contract", "policy", "sop", "agreement", "lease"]):
        return "authority"
    elif any(k in combined for k in ["memo", "memorandum", "draft", "brief", "transcript", "discovery", "deposition", "motion"]):
        return "work_product"
    elif any(k in combined for k in ["fact", "narrative", "intake", "timeline", "incident", "client", "email", "re:"]):
        return "fact_narrative"
    return "general"


def ingest_file_into_nexus(
    filepath: str,
    workspace_name: str = "General",
    archive_source: bool = True,
    filename: Optional[str] = None
) -> Dict[str, Any]:
    """
    Core closed-loop ingestion engine for a single file into PostgreSQL/pgvector.
    Preserves exact 1-based page numbers, structural headings, computes SHA-256 hashes,
    and returns a standardized Ingest Report.
    """
    if not os.path.exists(filepath):
        return {"status": "error", "error": f"File '{filepath}' not found"}

    if not filename:
        filename = os.path.basename(filepath)
    start_time = time.time()
    db = SessionLocal()

    try:
        # 1. Fetch or create workspace
        workspace = db.query(Workspace).filter(Workspace.name == workspace_name).first()
        if not workspace:
            workspace = Workspace(name=workspace_name, description="Auto-created by ingestion daemon")
            db.add(workspace)
            db.commit()
            db.refresh(workspace)

        # 2. Check Deduplication via File SHA-256
        file_hash = compute_file_hash(filepath)
        existing_doc = db.query(Document).filter(
            Document.workspace_id == workspace.id,
            Document.file_hash == file_hash
        ).first()

        if existing_doc:
            logger.info(f"Duplicate document skipped: '{filename}' (hash: {file_hash[:12]}) in workspace '{workspace_name}'")
            if archive_source:
                # Safely archive duplicate file
                archive_dir = os.path.join(os.path.dirname(filepath), ".ingested")
                os.makedirs(archive_dir, exist_ok=True)
                archive_dest = os.path.join(archive_dir, f"{int(time.time())}_{filename}")
                shutil.move(filepath, archive_dest)

            return {
                "status": "skipped_duplicate",
                "filename": filename,
                "workspace": workspace_name,
                "document_id": existing_doc.id,
                "file_hash": file_hash,
                "message": "Identical file content already indexed."
            }

        # 3. Parse Document into structured pages
        llama_docs = parse_document(filepath, filename)
        if not llama_docs:
            raise ValueError(f"Parser returned 0 pages/content for {filename}")

        # Reconstruct parsed pages for structural chunking
        pages = []
        for d in llama_docs:
            p_num = int(d.metadata.get("page_number") or d.metadata.get("page_label") or 1)
            pages.append(ParsedPage(
                page_number=p_num,
                text=d.text,
                has_images=d.metadata.get("has_images", False),
                ocr_applied=d.metadata.get("ocr_applied", False)
            ))

        total_pages = len(pages)
        ocr_pages = [p.page_number for p in pages if p.ocr_applied]

        # 4. Classify and Structural Chunking
        sample_snippet = "\n".join(p.text for p in pages[:2])[:2000]
        doc_type = classify_document_type(filename, sample_snippet)

        chunks: List[Chunk] = chunk_document_pages(
            pages=pages,
            filename=filename,
            file_hash=file_hash,
            max_chars=2000,
            overlap_chars=150,
            base_metadata={
                "workspace_id": workspace.id,
                "workspace_name": workspace.name,
                "doc_type": doc_type
            }
        )

        # 5. Batch Embeddings via Local Ollama (bge-large 1024d)
        chunk_texts = [c.text for c in chunks]
        embeddings = get_embeddings_batch(chunk_texts)

        # 6. Create Document record in DB
        new_doc = Document(
            filename=filename,
            workspace_id=workspace.id,
            file_hash=file_hash,
            total_pages=total_pages,
            total_chunks=len(chunks),
            doc_type=doc_type
        )
        db.add(new_doc)
        db.commit()
        db.refresh(new_doc)

        # Insert DocumentChunks
        for c, emb in zip(chunks, embeddings):
            chunk_rec = DocumentChunk(
                document_id=new_doc.id,
                workspace_id=workspace.id,
                filename=filename,
                page_number=c.page_number,
                chunk_index=c.chunk_index,
                header=c.header,
                content=c.text,
                source_hash=c.source_hash,
                doc_hash=file_hash,
                doc_type=doc_type,
                embedding=emb if emb else None
            )
            db.add(chunk_rec)

        elapsed_ms = round((time.time() - start_time) * 1000, 2)
        report = {
            "status": "completed",
            "document_id": new_doc.id,
            "filename": filename,
            "workspace": workspace_name,
            "file_hash": file_hash,
            "doc_type": doc_type,
            "pages_in": total_pages,
            "chunks_out": len(chunks),
            "ocr_pages": ocr_pages,
            "duration_ms": elapsed_ms,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        new_doc.ingest_report = json.dumps(report)
        db.commit()

        # Try LlamaIndex vector store ingestion for backward-compatibility if installed
        try:
            from .rag_engine import index_documents
            index_docs = [c.to_llama_document() for c in chunks]
            index_documents(index_docs, {
                "document_id": new_doc.id,
                "workspace_id": workspace.id,
                "workspace_name": workspace.name,
                "filename": filename,
                "doc_type": doc_type,
                "file_hash": file_hash
            })
        except Exception as e:
            logger.debug(f"LlamaIndex optional indexing skipped: {e}")

        # 7. Safe Archival: Move source file to .ingested/ if requested (never delete!)
        if archive_source:
            archive_dir = os.path.join(os.path.dirname(filepath), ".ingested")
            os.makedirs(archive_dir, exist_ok=True)
            archive_dest = os.path.join(archive_dir, f"{int(time.time())}_{filename}")
            shutil.move(filepath, archive_dest)
            logger.info(
                f"Successfully ingested '{filename}' into workspace '{workspace_name}' "
                f"({total_pages} pages, {len(chunks)} chunks, {elapsed_ms}ms). Archived to .ingested/"
            )
        else:
            logger.info(
                f"Successfully ingested '{filename}' into workspace '{workspace_name}' "
                f"({total_pages} pages, {len(chunks)} chunks, {elapsed_ms}ms)."
            )
        return report

    except Exception as e:
        db.rollback()
        logger.error(f"Failed to ingest file '{filename}': {e}", exc_info=True)
        return {
            "status": "failed",
            "filename": filename,
            "workspace": workspace_name,
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    finally:
        db.close()


# Backward compatibility alias
process_file = ingest_file_into_nexus


async def auto_ingest_loop():
    """Continuous background loop monitoring the WATCH_DIR for incoming documents."""
    logger.info(f"Starting KruschNexus Ingest Watch Daemon on: {WATCH_DIR}")
    os.makedirs(WATCH_DIR, exist_ok=True)

    while True:
        try:
            for item in os.listdir(WATCH_DIR):
                if item.startswith('.') or item == ".ingested":
                    continue

                item_path = os.path.join(WATCH_DIR, item)

                if os.path.isdir(item_path):
                    # Subdirectory name represents workspace name
                    workspace_name = item
                    for filename in os.listdir(item_path):
                        if filename.startswith('.') or filename == ".ingested":
                            continue
                        filepath = os.path.join(item_path, filename)
                        if os.path.isfile(filepath) and filename.lower().endswith(ALLOWED_EXT):
                            await asyncio.to_thread(process_file, filepath, workspace_name, filename)
                elif os.path.isfile(item_path) and item.lower().endswith(ALLOWED_EXT):
                    # Direct file in root watch directory maps to 'General' workspace
                    await asyncio.to_thread(process_file, item_path, "General", item)
        except Exception as e:
            logger.error(f"Watch daemon error: {e}")

        await asyncio.sleep(5)  # Poll every 5 seconds for rapid UX response


if __name__ == "__main__":
    asyncio.run(auto_ingest_loop())
