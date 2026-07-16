#!/usr/bin/env bash
# run_chunks.sh
# Runs embedding_engine.py over each _general_corpus_chunk* study,
# capping concurrency to avoid repeating the OOM/swap situation.

set -euo pipefail

PARENT="/home/manish/Desktop/machine/omnibioai-data/PubMed/Abstracts"
CHUNK_PREFIX="_general_corpus_chunk"
MAX_CONCURRENT=2   # tune based on observed RSS per chunk; start conservative
EMBED_MODEL="pubmedbert"

cd "$PARENT"

chunks=(${CHUNK_PREFIX}*)
echo "[INFO] Found ${#chunks[@]} chunks to process"

running=0
for chunk in "${chunks[@]}"; do
  echo "[START] $chunk"
  python -m ragbio.embeddings.embedding_engine --study "$chunk" --embed-model "$EMBED_MODEL" \
    > "logs_${chunk}.log" 2>&1 &

  running=$((running + 1))
  if [ "$running" -ge "$MAX_CONCURRENT" ]; then
    wait -n            # wait for any one background job to finish
    running=$((running - 1))
  fi
done

wait
echo "[DONE] All chunks processed."
