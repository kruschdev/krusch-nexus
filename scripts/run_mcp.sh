#!/usr/bin/env bash
# Launcher script for Krusch-Nexus Business RAG & Institutional Knowledge MCP Server

set -e

# Export project environment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PYTHONPATH="${PROJECT_ROOT}:${PYTHONPATH}"
export TAGGING_PROVIDER="${TAGGING_PROVIDER:-openrouter}"
export OPENROUTER_TAG_MODEL="${OPENROUTER_TAG_MODEL:-qwen/qwen-2.5-coder-32b-instruct}"

echo "Starting Krusch-Nexus Business RAG MCP Server..." >&2
echo "Engine: krusch-context-mcp vector + GraphRAG" >&2
echo "Tagging Provider: ${TAGGING_PROVIDER} (${OPENROUTER_TAG_MODEL})" >&2

# Run FastMCP stdio server
exec python3 -m src.backend.mcp_server "$@"
