#!/usr/bin/env bash
#
# check_base_images.sh
#
# Freshness check for the omnibioai "base image" family — the shared
# omnibioai-base + omnibioai-ml-* images other plugins/services build FROM.
#
# NOTE: an earlier scoping pass assumed there were only 2 base images.
# The registry actually carries 12 (omnibioai-base, 9x omnibioai-ml-<domain>,
# omnibioai-sklearn, omnibioai-xgboost) as of 2026-07-27. This list is small
# enough to check directly against the GHCR API with no pagination either
# way, so the script below covers the real set rather than a stale "2".
# Re-verify this list occasionally — it is hand-maintained, not discovered.
#
# For each base image:
#   - look up the latest version pushed to ghcr.io/omnibioai/<pkg>
#   - if a local image is tagged ghcr.io/omnibioai/<pkg>:<tag>, compare its
#     build time against the registry's last-pushed time
#   - report MISSING (no registry package), STALE (local build is newer
#     than what's on the registry), or OK
#
# This is a read-only check. It does not push or modify anything.
#
# Usage: GH_TOKEN=... ./check_base_images.sh

set -uo pipefail

ORG="omnibioai"
# Falls back to the gh CLI's own stored credentials (gh auth login) when
# GH_TOKEN isn't exported -- cron runs with a minimal environment, so this
# is what lets this script work unattended instead of failing silently
# every night on a missing env var.
GH_TOKEN="${GH_TOKEN:-$(gh auth token 2>/dev/null)}"
: "${GH_TOKEN:?No GH_TOKEN set and 'gh auth token' returned nothing -- run 'gh auth login' or export GH_TOKEN}"

BASE_PACKAGES=(
  omnibioai-base
  omnibioai-ml-chem
  omnibioai-ml-genomics
  omnibioai-ml-imaging
  omnibioai-ml-llm
  omnibioai-ml-metabolomics
  omnibioai-ml-microbiome
  omnibioai-ml-protein
  omnibioai-ml-singlecell
  omnibioai-ml-spatial
  omnibioai-sklearn
  omnibioai-xgboost
)

gh_api_get() {
  curl -s -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" "$1"
}

to_epoch() {
  date -d "$1" +%s 2>/dev/null
}

echo "Checking ${#BASE_PACKAGES[@]} base images against ghcr.io/${ORG}..."
echo ""

missing=0
stale=0
ok=0
no_local=0

for pkg in "${BASE_PACKAGES[@]}"; do
  resp=$(gh_api_get "https://api.github.com/orgs/${ORG}/packages/container/${pkg}/versions?per_page=100")
  if [ -z "$resp" ] || [ "$(echo "$resp" | jq -r 'type')" != "array" ] || [ "$(echo "$resp" | jq 'length')" -eq 0 ]; then
    echo "[MISSING]  $pkg  -- no package/versions found on registry"
    missing=$((missing+1))
    continue
  fi

  # newest version by created_at (API does not guarantee order)
  latest_remote=$(echo "$resp" | jq -r 'max_by(.created_at) | .created_at')
  remote_epoch=$(to_epoch "$latest_remote")

  # find any locally-tagged ghcr.io/omnibioai/<pkg>:<tag> image
  local_tags=$(docker images --format "{{.Repository}}:{{.Tag}}" "ghcr.io/${ORG}/${pkg}" 2>/dev/null | grep -v '<none>')

  if [ -z "$local_tags" ]; then
    echo "[no-local] $pkg  -- registry has it (latest push: $latest_remote), no locally-tagged copy to compare"
    no_local=$((no_local+1))
    continue
  fi

  worst_status="OK"
  while IFS= read -r ref; do
    [ -z "$ref" ] && continue
    local_created=$(docker inspect --format '{{.Created}}' "$ref" 2>/dev/null)
    [ -z "$local_created" ] && continue
    local_epoch=$(to_epoch "$local_created")
    if [ -n "$local_epoch" ] && [ -n "$remote_epoch" ] && [ "$local_epoch" -gt "$remote_epoch" ]; then
      echo "[STALE]    $ref  -- built $local_created, registry last pushed $latest_remote"
      worst_status="STALE"
    fi
  done <<< "$local_tags"

  if [ "$worst_status" = "OK" ]; then
    echo "[OK]       $pkg  -- registry last pushed $latest_remote, local build(s) not newer"
    ok=$((ok+1))
  else
    stale=$((stale+1))
  fi
done

echo ""
echo "==== Summary ===="
echo "OK:               $ok"
echo "STALE (local newer than registry): $stale"
echo "MISSING from registry:             $missing"
echo "On registry, no local copy to compare: $no_local"
