#!/bin/bash
# Nexus Session Close Workflow Script
# Validates environment and updates tracking documents.

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=== Nexus Session Close Protocol ===${NC}"

# 1. Run unit tests
echo -e "\n${YELLOW}[1/3] Running backend unit tests...${NC}"
if mcp_env/bin/python -m unittest src.backend.test_sync && \
   mcp_env/bin/python -m unittest src.backend.test_mcp && \
   mcp_env/bin/python -m unittest src.backend.test_nexus_integration && \
   mcp_env/bin/python -m unittest src.backend.test_monetization_guardrails && \
   mcp_env/bin/python -m unittest src.backend.test_license_monetization && \
   mcp_env/bin/python -m unittest src.backend.test_zero_knowledge_crypto && \
   mcp_env/bin/python -m unittest src.backend.test_google_workspace && \
   mcp_env/bin/python -m unittest src.backend.test_landing_signup && \
   mcp_env/bin/python -m unittest src.backend.test_agent_accessibility && \
   mcp_env/bin/python -m unittest src.backend.test_feedback_api && \
   mcp_env/bin/python -m unittest src.backend.test_telemetry && \
   mcp_env/bin/python -m unittest src.backend.test_e2e_signup_audit; then
    echo -e "${GREEN}✓ All unit tests passed successfully.${NC}"
else
    echo -e "${RED}✗ Unit tests failed! Please fix all tests before closing session.${NC}"
    exit 1
fi

# 2. Check for loose scratch files in root
echo -e "\n${YELLOW}[2/3] Checking workspace hygiene...${NC}"
LOOSE_SCRATCH=$(find . -maxdepth 1 -name "scratch*" ! -name "scratch" | wc -l)
LOOSE_TEST=$(find . -maxdepth 1 -name "test*" | wc -l)

if [ "$LOOSE_SCRATCH" -gt 0 ] || [ "$LOOSE_TEST" -gt 0 ]; then
    echo -e "${YELLOW}⚠ Warning: Found loose scratch or test files in root directory.${NC}"
    echo -e "Consider running: ${GREEN}mv scratch* test* scratch/${NC} to keep the root tidy."
else
    echo -e "${GREEN}✓ Root directory is clean.${NC}"
fi

# 3. Check for GEMINI_INFLIGHT.md updates
echo -e "\n${YELLOW}[3/3] Auditing session inflight documentation...${NC}"
if [ -f "GEMINI_INFLIGHT.md" ]; then
    LAST_UPDATE=$(grep -m 1 "Last updated:" GEMINI_INFLIGHT.md || true)
    echo -e "${GREEN}✓ GEMINI_INFLIGHT.md is present. ($LAST_UPDATE)${NC}"
else
    echo -e "${RED}✗ Error: GEMINI_INFLIGHT.md is missing! Please create it before closing.${NC}"
    exit 1
fi

echo -e "\n${GREEN}=== Session closed cleanly. Ready for checkout. ===${NC}"
