#!/usr/bin/env bash
set -euo pipefail

SRC_REAL="/home/manish/Desktop/machine/omnibioai-data/PubMed/Abstracts/_general_corpus"
SAMPLE_SIZE=20
TEST_ROOT="/tmp/gc_split_test_$$"
SRC="${TEST_ROOT}/_general_corpus"
CHUNK_SIZE=5
CHUNK_PREFIX="_general_corpus_chunk"

echo "[INFO] Setting up test sandbox at $TEST_ROOT"
mkdir -p "$SRC"

echo "[INFO] Copying $SAMPLE_SIZE sample files from real corpus (read-only, no move)..."
mapfile -t sample_files < <(find "$SRC_REAL" -maxdepth 1 -name "*.json" | head -n "$SAMPLE_SIZE")
for f in "${sample_files[@]}"; do
  cp "$f" "$SRC/"
done

COPIED=$(find "$SRC" -maxdepth 1 -name "*.json" | wc -l)
echo "[INFO] Copied $COPIED files into sandbox"

cd "$SRC"

find . -maxdepth 1 -name "*.json" -printf '%f\n' > "${TEST_ROOT}/gc_filelist.txt"
TOTAL=$(wc -l < "${TEST_ROOT}/gc_filelist.txt")
echo "[INFO] Test total files: $TOTAL"

split -l "$CHUNK_SIZE" -d -a 3 "${TEST_ROOT}/gc_filelist.txt" "${TEST_ROOT}/gc_chunk_"

for listfile in "${TEST_ROOT}"/gc_chunk_*; do
  idx="${listfile##*_}"
  dest="${TEST_ROOT}/${CHUNK_PREFIX}${idx}"
  mkdir -p "$dest"
  echo "[INFO] Moving $(wc -l < "$listfile") files -> $dest"
  xargs -a "$listfile" -I{} mv "$SRC/{}" "$dest/"
done

echo ""
echo "[VERIFY] Chunk directories created:"
ls -d "${TEST_ROOT}"/${CHUNK_PREFIX}* 2>/dev/null || { echo "[FAIL] No chunk dirs found"; exit 1; }

MOVED_TOTAL=$(find "${TEST_ROOT}" -maxdepth 2 -name "*.json" -path "*${CHUNK_PREFIX}*" | wc -l)
echo "[VERIFY] Files found across all chunks: $MOVED_TOTAL (expected $COPIED)"

REMAINING_IN_SRC=$(find "$SRC" -maxdepth 1 -name "*.json" | wc -l)
echo "[VERIFY] Files remaining in original sample dir: $REMAINING_IN_SRC (expected 0)"

if [ "$MOVED_TOTAL" -eq "$COPIED" ] && [ "$REMAINING_IN_SRC" -eq 0 ]; then
  echo "[PASS] Split logic verified successfully."
  echo "[INFO] Cleaning up tmp sandbox at $TEST_ROOT"
  rm -rf "$TEST_ROOT"
  echo "[DONE] Cleanup complete. Safe to run split_general_corpus.sh on the real directory."
else
  echo "[FAIL] Counts did not match — leaving $TEST_ROOT in place for inspection."
  exit 1
fi
