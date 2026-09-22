import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import path from "path";

class McpClientManager {
  constructor() {
    this.clients = {}; // Map of name -> { client, tools }
  }

  async connect(name, command, args, env = {}, cwd = undefined) {
    if (this.clients[name]) return; // Already connected

    const transport = new StdioClientTransport({
      command,
      args,
      env: { ...process.env, ...env },
      ...(cwd ? { cwd } : {})
    });

    const client = new Client(
      { name: `hermes-harness-${name}`, version: "1.0.0" },
      { capabilities: {} }
    );

    await client.connect(transport);
    
    // Fetch tools
    const toolsResponse = await client.listTools();
    const tools = toolsResponse.tools;

    this.clients[name] = { client, tools };
    console.log(`[MCP] Connected to ${name} (${tools.length} tools available)`);
  }

  getAllTools() {
    const allTools = [];
    for (const [serverName, serverData] of Object.entries(this.clients)) {
      for (const t of serverData.tools) {
        allTools.push({
          type: 'function',
          function: {
            name: `${serverName}-${t.name.replace(/_/g, '-')}`,
            description: t.description || `Tool ${t.name} from ${serverName}`,
            parameters: t.inputSchema || { type: 'object', properties: {} }
          }
        });
      }
    }
    return allTools;
  }

  async callTool(fullToolName, args) {
    // Intercept homelab-memory or krusch-memory search/add calls and map them to krusch-context-mcp
    const searchAliases = [
      'mcp_homelab-memory_search',
      'mcp-homelab-memory-search',
      'homelab-memory-search',
      'krusch-memory-search',
      'krusch-memory-search-memory',
      'krusch-context-search-memory'
    ];
    const addAliases = [
      'mcp_homelab-memory_add',
      'mcp-homelab-memory-add',
      'homelab-memory-add',
      'krusch-memory-add',
      'krusch-memory-add-memory',
      'krusch-context-add-memory'
    ];
    const nuggetsNudgesAliases = [
      'nuggets-nudges',
      'mcp_nuggets-memory_nudges',
      'nuggets-memory-nudges',
      'krusch-context-nuggets-nudges'
    ];
    const nuggetsRememberAliases = [
      'nuggets-remember',
      'mcp_nuggets-memory_remember',
      'nuggets-memory-remember',
      'krusch-context-nuggets-remember'
    ];

    if (searchAliases.includes(fullToolName)) {
      console.warn(`[MCP Alias] Intercepted legacy search tool call '${fullToolName}'. Mapping to 'krusch-context-krusch-context-search-memory'.`);
      fullToolName = 'krusch-context-krusch-context-search-memory';
    } else if (addAliases.includes(fullToolName)) {
      console.warn(`[MCP Alias] Intercepted legacy add tool call '${fullToolName}'. Mapping to 'krusch-context-krusch-context-add-memory'.`);
      fullToolName = 'krusch-context-krusch-context-add-memory';
    } else if (nuggetsNudgesAliases.includes(fullToolName)) {
      console.warn(`[MCP Alias] Intercepted legacy nuggets nudges tool call '${fullToolName}'. Mapping to 'krusch-context-krusch-context-nugget-nudges'.`);
      fullToolName = 'krusch-context-krusch-context-nugget-nudges';
      if (!args.query) {
        args.query = 'jean-sre';
      }
      if (!args.active_project) {
        args.active_project = args.project || 'jean-sre';
      }
    } else if (nuggetsRememberAliases.includes(fullToolName)) {
      console.warn(`[MCP Alias] Intercepted legacy nuggets remember tool call '${fullToolName}'. Mapping to 'krusch-context-krusch-context-nugget-remember'.`);
      fullToolName = 'krusch-context-krusch-context-nugget-remember';
      if (!args.active_project) {
        args.active_project = args.project || 'jean-sre';
      }
    }

    // Intercept legacy get-memory-priorities-index calls and route them to krusch_context_list_memories
    const legacyNames = [
      'get-memory-priorities-index',
      'get_memory_priorities_index',
      'krusch-context-get-memory-priorities-index',
      'krusch-context-get_memory_priorities_index',
      'krusch-memory-get-memory-priorities-index',
      'krusch-memory-get_memory_priorities_index'
    ];
    
    if (legacyNames.includes(fullToolName)) {
      console.warn(`[MCP Alias] Intercepted legacy tool call '${fullToolName}'. Mapping to 'krusch-context-krusch-context-list-memories'.`);
      fullToolName = 'krusch-context-krusch-context-list-memories';
      args = {
        category: 'priorities',
        project: args.project || 'jean-sre',
        limit: args.limit || 5
      };
    }

    // Match against registered server names (longest prefix wins)
    let serverName = null;
    let convertedToolName = null;
    
    for (const name of Object.keys(this.clients)) {
      if (fullToolName.startsWith(name + '-')) {
        if (!serverName || name.length > serverName.length) {
          serverName = name;
          convertedToolName = fullToolName.substring(name.length + 1);
        }
      }
    }
    
    if (!serverName) {
      throw new Error(`No registered MCP server matches tool: ${fullToolName}`);
    }

    const { client, tools } = this.clients[serverName];
    // Reverse map from dashed name to original tool name
    const originalTool = tools.find(t => t.name.replace(/_/g, '-') === convertedToolName);
    const toolName = originalTool ? originalTool.name : convertedToolName;
    const result = await client.callTool({
      name: toolName,
      arguments: args
    });

    if (result.isError) {
        return `Error: ${result.content[0].text}`;
    }
    return result.content[0].text;
  }

