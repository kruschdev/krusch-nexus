import { DBOS } from '@dbos-inc/dbos-sdk';
import { execSync } from 'child_process';
import { readFileSync, writeFileSync } from 'fs';
import pg from 'pg';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { randomUUID } from 'crypto';

// Configuration
const OLLAMA_URL = process.env.OLLAMA_URL || 'http://127.0.0.1:11434';
const CODING_MODEL = process.env.CODING_MODEL || 'qwen2.5-coder:7b';
const WORKER_ID = process.env.WORKER_ID || `worker-${process.pid}`;
const DATABASE_URL = process.env.DATABASE_URL || 'postgres://openclaw:openclaw_password@10.0.0.85:5434/kruschdb';

const pool = new pg.Pool({ connectionString: DATABASE_URL });
let mcpClient: Client | null = null;

async function initMcpClient() {
    if (mcpClient) return mcpClient;

    // Connect to the retrieval MCP locally on kruschdev
    const transport = new StdioClientTransport({
        command: 'bash',
        args: ['-c', 'cd /home/kruschdev/homelab/projects/krusch-nexus && mcp_env/bin/python3 -m src.backend.mcp_server'],
        env: process.env as Record<string, string>
    });
    
    mcpClient = new Client({ name: 'coding-agent', version: '1.0.0' }, { capabilities: {} });
    await mcpClient.connect(transport);
    return mcpClient;
}

const LOCAL_TOOLS = [
    {
        type: 'function',
        function: {
            name: 'run_shell_command',
            description: 'Execute a shell command on the worker node and return its output.',
            parameters: {
                type: 'object',
                properties: {
                    command: { type: 'string', description: 'The shell command to execute' },
                    cwd: { type: 'string', description: 'Working directory (optional)' }
                },
                required: ['command']
            }
        }
    },
    {
        type: 'function',
        function: {
            name: 'read_file',
            description: 'Read the contents of a file at the given path.',
            parameters: {
                type: 'object',
                properties: {
                    path: { type: 'string', description: 'Absolute path to the file' }
                },
                required: ['path']
            }
        }
    },
    {
        type: 'function',
        function: {
            name: 'write_file',
            description: 'Write content to a file at the given path.',
            parameters: {
                type: 'object',
                properties: {
                    path: { type: 'string', description: 'Absolute path to the file' },
                    content: { type: 'string', description: 'File content to write' }
                },
                required: ['path', 'content']
            }
        }
    },
    {
        type: 'function',
        function: {
            name: 'report_result',
            description: 'Report the final result of the task back to the orchestrator.',
            parameters: {
                type: 'object',
                properties: {
                    summary: { type: 'string', description: 'Human-readable summary of what was accomplished' },
                    success: { type: 'boolean', description: 'Whether the task completed successfully' }
                },
                required: ['summary', 'success']
            }
        }
    }
];

export class CodingAgentWorker {

    @DBOS.step()
    static async claimJob(): Promise<any | null> {
        const client = await pool.connect();
        try {
            await client.query('BEGIN');
            const res = await client.query(`
                SELECT job_id, job_type, payload
                FROM agent_execution_queue
                WHERE status = 'pending' AND job_type NOT IN ('debate_proposal', 'debate_rebuttal', 'human_review')
                ORDER BY started_at_ms ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            `);

            if (res.rows.length > 0) {
                const job = res.rows[0];
                await client.query(
                    `UPDATE agent_execution_queue SET status = 'running', picked_at_ms = $2 WHERE job_id = $1`,
                    [job.job_id, Date.now()]
                );
                await client.query('COMMIT');
                return job;
            }
            await client.query('ROLLBACK');
            return null;
        } catch (err) {
            await client.query('ROLLBACK').catch(() => {});
            throw err;
        } finally {
            client.release();
        }
    }

    @DBOS.step()
    static async completeJob(jobId: string, status: string, result: any) {
        await pool.query(
            `UPDATE agent_execution_queue SET status = $2, picked_at_ms = $3, result = $4 WHERE job_id = $1`,
            [jobId, status, Date.now(), JSON.stringify(result)]
        );
    }

