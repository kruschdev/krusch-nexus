"""
KruschNexus Command-Line Interface (CLI)
========================================
Unified entrypoint for offline document ingestion, hybrid corpus retrieval,
watch daemon lifecycle, FastMCP server, and the 'nexus doctor' environment audit.

Usage:
    nexus search "liquidated damages" --workspace Matter_Smith
    nexus ingest /data/lease.pdf --workspace Matter_Smith
    nexus doctor
    nexus daemon
    nexus mcp
"""

import os
import sys
import json
import shutil
import socket
import argparse
import subprocess
from urllib.parse import urlparse
from typing import Optional, Dict, Any, List

from .client import NexusClient
from .models import NexusConfig, DocType


def check_binary(bin_name: str) -> bool:
    """Check if binary exists in system PATH."""
    return shutil.which(bin_name) is not None


def probe_socket(host: str, port: int, timeout_sec: float = 0.5) -> bool:
    """Attempt a low-timeout TCP socket probe."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout_sec)
        sock.connect((host, port))
        sock.close()
        return True
    except Exception:
        return False


def run_doctor_checks(config: Optional[NexusConfig] = None) -> Dict[str, Any]:
    """
    Execute exhaustive diagnostic checks verifying:
    1. Poppler utilities (pdftotext, pdftoppm, pdfinfo)
    2. Tesseract OCR with English model data
    3. PostgreSQL connectivity & pgvector extension
    4. Ollama host connectivity & embedding model availability
    5. Air-gap internet egress boundary
    """
    conf = config or NexusConfig.from_env()
    results = {}
    passed = True

    # 1. Poppler binaries
    poppler_ok = check_binary("pdftotext") and check_binary("pdftoppm") and check_binary("pdfinfo")
    results["poppler"] = {
        "status": "PASS" if poppler_ok else "FAIL",
        "pdftotext": check_binary("pdftotext"),
        "pdftoppm": check_binary("pdftoppm"),
        "pdfinfo": check_binary("pdfinfo"),
    }
    if not poppler_ok:
        passed = False

    # 2. Tesseract binary & traineddata
    tess_ok = check_binary("tesseract")
    tess_lang_ok = False
    if tess_ok:
        try:
            res = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=5.0)
            tess_lang_ok = "eng" in res.stdout
        except Exception:
            pass

    results["tesseract"] = {
        "status": "PASS" if (tess_ok and tess_lang_ok) else ("WARN" if tess_ok else "FAIL"),
        "binary": tess_ok,
        "eng_model": tess_lang_ok
    }
    if not tess_ok:
        passed = False

    # 3. PostgreSQL & pgvector extension
    db_ok = False
    pgvector_ok = False
    try:
        from .store import get_engine
        from sqlalchemy import text
        engine = get_engine(conf.database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
            if engine.dialect.name == "postgresql":
                ext = conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).fetchone()
                pgvector_ok = bool(ext)
            else:
                pgvector_ok = True  # SQLite test/dev dialect
    except Exception as e:
        results["database_error"] = str(e)

    results["database"] = {
        "status": "PASS" if (db_ok and pgvector_ok) else "FAIL",
        "connected": db_ok,
        "pgvector": pgvector_ok,
        "url_scheme": conf.database_url.split("://")[0] if "://" in conf.database_url else "unknown"
    }
    if not (db_ok and pgvector_ok):
        passed = False

    # 4. Ollama host & model pull
    ollama_ok = False
    model_pulled = False
    try:
        import httpx
        with httpx.Client(timeout=4.0) as client:
            resp = client.get(f"{conf.ollama_url}/api/tags")
            if resp.status_code == 200:
                ollama_ok = True
                models = [m.get("name", "") for m in resp.json().get("models", [])]
                model_pulled = any(conf.embed_model in m for m in models)
    except Exception as e:
        results["ollama_error"] = str(e)

    results["ollama"] = {
        "status": "PASS" if (ollama_ok and model_pulled) else ("WARN" if ollama_ok else "FAIL"),
        "host_reachable": ollama_ok,
        "model_available": model_pulled,
        "target_model": conf.embed_model,
        "url": conf.ollama_url
    }
    if not ollama_ok:
        passed = False

    # 5. Air-gap internet probe check
    external_probes = [("8.8.8.8", 53), ("1.1.1.1", 53), ("example.com", 443)]
    leaked = []
    for host, port in external_probes:
        if probe_socket(host, port):
            leaked.append(f"{host}:{port}")

    results["air_gap"] = {
        "status": "PASS" if len(leaked) == 0 else "WARN",
        "air_gap_secure": len(leaked) == 0,
        "leaked_probes": leaked
    }

    results["healthy"] = passed
    return results


def cmd_doctor(args):
    """Execute 'nexus doctor' environment and dependency diagnostics."""
    print("=" * 60)
    print("  KruschNexus Doctor — Environment & Infrastructure Audit")
    print("=" * 60)

    results = run_doctor_checks()

    # Pretty print status
    def _status_fmt(st: str) -> str:
        if st == "PASS":
            return "[OK]"
        elif st == "WARN":
            return "[WARN]"
        return "[FAIL]"

    print(f"{_status_fmt(results['poppler']['status'])} Poppler utilities (pdftotext, pdftoppm, pdfinfo)")
    print(f"{_status_fmt(results['tesseract']['status'])} Tesseract OCR engine (lang: eng)")
    print(f"{_status_fmt(results['database']['status'])} Database connection & pgvector extension")
    print(f"{_status_fmt(results['ollama']['status'])} Ollama host & model ('{results['ollama']['target_model']}')")
    print(f"{_status_fmt(results['air_gap']['status'])} Air-gap boundary (zero cloud egress)")
    print("=" * 60)

    if args.json:
        print(json.dumps(results, indent=2))

    if not results["healthy"]:
        print("\nDoctor detected missing requirements or unhealthy components.", file=sys.stderr)
        return 1

    print("\nAll critical systems healthy. Nexus is ready for ingestion and search.")
    return 0


def cmd_search(args):
    """Execute hybrid search across a specific workspace."""
    client = NexusClient.from_env()
    hits = client.search(
        query=args.query,
        workspace=args.workspace,
        doc_type=args.doc_type,
        limit=args.limit
    )

    if not hits:
        print(f"No matching documents found in workspace '{args.workspace}'.")
        return 0

    print(f"\nFound {len(hits)} hit(s) in workspace '{args.workspace}':\n" + "=" * 60)
    for i, h in enumerate(hits, start=1):
        print(f"\n[{i}] Citation: {h.citation} (Score: {h.score})")
        print(f"    Header:   {h.header or 'General'}")
        snippet = h.text.replace('\n', ' ')[:250]
        print(f"    Content:  {snippet}...")
    print("\n" + "=" * 60)
    return 0


def cmd_ingest(args):
    """Ingest a single document file into a workspace."""
    client = NexusClient.from_env()
    print(f"Ingesting '{args.file}' into workspace '{args.workspace}'...")
    resolved_doc_type = DocType(args.doc_type.lower()) if args.doc_type else DocType.GENERAL
    report = client.ingest(
        filepath=args.file,
        workspace=args.workspace,
        doc_type=resolved_doc_type,
        archive=args.archive
    )
    print(json.dumps(report.model_dump(), indent=2))
    return 0 if report.status in ("completed", "skipped_duplicate") else 1


def cmd_daemon(args):
    """Start the file watch ingestion daemon."""
    from .daemon import run_daemon
    import asyncio
    conf = NexusConfig.from_env()
    if args.watch_dir:
        conf.watch_dir = args.watch_dir
    print(f"Starting Nexus Ingestion Daemon...")
    asyncio.run(run_daemon(watch_dir=args.watch_dir, config=conf))
    return 0


def cmd_mcp(args):
    """Start the FastMCP server."""
    from .mcp import main as mcp_main
    mcp_main()
    return 0


def cmd_reindex(args):
    """Reindex documents in a workspace or specific document."""
    client = NexusClient.from_env()
    if args.document_id:
        print(f"Re-indexing document ID {args.document_id}...")
        report = client.reparse(args.document_id)
        print(json.dumps(report.model_dump(), indent=2))
        return 0 if report.status == "completed" else 1
    else:
        print("Error: Specify --document-id to re-index.", file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(
        prog="nexus",
        description="Air-Gapped Universal Document Ingestion Engine & Citation Spine"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. Doctor
    p_doctor = subparsers.add_parser("doctor", help="Check local environment dependencies and node connectivity")
    p_doctor.add_argument("--json", action="store_true", help="Output diagnostic report in JSON format")
    p_doctor.set_defaults(func=cmd_doctor)

    # 2. Search
    p_search = subparsers.add_parser("search", help="Search the corpus with exact page citations")
    p_search.add_argument("query", type=str, help="Search query or legal statutory token")
    p_search.add_argument("--workspace", "-w", type=str, required=True, help="Target workspace (required)")
    p_search.add_argument("--limit", "-n", type=int, default=5, help="Number of results (default 5)")
    p_search.add_argument("--doc-type", "-t", type=str, default=None, help="Filter by document type")
    p_search.set_defaults(func=cmd_search)

    # 3. Ingest
    p_ingest = subparsers.add_parser("ingest", help="Ingest a file into a workspace")
    p_ingest.add_argument("file", type=str, help="Path to local file (PDF, DOCX, EML, etc.)")
    p_ingest.add_argument("--workspace", "-w", type=str, required=True, help="Target workspace (required)")
    p_ingest.add_argument("--doc-type", "-t", type=str, default="general", help="Document classification")
    p_ingest.add_argument("--archive", "-a", action="store_true", help="Move source file to .ingested/ upon success")
    p_ingest.set_defaults(func=cmd_ingest)

    # 4. Reindex
    p_reindex = subparsers.add_parser("reindex", help="Re-index an existing document in the corpus")
    p_reindex.add_argument("--document-id", "-d", type=int, required=True, help="Specific document ID to re-index")
    p_reindex.set_defaults(func=cmd_reindex)

    # 5. Daemon
    p_daemon = subparsers.add_parser("daemon", help="Run the automated folder-watching daemon")
    p_daemon.add_argument("--watch-dir", type=str, default=None, help="Root folder to watch")
    p_daemon.set_defaults(func=cmd_daemon)

    # 6. MCP
    p_mcp = subparsers.add_parser("mcp", help="Run the FastMCP server for AI agents")
    p_mcp.set_defaults(func=cmd_mcp)

    # 7. Verify alias
    p_verify = subparsers.add_parser("verify", help="Alias for nexus doctor")
    p_verify.add_argument("--json", action="store_true", help="Output diagnostic report in JSON format")
    p_verify.set_defaults(func=cmd_doctor)

    parsed = parser.parse_args()
    sys.exit(parsed.func(parsed))


if __name__ == "__main__":
    main()
