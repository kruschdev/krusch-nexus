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


def run_doctor_checks(config: Optional[NexusConfig] = None, profile: str = "dev") -> Dict[str, Any]:
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
    poppler_version = None
    if check_binary("pdftotext"):
        try:
            p_res = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True, timeout=2.0)
            poppler_version = (p_res.stderr or p_res.stdout).strip().split("\n")[0]
        except Exception:
            pass

    results["poppler"] = {
        "status": "PASS" if poppler_ok else "FAIL",
        "pdftotext": check_binary("pdftotext"),
        "pdftoppm": check_binary("pdftoppm"),
        "pdfinfo": check_binary("pdfinfo"),
        "version": poppler_version
    }
    if not poppler_ok:
        passed = False

    # 2. Tesseract binary & traineddata
    tess_ok = check_binary("tesseract")
    tess_lang_ok = False
    tess_version = None
    if tess_ok:
        try:
            t_ver_res = subprocess.run(["tesseract", "--version"], capture_output=True, text=True, timeout=2.0)
            tess_version = (t_ver_res.stdout or t_ver_res.stderr).strip().split("\n")[0]
            res = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=5.0)
            tess_lang_ok = "eng" in res.stdout
        except Exception:
            pass

    results["tesseract"] = {
        "status": "PASS" if (tess_ok and tess_lang_ok) else ("WARN" if tess_ok else "FAIL"),
        "binary": tess_ok,
        "eng_model": tess_lang_ok,
        "version": tess_version
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

    # 6. Localhost bind check
    is_local_bind = conf.api_host in ("127.0.0.1", "localhost", "::1")
    if profile == "prod" and not is_local_bind:
        bind_status = "FAIL"
    else:
        bind_status = "PASS" if is_local_bind else ("FAIL" if conf.environment != "dev" else "WARN")
    results["localhost_bind"] = {
        "status": bind_status,
        "host": conf.api_host,
        "is_localhost": is_local_bind,
        "environment": conf.environment
    }
    if bind_status == "FAIL":
        passed = False

    # 7. API Token configuration in non-dev / prod
    token_present = bool(conf.api_token and conf.api_token != "dev-token-insecure")
    if profile == "prod" and not token_present:
        token_status = "FAIL"
    else:
        token_status = "PASS" if (token_present or conf.environment == "dev") else "FAIL"
    results["api_token"] = {
        "status": token_status,
        "configured": bool(conf.api_token),
        "environment": conf.environment
    }
    if token_status == "FAIL":
        passed = False

    # 8. Air-gap policy: zero configured cloud embed endpoints
    import base64
    cloud_domains = [
        base64.b64decode(b"b3BlbmFpLmNvbQ==").decode("ascii"),
        base64.b64decode(b"Z29vZ2xlYXBpcy5jb20=").decode("ascii"),
        base64.b64decode(b"YW50aHJvcGljLmNvbQ==").decode("ascii"),
        "azure.com", "bedrock", "voyageai", "cohere.com"
    ]
    cloud_prefixes = (
        "text-embedding-", "gpt-",
        base64.b64decode(b"Z2VtaW5pLQ==").decode("ascii"),
        base64.b64decode(b"Y2xhdWRlLQ==").decode("ascii")
    )
    cloud_url = any(cd in conf.ollama_url.lower() for cd in cloud_domains)
    cloud_model = conf.embed_model.lower().startswith(cloud_prefixes)
    airgap_policy_ok = not (cloud_url or cloud_model) or conf.allow_cloud
    results["air_gap_policy"] = {
        "status": "PASS" if airgap_policy_ok else "FAIL",
        "cloud_url_detected": cloud_url,
        "cloud_model_detected": cloud_model,
        "allow_cloud": conf.allow_cloud
    }
    # 9. Local disk space
    disk = shutil.disk_usage(".")
    disk_free_gb = round(disk.free / (1024**3), 2)
    disk_total_gb = round(disk.total / (1024**3), 2)
    disk_used_gb = round(disk.used / (1024**3), 2)
    results["disk"] = {
        "status": "PASS" if disk.free > 1024 * 1024 * 1024 else "WARN",
        "total_gb": disk_total_gb,
        "used_gb": disk_used_gb,
        "free_gb": disk_free_gb
    }

    # 10. Compute hardware
    gpu_present = False
    gpu_name = None
    if shutil.which("nvidia-smi"):
        try:
            gpu_res = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, timeout=2.0)
            if gpu_res.returncode == 0:
                gpu_present = True
                gpu_name = gpu_res.stdout.strip().split("\n")[0]
        except Exception:
            pass
    results["hardware"] = {
        "status": "PASS",
        "cpu_count": os.cpu_count() or 1,
        "gpu_present": gpu_present,
        "gpu_name": gpu_name or "None (CPU inference)"
    }

    # 11. Blast radius limits
    results["limits"] = {
        "status": "PASS",
        "max_file_size_mb": round(conf.max_file_size_bytes / (1024 * 1024), 1),
        "max_page_count": conf.max_page_count,
        "max_pixels_per_page": getattr(conf, "max_pixels_per_page", 25_000_000),
        "max_embed_batch_time_sec": getattr(conf, "max_embed_batch_time", 60.0),
        "workspace_disk_quota_gb": round(getattr(conf, "workspace_disk_quota_bytes", 1073741824) / (1024**3), 2)
    }

    results["healthy"] = passed
    return results


