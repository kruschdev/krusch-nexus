#!/bin/bash

# Lock models into VRAM on kruschserv (2080 Ti) indefinitely (-1)
echo "Locking qwen3.5:9b into VRAM..."
curl -s -X POST http://10.0.0.85:11434/api/generate -d '{"model": "qwen3.5:9b", "keep_alive": -1}' > /dev/null

echo "Locking bge-large into VRAM..."
curl -s -X POST http://10.0.0.85:11434/api/generate -d '{"model": "bge-large", "keep_alive": -1}' > /dev/null

echo "Models locked! They will not be evicted from VRAM until Ollama restarts."
