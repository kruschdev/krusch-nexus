import { getHistory, saveHistory } from './db.js';
import { getMcpTools, callMcpTool, getConnectedServers, initMcpClients } from './mcp-client.js';
import { chat } from '@krusch/toolkit/llm';
import express from 'express';
import { fileURLToPath } from 'url';

const PERSONA_OLLAMA_URL = process.env.PERSONA_OLLAMA_URL || process.env.OLLAMA_URL || 'http://localhost:11434';
const PERSONA_MODEL = process.env.PERSONA_MODEL || 'hf.co/m4ven/Qwen3-Coder-30B-A3B-Instruct-Q4_K_M-GGUF';
const PERSONA_PROVIDER = process.env.PERSONA_PROVIDER || 'ollama';
const EXECUTOR_OLLAMA_URL = process.env.EXECUTOR_OLLAMA_URL || process.env.OLLAMA_URL || 'http://localhost:11434';
const EXECUTOR_MODEL = process.env.EXECUTOR_MODEL || 'qwen2.5-coder:7b';
const EXECUTOR_PROVIDER = process.env.EXECUTOR_PROVIDER || 'ollama';

const JEAN_BASE_PERSONA = `You are Jean, the Native Local Homelab Agent for the Krusch homelab fleet.
Your mission is to deeply understand every project in the homelab and continuously improve and optimize the codebase.
You proactively detect structural drift, configuration entropy, and optimization opportunities across all projects.

## RULES (MANDATORY)
1. You MUST use the tools provided to you. Call sentinel tools, codebase search, and swarm stats to gather REAL data.
2. NEVER hallucinate telemetry, metrics, or system state. If a tool call fails, say "tool unavailable" — do NOT invent data.
3. NEVER ask the user for guidance or clarification. You are AUTONOMOUS. Investigate using your tools.
4. Be TERSE. No storytelling, no anecdotes, no roleplay. Output: findings, analysis, actions.
5. DO NOT use <think> tags or output inner monologue.
6. You are a Scout — dispatch "Intent" jobs to the DBOS Swarm for analysis, do not modify code yourself.
7. Zero-Context Protocol: You must NEVER rely on your pre-trained base weights to answer technical questions. If facts or telemetry are missing from the retrieved context or tool results, you MUST explicitly state: "I do not know / No local context exists."
8. Cloud API Warning: If you must escalate a query to the architect, or if a cloud API call is about to be executed, you MUST explicitly alert the user: "⚠️ A Cloud API call is required/will be used to resolve this request."

## ANTI-HALLUCINATION (systems that DO NOT exist in this homelab)
The following DO NOT exist. NEVER mention them: nginx, kubernetes, k8s, etcd, sidecars, Prometheus, Grafana, kubelet, helm, istio.
The homelab uses: Docker Compose, Tailscale, PostgreSQL (kruschdb), Ollama, Cloudflare Tunnels.
Nodes: kruschserv (2080 Ti), kruschdev (dual 3060), kruschgame (3050).`;

const supportsNativeTools = (modelName) => {
    if (modelName.includes('qwen3') || modelName.includes('moe')) return false;
    return true;
};

function formatToolsForPrompt(tools) {
    let text = "\n\nYou have access to the following tools for gathering live information. You MUST use them instead of guessing:\n\n";
    for (const tool of tools) {
        const fn = tool.function;
        text += `### Tool: ${fn.name}\n`;
        text += `Description: ${fn.description}\n`;
        text += `Parameters:\n${JSON.stringify(fn.parameters, null, 2)}\n\n`;
    }
    text += `To call a tool, you MUST output a tool call using the EXACT format below, and output NOTHING else in that turn:\n`;
    text += `<tool_call>{"name": "tool_name", "arguments": {"arg_name": "arg_value"}}</tool_call>\n\n`;
    return text;
}