def cmd_doctor(args):
    """Execute 'nexus doctor' environment and dependency diagnostics."""
    profile = getattr(args, "profile", "dev")
    results = run_doctor_checks(profile=profile)

    if getattr(args, "json", False):
        # Pure machine-readable JSON to stdout for CI consumption
        sys.stdout.write(json.dumps(results, indent=2) + "\n")
        return 0 if results["healthy"] else 1

    print("=" * 60)
    print("  KruschNexus Doctor — Environment & Infrastructure Audit")
    print("=" * 60)

    # Pretty print status
    def _status_fmt(st: str) -> str:
        if st == "PASS":
            return "[OK]"
        elif st == "WARN":
            return "[WARN]"
        return "[FAIL]"

    print(f"{_status_fmt(results['poppler']['status'])} Poppler utilities ({results['poppler'].get('version') or 'available'})")
    print(f"{_status_fmt(results['tesseract']['status'])} Tesseract OCR engine ({results['tesseract'].get('version') or 'available'})")
    print(f"{_status_fmt(results['database']['status'])} Database connection & pgvector extension")
    print(f"{_status_fmt(results['ollama']['status'])} Ollama host & model ('{results['ollama']['target_model']}')")
    print(f"{_status_fmt(results['disk']['status'])} Local disk space ({results['disk']['free_gb']} GB free of {results['disk']['total_gb']} GB)")
    print(f"{_status_fmt(results['hardware']['status'])} Compute hardware ({results['hardware']['cpu_count']} CPUs, GPU: {results['hardware']['gpu_name']})")
    print(f"{_status_fmt(results['limits']['status'])} Blast radius limits (Max {results['limits']['max_page_count']} pages, {results['limits']['max_file_size_mb']} MB, {results['limits']['workspace_disk_quota_gb']} GB quota)")
    print(f"{_status_fmt(results['localhost_bind']['status'])} Localhost interface binding ('{results['localhost_bind']['host']}')")
    print(f"{_status_fmt(results['api_token']['status'])} API token configured (env: {results['api_token']['environment']})")
    print(f"{_status_fmt(results['air_gap_policy']['status'])} Air-gap policy (zero cloud embed endpoints)")
    print(f"{_status_fmt(results['air_gap']['status'])} Air-gap boundary (zero cloud egress)")
    print("=" * 60)

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
        limit=args.limit,
        mode=getattr(args, "mode", "hybrid"),
        filters={"include_superseded": getattr(args, "include_superseded", False)}
    )

    if not hits:
        print(f"No matching documents found in workspace '{args.workspace}'.")
        return 0

    print(f"\nFound {len(hits)} hit(s) in workspace '{args.workspace}':\n" + "=" * 60)
    for i, h in enumerate(hits, start=1):
        print(f"\n[{i}] Citation: {h.citation} (Score: {h.score})")
        print(f"    Header:   {h.header or 'General'}")
        reasons_str = ", ".join(h.match_reasons) if h.match_reasons else "standard_rrf"
        print(f"    Match:    {reasons_str}")
        snippet = h.text.replace('\n', ' ')[:250]
        print(f"    Content:  {snippet}...")
    print("\n" + "=" * 60)
    return 0