    @DBOS.step()
    static async callOllama(messages: any[], tools: any[], model: string): Promise<any> {
        let apiUrl = OLLAMA_URL;
        let provider = 'ollama';

        // Check if model is OpenRouter (includes / or moonshot)
        if (model.includes('/') || model.includes('moonshot')) {
            provider = 'openrouter';
            apiUrl = 'https://openrouter.ai/api/v1';
        } else if (model.includes('gemini')) {
            provider = 'gemini';
            apiUrl = 'https://generativelanguage.googleapis.com/v1beta/models/' + model + ':generateContent?key=' + process.env.GEMINI_API_KEY;
        }

        if (provider === 'ollama' || provider === 'openrouter') {
            const bodyObj: any = {
                model: model,
                messages,
                temperature: 0.1
            };
            if (tools && tools.length > 0) {
                bodyObj.tools = tools;
            }
            try {
                const headers: any = { 'Content-Type': 'application/json' };
                let url = `${apiUrl}/v1/chat/completions`;
                
                if (provider === 'openrouter') {
                    headers['Authorization'] = `Bearer ${process.env.OPENROUTER_API_KEY}`;
                    headers['HTTP-Referer'] = 'http://localhost:3000';
                    headers['X-Title'] = 'Krusch Nexus';
                    url = `${apiUrl}/chat/completions`;
                }

                const response = await fetch(url, {
                    method: 'POST',
                    headers,
                    body: JSON.stringify(bodyObj),
                    signal: AbortSignal.timeout(120_000)
                });

                if (!response.ok) {
                    const body = await response.text().catch(() => '');
                    throw new Error(`${provider} HTTP ${response.status}: ${body.slice(0, 500)}`);
                }
                return await response.json();
            } catch (err: any) {
                DBOS.logger.error(`${provider} fetch failed for ${apiUrl}: ${err.message}`, err.stack);
                throw new Error(`${provider} fetch failed for ${apiUrl}: ${err.message}`);
            }
        } else {
            // Very simple Gemini tools wrapper (fallback)
            // Using @krusch/toolkit/llm is better, but this works inline
            // Actually, we can just use @krusch/toolkit chat!
            // For now, let's just throw if Gemini isn't fully implemented in this inline fetch, 
            // but we can import chat from @krusch/toolkit if needed.
            throw new Error('Gemini fallback needs @krusch/toolkit/llm integration');
        }
    }

