#!/usr/bin/env bash
#
# migrate_public_images.sh
# Migrates only PUBLIC container images from ghcr.io/man4ish -> ghcr.io/omnibioai
# Skips any package whose visibility is "private" (leaves those for the separate
# PAT-authenticated migration).
#
# Requirements:
#   - skopeo, gh CLI, jq
#   - gh CLI authenticated (`gh auth login` or GH_TOKEN env var) — used only
#     to list packages + visibility, separate from the skopeo copy creds below.
#
# Required env vars (source and destination use DIFFERENT users, so skopeo's
# single stored login isn't enough — pass creds explicitly per copy instead):
#   SRC_GHCR_USER   e.g. man4ish
#   SRC_GHCR_TOKEN  read token for man4ish packages (e.g. $GHCR_PULL_TOKEN)
#   DST_GHCR_USER   your omnibioai-side username
#   DST_GHCR_TOKEN  push-capable token for omnibioai
#
# Usage:
#   export SRC_GHCR_USER=man4ish SRC_GHCR_TOKEN="$GHCR_PULL_TOKEN" \
#          DST_GHCR_USER=<you> DST_GHCR_TOKEN="$OMNIBIOAI_PUSH_TOKEN"
#   ./migrate_public_images.sh --dry-run
#   ./migrate_public_images.sh --limit 10
#   ./migrate_public_images.sh                # full run, all public images

set -euo pipefail

SRC_ORG="man4ish"
DST_ORG="omnibioai"
GHCR="ghcr.io"
DRY_RUN=false
LIMIT=0
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="migration_public_${TIMESTAMP}.log"
FAILED_LOG="failed_public_images.log"
SKIPPED_LOG="skipped_private_images.log"

usage() {
  echo "Usage: $0 [--dry-run] [--limit N]"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --limit) LIMIT="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown arg: $1"; usage ;;
  esac
done

command -v skopeo >/dev/null 2>&1 || { echo "skopeo not found. Install with: sudo apt install -y skopeo"; exit 1; }
command -v gh >/dev/null 2>&1 || { echo "gh CLI not found. Install from https://cli.github.com"; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq not found. Install with: sudo apt install -y jq"; exit 1; }

: "${SRC_GHCR_USER:?Set SRC_GHCR_USER (e.g. man4ish)}"
: "${SRC_GHCR_TOKEN:?Set SRC_GHCR_TOKEN (read token for man4ish packages)}"
: "${DST_GHCR_USER:?Set DST_GHCR_USER (your omnibioai-side username)}"
: "${DST_GHCR_TOKEN:?Set DST_GHCR_TOKEN (push-capable token for omnibioai)}"

echo "Fetching package list + visibility for ${SRC_ORG}..." | tee -a "$LOG_FILE"

# Pull name + visibility together so we don't need a second API call per package
PACKAGES_JSON=$(gh api "/users/${SRC_ORG}/packages?package_type=container&per_page=100" --paginate)

PUBLIC_IMAGES=($(echo "$PACKAGES_JSON" | jq -r '.[] | select(.visibility=="public") | .name'))
PRIVATE_IMAGES=($(echo "$PACKAGES_JSON" | jq -r '.[] | select(.visibility!="public") | .name'))

echo "Public images found: ${#PUBLIC_IMAGES[@]}" | tee -a "$LOG_FILE"
echo "Private images skipped: ${#PRIVATE_IMAGES[@]}" | tee -a "$LOG_FILE"

: > "$SKIPPED_LOG"
for img in "${PRIVATE_IMAGES[@]:-}"; do
  [[ -n "$img" ]] && echo "$img" >> "$SKIPPED_LOG"
done

: > "$FAILED_LOG"
TOTAL=${#PUBLIC_IMAGES[@]}
count=0

for img in "${PUBLIC_IMAGES[@]}"; do
  if [[ "$LIMIT" -gt 0 && "$count" -ge "$LIMIT" ]]; then
    echo "Limit of $LIMIT reached, stopping." | tee -a "$LOG_FILE"
    break
  fi

  SRC="docker://${GHCR}/${SRC_ORG}/${img}:latest"
  DST="docker://${GHCR}/${DST_ORG}/${img}:latest"

  echo "[$((count+1))/$TOTAL] $SRC -> $DST" | tee -a "$LOG_FILE"

  if $DRY_RUN; then
    echo "  (dry-run, skipping actual copy)" | tee -a "$LOG_FILE"
  else
    if skopeo copy --all \
        --src-creds "${SRC_GHCR_USER}:${SRC_GHCR_TOKEN}" \
        --dest-creds "${DST_GHCR_USER}:${DST_GHCR_TOKEN}" \
        "$SRC" "$DST" >> "$LOG_FILE" 2>&1; then
      echo "  OK" | tee -a "$LOG_FILE"
    else
      echo "  FAILED: $img" | tee -a "$LOG_FILE"
      echo "$img" >> "$FAILED_LOG"
    fi
  fi

  count=$((count+1))
done

echo "" | tee -a "$LOG_FILE"
echo "Done. $count public images processed." | tee -a "$LOG_FILE"
echo "Skipped ${#PRIVATE_IMAGES[@]} private images (see $SKIPPED_LOG)." | tee -a "$LOG_FILE"
if [[ -s "$FAILED_LOG" ]]; then
  echo "Some copies failed — see $FAILED_LOG" | tee -a "$LOG_FILE"
fi
