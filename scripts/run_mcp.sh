#!/usr/bin/env bash
# Launcher script for KruschNexus FastMCP Server

set -e

# Export project environment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PYTHONPATH="${PROJECT_ROOT}/src:${PYTHONPATH}"
export OLLAMA_EMBED_HOST="${OLLAMA_EMBED_HOST:-http://127.0.0.1:11434}"
export OLLAMA_EMBED_MODEL="${OLLAMA_EMBED_MODEL:-bge-large}"

echo "Starting KruschNexus FastMCP Server..." >&2
echo "Engine: PostgreSQL/pgvector + Local Ollama (${OLLAMA_EMBED_MODEL})" >&2

# Run FastMCP stdio server
exec python3 -m krusch_nexus.mcp "$@"