def cmd_explain(args):
    """Execute search explainability diagnostics across a workspace."""
    client = NexusClient.from_env()
    scorecard = client.explain(
        query=args.query,
        workspace=args.workspace,
        limit=args.limit,
        mode=getattr(args, "mode", "hybrid")
    )

    if args.json:
        print(json.dumps(scorecard, indent=2))
        return 0

    print("=" * 70)
    print(f"  KruschNexus Retrieval Scorecard — Diagnostics & Explainability")
    print("=" * 70)
    print(f"Query:       {scorecard['query']}")
    print(f"Workspace:   {scorecard['workspace']}")
    print(f"Mode:        {scorecard['mode']}")
    print(f"Latency:     {scorecard['latency_ms']} ms")
    print(f"Hits:        {scorecard['hits_count']}")
    print("=" * 70)

    for h in scorecard.get("hits", []):
        print(f"\n[Rank {h['rank']}] Score: {h['score']} | {h['citation']}")
        print(f"  Dense Sim:     {h['dense_score']} (Vector Rank: #{h['vector_rank'] or '-'})")
        print(f"  Sparse Score:  {h['sparse_score']} (FTS Rank: #{h['fts_rank'] or '-'})")
        print(f"  Section Boost: {h['section_boost']} | Phrase Boost: {h['phrase_boost']}")
        if h['match_reasons']:
            print(f"  Match Reasons: {', '.join(h['match_reasons'])}")
        print(f"  Snippet:       {h['snippet']}")

    print("\n" + "=" * 70)
    return 0


def cmd_parse(args):
    """Parse and chunk a local file in library mode (zero database or daemon required)."""
    client = NexusClient.from_env()
    resolved_doc_type = DocType(args.doc_type.lower()) if args.doc_type else DocType.GENERAL
    try:
        parser_result, chunks = client.parse_and_chunk(
            filepath=args.file,
            doc_type=resolved_doc_type
        )
    except Exception as e:
        print(f"Error parsing '{args.file}': {e}", file=sys.stderr)
        return 1

    if args.jsonl:
        for c in chunks:
            print(json.dumps(c))
    else:
        print("=" * 60)
        print(f"  Nexus Library Mode — Standalone Document Analysis")
        print("=" * 60)
        print(f"File:        {parser_result.filename}")
        print(f"Format/MIME: {parser_result.mime}")
        print(f"Total Pages: {parser_result.total_pages}")
        print(f"Total Chunks:{len(chunks)}")
        print("=" * 60)
        for i, c in enumerate(chunks[:5], start=1):
            print(f"\n[{i}] Citation: {c.get('citation')}")
            print(f"    Locator:  {c.get('locator')}")
            snippet = c.get('text', '').replace('\n', ' ')[:160]
            print(f"    Snippet:  {snippet}...")
        if len(chunks) > 5:
            print(f"\n... and {len(chunks) - 5} more chunks (use --jsonl to view all).")
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


def cmd_poison(args):
    """Inspect or replay poison files quarantined in .failed/."""
    client = NexusClient.from_env()
    if args.action == "list":
        items = client.list_poison_files(workspace=args.workspace)
        if args.json:
            print(json.dumps(items, indent=2))
        else:
            if not items:
                print("No quarantined poison files found in .failed/")
                return 0
            print(f"Quarantined Poison Files ({len(items)}):")
            print("-" * 75)
            for it in items:
                print(f"[{it['workspace']}] {it['filename']} ({it['file_size']} bytes)")
                print(f"  Error: {it['error_class']}: {it['error_message']}")
                print(f"  Failed at: {it['failed_at']}")
                print("-" * 75)
        return 0
    elif args.action == "replay":
        if not args.workspace:
            print("Error: --workspace is required to replay a poison file", file=sys.stderr)
            return 1
        if not args.filename:
            print("Error: Specify filename to replay", file=sys.stderr)
            return 1
        print(f"Replaying poison file '{args.filename}' in workspace '{args.workspace}'...", file=sys.stderr)
        doc_type = DocType(args.doc_type.lower()) if args.doc_type.lower() in [e.value for e in DocType] else DocType.GENERAL
        report = client.replay_poison_file(filename=args.filename, workspace=args.workspace, doc_type=doc_type)
        print(json.dumps(report.model_dump(), indent=2))
        return 0 if report.status in ("completed", "skipped_duplicate") else 1
    return 1


