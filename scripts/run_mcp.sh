#!/usr/bin/env bash
# Launcher script for Krusch-Nexus Business RAG & Institutional Knowledge MCP Server

set -e

# Export project environment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PYTHONPATH="${PROJECT_ROOT}:${PYTHONPATH}"
export EMBEDDING_PROVIDER="${EMBEDDING_PROVIDER:-ollama}"
export OLLAMA_EMBED_HOST="${OLLAMA_EMBED_HOST:-http://127.0.0.1:11434}"
export TAGGING_PROVIDER="${TAGGING_PROVIDER:-ollama}"

echo "Starting KruschNexus Ingestion & RAG MCP Server..." >&2
echo "Engine: PostgreSQL/pgvector + Local Ollama (${EMBEDDING_PROVIDER})" >&2

# Run FastMCP stdio server
exec python3 -m src.backend.mcp_server "$@"
