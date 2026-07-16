#!/usr/bin/env bash
set -uo pipefail

# ---- CONFIG ----
NEW_ORG="omnibioai"
PKG_PREFIX="omnibioai-sif"     # -> ghcr.io/omnibioai/omnibioai-sif/<name>:<tag>
TAG="arm64"                     # matches the existing *_arm64.sif -> :arm64 convention
GH_USER="${GH_USER:?Set GH_USER env var to your GitHub username}"
GH_TOKEN="${GH_TOKEN:?Set GH_TOKEN env var with a PAT that has write:packages scope}"
SIF_DIR="${1:-.}"               # directory containing the .sif files, default: current dir
LOG_FILE="./sif_push.log"       # record of "name:tag" pairs already confirmed pushed
SKIPPED_FILE="./sif_skipped.txt"

touch "$LOG_FILE"
> "$SKIPPED_FILE"

if ! command -v oras > /dev/null 2>&1; then
  echo "oras not found on PATH. Install it first (see earlier instructions) then re-run."
  exit 1
fi

echo "$GH_TOKEN" | oras login ghcr.io -u "$GH_USER" --password-stdin

already_done() {
  grep -Fxq "$1" "$LOG_FILE" 2>/dev/null
}

mark_done() {
  echo "$1" >> "$LOG_FILE"
}

remote_tag_exists() {
  # $1 = full ref, e.g. ghcr.io/omnibioai/omnibioai-sif/interproscan:arm64
  oras manifest fetch "$1" > /dev/null 2>&1
}

shopt -s nullglob
count_total=0
count_pushed=0
count_skipped_existing=0
count_failed=0
count_no_match=0

for f in "$SIF_DIR"/*.sif; do
  count_total=$((count_total+1))
  base=$(basename "$f")

  case "$base" in
    *_arm64.sif)
      name="${base%_arm64.sif}"
      name=$(echo "$name" | tr '[:upper:]' '[:lower:]')
      ;;
    *)
      echo "  [no-match] $base does not follow <name>_arm64.sif pattern, skipping"
      echo "$base" >> "$SKIPPED_FILE"
      count_no_match=$((count_no_match+1))
      continue
      ;;
  esac

  dst="ghcr.io/${NEW_ORG}/${PKG_PREFIX}/${name}:${TAG}"
  key="${name}:${TAG}"

  if already_done "$key"; then
    echo "  [skip] $key already pushed (per $LOG_FILE)"
    count_skipped_existing=$((count_skipped_existing+1))
    continue
  fi

  if remote_tag_exists "$dst"; then
    echo "  [skip] $dst already exists at destination"
    mark_done "$key"
    count_skipped_existing=$((count_skipped_existing+1))
    continue
  fi

  echo "=== Pushing $base -> $dst ==="
  if oras push --disable-path-validation "$dst" "$f"; then
    mark_done "$key"
    count_pushed=$((count_pushed+1))
  else
    echo "  !! push failed for $base"
    count_failed=$((count_failed+1))
  fi
done

echo ""
echo "==== Summary ===="
echo "Total .sif files seen:      $count_total"
echo "Pushed this run:            $count_pushed"
echo "Already done (skipped):     $count_skipped_existing"
echo "Failed:                     $count_failed"
echo "Didn't match naming pattern: $count_no_match (see $SKIPPED_FILE)"
echo ""
echo "Review: https://github.com/orgs/${NEW_ORG}/packages"
echo "Safe to re-run this script to resume; it will skip everything already pushed."