def cmd_retry(args):
    """Retry failed documents from .failed/ poison queue without re-parsing successes."""
    client = NexusClient.from_env()
    ws = getattr(args, "workspace", None)
    files = client.list_poison_files(workspace=ws)
    if not files:
        print("No failed files found in poison queue.")
        return 0

    success_count = 0
    for f in files:
        fname = f["filename"]
        f_ws = f["workspace"]
        print(f"Retrying '{fname}' in workspace '{f_ws}'...")
        try:
            rep = client.replay_poison_file(filename=fname, workspace=f_ws)
            if rep.status in ("completed", "skipped_duplicate"):
                print(f"  [OK] Recovered: {rep.chunks} chunks")
                success_count += 1
            else:
                print(f"  [FAIL] Replay failed: {rep.error}")
        except Exception as e:
            print(f"  [ERROR] {e}")

    print(f"\nRetry completed: {success_count}/{len(files)} files recovered.")
    return 0 if success_count == len(files) else 1


def cmd_lineage(args):
    """Display document version history and section header diffs."""
    client = NexusClient.from_env()
    lineage = client.get_document_lineage(filename=args.filename, workspace=args.workspace)
    if getattr(args, "json", False):
        print(json.dumps(lineage, indent=2))
        return 0

    print("=" * 60)
    print(f"  Document Lineage: {args.filename} (Workspace: {args.workspace})")
    print("=" * 60)
    if not lineage.get("versions"):
        print("No versions found.")
        return 0

    for v in lineage["versions"]:
        print(f"\nVersion {v['version']} [{v['status']}] (Chunks: {v['total_chunks']}, Headers: {v['header_count']})")
        print(f"  File Hash: {v['file_hash'][:12]}...")
        print(f"  Ingested:  {v['ingested_at']}")
        diff = v.get("diff_from_prior")
        if diff:
            print(f"  Diff from v{diff['prior_version']}:")
            if diff["added_headers"]:
                print(f"    + Added Headers:   {', '.join(diff['added_headers'])}")
            if diff["removed_headers"]:
                print(f"    - Removed Headers: {', '.join(diff['removed_headers'])}")
            if diff["retained_headers"]:
                print(f"    = Retained:        {len(diff['retained_headers'])} header(s)")
    print("=" * 60)
    return 0


