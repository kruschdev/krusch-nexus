import pg from 'pg';

const DATABASE_URL = process.env.DBOS_DATABASE_URL;
const pool = new pg.Pool({ connectionString: DATABASE_URL });

export async function getHistory(conversationId) {
    const res = await pool.query(
        `SELECT content FROM ide_agent_memory WHERE category = 'activity' AND id = $1`,
        [conversationId]
    );
    if (res.rows.length > 0) {
        try {
            return JSON.parse(res.rows[0].content);
        } catch(e) {
            return [];
        }
    }
    return [];
}

export async function saveHistory(conversationId, messages) {
    // Store in ide_agent_memory under activity, assuming id is a UUID
    await pool.query(
        `INSERT INTO ide_agent_memory (id, category, content) 
         VALUES ($1, 'activity', $2) 
         ON CONFLICT (id) DO UPDATE SET content = $2`,
        [conversationId, JSON.stringify(messages)]
    );
}
