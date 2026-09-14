#!/usr/bin/env bash
# split_general_corpus.sh
# Splits _general_corpus/*.json into chunk subdirectories of CHUNK_SIZE files each,
# so ragbio.embeddings.embedding_engine can be run per-chunk via --study <chunk_name>
# without any code changes.

set -euo pipefail

SRC="/home/manish/Desktop/machine/omnibioai-data/PubMed/Abstracts/_general_corpus"
PARENT="$(dirname "$SRC")"
CHUNK_SIZE=500000
CHUNK_PREFIX="_general_corpus_chunk"

cd "$SRC"

echo "[INFO] Listing files (this may take a bit for 28M entries)..."
# Strip find's leading ./ with POSIX shell parameter expansion.
find . -maxdepth 1 -name "*.json" -exec sh -c '
  for path do
    printf "%s\n" "${path#./}"
  done
' sh {} + > /tmp/gc_filelist.txt
TOTAL=$(wc -l < /tmp/gc_filelist.txt)
echo "[INFO] Total files: $TOTAL"

split -l "$CHUNK_SIZE" -d -a 3 /tmp/gc_filelist.txt /tmp/gc_chunk_

for listfile in /tmp/gc_chunk_*; do
  idx="${listfile##*_}"
  dest="${PARENT}/${CHUNK_PREFIX}${idx}"
  mkdir -p "$dest"
  echo "[INFO] Moving $(wc -l < "$listfile") files -> $dest"
  # Feed the list on stdin; BSD xargs has no GNU -a option.
  xargs -I{} mv "$SRC/{}" "$dest/" < "$listfile"
done

echo "[DONE] Split complete. Chunks created under $PARENT/${CHUNK_PREFIX}*"
rmdir "$SRC" 2>/dev/null || echo "[NOTE] $SRC not empty or already removed, leaving as-is"
