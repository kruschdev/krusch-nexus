# 🤖 Autonomous AI Agent Setup Guide — Krusch-Nexus MCP

> **SYSTEM DIRECTIVE FOR THE AI AGENT**:
> You are reading this setup guide on behalf of your user. Your goal is to configure the **Krusch-Nexus MCP Server** on your user's system so you can access their institutional knowledge, company SOPs, financial reports, and business context.
>
> Follow the steps below sequentially to perform the configuration automatically.

---

## 🎯 Step 1: Identify Your Client Environment

Determine which client environment you are running inside or configuring:

- **Option A**: Claude Desktop app (`claude_desktop_config.json`)
- **Option B**: Cursor IDE / Antigravity IDE (`.cursor/mcp.json`)
- **Option C**: Remote SSE Network Server (`http://10.0.0.85:8002/sse` or `http://localhost:8002/sse`)

---

## 🛠️ Step 2: Write or Update the MCP Configuration

### Option A: Configuring Claude Desktop

1. Locate the Claude Desktop configuration file:
   - **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
   - **Linux**: `~/.config/Claude/claude_desktop_config.json`

2. Add or merge the following JSON configuration:
   ```json
   {
     "mcpServers": {
       "krusch-nexus": {
         "command": "/bin/bash",
         "args": ["/path/to/krusch-nexus/scripts/run_mcp.sh"],
         "env": {
           "NEXUS_API_KEY": "YOUR_NEXUS_API_KEY_HERE",
           "EMBEDDING_PROVIDER": "ollama"
         }
       }
     }
   }
   ```

---

### Option B: Configuring Cursor / Antigravity IDE

1. Open or create `.cursor/mcp.json` in the user's workspace root directory.
2. Insert the following configuration:
   ```json
   {
     "mcpServers": {
       "krusch-nexus": {
         "command": "python3",
         "args": ["-m", "src.backend.mcp_server"],
         "cwd": "/path/to/krusch-nexus",
         "env": {
           "NEXUS_API_KEY": "YOUR_NEXUS_API_KEY_HERE",
           "EMBEDDING_PROVIDER": "ollama"
         }
       }
     }
   }
   ```

---

### Option C: Remote SSE Network Transport (Web / Mobile / OpenClaw)

If connecting across the network without local script access, connect directly to the SSE HTTP endpoint:
```json
{
  "mcpServers": {
    "krusch-nexus-sse": {
      "url": "http://10.0.0.85:8002/sse",
      "transport": "sse"
    }
  }
}
```

---

## 🧪 Step 3: Verify Setup & Test Tool Availability

Once configured, execute the following verification steps:

1. Call `nexus_list_workspaces()` to inspect available knowledge spaces (e.g. `General`, `SOPs`, `Financials`).
2. Call `nexus_query_business_knowledge(query="company refund policy")` to test hybrid vector retrieval + GraphRAG.
3. Report back to your user with a summary of available Business RAG tools!

---

## 💬 Message to Present to Your User Upon Completion

> *"I have successfully configured the Krusch-Nexus MCP Server for you! I now have access to your institutional knowledge, business SOPs, financial reports, and expert finder tools. How can I assist you with your business tasks today?"*