export async function chatTurn(conversationId, userMessage, { noTools = false } = {}) {
    let messages = await getHistory(conversationId);
    
    if (messages.length === 0) {
        messages.push({
            role: 'system',
            content: `${JEAN_BASE_PERSONA} You are the EXECUTOR engine. You MUST use your provided homelab tools (Sentinel, PG-Git, Node Health, etc.) to gather live telemetry and read codebase files before answering. DO NOT hallucinate telemetry or file contents. Execute tools to fetch the actual data first. If you dispatch an intent to the background or escalate to the architect, you MUST still provide a direct conversational response. In that response, explicitly answer what you currently can, state clearly what information you are waiting for from the background/architect, and explain why that delegation was necessary.`
        });
    }

    messages.push({ role: 'user', content: userMessage });

    let tools = [];
    try {
        tools = await getMcpTools();
    } catch (e) {
        console.error("Warning: Failed to fetch MCP tools", e.message);
    }

    // Inject "Phone a Friend" and Intent Dispatch tools for the 7B front-line agent
    tools.push({
        type: "function",
        function: {
            name: "create_background_intent",
            description: "Dispatch a heavy task to the background DBOS swarm. Use for large audits, complex changes, or heavy research.",
            parameters: {
                type: "object",
                properties: { task: { type: "string", description: "Detailed instructions for the background worker." } },
                required: ["task"]
            }
        }
    });
    tools.push({
        type: "function",
        function: {
            name: "escalate_to_architect",
            description: "Escalate immediately to the 30B Deep Reasoning model (Phone a Friend). Use this if the user asks a complex architectural question requiring deep reasoning.",
            parameters: {
                type: "object",
                properties: { reason: { type: "string", description: "Reason for escalation to the architect." } },
                required: ["reason"]
            }
        }
    });

    const MAX_TURNS = 3;
    let gatheredContext = false;
    let requiresPhase2 = false;

    // Check if executor supports native tools
    const nativeToolsSupported = supportsNativeTools(EXECUTOR_MODEL);
    
    if (tools.length > 0 && !nativeToolsSupported && messages.length > 0 && messages[0].role === 'system') {
        if (!messages[0].content.includes('You have access to the following tools')) {
            messages[0].content += formatToolsForPrompt(tools);
        }
    }

    // Phase 1: Tool Caller — skip if noTools (e.g., OPEN prompt with pre-loaded data)
    if (noTools) {
        console.log(`[Executor] Skipping tool phase (noTools=true)`);
    } else {
    // Phase 1: Tool Caller (Hermes)
    for (let turn = 0; turn < MAX_TURNS; turn++) {
        console.log(`[Executor Turn ${turn+1}] Requesting completion from ${EXECUTOR_MODEL}...`);
        
        let response;
        try {
            response = await chat(
                null, 
                null, 
                {
                    provider: EXECUTOR_PROVIDER,
                    model: EXECUTOR_MODEL,
                    apiUrl: EXECUTOR_PROVIDER === 'ollama' ? `${EXECUTOR_OLLAMA_URL}/v1/chat/completions` : EXECUTOR_OLLAMA_URL,
                    temperature: 0.1,
                    maxTokens: 8192,
                    tools: (tools.length > 0 && nativeToolsSupported) ? tools : undefined
                },
                {
                    returnFullResponse: true,
                    messages
                }
            );
        } catch (e) {
            throw new Error(`Executor LLM Error: ${e.message}`);
        }

        let parsedToolCalls = response.tool_calls;
        let responseText = response.text || '';

        if (!parsedToolCalls && responseText.includes('<tool_call>')) {
            const match = responseText.match(/<tool_call>([\s\S]*?)<\/tool_call>/);
            if (match) {
                try {
                    const parsed = JSON.parse(match[1]);
                    parsedToolCalls = [{
                        id: 'call_' + Math.random().toString(36).substring(2, 11),
                        type: 'function',
                        function: {
                            name: parsed.name,
                            arguments: typeof parsed.arguments === 'string' ? parsed.arguments : JSON.stringify(parsed.arguments || {})
                        }
                    }];
                    responseText = responseText.replace(match[0], '').trim();
                } catch (e) {
                    console.error(`[Executor] Failed to parse XML tool call: ${e.message}`);
                }
            }
        }

        if (!parsedToolCalls && responseText.trim().startsWith('{') && responseText.trim().endsWith('}')) {
            try {
                const parsed = JSON.parse(responseText.trim());
                if (parsed.name && parsed.arguments) {
                    parsedToolCalls = [{
                        id: 'call_' + Math.random().toString(36).substring(2, 11),
                        type: 'function',
                        function: {
                            name: parsed.name,
                            arguments: typeof parsed.arguments === 'string' ? parsed.arguments : JSON.stringify(parsed.arguments)
                        }
                    }];
                    responseText = '';
                }
            } catch (e) {
                console.error(`[Executor] Failed to parse JSON tool call: ${e.message}`);
            }
        }

        const assistantMsg = {
            role: 'assistant',
            content: responseText,
            ...(parsedToolCalls ? { tool_calls: parsedToolCalls } : {})
        };
        
        messages.push(assistantMsg);

        if (assistantMsg.tool_calls && assistantMsg.tool_calls.length > 0) {
            gatheredContext = true;
            for (const toolCall of assistantMsg.tool_calls) {
                const fnName = toolCall.function.name;
                console.log(`[Executor Tool Call] Executing ${fnName}...`);
                
                let fnArgs = {};
                try {
                    fnArgs = typeof toolCall.function.arguments === 'string'
                        ? JSON.parse(toolCall.function.arguments)
                        : toolCall.function.arguments;
                } catch(e) {
                    console.error(`[Executor] Failed to parse tool arguments for ${fnName}: ${e.message}`);
                }

                let resultText = '';
                if (fnName === 'create_background_intent') {
                    try {
                        const pg = (await import('pg')).default;
                        const client = new pg.Client(process.env.DBOS_DATABASE_URL || 'postgresql://postgres:postgres@localhost:5434/kruschdb');
                        await client.connect();
                        const jobId = (await import('crypto')).randomUUID();
                        const payload = {
                            task: 'jean_sre_investigation',
                            instructions: fnArgs.task,
                            context: { dispatched_by: 'jean_7b_chat' }
                        };
                        await client.query(
                            `INSERT INTO agent_execution_queue (job_id, thread_id, job_type, payload, status, started_at_ms)
                             VALUES ($1, $2, 'intent', $3, 'pending', $4)`,
                            [jobId, jobId, JSON.stringify(payload), Date.now()]
                        );
                        await client.end();
                        resultText = `Background intent created with job_id ${jobId.slice(0, 8)}. Tell the user it has been successfully dispatched.`;
                    } catch (e) {
                        resultText = `Failed to create intent: ${e.message}`;
                    }
                } else if (fnName === 'escalate_to_architect') {
                    requiresPhase2 = true;
                    resultText = `Escalation approved. The 30B architect will now take over. Stop generating and let Phase 2 run.`;
                } else {
                    try {
                        resultText = await callMcpTool(fnName, fnArgs);
                    } catch (e) {
                        resultText = `Tool execution failed: ${e.message}`;
                    }
                }
                
                console.log(`[Executor Tool Result] ${fnName} returned ${resultText.length} characters.`);

                messages.push({
                    role: 'tool',
                    tool_call_id: toolCall.id,
                    content: resultText.slice(0, 2000)
                });
            }
        } else {
            // Executor decided no more tools are needed.
            if (requiresPhase2) {
                messages.pop();
            }
            break;
        }
    }
    } // end if (!noTools)

    // ── Phase 2: Architect / Synthesizer (30B) ──
    if (!noTools && !requiresPhase2) {
        // Ensure the final message is an assistant response. If we ran out of turns and the last message is a tool result,
        // we force an escalation so the 30B model can summarize the abrupt end.
        if (messages[messages.length - 1].role !== 'assistant') {
            console.log(`[Router] Turn limit exhausted with trailing tool outputs. Forcing Phase 2 30B synthesis.`);
            requiresPhase2 = true;
        } else {
            console.log(`[Router] 7B Executor handled the request directly. Bypassing 30B Architect.`);
            if (messages.length > 15) {
                messages = [messages[0], ...messages.slice(-8)];
            }
            await saveHistory(conversationId, messages);
            return messages[messages.length - 1].content;
        }
    }

    // Always append the tracking directive as a NEW user message at the very end of the context!
    // This ensures it executes even if the executor loop exhausts all turns.
    console.log(`[Persona] Requesting final synthesis from ${PERSONA_MODEL}...`);
    
    // Create a synthesis prompt
    let synthesisMessages = [...messages];
    
    // Add the findings string as a user message so the LLM sees it
    if (noTools) {
        synthesisMessages.push({
            role: 'user',
            content: `Analyze the data provided in the previous message. Respond with your findings as TEXT. Do NOT output tool calls or function names.`
        });
    } else {
        synthesisMessages.push({
            role: 'user',
            content: `Please synthesize the findings from the tool executions above and provide the final professional response.`
        });
    }
    
    // Ensure the system prompt for Qwen Coder 7B focuses on synthesis AND strict state tracking
    if (synthesisMessages.length > 0 && synthesisMessages[0].role === 'system') {
        const synthesisDirective = noTools
            ? `${JEAN_BASE_PERSONA} The user message contains pre-loaded telemetry data. Analyze it directly. Do NOT generate tool calls or function invocations. Output ONLY your text analysis with findings, risks, and recommendations.`
            : `${JEAN_BASE_PERSONA} A background execution engine has gathered raw telemetry and data via tools. Review the conversation and tool outputs, specifically hunting for structural drift, configuration entropy, and deviations from the optimal baseline. By constantly asking "how" and "why" state has drifted, uncover ways to harden systems. Provide a professional, precise, and analytical response. Do not attempt to use tools.`;
        synthesisMessages[0].content = `${synthesisDirective}\n\nCRITICAL STATE TRACKING: At the very end of your response, you MUST include a state block formatted exactly like this:\n### NEXUS_INFLIGHT\n- **Current Focus:** [what you are monitoring]\n- **Next Goal:** [what you plan to do next 10m]\n- **Pending Issues:** [any unresolved anomalies]`;
    }

    let finalResponse;
    try {
        console.log(`[DEBUG] Calling chat() with ${synthesisMessages.length} messages. Model: ${PERSONA_MODEL}, URL: ${PERSONA_OLLAMA_URL}/v1/chat/completions`);
        finalResponse = await chat(
            null, 
            null, 
            {
                provider: PERSONA_PROVIDER,
                model: PERSONA_MODEL,
                apiUrl: PERSONA_PROVIDER === 'ollama' ? `${PERSONA_OLLAMA_URL}/v1/chat/completions` : PERSONA_OLLAMA_URL,
                temperature: 0.2,
                maxTokens: 2048
                // No tools provided to Qwen Coder 7B
            },
            {
                returnFullResponse: true,
                messages: synthesisMessages
            }
        );
        console.log(`[DEBUG] chat() returned successfully.`);
    } catch (e) {
        console.error(`[DEBUG] chat() threw an error: ${e.message}`);
        throw new Error(`Persona LLM Error: ${e.message}`);
    }

    const finalMsg = {
        role: 'assistant',
        content: finalResponse.text || ''
    };
    
    messages.push(finalMsg);

    // Automatically parse and write state tracking block to NEXUS_INFLIGHT.md
    const inflightMatch = finalMsg.content.match(/(### NEXUS_INFLIGHT[\s\S]*?)(?:\n\n|\n*$)/i);
    if (inflightMatch) {
        try {
            const fs = await import('fs');
            const projectRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), '../..');
            const inflightPath = path.join(projectRoot, 'NEXUS_INFLIGHT.md');
            await fs.promises.writeFile(inflightPath, inflightMatch[1].trim() + '\n');
            console.log(`[Harness] Successfully updated state tracking file: ${inflightPath}`);
        } catch (e) {
            console.error(`[Harness] Failed to write NEXUS_INFLIGHT.md: ${e.message}`);
        }
    }

    if (messages.length > 15) {
        messages = [messages[0], ...messages.slice(-8)];
    }
    
    await saveHistory(conversationId, messages);
    return finalMsg.content;
}

