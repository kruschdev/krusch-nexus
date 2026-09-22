"""
KruschNexus Command-Line Interface (CLI)
========================================
Unified entrypoint for offline document ingestion, hybrid corpus retrieval,
watch daemon lifecycle, FastMCP server, and air-gap verification.

Usage:
    nexus search "liquidated damages" --workspace Matter_Smith
    nexus ingest /data/lease.pdf --workspace Matter_Smith
    nexus daemon
    nexus mcp
    nexus verify --offline
"""

import sys
import json
import argparse
from typing import Optional

from .client import Nexus
from .config import NexusConfig
from .offline_check import verify_offline_environment


def cmd_search(args):
    """Execute hybrid search across a specific workspace."""
    nx = Nexus.from_env()
    hits = nx.search(
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
        print(f"\n[{i}] Citation: {h.citation} (Score: {h.rrf_score})")
        print(f"    Header:   {h.header or 'General'}")
        snippet = h.content.replace('\n', ' ')[:250]
        print(f"    Content:  {snippet}...")
    print("\n" + "=" * 60)
    return 0


def cmd_ingest(args):
    """Ingest a single document file into a workspace."""
    nx = Nexus.from_env()
    print(f"Ingesting '{args.file}' into workspace '{args.workspace}'...")
    report = nx.ingest(
        filepath=args.file,
        workspace=args.workspace,
        doc_type=args.doc_type or "general",
        archive=args.archive
    )
    print(json.dumps(report.model_dump(), indent=2))
    return 0 if report.status in ("completed", "skipped_duplicate") else 1


def cmd_daemon(args):
    """Start the file watch ingestion daemon."""
    from .ingest import auto_ingest_loop
    import asyncio
    conf = NexusConfig.from_env()
    if args.watch_dir:
        conf.watch_dir = args.watch_dir
    print(f"Starting Nexus Ingestion Daemon...")
    asyncio.run(auto_ingest_loop(config=conf))
    return 0


def cmd_mcp(args):
    """Start the FastMCP server."""
    from .mcp import main as mcp_main
    mcp_main()
    return 0


def cmd_verify(args):
    """Execute self-checks verifying offline guarantees and local nodes."""
    print("Executing KruschNexus Air-Gap & Homelab Self-Check...")
    result = verify_offline_environment()
    print(json.dumps(result, indent=2))

    if args.offline and not result["air_gap_secure"]:
        print("\n[ALERT] Air-gap check failed: Outbound internet connectivity detected!", file=sys.stderr)
        return 1

    print("\n[OK] Self-check completed successfully.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="nexus",
        description="Universal Offline Document Ingestion Engine & Page-True Citation Spine"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. Search
    p_search = subparsers.add_parser("search", help="Search the corpus with exact page citations")
    p_search.add_argument("query", type=str, help="Search query or legal statutory token")
    p_search.add_argument("--workspace", "-w", type=str, required=True, help="Target workspace (required)")
    p_search.add_argument("--limit", "-n", type=int, default=5, help="Number of results (default 5)")
    p_search.add_argument("--doc-type", "-t", type=str, default=None, help="Filter by document type")
    p_search.set_defaults(func=cmd_search)

    # 2. Ingest
    p_ingest = subparsers.add_parser("ingest", help="Ingest a file into a workspace")
    p_ingest.add_argument("file", type=str, help="Path to local file (PDF, DOCX, EML, etc.)")
    p_ingest.add_argument("--workspace", "-w", type=str, required=True, help="Target workspace (required)")
    p_ingest.add_argument("--doc-type", "-t", type=str, default="general", help="Document classification")
    p_ingest.add_argument("--archive", "-a", action="store_true", help="Move source file to .ingested/ upon success")
    p_ingest.set_defaults(func=cmd_ingest)

    # 3. Daemon
    p_daemon = subparsers.add_parser("daemon", help="Run the automated folder-watching daemon")
    p_daemon.add_argument("--watch-dir", type=str, default=None, help="Root folder to watch")
    p_daemon.set_defaults(func=cmd_daemon)

    # 4. MCP
    p_mcp = subparsers.add_parser("mcp", help="Run the FastMCP server for AI agents")
    p_mcp.set_defaults(func=cmd_mcp)

    # 5. Verify
    p_verify = subparsers.add_parser("verify", help="Run air-gap and infrastructure verification")
    p_verify.add_argument("--offline", action="store_true", help="Assert zero outbound internet egress")
    p_verify.set_defaults(func=cmd_verify)

    parsed = parser.parse_args()
    sys.exit(parsed.func(parsed))


if __name__ == "__main__":
    main()
