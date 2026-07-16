#!/usr/bin/env bash
#
# set_public_visibility.sh
# Bulk-sets visibility=public for container packages under the omnibioai org.
# Requires: org-level "allow public packages" setting already enabled
# (Org Settings -> Packages -> visibility policy) — the API call will 422
# otherwise.
#
# Usage:
#   ./set_public_visibility.sh --dry-run
#   ./set_public_visibility.sh --limit 10
#   ./set_public_visibility.sh                # all public-source images
#   ./set_public_visibility.sh --image-list images.txt

set -euo pipefail

DST_ORG="omnibioai"
DRY_RUN=false
LIMIT=0
IMAGE_LIST=""
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="visibility_${TIMESTAMP}.log"
FAILED_LOG="failed_visibility.log"

usage() {
  echo "Usage: $0 [--dry-run] [--limit N] [--image-list file.txt]"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --limit) LIMIT="$2"; shift 2 ;;
    --image-list) IMAGE_LIST="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown arg: $1"; usage ;;
  esac
done

command -v gh >/dev/null 2>&1 || { echo "gh CLI not found."; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq not found."; exit 1; }

if [[ -n "$IMAGE_LIST" ]]; then
  mapfile -t IMAGES < "$IMAGE_LIST"
else
  echo "Fetching current package list for ${DST_ORG}..." | tee -a "$LOG_FILE"
  IMAGES=($(gh api "/orgs/${DST_ORG}/packages?package_type=container&per_page=100" \
    --paginate --jq '.[].name'))
fi

TOTAL=${#IMAGES[@]}
echo "Found $TOTAL packages under ${DST_ORG}" | tee -a "$LOG_FILE"

: > "$FAILED_LOG"
count=0

for img in "${IMAGES[@]}"; do
  if [[ "$LIMIT" -gt 0 && "$count" -ge "$LIMIT" ]]; then
    echo "Limit of $LIMIT reached, stopping." | tee -a "$LOG_FILE"
    break
  fi

  CURRENT_VIS=$(gh api "/orgs/${DST_ORG}/packages/container/${img}" --jq '.visibility' 2>/dev/null || echo "unknown")

  echo "[$((count+1))/$TOTAL] ${img} (current: ${CURRENT_VIS})" | tee -a "$LOG_FILE"

  if [[ "$CURRENT_VIS" == "public" ]]; then
    echo "  already public, skipping" | tee -a "$LOG_FILE"
    count=$((count+1))
    continue
  fi

  if $DRY_RUN; then
    echo "  (dry-run, would PATCH to public)" | tee -a "$LOG_FILE"
  else
    if gh api --method PATCH "/orgs/${DST_ORG}/packages/container/${img}" \
        -f visibility=public >> "$LOG_FILE" 2>&1; then
      echo "  set to public" | tee -a "$LOG_FILE"
    else
      echo "  FAILED: $img" | tee -a "$LOG_FILE"
      echo "$img" >> "$FAILED_LOG"
    fi
  fi

  count=$((count+1))
done

echo "" | tee -a "$LOG_FILE"
echo "Done. $count packages processed." | tee -a "$LOG_FILE"
if [[ -s "$FAILED_LOG" ]]; then
  echo "Failures logged to $FAILED_LOG (likely means org policy still blocks public visibility)" | tee -a "$LOG_FILE"
fi