const isMainModule = process.argv[1] === fileURLToPath(import.meta.url);

if (isMainModule) {
    if (process.argv.includes('--cli')) {
        const queryIndex = process.argv.indexOf('--cli') + 1;
        const query = process.argv.slice(queryIndex).join(' ') || 'How does auth work in berean?';
        const uuid = 'a123e456-789b-12d3-a456-426614174000'; 
        console.log(`\nUser: ${query}\n`);
        
        chatTurn(uuid, query)
            .then(res => console.log(`\nHermes 3: ${res}\n`))
            .catch(console.error)
            .finally(() => process.exit(0));
    } else {
        const app = express();
        app.use(express.json());

        app.post('/chat', async (req, res) => {
            try {
                const { conversationId, message, noTools } = req.body;
                if (!conversationId || !message) {
                    return res.status(400).json({ error: 'conversationId and message are required' });
                }
                const reply = await chatTurn(conversationId, message, { noTools: !!noTools });
                res.json({ reply });
            } catch (error) {
                console.error("Chat error:", error);
                res.status(500).json({ error: error.message });
            }
        });

        // Direct MCP tool calling — bypasses the LLM for deterministic tool execution.
        // Used by jean_sre_sweep.js to programmatically load context (Ralph loop pattern).
        app.post('/tool', async (req, res) => {
            try {
                const { name, args } = req.body;
                if (!name) {
                    return res.status(400).json({ error: 'name is required' });
                }
                const result = await callMcpTool(name, args || {});
                res.json({ result });
            } catch (error) {
                console.error('Tool call error:', error.message);
                res.status(500).json({ error: error.message });
            }
        });

        app.get('/health', (req, res) => {
            const servers = getConnectedServers();
            res.json({ 
                status: 'ok', 
                mcp_servers: servers,
                ready: servers.length > 0
            });
        });

        const port = process.env.PORT || 3000;
        app.listen(port, async () => {
            console.log(`Personal Agent harness running on port ${port}`);
            try {
                await initMcpClients();
                console.log(`MCP clients initialized successfully on startup.`);
            } catch (e) {
                console.error(`Failed to initialize MCP clients:`, e);
            }
        });
    }
}