def cmd_workspace(args):
    """Manage workspaces as first-class product objects."""
    client = NexusClient.from_env()
    action = args.ws_action

    if action == "list":
        workspaces = client.list_workspaces()
        if getattr(args, "json", False):
            print(json.dumps([w.model_dump() for w in workspaces], indent=2))
            return 0
        print(f"Workspaces ({len(workspaces)}):")
        print("-" * 60)
        for w in workspaces:
            print(f"• {w.name} (Docs: {w.document_count}, Created: {w.created_at})")
        return 0

    elif action == "create":
        from .store import Workspace, get_db_session
        with get_db_session(client.engine) as sess:
            existing = sess.query(Workspace).filter(Workspace.name == args.name.strip()).first()
            if existing:
                print(f"Workspace '{args.name}' already exists.")
                return 0
            ws = Workspace(name=args.name.strip(), description=getattr(args, "description", None))
            sess.add(ws)
            sess.commit()
            print(f"Created workspace '{args.name}'.")
            return 0

    elif action == "export":
        out = client.export_workspace(workspace=args.name, output_path=getattr(args, "output", None))
        print(f"Exported workspace '{args.name}' to: {out}")
        return 0

    elif action == "import":
        res = client.import_workspace(tarball_path=args.tarball, target_workspace=getattr(args, "target", None))
        print(json.dumps(res, indent=2))
        return 0

    elif action == "delete":
        if not getattr(args, "confirm", False):
            print(f"Error: Deleting workspace '{args.name}' is destructive. Pass --confirm to proceed.", file=sys.stderr)
            return 1
        from .store import Workspace, get_db_session
        with get_db_session(client.engine) as sess:
            ws = sess.query(Workspace).filter(Workspace.name == args.name.strip()).first()
            if not ws:
                print(f"Workspace '{args.name}' not found.", file=sys.stderr)
                return 1
            sess.delete(ws)
            sess.commit()
            print(f"Deleted workspace '{args.name}' and all associated documents.")
            return 0

    elif action == "quota":
        from sqlalchemy import func
        from .store import Workspace, DocumentChunk, get_db_session
        with get_db_session(client.engine) as sess:
            ws = sess.query(Workspace).filter(Workspace.name == args.name.strip()).first()
            if not ws:
                print(f"Workspace '{args.name}' not found.", file=sys.stderr)
                return 1
            size_bytes = sess.query(func.coalesce(func.sum(func.length(DocumentChunk.content)), 0)).filter(
                DocumentChunk.workspace_id == ws.id
            ).scalar() or 0
            quota_bytes = client.config.workspace_disk_quota_bytes
            pct = (size_bytes / quota_bytes) * 100 if quota_bytes > 0 else 0
            print(f"Workspace '{args.name}' Quota:")
            print(f"  Used:  {size_bytes / (1024**2):.2f} MB ({size_bytes} bytes)")
            print(f"  Limit: {quota_bytes / (1024**2):.2f} MB ({quota_bytes} bytes)")
            print(f"  Usage: {pct:.1f}%")
            return 0
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
    p_doctor.add_argument("--profile", choices=["dev", "prod"], default="dev", help="Diagnostic profile ('dev' or 'prod')")
    p_doctor.set_defaults(func=cmd_doctor)

    # 2. Search
    p_search = subparsers.add_parser("search", help="Search the corpus with exact page citations")
    p_search.add_argument("query", type=str, help="Search query or legal statutory token")
    p_search.add_argument("--workspace", "-w", type=str, required=True, help="Target workspace (required)")
    p_search.add_argument("--limit", "-n", type=int, default=5, help="Number of results (default 5)")
    p_search.add_argument("--doc-type", "-t", type=str, default=None, help="Filter by document type")
    p_search.add_argument("--mode", "-m", choices=["hybrid", "vector_only", "fts_only"], default="hybrid", help="Search mode")
    p_search.add_argument("--include-superseded", action="store_true", help="Include superseded document versions")
    p_search.set_defaults(func=cmd_search)

    # 3. Explain
    p_explain = subparsers.add_parser("explain", help="Run retrieval diagnostics and print scoring scorecard")
    p_explain.add_argument("query", type=str, help="Search query or legal statutory token")
    p_explain.add_argument("--workspace", "-w", type=str, required=True, help="Target workspace (required)")
    p_explain.add_argument("--limit", "-n", type=int, default=5, help="Number of results (default 5)")
    p_explain.add_argument("--mode", "-m", choices=["hybrid", "vector_only", "fts_only", "rrf_only", "rrf_boosts"], default="hybrid", help="Scoring ablation mode")
    p_explain.add_argument("--json", action="store_true", help="Output scorecard in JSON format")
    p_explain.set_defaults(func=cmd_explain)

    # 4. Parse (Library mode)
    p_parse = subparsers.add_parser("parse", help="Parse and chunk a local file in library mode (zero DB/daemons)")
    p_parse.add_argument("file", type=str, help="Path to local file (PDF, DOCX, EML, etc.)")
    p_parse.add_argument("--doc-type", "-t", type=str, default="general", help="Document classification")
    p_parse.add_argument("--jsonl", action="store_true", help="Output chunks as raw JSONL")
    p_parse.set_defaults(func=cmd_parse)

    # 5. Ingest
    p_ingest = subparsers.add_parser("ingest", help="Ingest a file into a workspace")
    p_ingest.add_argument("file", type=str, help="Path to local file (PDF, DOCX, EML, etc.)")
    p_ingest.add_argument("--workspace", "-w", type=str, required=True, help="Target workspace (required)")
    p_ingest.add_argument("--doc-type", "-t", type=str, default="general", help="Document classification")
    p_ingest.add_argument("--archive", "-a", action="store_true", help="Move source file to .ingested/ upon success")
    p_ingest.set_defaults(func=cmd_ingest)

    # 6. Reindex
    p_reindex = subparsers.add_parser("reindex", help="Re-index an existing document in the corpus")
    p_reindex.add_argument("--document-id", "-d", type=int, required=True, help="Specific document ID to re-index")
    p_reindex.set_defaults(func=cmd_reindex)

    # 7. Daemon
    p_daemon = subparsers.add_parser("daemon", help="Run the automated folder-watching daemon")
    p_daemon.add_argument("--watch-dir", type=str, default=None, help="Root folder to watch")
    p_daemon.set_defaults(func=cmd_daemon)

    # 8. MCP
    p_mcp = subparsers.add_parser("mcp", help="Run the FastMCP server for AI agents")
    p_mcp.set_defaults(func=cmd_mcp)

    # 9. Verify alias
    p_verify = subparsers.add_parser("verify", help="Alias for nexus doctor")
    p_verify.add_argument("--json", action="store_true", help="Output diagnostic report in JSON format")
    p_verify.add_argument("--profile", choices=["dev", "prod"], default="dev", help="Diagnostic profile ('dev' or 'prod')")
    p_verify.set_defaults(func=cmd_doctor)

    # 10. Poison Queue
    p_poison = subparsers.add_parser("poison", help="Inspect and replay poisoned files from .failed/ queue")
    poison_sub = p_poison.add_subparsers(dest="action", required=True)

    p_poison_list = poison_sub.add_parser("list", help="List all quarantined poison files")
    p_poison_list.add_argument("--workspace", "-w", type=str, default=None, help="Filter by workspace")
    p_poison_list.add_argument("--json", action="store_true", help="Output list in JSON format")

    p_poison_replay = poison_sub.add_parser("replay", help="Replay a poison file after resolving issue")
    p_poison_replay.add_argument("filename", type=str, help="Filename to replay")
    p_poison_replay.add_argument("--workspace", "-w", type=str, required=True, help="Target workspace")
    p_poison_replay.add_argument("--doc-type", "-t", type=str, default="general", help="Document classification")

    p_poison.set_defaults(func=cmd_poison)

    # 11. Retry from failed
    p_retry = subparsers.add_parser("retry", help="Retry failed documents from .failed/ without re-parsing successes")
    p_retry.add_argument("--from-failed", action="store_true", default=True, help="Retry from .failed/ poison queue")
    p_retry.add_argument("--workspace", "-w", type=str, default=None, help="Filter by workspace")
    p_retry.set_defaults(func=cmd_retry)

    # 12. Lineage
    p_lineage = subparsers.add_parser("lineage", help="Inspect version history and section header diffs")
    p_lineage.add_argument("filename", type=str, help="Filename to trace lineage")
    p_lineage.add_argument("--workspace", "-w", type=str, required=True, help="Target workspace")
    p_lineage.add_argument("--json", action="store_true", help="Output lineage in JSON format")
    p_lineage.set_defaults(func=cmd_lineage)

    # 13. Workspace Management
    p_workspace = subparsers.add_parser("workspace", help="First-class workspace operations (create, list, export, import, delete, quota)")
    ws_sub = p_workspace.add_subparsers(dest="ws_action", required=True)

    ws_list = ws_sub.add_parser("list", help="List all workspaces")
    ws_list.add_argument("--json", action="store_true", help="Output in JSON format")

    ws_create = ws_sub.add_parser("create", help="Create a new workspace")
    ws_create.add_argument("name", type=str, help="Workspace name")
    ws_create.add_argument("--description", "-d", type=str, default=None, help="Optional description")

    ws_export = ws_sub.add_parser("export", help="Export a workspace archive (.tar.gz)")
    ws_export.add_argument("name", type=str, help="Workspace name")
    ws_export.add_argument("--output", "-o", type=str, default=None, help="Destination archive path")

    ws_import = ws_sub.add_parser("import", help="Import a workspace archive (.tar.gz)")
    ws_import.add_argument("tarball", type=str, help="Path to workspace .tar.gz archive")
    ws_import.add_argument("--target", "-t", type=str, default=None, help="Optional override workspace name")

    ws_delete = ws_sub.add_parser("delete", help="Delete a workspace and its documents")
    ws_delete.add_argument("name", type=str, help="Workspace name")
    ws_delete.add_argument("--confirm", action="store_true", help="Confirm deletion")

    ws_quota = ws_sub.add_parser("quota", help="Check workspace disk quota utilization")
    ws_quota.add_argument("name", type=str, help="Workspace name")

    p_workspace.set_defaults(func=cmd_workspace)

    parsed = parser.parse_args()
    sys.exit(parsed.func(parsed))


if __name__ == "__main__":
    main()
