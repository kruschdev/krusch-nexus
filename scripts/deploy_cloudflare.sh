#!/bin/bash
# Script to build & deploy Krusch-Nexus directly to Cloudflare Workers

set -e

echo "=== Krusch-Nexus Cloudflare Workers Deployment ==="

if ! command -v npx &> /dev/null; then
    echo "Error: npx/node is not installed."
    exit 1
fi

echo "[1/2] Verifying Cloudflare Wrangler environment..."
npx wrangler --version

echo "[2/2] Deploying Krusch-Nexus to Cloudflare Workers Edge..."
npx wrangler deploy

echo "=== Cloudflare Workers Deployment Complete! ==="
