#!/usr/bin/env bash
set -uo pipefail

# Scans every sibling repo under MACHINE_DIR for leftover ghcr.io/man4ish
# references, using the same logic as update_ghcr_refs.sh (dry-run only,
# same exclusions for release/, obsolete/, node_modules/, work/, *.log etc).
# Prints a per-repo summary so you can decide where to actually apply the fix.

MACHINE_DIR="${1:-$HOME/Desktop/machine}"
UPDATE_SCRIPT="$(cd "$(dirname "$0")" && pwd)/update_ghcr_refs.sh"

if [ ! -f "$UPDATE_SCRIPT" ]; then
  echo "!! update_ghcr_refs.sh not found next to this script ($UPDATE_SCRIPT)"
  exit 1
fi

REPOS=(
  omnibioai-tes
  omnibioai-lims
  omnibioai-rag
  omnibioai-model-registry
  omnibioai-control-center
  omnibioai-dev-hub
  omnibioai-sdk
  omnibioai-workflow-bundles
  omnibioai-tool-images
  omnibioai-auth
  omnibioai-policy-engine
  omnibioai-hpc-policy-engine
  omnibioai-security-audit
  omnibioai-api-gateway
  omnibioai-launcher
  omnibioai-toolserver
  omnibioai-tool-runtime
  omnibioai-iam-client
  omnibioai-security-sdk
  omnibioai-docs
)

echo "Scanning ${#REPOS[@]} sibling repos under $MACHINE_DIR for 'ghcr.io/man4ish' references..."
echo ""

declare -a HITS=()

for repo in "${REPOS[@]}"; do
  repo_path="${MACHINE_DIR}/${repo}"
  if [ ! -d "$repo_path" ]; then
    echo "  [skip] $repo (directory not found)"
    continue
  fi

  output=$(bash "$UPDATE_SCRIPT" "$repo_path" 2>&1)
  match_line=$(echo "$output" | grep -m1 "^Found ")

  if [ -z "$match_line" ]; then
    echo "  [ok]   $repo - no matches"
  else
    count=$(echo "$match_line" | grep -oP '\d+(?= matches)')
    files=$(echo "$match_line" | grep -oP '(?<=across )\d+(?= files)')
    echo "  [HIT]  $repo - $count matches across $files files"
    HITS+=("$repo")
  fi
done

echo ""
echo "==== Summary ===="
if [ ${#HITS[@]} -eq 0 ]; then
  echo "No repos with lingering references found."
else
  echo "Repos needing attention:"
  for r in "${HITS[@]}"; do
    echo "  - $r"
    echo "      Review: bash $UPDATE_SCRIPT ${MACHINE_DIR}/${r}"
    echo "      Apply:  bash $UPDATE_SCRIPT ${MACHINE_DIR}/${r} --apply"
  done
fi