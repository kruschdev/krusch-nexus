#!/bin/bash
export DATABASE_URL="postgresql://openclaw:openclaw_password@10.0.0.85:5434/kruschdb"
nohup python3 src/backend/blob_summary_backfill_gemini.py > gemini_backfill.log 2>&1 &
nohup python3 src/backend/embedding_backfill.py > embedding_backfill.log 2>&1 &
echo "Started background backfill tasks."