    @DBOS.step()
    static async generateEmbedding(text: string): Promise<number[]> {
        let apiUrl = OLLAMA_URL;
        const model = 'bge-large:latest';
        try {
            const response = await fetch(`${apiUrl}/api/embeddings`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model, prompt: text }),
                signal: AbortSignal.timeout(30_000)
            });
            if (!response.ok) {
                const body = await response.text().catch(() => '');
                throw new Error(`Ollama HTTP ${response.status}: ${body.slice(0, 500)}`);
            }
            const data = await response.json();
            return data.embedding;
        } catch (err: any) {
            DBOS.logger.error(`Ollama embedding fetch failed: ${err.message}`);
            throw err;
        }
    }

    @DBOS.step()
    static async executeLocalTool(name: string, args: any): Promise<string> {
        switch (name) {
            case 'run_shell_command': {
                try {
                    const output = execSync(args.command, {
                        cwd: args.cwd || '/home/kruschdev/homelab',
                        timeout: 30_000,
                        encoding: 'utf-8',
                        maxBuffer: 1024 * 1024
                    });
                    return output.slice(0, 4000);
                } catch (err: any) {
                    return `Command failed (exit ${err.status}): ${(err.stderr || err.message).slice(0, 2000)}`;
                }
            }
            case 'read_file': {
                try {
                    return readFileSync(args.path, 'utf-8').slice(0, 8000);
                } catch (err: any) {
                    return `Failed to read file: ${err.message}`;
                }
            }
            case 'write_file': {
                try {
                    writeFileSync(args.path, args.content, 'utf-8');
                    return `File written successfully: ${args.path}`;
                } catch (err: any) {
                    return `Failed to write file: ${err.message}`;
                }
            }
            case 'report_result': {
                return JSON.stringify({ summary: args.summary, success: args.success });
            }
            default:
                throw new Error(`Unknown local tool: ${name}`);
        }
    }

    @DBOS.step()
    static async executeMcpTool(name: string, args: any): Promise<string> {
        const client = await initMcpClient();
        const result = await client.callTool({ name, arguments: args });
        const content = result.content as any[];
        if (result.isError) {
            return `Error: ${content[0]?.text}`;
        }
        return content[0]?.text;
    }

    @DBOS.workflow()
    static async executeJobWorkflow(job: any): Promise<void> {
        DBOS.logger.info(`Executing job ${job.job_id}`);

        try {
            const mcpClient = await initMcpClient();
            const mcpToolsRes = await mcpClient.listTools();
            const mcpTools = mcpToolsRes.tools.map((t: any) => ({
                type: 'function',
                function: {
                    name: t.name,
                    description: t.description,
                    parameters: t.inputSchema
                }
            }));
            
            const allTools = [...LOCAL_TOOLS, ...mcpTools];

            const messages: any[] = [
                {
                    role: 'system',
                    content: `You are the Krusch-Nexus Coding Agent running on ${WORKER_ID}. You receive tasks from the DBOS queue and execute them using the available tools. You have access to local file system tools and the 'krusch_context_search_code' tool to search the knowledge base. Always call report_result when finished to summarize what you accomplished. Be concise and efficient. Execute the task described in the user message.`
                },
                {
                    role: 'user',
                    content: typeof job.payload === 'string' ? job.payload : JSON.stringify(job.payload, null, 2)
                }
            ];

            const CASCADE_MODELS = [CODING_MODEL, 'qwen2.5-coder:14b', 'moonshotai/kimi-k3'];
            let currentModelIdx = 0;
            let currentModel = CASCADE_MODELS[currentModelIdx];

            const MAX_TURNS = 15;
            for (let turn = 0; turn < MAX_TURNS; turn++) {
                try {
                    const response = await CodingAgentWorker.callOllama(messages, allTools, currentModel);
                    const choice = response.choices?.[0];
                    if (!choice) throw new Error('No choices returned from Ollama');

                    const assistantMsg = choice.message;
                    messages.push(assistantMsg);

                    if (assistantMsg.tool_calls?.length > 0) {
                        for (const toolCall of assistantMsg.tool_calls) {
                            const fnName = toolCall.function.name;
                            const fnArgs = typeof toolCall.function.arguments === 'string'
                                ? JSON.parse(toolCall.function.arguments)
                                : toolCall.function.arguments;

                            let result = '';
                            if (LOCAL_TOOLS.some(t => t.function.name === fnName)) {
                                result = await CodingAgentWorker.executeLocalTool(fnName, fnArgs);
                            } else if (mcpTools.some((t: any) => t.function.name === fnName)) {
                                result = await CodingAgentWorker.executeMcpTool(fnName, fnArgs);
                            } else {
                                result = `Error: Unknown tool ${fnName}`;
                            }

                            messages.push({
                                role: 'tool',
                                tool_call_id: toolCall.id,
                                content: result
                            });

                            if (fnName === 'report_result') {
                                const parsed = JSON.parse(result);
                                if (!parsed.success && currentModelIdx < CASCADE_MODELS.length - 1) {
                                    // Cascade to next model
                                    currentModelIdx++;
                                    currentModel = CASCADE_MODELS[currentModelIdx];
                                    DBOS.logger.warn(`Job ${job.job_id} cascading to ${currentModel} due to low confidence/failure`);
                                    messages.push({
                                        role: 'system',
                                        content: `The previous attempt failed. Escalating to a more capable model (${currentModel}). Please review the context and try again.`
                                    });
                                    continue;
                                }

                                await CodingAgentWorker.completeJob(job.job_id, 'completed', parsed);
                                DBOS.logger.info(`Job ${job.job_id} completed successfully.`);
                                return;
                            }
                        }
                    } else {
                        await CodingAgentWorker.completeJob(job.job_id, 'completed', {
                            summary: assistantMsg.content?.slice(0, 1000) || 'No output',
                            success: true
                        });
                        DBOS.logger.info(`Job ${job.job_id} completed (text-only).`);
                        return;
                    }
                } catch (innerErr: any) {
                    if (currentModelIdx < CASCADE_MODELS.length - 1) {
                        currentModelIdx++;
                        currentModel = CASCADE_MODELS[currentModelIdx];
                        DBOS.logger.warn(`Job ${job.job_id} errored on ${CASCADE_MODELS[currentModelIdx-1]}, cascading to ${currentModel}`);
                        messages.push({
                            role: 'system',
                            content: `The previous attempt encountered an error: ${innerErr.message}. Escalating to a more capable model (${currentModel}). Please review the context and try again.`
                        });
                    } else {
                        throw innerErr;
                    }
                }
            }

            await CodingAgentWorker.completeJob(job.job_id, 'failed', { error: `Exceeded ${MAX_TURNS} turns` });
        } catch (err: any) {
            writeFileSync('/home/kruschdev/homelab/projects/krusch-nexus/src/coding-agent/error.txt', err.stack || err.message, 'utf-8');
            await CodingAgentWorker.completeJob(job.job_id, 'failed', { error: err.message });
            DBOS.logger.error(`Job ${job.job_id} failed: ${err.message}`);
        }
    }

    // Poll the queue every 10 seconds.
    @DBOS.scheduled({ crontab: "*/10 * * * * *" })
    @DBOS.workflow()
    static async pollQueueScheduler(schedDate: Date, startTime: Date): Promise<void> {
        const job = await CodingAgentWorker.claimJob();
        if (job) {
            await DBOS.startWorkflow(CodingAgentWorker).executeJobWorkflow(job);
        }
    }

    // Auto-ingest pipeline: sync homelab metadata every 5 minutes
    @DBOS.scheduled({ crontab: "0 */5 * * * *" })
    @DBOS.workflow()
    static async autoIngestPipeline(schedDate: Date, startTime: Date): Promise<void> {
        DBOS.logger.info("Running auto-ingest pipeline for homelab...");
        try {
            // sync_to_pg.js handles inline summarize and embedding for new blobs
            const output = execSync('node /home/kruschdev/homelab/projects/pg-git/scripts/sync_to_pg.js .', {
                cwd: '/home/kruschdev/homelab',
                timeout: 300_000, // 5 minutes
                encoding: 'utf-8',
                maxBuffer: 50 * 1024 * 1024
            });
            DBOS.logger.info(`Auto-ingest completed successfully.`);
        } catch (err: any) {
            DBOS.logger.error(`Auto-ingest failed: ${err.message}`);
        }
    }

    // Data Flywheel pipeline: Embed lessons learned every 2 minutes
    @DBOS.scheduled({ crontab: "0 */2 * * * *" })
    @DBOS.workflow()
    static async dataFlywheelPipeline(schedDate: Date, startTime: Date): Promise<void> {
        DBOS.logger.info("Running Data Flywheel pipeline...");
        const client = await pool.connect();
        try {
            const res = await client.query(`
                SELECT job_id, job_type, payload, result, status
                FROM agent_execution_queue
                WHERE status IN ('completed', 'failed') AND completed_at_ms = 0
                LIMIT 5
                FOR UPDATE SKIP LOCKED
            `);
            
            for (const job of res.rows) {
                DBOS.logger.info(`Extracting lessons learned for job ${job.job_id}`);
                
                const messages = [
                    {
                        role: 'system',
                        content: 'You are an expert AI architect. Review the provided task payload and its execution result. Extract 1-2 bullet points of technical lessons learned, successful strategies, or reasons for failure. Keep the insight extremely concise (under 2 sentences total).'
                    },
                    {
                        role: 'user',
                        content: `Job Status: ${job.status}\n\nPayload:\n${JSON.stringify(job.payload, null, 2)}\n\nResult:\n${JSON.stringify(job.result, null, 2)}`
                    }
                ];
                
                const response = await CodingAgentWorker.callOllama(messages, [], CODING_MODEL);
                const lessonContent = response.choices?.[0]?.message?.content || 'No lesson extracted';
                
                const embedding = await CodingAgentWorker.generateEmbedding(lessonContent);
                const embeddingStr = `[${embedding.join(',')}]`;
                
                const memoryId = randomUUID();
                await client.query(`
                    INSERT INTO ide_agent_memory (id, category, content, embedding)
                    VALUES ($1, 'lessons', $2, $3)
                `, [memoryId, lessonContent, embeddingStr]);
                
                await client.query(`
                    UPDATE agent_execution_queue
                    SET completed_at_ms = $2
                    WHERE job_id = $1
                `, [job.job_id, Date.now()]);
                
                DBOS.logger.info(`Successfully stored lesson ${memoryId} for job ${job.job_id}`);
            }
        } catch (err: any) {
            DBOS.logger.error(`Data Flywheel failed: ${err.message}`);
        } finally {
            client.release();
        }
    }
}

// Start DBOS if this file is executed directly
if (require.main === module || process.argv[1]?.endsWith('worker.ts') || process.argv[1]?.endsWith('worker.js')) {
    DBOS.launch().then(() => {
        DBOS.logger.info("Coding Agent DBOS Worker started successfully.");
    }).catch((err) => {
        console.error("Failed to start DBOS", err);
        process.exit(1);
    });
}
