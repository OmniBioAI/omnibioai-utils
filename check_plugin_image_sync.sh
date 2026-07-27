#!/usr/bin/env bash
#
# check_plugin_image_sync.sh
#
# Read-only gap check for plugin Docker images: is everything built
# locally also on ghcr.io/omnibioai, and not older than the local build?
#
# There's no local push-tracking log for plugin images (unlike SIFs'
# sif_push.log), so this script rebuilds that comparison from scratch each
# run against the registry, rather than trusting a local log that nothing
# actually writes to.
#
# Real scale (verified 2026-07-27, do not trust "600+" without re-checking):
#   - locally-built plugin images (ghcr.io/omnibioai/omnibioai-plugin-*): ~46
#   - plugin packages actually on the registry:                          180
#   - plugin source directories on disk (built or not):                  ~245
# A full sweep only needs a per-version API lookup for plugins that exist
# in BOTH sets (comparison only makes sense there) -- roughly 46 calls,
# plus ~2 calls to paginate the registry's plugin package list. That's
# ~48 calls total against a 5000/hour authenticated budget, so the
# rate-limit handling below is precautionary (keeps this safe if the
# plugin count grows a lot) rather than load-bearing at today's scale.
#
# Usage: GH_TOKEN=... ./check_plugin_image_sync.sh

set -uo pipefail

ORG="omnibioai"
# Falls back to the gh CLI's own stored credentials (gh auth login) when
# GH_TOKEN isn't exported -- cron runs with a minimal environment, so this
# is what lets this script work unattended instead of failing silently
# every night on a missing env var.
GH_TOKEN="${GH_TOKEN:-$(gh auth token 2>/dev/null)}"
: "${GH_TOKEN:?No GH_TOKEN set and 'gh auth token' returned nothing -- run 'gh auth login' or export GH_TOKEN}"
RATE_LIMIT_FLOOR=200      # pause if remaining calls drop below this
BATCH_SIZE=20             # check remaining quota every N version lookups
REPORT_FILE="./plugin_sync_report_$(date +%Y%m%d_%H%M%S).txt"

gh_api_get() {
  curl -s -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" "$1"
}

to_epoch() { date -d "$1" +%s 2>/dev/null; }

check_rate_limit() {
  # /rate_limit itself is free -- does not consume quota
  local remaining reset
  remaining=$(gh_api_get "https://api.github.com/rate_limit" | jq -r '.resources.core.remaining')
  reset=$(gh_api_get "https://api.github.com/rate_limit" | jq -r '.resources.core.reset')
  echo "$remaining $reset"
}

echo "=== Step 1: enumerate local plugin images ===" | tee -a "$REPORT_FILE"
mapfile -t LOCAL_PLUGINS < <(
  docker images --format "{{.Repository}}:{{.Tag}}" \
    | grep -E "^ghcr\.io/${ORG}/omnibioai-plugin-" \
    | grep -v '<none>' \
    | sort -u
)
echo "Local plugin images found: ${#LOCAL_PLUGINS[@]}" | tee -a "$REPORT_FILE"

echo "" | tee -a "$REPORT_FILE"
echo "=== Step 2: paginate registry plugin packages ===" | tee -a "$REPORT_FILE"
page=1
declare -A REGISTRY_PLUGINS=()   # name -> 1
while : ; do
  resp=$(gh_api_get "https://api.github.com/orgs/${ORG}/packages?package_type=container&per_page=100&page=${page}")
  count=$(echo "$resp" | jq 'length' 2>/dev/null)
  [ -z "$count" ] || [ "$count" = "0" ] || [ "$count" = "null" ] && break
  while IFS= read -r name; do
    [[ "$name" == *-plugin-* ]] && REGISTRY_PLUGINS["$name"]=1
  done < <(echo "$resp" | jq -r '.[].name')
  page=$((page+1))
done
echo "Registry plugin packages found: ${#REGISTRY_PLUGINS[@]} (across $((page-1)) pages)" | tee -a "$REPORT_FILE"

echo "" | tee -a "$REPORT_FILE"
echo "=== Step 3: compare + check staleness (rate-limit aware) ===" | tee -a "$REPORT_FILE"

gap_missing=0
gap_stale=0
ok=0
checked=0
sweep_complete=true
declare -a UNCHECKED=()

