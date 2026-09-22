#!/usr/bin/env python3
"""
CLI wrapper for Nexus GitHub repo ingestion.

Usage:
    python -m src.backend.ingest_repo_cli https://github.com/vercel/ai
    python -m src.backend.ingest_repo_cli https://github.com/langchain-ai/langchainjs --no-tags --limit=50
    python -m src.backend.ingest_repo_cli https://github.com/modelcontextprotocol/typescript-sdk --workspace=mcp-reference
"""

import argparse
import sys
import os

# Ensure the project root is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.backend.repo_ingest import ingest_github_repo


def main():
    parser = argparse.ArgumentParser(description="Ingest a GitHub repository into Krusch-Nexus")
    parser.add_argument("repo_url", help="GitHub repository URL")
    parser.add_argument("--workspace", help="Custom workspace name (default: repo:<name>)")
    parser.add_argument("--no-tags", action="store_true", help="Skip LLM tag generation")
    parser.add_argument("--limit", type=int, help="Max files to process")

    args = parser.parse_args()

    print(f"Krusch-Nexus Repo Ingestion CLI")
    print(f"Repository: {args.repo_url}")
    if args.workspace:
        print(f"Workspace: {args.workspace}")
    if args.no_tags:
        print(f"Tags: DISABLED")
    if args.limit:
        print(f"File limit: {args.limit}")

    result = ingest_github_repo(
        repo_url=args.repo_url,
        workspace_name=args.workspace,
        generate_tags=not args.no_tags,
        max_files=args.limit,
    )

    print(f"\n{'='*40}")
    print(f"Results: {result['indexed']} indexed, {result['tagged']} tagged, {result['failed']} failed")
    sys.exit(0 if result['failed'] == 0 else 1)


if __name__ == "__main__":
    main()
