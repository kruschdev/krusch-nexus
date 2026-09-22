#!/bin/bash
# Nexus Session Continue/Resume Workflow Script
# Restores context and prints active node readiness.

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=== Nexus Session Continue Protocol ===${NC}"

# 1. Print Active Session Context
echo -e "\n${YELLOW}[1/2] Active In-Flight State (GEMINI_INFLIGHT.md):${NC}"
if [ -f "GEMINI_INFLIGHT.md" ]; then
    echo -e "--------------------------------------------------------"
    # Show active items and next steps
    sed -n '/## Last Session/,/##/p' GEMINI_INFLIGHT.md | grep -v "##" || true
    sed -n '/## Next Steps/,/##/p' GEMINI_INFLIGHT.md | grep -v "##" || true
    echo -e "--------------------------------------------------------"
else
    echo -e "${RED}⚠ Warning: No active GEMINI_INFLIGHT.md file found.${NC}"
fi

# 2. Run node connection audits
echo -e "\n${YELLOW}[2/2] Auditing homelab services and backends...${NC}"

# Check FastAPI Backend (kruschserv)
if curl -s --max-time 3 http://10.0.0.85:8001/health >/dev/null; then
    echo -e "${GREEN}✓ Nexus Backend (kruschserv:8001) is reachable.${NC}"
elif curl -s --max-time 3 http://localhost:8001/health >/dev/null; then
    echo -e "${GREEN}✓ Nexus Backend (localhost:8001) is reachable.${NC}"
else
    echo -e "${RED}✗ Error: Nexus Backend is unreachable on kruschserv:8001.${NC}"
fi

# Check local Ollama hosts (from config)
OLLAMA_HOST="10.0.0.85"

if curl -s --max-time 3 "http://$OLLAMA_HOST:11434/api/tags" >/dev/null; then
    echo -e "${GREEN}✓ Inference Host ($OLLAMA_HOST:11434) is online.${NC}"
else
    echo -e "${YELLOW}⚠ Warning: Inference Host ($OLLAMA_HOST) is offline or unreachable.${NC}"
fi

echo -e "\n${GREEN}=== Workspace context loaded. Ready for execution. ===${NC}"
