#!/bin/bash
# check_unpushed_work.sh
#
# Cross-repo health check: scans every git repo under ROOT for uncommitted
# changes and unpushed commits. Companion to ecosystem_status.sh, but
# focused specifically on "is anything at risk of being lost if this
# machine dies" rather than general status.
#
# Exit code is non-zero if any repo has unpushed commits (untracked/modified
# files alone don't fail the check, since WIP is normal -- unpushed commits
# are the real risk, since committing already signals "this is done").
#
# Usage:
#   ./check_unpushed_work.sh [--root PATH] [--json PATH] [--quiet]
#
# Intended to be run from cron, e.g.:
#   0 7 * * * /home/manish/Desktop/machine/omnibioai-utils/check_unpushed_work.sh \
#       --json /home/manish/Desktop/machine/work/unpushed_work.json \
#       >> /home/manish/Desktop/machine/work/backups/omnibioai-unpushed-check.log 2>&1

set -uo pipefail

ROOT="/home/manish/Desktop/machine"
JSON_OUT=""
QUIET=false

while [ $# -gt 0 ]; do
    case "$1" in
        --root) ROOT="$2"; shift 2 ;;
        --json) JSON_OUT="$2"; shift 2 ;;
        --quiet) QUIET=true; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
TOTAL=0
CLEAN=0
UNPUSHED_COUNT=0
DIRTY_ONLY_COUNT=0
JSON_ROWS=()

log() { echo "[INFO] $(date -Iseconds) $*"; }

if [ ! -d "$ROOT" ]; then
    echo "[ERROR] $(date -Iseconds) Root directory not found: $ROOT" >&2
    exit 2
fi

$QUIET || printf "%-38s %-8s %-10s %s\n" "REPOSITORY" "BRANCH" "STATUS" "DETAILS"
$QUIET || printf '%.0s-' {1..90}; $QUIET || echo

REPOS_WITH_UNPUSHED=()

for dir in "$ROOT"/omnibioai*/; do
    [ -d "${dir}.git" ] || continue
    repo_name=$(basename "$dir")
    TOTAL=$((TOTAL + 1))

    branch=$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")

    # untracked + modified (working tree state)
    porcelain=$(git -C "$dir" status --porcelain 2>/dev/null)
    modified_count=$(echo "$porcelain" | grep -c '^ M\|^M ' || true)
    untracked_count=$(echo "$porcelain" | grep -c '^??' || true)

    # unpushed commits (only meaningful if an upstream is configured)
    unpushed_count=0
    if git -C "$dir" rev-parse --abbrev-ref --symbolic-full-name '@{u}' > /dev/null 2>&1; then
        unpushed_count=$(git -C "$dir" log '@{u}..HEAD' --oneline 2>/dev/null | wc -l | tr -d ' ')
    fi

    is_dirty=false
    [ "$modified_count" -gt 0 ] 2>/dev/null && is_dirty=true
    [ "$untracked_count" -gt 0 ] 2>/dev/null && is_dirty=true
    [ "$unpushed_count" -gt 0 ] 2>/dev/null && is_dirty=true

    details=""
    [ "$modified_count" -gt 0 ] 2>/dev/null && details+="${modified_count} modified "
    [ "$untracked_count" -gt 0 ] 2>/dev/null && details+="${untracked_count} untracked "
    [ "$unpushed_count" -gt 0 ] 2>/dev/null && details+="${unpushed_count} unpushed "

    if [ "$unpushed_count" -gt 0 ] 2>/dev/null; then
        UNPUSHED_COUNT=$((UNPUSHED_COUNT + 1))
        REPOS_WITH_UNPUSHED+=("$repo_name ($details)")
    elif [ "$is_dirty" = true ]; then
        DIRTY_ONLY_COUNT=$((DIRTY_ONLY_COUNT + 1))
    else
        CLEAN=$((CLEAN + 1))
    fi

    status_label="clean"
    [ "$is_dirty" = true ] && status_label="dirty"

    $QUIET || printf "%-38s %-8s %-10s %s\n" "$repo_name" "$branch" "$status_label" "$details"

    if [ -n "$JSON_OUT" ]; then
        JSON_ROWS+=("{\"repo\":\"${repo_name}\",\"branch\":\"${branch}\",\"modified\":${modified_count:-0},\"untracked\":${untracked_count:-0},\"unpushed\":${unpushed_count:-0}}")
    fi
done

$QUIET || echo
echo "Summary: ${TOTAL} repos scanned | ${CLEAN} clean | ${DIRTY_ONLY_COUNT} dirty (uncommitted only) | ${UNPUSHED_COUNT} with UNPUSHED COMMITS"

if [ "${#REPOS_WITH_UNPUSHED[@]}" -gt 0 ]; then
    echo
    echo "Repos with unpushed commits (at risk if this machine is lost):"
    for r in "${REPOS_WITH_UNPUSHED[@]}"; do
        echo "  - $r"
    done
fi

if [ -n "$JSON_OUT" ]; then
    {
        echo "{"
        echo "  \"checked_at\": \"${TIMESTAMP}\","
        echo "  \"total_repos\": ${TOTAL},"
        echo "  \"clean\": ${CLEAN},"
        echo "  \"dirty_only\": ${DIRTY_ONLY_COUNT},"
        echo "  \"with_unpushed\": ${UNPUSHED_COUNT},"
        printf "  \"repos\": [%s]\n" "$(IFS=,; echo "${JSON_ROWS[*]}")"
        echo "}"
    } > "$JSON_OUT"
    log "Wrote JSON summary to $JSON_OUT"
fi

if [ "$UNPUSHED_COUNT" -gt 0 ]; then
    exit 1
fi
exit 0