i=0
for ref in "${LOCAL_PLUGINS[@]}"; do
  i=$((i+1))
  pkg=$(echo "$ref" | sed -E "s#^ghcr\.io/${ORG}/##; s/:.*//")
  tag=$(echo "$ref" | sed -E 's/^.*://')

  # every BATCH_SIZE calls, check remaining quota before continuing
  if (( i % BATCH_SIZE == 1 )); then
    read -r remaining reset <<< "$(check_rate_limit)"
    echo "  [quota check] remaining=$remaining (before item $i/${#LOCAL_PLUGINS[@]})" | tee -a "$REPORT_FILE"
    if [ -n "$remaining" ] && [ "$remaining" -lt "$RATE_LIMIT_FLOOR" ]; then
      wait_s=$(( reset - $(date +%s) + 5 ))
      if [ "$wait_s" -gt 0 ] && [ "$wait_s" -lt 900 ]; then
        echo "  Rate limit low ($remaining < $RATE_LIMIT_FLOOR). Sleeping ${wait_s}s until reset." | tee -a "$REPORT_FILE"
        sleep "$wait_s"
      else
        echo "  Rate limit low ($remaining < $RATE_LIMIT_FLOOR) and reset is too far away ($wait_s s)." | tee -a "$REPORT_FILE"
        echo "  Aborting sweep early -- remaining ${#LOCAL_PLUGINS[@]}-$((i-1)) items NOT checked." | tee -a "$REPORT_FILE"
        sweep_complete=false
        for ((j=i; j<=${#LOCAL_PLUGINS[@]}; j++)); do
          UNCHECKED+=("${LOCAL_PLUGINS[$((j-1))]}")
        done
        break
      fi
    fi
  fi

  if [ -z "${REGISTRY_PLUGINS[$pkg]:-}" ]; then
    echo "[GAP-MISSING] $ref -- no '$pkg' package on registry at all" | tee -a "$REPORT_FILE"
    gap_missing=$((gap_missing+1))
    checked=$((checked+1))
    continue
  fi

  resp=$(gh_api_get "https://api.github.com/orgs/${ORG}/packages/container/${pkg}/versions?per_page=100")
  latest_remote=$(echo "$resp" | jq -r 'if length > 0 then (max_by(.created_at) | .created_at) else empty end')
  checked=$((checked+1))

  if [ -z "$latest_remote" ]; then
    echo "[GAP-MISSING] $ref -- package exists but has zero versions on registry" | tee -a "$REPORT_FILE"
    gap_missing=$((gap_missing+1))
    continue
  fi

  local_created=$(docker inspect --format '{{.Created}}' "$ref" 2>/dev/null)
  remote_epoch=$(to_epoch "$latest_remote")
  local_epoch=$(to_epoch "$local_created")

  if [ -n "$local_epoch" ] && [ -n "$remote_epoch" ] && [ "$local_epoch" -gt "$remote_epoch" ]; then
    echo "[GAP-STALE]   $ref -- built $local_created, registry last pushed $latest_remote" | tee -a "$REPORT_FILE"
    gap_stale=$((gap_stale+1))
  else
    ok=$((ok+1))
  fi
done

echo "" | tee -a "$REPORT_FILE"
echo "==== Summary ====" | tee -a "$REPORT_FILE"
echo "Local plugin images:      ${#LOCAL_PLUGINS[@]}" | tee -a "$REPORT_FILE"
echo "Checked this run:         $checked" | tee -a "$REPORT_FILE"
echo "OK (on registry, not stale): $ok" | tee -a "$REPORT_FILE"
echo "GAP - missing from registry: $gap_missing" | tee -a "$REPORT_FILE"
echo "GAP - registry stale:        $gap_stale" | tee -a "$REPORT_FILE"

if $sweep_complete; then
  echo "SWEEP_COMPLETE: yes -- every local plugin image was checked" | tee -a "$REPORT_FILE"
else
  echo "SWEEP_COMPLETE: NO -- ${#UNCHECKED[@]} images were NOT checked due to rate limiting:" | tee -a "$REPORT_FILE"
  for u in "${UNCHECKED[@]}"; do echo "    - $u" | tee -a "$REPORT_FILE"; done
  echo "  Re-run this script to resume; do not treat this run's clean summary as a full-sweep guarantee." | tee -a "$REPORT_FILE"
fi

echo "" | tee -a "$REPORT_FILE"
echo "Full report: $REPORT_FILE"
