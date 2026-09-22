const CODING_MODEL = process.env.CODING_MODEL || 'qwen2.5-coder:7b';
const OLLAMA_URL = process.env.OLLAMA_URL || 'http://10.0.0.85:11434';

async function test() {
    console.log("Testing completion...");
    try {
        const res = await fetch(`${OLLAMA_URL}/v1/chat/completions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: CODING_MODEL,
                messages: [{role: 'user', content: 'Say hello.'}],
                temperature: 0.1
            })
        });
        const data = await res.json();
        console.log("Completion response:", data.choices?.[0]?.message?.content);
    } catch (e) {
        console.error("Completion failed:", e.message);
    }

    console.log("Testing embeddings...");
    try {
        const res2 = await fetch(`${OLLAMA_URL}/api/embeddings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: 'nomic-embed-text:latest',
                prompt: 'Say hello.'
            })
        });
        const data2 = await res2.json();
        console.log("Embedding length:", data2.embedding?.length);
    } catch (e) {
        console.error("Embedding failed:", e.message);
    }
}
test();
