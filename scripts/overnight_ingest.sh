#!/bin/bash

# Overnight Batch Ingestion Script for Krusch Nexus
# This script will sequentially ingest high-value AI frameworks and SDKs
# utilizing the newly optimized Compute-Data Split architecture.

set -e

REPOS=(
    "https://github.com/modelcontextprotocol/typescript-sdk"
    "https://github.com/modelcontextprotocol/python-sdk"
    "https://github.com/langchain-ai/langgraph"
    "https://github.com/langchain-ai/langchainjs"
    "https://github.com/anthropics/anthropic-sdk-python"
    "https://github.com/anthropics/anthropic-sdk-typescript"
    "https://github.com/vercel/ai"
)

echo "Starting Krusch Nexus Overnight Batch Ingestion..."
echo "Total Repositories to ingest: ${#REPOS[@]}"
echo "=================================================="

# Ensure we are in the project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

for repo in "${REPOS[@]}"; do
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Starting ingestion for $repo..."
    
    # Run the ingestion CLI inside the backend container to ensure all env vars and DB connections are correct
    docker compose exec backend python -m src.backend.ingest_repo_cli "$repo" || {
        echo "[!] Error ingesting $repo, continuing to next..."
    }
    
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Finished $repo."
    echo "--------------------------------------------------"
    
    # Optional: Brief cooldown between massive repos to let the fleet settle
    sleep 10
done

echo "=================================================="
echo "Overnight Batch Ingestion Complete!"
