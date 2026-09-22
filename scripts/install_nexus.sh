#!/usr/bin/env bash
# ==============================================================================
# Krusch-Nexus Cloud-Native MCP Server & Institutional RAG Installer
# Usage: curl -sSL https://krusch.dev/nexus/install.sh | bash -s -- --license-key YOUR_LICENSE_KEY
# ==============================================================================

set -e

LICENSE_KEY=""
INSTALL_DIR="${HOME}/.krusch-nexus"
API_HOST="${NEXUS_API_HOST:-https://krusch.dev}"

# Parse command line flags
while [[ $# -gt 0 ]]; do
  case $1 in
    --license-key|-k)
      LICENSE_KEY="$2"
      shift 2
      ;;
    --dir|-d)
      INSTALL_DIR="$2"
      shift 2
      ;;
    --host)
      API_HOST="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

echo "========================================================================"
echo "⚡ Installing Krusch-Nexus Business RAG & MCP Server..."
echo "========================================================================"

if [ -z "${LICENSE_KEY}" ]; then
  echo "⚠️ No license key provided via --license-key. Generating free trial license key..."
  RAW_KEY=$(curl -sSL -X POST "${API_HOST}/api/license/generate" -H "Content-Type: application/json" -d '{}' || echo "")
  LICENSE_KEY=$(echo "$RAW_KEY" | grep -o '"license_key":"[^"]*' | cut -d'"' -f4 || echo "")
  if [ -z "${LICENSE_KEY}" ]; then
    LICENSE_KEY="NEXUS-LIC-TRIAL-BETA-2026"
  fi
  echo "✓ Assigned License Key: ${LICENSE_KEY}"
fi

echo "🔍 Verifying License Key with ${API_HOST}..."
VERIFY_RES=$(curl -sSL -X POST "${API_HOST}/api/license/verify" -H "Content-Type: application/json" -d "{\"license_key\": \"${LICENSE_KEY}\"}" || echo "")
echo "✓ License Validated: ${VERIFY_RES}"

echo "📦 Creating installation directory: ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"

if [ ! -d "${INSTALL_DIR}/.git" ]; then
  echo "📥 Cloning Krusch-Nexus repository..."
  git clone https://github.com/krusch-nexus/krusch-nexus.git "${INSTALL_DIR}" || {
    echo "ℹ️ Using existing local deployment workspace..."
  }
fi

cd "${INSTALL_DIR}"

echo "🐍 Setting up Python Virtual Environment (mcp_env)..."
if [ ! -d "mcp_env" ]; then
  python3 -m venv mcp_env
fi

./mcp_env/bin/pip install --upgrade pip > /dev/null 2>&1 || true
./mcp_env/bin/pip install fastapi uvicorn httpx sqlalchemy psycopg2-binary sentence-transformers mcp > /dev/null 2>&1 || true

echo "⚙️ Configuring Local MCP Client Integration..."
MCP_CONFIG_FILE="${HOME}/.gemini/antigravity-ide/mcp_config.json"
if [ ! -f "${MCP_CONFIG_FILE}" ]; then
  mkdir -p "$(dirname "${MCP_CONFIG_FILE}")"
  echo '{"mcpServers": {}}' > "${MCP_CONFIG_FILE}"
fi

# Generate configuration snippet
cat << EOF > "${INSTALL_DIR}/nexus_mcp_snippet.json"
{
  "krusch-nexus": {
    "command": "${INSTALL_DIR}/mcp_env/bin/python",
    "args": ["-m", "src.backend.mcp_server"],
    "cwd": "${INSTALL_DIR}",
    "env": {
      "PYTHONPATH": "${INSTALL_DIR}",
      "NEXUS_LICENSE_KEY": "${LICENSE_KEY}",
      "DBOS_DATABASE_URL": "postgresql://openclaw:openclaw_password@10.0.0.85:5434/kruschdb",
      "EMBEDDING_PROVIDER": "openrouter",
      "TAGGING_PROVIDER": "openrouter"
    }
  }
}
EOF

echo "========================================================================"
echo "✅ Krusch-Nexus MCP Installation Complete!"
echo "========================================================================"
echo "🔑 License Key : ${LICENSE_KEY}"
echo "📁 Install Dir  : ${INSTALL_DIR}"
echo "⚙️ MCP Snippet  : ${INSTALL_DIR}/nexus_mcp_snippet.json"
echo ""
echo "To authorize in Claude Desktop or Cursor/Antigravity, add 'krusch-nexus' from nexus_mcp_snippet.json into your mcp_config.json."
echo "========================================================================"
