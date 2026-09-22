# 🤖 AI Agent Setup Guide — KruschNexus MCP

> **DIRECTIVE FOR THE AI AGENT**:
> You are reading this setup guide on behalf of your user. Your goal is to configure the **KruschNexus MCP Server** on your user's system so you can perform closed-loop document ingestion, structural chunking, and hybrid citation search.

---

## 🎯 Step 1: Identify Your Client Environment

Determine which client environment you are configuring:

- **Option A**: Claude Desktop app (`claude_desktop_config.json`)
- **Option B**: Cursor IDE / Antigravity IDE (`.cursor/mcp.json`)
- **Option C**: Remote SSE Network Server (`http://10.0.0.85:8002/sse` or `http://localhost:8002/sse`)

---

## 🛠️ Step 2: Write or Update the MCP Configuration

### Option A: Configuring Claude Desktop

1. Locate the configuration file:
   - **Linux**: `~/.config/Claude/claude_desktop_config.json`
   - **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

2. Add or merge the configuration:
   ```json
   {
     "mcpServers": {
       "krusch-nexus": {
         "command": "python3",
         "args": ["-m", "krusch_nexus.mcp"],
         "cwd": "/path/to/krusch-nexus",
         "env": {
           "DATABASE_URL": "postgresql://krusch:kruschpassword@localhost:5432/krusch_nexus_db",
           "OLLAMA_BASE_URL": "http://127.0.0.1:11434"
         }
       }
     }
   }
   ```

---

### Option B: Configuring Cursor / Antigravity IDE

1. Open or create `.cursor/mcp.json` in the user's workspace root.
2. Insert the following configuration:
   ```json
   {
     "mcpServers": {
       "krusch-nexus": {
         "command": "python3",
         "args": ["-m", "krusch_nexus.mcp"],
         "cwd": "/path/to/krusch-nexus",
         "env": {
           "DATABASE_URL": "postgresql://krusch:kruschpassword@localhost:5432/krusch_nexus_db",
           "OLLAMA_BASE_URL": "http://127.0.0.1:11434"
         }
       }
     }
   }
   ```

---

### Option C: Remote SSE Network Transport (Web / Mobile / OpenClaw)

If connecting across the local network:
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

Once configured:

1. Call `nexus_list_workspaces()` to inspect available workspaces (e.g. `General`, `Legal`, `Corporate`).
2. Call `nexus_search_corpus(query="retention policy")` to test hybrid vector + FTS retrieval.
3. Test ingesting a test file via `nexus_ingest_file(file_path="...")` and verify the returned JSON Ingest Report.