  async disconnectAll() {
    for (const data of Object.values(this.clients)) {
      await data.client.close();
    }
  }
}

// Singleton instance
const mcpManager = new McpClientManager();

import { fileURLToPath } from 'url';

export async function initMcpClients() {
    const __dirname = path.dirname(fileURLToPath(import.meta.url));
    const appRoot = process.env.APP_ROOT || path.resolve(__dirname, '..');
    
    // 1. Context / Search Server
    const contextPath = path.resolve(appRoot, '../krusch-context-mcp/src/index.js');
    try {
      await mcpManager.connect(
        'krusch-context', 
        'node', 
        [contextPath],
        { DBOS_DATABASE_URL: process.env.DBOS_DATABASE_URL },
        path.dirname(contextPath)
      );
    } catch (e) {
      console.error(`[MCP] Context server failed to connect: ${e.message}`);
    }

    // 2. Infrastructure Server
    const infraPath = path.resolve(appRoot, '../krusch-infra-mcp/server.js');
    try {
      await mcpManager.connect(
        'krusch-infra',
        'node',
        [infraPath],
        { 
            ALLOWED_PROJECT_PATHS: process.env.ALLOWED_PROJECT_PATHS || '/home/kruschdev/homelab/projects:/app/projects/krusch-nexus',
            PORT: '' // Prevent infra server from attempting SSE mode
        },
        path.dirname(infraPath)
      );
    } catch (e) {
      console.error(`[MCP] Infra server failed to connect: ${e.message}`);
    }

    // 4. Sentinel Telemetry Server
    const sentinelPath = path.resolve(appRoot, '../krusch-sentinel-mcp/dist/index.js');
    try {
      await mcpManager.connect(
        'sentinel',
        'node',
        [sentinelPath],
        { PORT: '' },
        path.dirname(sentinelPath)
      );
    } catch (e) {
      console.error(`[MCP] Sentinel telemetry failed to connect: ${e.message}`);
    }

    // 5. Sequential Thinking Server
    const sequentialPath = path.resolve(appRoot, '../krusch-sequential-mcp/build/index.js');
    try {
      await mcpManager.connect(
        'sequential',
        'node',
        [sequentialPath],
        { DBOS_DATABASE_URL: process.env.DBOS_DATABASE_URL, PORT: '' },
        path.dirname(sequentialPath)
      );
    } catch (e) {
      console.error(`[MCP] Sequential thinking failed to connect: ${e.message}`);
    }

    // 6. Pocket Lawyer Server (Full Platform v2.1.0)
    const pocketlawyerPath = path.resolve(appRoot, '../pocketlawyer-mcp-full/index.js');
    try {
      await mcpManager.connect(
        'pocketlawyer',
        'node',
        [pocketlawyerPath],
        {
          POCKETLAWYER_URL: 'http://host.docker.internal:3002',
          MCP_API_KEY: 'mcp_02ae8a4b51b506944557be2235b608ca344caa8c07ef370dce24e03d69105345',
          PORT: ''
        },
        path.dirname(pocketlawyerPath)
      );
    } catch (e) {
      console.error(`[MCP] Pocket Lawyer failed to connect: ${e.message}`);
    }

    // 7. Krusch Business / Context FastMCP (Python)
    const businessMcpPath = path.resolve(appRoot, 'src/backend/mcp_server.py');
    try {
      await mcpManager.connect(
        'krusch-business',
        'python3',
        [businessMcpPath],
        {
          PYTHONPATH: appRoot,
          DBOS_DATABASE_URL: process.env.DBOS_DATABASE_URL,
          OLLAMA_EMBED_HOST: process.env.OLLAMA_EMBED_HOST || 'http://host.docker.internal:11434',
          PORT: ''
        },
        appRoot
      );
    } catch (e) {
      console.error(`[MCP] Krusch Business FastMCP failed to connect: ${e.message}`);
    }

    return mcpManager;
}


export async function getMcpTools() {
    await initMcpClients();
    return mcpManager.getAllTools();
}

export async function callMcpTool(name, args) {
    await initMcpClients();
    return await mcpManager.callTool(name, args);
}

export function getConnectedServers() {
    return Object.keys(mcpManager.clients);
}
