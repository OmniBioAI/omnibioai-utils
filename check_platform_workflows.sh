#!/usr/bin/env bash
#
# check_platform_workflows.sh
#
# Health check for the GitHub Actions workflows that build+push
# platform/system Docker images (omnibioai-auth, omnibioai-tes,
# omnibioai-toolserver, etc.) to ghcr.io/omnibioai.
#
# Deliberately does NOT re-verify "is the image on the registry" the way
# check_base_images.sh does for base images -- if CI/CD is the actual
# push mechanism for these repos, a local push-tracking check would just
# duplicate that guarantee. The real risk for a CI/CD-driven category is
# "is the automation still working", so this checks workflow run health
# via the Actions API instead.
#
# IMPORTANT, discovered 2026-07-27: as of today, EVERY docker-push workflow
# in this list is sitting in .github/workflows_disabled/ (moved there by
# disable_cicd.sh, commit message "re-enable before launch"), and the last
# runs recorded before being disabled (2026-06-10) were themselves FAILING,
# not passing. So right now this check will report DISABLED for the whole
# list, which is expected and not a bug in the check -- but it also means
# the assumption "CI/CD already guarantees these get pushed" does NOT
# currently hold. Nothing is pushing these images automatically right now.
# Re-run this after CI/CD is re-enabled for it to be a meaningful signal
# again; until then treat DISABLED as "no push guarantee exists, verify
# manually" rather than "healthy, nothing to do".
#
# Usage: GH_TOKEN=... ./check_platform_workflows.sh [machine_dir]

set -uo pipefail

MACHINE_DIR="${1:-$HOME/Desktop/machine}"
# Falls back to the gh CLI's own stored credentials (gh auth login) when
# GH_TOKEN isn't exported -- cron runs with a minimal environment, so this
# is what lets this script work unattended instead of failing silently
# every night on a missing env var.
GH_TOKEN="${GH_TOKEN:-$(gh auth token 2>/dev/null)}"
: "${GH_TOKEN:?No GH_TOKEN set and 'gh auth token' returned nothing -- run 'gh auth login' or export GH_TOKEN}"
STALE_DAYS=14

# repo -> docker-push workflow filename (discovered by grepping
# .github/workflows{,_disabled}/*.y*ml for docker/build-push-action|ghcr.io)
declare -A REPO_WORKFLOWS=(
  [omnibioai-tes]=ci.yml
  [omnibioai-lims]=ci.yml
  [omnibioai-rag]=ci.yml
  [omnibioai-model-registry]=ci.yml
  [omnibioai-control-center]=ci.yml
  [omnibioai-dev-hub]=ci.yml
  [omnibioai-sdk]=ci.yml
  [omnibioai-workflow-bundles]=ci.yml
  [omnibioai-auth]=ci.yml
  [omnibioai-policy-engine]=ci.yml
  [omnibioai-hpc-policy-engine]=ci.yml
  [omnibioai-security-audit]=ci.yml
  [omnibioai-api-gateway]=ci.yml
  [omnibioai-launcher]=docker-publish.yml
  [omnibioai-toolserver]=ci.yml
  [omnibioai-tool-runtime]=ci.yml
  [omnibioai-iam-client]=ci.yml
  [omnibioai-security-sdk]=ci.yml
  [omnibioai]=ci.yml
  [omnibioai-videos]=ci.yml
)

gh_api_get() {
  curl -s -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" "$1"
}

now_epoch=$(date +%s)

declare -a DISABLED_LIST=()
declare -a FAILING_LIST=()
declare -a STALE_LIST=()
declare -a OK_LIST=()
declare -a UNKNOWN_LIST=()

for repo in "${!REPO_WORKFLOWS[@]}"; do
  wf="${REPO_WORKFLOWS[$repo]}"
  active_path="${MACHINE_DIR}/${repo}/.github/workflows/${wf}"
  disabled_path="${MACHINE_DIR}/${repo}/.github/workflows_disabled/${wf}"

  is_disabled=false
  if [ -f "$disabled_path" ] && [ ! -f "$active_path" ]; then
    is_disabled=true
  fi

  runs=$(gh_api_get "https://api.github.com/repos/OmniBioAI/${repo}/actions/runs?per_page=1")
  run_count=$(echo "$runs" | jq -r '.total_count // 0' 2>/dev/null)

  if [ -z "$run_count" ] || [ "$run_count" = "0" ] || [ "$run_count" = "null" ]; then
    echo "[UNKNOWN]  $repo/$wf -- no workflow runs found via API"
    UNKNOWN_LIST+=("$repo")
    continue
  fi

  conclusion=$(echo "$runs" | jq -r '.workflow_runs[0].conclusion')
  created_at=$(echo "$runs" | jq -r '.workflow_runs[0].created_at')
  run_epoch=$(date -d "$created_at" +%s 2>/dev/null)
  age_days=$(( (now_epoch - run_epoch) / 86400 ))

  if $is_disabled; then
    echo "[DISABLED] $repo/$wf -- workflow disabled; last recorded run: $conclusion on $created_at (${age_days}d ago)"
    DISABLED_LIST+=("$repo (last: $conclusion, ${age_days}d ago)")
  elif [ "$conclusion" = "failure" ] || [ "$conclusion" = "cancelled" ] || [ "$conclusion" = "timed_out" ]; then
    echo "[FAILING]  $repo/$wf -- last run: $conclusion on $created_at (${age_days}d ago)"
    FAILING_LIST+=("$repo (last: $conclusion, ${age_days}d ago)")
  elif [ "$age_days" -gt "$STALE_DAYS" ]; then
    echo "[STALE]    $repo/$wf -- active but no run in ${age_days}d (last: $conclusion on $created_at)"
    STALE_LIST+=("$repo (${age_days}d since last run)")
  elif [ "$conclusion" = "success" ]; then
    echo "[OK]       $repo/$wf -- last run succeeded $created_at (${age_days}d ago)"
    OK_LIST+=("$repo")
  else
    echo "[UNKNOWN]  $repo/$wf -- unrecognized conclusion '$conclusion'"
    UNKNOWN_LIST+=("$repo")
  fi
done

echo ""
echo "==== Summary ===="
echo "OK:        ${#OK_LIST[@]}"
echo "FAILING:   ${#FAILING_LIST[@]}"
echo "STALE:     ${#STALE_LIST[@]}"
echo "DISABLED:  ${#DISABLED_LIST[@]}"
echo "UNKNOWN:   ${#UNKNOWN_LIST[@]}"

if [ ${#DISABLED_LIST[@]} -gt 0 ]; then
  echo ""
  echo "!! ${#DISABLED_LIST[@]} of ${#REPO_WORKFLOWS[@]} repos have their docker-push workflow disabled."
  echo "   CI/CD is NOT currently providing a push guarantee for these images."
fi
if [ ${#FAILING_LIST[@]} -gt 0 ]; then
  echo ""
  echo "!! Repos with a failing docker-push workflow (active, but broken):"
  for r in "${FAILING_LIST[@]}"; do echo "     - $r"; done
fi
