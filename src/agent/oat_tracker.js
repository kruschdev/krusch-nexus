import pg from 'pg';
import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MATRIX_PATH = path.join(__dirname, 'oat_matrix.json');
const DATABASE_URL = process.env.DBOS_DATABASE_URL || process.env.DATABASE_URL || 'postgresql://openclaw:openclaw_password@10.0.0.85:5434/kruschdb';

async function train() {
    console.log(`Connecting to Postgres: ${DATABASE_URL.split('@')[1] || DATABASE_URL}`);
    const pool = new pg.Pool({ connectionString: DATABASE_URL });
    const client = await pool.connect();
    
    try {
        console.log('Fetching interaction memory traces to learn successful transition flows...');
        const res = await client.query(`
            SELECT action_trace 
            FROM interaction_memory 
            WHERE status = 'active' OR status = 'deprecated';
        `);
        
        console.log(`Found ${res.rows.length} execution states. Processing traces...`);
        
        // Count transitions: P(next_tool | current_tool)
        const counts = {}; // current_tool -> { next_tool -> count }
        const totals = {}; // current_tool -> total_count
        
        let traceCount = 0;
        for (const row of res.rows) {
            let trace = [];
            if (row.action_trace) {
                try {
                    trace = typeof row.action_trace === 'string' ? JSON.parse(row.action_trace) : row.action_trace;
                } catch (e) {
                    continue;
                }
            }
            if (!Array.isArray(trace)) trace = trace ? [trace] : [];
            if (trace.length === 0) continue;
            
            traceCount++;
            let current = 'START';
            
            for (const step of trace) {
                const next = step.action || step.tool || 'unknown';
                
                if (!counts[current]) counts[current] = {};
                counts[current][next] = (counts[current][next] || 0) + 1;
                totals[current] = (totals[current] || 0) + 1;
                
                current = next;
            }
            
            // transition to END
            if (!counts[current]) counts[current] = {};
            counts[current]['END'] = (counts[current]['END'] || 0) + 1;
            totals[current] = (totals[current] || 0) + 1;
        }
        
        if (traceCount === 0) {
            console.warn('⚠️ No valid execution traces found to train from.');
            return;
        }
        
        // Calculate probabilities
        const probabilities = {};
        for (const [current, nexts] of Object.entries(counts)) {
            probabilities[current] = {};
            const total = totals[current];
            for (const [next, count] of Object.entries(nexts)) {
                probabilities[current][next] = count / total;
            }
        }
        
        await fs.writeFile(MATRIX_PATH, JSON.stringify(probabilities, null, 2), 'utf-8');
        console.log(`✅ OAT transition matrix successfully trained from ${traceCount} traces and written to ${MATRIX_PATH}`);
        
    } finally {
        client.release();
        await pool.end();
    }
}

async function scoreTrajectory(memoryId) {
    console.log(`Loading OAT transition matrix from: ${MATRIX_PATH}`);
    let matrix = {};
    try {
        const matrixData = await fs.readFile(MATRIX_PATH, 'utf-8');
        matrix = JSON.parse(matrixData);
    } catch (err) {
        console.error(`Error loading matrix: ${err.message}. Please run with --train first.`);
        return;
    }
    
    const pool = new pg.Pool({ connectionString: DATABASE_URL });
    const client = await pool.connect();
    
    try {
        console.log(`Fetching memory trace for ID: ${memoryId}`);
        const res = await client.query(`
            SELECT action_trace, author_id, content 
            FROM interaction_memory 
            WHERE id = $1;
        `, [memoryId]);
        
        if (res.rows.length === 0) {
            console.error(`Memory ID '${memoryId}' not found.`);
            return;
        }
        
        const row = res.rows[0];
        let trace = [];
        if (row.action_trace) {
            try {
                trace = typeof row.action_trace === 'string' ? JSON.parse(row.action_trace) : row.action_trace;
            } catch (e) {
                console.error('Failed to parse trace JSON.');
                return;
            }
        }
        if (!Array.isArray(trace)) trace = trace ? [trace] : [];
        
        if (trace.length === 0) {
            console.log('Trajectory trace is empty.');
            return;
        }
        
        console.log(`\n=== 🔎 Out-of-Distribution Anomaly Score (OAT) ===`);
        console.log(`Agent/Author: ${row.author_id}`);
        console.log(`Content: "${row.content.substring(0, 100)}..."`);
        console.log(`Steps in trajectory: ${trace.length}\n`);
        
        let current = 'START';
        let stepIdx = 0;
        let cumulativeAnomalies = 0;
        
        for (const step of trace) {
            stepIdx++;
            const next = step.action || step.tool || 'unknown';
            
            const prob = (matrix[current] && matrix[current][next]) || 0;
            const statusEmoji = prob < 0.05 ? '🚨 [ANOMALY]' : '✅ [NORMAL]';
            
            console.log(`Step ${stepIdx}: ${current} -> ${next}`);
            console.log(`  Probability: ${(prob * 100).toFixed(1)}% | Status: ${statusEmoji}`);
            
            if (prob < 0.05) {
                cumulativeAnomalies++;
            }
            current = next;
        }
        
        const probEnd = (matrix[current] && matrix[current]['END']) || 0;
        const endEmoji = probEnd < 0.05 ? '🚨 [ANOMALY]' : '✅ [NORMAL]';
        console.log(`Step ${stepIdx + 1}: ${current} -> END`);
        console.log(`  Probability: ${(probEnd * 100).toFixed(1)}% | Status: ${endEmoji}`);
        if (probEnd < 0.05) cumulativeAnomalies++;
        
        const anomalyScore = cumulativeAnomalies / (trace.length + 1);
        console.log(`\n--- Final OAT Assessment ---`);
        console.log(`Anomaly Score: ${anomalyScore.toFixed(3)} (${cumulativeAnomalies} out-of-distribution transitions)`);
        if (anomalyScore > 0.3) {
            console.log(`⚠️  TRAJECTORY WARNING: High anomaly score. This agent run deviated significantly from normal successful trajectories.`);
        } else {
            console.log(`🟢  TRAJECTORY PASS: Routine successful flow.`);
        }
        
    } finally {
        client.release();
        await pool.end();
    }
}

async function main() {
    const args = process.argv.slice(2);
    if (args.includes('--train')) {
        await train();
    } else if (args.includes('--score')) {
        const idIndex = args.indexOf('--score') + 1;
        const memoryId = args[idIndex];
        if (!memoryId) {
            console.error('Usage: node oat_tracker.js --score <memory_uuid>');
            process.exit(1);
        }
        await scoreTrajectory(memoryId);
    } else {
        console.log(`
Krusch-Nexus OAT Trajectory Anomaly Tracker
Usage:
  node oat_tracker.js --train            Train transition matrix from historical successful runs
  node oat_tracker.js --score <UUID>      Score dynamic transition probabilities of a memory trace
`);
    }
}

main().catch(console.error);
