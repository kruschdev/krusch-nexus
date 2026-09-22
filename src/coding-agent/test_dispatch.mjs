import pg from 'pg';

const DATABASE_URL = process.env.DATABASE_URL || 'postgres://openclaw:openclaw_password@10.0.0.85:5434/kruschdb';
const pool = new pg.Pool({ connectionString: DATABASE_URL });

async function insertTestJob() {
    const payload = {
        task: "Use the krusch_context_search_code tool to find out how authentication works in the berean project. Then write a file /tmp/auth_report.txt summarizing what you found."
    };
    
    await pool.query(`
        INSERT INTO agent_execution_queue (job_id, thread_id, job_type, status, payload, started_at_ms)
        VALUES (gen_random_uuid(), gen_random_uuid(), 'coding_task', 'pending', $1, extract(epoch from now()) * 1000)
    `, [JSON.stringify(payload)]);
    
    console.log("Test job inserted");
    process.exit(0);
}

insertTestJob().catch(console.error);
