/**
 * Model comparison for Jean's Thinker prompt.
 * Tests which local model best produces parseable investigation intents.
 */

const OLLAMA_URL = 'http://10.0.0.85:11434';
const MODELS = [
    'hermes3:8b',
    'qwen2.5-coder:7b',
    'qwen3.5:9b',
];

const TEST_PROMPT = `## JEAN: CODE OPTIMIZATION THINKER (no tools — analysis only)

### Context
No previous context.

### Current State
No state.

### Objective
Goal: "Code review and optimization"
Workflow: WORKFLOW_CODE_AUDIT: Read project files and analyze for optimization opportunities.

### Target Project
**berean** at \`/home/kruschdev/homelab/projects/berean\`

### Worker Capabilities
Your intents are dispatched to a worker on **kruschdev** which has the full homelab monorepo.
Workers can:
- **read_file**: Read any file at an absolute path
- **run_shell_command**: Run grep, find, wc, cat, head, jq, etc.
- **report_result**: Return structured findings

Workers **CANNOT** modify production code. This is READ-ONLY analysis.

### What to Look For
Pick 2-3 of these concrete checks for project **berean**:
1. **Toolkit migration**: Check package.json for direct deps that should use @krusch/toolkit (dotenv, node-cron, better-sqlite3, jsonwebtoken, @google/genai)
2. **Dead code**: Run \`grep -r 'require(' /home/kruschdev/homelab/projects/berean/src/ 2>/dev/null | head -20\` to find CommonJS require() in ESM projects
3. **Hardcoded secrets**: Run \`grep -rn 'password|api_key|secret' /home/kruschdev/homelab/projects/berean/src/ --include='*.js' --include='*.ts' 2>/dev/null | grep -v node_modules | head -10\`
4. **Large files**: Run \`find /home/kruschdev/homelab/projects/berean -name '*.js' | xargs wc -l 2>/dev/null | sort -rn | head -10\` to find oversized files

## TASK (respond with TEXT only — NO tool calls)
You are the THINKER. Plan 2-3 concrete file-reading investigations for **berean**.

Output EXACTLY this format:

### ANALYSIS
[Brief note on what you know about this project from context. If nothing, say so.]

### INVESTIGATION PLAN
List 2-3 concrete commands. Each MUST be an executable shell command or file read:
1. **Action**: read_file \`/home/kruschdev/homelab/projects/berean/package.json\` **Reason**: [Check dependencies for toolkit migration opportunities]
2. **Action**: run_shell_command \`grep -rn 'require(' /home/kruschdev/homelab/projects/berean/src/ --include='*.js' | head -20\` **Reason**: [Find CommonJS imports in ESM project]
3. **Action**: run_shell_command \`find /home/kruschdev/homelab/projects/berean -name '*.js' | xargs wc -l | sort -rn | head -10\` **Reason**: [Find oversized files]

### HYPOTHESIS
[What optimization opportunities do you expect to find in berean?]`;

// Intent parser (same as Jean's)
function parseThinkerPlan(thinkerOutput) {
    const intents = [];
    const planMatch = thinkerOutput.match(/### INVESTIGATION PLAN[\s\S]*?(?=###|$)/i);
    const searchText = planMatch ? planMatch[0] : thinkerOutput;
    const numberedItems = searchText.match(/\d+\.\s+\*\*[^*]+\*\*[^\d]*/g) || [];
    for (const item of numberedItems) {
        const cleanItem = item.replace(/\n/g, ' ').trim();
        intents.push({ task: cleanItem.slice(0, 200) });
    }
    if (intents.length === 0) {
        const cmdMatches = thinkerOutput.matchAll(/`((?:read_file|grep|find|cat|head|wc|ls|run_shell_command)\s+[^`]+)`/g);
        for (const m of cmdMatches) {
            if (intents.length < 3) intents.push({ task: `run: ${m[1].trim().slice(0, 150)}` });
        }
    }
    return intents;
}

async function testModel(model) {
    const start = Date.now();
    try {
        const res = await fetch(`${OLLAMA_URL}/api/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model,
                prompt: TEST_PROMPT,
                stream: false,
                options: { temperature: 0.3, num_predict: 1024 }
            })
        });
        const data = await res.json();
        const reply = data.response || '';
        const elapsed = ((Date.now() - start) / 1000).toFixed(1);
        const intents = parseThinkerPlan(reply);

        console.log(`\n${'═'.repeat(60)}`);
        console.log(`MODEL: ${model} (${elapsed}s, ${reply.length} chars)`);
        console.log(`INTENTS PARSED: ${intents.length}`);
        console.log(`${'─'.repeat(60)}`);

        if (intents.length > 0) {
            intents.forEach((i, idx) => console.log(`  ${idx + 1}. ${i.task}`));
        } else {
            console.log(`  ❌ NO PARSEABLE INTENTS`);
        }

        console.log(`${'─'.repeat(60)}`);
        console.log(`FULL OUTPUT (first 800 chars):`);
        console.log(reply.slice(0, 800));

        return { model, intents: intents.length, elapsed, chars: reply.length };
    } catch (e) {
        console.log(`\n❌ ${model}: ${e.message}`);
        return { model, intents: 0, elapsed: 0, chars: 0, error: e.message };
    }
}

async function main() {
    console.log('🔬 Jean Thinker Model Comparison');
    console.log(`Testing ${MODELS.length} models with identical code audit prompt\n`);

    const results = [];
    for (const model of MODELS) {
        console.log(`\n⏳ Testing ${model}...`);
        results.push(await testModel(model));
    }

    console.log(`\n\n${'═'.repeat(60)}`);
    console.log('SUMMARY');
    console.log(`${'═'.repeat(60)}`);
    console.log(`${'Model'.padEnd(25)} ${'Intents'.padEnd(10)} ${'Time'.padEnd(10)} Chars`);
    console.log(`${'─'.repeat(60)}`);
    for (const r of results) {
        const status = r.intents > 0 ? '✅' : '❌';
        console.log(`${status} ${r.model.padEnd(23)} ${String(r.intents).padEnd(10)} ${r.elapsed}s${' '.repeat(Math.max(0, 7 - String(r.elapsed).length))} ${r.chars}`);
    }
}

main();
